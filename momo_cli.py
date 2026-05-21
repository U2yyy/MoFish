#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
墨墨背单词 - 隐蔽式终端客户端
=============================

设计理念:
    这是一个"伪装日志模式"的背单词工具。单词会以假乱真的方式
    伪装成系统编译日志、数据库查询、模块加载等开发者常见日志，
    让使用者在办公环境中也能低调地背诵单词。

作者: MoFish CLI Team
Python: 3.8+
"""

import os
import sys
import json
import time
import random
import signal
from datetime import datetime
from typing import Optional, List, Dict, Any

# ============================================================================
# 依赖检查与导入
# ============================================================================

try:
    import requests
except ImportError:
    print("[错误] 缺少 requests 库，请运行: pip install requests")
    sys.exit(1)

try:
    from rich.console import Console
    from rich.table import Table
    from rich.progress import Progress, SpinnerColumn, TextColumn
    from rich.panel import Panel
    from rich.text import Text
except ImportError:
    print("[错误] 缺少 rich 库，请运行: pip install rich")
    sys.exit(1)

try:
    from readchar import readchar
except ImportError:
    # readchar 不可用时的降级方案
    readchar = None
    print("[警告] 缺少 readchar 库，按键响应可能需要回车确认。请运行: pip install readchar")

# ============================================================================
# 常量与配置
# ============================================================================

CONFIG_FILE = "config.json"  # 配置文件名

# ============================================================================
# 墨墨开放平台 API 配置（基于 document.yaml）
# ============================================================================

# API 服务器地址
# 生产服务: https://open.maimemo.com/open
# 测试服务: https://open-dev.maimemo.com/open
API_BASE_URL = "https://open.maimemo.com/open"

# ============================================================================
# API 端点定义（直接从 document.yaml 提取）
# ============================================================================
# 获取今日学习单词: POST /api/v1/study/get_today_items
# 查询单词详情: POST /api/v1/vocabulary/query
# 学习反馈状态: FAMILIAR / VAGUE / FORGET / WELL_FAMILIAR / CANCEL_WELL_FAMILIAR

ENDPOINTS = {
    # 获取今日学习任务
    # POST /api/v1/study/get_today_items
    "get_today_items": "/api/v1/study/get_today_items",

    # 获取今日学习进度
    # POST /api/v1/study/get_study_progress
    "get_study_progress": "/api/v1/study/get_study_progress",

    # 查询学习记录
    # POST /api/v1/study/query_study_records
    "query_study_records": "/api/v1/study/query_study_records",

    # 查询单词详情
    # POST /api/v1/vocabulary/query
    "query_vocabulary": "/api/v1/vocabulary/query",

    # 获取单词（根据拼写）
    # GET /api/v1/vocabulary?spelling=xxx
    "get_vocabulary": "/api/v1/vocabulary",

    # 提前复习
    # POST /api/v1/study/advance_study
    "advance_study": "/api/v1/study/advance_study",

    # 查询云词本
    # GET /api/v1/notepads?limit=10&offset=0
    "list_notepads": "/api/v1/notepads",
}

# 学习反馈状态枚举（StudyResponse）
# FAMILIAR: 认识
# VAGUE: 模糊
# FORGET: 忘记
# WELL_FAMILIAR: 熟知
# CANCEL_WELL_FAMILIAR: 取消熟知
LEARNING_STATUS = {
    "1": "FAMILIAR",    # 认识
    "2": "VAGUE",       # 模糊
    "3": "FORGET",      # 忘记
}

LEARNING_STATUS_DISPLAY = {
    "FAMILIAR": "认识",
    "VAGUE": "模糊",
    "FORGET": "忘记",
    "WELL_FAMILIAR": "熟知",
}

# 按键到状态的映射
KEY_TO_STATUS = {
    "1": "FAMILIAR",
    "2": "VAGUE",
    "3": "FORGET",
}

# ============================================================================
# 伪装日志模板
# ============================================================================

# 伪装日志格式模板列表 - 单词会嵌入到这些日志格式中
# 设计思路: 让日志看起来像真实的开发/系统日志
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

# 虚假堆栈跟踪模板 - 用于 Boss 键效果
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

Operations to perform:
  Apply ALL migrations: admin, auth, contenttypes, sessions
  Running migrations:
    No migrations to apply.
    Traceback (most recent call last):
  File "manage.py", line 22, in <module>
    execute_from_command_line(sys.argv)
Exception: Database locked""",
]

# ============================================================================
# MaimemoClient 类 - API 客户端
# ============================================================================

class MaimemoClient:
    """
    墨墨开放平台 API 客户端

    基于 document.yaml 中定义的 OpenAPI 3.1.0 规范实现。

    API Base URL: https://open.maimemo.com/open

    主要端点:
        POST /api/v1/study/get_today_items - 获取今日学习任务
        POST /api/v1/vocabulary/query - 查询单词详情
        POST /api/v1/study/advance_study - 提前复习/学习反馈
    """

    def __init__(self, api_token: str):
        """
        初始化客户端

        Args:
            api_token: 墨墨开放平台的 API Token
        """
        self.api_token = api_token.strip()
        self.base_url = API_BASE_URL
        self.session = requests.Session()

        # 设置默认请求头
        # 认证方式: Authorization: Bearer <token>
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "MoFish-CLI/1.0",
        })

        # 连接超时和读取超时（秒）
        self.timeout = (10, 30)

    def _make_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        params: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        统一的请求发送方法

        Args:
            method: HTTP 方法 (GET, POST, etc.)
            endpoint: API 端点路径
            data: 请求体数据 (dict)
            params: URL 查询参数 (dict)

        Returns:
            API 响应的 JSON 数据

        Raises:
            MaimemoAPIError: API 返回错误时
            MaimemoNetworkError: 网络请求失败时
        """
        url = f"{self.base_url}{endpoint}"

        try:
            response = self.session.request(
                method=method,
                url=url,
                json=data,
                params=params,
                timeout=self.timeout
            )

            # 尝试解析 JSON 响应
            try:
                result = response.json()
            except json.JSONDecodeError:
                result = {"raw_text": response.text}

            # 检查 HTTP 状态码
            if not response.ok:
                error_msg = result.get("message", result.get("error", "Unknown error"))
                raise MaimemoAPIError(
                    f"API 请求失败 [{response.status_code}]: {error_msg}",
                    status_code=response.status_code,
                    response=result
                )

            return result

        except requests.exceptions.Timeout:
            raise MaimemoNetworkError("API 请求超时，请检查网络连接")
        except requests.exceptions.ConnectionError:
            raise MaimemoNetworkError("无法连接到墨墨服务器，请检查网络")
        except MaimemoAPIError:
            raise
        except Exception as e:
            raise MaimemoNetworkError(f"请求异常: {str(e)}")

    def get_today_items(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        获取今日学习单词

        API 端点: POST /api/v1/study/get_today_items

        官方说明:
            获取当日学习列表，如果当日未打开 App 进行初始化则无法获取
            公测期间不保证可用性和可能会随时调整，需要在 App 中开启自动同步

        请求体参数 (可选):
            is_finished: bool - 筛选是否已完成
            is_new: bool - 筛选是否新学单词
            voc_ids: string[] - 根据单词 ID 列表查询，最多 1000
            spellings: string[] - 根据单词拼写列表查询，最多 1000
            limit: int - 最多获取前 1000 条数据，默认 50

        返回格式 (StudyTodayItem):
            {
                "voc_id": "单词ID",
                "voc_spelling": "单词拼写",
                "order": 1,
                "first_response": "FAMILIAR|VAGUE|FORGET|WELL_FAMILIAR|CANCEL_WELL_FAMILIAR",
                "is_new": false,
                "is_finished": false
            }

        注意:
            此接口只返回单词 ID 和拼写，不包含音标、释义等详情。
            需要额外调用 query_vocabulary 获取完整信息。
        """
        endpoint = ENDPOINTS["get_today_items"]

        # 请求体
        payload = {
            "limit": limit,
            # 可选筛选参数
            # "is_finished": False,  # 获取未完成的
            # "is_new": False,       # 获取复习词（非新词）
        }

        response = self._make_request("POST", endpoint, data=payload)

        # 解析响应: { data: { today_items: [...] } }
        items = response.get("data", {}).get("today_items", [])

        return items

    def query_vocabulary(self, spellings: List[str] = None, ids: List[str] = None) -> List[Dict[str, Any]]:
        """
        查询单词详情

        API 端点: POST /api/v1/vocabulary/query

        请求体参数 (互斥，只能生效一个):
            spellings: string[] - 根据拼写查询，最多 1000
            ids: string[] - 根据 id 查询，最多 1000

        返回格式 (Vocabulary):
            {
                "id": "单词ID",
                "spelling": "单词拼写"
            }

        注意:
            文档说 Vocabulary 有 id 和 spelling，但实际响应可能包含更多字段
            （如音标、释义等），需要根据实际情况调整解析逻辑。
        """
        endpoint = ENDPOINTS["query_vocabulary"]

        payload = {}
        if spellings:
            payload["spellings"] = spellings
        elif ids:
            payload["ids"] = ids
        else:
            return []

        response = self._make_request("POST", endpoint, data=payload)

        # 解析响应: { voc: [...] }
        voc_list = response.get("voc", [])

        return voc_list

    def get_word_details_batch(self, spellings: List[str]) -> Dict[str, Dict[str, Any]]:
        """
        批量获取单词详情（用于获取音标、释义等）

        由于 get_today_items 只返回 voc_id 和 voc_spelling，
        需要调用此方法获取每个单词的完整信息。

        Args:
            spellings: 单词拼写列表

        Returns:
            Dict[str, Dict]: 以拼写为 key 的单词详情字典
        """
        # 分批查询，每批最多 1000
        batch_size = 1000
        result = {}

        for i in range(0, len(spellings), batch_size):
            batch = spellings[i:i + batch_size]
            try:
                voc_list = self.query_vocabulary(spellings=batch)
                for voc in voc_list:
                    result[voc.get("spelling", "")] = voc
            except Exception as e:
                print(f"[警告] 批量查询单词详情失败: {e}")

        return result

    def advance_study(self, voc_ids: List[str]) -> bool:
        """
        提前复习 / 学习反馈

        API 端点: POST /api/v1/study/advance_study

        官方说明:
            将单词提前到当下马上复习，需要升级到 10 级解锁提前复习功能
            公测期间不保证可用性

        请求体:
            { "voc_ids": string[] }  # 单词 ID 列表，最多 1000

        返回:
            { "advanced_count": int }  # 成功提前的数量

        注意:
            这个接口设计用于"提前复习"功能，可能是将单词移到待复习队列。
            对于学习反馈（认识/模糊/忘记），可能需要不同的接口。
            目前公测期间，暂未有明确的学习反馈接口文档。
        """
        endpoint = ENDPOINTS["advance_study"]

        payload = {
            "voc_ids": voc_ids
        }

        try:
            response = self._make_request("POST", endpoint, data=payload)
            advanced_count = response.get("advanced_count", 0)
            return advanced_count > 0
        except Exception as e:
            # 静默处理同步失败，不影响用户继续学习
            print(f"[警告] 进度同步失败: {e}")
            return False

    def get_study_progress(self) -> Dict[str, Any]:
        """
        获取今日学习进度

        API 端点: POST /api/v1/study/get_study_progress

        返回:
            {
                "finished": int,    # 已完成单词数
                "total": int,       # 今日应完成总数
                "study_time": int   # 今日学习时长（毫秒）
            }
        """
        endpoint = ENDPOINTS["get_study_progress"]
        return self._make_request("POST", endpoint)

    def query_study_records(self, limit: int = 50) -> Dict[str, Any]:
        """
        查询学习记录

        API 端点: POST /api/v1/study/query_study_records

        请求体:
            {
                next_study_date?: { start?, end? },
                voc_ids?: string[],
                spellings?: string[],
                as_count?: bool,
                limit?: int  # 默认 50，最多 1000
            }

        返回:
            {
                "records": [StudyRecord],
                "count": int
            }
        """
        endpoint = ENDPOINTS["query_study_records"]
        payload = {"limit": limit}
        return self._make_request("POST", endpoint, data=payload)

    def get_vocabulary(self, spelling: str) -> Optional[Dict[str, Any]]:
        """
        获取单个单词详情（根据拼写）

        API 端点: GET /api/v1/vocabulary?spelling=xxx

        Args:
            spelling: 单词拼写

        返回:
            Vocabulary 对象或 None
        """
        endpoint = ENDPOINTS["get_vocabulary"]
        try:
            response = self._make_request("GET", endpoint, params={"spelling": spelling})
            return response.get("voc")
        except Exception:
            return None

    def list_notepads(self, limit: int = 10, offset: int = 0) -> List[Dict[str, Any]]:
        """
        查询云词本列表

        API 端点: GET /api/v1/notepads?limit=10&offset=0

        Args:
            limit: 查询数量
            offset: 查询跳过

        返回:
            [BriefNotepad, ...]
        """
        endpoint = ENDPOINTS["list_notepads"]
        response = self._make_request("GET", endpoint, params={"limit": limit, "offset": offset})
        return response.get("notepads", [])

    def close(self):
        """关闭 HTTP 会话"""
        self.session.close()


# ============================================================================
# 自定义异常类
# ============================================================================

class MaimemoAPIError(Exception):
    """API 返回错误时的异常"""
    def __init__(self, message: str, status_code: int = 0, response: Dict = None):
        super().__init__(message)
        self.status_code = status_code
        self.response = response or {}


class MaimemoNetworkError(Exception):
    """网络请求异常"""
    pass


# ============================================================================
# StealthConsole 类 - 隐蔽终端交互
# ============================================================================

class StealthConsole:
    """
    隐蔽式终端控制台

    职责:
        1. 以伪装日志形式展示单词
        2. 捕获用户按键 (1/2/3/q/b)
        3. 渲染释义展示
        4. 处理 Boss 键效果

    设计理念:
        单词不应该"突兀"地出现在屏幕上。通过伪装成开发者常见的
        编译日志、查询日志、模块加载日志，让背单词行为融入
        日常开发环境，不引起旁人注意。
    """

    def __init__(self, console: Console):
        """
        初始化隐蔽终端

        Args:
            console: rich.Console 实例，用于渲染美化输出
        """
        self.console = console
        self.word_version = f"{random.randint(2, 4)}.{random.randint(0, 9)}.{random.randint(0, 9)}"

    def _get_timestamp(self) -> str:
        """获取当前时间戳字符串"""
        return datetime.now().strftime("%H:%M:%S")

    def _select_disguise_template(self, word: str) -> str:
        """
        选择伪装日志模板

        Args:
            word: 当前要展示的单词

        Returns:
            格式化后的伪装日志字符串
        """
        template = random.choice(DISGUISE_TEMPLATES)
        return template.format(
            time=self._get_timestamp(),
            word=word,
            version=self.word_version
        )

    def display_disguised_word(self, word: str) -> None:
        """
        以伪装日志形式展示单词

        设计说明:
            将单词伪装成一行系统日志，日志级别为 [INFO] 或 [DEBUG]，
            格式类似开发环境的编译输出。单词使用高亮色显示，
            营造"日志中恰好出现这个单词"的视觉效果。

        Args:
            word: 要展示的单词
        """
        disguise_log = self._select_disguise_template(word)

        # 创建 Rich Text 对象，对单词部分使用高亮
        # 格式: [时间戳] [INFO] Loading package "单词" (v3.2.1) successfully.
        parts = disguise_log.split('"')
        if len(parts) >= 2:
            # 重构文本，让单词部分高亮
            text = Text()
            text.append(parts[0] + '"', style="cyan")  # 日志前缀
            text.append(word, style="bold bright_green")  # 单词高亮
            text.append('"' + parts[2], style="cyan")  # 日志后缀
        else:
            text = Text(disguise_log, style="cyan")

        # 输出伪装日志
        self.console.print(text)

    def display_definition(
        self,
        word: str,
        phonetic: str = "",
        definition: str = "",
        status: str = ""
    ) -> None:
        """
        以"API 响应"格式展示单词释义

        设计说明:
            用户做出选择后，释义以 JSON 格式的 API 响应形式展示。
            这种格式对于非技术人员看起来像是某种数据查询结果，
            不太会引起注意。

        Args:
            word: 单词
            phonetic: 音标
            definition: 释义
            status: 用户选择的状态
        """
        status_text = LEARNING_STATUS_DISPLAY.get(status, status)

        # 构造虚假的 API 响应
        response_json = {
            "status": "success",
            "code": 200,
            "data": {
                "word": word,
                "phonetic": phonetic,
                "definition": definition,
                "learning_status": status_text,
                "timestamp": datetime.now().isoformat()
            }
        }

        # 创建格式化的 JSON 文本
        json_str = json.dumps(response_json, ensure_ascii=False, indent=2)

        # 逐行渲染，添加颜色
        lines = json_str.split('\n')
        styled_lines = []
        for line in lines:
            if '"word"' in line or '"phonetic"' in line or '"definition"' in line:
                # 键名高亮
                styled_lines.append(Text(line, style="dim cyan"))
            elif '"learning_status"' in line:
                # 状态值根据选择变色
                color = {
                    "FAMILIAR": "bright_green",
                    "VAGUE": "yellow",
                    "FORGET": "bright_red",
                }.get(status, "white")
                styled_lines.append(Text(line, style=color))
            else:
                styled_lines.append(Text(line, style="white"))

        # 使用 Panel 包裹，增加边框
        panel = Panel(
            "\n".join([str(line) for line in styled_lines]),
            title="[API Response]",
            border_style="dim",
            padding=(1, 2)
        )

        self.console.print(panel)
        self.console.print()

        # 提示用户按任意键继续
        self.console.print(Text("Press any key to continue...", style="dim italic"))

    def display_progress(self, current: int, total: int, status: str = "") -> None:
        """
        展示简洁的进度指示

        Args:
            current: 当前第几个
            total: 总共多少个
            status: 当前选择的状态
        """
        # 简单进度条: [#####-----) 5/12
        filled = int(20 * current / total) if total > 0 else 0
        bar = '=' * filled + '-' * (20 - filled)
        status_icon = {
            "FAMILIAR": '[OK]',
            "VAGUE": '[~]',
            "FORGET": '[X]'
        }.get(status, '')

        progress_text = f"\r[{bar}] {current}/{total} {status_icon}    "
        self.console.print(Text(progress_text, style="cyan"), end="")

    def trigger_boss_key(self) -> None:
        """
        触发 Boss 键效果

        设计说明:
            当用户按下 'b' 键时，立即清屏并显示一大段虚假的
            Python 错误堆栈或 Git merge 失败信息。
            目的是在有人突然出现在屏幕前时，快速切换到"工作状态"。

        效果:
            1. 清屏
            2. 显示 30+ 行虚假错误信息
            3. 光标移到最底下
        """
        # 清屏
        self.console.clear()

        # 选择一个虚假堆栈跟踪
        fake_output = random.choice(FAKE_TRACEBACKS)

        # 如果堆栈不够长，再补充一些假日志让它更真实
        additional_lines = []
        for _ in range(30):
            log_type = random.choice(["DEBUG", "INFO", "WARN", "ERROR"])
            modules = ["main.py", "compiler.py", "build.py", "settings.py", "utils.py"]
            actions = [
                "Loading configuration...",
                "Checking dependencies...",
                "Validating input...",
                "Processing...",
                "Cleaning up temp files...",
            ]
            additional_lines.append(
                f"[{self._get_timestamp()}] [{log_type}] "
                f"{random.choice(modules)}: {random.choice(actions)}"
            )

        all_lines = additional_lines + ["", fake_output]

        # 打印所有行
        for line in all_lines:
            if "ERROR" in line or "Traceback" in line or "failed" in line.lower():
                print(Text(line, style="bright_red"))
            elif "WARN" in line:
                print(Text(line, style="yellow"))
            else:
                print(Text(line, style="dim"))

        # 移动光标到最底下（通过打印空行实现）
        for _ in range(5):
            print()

    def wait_for_key(self) -> str:
        """
        等待用户按键

        Returns:
            用户按下的字符

        注意:
            优先使用 readchar 实现无感按键（无需回车）。
            如果不可用，降级为 input() 方式。
        """
        if readchar:
            # 使用 readchar，无感按键
            key = readchar()
            return key
        else:
            # 降级方案：需要回车
            return input()

    def display_welcome(self, word_count: int) -> None:
        """
        显示欢迎信息

        Args:
            word_count: 今日待学习单词数
        """
        self.console.print()
        welcome = Text(
            f"MoFish CLI v1.0 - 今日待复习: {word_count} 词",
            style="bold cyan"
        )
        self.console.print(welcome)
        self.console.print()

        # 显示按键提示（低调样式）
        help_text = Text(
            "[1] 认识   [2] 模糊   [3] 忘记   [q] 保存退出   [b] Boss键",
            style="dim"
        )
        self.console.print(help_text)
        self.console.print()

    def display_completion(self, learned: int, known: int, fuzzy: int, forgotten: int) -> None:
        """
        显示学习完成摘要

        Args:
            learned: 总学习词数
            known: 认识数
            fuzzy: 模糊数
            forgotten: 忘记数
        """
        self.console.print()

        # 创建一个表格展示统计
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
    """
    加载配置文件

    Returns:
        API Token 字符串，配置不存在则返回 None
    """
    if not os.path.exists(CONFIG_FILE):
        return None

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
            return config.get("api_token", "")
    except (json.JSONDecodeError, IOError):
        return None


def save_config(api_token: str) -> bool:
    """
    保存配置到文件

    Args:
        api_token: 墨墨 API Token

    Returns:
        保存是否成功
    """
    try:
        config = {"api_token": api_token.strip()}
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
        return True
    except IOError as e:
        print(f"配置保存失败: {e}")
        return False


def clear_config() -> bool:
    """
    清除已保存的配置（Token）

    Returns:
        是否成功清除
    """
    if not os.path.exists(CONFIG_FILE):
        print("配置文件不存在，无需清除")
        return True

    try:
        os.remove(CONFIG_FILE)
        print(f"配置已清除 ({CONFIG_FILE})")
        return True
    except IOError as e:
        print(f"清除配置失败: {e}")
        return False


def setup_config() -> Optional[str]:
    """
    引导用户设置 API Token

    首次运行或配置文件损坏时调用此函数引导用户输入。

    Returns:
        用户输入的 API Token，失败返回 None
    """
    print()
    print("=" * 60)
    print("MoFish CLI - 首次运行配置")
    print("=" * 60)
    print()
    print("请输入您的墨墨开放平台 API Token")
    print("获取地址: https://open.maimemo.com")
    print()
    print("注意: Token 将保存在本地 config.json 文件中")
    print()

    while True:
        token = input("请输入 Token: ").strip()

        if not token:
            print("Token 不能为空，请重新输入")
            continue

        if len(token) < 10:
            print("Token 长度过短，请确认是否输入正确")
            continue

        # 询问确认
        confirm = input(f"\n确认保存此 Token? (y/n): ").strip().lower()
        if confirm == 'y':
            break

    if save_config(token):
        print(f"\n配置已保存到 {CONFIG_FILE}")
        return token

    return None


# ============================================================================
# 主程序入口
# ============================================================================

def main():
    """
    主程序入口

    流程:
        1. 检查/加载配置文件
        2. 初始化 API 客户端
        3. 获取今日学习任务
        4. 获取单词详情（音标、释义）
        5. 进入学习循环
        6. 处理退出
    """
    # 命令行参数解析
    import argparse
    parser = argparse.ArgumentParser(description="MoFish CLI - 墨墨背单词隐蔽版")
    parser.add_argument("--clear-token", "-c", action="store_true",
                        help="清除已保存的 API Token")
    args = parser.parse_args()

    # 处理清除 Token
    if args.clear_token:
        clear_config()
        sys.exit(0)

    # 创建 Rich Console 实例
    console = Console()

    # 清理屏幕
    console.clear()

    # 打印启动信息（伪装成项目启动）
    print(Text("[14:00:00] [INFO] MoFish CLI initializing...", style="cyan"))
    time.sleep(0.3)
    print(Text("[14:00:00] [DEBUG] Loading configuration from ./config.json", style="dim"))
    time.sleep(0.2)
    print(Text("[14:00:00] [INFO] Environment: production", style="cyan"))
    time.sleep(0.1)
    print()

    # =========================================================================
    # 步骤 1-3: 加载配置、初始化客户端、获取任务（可重试）
    # =========================================================================
    client = None
    retry_mode = False

    while True:
        # 加载或重新输入配置
        if not retry_mode:
            api_token = load_config()
            if not api_token:
                api_token = setup_config()
                if not api_token:
                    console.print(Text("[错误] 配置失败，程序退出", style="bold red"))
                    sys.exit(1)
            else:
                console.print(Text("[14:00:00] [INFO] Configuration loaded successfully", style="cyan"))
        else:
            # 重新输入 Token
            console.print()
            console.print(Text("您的 Token 可能已过期或无效，请选择操作：", style="yellow"))
            console.print(Text("  [1] 重新输入 Token", style="cyan"))
            console.print(Text("  [2] 清除 Token 并退出", style="cyan"))
            console.print(Text("  [q] 直接退出", style="dim"))
            console.print()

            choice = input("请选择 (1/2/q): ").strip().lower()

            if choice == '1':
                new_token = setup_config()
                if not new_token:
                    console.print(Text("[错误] Token 输入失败", style="bold red"))
                    sys.exit(1)
                api_token = new_token
            elif choice == '2':
                clear_config()
                sys.exit(0)
            else:
                sys.exit(0)

        retry_mode = True  # 之后进入重试模式

        # 关闭旧客户端
        if client:
            client.close()

        # 初始化 API 客户端
        console.print(Text("[14:00:00] [DEBUG] Initializing Maimemo API client...", style="dim"))
        console.print(Text(f"[14:00:00] [DEBUG] API Base URL: {API_BASE_URL}", style="dim"))

        try:
            client = MaimemoClient(api_token)
            console.print(Text("[14:00:00] [INFO] API client initialized", style="cyan"))
        except Exception as e:
            console.print(Text(f"[错误] API 客户端初始化失败: {e}", style="bold red"))
            sys.exit(1)

        # 获取今日学习任务
        console.print(Text("[14:00:00] [INFO] Fetching today's study task...", style="cyan"))
        console.print(Text(f"[14:00:00] [DEBUG] HTTP POST {ENDPOINTS['get_today_items']}", style="dim"))

        try:
            today_items = client.get_today_items(limit=100)
            total_words = len(today_items)

            if total_words == 0:
                # 没有待复习单词，显示功能菜单
                console.print()
                console.print(Text("=" * 50, style="dim"))
                console.print(Text("  今日暂无待复习单词", style="bright_green"))
                console.print(Text("=" * 50, style="dim"))
                console.print()
                console.print(Text("请选择操作：", style="yellow"))
                console.print(Text("  [1] 查看今日学习进度", style="cyan"))
                console.print(Text("  [2] 查询学习记录", style="cyan"))
                console.print(Text("  [3] 查询云词本", style="cyan"))
                console.print(Text("  [4] 手动输入单词学习", style="cyan"))
                console.print(Text("  [q] 退出程序", style="dim"))
                console.print()

                choice = input("请选择 (1/2/3/4/q): ").strip().lower()

                if choice == '1':
                    # 查看今日学习进度
                    console.print()
                    try:
                        progress = client.get_study_progress()
                        p = progress.get("progress", {})
                        finished = p.get("finished", 0)
                        total = p.get("total", 0)
                        study_time = p.get("study_time", 0)
                        study_minutes = study_time // 60000 if study_time else 0

                        console.print(Text("[今日学习进度]", style="bold cyan"))
                        console.print(Text(f"  已完成: {finished} / {total} 词", style="white"))
                        console.print(Text(f"  学习时长: {study_minutes} 分钟", style="white"))
                        if finished >= total and total > 0:
                            console.print(Text("  太棒了！今日任务已全部完成！", style="bright_green"))
                    except Exception as e:
                        console.print(Text(f"[错误] 获取学习进度失败: {e}", style="bold red"))

                    input("\n按回车返回上一级...")

                elif choice == '2':
                    # 查询学习记录
                    console.print()
                    try:
                        records = client.query_study_records(limit=20)
                        record_list = records.get("records", [])
                        count = records.get("count", 0)

                        console.print(Text("[学习记录]", style="bold cyan"))
                        console.print(Text(f"  共 {count} 条记录，显示最近 20 条", style="dim"))
                        console.print()

                        if record_list:
                            for i, rec in enumerate(record_list, 1):
                                spelling = rec.get("voc_spelling", "?")
                                study_count = rec.get("study_count", 0)
                                last_response = rec.get("last_response", "")
                                console.print(f"  {i}. {spelling} (复习{study_count}次, {last_response})")
                        else:
                            console.print(Text("  暂无学习记录", style="dim"))
                    except Exception as e:
                        console.print(Text(f"[错误] 获取学习记录失败: {e}", style="bold red"))

                    input("\n按回车返回上一级...")

                elif choice == '3':
                    # 查询云词本
                    console.print()
                    try:
                        notepads = client.list_notepads(limit=20)
                        console.print(Text("[云词本]", style="bold cyan"))
                        console.print()

                        if notepads:
                            for i, np in enumerate(notepads, 1):
                                title = np.get("title", "?")
                                brief = np.get("brief", "")
                                np_type = np.get("type", "")
                                console.print(f"  {i}. {title} - {brief} [{np_type}]")
                        else:
                            console.print(Text("  暂无云词本", style="dim"))
                    except Exception as e:
                        console.print(Text(f"[错误] 获取云词本失败: {e}", style="bold red"))

                    input("\n按回车返回上一级...")

                elif choice == '4':
                    # 手动输入单词学习
                    console.print()
                    console.print(Text("请输入单词（输入 q 返回）：", style="cyan"))
                    word_input = input("单词: ").strip().lower()

                    if word_input and word_input != 'q':
                        try:
                            voc = client.get_vocabulary(word_input)
                            if voc:
                                console.print()
                                console.print(Text(f"[单词信息]", style="bold cyan"))
                                console.print(Text(f"  单词: {voc.get('spelling', word_input)}", style="white"))
                                # 尝试获取更多字段（如果有的话）
                                if "phonetic" in voc:
                                    console.print(Text(f"  音标: {voc.get('phonetic', '')}", style="white"))
                                if "definition" in voc:
                                    console.print(Text(f"  释义: {voc.get('definition', '')}", style="white"))
                            else:
                                console.print(Text(f"[警告] 未找到单词: {word_input}", style="yellow"))
                        except Exception as e:
                            console.print(Text(f"[错误] 查询失败: {e}", style="bold red"))

                    input("\n按回车返回上一级...")

                else:
                    # 退出
                    client.close()
                    sys.exit(0)

                # 继续循环，显示主菜单
                continue

            console.print(Text(f"[14:00:00] [INFO] Loaded {total_words} words for review", style="cyan"))
            time.sleep(0.5)
            break  # 成功获取，跳出重试循环

        except MaimemoAPIError as e:
            console.print(Text(f"[错误] API 请求失败: {e}", style="bold red"))

            # 401 认证错误，不退出，继续循环让用户选择
            if getattr(e, 'status_code', 0) != 401:
                console.print(Text("[错误] 请检查您的 Token 是否正确，或稍后重试", style="dim"))
                client.close()
                sys.exit(1)
            # 401 错误，继续循环让用户选择
        except MaimemoNetworkError as e:
            console.print(Text(f"[错误] 网络错误: {e}", style="bold red"))
            client.close()
            sys.exit(1)

    # =========================================================================
    # 步骤 4: 获取单词详情（音标、释义）
    # =========================================================================
    console.print(Text("[14:00:00] [INFO] Fetching word details...", style="cyan"))
    console.print(Text(f"[14:00:00] [DEBUG] HTTP POST {ENDPOINTS['query_vocabulary']}", style="dim"))

    try:
        # 提取所有单词拼写
        spellings = [item.get("voc_spelling", "") for item in today_items]

        # 批量获取单词详情
        # 注意: 根据文档，vocabulary query 可能只返回 id 和 spelling
        # 音标和释义可能需要其他接口获取，这里做降级处理
        word_details = {}
        try:
            word_details = client.get_word_details_batch(spellings)
        except Exception as e:
            console.print(Text(f"[警告] 获取单词详情失败: {e}", style="yellow"))
            console.print(Text("[警告] 将只显示单词拼写", style="dim"))

        # 合并数据：优先使用 API 返回的详情，否则只用拼写
        words = []
        for item in today_items:
            spelling = item.get("voc_spelling", "")
            detail = word_details.get(spelling, {})
            words.append({
                "id": item.get("voc_id", ""),
                "word": spelling,
                # 以下字段可能为空，取决于 API 实际返回
                "phonetic": detail.get("phonetic", ""),
                "definition": detail.get("definition", ""),
                "is_new": item.get("is_new", False),
                "is_finished": item.get("is_finished", False),
                "first_response": item.get("first_response", ""),
            })

        console.print(Text(f"[14:00:00] [INFO] Word details loaded", style="cyan"))
        time.sleep(0.5)

    except Exception as e:
        console.print(Text(f"[警告] 获取单词详情异常: {e}", style="yellow"))
        # 降级：只使用基本信息
        words = []
        for item in today_items:
            words.append({
                "id": item.get("voc_id", ""),
                "word": item.get("voc_spelling", ""),
                "phonetic": "",
                "definition": "",
            })

    # =========================================================================
    # 步骤 5: 进入学习循环
    # =========================================================================

    # 初始化隐蔽终端
    stealth = StealthConsole(console)

    # 显示欢迎信息
    stealth.display_welcome(total_words)

    # 学习状态统计
    stats = {"known": 0, "fuzzy": 0, "forgotten": 0}
    current_index = 0

    # 信号处理：优雅退出
    def signal_handler(sig, frame):
        console.print()
        console.print(Text("\n[INFO] 保存进度并退出...", style="cyan"))
        client.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    # 学习主循环
    while current_index < total_words:
        word_data = words[current_index]
        word = word_data["word"]

        # 清屏并显示进度
        console.clear()
        stealth.display_progress(current_index + 1, total_words, "")

        # 以伪装日志形式展示单词
        stealth.display_disguised_word(word)

        # 等待用户按键
        key = stealth.wait_for_key()

        # 处理按键
        if key.lower() == 'q':
            # 保存并退出
            break

        elif key.lower() == 'b':
            # Boss 键：显示假日志
            stealth.trigger_boss_key()
            input()  # 等待用户确认
            continue

        elif key in ['1', '2', '3']:
            status = KEY_TO_STATUS.get(key, "")

            # 尝试同步到云端
            # 注意: advance_study 接口用于"提前复习"，学习反馈接口暂不明确
            try:
                client.advance_study([word_data["id"]])
            except Exception as e:
                pass  # 静默处理，不影响学习流程

            # 更新统计
            if status == "FAMILIAR":
                stats["known"] += 1
            elif status == "VAGUE":
                stats["fuzzy"] += 1
            else:
                stats["forgotten"] += 1

            # 显示释义
            stealth.display_definition(
                word=word,
                phonetic=word_data.get("phonetic", ""),
                definition=word_data.get("definition", ""),
                status=status
            )

            # 等待用户按键后继续
            stealth.wait_for_key()

            # 下一词
            current_index += 1

        else:
            # 无效按键，静默忽略
            continue

    # =========================================================================
    # 步骤 6: 完成学习
    # =========================================================================

    console.clear()

    # 显示完成摘要
    stealth.display_completion(
        learned=current_index,
        known=stats["known"],
        fuzzy=stats["fuzzy"],
        forgotten=stats["forgotten"]
    )

    # 关闭客户端
    client.close()

    console.print(Text("[INFO] Session closed. Goodbye!", style="dim"))
    time.sleep(1)
    console.clear()


if __name__ == "__main__":
    main()
