"""Загрузка инструментов BroJS Journal через MCP (HTTP transport)."""
import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from dotenv import load_dotenv
from langchain_mcp_adapters.client import MultiServerMCPClient

load_dotenv()

JOURNAL_SERVER_NAME = "journal-bh-professor"
JOURNAL_PREFIX      = f"mcp__{JOURNAL_SERVER_NAME}__"

# ⚠️ ЗАГЛУШКА #2 — замени в .env: JOURNAL_TOKEN=jrnl_xxxxxxxx
_JOURNAL_TOKEN = os.getenv("JOURNAL_TOKEN", "YOUR_JOURNAL_TOKEN_HERE")

JOURNAL_MCP_URL = "https://platform.brojs.ru/jrnl-bh/api/mcp"

# Инструменты для работы с курсами и уроками
JOURNAL_COURSES_LESSONS = frozenset({
    "courses_list",
    "lessons_list",
})

# Инструменты для работы с заданиями и сдачами
JOURNAL_TASKS_SUBMISSIONS = frozenset({
    "tasks_list",
    "task_text",
    "task_get",
    "task_update_answer",
    "task_submit",
    "task_comment",
    "task_submission_status",
})


@dataclass(frozen=True)
class JournalToolsets:
    courses_lessons_tools: list
    tasks_submissions_tools: list


def _build_mcp_config() -> dict:
    return {
        JOURNAL_SERVER_NAME: {
            "transport": "http",
            "url": JOURNAL_MCP_URL,
            "headers": {
                "Authorization": f"Bearer {_JOURNAL_TOKEN}",
            },
        }
    }


async def _fetch_tools() -> dict[str, list]:
    config = _build_mcp_config()
    client = MultiServerMCPClient(config)
    out: dict[str, list] = {}
    for name in config:
        try:
            out[name] = await client.get_tools(server_name=name)
        except Exception as exc:
            print(f"MCP '{name}': не удалось загрузить инструменты — {type(exc).__name__}: {exc}")
            out[name] = []
    return out


def _load_tools_sync() -> dict[str, list]:
    """Загружает MCP-инструменты синхронно, корректно обрабатывая уже запущенный event loop."""
    try:
        asyncio.get_running_loop()
        # Event loop уже запущен (ноутбук / LangGraph dev) — запускаем в отдельном потоке
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: asyncio.run(_fetch_tools())).result()
    except RuntimeError:
        # Event loop не запущен — просто asyncio.run
        return asyncio.run(_fetch_tools())


def _rename_tools(tools_by_server: dict[str, list]) -> None:
    """Добавляет префикс mcp__<server>__ к именам инструментов."""
    for server_name, tools in tools_by_server.items():
        for tool in tools:
            tool.name = f"mcp__{server_name}__{tool.name}"


def _subset(all_tools: list, suffixes: frozenset[str]) -> list:
    return [
        t for t in all_tools
        if t.name.startswith(JOURNAL_PREFIX)
        and t.name.removeprefix(JOURNAL_PREFIX) in suffixes
    ]


def load_journal_toolsets() -> JournalToolsets:
    """Загружает и возвращает разбитые на группы инструменты журнала."""
    print("Загружаем MCP-инструменты журнала...")
    tools_by_server = _load_tools_sync()
    _rename_tools(tools_by_server)

    journal_tools = tools_by_server.get(JOURNAL_SERVER_NAME, [])

    print(f"=== MCP {JOURNAL_SERVER_NAME}: загружено {len(journal_tools)} инструментов ===")
    for t in journal_tools:
        print(f"  - {t.name}")

    return JournalToolsets(
        courses_lessons_tools=_subset(journal_tools, JOURNAL_COURSES_LESSONS),
        tasks_submissions_tools=_subset(journal_tools, JOURNAL_TASKS_SUBMISSIONS),
    )
