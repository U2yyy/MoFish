# MoFish · 摸鱼背单词

> 一个跑在终端里、伪装成日志输出的墨墨背单词客户端

```
[14:23:17] [INFO] Loading package "ubiquitous" (v3.2.41) successfully.
[14:23:17] [DEBUG] Redis HGET dict:resilient -> hit
[14:23:17] [INFO] webpack: Built module "ephemeral.js" (47KB)
[14:23:17] [INFO] HTTP GET /api/vocab/perspicacious - 200 OK (124ms)
  ^ press any key to inspect last task ...   [b] boss  [q] save & quit
```

光标始终对着最后一行的"真单词"，看起来像在等编译；按一下键，弹出
"API Response 200 OK" 的 JSON 面板，里面才是音标和释义。按 `b` 立刻满屏
fake traceback。

---

## 目录

- [实现效果](#实现效果)
- [为什么这个项目存在](#为什么这个项目存在)
- [快速开始（小白请从这里读）](#快速开始小白请从这里读)
  - [1. 装 Python 和依赖](#1-装-python-和依赖)
  - [2. 准备 `config.json`](#2-准备-configjson)
  - [3. 获取两个 token](#3-获取两个-token)
  - [4. 跑起来](#4-跑起来)
- [按键说明](#按键说明)
- [协议 Writeup](#协议-writeup)
- [免责声明](#免责声明)

---

## 实现效果

![image](https://github.com/user-attachments/assets/aa4262c0-7ae5-432f-935f-c16e78e09c38)

![image](https://github.com/user-attachments/assets/5d288a7e-3238-4550-a936-da66504cee9c)

## 为什么这个项目存在

墨墨背单词的官方开放平台（`open.maimemo.com`）只有 **只读** REST API：
能查今日单词、能查进度，但 **不能提交学习反馈**。这意味着所有第三方
CLI 客户端最多只能"看"，不能"用"——背了等于没背，进度不会同步。

GitHub 上能搜到的墨墨第三方工具基本止步于此。

这个仓库的核心贡献是：

1. 抓包并逆向了网页端 (`tc-apis.maimemo.com`) 的私有 **二进制 Protobuf
   WebSocket 协议**——这是个没有公开 `.proto`、没有 `_pb2.py` 的内部协议。
2. 摸清了三个关键握手/反作弊点（`SYSTEM_READY` 必须先到、空 body 也要
   写 field 4、`study_duration` 不能超过墙钟耗时）。
3. 用 ~400 行 Python 无依赖地手写了 varint / length-delimited 编解码，
   实现了完整的「初始化 → 取词 → 提交」闭环。

## 仓库长什么样

```
MoFish/
├── README.md
├── LICENSE                  # MIT
├── main.py                  # CLI 入口：REST 拿进度 + WS 真背词 + 伪装 UI
├── momo_ws.py               # 核心：WebSocket 协议实现 + 反作弊封顶
├── maimemo.proto            # 逆向出来的 .proto 定义（参考用，不参与编译）
├── requirements.txt
└── config.example.json      # 配置模板
```

总共两个 Python 文件，没有任何业务复杂度。

---

## 快速开始（小白请从这里读）

> 如果你已经会用 Python，跳到 [按键说明](#按键说明) 就行。

### 1. 装 Python 和依赖

需要 Python **3.8 或更新**。检查一下：

```bash
python3 --version
```

如果命令找不到，或者版本 < 3.8：

- **macOS**：`brew install python@3.11`
- **Windows**：到 [python.org](https://www.python.org/downloads/) 下安装包，
  装的时候 **一定要勾上** "Add Python to PATH"。
- **Linux**：`sudo apt install python3 python3-pip`（Debian/Ubuntu）。

把这个仓库拉下来：

```bash
git clone https://github.com/<你的用户名>/MoFish.git
cd MoFish
```

装依赖。强烈建议用虚拟环境，免得污染全局：

```bash
python3 -m venv .venv
source .venv/bin/activate      # macOS / Linux
# Windows PowerShell:  .venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

> 如果 `pip install` 卡住，可以加国内源：
> `pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple`

### 2. 准备 `config.json`

仓库里有一份模板叫 `config.example.json`，复制一份：

```bash
cp config.example.json config.json     # macOS / Linux
# Windows: copy config.example.json config.json
```

用任意文本编辑器打开 `config.json`，长这样：

```json
{
    "ws_token": "在这里粘贴 ws_token",
    "rest_token": "在这里粘贴 rest_token"
}
```

把两个字段都填上你自己的 token（怎么拿见下一节）。

> `config.json` 已经在 `.gitignore` 里，**不会** 被 git 追踪。但如果你
> fork 了这个 repo 想自己发版，再 double check 一次：
> `git status` 应该看不见 `config.json`。

### 3. 获取两个 token

需要 **两个 token**：

| 字段 | 用途 | 从哪拿 |
|---|---|---|
| `rest_token` | 显示今日学习进度（已学 N / 总 M） | 见下方 [获取 rest_token](#获取-rest_token) |
| `ws_token` | 真正的背词通道（取词、提交反馈） | 见下方 [获取 ws_token](#获取-ws_token) |

#### 获取 `rest_token`

需要在App侧获取公开的API_KEY

![image](https://github.com/user-attachments/assets/3a72f408-5112-4d7a-8eda-06bb89eec3b8)

![image](https://github.com/user-attachments/assets/5856fa7f-b6c8-4192-a007-86b080826c4d)

![image](https://github.com/user-attachments/assets/df56f633-1cd4-4286-b635-4a56acdfae93)

#### 获取 `ws_token`

![image](https://github.com/user-attachments/assets/2d0ebcd2-fde5-4425-9697-93ce28be37ed)

> 浏览器登录网页版墨墨——https://www.maimemo.com，点击「开始学习」、打开 DevTools、在Network里面很容易能看到明文存储的token

⚠️ 两个 token 不通用：开放平台 token **不能**用于 WebSocket，网页端 token
**不能**用于 REST。务必分别获取。

### 4. 跑起来

```bash
python main.py
```

正常情况下你会看到几行假装在加载配置的日志，然后弹出今日进度：

```
==================================================
  今日进度: 12/40 词 (剩余 28 词)
==================================================

  按 [y] 开始背词  /  其他键退出
```

按 `y` 开背。

> **常见踩坑**：
> - `[错误] 配置不完整` → `config.json` 字段没填或拼错。
> - `[错误] Token 验证失败` / `[错误] WebSocket 连接失败` → `ws_token`
>   不对，或者你用的是开放平台 token（那个是给 REST 用的）。
> - `[错误] 等待 SYSTEM_READY 超时` → 网络访问 `tc-apis.maimemo.com` 出问题，
>   挂代理试试。
> - 第一次按键没反应 → 没装 `readchar`，重跑 `pip install -r requirements.txt`。

---

## 按键说明

| 键 | 含义 |
|---|---|
| `1` | 认识 |
| `2` | 模糊 |
| `3` | 忘记 |
| `b` | Boss 键：满屏 fake traceback，按任意键回来 |
| `q` | 保存当前进度并退出 |
| `Ctrl-C` | 强制退出 |

学习循环是两屏式：

1. **召回屏**：一堆假日志，光标对准的最后一行是真单词。按任意键进入下一屏。
2. **判答屏**：弹出 "API Response 200 OK" 风格的 JSON 面板，露出音标和释义。
   按 `1/2/3` 判答，自动提交云端，立刻下一个词。

---

## 协议 Writeup

如果你的目的不是用这个 CLI，而是想自己也搞一份，往下读就够了。

### 1. 信封（WebsocketProtocolMessage）

每一帧都是这玩意儿。字段编号花了一晚上对：

| field | name      | type   | 备注 |
|------:|-----------|--------|------|
| 1 | `id`       | string | 请求/帧 ID |
| 2 | `reply_id` | string | 响应帧专用：填对方那条请求的 id |
| 3 | `event`    | int32  | 见下表 |
| 4 | `data`     | bytes  | **空 body 也必须写 field 4 = `22 00`** |
| 5 | `success`  | bool   | 请求恒为 true |
| 6 | `errors`   | repeated | 错误时下发 |

`event` 枚举（截了实际用得到的）：

```
SYSTEM_READY             = 1
SYSTEM_PING              = 2
WEBSTUDY_INIT_STUDY      = 1001
WEBSTUDY_GET_WORD        = 1002
WEBSTUDY_SUBMIT_RESPONSE = 1003
```

### 2. 三个握手坑

1. **空 body 必须写 field 4**
   JS 客户端对 `INIT_STUDY` 之类的请求会显式 encode `data = b""`，对应
   wire 上是 `22 00`。Python 这边如果按"空字段省略"的常规打法会被网关
   直接吞掉，业务请求看不到任何响应，调试半天没头绪。

2. **必须等 SYSTEM_READY 才能发业务请求**
   握手成功后服务端会主动推一帧 `event=1 (SYSTEM_READY)`。在它之前发
   `INIT_STUDY` 会被回 `study_internal_error`，提示极其误导。代码里
   `MaimemoWSClient.connect()` 用 `asyncio.Event` 同步这个。

3. **SYSTEM_PING 要按格式回**
   PING 帧的 `id` 要原样塞进 `reply_id`，`event` 仍是 `2`，自己也要
   `success=true`。不应答会被踢链接。

### 3. 反作弊：两段独立计时，sum ≤ elapsed

最烦的一个点，前后改了两版才对上。`WEBSTUDY_SUBMIT_RESPONSE` 提交时如果
时间值不对，服务端会回：

```
webstudy_invalid_param  detail="study duration illegal"
```

抓 web 端一次 VAGUE 提交的原始包解出来：

```
field 4 recall_duration: 2030 ms
field 5 study_duration:  767  ms
```

**两个字段是两段独立计时，不是同义词**：

- `recall_duration` = 第一屏（看单词、回忆释义）耗时
- `study_duration`  = 第二屏（看到答案、按 1/2/3）耗时

合在一起必须 ≤ 自 `GET_WORD` 到 `SUBMIT` 的真实墙钟时。
所以客户端正确的做法是 **实测两段时间**：

```python
# main.py
recall_start = time.monotonic()
read_key()                              # 用户翻面
recall_duration_ms = int((time.monotonic() - recall_start) * 1000)

judge_start = time.monotonic()
while True:
    if read_key() in ("1", "2", "3"): break
study_duration_ms = int((time.monotonic() - judge_start) * 1000)
```

`momo_ws.py` 里还留了一层兜底：太快 (< 300ms) 顶到最小值、sum 超预算等比例
缩、缩完还超就 `asyncio.sleep` 把预算挣回来。

### 4. SUBMIT 响应自带下一个词

`WsWebStudySubmitResponseResponse.next` (field 1) 内嵌一个完整的
`WsWebStudyGetWordResponse`。意味着稳态学习循环里你 **不需要** 再单独
发一次 `GET_WORD`——直接读响应里的 next 就行。少一个 round trip。

### 5. 服务端权威进度藏在 field 22

最初以为想拿"今日已学/总数"只能去 REST 的 `get_study_progress`。后来从
SUBMIT 响应的 next-word 里解出 `GetWordResponse.field 22`，5 字节就是一个
嵌套 message：

```
field 22 (progress sub-message):
    field 1 int32 = finished
    field 2 int32 = total
```

**每次取词、每次成功提交都带**。这意味着：

- 不需要每词一次 REST 调用就能维护实时进度；
- 客户端不要自己数"学了几个"——服务端的规则是 **只有 FAMILIAR 推进 finished，
  VAGUE/FORGET 把单词压回队列但不计数**。本仓库现在的进度条完全由
  `client.progress` 驱动，UI 永远跟服务端对齐。

完整 proto 定义见 [`maimemo.proto`](./maimemo.proto)。

---

## 免责声明

- 本项目仅用于个人学习和技术研究。不保证持续可用——墨墨任何一次客户
  端升级都可能让协议失效。
- 请遵守墨墨背单词的用户协议。使用本工具产生的任何账号风险（包括但不
  限于被风控、封禁）由使用者自行承担。
- 不要用本项目刷数据、薅会员、或做任何超出"正常背单词"范畴的事。

## License

[MIT](./LICENSE)
