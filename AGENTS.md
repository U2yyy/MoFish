# AGENTS.md

Guidance for Codex / Codex / 任何 coding agent 接手这个仓库时的"前置阅读"。
读完这一份就基本能避开历史上踩过的坑。

---

## 项目一句话

MoFish = 跑在终端里、伪装成日志输出的墨墨背单词 CLI。
**核心价值不是这个 CLI 本身**，而是逆向出来的私有 WebSocket 二进制 Protobuf
协议——所以协议层（`momo_ws.py`）改动要格外小心，每动一次都要对照协议 writeup
和已有抓包记录确认。

## 文件地图

```
main.py              — CLI 入口 + 学习循环 + 伪装 UI + 统计 + 设置页
momo_ws.py           — WebSocket 客户端：手写 Protobuf 编解码、反作弊封顶、心跳应答
maimemo.proto        — 逆向出来的 .proto（仅参考，不参与编译）
config.json          — 用户私有，**不入 Git**
config.example.json  — 模板，入 Git
data/                — 学习记录 JSONL，**不入 Git**
AGENTS.md            — 本文件，**不入 Git**
README.md            — 面向 GitHub 访客；有完整的协议 writeup
```

## 跑起来

```bash
pip install -r requirements.txt
cp config.example.json config.json   # 填两个 token
python main.py                       # 背词
python main.py --stats               # 看统计（不连网）
python main.py --stats --days 7      # 最近 7 天
MOFISH_WS_TRACE=1 python main.py     # 调协议时打印每一帧
```

Python 3.8+。无构建步骤。

---

## 协议核心知识（**所有动 momo_ws.py 前必读**）

### 信封 WebsocketProtocolMessage

```
field 1: id        (string)   请求 ID（响应帧可空）
field 2: reply_id  (string)   回应的目标请求 ID（请求帧为空）
field 3: event     (int32)    见 EVENT_NAMES
field 4: data      (bytes)    业务消息体（** 见下方坑 1 **）
field 5: success   (bool)     请求恒为 true
field 6: errors    (repeated) 失败时下发
```

### Event 枚举

```
SYSTEM_READY             = 1
SYSTEM_PING              = 2
WEBSTUDY_INIT_STUDY      = 1001
WEBSTUDY_GET_WORD        = 1002
WEBSTUDY_SUBMIT_RESPONSE = 1003
WEBSTUDY_ADD_WORDS       = 1011  ← 从已绑定词本舀 N 个词到今日队列（见坑 6）
```

### 业务字段 voc_id 是字符串

`SubmitResponseRequest.voc_id` 是 24-hex 字符（如 `"57067be2a172044907c63018"`），
**不是 UUID**。直接透传 `WebStudyWord.id` 即可，不要做任何解析。

### StudyResponse 枚举

```
FAMILIAR             = 1
VAGUE                = 2
FORGET               = 3
WELL_FAMILIAR        = 4
CANCEL_WELL_FAMILIAR = 5
```

### StudyMethod 枚举

```
STUDY_EN_CN = 0  ← 现在用这个（看英文回中文）
STUDY_CN_EN = 1
```

⚠️ `STUDY_EN_CN = 0` 是 proto3 默认值。我们的 `encode_submit_request` 走的是
"`if sm_n: out += ...`"——所以 `study_method` field 实际上 **不会被发送**。
这跟 web 端抓包一致（web 也不发 field 3），是正确行为。**不要"修复"成总是发送。**

---

## 历史上踩过的坑（按时间倒序，越靠前越新）

### 坑 1：所有空 body 都必须显式写 field 4 = `22 00`

**踩过的位置**：
1. `INIT_STUDY` / `GET_WORD` 等业务请求的空 body → 写在 `encode_envelope`
2. `_reply_ping` 回应 SYSTEM_PING → 之前漏写，导致服务端判脏帧、几次心跳没回就踢

**症状**：业务请求石沉大海无响应；或闲置一段时间 ws 自动断开（且看不到任何报错）。

**根因**：proto3 默认行为是"空字段省略不编码"，但墨墨的网关用的是非标准
parser，缺 field 4 就会把帧扔了。所有 outbound 帧都必须显式 `_enc_bytes(4, b"")`。

**已实证字节布局（来自 web 抓包）**：
```
PING (server → us):  0a 24 <uuid> 12 00 18 02 22 00 28 01
PONG (us → server):  0a 00 12 24 <uuid> 18 02 22 00 28 01
                                              ^^^^^ ← 必须有
```

### 坑 2：反作弊是 "sum ≤ elapsed"，不是各字段独立 cap

**症状**：标 FAMILIAR 有时能成功，标 VAGUE/FORGET 频繁被 `webstudy_invalid_param`
拒，错误 detail = `study duration illegal`。

**根因**：`recall_duration` 和 `study_duration` 是 **两段独立计时**（看单词 / 看答案），
不是一个值的两种名字。服务端校验的是它们的 **和** ≤ 从 GET_WORD 到 SUBMIT 的墙钟时间。
我以前两个字段各自 cap 到 `elapsed - 100`，sum 直接超 elapsed → 拒。

**正确做法**：在 `learning_loop` 里用 `time.monotonic()` **实测两段时间**，
传给 `submit_response`。`MaimemoWSClient` 只做兜底：
- 任一字段 < 300ms 顶到 300ms
- sum 仍超预算 → 等比例缩放 + 必要时 `asyncio.sleep` 补够

### 坑 3：必须等 SYSTEM_READY 才能发业务请求

握手成功后服务端会主动推一帧 `event=1 (SYSTEM_READY)`。在这之前发 `INIT_STUDY`
会被回 `study_internal_error`（这个错误名极其误导）。代码里 `connect()` 用
`asyncio.Event` 同步这个。

### 坑 4：服务端权威进度在 GetWordResponse.field 22

`field 22 = { field 1: finished, field 2: total }` 嵌套 message。
**不要本地数 `len(learned)`**——服务端规则是 **VAGUE/FORGET 不推进 finished**，
只有 FAMILIAR 才推。本仓库的进度条完全由 `client.progress` 驱动。

`total` 会随用户在 App / 小程序里临时添加新词而动态变化。比如学完今日
原计划 252 词后再加 248 词，REST `get_study_progress` 与 WS `field 22`
都会同步更新到 `{finished: 252, total: 500}`，两边数值始终一致。

### 坑 5：SUBMIT 响应自带下一个词

`WsWebStudySubmitResponseResponse.next` (field 1) 内嵌完整的 `GetWordResponse`。
**稳态学习循环里不要再单独发 GET_WORD**，直接读 `client.last_next_word` 即可，
省一次 round trip。

### 坑 6：Event 1011 = 从已绑定词本舀 N 个词到今日队列（**语义已锁定**）

**前提**：用户账号层面已经绑定了词本（自建 notepad 或官方词书）。今日要学
的词的"水池"就是这些词本里还没学完的词。

```
SEND: { field 4: { field 2: { field 1: <count>, field 2: <mode> } } }
RECV: { field 4: { field 1: <count> } }
```

- `count`：希望从词池再舀多少个进入今日队列
- `mode`：候选语义为加词来源/模式（手动加词 / 复习追加 等），实证样本 = 2，**不知道其他取值的语义，先无脑写死 2**
- **body 里没有 voc_id 列表是设计如此**，不是漏读——具体加哪些词由服务端
  按词本算法挑

实证（2026-05-22）：网页端加了 248 个新词后捕获 `{field 1: 248, field 2: 2}`，
同一时刻 REST `get_today_items` 中 `is_new=True` 的条目数 = 248，严格相等。

⚠️ **不要用 REST `/api/v1/study/add_words`** —— 那条路要求客户端自己提供
voc_id 数组，等于让用户挑词。1011 这条路只问"加几个"，由服务端按词本顺序
喂，更贴合摸鱼背词的产品定位。

实现见 `momo_ws.py:add_words(count, mode=2)`，CLI 入口 `python main.py --add N`。

### 坑 7：GetWordResponse.field 23 = study_time_ms

抓包实证 `field 23` 与 REST `get_study_progress.study_time` 数值完全相等
（毫秒级的今日累计学习时长）。意味着 WS 单通道就能拿到学习时长，不一定要走 REST。
当前 `parse_get_word_response` 已抽出为 `study_time_ms`，但 main.py 还是用 REST，
后续可考虑切到纯 WS。

---

## 学习循环关键时序（main.py `learning_loop`）

```
get_word()                                  ← 取词
  └─ render recall screen
recall_start = monotonic()
read_key()                                  ← 用户按任意键揭开答案（[b]=boss [q]=退出）
recall_duration_ms = elapsed
  └─ render answer panel
judge_start = monotonic()
read_key() until in ("1","2","3")           ← 用户判答
study_duration_ms = elapsed
submit_response(word_id, status, recall_ms, study_ms)
  ├─ if ok: word = client.last_next_word    ← 用响应自带的 next
  └─ if !ok and !client.is_alive: 友好退出  ← 断线提示
```

## 配置项

`config.json` 字段：

| 字段 | 类型 | 用途 |
|---|---|---|
| `ws_token` | string | WebSocket 鉴权（浏览器抓包获取） |
| `rest_token` | string | REST 鉴权（open.maimemo.com 开放平台申请） |
| `data_dir` | string | JSONL 落盘目录，默认 `"data"` |
| `record_enabled` | bool | 是否启用 JSONL 写入，默认 false |
| `hide_judge_hint` | bool | 判答屏是否隐藏底部 hint，默认 false |

两个 bool 开关可以在启动菜单按 `s` 进入交互式设置页切换。

## 数据落盘 schema（`data/sessions.jsonl` 每行一条）

```json
{
  "ts": "2026-05-22T10:23:45+08:00",
  "spelling": "ubiquitous",
  "voc_id": "57067be2a172044907c63018",
  "response": "FAMILIAR",
  "recall_ms": 2030,
  "study_ms": 767,
  "interpretation": "adj. 无处不在的；普遍存在的",
  "phonetic_us": "[juːˈbɪkwɪtəs]",
  "phonetic_uk": "",
  "difficulty": 3,
  "progress_after": {"finished": 13, "total": 252}
}
```

只在 `record_enabled=true` 且 submit 成功时追加。失败 / boss 跳过 / q 退出都不写。

---

## 不要做的事

1. **不要把"空字段省略"的 proto3 标准行为引入到 outbound 帧编码**——服务端不认账。
2. **不要在 `momo_ws.py:submit_response` 里加各字段独立的 duration cap**——会破坏
   反作弊正确语义，重新引入坑 2。
3. **不要本地数"已学几个词"**——服务端权威，见坑 4。
4. **不要在 SUBMIT 成功后再发 GET_WORD**——见坑 5。
5. **不要引入新第三方依赖**。仅 `requests` / `websockets` / `rich` / `readchar`。
6. **不要主动给客户端加心跳**（曾经考虑过、又撤了）。抓包确认 web 端是
   **被动应答模式**，服务端发 PING、客户端回 PONG。我们只要 PONG 字节正确就行。
7. **不要碰伪装日志 / boss 键 / 两屏式 UI** 的视觉细节，那是产品定位。
8. **不要触发 git 操作**（commit / push / amend）除非用户明确要求。

## 调试技巧

- `MOFISH_WS_TRACE=1` 打印每一帧的 event / reply_id / data_len，最快定位协议层问题。
- 拿到可疑 base64 帧 → 一行 Python 验证：
  ```python
  import base64; import momo_ws as m
  env = m.parse_envelope(base64.b64decode("..."))
  print(env)
  ```
- 业务 body 想看清楚结构 → `m._decode_message(env['data'])` 拿到 `{field_num: [values]}`。

## 提交 / Code review 习惯

- 协议层改动 **必须** 在 commit message 里写明：
  - 改了哪个字段编号 / wire type / 默认值处理
  - 有没有抓包对照、对照的是哪段
- UI / 统计 / 配置改动相对自由，但不要顺手"重构"协议层。
- 不要替用户写 commit / push，除非 explicitly 被要求。
