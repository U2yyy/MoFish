#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MoFish 主入口
==============

REST 拿进度 + WebSocket 真背词 + 伪装日志输出 + 单键交互 + Boss 键。

作者: MoFish CLI Team
Python: 3.8+
"""

import os
import sys
import json
import time
import random
import asyncio
import requests
from datetime import datetime
from typing import Optional, List, Dict, Any

from rich.console import Console
from rich.table import Table
from rich.text import Text
from rich.panel import Panel

# WebSocket 客户端 + 伪装控制台
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from momo_ws import MaimemoWSClient, StealthConsole

try:
    from readchar import readchar as _readchar
except ImportError:
    _readchar = None


# ============================================================================
# 配置
# ============================================================================

CONFIG_FILE = "config.json"
API_BASE_URL = "https://open.maimemo.com/open"


# ============================================================================
# 单键读取
# ============================================================================

def read_key() -> str:
    """单键读取，无需回车。无 readchar 则降级为 input()."""
    if _readchar is not None:
        try:
            ch = _readchar()
            if ch in ("\r", "\n"):
                return "\n"
            if ch == "\x03":  # Ctrl-C
                raise KeyboardInterrupt
            return ch
        except KeyboardInterrupt:
            raise
        except Exception:
            return input().strip()[:1] or "\n"
    return input().strip()[:1] or "\n"


# ============================================================================
# REST: 取学习进度
# ============================================================================

class MaimemoRESTClient:
    """仅用于获取今日学习进度（已完成/总数/已用时长）"""

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

    def get_study_progress(self) -> Dict[str, Any]:
        try:
            resp = self.session.post(
                f"{self.base_url}/api/v1/study/get_study_progress",
                json={}, timeout=self.timeout,
            )
            if resp.ok:
                return resp.json().get("data", {}).get("progress", {})
        except Exception as e:
            print(f"[错误] 获取学习进度失败: {e}")
        return {}

    def close(self):
        self.session.close()


# ============================================================================
# 伪装日志辅助 - 单词显示（两屏式）
# ============================================================================

# 第一屏(召回阶段)：伪装日志只露拼写
DISGUISE_RECALL = [
    '[{ts}] [INFO] Loading package "{w}" (v{ver}) successfully.',
    '[{ts}] [DEBUG] SELECT * FROM vocabulary WHERE word="{w}" LIMIT 1;',
    '[{ts}] [INFO] Module "{w}" imported successfully.',
    '[{ts}] [WARN] Cache miss for key: {w}, fetching from disk...',
    '[{ts}] [INFO] Compiling regex: ^{w}$ - 0 errors',
    '[{ts}] [DEBUG] Redis HGET dict:{w} -> hit',
    '[{ts}] [INFO] HTTP GET /api/vocab/{w} - 200 OK ({lat}ms)',
    '[{ts}] [INFO] [ThreadPool-{tid}] Processing: resolve("{w}")',
    '[{ts}] [DEBUG] pytest collected: test_{w}',
    '[{ts}] [INFO] webpack: Built module "{w}.js" ({sz}KB)',
]


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def render_recall_line(console: Console, spelling: str) -> None:
    """渲染第一屏：单词以假乱真嵌进日志，单词高亮"""
    template = random.choice(DISGUISE_RECALL)
    line = template.format(
        ts=_now(), w=spelling,
        ver=f"{random.randint(1,5)}.{random.randint(0,9)}.{random.randint(0,99)}",
        lat=random.randint(8, 240),
        tid=random.randint(1, 16),
        sz=random.randint(2, 96),
    )
    # 把 spelling 那一段高亮，其余 dim
    if spelling in line:
        before, _, after = line.partition(spelling)
        text = Text()
        text.append(before, style="cyan")
        text.append(spelling, style="bold bright_green")
        text.append(after, style="cyan")
        console.print(text)
    else:
        console.print(Text(line, style="cyan"))


def render_answer_panel(console: Console, word: Dict[str, Any], status: Optional[str] = None) -> None:
    """渲染答案屏：伪装成 API Response 的 JSON，含释义"""
    learning = {"FAMILIAR": "认识", "VAGUE": "模糊", "FORGET": "忘记"}.get(status or "", None)
    payload = {
        "status": "success",
        "code": 200,
        "data": {
            "word": word.get("spelling", ""),
            "phonetic_us": word.get("phonetic_us", ""),
            "phonetic_uk": word.get("phonetic_uk", ""),
            "interpretation": word.get("interpretation", ""),
            "difficulty": word.get("difficulty", 0),
        },
    }
    if learning is not None:
        payload["data"]["learning_status"] = learning

    json_str = json.dumps(payload, ensure_ascii=False, indent=2)
    lines = []
    for ln in json_str.split("\n"):
        if any(k in ln for k in ('"word"', '"phonetic', '"interpretation"', '"difficulty"')):
            lines.append(Text(ln, style="dim cyan"))
        elif '"learning_status"' in ln:
            color = {"认识": "bright_green", "模糊": "yellow", "忘记": "bright_red"}.get(learning or "", "white")
            lines.append(Text(ln, style=color))
        else:
            lines.append(Text(ln, style="white"))

    panel = Panel(
        Text("\n").join(lines),
        title=f"[API Response 200 OK]",
        border_style="dim",
        padding=(1, 2),
    )
    console.print(panel)


# ============================================================================
# Boss 键: 满屏假 traceback
# ============================================================================

FAKE_BOSS_OUTPUTS = [
    """Traceback (most recent call last):
  File "/srv/app/manage.py", line 22, in <module>
    execute_from_command_line(sys.argv)
  File "/usr/local/lib/python3.10/site-packages/django/core/management/__init__.py", line 446, in execute_from_command_line
    utility.execute()
  File "/usr/local/lib/python3.10/site-packages/django/core/management/__init__.py", line 440, in execute
    self.fetch_command(subcommand).run_from_argv(self.argv)
  File "/usr/local/lib/python3.10/site-packages/django/db/backends/base/base.py", line 215, in connect
    self.connection = self.get_new_connection(conn_params)
django.db.utils.OperationalError: server closed the connection unexpectedly""",
    """error: failed to push some refs to 'git@github.com:corp/internal-platform.git'
hint: Updates were rejected because the remote contains work that you do
hint: not have locally. This is usually caused by another repository pushing
hint: to the same ref. You may want to first integrate the remote changes
hint: (e.g., 'git pull ...') before pushing again.""",
    """[ERROR] Build failed in 47.2s
> Task :app:compileKotlin FAILED
e: /project/src/main/kotlin/com/corp/Service.kt: (134, 21): Unresolved reference: cache
e: /project/src/main/kotlin/com/corp/Service.kt: (188, 9): Type mismatch: inferred type is Unit but Job was expected
FAILURE: Build failed with an exception.""",
]


def trigger_boss(console: Console) -> None:
    console.clear()
    for _ in range(28):
        lv = random.choice(["DEBUG", "INFO", "WARN", "ERROR"])
        mod = random.choice(["main.py", "build.py", "compiler.py", "settings.py", "utils.py", "router.py"])
        act = random.choice([
            "Loading configuration...", "Resolving dependencies...",
            "Validating input...", "Allocating buffer...", "Hot reload triggered",
            "Cache warmup done", "Migrating schema...", "Compiling sources...",
        ])
        line = f"[{_now()}] [{lv}] {mod}: {act}"
        if lv == "ERROR":
            console.print(Text(line, style="bright_red"))
        elif lv == "WARN":
            console.print(Text(line, style="yellow"))
        else:
            console.print(Text(line, style="dim"))
    console.print()
    console.print(Text(random.choice(FAKE_BOSS_OUTPUTS), style="bright_red"))
    console.print()
    console.print(Text("press any key to retry...", style="dim italic"))


# ============================================================================
# 配置加载
# ============================================================================

def load_config() -> Dict[str, str]:
    if not os.path.exists(CONFIG_FILE):
        return {}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# ============================================================================
# 学习循环
# ============================================================================

async def learning_loop(client: MaimemoWSClient, console: Console,
                        finished: int, total: int) -> Dict[str, Any]:
    """
    两屏式背词循环。
      Phase 1 (recall): 伪装日志显示拼写, 等用户按任意键(b=boss, q=退出)显示释义
      Phase 2 (judge): 显示释义, 等 1/2/3 判答, 提交云端
    """
    stats = {"known": 0, "fuzzy": 0, "forgotten": 0}
    learned: List[Dict] = []

    if not await client.connect():
        console.print(Text("[错误] WebSocket 连接失败", style="bold red"))
        return {"stats": stats, "learned": learned}

    try:
        if not await client.initialize():
            console.print(Text("[错误] 学习会话初始化失败", style="bold red"))
            return {"stats": stats, "learned": learned}

        word = await client.get_word()
        idx = 0

        while word and word.get("id"):
            idx += 1
            current_global = finished + idx

            # ---------- 渲染进度 + 第一屏 ----------
            console.clear()
            filled = int(20 * current_global / total) if total > 0 else 0
            bar = "=" * filled + "-" * (20 - filled)
            console.print(Text(f"[{bar}] {current_global}/{total}", style="cyan"))
            console.print()

            # 上方铺几行假日志，真单词永远是最后一行——用户视线落到提示之上即可
            noise_pool = [
                "config", "router", "cache", "logger", "session", "metrics",
                "worker", "scheduler", "kafka-consumer", "pool", "redis",
                "auth-middleware", "rate-limiter", "tracer", "queue-worker",
            ]
            for _ in range(random.randint(2, 4)):
                render_recall_line(console, random.choice(noise_pool))
            render_recall_line(console, word["spelling"])  # 真单词 = 最后一行

            console.print()
            console.print(Text(
                "  ^ press any key to inspect last task ...   [b] boss  [q] save & quit",
                style="dim italic",
            ))

            # ---------- 第一屏交互：等用户回忆 ----------
            recall_key = read_key()
            if recall_key.lower() == "q":
                break
            if recall_key.lower() == "b":
                trigger_boss(console)
                read_key()
                # boss 之后回到当前单词，重画
                continue

            # ---------- 第二屏：显示释义 + 等判答 ----------
            console.print()
            render_answer_panel(console, word)
            console.print()
            console.print(Text(
                "  [1] 认识   [2] 模糊   [3] 忘记   [b] boss   [q] save & quit",
                style="dim",
            ))

            while True:
                k = read_key()
                kl = k.lower()
                if kl == "q":
                    return {"stats": stats, "learned": learned}
                if kl == "b":
                    trigger_boss(console)
                    read_key()
                    # boss 之后重画当前答案屏
                    console.clear()
                    console.print(Text(f"[{bar}] {current_global}/{total}", style="cyan"))
                    console.print()
                    render_answer_panel(console, word)
                    console.print()
                    console.print(Text("  [1] 认识   [2] 模糊   [3] 忘记   [b] boss   [q] save & quit", style="dim"))
                    continue
                if kl in ("1", "2", "3"):
                    status = {"1": "FAMILIAR", "2": "VAGUE", "3": "FORGET"}[kl]
                    break

            # ---------- 提交反馈 ----------
            # duration 由 client 内部按真实经过时间封顶，这里给较大的目标值即可
            ok = await client.submit_response(
                word_id=word["id"],
                response=status,
                recall_duration=random.randint(800, 4000),
                study_duration=random.randint(2000, 9000),
                study_method="STUDY_EN_CN",
            )

            if ok:
                learned.append({"spelling": word["spelling"], "id": word["id"], "status": status})
                {"FAMILIAR": "known", "VAGUE": "fuzzy", "FORGET": "forgotten"}[status]
                if status == "FAMILIAR":
                    stats["known"] += 1
                elif status == "VAGUE":
                    stats["fuzzy"] += 1
                else:
                    stats["forgotten"] += 1
                # 提交成功 → 下一个单词直接来自响应
                word = client.last_next_word
            else:
                # 提交失败：再 get 一次
                word = await client.get_word()

        else:
            console.print(Text("\n[提示] 学习完成，没有更多单词", style="bright_green"))
    finally:
        await client.close()

    return {"stats": stats, "learned": learned}


# ============================================================================
# main
# ============================================================================

def main():
    console = Console()
    console.clear()

    config = load_config()
    ws_token = config.get("ws_token", "")
    rest_token = config.get("rest_token", "")

    if not ws_token or not rest_token:
        console.print(Text("[错误] 配置不完整，请检查 config.json", style="bold red"))
        console.print(Text("需要 ws_token 和 rest_token", style="yellow"))
        sys.exit(1)

    # 启动假日志
    console.print(Text(f"[{_now()}] [INFO] MoFish initializing...", style="cyan"))
    time.sleep(0.2)
    console.print(Text(f"[{_now()}] [DEBUG] Loading configuration...", style="dim"))
    time.sleep(0.15)
    console.print(Text(f"[{_now()}] [INFO] Fetching today's study task...", style="cyan"))

    # 拉进度
    rest = MaimemoRESTClient(rest_token)
    try:
        progress = rest.get_study_progress()
    finally:
        rest.close()

    finished = progress.get("finished", 0)
    total = progress.get("total", 0)
    study_minutes = (progress.get("study_time", 0) or 0) // 60000

    console.print(Text(f"[{_now()}] [INFO] Progress: {finished}/{total}, study time: {study_minutes}min", style="cyan"))

    if total > 0 and finished >= total:
        console.print()
        console.print(Text("=" * 50, style="dim"))
        console.print(Text("  今日任务已全部完成", style="bright_green"))
        console.print(Text(f"  已学习: {finished} 词  |  学习时长: {study_minutes} 分钟", style="dim"))
        console.print(Text("=" * 50, style="dim"))
        return

    remaining = total - finished
    console.print()
    console.print(Text("=" * 50, style="dim"))
    console.print(Text(f"  今日进度: {finished}/{total} 词 (剩余 {remaining} 词)", style="bold cyan"))
    console.print(Text("=" * 50, style="dim"))
    console.print()
    console.print(Text("  按 [y] 开始背词  /  其他键退出", style="dim"))

    ch = read_key().lower()
    if ch != "y":
        console.print(Text("已取消", style="dim"))
        return

    console.clear()
    console.print(Text(f"[{_now()}] [INFO] Starting WebSocket learning session...", style="cyan"))
    time.sleep(0.4)

    client = MaimemoWSClient(ws_token)
    result = asyncio.run(learning_loop(client, console, finished, total))
    stats = result["stats"]
    learned = result["learned"]

    # 学习摘要
    console.clear()
    console.print()
    table = Table(title="学习摘要", show_header=True, header_style="bold cyan")
    table.add_column("状态", style="white")
    table.add_column("数量", justify="right", style="cyan")
    table.add_column("占比", justify="right", style="dim")
    n = len(learned) or 1
    table.add_row("认识", str(stats["known"]), f"{stats['known']*100//n}%")
    table.add_row("模糊", str(stats["fuzzy"]), f"{stats['fuzzy']*100//n}%")
    table.add_row("忘记", str(stats["forgotten"]), f"{stats['forgotten']*100//n}%")
    table.add_row("总计", str(len(learned)), "100%")
    console.print(table)
    console.print()
    console.print(Text("进度已同步到云端", style="dim italic"))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
