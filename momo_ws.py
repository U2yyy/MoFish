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
# Protobuf 二进制编解码 (手写最小实现，无需 protoc)
#
# 通过分析网页前端的 vendors.416116d4.js 还原:
#   WebsocketProtocolMessage { 1:id(str) 2:reply_id(str) 3:event(int32) 4:data(bytes) 5:success(bool) }
#   WsWebStudyInitStudyRequest {}
#   WsWebStudyGetWordRequest { 1:back(bool) }
#   WsWebStudySubmitResponseRequest { 1:voc_id(str) 2:response(int32 enum)
#                                     3:study_method(int32 enum) 4:recall_duration(int32) 5:study_duration(int32) }
#   WsWebStudyGetWordResponse { 1:word(WebStudyWord) 2:interpretations(repeated)
#                               3:phrases 4:notes 23:study_time_ms }
#   WebStudyWord { 1:id(str) 2:voc_id(int32) 3:spelling 4:phonetic_us 5:phonetic_uk 7:difficulty }
#   WebStudyInterpretation { 1:id 2:voc_id 3:interpretation(str) }
#   WsWebStudySubmitResponseResponse { 1:next(WsWebStudyGetWordResponse) }
# ============================================================================

# WebsocketProtocolEvent 枚举
EV_SYSTEM_READY = 1
EV_SYSTEM_PING = 2
EV_WEBSTUDY_INIT_STUDY = 1001
EV_WEBSTUDY_GET_WORD = 1002
EV_WEBSTUDY_SUBMIT_RESPONSE = 1003

EVENT_NAMES = {
    1: "SYSTEM_READY", 2: "SYSTEM_PING", 3: "SYSTEM_SUBSCRIBE_TOPICS",
    1001: "WEBSTUDY_INIT_STUDY", 1002: "WEBSTUDY_GET_WORD", 1003: "WEBSTUDY_SUBMIT_RESPONSE",
}

# StudyResponse 枚举
STUDY_RESPONSE = {"FAMILIAR": 1, "VAGUE": 2, "FORGET": 3, "WELL_FAMILIAR": 4, "CANCEL_WELL_FAMILIAR": 5}
# StudyMethod 枚举
STUDY_METHOD = {"STUDY_EN_CN": 0, "STUDY_CN_EN": 1}


def _enc_varint(v: int) -> bytes:
    """编码无符号 varint"""
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _enc_tag(field: int, wire: int) -> bytes:
    return _enc_varint((field << 3) | wire)


def _enc_string(field: int, s: str) -> bytes:
    data = s.encode("utf-8")
    return _enc_tag(field, 2) + _enc_varint(len(data)) + data


def _enc_bytes(field: int, b: bytes) -> bytes:
    return _enc_tag(field, 2) + _enc_varint(len(b)) + b


def _enc_int32(field: int, v: int) -> bytes:
    return _enc_tag(field, 0) + _enc_varint(v & 0xFFFFFFFFFFFFFFFF)


def _enc_bool(field: int, v: bool) -> bytes:
    return _enc_tag(field, 0) + _enc_varint(1 if v else 0)


def _dec_varint(buf: bytes, pos: int):
    """解码 varint，返回 (value, new_pos)"""
    result, shift = 0, 0
    while pos < len(buf):
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7
    return result, pos


def _decode_message(buf: bytes) -> Dict[int, list]:
    """
    通用解码器：返回 {field_num: [value, value, ...]}
    wire=0 -> int
    wire=2 -> bytes (string 由调用者按需 decode)
    wire=1 -> int (64bit, 当 raw bytes 处理)
    wire=5 -> int (32bit)
    """
    out: Dict[int, list] = {}
    pos = 0
    while pos < len(buf):
        tag, pos = _dec_varint(buf, pos)
        if tag == 0:
            break
        field, wire = tag >> 3, tag & 7
        if wire == 0:
            val, pos = _dec_varint(buf, pos)
        elif wire == 2:
            length, pos = _dec_varint(buf, pos)
            val = buf[pos:pos + length]
            pos += length
        elif wire == 1:
            val = buf[pos:pos + 8]
            pos += 8
        elif wire == 5:
            val = buf[pos:pos + 4]
            pos += 4
        else:
            break
        out.setdefault(field, []).append(val)
    return out


def _get_str(fields: Dict[int, list], num: int, default: str = "") -> str:
    v = fields.get(num)
    if not v:
        return default
    raw = v[0]
    return raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)


def _get_int(fields: Dict[int, list], num: int, default: int = 0) -> int:
    v = fields.get(num)
    return int(v[0]) if v else default


def _get_bytes_list(fields: Dict[int, list], num: int) -> list:
    return [v for v in fields.get(num, []) if isinstance(v, (bytes, bytearray))]


# ----------------------------------------------------------------------------
# 业务消息编码
# ----------------------------------------------------------------------------

def encode_envelope(msg_id: str, event: int, data: bytes = b"") -> bytes:
    """
    编码外壳 WebsocketProtocolMessage。

    注意: JS encoder 对空 body 请求也会写 field 4 = 空 bytes (即 `22 00`)。
    缺失 data 字段会被网关识别为"无 body"直接丢弃，必须显式写出来。
    """
    out = b""
    out += _enc_string(1, msg_id)
    out += _enc_int32(3, event)
    out += _enc_bytes(4, data)
    out += _enc_bool(5, True)
    return out


def encode_get_word_request(back: bool) -> bytes:
    if not back:
        return b""
    return _enc_bool(1, True)


def encode_submit_request(voc_id: str, response: str, study_method: str,
                          recall_duration: int, study_duration: int) -> bytes:
    out = b""
    if voc_id:
        out += _enc_string(1, voc_id)
    resp_n = STUDY_RESPONSE.get(response, 0)
    if resp_n:
        out += _enc_int32(2, resp_n)
    sm_n = STUDY_METHOD.get(study_method, 0)
    if sm_n:
        out += _enc_int32(3, sm_n)
    if recall_duration:
        out += _enc_int32(4, recall_duration)
    if study_duration:
        out += _enc_int32(5, study_duration)
    return out


# ----------------------------------------------------------------------------
# 业务消息解析
# ----------------------------------------------------------------------------

def parse_web_study_word(buf: bytes) -> Dict[str, Any]:
    """解析 WebStudyWord"""
    f = _decode_message(buf)
    return {
        "id": _get_str(f, 1),
        "voc_id": _get_int(f, 2),
        "spelling": _get_str(f, 3),
        "phonetic_us": _get_str(f, 4),
        "phonetic_uk": _get_str(f, 5),
        "hyphenation": _get_str(f, 6),
        "difficulty": _get_int(f, 7),
    }


def parse_get_word_response(buf: bytes) -> Dict[str, Any]:
    """解析 WsWebStudyGetWordResponse"""
    f = _decode_message(buf)
    word_bufs = _get_bytes_list(f, 1)
    word = parse_web_study_word(word_bufs[0]) if word_bufs else {}

    interpretations = []
    for ib in _get_bytes_list(f, 2):
        fi = _decode_message(ib)
        text = _get_str(fi, 3)
        if text:
            interpretations.append(text)

    return {
        "id": word.get("id", ""),
        "voc_id": word.get("voc_id", 0),
        "spelling": word.get("spelling", ""),
        "phonetic_us": word.get("phonetic_us", ""),
        "phonetic_uk": word.get("phonetic_uk", ""),
        "difficulty": word.get("difficulty", 0),
        "interpretation": "\n".join(interpretations),
        "interpretations": interpretations,
        "study_time_ms": _get_int(f, 23),
    }


def parse_envelope(buf: bytes) -> Dict[str, Any]:
    """解析外壳 WebsocketProtocolMessage（含 errors 字段）"""
    f = _decode_message(buf)
    errors = []
    for err_buf in _get_bytes_list(f, 6):
        ef = _decode_message(err_buf)
        # WebsocketProtocolError 至少包含 code/kind(string) 和 message(string)
        err = {}
        for fnum, vals in ef.items():
            for v in vals:
                if isinstance(v, (bytes, bytearray)):
                    try:
                        err[fnum] = v.decode("utf-8", errors="replace")
                    except Exception:
                        err[fnum] = repr(v)
                else:
                    err[fnum] = v
        errors.append(err)
    return {
        "id": _get_str(f, 1),
        "reply_id": _get_str(f, 2),
        "event": _get_int(f, 3),
        "data": (_get_bytes_list(f, 4)[0] if _get_bytes_list(f, 4) else b""),
        "success": bool(_get_int(f, 5, 0)),
        "errors": errors,
    }


# 向后兼容旧函数名
def parse_word_response(data: bytes) -> Dict[str, Any]:
    return parse_get_word_response(data)


# ============================================================================
# WebSocket 学习客户端 (单连接 + 后台 recv loop + reply_id 路由)
# ============================================================================

class MaimemoWSClient:
    """
    基于 WebSocket 的墨墨学习客户端 (纯二进制 Protobuf 协议)

    - 单连接长期复用
    - 后台 recv loop 持续解码服务端推送的 envelope
    - SYSTEM_PING 自动二进制回复
    - 业务请求通过 reply_id 路由到 Future
    """

    def __init__(self, api_token: str):
        self.api_token = api_token.strip()
        self.ws = None
        self._create_ssl_context()
        self._connected = False
        self._msg_seq = 0
        self._pending: Dict[str, asyncio.Future] = {}
        self._recv_task: Optional[asyncio.Task] = None
        self._initialized = False
        self._init_payload: Optional[bytes] = None
        self._ready_event: Optional[asyncio.Event] = None
        # 反作弊：服务端会检查 study_duration <= 实际从 get_word 到 submit 的时间
        self._last_word_received_at: Optional[float] = None
        self.last_next_word: Optional[Dict[str, Any]] = None

    def _create_ssl_context(self):
        self.ssl_ctx = ssl.create_default_context()
        self.ssl_ctx.check_hostname = False
        self.ssl_ctx.verify_mode = ssl.CERT_NONE

    def _next_id(self) -> str:
        self._msg_seq += 1
        return f"req-{self._msg_seq}"

    async def connect(self) -> bool:
        """
        建立 WebSocket 连接，启动后台 recv loop。

        必须等到 SYSTEM_READY 收到后才返回——否则后续业务请求会被网关以
        `study_internal_error` 拒绝。
        """
        if self._connected and self.ws is not None:
            return True
        url = f"{WS_URL}?token={self.api_token}"
        try:
            self.ws = await websockets.connect(url, ssl=self.ssl_ctx)
            self._connected = True
            self._ready_event = asyncio.Event()
            self._recv_task = asyncio.create_task(self._recv_loop())
            try:
                await asyncio.wait_for(self._ready_event.wait(), timeout=10)
            except asyncio.TimeoutError:
                print("[错误] 等待 SYSTEM_READY 超时")
                await self.close()
                return False
            return True
        except Exception as e:
            print(f"[错误] WebSocket 连接失败: {e}")
            self._connected = False
            return False

    async def close(self):
        self._connected = False
        if self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()
            try:
                await self._recv_task
            except (asyncio.CancelledError, Exception):
                pass
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
        self.ws = None
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(ConnectionError("连接已关闭"))
        self._pending.clear()

    async def _send_envelope(self, msg_id: str, event: int, data: bytes = b""):
        """发送一个 envelope"""
        frame = encode_envelope(msg_id, event, data)
        await self.ws.send(frame)

    async def _reply_ping(self, ping_id: str):
        """回复 SYSTEM_PING：把对方的 id 放进 reply_id，event=SYSTEM_PING"""
        out = b""
        out += _enc_string(2, ping_id)
        out += _enc_int32(3, EV_SYSTEM_PING)
        out += _enc_bool(5, True)
        try:
            await self.ws.send(out)
        except Exception:
            pass

    async def _recv_loop(self):
        """后台读帧：分发给 pending future 或处理系统事件"""
        try:
            while self._connected and self.ws is not None:
                try:
                    frame = await self.ws.recv()
                except websockets.exceptions.ConnectionClosed:
                    break
                if not isinstance(frame, (bytes, bytearray)):
                    continue
                env = parse_envelope(bytes(frame))
                event = env["event"]
                reply_id = env["reply_id"]
                msg_id = env["id"]
                if os.environ.get("MOFISH_WS_TRACE"):
                    print(f"[trace] recv frame_len={len(frame)} event={event} reply_id={reply_id!r} msg_id={msg_id!r} data_len={len(env['data'])}  raw_head={bytes(frame)[:30].hex()}  pending={list(self._pending.keys())}")

                # SYSTEM_PING -> 自动回复
                if event == EV_SYSTEM_PING:
                    await self._reply_ping(msg_id)
                    continue

                # SYSTEM_READY: 服务端在连接建立时主动推送
                if event == EV_SYSTEM_READY:
                    if self._ready_event is not None:
                        self._ready_event.set()
                    continue

                # 业务响应：通过 reply_id 路由
                if reply_id and reply_id in self._pending:
                    fut = self._pending.pop(reply_id)
                    if not fut.done():
                        fut.set_result(env)
                    continue

                # 服务端主动推送的 INIT 数据 (没有 reply_id 时)
                if event == EV_WEBSTUDY_INIT_STUDY:
                    self._init_payload = env["data"]
                    self._initialized = True
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[警告] recv loop 异常: {e}")
        finally:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError("连接已断开"))
            self._pending.clear()

    async def _request(self, event: int, data: bytes = b"", timeout: float = 10) -> Optional[Dict[str, Any]]:
        """通用请求-响应：发送 envelope，按 reply_id 等待"""
        if not self._connected or self.ws is None:
            print("[错误] WebSocket 未连接")
            return None
        msg_id = self._next_id()
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[msg_id] = fut
        try:
            await self._send_envelope(msg_id, event, data)
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(msg_id, None)
            print(f"[错误] {EVENT_NAMES.get(event, event)} 超时")
            return None
        except Exception as e:
            self._pending.pop(msg_id, None)
            print(f"[错误] {EVENT_NAMES.get(event, event)} 失败: {e}")
            return None

    async def initialize(self) -> bool:
        """init_study 的别名"""
        return await self.init_study()

    async def init_study(self) -> bool:
        """初始化学习会话 (WEBSTUDY_INIT_STUDY)"""
        env = await self._request(EV_WEBSTUDY_INIT_STUDY, b"", timeout=15)
        if env is None:
            return False
        self._initialized = True
        self._init_payload = env.get("data", b"")
        return True

    async def get_word(self, back: bool = False) -> Optional[Dict[str, Any]]:
        """拉取下一个单词 (WEBSTUDY_GET_WORD)"""
        data = encode_get_word_request(back)
        env = await self._request(EV_WEBSTUDY_GET_WORD, data, timeout=10)
        if env is None:
            return None
        result = parse_get_word_response(env.get("data", b""))
        if result.get("id"):
            self._last_word_received_at = time.monotonic()
        return result

    async def submit_response(
        self,
        word_id: str,
        response: str = "FAMILIAR",
        recall_duration: int = 1000,
        study_duration: int = 2000,
        study_method: str = "STUDY_CN_EN",
    ) -> bool:
        """
        提交学习反馈 (WEBSTUDY_SUBMIT_RESPONSE)

        返回 True 表示服务端接受。响应内嵌的下一个单词存在 self.last_next_word 里供调用方读取。
        """
        # 反作弊封顶: study_duration 不能超过实际从 GET_WORD 到现在的经过时间
        if self._last_word_received_at is not None:
            elapsed_ms = int((time.monotonic() - self._last_word_received_at) * 1000)
            # 留 100ms 余量给网络抖动
            cap_ms = max(0, elapsed_ms - 100)
            if study_duration > cap_ms:
                study_duration = cap_ms
            if recall_duration > cap_ms:
                recall_duration = cap_ms

        data = encode_submit_request(
            voc_id=word_id,
            response=response,
            study_method=study_method,
            recall_duration=recall_duration,
            study_duration=study_duration,
        )
        env = await self._request(EV_WEBSTUDY_SUBMIT_RESPONSE, data, timeout=10)
        if env is None:
            return False
        if not env.get("success", True):
            print(f"[错误] SUBMIT_RESPONSE 被服务端拒绝: {env.get('errors')}")
            self.last_next_word = None
            return False
        # SubmitResponse 内嵌 next=WsWebStudyGetWordResponse (field 1)
        body = _decode_message(env.get("data", b""))
        next_bufs = _get_bytes_list(body, 1)
        self.last_next_word = parse_get_word_response(next_bufs[0]) if next_bufs else None
        # submit 成功后，下一个单词等价于刚被推送过来——重置计时基准
        if self.last_next_word and self.last_next_word.get("id"):
            self._last_word_received_at = time.monotonic()
        return True


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

    # 必须先初始化学习会话
    if not await client.init_study():
        console.print(Text("\n[错误] 初始化学习会话失败", style="bold red"))
        return

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
    """验证 WebSocket token 是否有效（仅检查能否建立连接）"""
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    url = f"{WS_URL}?token={token}"
    try:
        async with websockets.connect(url, ssl=ssl_ctx, open_timeout=5) as ws:
            try:
                await asyncio.wait_for(ws.recv(), timeout=3)
                return True
            except asyncio.TimeoutError:
                return True
    except websockets.exceptions.ConnectionClosed as e:
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

    if not await client.connect():
        sys.exit(1)

    print(Text("[14:00:00] [INFO] Connected successfully", style="cyan"))
    time.sleep(0.5)

    try:
        await run_study(client, console)
    finally:
        await client.close()

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
