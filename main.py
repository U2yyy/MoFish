#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
墨墨背单词 - 主入口
===================

协调 REST API 获取单词列表，WebSocket 进行学习

作者: MoFish CLI Team
Python: 3.8+
"""

import os
import sys
import json
import time
import asyncio
import ssl
import requests
from datetime import datetime
from typing import Optional, List, Dict, Any
from rich.console import Console
from rich.table import Table
from rich.text import Text

# ============================================================================
# 配置
# ============================================================================

CONFIG_FILE = "config.json"
API_BASE_URL = "https://open.maimemo.com/open"
WS_URL = "wss://tc-apis.maimemo.com/study/ws/webstudy"

# ============================================================================
# 依赖检查
# ============================================================================

try:
    import websockets
except ImportError:
    print("[错误] 缺少 websockets 库，请运行: pip install websockets")
    sys.exit(1)

try:
    from rich.console import Console
except ImportError:
    print("[错误] 缺少 rich 库，请运行: pip install rich")
    sys.exit(1)

# ============================================================================
# REST API 客户端
# ============================================================================

class MaimemoRESTClient:
    """REST API 客户端，用于获取单词列表"""

    def __init__(self, api_token: str):
        self.api_token = api_token.strip()
        self.base_url = API_BASE_URL
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        self.timeout = (10, 30)

    def get_today_items(self, limit: int = 100, is_finished: bool = None) -> List[Dict[str, Any]]:
        """获取今日学习单词"""
        try:
            payload = {"limit": limit}
            if is_finished is not None:
                payload["is_finished"] = is_finished
            resp = self.session.post(
                f"{self.base_url}/api/v1/study/get_today_items",
                json=payload,
                timeout=self.timeout
            )
            if resp.ok:
                return resp.json().get("data", {}).get("today_items", [])
        except Exception as e:
            print(f"[错误] 获取今日任务失败: {e}")
        return []

    def get_study_progress(self) -> Dict[str, Any]:
        """获取今日学习进度"""
        try:
            resp = self.session.post(
                f"{self.base_url}/api/v1/study/get_study_progress",
                json={},
                timeout=self.timeout
            )
            if resp.ok:
                return resp.json().get("data", {}).get("progress", {})
        except Exception as e:
            print(f"[错误] 获取学习进度失败: {e}")
        return {}

    def query_vocabulary(self, spellings: List[str]) -> Dict[str, Dict]:
        """批量查询单词详情"""
        try:
            resp = self.session.post(
                f"{self.base_url}/api/v1/vocabulary/query",
                json={"spellings": spellings},
                timeout=self.timeout
            )
            if resp.ok:
                result = {}
                for item in resp.json().get("voc", []):
                    result[item.get("spelling", "")] = item
                return result
        except Exception as e:
            print(f"[警告] 查询单词详情失败: {e}")
        return {}

    def close(self):
        self.session.close()


# ============================================================================
# WebSocket 学习客户端
# ============================================================================

class MaimemoWSLearner:
    """WebSocket 学习客户端"""

    def __init__(self, api_token: str):
        self.api_token = api_token.strip()
        self.ssl_ctx = ssl.create_default_context()
        self.ssl_ctx.check_hostname = False
        self.ssl_ctx.verify_mode = ssl.CERT_NONE

    async def connect(self) -> Optional[Any]:
        url = f"{WS_URL}?token={self.api_token}"
        try:
            return await websockets.connect(url, ssl=self.ssl_ctx)
        except Exception as e:
            print(f"[错误] WebSocket 连接失败: {e}")
            return None

    async def get_word_uuid(self) -> Optional[str]:
        """获取单词 UUID"""
        ws = await self.connect()
        if not ws:
            return None

        try:
            await ws.send('WEBSTUDY_INIT_STUDY {}')
            await ws.recv()  # 忽略初始化响应

            await ws.send('WEBSTUDY_GET_WORD')
            resp = await asyncio.wait_for(ws.recv(), timeout=10)

            if isinstance(resp, bytes) and len(resp) > 38:
                uuid = resp[2:38].decode('utf-8')
                await ws.close()
                return uuid

            await ws.close()
        except Exception as e:
            print(f"[错误] 获取单词失败: {e}")
            await ws.close()

        return None

    async def submit_response(
        self,
        word_id: str,
        response: str = "FAMILIAR",
        recall_duration: int = 1000,
        study_duration: int = 2000
    ) -> bool:
        """提交学习反馈"""
        ws = await self.connect()
        if not ws:
            return False

        try:
            # 初始化
            await ws.send('WEBSTUDY_INIT_STUDY {}')
            await ws.recv()

            # 提交反馈
            data = {
                "voc_id": word_id,
                "response": response,
                "recall_duration": recall_duration,
                "study_duration": study_duration,
                "study_method": "STUDY_CN_EN"
            }
            await ws.send(f'WEBSTUDY_SUBMIT_RESPONSE {json.dumps(data)}')
            await asyncio.wait_for(ws.recv(), timeout=10)

            await ws.close()
            return True

        except Exception as e:
            print(f"[错误] 提交反馈失败: {e}")
            await ws.close()
            return False


# ============================================================================
# 主程序
# ============================================================================

def load_config() -> Dict[str, str]:
    """加载配置"""
    if not os.path.exists(CONFIG_FILE):
        return {}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}


def main():
    console = Console()
    console.clear()

    # 加载配置
    config = load_config()
    ws_token = config.get("ws_token", "")
    rest_token = config.get("rest_token", "")

    if not ws_token or not rest_token:
        console.print(Text("[错误] 配置不完整，请检查 config.json", style="bold red"))
        console.print(Text("需要 ws_token 和 rest_token", style="yellow"))
        sys.exit(1)

    # 打印启动信息
    print(Text("[14:00:00] [INFO] MoFish initializing...", style="cyan"))
    time.sleep(0.3)
    print(Text("[14:00:00] [DEBUG] Loading configuration...", style="dim"))
    time.sleep(0.2)
    print()

    # 获取今日单词和学习进度
    console.print(Text("[14:00:00] [INFO] Fetching today's study task...", style="cyan"))
    client = MaimemoRESTClient(rest_token)

    try:
        # 获取学习进度
        progress = client.get_study_progress()
        finished = progress.get("finished", 0)
        total = progress.get("total", 0)
        study_time = progress.get("study_time", 0)  # 毫秒
        study_minutes = study_time // 60000 if study_time else 0
    finally:
        client.close()

    console.print(Text(f"[14:00:00] [INFO] Progress: {finished}/{total}, study time: {study_minutes}min", style="cyan"))
    time.sleep(0.5)

    # 检查是否完成
    if finished >= total and total > 0:
        console.print()
        console.print(Text("=" * 50, style="dim"))
        console.print(Text("  太棒了！今日任务已全部完成！", style="bright_green"))
        console.print(Text(f"  已学习: {finished} 词  |  学习时长: {study_minutes} 分钟", style="dim"))
        console.print(Text("=" * 50, style="dim"))
        console.print()
        sys.exit(0)

    # 显示进度信息
    remaining = total - finished
    console.print()
    console.print(Text("=" * 50, style="dim"))
    console.print(Text(f"  今日进度: {finished}/{total} 词 (剩余 {remaining} 词)", style="bold cyan"))
    console.print(Text("=" * 50, style="dim"))
    console.print()

    choice = input("是否开始学习? (y/n): ").strip().lower()

    if choice != 'y':
        console.print(Text("已取消", style="dim"))
        sys.exit(0)

    # 开始学习循环
    console.clear()
    console.print(Text("[14:00:00] [INFO] Starting WebSocket learning session...", style="cyan"))
    time.sleep(0.5)

    learned = []
    stats = {"known": 0, "fuzzy": 0, "forgotten": 0}
    current = 0

    learner = MaimemoWSLearner(ws_token)

    # 学习循环 - 直接使用 WS 获取单词
    async def learning_loop():
        nonlocal current
        import random

        while True:
            current += 1

            # 显示进度 (基于已学计数)
            filled = int(20 * (current - 1) / total) if total > 0 else 0
            bar = '=' * filled + '-' * (20 - filled)
            console.print(Text(f"\r[{bar}] {finished + current - 1}/{total}    ", style="cyan"), end="")

            # 在同一次 WS 连接中获取 UUID 并提交
            ws = await learner.connect()
            if not ws:
                console.print(Text(f"\n[错误] 连接失败，退出", style="bold red"))
                break

            try:
                # 初始化
                await ws.send('WEBSTUDY_INIT_STUDY {}')
                init_resp = await asyncio.wait_for(ws.recv(), timeout=10)
                if not isinstance(init_resp, bytes):
                    console.print(Text(f"\n[错误] 初始化失败", style="bold red"))
                    break

                # 获取单词 UUID
                await ws.send('WEBSTUDY_GET_WORD')
                resp = await asyncio.wait_for(ws.recv(), timeout=10)
                if not isinstance(resp, bytes) or len(resp) < 38:
                    console.print(Text(f"\n[提示] 学习完成，没有更多单词", style="bright_green"))
                    break

                ws_uuid = resp[2:38].decode('utf-8')

                # 显示伪装日志 (用 UUID 前8位作为假单词)
                fake_word = ws_uuid[:8]
                disguise_templates = [
                    '[{time}] [INFO] Loading package "{word}" (v3.2.1) successfully.',
                    '[{time}] [DEBUG] Query SELECT * FROM vocabulary WHERE word="{word}" LIMIT 1;',
                    '[{time}] [INFO] Module "{word}" imported successfully.',
                ]
                template = random.choice(disguise_templates)
                timestamp = datetime.now().strftime("%H:%M:%S")
                log = template.format(time=timestamp, word=fake_word)

                # 换行后显示伪装日志
                console.print()
                parts = log.split('"')
                if len(parts) >= 2:
                    text = Text()
                    text.append(parts[0] + '"', style="cyan")
                    text.append(fake_word, style="bold bright_green")
                    text.append('"' + parts[2], style="cyan")
                    console.print(text)
                else:
                    console.print(Text(log, style="cyan"))

                console.print()
                console.print(Text("[1] 认识   [2] 模糊   [3] 忘记   [q] 退出", style="dim"))

                # 等待按键
                key = input("\n请选择: ").strip().lower()

                if key == 'q':
                    break

                if key not in ['1', '2', '3']:
                    continue

                # 映射到状态
                status_map = {"1": "FAMILIAR", "2": "VAGUE", "3": "FORGET"}
                status = status_map[key]

                # 提交反馈
                data = {
                    "voc_id": ws_uuid,
                    "response": status,
                    "recall_duration": random.randint(500, 2000),
                    "study_duration": random.randint(1000, 3000),
                    "study_method": "STUDY_CN_EN"
                }
                await ws.send(f'WEBSTUDY_SUBMIT_RESPONSE {json.dumps(data)}')
                await asyncio.wait_for(ws.recv(), timeout=10)

                # 更新统计
                learned.append({"word_id": ws_uuid, "status": status})
                if status == "FAMILIAR":
                    stats["known"] += 1
                elif status == "VAGUE":
                    stats["fuzzy"] += 1
                else:
                    stats["forgotten"] += 1

            except Exception as e:
                console.print(Text(f"\n[错误] {e}", style="bold red"))
            finally:
                await ws.close()

        return stats, learned

    # 运行异步学习循环
    stats, learned = asyncio.run(learning_loop())

    # 学习完成
    console.clear()
    console.print()

    table = Table(title="学习摘要", show_header=True, header_style="bold cyan")
    table.add_column("状态", style="white")
    table.add_column("数量", justify="right", style="cyan")
    table.add_column("占比", justify="right", style="dim")

    total_learned = len(learned)
    t = total_learned if total_learned > 0 else 1
    table.add_row("认识", str(stats["known"]), f"{stats['known']*100//t}%")
    table.add_row("模糊", str(stats["fuzzy"]), f"{stats['fuzzy']*100//t}%")
    table.add_row("忘记", str(stats["forgotten"]), f"{stats['forgotten']*100//t}%")
    table.add_row("总计", str(total_learned), "100%")

    console.print(table)
    console.print()
    console.print(Text("进度已同步到云端", style="dim italic"))

    time.sleep(2)
    console.clear()


if __name__ == "__main__":
    main()
