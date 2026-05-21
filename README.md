# MoFish
🦑 MoFish - 墨墨背单词隐蔽式终端客户端

## 功能特性

- **CLI 模式** (`momo_cli.py`): 基于 REST API，获取每日复习任务
- **WebSocket 模式** (`momo_ws.py`): 基于 WebSocket 协议，实时学习反馈
- **伪装日志**: 单词以假乱真地伪装成开发环境日志
- **Boss 键**: 一键切换到假错误画面，快速隐藏学习状态

## 快速开始

### 安装依赖

```bash
pip install requests rich readchar websockets
```

### 配置 Token

```bash
# 首次运行会自动提示输入 Token
python3 momo_cli.py   # REST API 模式
python3 momo_ws.py    # WebSocket 模式
```

### 使用说明

| 按键 | 功能 |
|------|------|
| `1` | 认识 |
| `2` | 模糊 |
| `3` | 忘记 |
| `q` | 保存退出 |
| `b` | Boss 键（紧急清屏）|

## 项目结构

```
MoFish/
├── momo_cli.py      # REST API 客户端
├── momo_ws.py       # WebSocket 客户端
├── config.json      # Token 配置（不提交）
├── document.yaml     # 墨墨 API 文档
├── maimemo.proto    # WebSocket Protobuf 定义
└── SPEC.md          # 项目规格文档
```

## 注意事项

- `config.json` 包含敏感信息，已加入 `.gitignore`
- 需要分别获取 REST API Token 和 WebSocket Token
- WebSocket Token 需要在打开墨墨 App 后在网页版获取
