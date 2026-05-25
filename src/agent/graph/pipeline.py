"""LangGraph pipeline: последовательно выполняет все незакрытые задания курса."""
from __future__ import annotations

import asyncio
import base64
import json
import re
from typing import TypedDict

from langchain_core.messages import HumanMessage
from langgraph.graph import START, StateGraph

from src.agent.agent import homework_direct_agent, rework_agent
from src.agent.constants import COURSE_ID, GITEA_OWNER
from src.agent.gitea_tools import _get as gitea_get
from src.agent.mcp_client import JOURNAL_PREFIX, load_journal_toolsets

# ---------------------------------------------------------------------------
# Типы состояния
# ---------------------------------------------------------------------------

class TaskInfo(TypedDict):
    id: str
    title: str
    status: str


class PipelineState(TypedDict):
    tasks: list[TaskInfo]
    current_index: int
    results: list[dict]
    errors: list[str]


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

_journal = load_journal_toolsets()


def _get_journal_tool(suffix: str):
    all_tools = _journal.courses_lessons_tools + _journal.tasks_submissions_tools
    target = f"{JOURNAL_PREFIX}{suffix}"
    for t in all_tools:
        if t.name == target:
            return t
    return None


def _parse_text(raw) -> str:
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("type") == "text":
                return item.get("text", "")
    return str(raw)


def _parse_tasks(raw) -> list[TaskInfo]:
    text = _parse_text(raw) if not isinstance(raw, str) else raw
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return []
    items = data.get("tasks", data) if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    result = []
    for item in items:
        t = item.get("task", item) if isinstance(item, dict) else {}
        tid = t.get("id", "")
        if tid:
            result.append(TaskInfo(
                id=tid,
                title=t.get("title", t.get("name", "")),
                status=item.get("status", "") if isinstance(item, dict) else "",
            ))
    return result


_CODING_KW = [
    "code", "напиши", "реализуй", "python", "langchain", "langgraph",
    "агент", "agent", "граф", "graph", "файл", "функц", "программ",
    "скрипт", "алгоритм", "библиотек", "api", "сервер", "модуль", "класс",
    # дополнительные ключевые слова для курса KFU-26-1
    "ai", "llm", "rag", "mcp", "stream", "human", "interrupt", "middleware",
    "память", "игра", "текст", "fluency", "практическ", "создай", "создайт",
    "задание", "deep", "search", "поиск",
]

# Задания, которые точно не требуют кода (теория, чтение)
_NON_CODING_TITLES = []


def _is_coding(task: TaskInfo) -> bool:
    title = (task.get("title") or "").lower()
    if any(nc in title for nc in _NON_CODING_TITLES):
        return False
    # Если хотя бы одно кодинговое слово — берём задание
    if any(kw in title for kw in _CODING_KW):
        return True
    # Для этого курса все задания — программирование, берём всё
    return True


async def _task_text(task_id: str) -> str:
    tool = _get_journal_tool("task_text")
    if not tool:
        return ""
    try:
        return _parse_text(await tool.ainvoke({"taskId": task_id}))
    except Exception:
        return ""


async def _task_json(task_id: str) -> dict:
    tool = _get_journal_tool("task_get")
    if not tool:
        return {}
    try:
        raw = _parse_text(await tool.ainvoke({"taskId": task_id}))
        return json.loads(raw)
    except Exception:
        return {}


async def _existing_repo_url(task_id: str) -> str | None:
    data = await _task_json(task_id)
    url = (data.get("answer") or {}).get("content", "")
    if url and url.startswith(f"https://git.brojs.ru/{GITEA_OWNER}/"):
        return url
    return None


async def _verify_repo(repo_name: str) -> dict:
    """Проверяет наличие ключевых файлов в репозитории через Gitea API."""
    verification: dict = {"files_found": [], "files_missing": [], "issues": []}
    for fname in ["main.py", "requirements.txt", "src/main.py", "app.py"]:
        try:
            result = gitea_get(f"/api/v1/repos/{GITEA_OWNER}/{repo_name}/contents/{fname}")
            raw = result.get("content", "")
            content = base64.b64decode(raw.replace("\n", "")).decode("utf-8")
            verification["files_found"].append(fname)
            if len(content.strip()) < 50:
                verification["issues"].append(f"{fname}: слишком короткий ({len(content)} символов)")
            if fname == "requirements.txt" and "langchain" not in content:
                verification["issues"].append("requirements.txt: нет зависимости langchain")
        except Exception:
            if fname in ("main.py", "requirements.txt"):
                verification["files_missing"].append(fname)
    return verification


def _needs_retry(v: dict) -> bool:
    has_code = any(f in v["files_found"] for f in ["main.py", "src/main.py", "app.py"])
    has_reqs = "requirements.txt" in v["files_found"]
    return not has_code or not has_reqs or bool(v["issues"])


def _fix_prompt(task: TaskInfo, repo_name: str, v: dict) -> str:
    lines = [f"Решение задания {task['id']} нуждается в доработке.", ""]
    if v["files_missing"]:
        lines.append(f"Отсутствуют файлы: {', '.join(v['files_missing'])}")
    for issue in v["issues"]:
        lines.append(f"Проблема: {issue}")
    lines += [
        "",
        f"Репозиторий: https://git.brojs.ru/{GITEA_OWNER}/{repo_name}",
        "",
        "Требуется:",
        "- Напиши ПОЛНЫЙ код в main.py (не только requirements.txt)",
        "- Добавь requirements.txt с зависимостями (langchain>1.0.0)",
        "- Используй gitea_write_file для исправления файлов",
        "- Вызови task_update_answer и task_submit",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Вспомогательное: retry при rate-limit 429
# ---------------------------------------------------------------------------

MAX_RETRIES = 2
RATE_LIMIT_RETRIES = 5        # сколько раз повторять при 429
RATE_LIMIT_PAUSE   = 90       # секунд ожидания перед повтором
TASK_PAUSE         = 15       # пауза между заданиями (снижает давление на rate limit)


def _is_rate_limit(exc: Exception) -> bool:
    """Проверяет, является ли исключение ошибкой rate-limit (429)."""
    msg = str(exc)
    return "429" in msg or "rate" in msg.lower() or "rate_limit" in msg.lower()


async def _invoke_with_retry(agent, messages, config):
    """Вызывает агента с автоматическим retry при 429."""
    for attempt in range(1, RATE_LIMIT_RETRIES + 1):
        try:
            return await agent.ainvoke(messages, config)
        except Exception as e:
            if _is_rate_limit(e) and attempt < RATE_LIMIT_RETRIES:
                wait = RATE_LIMIT_PAUSE * attempt
                print(f"[pipeline] Rate limit (попытка {attempt}/{RATE_LIMIT_RETRIES}), "
                      f"жду {wait}с...")
                await asyncio.sleep(wait)
            else:
                raise


# ---------------------------------------------------------------------------
# Узлы графа
# ---------------------------------------------------------------------------


async def fetch_tasks(state: PipelineState) -> dict:
    """Загружает все незакрытые задания курса."""
    tool = _get_journal_tool("tasks_list")
    if not tool:
        return {"tasks": [], "current_index": 0, "results": [], "errors": ["tasks_list не найден"]}

    raw = await tool.ainvoke({"courseId": COURSE_ID})
    all_tasks = _parse_tasks(raw)

    pending = [t for t in all_tasks if t["status"] in ("todo", "in_progress", "", None)]
    coding  = [t for t in pending if _is_coding(t)]
    skipped = [t for t in pending if not _is_coding(t)]

    if skipped:
        print(f"[pipeline] Пропущены не-кодинговые задания: {[t['title'] for t in skipped]}")

    print(f"[pipeline] Найдено {len(coding)} заданий для выполнения")
    return {"tasks": coding, "current_index": 0, "results": [], "errors": []}


async def process_one_task(state: PipelineState) -> dict:
    """Выполняет одно задание."""
    if state["current_index"] >= len(state["tasks"]):
        return state

    task     = state["tasks"][state["current_index"]]
    task_id  = task["id"]
    results  = list(state.get("results", []))
    errors   = list(state.get("errors", []))

    repo_url  = await _existing_repo_url(task_id)
    is_rework = repo_url is not None

    if is_rework:
        data     = await _task_json(task_id)
        comments = data.get("comments") or data.get("feedback", "")
        prompt = (
            f"Пересдача задания.\n\n"
            f"ID: {task_id}\n"
            f"Название: {task.get('title', '')}\n"
            f"Репозиторий: {repo_url}\n"
            f"Комментарии преподавателя: {comments}\n\n"
            "Внеси исправления и отправь снова."
        )
        agent_to_use = rework_agent
    else:
        task_text = await _task_text(task_id)
        prompt = (
            f"Выполни задание.\n\n"
            f"ID: {task_id}\n"
            f"Название: {task.get('title', '')}\n\n"
            f"Текст задания:\n{task_text}\n\n"
            "Первая сдача. Напиши код с нуля."
        )
        agent_to_use = homework_direct_agent

    try:
        print(f"[pipeline] Задание {task_id[:8]} — {'пересдача' if is_rework else 'первая сдача'}: "
              f"{task.get('title','')[:50]}")

        result = await _invoke_with_retry(
            agent_to_use,
            {"messages": [HumanMessage(content=prompt)]},
            {"configurable": {"thread_id": f"pipeline-task-{task_id}"}},
        )
        last    = (result.get("messages") or [{}])[-1]
        output  = getattr(last, "content", str(last))
        mode    = "rework" if is_rework else "first_submission"

        # Верификация репозитория (только для новых сдач)
        repo_name    = f"task-{task_id}"
        verification = {}
        retries      = 0

        if not is_rework:
            verification = await _verify_repo(repo_name)
            while _needs_retry(verification) and retries < MAX_RETRIES:
                retries += 1
                fix_msg = _fix_prompt(task, repo_name, verification)
                result  = await _invoke_with_retry(
                    agent_to_use,
                    {"messages": [HumanMessage(content=fix_msg)]},
                    {"configurable": {"thread_id": f"pipeline-task-{task_id}-retry-{retries}"}},
                )
                verification = await _verify_repo(repo_name)

        print(f"[pipeline] Задание {task_id[:8]} — OK (retries={retries})")
        results.append({
            "task_id":      task_id,
            "status":       "done",
            "mode":         mode,
            "output":       output[:500],
            "verification": verification,
            "retries":      retries,
        })

    except Exception as e:
        print(f"[pipeline] Задание {task_id[:8]} — ОШИБКА: {e}")
        errors.append(f"Задание {task_id} ({'rework' if is_rework else 'new'}): {e}")

    # Пауза между заданиями чтобы не перегружать rate limit
    print(f"[pipeline] Пауза {TASK_PAUSE}с перед следующим заданием...")
    await asyncio.sleep(TASK_PAUSE)

    return {
        "results":       results,
        "current_index": state["current_index"] + 1,
        "errors":        errors,
    }


def route(state: PipelineState) -> str:
    if state["current_index"] < len(state["tasks"]):
        return "process_one_task"
    return "__end__"


# ---------------------------------------------------------------------------
# Сборка графа
# ---------------------------------------------------------------------------

_builder = StateGraph(PipelineState)
_builder.add_node("fetch_tasks",      fetch_tasks)
_builder.add_node("process_one_task", process_one_task)

_builder.add_edge(START, "fetch_tasks")
_builder.add_conditional_edges("fetch_tasks",      route, {"process_one_task": "process_one_task", "__end__": "__end__"})
_builder.add_conditional_edges("process_one_task", route, {"process_one_task": "process_one_task", "__end__": "__end__"})

pipeline = _builder.compile()
