#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MoFish 主入口
==============

REST 拿进度 + WebSocket 真背词 + 伪装日志输出 + 单键交互 + Boss 键。

作者: U2y
Python: 3.8+
"""

import os
import sys
import json
import time
import random
import asyncio
import argparse
import requests
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

from rich.console import Console
from rich.table import Table
from rich.text import Text
from rich.panel import Panel

# WebSocket 客户端 + 伪装控制台
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from momo_ws import MaimemoWSClient

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
# JSONL Session Record
# ============================================================================

def append_session_record(record: dict, data_dir: str) -> None:
    """Append a session record to sessions.jsonl. Silently ignores write failures."""
    try:
        os.makedirs(data_dir, exist_ok=True)
        path = os.path.join(data_dir, "sessions.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


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

def load_config() -> Dict[str, Any]:
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
                        finished: int, total: int, data_dir: str,
                        record_enabled: bool = False,
                        hide_judge_hint: bool = False) -> Dict[str, Any]:
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

        while word and word.get("id"):
            # 服务端权威进度（WS 自带，VAGUE/FORGET 不会推进 finished）
            srv_finished = client.progress.get("finished", finished)
            srv_total = client.progress.get("total", total) or 1
            # 当前正在背的这一个：finished 还没 +1，所以显示 +1 让进度条不挂在上一个数
            current_global = srv_finished + 1

            # ---------- 渲染进度 + 第一屏 ----------
            console.clear()
            filled = int(20 * current_global / srv_total) if srv_total > 0 else 0
            bar = "=" * filled + "-" * (20 - filled)
            console.print(Text(f"[{bar}] {current_global}/{srv_total}", style="cyan"))
            console.print()

            # 上一轮 submit 的错误持久化提示一次，避免被 clear 吞掉
            if client.last_error:
                console.print(Text(f"  [!] 上一次提交失败: {client.last_error}", style="bold yellow"))
                console.print()
                client.last_error = ""

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
            # 实测 recall_duration（看单词到揭开答案这段）
            recall_start = time.monotonic()
            recall_key = read_key()
            if recall_key.lower() == "q":
                break
            if recall_key.lower() == "b":
                trigger_boss(console)
                read_key()
                # boss 之后回到当前单词，重画
                continue
            recall_duration_ms = int((time.monotonic() - recall_start) * 1000)

            # ---------- 第二屏：显示释义 + 等判答 ----------
            console.print()
            render_answer_panel(console, word)
            console.print()
            if not hide_judge_hint:
                console.print(Text(
                    "  [1] 认识   [2] 模糊   [3] 忘记   [b] boss   [q] save & quit",
                    style="dim",
                ))

            # 实测 study_duration（看答案到按 1/2/3 这段）
            judge_start = time.monotonic()
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
                    console.print(Text(f"[{bar}] {current_global}/{srv_total}", style="cyan"))
                    console.print()
                    render_answer_panel(console, word)
                    console.print()
                    if not hide_judge_hint:
                        console.print(Text("  [1] 认识   [2] 模糊   [3] 忘记   [b] boss   [q] save & quit", style="dim"))
                    judge_start = time.monotonic()  # 重计时
                    continue
                if kl in ("1", "2", "3"):
                    status = {"1": "FAMILIAR", "2": "VAGUE", "3": "FORGET"}[kl]
                    break
            study_duration_ms = int((time.monotonic() - judge_start) * 1000)

            # ---------- 提交反馈 ----------
            # duration 是 web 端语义下的两段实测值；client 只做兜底
            ok = await client.submit_response(
                word_id=word["id"],
                response=status,
                recall_duration=recall_duration_ms,
                study_duration=study_duration_ms,
                study_method="STUDY_EN_CN",
            )

            if ok:
                learned.append({"spelling": word["spelling"], "id": word["id"], "status": status})
                if record_enabled:
                    append_session_record({
                        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
                        "spelling": word["spelling"],
                        "voc_id": word.get("voc_id") or "",
                        "response": status,
                        "recall_ms": recall_duration_ms,
                        "study_ms": study_duration_ms,
                        "interpretation": word.get("interpretation") or "",
                        "phonetic_us": word.get("phonetic_us") or "",
                        "phonetic_uk": word.get("phonetic_uk") or "",
                        "difficulty": word.get("difficulty") or 0,
                        "progress_after": dict(client.progress),
                    }, data_dir)
                if status == "FAMILIAR":
                    stats["known"] += 1
                elif status == "VAGUE":
                    stats["fuzzy"] += 1
                else:
                    stats["forgotten"] += 1
                # 提交成功 → 下一个单词直接来自响应
                word = client.last_next_word
            else:
                # 服务端断了就别再尝试，友好提示用户
                if not client.is_alive:
                    console.print()
                    console.print(Text(
                        f"\n[提示] {client.last_error or '连接已断开'}",
                        style="bold yellow",
                    ))
                    console.print(Text("已保存当前学习记录，下次重新运行即可继续。", style="dim"))
                    time.sleep(1.5)
                    break
                # 提交失败但连接还在：再 get 一次
                word = await client.get_word()

        else:
            # while 自然结束（word 变 None）：分清是真的学完还是断线
            if not client.is_alive:
                console.print()
                console.print(Text(
                    f"\n[提示] {client.last_error or '连接已断开'}",
                    style="bold yellow",
                ))
                console.print(Text("已保存当前学习记录，下次重新运行即可继续。", style="dim"))
                time.sleep(1.5)
            else:
                console.print(Text("\n[提示] 学习完成，没有更多单词", style="bright_green"))
    finally:
        await client.close()

    return {"stats": stats, "learned": learned}


# ============================================================================
# main
# ============================================================================

def main():
    parser = argparse.ArgumentParser(prog="MoFish", add_help=False)
    parser.add_argument("--stats", action="store_true")
    parser.add_argument("--days", type=int, default=None)
    args, _ = parser.parse_known_args()

    config = load_config()
    data_dir = config.get("data_dir", "data")

    if args.stats:
        print_stats(data_dir, args.days)
        return

    console = Console()
    console.clear()

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

    while True:
        console.print(Text("  按 [y] 开始背词  /  [s] 设置  /  其他键退出", style="dim"))
        ch = read_key().lower()
        if ch == "s":
            run_settings(config, CONFIG_FILE, console)
            continue
        elif ch == "y":
            break
        else:
            console.print(Text("已取消", style="dim"))
            return

    console.clear()
    console.print(Text(f"[{_now()}] [INFO] Starting WebSocket learning session...", style="cyan"))
    time.sleep(0.4)

    client = MaimemoWSClient(ws_token)
    result = asyncio.run(learning_loop(client, console, finished, total, data_dir,
                                      record_enabled=config.get("record_enabled", False),
                                      hide_judge_hint=config.get("hide_judge_hint", False)))
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


# ============================================================================
# Stats CLI
# ============================================================================

def parse_iso_ts(ts_str: str):
    """Parse ISO timestamp, return None on failure."""
    try:
        # Handle formats like "2026-05-22T10:23:45+08:00" or "2026-05-22T10:23:45"
        ts_str = ts_str.strip()
        if "+" in ts_str or ts_str.endswith("Z"):
            dt = datetime.fromisoformat(ts_str)
        else:
            dt = datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc)
        return dt.astimezone()
    except Exception:
        return None


def load_session_records(data_dir: str):
    """Load all valid JSONL records, skip malformed lines."""
    path = os.path.join(data_dir, "sessions.jsonl")
    if not os.path.exists(path):
        return []
    records = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        pass
    return records


def filter_by_days(records, days: Optional[int]):
    """Filter records by days (from today). None means all records."""
    if days is None:
        return records
    now = datetime.now().astimezone()
    cutoff = now - timedelta(days=days)
    filtered = []
    unknown_bucket = []
    for r in records:
        dt = parse_iso_ts(r.get("ts", ""))
        if dt is None:
            unknown_bucket.append(r)
        elif dt >= cutoff:
            filtered.append(r)
    # Put unknown bucket at the end, included in full count
    return filtered, unknown_bucket


def print_stats(data_dir: str, days: Optional[int]):
    console = Console()

    all_records = load_session_records(data_dir)
    if not all_records:
        console.print("[dim]还没有学习记录。先跑 'python main.py' 背几个词。[/dim]")
        return

    if days is not None:
        filtered, _unknown = filter_by_days(all_records, days)
        records = filtered
    else:
        records = all_records

    # Count totals (use all records for "total N" display, filtered for stats)
    total_count = len(all_records)

    n = len(records) or 1

    # --- Block 1: 总览 ---
    familiar = sum(1 for r in records if r.get("response") == "FAMILIAR")
    vague = sum(1 for r in records if r.get("response") == "VAGUE")
    forget = sum(1 for r in records if r.get("response") == "FORGET")

    # Unique words
    seen = {}
    for r in records:
        sp = r.get("spelling", "")
        if sp not in seen:
            seen[sp] = []
        seen[sp].append(r)

    unique_words = len(seen)

    avg_recall = 0.0
    avg_study = 0.0
    if records:
        avg_recall = sum(r.get("recall_ms", 0) for r in records) / n / 1000
        avg_study = sum(r.get("study_ms", 0) for r in records) / n / 1000

    period_label = f"最近 {days} 天" if days is not None else "累计"
    console.print()
    console.print(Text(f"学习总览（{period_label} / 共 {total_count} 条记录）", style="bold cyan"))
    console.print(Text("─" * 36, style="dim"))
    console.print(f"总提交次数：     {len(records)}")
    console.print(f"不重复单词数：   {unique_words}")
    console.print(f"认识 (FAMILIAR): {familiar} ({familiar*100//n}%)")
    console.print(f"模糊 (VAGUE):    {vague} ({vague*100//n}%)")
    console.print(f"忘记 (FORGET):   {forget} ({forget*100//n}%)")
    console.print(f"平均回忆耗时：   {avg_recall:.1f} 秒")
    console.print(f"平均判断耗时：   {avg_study:.1f} 秒")
    console.print()

    # --- Block 2: 顽固词 Top 10 ---
    stubborn = []
    for sp, recs in seen.items():
        cnt = len(recs)
        bad = sum(1 for r in recs if r.get("response") in ("VAGUE", "FORGET"))
        if bad >= 1 and cnt >= 2:
            last_rec = recs[-1]
            stubborn.append({
                "spelling": sp,
                "count": cnt,
                "bad": bad,
                "rate": bad / cnt,
                "interpretation": last_rec.get("interpretation", ""),
            })
    stubborn.sort(key=lambda x: (-x["rate"], -x["count"]))
    stubborn = stubborn[:10]

    if stubborn:
        console.print(Text("顽固词 Top 10（被标记 VAGUE/FORGET ≥ 2 次）", style="bold cyan"))
        t = Table(show_header=True, header_style="bold cyan")
        t.add_column("单词", style="white")
        t.add_column("出现次数", justify="right", style="cyan")
        t.add_column("顽固率", justify="right", style="yellow")
        t.add_column("最近释义", style="dim")
        for item in stubborn:
            rate_str = f"{item['bad']}/{item['count']} = {int(item['rate']*100)}%"
            t.add_row(
                item["spelling"],
                str(item["count"]),
                rate_str,
                item["interpretation"][:30],
            )
        console.print(t)
        console.print()

    # --- Block 3: 秒杀词 Top 10 ---
    speedsters = []
    for sp, recs in seen.items():
        if all(r.get("response") == "FAMILIAR" for r in recs):
            avg_ms = sum(r.get("recall_ms", 0) for r in recs) / len(recs)
            if avg_ms < 1500:
                last_rec = recs[-1]
                speedsters.append({
                    "spelling": sp,
                    "avg_ms": avg_ms,
                    "count": len(recs),
                    "interpretation": last_rec.get("interpretation", ""),
                })
    speedsters.sort(key=lambda x: x["avg_ms"])
    speedsters = speedsters[:10]

    if speedsters:
        console.print(Text("秒杀词 Top 10（全FAMILIAR 且平均 recall < 1.5s）", style="bold cyan"))
        t2 = Table(show_header=True, header_style="bold cyan")
        t2.add_column("单词", style="white")
        t2.add_column("平均回忆耗时(ms)", justify="right", style="cyan")
        t2.add_column("出现次数", justify="right", style="dim")
        t2.add_column("最近释义", style="dim")
        for item in speedsters:
            t2.add_row(
                item["spelling"],
                str(int(item["avg_ms"])),
                str(item["count"]),
                item["interpretation"][:30],
            )
        console.print(t2)
        console.print()

    # --- Block 4: 每日学习曲线 (last 14 days) ---
    now = datetime.now().astimezone()
    date_counts: Dict[str, int] = {}
    for i in range(14):
        dt = now - timedelta(days=i)
        date_counts[dt.strftime("%m-%d")] = 0

    for r in records:
        dt = parse_iso_ts(r.get("ts", ""))
        if dt:
            key = dt.strftime("%m-%d")
            if key in date_counts:
                date_counts[key] += 1

    max_count = max(date_counts.values()) or 1
    bar_width = 30

    console.print(Text("每日学习曲线（最近 14 天）", style="bold cyan"))
    for day_str in sorted(date_counts.keys()):
        cnt = date_counts[day_str]
        bar_len = int(cnt / max_count * bar_width) if max_count > 0 else 0
        bar = "█" * bar_len
        if bar_len == 0:
            bar_str = f"{day_str} ─ {cnt}"
        else:
            bar_str = f"{day_str} {bar} {cnt}"
        console.print(Text(bar_str, style="cyan"))
    console.print()


# ============================================================================
# 交互式设置
# ============================================================================

def _render_settings_page(record: bool, hide_hint: bool) -> None:
    """渲染设置页。"""
    console = Console()
    console.clear()
    console.print(Text("─── MoFish 设置 ───", style="bold cyan"))
    console.print()
    cur = "[已开启]" if record else "[已关闭]"
    console.print(Text(f"  [1] 学习记录写入 data/sessions.jsonl   {cur}", style="white"))
    console.print(Text("     开启后每次成功背词会记录一条 JSONL", style="dim"))
    console.print()
    cur2 = "[已开启]" if hide_hint else "[已关闭]"
    console.print(Text(f"  [2] 隐藏判答屏底部提示              {cur2}", style="white"))
    console.print(Text("     开启后不再显示 [1] 认识 [2] 模糊 [3] 忘记", style="dim"))
    console.print()
    console.print(Text("  按 [1] / [2] 切换     按 [q] 返回", style="dim"))


def run_settings(config: Dict[str, Any], config_file: str, console: Console) -> None:
    """交互式设置页，修改后直接持久化到 config.json。"""
    record = config.get("record_enabled", False)
    hide_hint = config.get("hide_judge_hint", False)

    while True:
        _render_settings_page(record, hide_hint)
        k = read_key().lower()
        if k == "1":
            record = not record
        elif k == "2":
            hide_hint = not hide_hint
        elif k == "q":
            # 退出前持久化最新状态
            config["record_enabled"] = record
            config["hide_judge_hint"] = hide_hint
            try:
                with open(config_file, "w", encoding="utf-8") as f:
                    json.dump(config, f, ensure_ascii=False, indent=4)
            except Exception:
                pass
            return
        else:
            continue

        # 每次切换后立即保存
        config["record_enabled"] = record
        config["hide_judge_hint"] = hide_hint
        try:
            with open(config_file, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=4)
        except Exception:
            pass


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
