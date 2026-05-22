#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
墨墨背单词 WebSocket 客户端
==========================

逆向自网页客户端 `tc-apis.maimemo.com` 的私有 Protobuf 协议。

协议要点（用 protoc 生成的 `_pb2.py` 不存在，全部手写编解码）：

1. 信封 ``WebsocketProtocolMessage``::

       1: id        string   请求 ID
       2: reply_id  string   回复对端某条请求的 ID
       3: event     int32    见 EVENT_NAMES
       4: data      bytes    业务消息体（Protobuf）
       5: success   bool
       6: errors    repeated WebsocketProtocolError

   注意：JS 端对空 body 也会写 ``field 4 = b""``（即 ``22 00``）。
   缺这个字段网关会判定为"无 body"直接丢弃，必须显式写出来。

2. 连接握手：服务端在 ``wss://tc-apis.maimemo.com/study/ws/webstudy?token=...``
   建联后会主动推一帧 ``event=SYSTEM_READY``。**必须等到这帧才能发业务请求**，
   否则会被 ``study_internal_error`` 拒绝。

3. 反作弊：``WEBSTUDY_SUBMIT_RESPONSE`` 中的 ``study_duration`` /
   ``recall_duration`` 不能超过实际从 ``GET_WORD`` 到 ``SUBMIT`` 的墙钟时间。
   服务端有时间校验，超过会返回 ``webstudy_invalid_param``。
"""

from __future__ import annotations

import os
import ssl
import time
import asyncio
from typing import Optional, Dict, Any, List

import websockets


# ---------------------------------------------------------------------------
# 协议常量
# ---------------------------------------------------------------------------

WS_URL = "wss://tc-apis.maimemo.com/study/ws/webstudy"

# WebsocketProtocolEvent 枚举（来自前端 JS）
EV_SYSTEM_READY = 1
EV_SYSTEM_PING = 2
EV_WEBSTUDY_INIT_STUDY = 1001
EV_WEBSTUDY_GET_WORD = 1002
EV_WEBSTUDY_SUBMIT_RESPONSE = 1003

EVENT_NAMES: Dict[int, str] = {
    1: "SYSTEM_READY",
    2: "SYSTEM_PING",
    3: "SYSTEM_SUBSCRIBE_TOPICS",
    1001: "WEBSTUDY_INIT_STUDY",
    1002: "WEBSTUDY_GET_WORD",
    1003: "WEBSTUDY_SUBMIT_RESPONSE",
}

# 学习反馈枚举
STUDY_RESPONSE: Dict[str, int] = {
    "FAMILIAR": 1, "VAGUE": 2, "FORGET": 3,
    "WELL_FAMILIAR": 4, "CANCEL_WELL_FAMILIAR": 5,
}
STUDY_METHOD: Dict[str, int] = {"STUDY_EN_CN": 0, "STUDY_CN_EN": 1}


# ---------------------------------------------------------------------------
# Protobuf 手写编解码（无 protoc 依赖）
# ---------------------------------------------------------------------------

def _enc_varint(v: int) -> bytes:
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
    """通用解码：返回 ``{field_num: [raw_value, ...]}``。"""
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


def _get_bytes_list(fields: Dict[int, list], num: int) -> List[bytes]:
    return [v for v in fields.get(num, []) if isinstance(v, (bytes, bytearray))]


# ---------------------------------------------------------------------------
# 业务消息编码
# ---------------------------------------------------------------------------

def encode_envelope(msg_id: str, event: int, data: bytes = b"") -> bytes:
    """编码 ``WebsocketProtocolMessage``，空 body 也写 field 4。"""
    return (
        _enc_string(1, msg_id)
        + _enc_int32(3, event)
        + _enc_bytes(4, data)
        + _enc_bool(5, True)
    )


def encode_get_word_request(back: bool) -> bytes:
    """``WsWebStudyGetWordRequest`` — back=False 时为空 body。"""
    return _enc_bool(1, True) if back else b""


def encode_submit_request(
    voc_id: str, response: str, study_method: str,
    recall_duration: int, study_duration: int,
) -> bytes:
    """``WsWebStudySubmitResponseRequest``。"""
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


# ---------------------------------------------------------------------------
# 业务消息解析
# ---------------------------------------------------------------------------

def parse_web_study_word(buf: bytes) -> Dict[str, Any]:
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
    """
    ``WsWebStudyGetWordResponse``: word + interpretations + study_time + progress。

    抓包实证 field 22 是服务端权威进度（嵌套 message）：
      field 1 = finished
      field 2 = total
    """
    f = _decode_message(buf)
    word_bufs = _get_bytes_list(f, 1)
    word = parse_web_study_word(word_bufs[0]) if word_bufs else {}

    interpretations: List[str] = []
    for ib in _get_bytes_list(f, 2):
        fi = _decode_message(ib)
        text = _get_str(fi, 3)
        if text:
            interpretations.append(text)

    progress: Dict[str, int] = {}
    prog_bufs = _get_bytes_list(f, 22)
    if prog_bufs:
        pf = _decode_message(prog_bufs[0])
        progress = {
            "finished": _get_int(pf, 1),
            "total": _get_int(pf, 2),
        }

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
        "progress": progress,
    }


def parse_envelope(buf: bytes) -> Dict[str, Any]:
    f = _decode_message(buf)
    errors = []
    for err_buf in _get_bytes_list(f, 6):
        ef = _decode_message(err_buf)
        err = {}
        for fnum, vals in ef.items():
            for v in vals:
                if isinstance(v, (bytes, bytearray)):
                    err[fnum] = v.decode("utf-8", errors="replace")
                else:
                    err[fnum] = v
        errors.append(err)
    data_list = _get_bytes_list(f, 4)
    return {
        "id": _get_str(f, 1),
        "reply_id": _get_str(f, 2),
        "event": _get_int(f, 3),
        "data": data_list[0] if data_list else b"",
        "success": bool(_get_int(f, 5, 0)),
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# WebSocket 学习客户端
# ---------------------------------------------------------------------------

class MaimemoWSClient:
    """
    单连接、长复用的 WebSocket 学习客户端。

    设计要点：
      * **后台 recv loop**：所有入帧统一在 :meth:`_recv_loop` 里 demux，按
        ``reply_id`` 路由到对应 Future；业务调用方只 await 自己那个 Future。
      * **SYSTEM_PING 自动应答**：服务端定期发心跳，不应答会被踢。
      * **SYSTEM_READY 同步**：:meth:`connect` 必须等到这帧才返回。
      * **反作弊封顶**：见 :meth:`submit_response`。
    """

    def __init__(self, api_token: str):
        self.api_token = api_token.strip()
        self.ws = None
        self._ssl_ctx = ssl.create_default_context()
        # 墨墨网关证书有时与 SNI 不严格匹配，关闭校验更稳
        self._ssl_ctx.check_hostname = False
        self._ssl_ctx.verify_mode = ssl.CERT_NONE

        self._connected = False
        self._msg_seq = 0
        self._pending: Dict[str, asyncio.Future] = {}
        self._recv_task: Optional[asyncio.Task] = None
        self._ready_event: Optional[asyncio.Event] = None

        # 反作弊计时基准：上一次 GET_WORD（或 submit 自带 next）落地的时刻
        self._last_word_received_at: Optional[float] = None
        # SUBMIT_RESPONSE 内嵌的下一个单词，供调用方读取
        self.last_next_word: Optional[Dict[str, Any]] = None
        # 最近一次失败原因（成功时清空），供 UI 在下一帧持久化显示
        self.last_error: str = ""
        # 服务端权威进度 {"finished": N, "total": M}，每次取词/提交后刷新
        self.progress: Dict[str, int] = {}

    def _next_id(self) -> str:
        self._msg_seq += 1
        return f"req-{self._msg_seq}"

    @property
    def is_alive(self) -> bool:
        """连接是否还活着；recv loop 检测到 ConnectionClosed 后会变 False。"""
        return self._connected and self.ws is not None

    async def connect(self) -> bool:
        """建联并等到 SYSTEM_READY。"""
        if self._connected and self.ws is not None:
            return True
        url = f"{WS_URL}?token={self.api_token}"
        try:
            self.ws = await websockets.connect(url, ssl=self._ssl_ctx)
        except Exception as e:
            print(f"[错误] WebSocket 连接失败: {e}")
            return False
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
        await self.ws.send(encode_envelope(msg_id, event, data))

    async def _reply_ping(self, ping_id: str):
        """
        回应服务端 PING。

        抓包实证 web 端的 PONG 字节布局（46 字节）：
            0a 00   field 1 = "" (空 id)
            12 24 ...  field 2 = reply_id (UUID 36 字符)
            18 02   field 3 = SYSTEM_PING(2)
            22 00   field 4 = "" (空 bytes，**必须写**)
            28 01   field 5 = success(true)

        缺 ``field 4`` 服务端会把帧视为脏帧丢弃，几次心跳没回就被踢——
        和 INIT_STUDY 那个 ``22 00`` 是一个坑。
        """
        frame = (
            _enc_string(1, "")
            + _enc_string(2, ping_id)
            + _enc_int32(3, EV_SYSTEM_PING)
            + _enc_bytes(4, b"")
            + _enc_bool(5, True)
        )
        try:
            await self.ws.send(frame)
        except Exception:
            pass

    async def _recv_loop(self):
        try:
            while self._connected and self.ws is not None:
                try:
                    frame = await self.ws.recv()
                except websockets.exceptions.ConnectionClosed as e:
                    # 服务端长时间无业务请求会主动断（典型场景：召回屏空闲太久）
                    self._connected = False
                    reason = (e.reason or "").strip()
                    if e.code in (1000, 1001) or "idle" in reason.lower() or not reason:
                        self.last_error = "服务端关闭了连接（长时间无操作触发空闲超时）"
                    else:
                        self.last_error = f"服务端关闭了连接（code={e.code}, reason={reason!r}）"
                    break
                if not isinstance(frame, (bytes, bytearray)):
                    continue
                env = parse_envelope(bytes(frame))
                event = env["event"]
                reply_id = env["reply_id"]

                if os.environ.get("MOFISH_WS_TRACE"):
                    print(
                        f"[trace] recv len={len(frame)} event={event} "
                        f"reply_id={reply_id!r} data_len={len(env['data'])}"
                    )

                if event == EV_SYSTEM_PING:
                    await self._reply_ping(env["id"])
                    continue

                if event == EV_SYSTEM_READY:
                    if self._ready_event is not None:
                        self._ready_event.set()
                    continue

                if reply_id and reply_id in self._pending:
                    fut = self._pending.pop(reply_id)
                    if not fut.done():
                        fut.set_result(env)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[警告] recv loop 异常: {e}")
        finally:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError("连接已断开"))
            self._pending.clear()

    async def _request(
        self, event: int, data: bytes = b"", timeout: float = 10,
    ) -> Optional[Dict[str, Any]]:
        if not self._connected or self.ws is None:
            print("[错误] WebSocket 未连接")
            return None
        msg_id = self._next_id()
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
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

    # ---------- 业务接口 ----------

    async def initialize(self) -> bool:
        """``WEBSTUDY_INIT_STUDY`` —— 学习会话握手。"""
        env = await self._request(EV_WEBSTUDY_INIT_STUDY, b"", timeout=15)
        return env is not None

    async def get_word(self, back: bool = False) -> Optional[Dict[str, Any]]:
        """``WEBSTUDY_GET_WORD`` —— 拉取下一个待背单词。"""
        data = encode_get_word_request(back)
        env = await self._request(EV_WEBSTUDY_GET_WORD, data, timeout=10)
        if env is None:
            return None
        result = parse_get_word_response(env.get("data", b""))
        if result.get("id"):
            self._last_word_received_at = time.monotonic()
        if result.get("progress"):
            self.progress = result["progress"]
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
        ``WEBSTUDY_SUBMIT_RESPONSE`` —— 提交单词反馈。

        **反作弊语义**（实抓 web 端 VAGUE 请求确认）：
          * ``recall_duration`` = 第一屏（看单词、回忆）耗时
          * ``study_duration``  = 第二屏（看答案、判答）耗时
          * 两段独立计时，``recall_duration + study_duration`` 不应超过自
            ``GET_WORD`` 到 ``SUBMIT`` 的实际墙钟时间

        因此本方法 **不再各自封顶**——调用方应当用 ``time.monotonic()`` 实测
        两段时间并传入。这里只做两个兜底：

          1. 任一字段 < ``MIN_DURATION_MS`` → 上调到该值（避免 0 被服务端判非法）；
          2. ``recall + study`` 仍然超 ``elapsed - 100ms`` → 等比例缩到合法范围。

        响应里内嵌的"下一个单词"会被存到 :attr:`last_next_word`；失败原因
        会被存到 :attr:`last_error`。
        """
        MIN_DURATION_MS = 300

        recall_duration = max(recall_duration, MIN_DURATION_MS)
        study_duration = max(study_duration, MIN_DURATION_MS)

        if self._last_word_received_at is not None:
            elapsed_ms = int((time.monotonic() - self._last_word_received_at) * 1000)
            budget_ms = max(0, elapsed_ms - 100)
            total = recall_duration + study_duration
            if total > budget_ms and total > 0:
                # 等比例缩放，保住两段相对比例
                ratio = budget_ms / total
                recall_duration = max(MIN_DURATION_MS, int(recall_duration * ratio))
                study_duration = max(MIN_DURATION_MS, int(study_duration * ratio))
                # 缩完仍可能超（两个都被 MIN 顶住时），再 sleep 把预算挣回来
                if recall_duration + study_duration > budget_ms:
                    need_ms = (recall_duration + study_duration) - budget_ms
                    await asyncio.sleep(need_ms / 1000)

        data = encode_submit_request(
            voc_id=word_id,
            response=response,
            study_method=study_method,
            recall_duration=recall_duration,
            study_duration=study_duration,
        )
        env = await self._request(EV_WEBSTUDY_SUBMIT_RESPONSE, data, timeout=10)
        if env is None:
            self.last_error = "SUBMIT_RESPONSE 超时未收到响应"
            return False
        if not env.get("success", True):
            self.last_error = f"服务端拒绝 SUBMIT_RESPONSE: {env.get('errors')}"
            self.last_next_word = None
            return False
        self.last_error = ""

        # SubmitResponse 内嵌 next = WsWebStudyGetWordResponse (field 1)
        body = _decode_message(env.get("data", b""))
        next_bufs = _get_bytes_list(body, 1)
        self.last_next_word = parse_get_word_response(next_bufs[0]) if next_bufs else None
        if self.last_next_word and self.last_next_word.get("id"):
            self._last_word_received_at = time.monotonic()
        # 服务端权威进度同步刷新（VAGUE/FORGET 不进度，FAMILIAR 才会）
        if self.last_next_word and self.last_next_word.get("progress"):
            self.progress = self.last_next_word["progress"]
        return True
