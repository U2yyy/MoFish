#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
墨墨背单词 - WebSocket 版客户端
===============================

基于 common.29c89a32.js 分析实现
协议: 纯文本消息 + JSON 数据 + Protobuf 二进制

作者: MoFish CLI Team
Python: 3.8+
"""

import os
import sys
import json
import time
import random
import asyncio
import websockets
import ssl
import struct
from datetime import datetime
from typing import Optional, List, Dict, Any

# ============================================================================
# 依赖检查
# ============================================================================

try:
    import requests
except ImportError:
    print("[错误] 缺少 requests 库，请运行: pip install requests")
    sys.exit(1)

try:
    import websockets
except ImportError:
    print("[错误] 缺少 websockets 库，请运行: pip install websockets")
    sys.exit(1)

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
except ImportError:
    print("[错误] 缺少 rich 库，请运行: pip install rich")
    sys.exit(1)

try:
    from readchar import readchar
except ImportError:
    readchar = None
    print("[警告] 缺少 readchar 库，按键响应可能需要回车确认")

# ============================================================================
# 常量
# ============================================================================

CONFIG_FILE = "config.json"

# 配置项（不提交到 Git）
_config_cache = {}

# WebSocket 服务器
WS_URL = "wss://tc-apis.maimemo.com/study/ws/webstudy"
WS_API_URL = "https://tc-apis.maimemo.com"

# 学习状态
LEARNING_STATUS = {
    "FAMILIAR": "认识",
    "VAGUE": "模糊",
    "FORGET": "忘记",
    "WELL_FAMILIAR": "熟知",
}

# 按键映射
KEY_TO_STATUS = {
    "1": "FAMILIAR",
    "2": "VAGUE",
    "3": "FORGET",
}

# ============================================================================
# 伪装日志模板
# ============================================================================

DISGUISE_TEMPLATES = [
    '[{time}] [INFO] Loading package "{word}" (v{version}) successfully.',
    '[{time}] [DEBUG] Query SELECT * FROM vocabulary WHERE word="{word}" LIMIT 1;',
    '[{time}] [INFO] Module "{word}" imported successfully.',
    '[{time}] [WARN] Cache miss for key: {word}, fetching from disk...',
    '[{time}] [INFO] Compiling regex pattern: ^{word}$',
    '[{time}] [DEBUG] JSON decode: {{"word": "{word}", "status": "pending"}}',
    '[{time}] [INFO] HTTP GET /api/vocab/{word} - 200 OK',
    '[{time}] [DEBUG] Evaluating expression: vocabulary.lookup("{word}")',
    '[{time}] [INFO] Loading dictionary entry: {word} ... done.',
    '[{time}] [WARN] Slow query detected: SELECT * FROM words WHERE w="{word}"',
    '[{time}] [INFO] [ThreadPoolWorker] Processing task: fetch_def("{word}")',
    '[{time}] [DEBUG] Redis HGET dict:{word} -> hit',
    '[{time}] [INFO] ESLint: Checking {word}.md ... 0 errors',
    '[{time}] [DEBUG] pytest: collected item test_{word}',
]

FAKE_TRACEBACKS = [
    """Traceback (most recent call last):
  File "/project/src/main.py", line 42, in <module>
    main()
  File "/project/src/main.py", line 38, in main
    run_build()
  File "/project/src/build.py", line 156, in run_build
    compile_assets()
  File "/project/src/compiler.py", line 89, in compile_assets
    raise BuildError("Asset compilation failed")
BuildError: Asset compilation failed""",

    """Merge failed!
Auto-merging src/config/settings.py
CONFLICT (content): Merge conflict in src/config/settings.py
error: merge failed, please resolve conflicts manually""",

    """ERROR: Command failed with exit code 1:
$ python manage.py migrate
Traceback (most recent call last):
  File "manage.py", line 22, in <module>
    execute_from_command_line(sys.argv)
Exception: Database locked""",
]

# ============================================================================
# Protobuf 解析工具
# ============================================================================

def parse_varint(data: bytes, pos: int) -> tuple:
    """解析 Varint，返回 (value, new_pos)"""
    result = 0
    shift = 0
    while pos < len(data):
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7f) << shift
        if not (byte & 0x80):
            break
        shift += 7
    return result, pos

def parse_protobuf_string(data: bytes, pos: int) -> tuple:
    """解析 Protobuf 字符串字段，返回 (string_value, new_pos)"""
    length, pos = parse_varint(data, pos)
    if pos + length > len(data):
        return "", len(data)
    return data[pos:pos+length].decode('utf-8', errors='replace'), pos + length

def parse_protobuf_field(data: bytes, pos: int) -> tuple:
    """解析一个 Protobuf 字段，返回 (field_number, wire_type, value, new_pos)"""
    if pos >= len(data):
        return None, None, None, pos

    tag, pos = parse_varint(data, pos)
    field_number = tag >> 3
    wire_type = tag & 0x7

    if wire_type == 0:  # Varint
        value, pos = parse_varint(data, pos)
    elif wire_type == 2:  # Length-delimited
        value, pos = parse_protobuf_string(data, pos)
    elif wire_type == 5:  # 32-bit
        value = struct.unpack('<I', data[pos:pos+4])[0]
        pos += 4
    else:
        value = None

    return field_number, wire_type, value, pos

def parse_word_response(data: bytes) -> Dict[str, Any]:
    """
    解析 GET_WORD 返回的 Protobuf 响应

    响应格式 (根据 JS 代码分析):
    message WsWebStudyGetWordResponse {
        WordItem word = 1;
        int64 study_time_ms = 2;
    }

    WordItem {
        string id = 1;           // UUID
        string spelling = 2;     // 单词拼写
        string phonetic_us = 3;   // 美式音标
        string phonetic_uk = 4;   // 英式音标
        ... 更多字段
    }
    """
    result = {
        "id": "",
        "spelling": "",
        "phonetic_us": "",
        "phonetic_uk": "",
        "study_time_ms": 0,
        "interpretation": "",
        "difficulty": 0,
    }

    pos = 0
    current_field = None

    while pos < len(data):
        field_num, wire_type, value, pos = parse_protobuf_field(data, pos)
        if field_num is None:
            break

        # WordItem 的嵌套字段 (field 1)
        if field_num == 1 and wire_type == 2:
            # 嵌套的 WordItem
            nested_pos = 0
            nested_data = value if isinstance(value, bytes) else value.encode('utf-8') if isinstance(value, str) else b''
            while nested_pos < len(nested_data):
                nf_num, nf_wire, nf_value, nested_pos = parse_protobuf_field(nested_data, nested_pos)
                if nf_num is None:
                    break

                if nf_num == 1:  # id
                    result["id"] = nf_value
                elif nf_num == 2:  # spelling
                    result["spelling"] = nf_value
                elif nf_num == 3:  # phonetic_us
                    result["phonetic_us"] = nf_value
                elif nf_num == 4:  # phonetic_uk
                    result["phonetic_uk"] = nf_value
                elif nf_num == 7:  # difficulty
                    result["difficulty"] = nf_value
                elif nf_num == 12:  # interpretation
                    result["interpretation"] = nf_value

        elif field_num == 2 and wire_type == 0:  # study_time_ms
            result["study_time_ms"] = value

    return result


def parse_submit_response(data: bytes) -> Optional[Dict[str, Any]]:
    """
    解析 SUBMIT_RESPONSE 返回的 Protobuf 响应
    包含下一个单词的信息
    """
    result = parse_word_response(data)
    return result if result.get("id") else None


# ============================================================================
# WebSocket 学习客户端 (完整协议)
# ============================================================================

class MaimemoWSClient:
    """
    基于 WebSocket 的墨墨学习客户端

    协议分析结果:
    - 连接: wss://tc-apis.maimemo.com/study/ws/webstudy?token=xxx
    - 消息格式: 纯文本 action，或 action + JSON 数据
    - 需要先发送 SYSTEM_PING 保持连接，然后 INIT_STUDY 初始化
    """

    def __init__(self, api_token: str):
        self.api_token = api_token.strip()
        self.ws = None
        self._create_ssl_context()
        self._connected = False
        self._reply_id = 0

    def _create_ssl_context(self):
        """创建 SSL 上下文 (处理 macOS 证书问题)"""
        self.ssl_ctx = ssl.create_default_context()
        self.ssl_ctx.check_hostname = False
        self.ssl_ctx.verify_mode = ssl.CERT_NONE

    def _next_reply_id(self) -> str:
        """生成唯一的 reply_id"""
        self._reply_id += 1
        return str(self._reply_id)

    async def connect(self) -> bool:
        """建立 WebSocket 连接"""
        url = f"{WS_URL}?token={self.api_token}"
        try:
            self.ws = await websockets.connect(url, ssl=self.ssl_ctx)
            self._connected = True
            return True
        except Exception as e:
            print(f"[错误] WebSocket 连接失败: {e}")
            self._connected = False
            return False

    async def close(self):
        """关闭连接"""
        if self.ws:
            await self.ws.close()
            self.ws = None
            self._connected = False

    async def _wait_for_event(self, event: str, timeout: float = 10) -> Optional[Dict]:
        """等待特定的事件"""
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                response = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
                # 解析响应 - 可能是文本或二进制
                if isinstance(response, str):
                    # 可能是 ping 或其他文本消息
                    if "SYSTEM_PING" in response:
                        await self.ws.send('{"event":"SYSTEM_PING","data":{}}')
                        continue
                # 返回原始响应供调用者处理
                return response
            except asyncio.TimeoutError:
                continue
        return None

    async def init_study(self) -> bool:
        """
        初始化学习会话

        需要在 GET_WORD 之前调用
        """
        try:
            # 发送 INIT_STUDY
            await self.ws.send('WEBSTUDY_INIT_STUDY {}')

            # 等待响应
            response = await asyncio.wait_for(self.ws.recv(), timeout=10)
            return True

        except asyncio.TimeoutError:
            print("[错误] 初始化学习超时")
            return False
        except Exception as e:
            print(f"[错误] 初始化学习失败: {e}")
            return False

    async def get_word(self) -> Optional[Dict[str, Any]]:
        """
        获取一个单词（完整信息）

        返回:
            Dict 包含: id, spelling, phonetic_us, phonetic_uk, interpretation, difficulty
            或 None 如果失败
        """
        # 建立新连接
        if not await self.connect():
            return None

        try:
            # 发送 GET_WORD (纯文本)
            await self.ws.send('WEBSTUDY_GET_WORD')

            # 接收响应 (二进制 protobuf)
            response = await asyncio.wait_for(self.ws.recv(), timeout=10)

            # 解析 Protobuf 响应
            if isinstance(response, bytes):
                word_data = parse_word_response(response)
                return word_data
            else:
                # 如果是文本，尝试解析（不应该发生）
                print(f"[警告] 收到意外文本响应: {response[:100]}")
                return None

        except asyncio.TimeoutError:
            print("[错误] 获取单词超时")
            return None
        except Exception as e:
            print(f"[错误] 获取单词失败: {e}")
            return None
        finally:
            await self.close()

    async def submit_response(
        self,
        word_id: str,
        response: str = "FAMILIAR",
        recall_duration: int = 1000,
        study_duration: int = 2000,
        study_method: str = "STUDY_CN_EN"
    ) -> bool:
        """
        提交学习反馈
        """
        # 建立新连接
        if not await self.connect():
            return False

        try:
            # 构造 JSON 数据
            data = {
                "voc_id": word_id,
                "response": response,
                "recall_duration": recall_duration,
                "study_duration": study_duration,
                "study_method": study_method
            }

            # 发送 SUBMIT_RESPONSE + JSON
            message = f'WEBSTUDY_SUBMIT_RESPONSE {json.dumps(data)}'
            await self.ws.send(message)

            # 接收响应
            response = await asyncio.wait_for(self.ws.recv(), timeout=10)

            return True

        except asyncio.TimeoutError:
            print("[错误] 提交反馈超时")
            return False
        except Exception as e:
            print(f"[错误] 提交反馈失败: {e}")
            return False
        finally:
            await self.close()

    async def query_word(self, word_id: str) -> Optional[Dict[str, Any]]:
        """
        查询单词详情

        参数:
            word_id: 单词ID (UUID)
        返回:
            单词详情Dict 或 None
        """
        if not await self.connect():
            return None

        try:
            # 发送 QUERY_WORD 请求
            data = {"voc_id": word_id}
            message = f'WEBSTUDY_QUERY_WORD {json.dumps(data)}'
            await self.ws.send(message)

            # 接收响应
            response = await asyncio.wait_for(self.ws.recv(), timeout=10)

            if isinstance(response, bytes):
                return parse_word_response(response)

            return None

        except asyncio.TimeoutError:
            print("[错误] 查询单词超时")
            return None
        except Exception as e:
            print(f"[错误] 查询单词失败: {e}")
            return None
        finally:
            await self.close()


# ============================================================================
# 终端 UI
# ============================================================================

class StealthConsole:
    def __init__(self, console: Console):
        self.console = console
        self.word_version = f"{random.randint(2, 4)}.{random.randint(0, 9)}.{random.randint(0, 9)}"

    def _get_timestamp(self) -> str:
        return datetime.now().strftime("%H:%M:%S")

    def _select_disguise_template(self, word: str) -> str:
        template = random.choice(DISGUISE_TEMPLATES)
        return template.format(
            time=self._get_timestamp(),
            word=word,
            version=self.word_version
        )

    def display_disguised_word(self, word: str) -> None:
        disguise_log = self._select_disguise_template(word)
        parts = disguise_log.split('"')
        if len(parts) >= 2:
            text = Text()
            text.append(parts[0] + '"', style="cyan")
            text.append(word, style="bold bright_green")
            text.append('"' + parts[2], style="cyan")
        else:
            text = Text(disguise_log, style="cyan")
        self.console.print(text)

    def display_word_with_details(self, word_data: Dict, status: str = "") -> None:
        """
        展示单词详情（包含拼写、音标、释义）

        伪装成 API 响应格式
        """
        word = word_data.get("spelling", word_data.get("word", ""))
        phonetic_us = word_data.get("phonetic_us", "")
        phonetic_uk = word_data.get("phonetic_uk", "")
        interpretation = word_data.get("interpretation", "")
        difficulty = word_data.get("difficulty", 0)
        status_text = LEARNING_STATUS.get(status, status)

        # 构造 API 响应风格的数据
        response_json = {
            "status": "success",
            "code": 200,
            "data": {
                "word": word,
                "phonetic_us": phonetic_us,
                "phonetic_uk": phonetic_uk,
                "interpretation": interpretation,
                "difficulty": difficulty,
                "learning_status": status_text,
                "timestamp": datetime.now().isoformat()
            }
        }

        json_str = json.dumps(response_json, ensure_ascii=False, indent=2)
        lines = json_str.split('\n')
        styled_lines = []
        for line in lines:
            if any(k in line for k in ['"word"', '"phonetic', '"interpretation"', '"difficulty"']):
                styled_lines.append(Text(line, style="dim cyan"))
            elif '"learning_status"' in line:
                color = {"FAMILIAR": "bright_green", "VAGUE": "yellow", "FORGET": "bright_red"}.get(status, "white")
                styled_lines.append(Text(line, style=color))
            else:
                styled_lines.append(Text(line, style="white"))

        panel = Panel("\n".join([str(l) for l in styled_lines]),
                      title="[API Response]", border_style="dim", padding=(1, 2))
        self.console.print(panel)
        self.console.print()
        self.console.print(Text("Press any key to continue...", style="dim italic"))

    def display_word_detail(self, word_data: Dict, status: str = "") -> None:
        """向后兼容：调用新方法"""
        self.display_word_with_details(word_data, status)

    def display_progress(self, current: int, total: int, status: str = "") -> None:
        filled = int(20 * current / total) if total > 0 else 0
        bar = '=' * filled + '-' * (20 - filled)
        status_icon = {"FAMILIAR": '[OK]', "VAGUE": '[~]', "FORGET": '[X]'}.get(status, '')
        progress_text = f"\r[{bar}] {current}/{total} {status_icon}    "
        self.console.print(Text(progress_text, style="cyan"), end="")

    def trigger_boss_key(self) -> None:
        self.console.clear()
        fake_output = random.choice(FAKE_TRACEBACKS)
        additional_lines = []
        for _ in range(30):
            log_type = random.choice(["DEBUG", "INFO", "WARN", "ERROR"])
            modules = ["main.py", "compiler.py", "build.py", "settings.py", "utils.py"]
            actions = ["Loading configuration...", "Checking dependencies...", "Validating input..."]
            additional_lines.append(f"[{self._get_timestamp()}] [{log_type}] {random.choice(modules)}: {random.choice(actions)}")

        all_lines = additional_lines + ["", fake_output]
        for line in all_lines:
            if "ERROR" in line or "Traceback" in line or "failed" in line.lower():
                print(Text(line, style="bright_red"))
            elif "WARN" in line:
                print(Text(line, style="yellow"))
            else:
                print(Text(line, style="dim"))
        for _ in range(5):
            print()

    def wait_for_key(self) -> str:
        if readchar:
            return readchar()
        return input()

    def display_welcome(self, total: int) -> None:
        self.console.print()
        self.console.print(Text(f"MoFish WS v1.1 - 今日待复习: {total} 词", style="bold cyan"))
        self.console.print()
        help_text = Text("[1] 认识   [2] 模糊   [3] 忘记   [q] 保存退出   [b] Boss键", style="dim")
        self.console.print(help_text)
        self.console.print()

    def display_completion(self, learned: int, known: int, fuzzy: int, forgotten: int) -> None:
        self.console.print()
        table = Table(title="学习摘要", show_header=True, header_style="bold cyan")
        table.add_column("状态", style="white")
        table.add_column("数量", justify="right", style="cyan")
        table.add_column("占比", justify="right", style="dim")

        total = learned if learned > 0 else 1
        table.add_row("认识", str(known), f"{known*100//total}%")
        table.add_row("模糊", str(fuzzy), f"{fuzzy*100//total}%")
        table.add_row("忘记", str(forgotten), f"{forgotten*100//total}%")
        table.add_row("总计", str(learned), "100%")

        self.console.print(table)
        self.console.print()
        self.console.print(Text("进度已同步到云端", style="dim italic"))


# ============================================================================
# 配置管理
# ============================================================================

def load_config() -> Optional[str]:
    if not os.path.exists(CONFIG_FILE):
        return None
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("ws_token", "")
    except:
        return None

def save_config(api_token: str) -> bool:
    try:
        config = {}
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
        config["ws_token"] = api_token.strip()
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4)
        return True
    except:
        return False

def setup_config() -> Optional[str]:
    print()
    print("=" * 60)
    print("MoFish WS - 首次运行配置")
    print("=" * 60)
    print()
    print("请输入您的墨墨开放平台 WebSocket Token")
    print("(注意: 需要使用 WebSocket 专用 token，不是 REST API token)")
    print()
    print("获取地址: https://open.maimemo.com")
    print()

    while True:
        token = input("请输入 Token: ").strip()
        if not token:
            print("Token 不能为空")
            continue
        if len(token) < 10:
            print("Token 长度过短")
            continue
        confirm = input("确认保存此 Token? (y/n): ").strip().lower()
        if confirm == 'y':
            break

    # 保存到 config.json
    try:
        config = {}
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
        config["ws_token"] = token.strip()
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4)
        print(f"配置已保存到 {CONFIG_FILE}")
        return token
    except:
        return None


# ============================================================================
# 主程序
# ============================================================================

async def run_study(client: MaimemoWSClient, console: Console):
    """运行学习循环"""
    stealth = StealthConsole(console)

    # 学习状态统计
    stats = {"known": 0, "fuzzy": 0, "forgotten": 0}
    current_index = 0
    total_words = 999  # 未知总数，先设置一个大的

    stealth.display_welcome(total_words)

    while True:
        # 获取单词（完整信息）
        word_data = await client.get_word()
        if not word_data or not word_data.get("id"):
            console.print(Text("\n[错误] 获取单词失败，退出", style="bold red"))
            break

        word_id = word_data.get("id", "")
        spelling = word_data.get("spelling", word_id)
        phonetic = word_data.get("phonetic_us", "") or word_data.get("phonetic_uk", "")

        # 清屏并显示进度
        console.clear()
        stealth.display_progress(current_index + 1, total_words, "")
        stealth.display_disguised_word(spelling)

        # 如果有音标，也显示一下
        if phonetic:
            console.print(Text(f"  [{phonetic}]", style="dim"))

        # 等待用户按键
        key = stealth.wait_for_key()

        if key.lower() == 'q':
            break

        elif key.lower() == 'b':
            stealth.trigger_boss_key()
            input()
            continue

        elif key in ['1', '2', '3']:
            status = KEY_TO_STATUS.get(key, "FORGET")

            # 提交反馈
            await client.submit_response(
                word_id=word_id,
                response=status,
                recall_duration=random.randint(500, 2000),
                study_duration=random.randint(1000, 3000),
                study_method="STUDY_CN_EN"
            )

            # 更新统计
            if status == "FAMILIAR":
                stats["known"] += 1
            elif status == "VAGUE":
                stats["fuzzy"] += 1
            else:
                stats["forgotten"] += 1

            # 显示详情（包含完整单词信息）
            stealth.display_word_with_details(word_data, status)
            stealth.wait_for_key()
            current_index += 1

        else:
            continue

    # 完成
    console.clear()
    stealth.display_completion(
        learned=current_index,
        known=stats["known"],
        fuzzy=stats["fuzzy"],
        forgotten=stats["forgotten"]
    )


async def verify_ws_token(token: str) -> bool:
    """验证 WebSocket token 是否有效"""
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    url = f"{WS_URL}?token={token}"
    try:
        async with websockets.connect(url, ssl=ssl_ctx, open_timeout=5) as ws:
            # 尝试发送 GET_WORD
            await ws.send('WEBSTUDY_GET_WORD')
            # 等待响应
            try:
                resp = await asyncio.wait_for(ws.recv(), timeout=5)
                # 如果收到响应而不是关闭连接，说明 token 有效
                return True
            except asyncio.TimeoutError:
                return True  # 超时也认为可能有效
    except websockets.exceptions.ConnectionClosed as e:
        # 检查是否是认证错误
        if e.code == 3401 or "3401" in str(e):
            return False
        return False
    except Exception:
        return False


async def main_async():
    """异步主程序"""
    console = Console()
    console.clear()

    # 打印启动信息
    print(Text("[14:00:00] [INFO] MoFish WS initializing...", style="cyan"))
    time.sleep(0.3)
    print(Text("[14:00:00] [DEBUG] Loading configuration...", style="dim"))
    time.sleep(0.2)
    print()

    # 加载配置
    api_token = load_config()
    if not api_token:
        api_token = setup_config()
        if not api_token:
            console.print(Text("[错误] 配置失败", style="bold red"))
            sys.exit(1)
    else:
        # 验证现有 token
        console.print(Text("[14:00:00] [INFO] Verifying WebSocket token...", style="cyan"))
        is_valid = await verify_ws_token(api_token)
        if not is_valid:
            console.print(Text("[错误] Token 验证失败!", style="bold red"))
            console.print()
            console.print(Text("WebSocket 需要专用的 Token，不是 REST API Token", style="yellow"))
            console.print(Text("请到以下地址获取 WebSocket Token:", style="cyan"))
            console.print(Text("  https://open.maimemo.com", style="dim"))
            console.print()
            confirm = input("是否重新输入 Token? (y/n): ").strip().lower()
            if confirm == 'y':
                api_token = setup_config()
                if not api_token:
                    sys.exit(1)
            else:
                sys.exit(1)

    # 创建客户端
    client = MaimemoWSClient(api_token)

    print(Text("[14:00:00] [DEBUG] Connecting to WebSocket...", style="dim"))

    # 测试连接
    if not await client.connect():
        sys.exit(1)

    await client.close()
    print(Text("[14:00:00] [INFO] Connected successfully", style="cyan"))
    time.sleep(0.5)

    # 运行学习
    await run_study(client, console)

    console.print(Text("[INFO] Session closed. Goodbye!", style="dim"))
    time.sleep(1)
    console.clear()


def main():
    """同步入口"""
    import argparse
    parser = argparse.ArgumentParser(description="MoFish WS - 墨墨背单词 WebSocket 版")
    parser.add_argument("--clear-token", "-c", action="store_true", help="清除 Token")
    args = parser.parse_args()

    if args.clear_token:
        if os.path.exists(CONFIG_FILE):
            os.remove(CONFIG_FILE)
            print("Token 已清除")
        sys.exit(0)

    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        print("\n[INFO] 中断退出")
        sys.exit(0)


if __name__ == "__main__":
    main()
