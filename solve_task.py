"""
Быстрый решатель заданий BroJS.
Схема: читаем задание (MCP) → 1 LLM-вызов → пушим на Gitea → сабмитим (MCP).
Автономный — не импортирует src.agent, нет двойной загрузки MCP.

Использование:
    python solve_task.py <full_task_id>
"""
import asyncio
import base64
import io
import json
import os
import sys

# Перенастраиваем stdout/stderr на UTF-8 (Windows cp1251 не осиливает →, ✅ и т.д.)
# reconfigure() меняет кодировку у существующего враппера на месте — безопаснее, чем
# создавать новый TextIOWrapper поверх буфера (последнее ломается при перенаправлении в файл).
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    elif hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass
try:
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    elif hasattr(sys.stderr, "buffer"):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

# Обходим локальный прокси
os.environ["NO_PROXY"] = "openrouter.ai,platform.brojs.ru,git.brojs.ru," + os.environ.get("NO_PROXY", "")

from dotenv import load_dotenv
load_dotenv()

import httpx
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_openai import ChatOpenAI

GITEA_BASE_URL = "https://git.brojs.ru"
GITEA_OWNER    = os.getenv("GITEA_OWNER", "glevelll")
GITEA_TOKEN    = os.getenv("GITEA_TOKEN", "")
JOURNAL_TOKEN  = os.getenv("JOURNAL_TOKEN", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

MCP_URL = "https://platform.brojs.ru/jrnl-bh/api/mcp"

# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------

llm = ChatOpenAI(
    model="openai/gpt-oss-20b:free",
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENAI_API_KEY,
    temperature=0.0,
    max_tokens=4096,
)

# ---------------------------------------------------------------------------
# MCP — один постоянный клиент на весь запуск
# ---------------------------------------------------------------------------

_mcp_tools: dict = {}
_mcp_client = None          # держим клиент живым чтобы сессия не переоткрывалась
_last_mcp_call_time = 0.0   # для паузы между вызовами

# Exponential backoff: 15, 30, 60, 120, 240 секунд
_BACKOFF = [15, 30, 60, 120, 240]
_INTER_CALL_DELAY = 1.5     # секунд между последовательными MCP-вызовами


def _is_429(exc: BaseException) -> bool:
    """Рекурсивно проверяет, содержит ли исключение (или вложенные) ошибку 429.

    Нужно потому что anyio оборачивает HTTP-ошибки в ExceptionGroup,
    и '429' есть только во вложенном исключении, а не в str(ExceptionGroup).
    """
    if "429" in str(exc):
        return True
    # ExceptionGroup (Python 3.11+ / anyio): смотрим вложенные
    if hasattr(exc, "exceptions"):
        return any(_is_429(sub) for sub in exc.exceptions)
    # __cause__ / __context__
    if exc.__cause__ is not None and exc.__cause__ is not exc:
        return _is_429(exc.__cause__)
    return False


async def _load_mcp():
    global _mcp_tools, _mcp_client
    if _mcp_tools:
        return
    config = {
        "journal": {
            "transport": "streamable_http",
            "url": MCP_URL,
            "headers": {"Authorization": f"Bearer {JOURNAL_TOKEN}"},
        }
    }
    _mcp_client = MultiServerMCPClient(config)
    for i, pause in enumerate([0] + _BACKOFF):
        try:
            if pause:
                print(f"  [mcp] 429 при загрузке, жду {pause}с (попытка {i+1})...")
                await asyncio.sleep(pause)
            tools = await _mcp_client.get_tools(server_name="journal")
            _mcp_tools = {t.name: t for t in tools}
            print(f"  [mcp] Загружено {len(_mcp_tools)} инструментов")
            return
        except BaseException as e:
            if not _is_429(e) or i == len(_BACKOFF):
                raise


async def mcp_call(name: str, args: dict):
    global _last_mcp_call_time
    await _load_mcp()

    # Пауза между вызовами — предотвращает burst
    import time
    elapsed = time.monotonic() - _last_mcp_call_time
    if elapsed < _INTER_CALL_DELAY:
        await asyncio.sleep(_INTER_CALL_DELAY - elapsed)

    tool = _mcp_tools.get(name)
    if not tool:
        raise RuntimeError(f"MCP tool '{name}' not found. Available: {list(_mcp_tools.keys())}")

    for i, pause in enumerate([0] + _BACKOFF):
        try:
            if pause:
                print(f"  [mcp] {name} → 429, жду {pause}с (попытка {i+1})...")
                await asyncio.sleep(pause)
            result = await tool.ainvoke(args)
            _last_mcp_call_time = time.monotonic()
            if isinstance(result, list):
                return next((x["text"] for x in result if x.get("type") == "text"), str(result))
            return str(result)
        except BaseException as e:
            if not _is_429(e) or i == len(_BACKOFF):
                raise


# ---------------------------------------------------------------------------
# Gitea
# ---------------------------------------------------------------------------

def _gh():
    return {"Authorization": f"token {GITEA_TOKEN}", "Content-Type": "application/json"}


async def _get_task_meta(task_id: str) -> dict:
    """Возвращает существующий repo_url и комментарии преподавателя (если есть).

    Используется для определения: первая сдача или пересдача после отклонения.
    """
    try:
        raw = await mcp_call("task_get", {"taskId": task_id})
        data = json.loads(raw)
        url = (data.get("answer") or {}).get("content", "")
        repo_url = url if url.startswith(f"{GITEA_BASE_URL}/{GITEA_OWNER}/") else None
        comments = data.get("comments") or data.get("feedback", "") or ""
        # comments может быть списком объектов
        if isinstance(comments, list):
            comments = "\n".join(
                c.get("text", c.get("content", str(c))) for c in comments if c
            )
        return {"repo_url": repo_url, "comments": str(comments).strip()}
    except Exception as e:
        print(f"  [meta] Не удалось получить метаданные задания: {e}")
        return {"repo_url": None, "comments": ""}


def gitea_create_repo(name: str) -> str:
    with httpx.Client(timeout=30) as c:
        r = c.post(f"{GITEA_BASE_URL}/api/v1/user/repos", headers=_gh(),
                   json={"name": name, "private": False, "auto_init": False})
        if r.status_code == 409:
            return f"{GITEA_BASE_URL}/{GITEA_OWNER}/{name}"
        r.raise_for_status()
        return r.json().get("html_url", f"{GITEA_BASE_URL}/{GITEA_OWNER}/{name}")


def gitea_write(repo: str, path: str, content: str, msg: str):
    encoded = base64.b64encode(content.encode()).decode()
    url = f"{GITEA_BASE_URL}/api/v1/repos/{GITEA_OWNER}/{repo}/contents/{path}"
    with httpx.Client(timeout=30) as c:
        r = c.get(url, headers=_gh())
        if r.status_code == 200:
            sha = r.json().get("sha", "")
            c.put(url, headers=_gh(), json={"message": msg, "content": encoded, "sha": sha}).raise_for_status()
        else:
            c.post(url, headers=_gh(), json={"message": msg, "content": encoded}).raise_for_status()


# ---------------------------------------------------------------------------
# LLM: генерация кода
# ---------------------------------------------------------------------------

_PROMPT = '''\
Ты — Python-разработчик. Напиши решение для учебного задания по LLM/AI.
Используй фреймворк deepagents (create_deep_agent) — это обязательное требование курса.

## Задание
{task_text}

## ОБЯЗАТЕЛЬНЫЕ ТЕХНИЧЕСКИЕ ПАТТЕРНЫ

> ⚠️ ЗАПРЕЩЕНО: langchain_ollama, OllamaEmbeddings, Ollama, langchain_community.
> Для LLM и эмбеддингов — ТОЛЬКО OpenRouter через langchain_openai.

### LLM — всегда OpenRouter:
```python
import os
from langchain_openai import ChatOpenAI

# Используем OpenRouter вместо Ollama: облачный API не требует локального GPU,
# совместим с OpenAI SDK «из коробки» (только base_url), легко масштабируется.
# Модель gpt-oss-20b:free — бесплатный тир OpenRouter для учебных задач.
# Ключ OPENAI_API_KEY=sk-or-v1-... хранится в .env (не в репозитории).
llm = ChatOpenAI(
    model="openai/gpt-oss-20b:free",
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENAI_API_KEY"),
    temperature=0.0,
)
```

### Базовый агент (deepagents) — ОБЯЗАТЕЛЬНАЯ основа:
```python
import asyncio, os
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from langchain.tools import tool
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend, LocalShellBackend, CompositeBackend

llm = ChatOpenAI(model="openai/gpt-oss-20b:free", base_url="https://openrouter.ai/api/v1", api_key=os.getenv("OPENAI_API_KEY"))

backend = CompositeBackend([
    LocalShellBackend(workspace_dir="./workspace"),
    FilesystemBackend(),
])

@tool
def my_tool(query: str) -> str:
    """Tool description."""
    return f"result for {{query}}"

agent = create_deep_agent(
    model=llm,
    tools=[my_tool],
    backend=backend,
    system_prompt="You are a helpful agent.",
)

async def main():
    result = await agent.ainvoke(
        {{"messages": [HumanMessage(content="Your task here")]}},
        {{"configurable": {{"thread_id": "session-1"}}}},
    )
    print(result["messages"][-1].content)

if __name__ == "__main__":
    asyncio.run(main())
```
requirements.txt: deepagents, langchain-openai>=0.3.0, langchain>=1.2.10, langgraph>=0.2.0

### RAG с ChromaDB (для RAG-заданий с ChromaDB):
```python
# ВАЖНО: embeddings — ТОЛЬКО OpenAIEmbeddings через OpenRouter, НЕ OllamaEmbeddings!
from langchain_openai import OpenAIEmbeddings
from langchain_chroma import Chroma
from langchain_core.documents import Document

embeddings = OpenAIEmbeddings(
    model="text-embedding-3-small",
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENAI_API_KEY"),
)
vector_store = Chroma(collection_name="knowledge", embedding_function=embeddings)

@tool
def search_knowledge(query: str) -> str:
    """Search the knowledge base for relevant information."""
    docs = vector_store.similarity_search(query, k=3)
    return "\\n".join(d.page_content for d in docs) if docs else "No results."

@tool
def add_to_knowledge(content: str, title: str = "doc") -> str:
    """Add content to the knowledge base."""
    vector_store.add_documents([Document(page_content=content, metadata={{"title": title}})])
    return f"Added: {{title}}"
```
requirements.txt добавить: langchain-chroma, chromadb

### Планирующий агент (для planning-заданий):
```python
from langgraph.graph import StateGraph, START, END
from typing import TypedDict, Annotated
from langgraph.graph.message import add_messages

class PlanState(TypedDict):
    messages: Annotated[list, add_messages]
    plan: list[str]
    current_step: int

def planner_node(state):
    # LLM создаёт план
    ...

def executor_node(state):
    # LLM выполняет шаг плана
    ...
```

### Самокорректирующийся агент:
```python
# Агент проверяет свой вывод и исправляет если нужно
@tool
def validate_output(output: str) -> str:
    """Validate the output and return issues if any."""
    issues = []
    if len(output) < 10:
        issues.append("Output too short")
    return "OK" if not issues else f"Issues: {{', '.join(issues)}}"
```

### Структурированный вывод (Pydantic):
```python
from pydantic import BaseModel, Field
from langchain_core.output_parsers import PydanticOutputParser

class MyOutput(BaseModel):
    field1: str = Field(description="...")
    field2: int = Field(description="...")

parser = PydanticOutputParser(pydantic_object=MyOutput)
```

## Требования
- Полный рабочий код без заглушек (no pass, TODO, ...)
- ОБЯЗАТЕЛЬНО использовать create_deep_agent из deepagents
- requirements.txt: deepagents, langchain>=1.2.10, langchain-openai>=0.3.0, langgraph>=0.2.0 + нужные доп. зависимости

{rework_section}
## Ответ — ТОЛЬКО JSON без markdown:
{{"main_py": "...", "requirements_txt": "...", "extra_files": {{}}}}

extra_files — только если нужны доп. файлы, иначе пустой объект.
'''

_REWORK_SECTION = '''\
## ПЕРЕСДАЧА — комментарии преподавателя
Предыдущее решение было отклонено. Обязательно учти замечания:

{comments}

Исправь именно то, что указано выше. Не меняй то, что работало правильно.
'''


async def generate(task_text: str, rework_comments: str = "", retries=5) -> dict:
    rework_section = (
        _REWORK_SECTION.format(comments=rework_comments) if rework_comments else ""
    )
    prompt = _PROMPT.format(task_text=task_text, rework_section=rework_section)
    for attempt in range(1, retries + 1):
        try:
            print(f"  [llm] Генерирую решение (попытка {attempt})...")
            resp = await llm.ainvoke(prompt)
            raw = resp.content.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            return json.loads(raw.strip())
        except json.JSONDecodeError as e:
            print(f"  [llm] JSON parse error: {e}. Повтор...")
            if attempt == retries:
                raise
        except Exception as e:
            if "429" in str(e) and attempt < retries:
                wait = 90 * attempt
                print(f"  [llm] 429, жду {wait}с (попытка {attempt}/{retries})...")
                await asyncio.sleep(wait)
            else:
                raise


# ---------------------------------------------------------------------------
# Основная логика
# ---------------------------------------------------------------------------

COURSE_ID = "698b49da77cb6d4d2e43ce78"

# ---------------------------------------------------------------------------
# Автоматическая выборка todo-заданий
# ---------------------------------------------------------------------------

def _parse_todo_tasks(raw: str) -> list[str]:
    """Парсит ответ tasks_list и возвращает ID заданий со статусом todo/in_progress."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    items = data.get("tasks", data) if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    result = []
    for item in items:
        t      = item.get("task", item) if isinstance(item, dict) else {}
        tid    = t.get("id", "")
        status = item.get("status", "")
        if tid and status in ("todo", "in_progress", "", None):
            title = t.get("title", t.get("name", ""))
            result.append((tid, title))
    return result


async def fetch_todo_tasks(course_id: str = COURSE_ID) -> list[tuple[str, str]]:
    """Возвращает список (task_id, title) незакрытых заданий курса."""
    print(f"[auto] Получаем список заданий курса {course_id}...")
    raw = await mcp_call("tasks_list", {"courseId": course_id})
    tasks = _parse_todo_tasks(raw)
    print(f"[auto] Найдено todo-заданий: {len(tasks)}")
    for tid, title in tasks:
        print(f"  - {tid[:8]}... {title}")
    return tasks


async def fetch_all_tasks(course_id: str = COURSE_ID) -> list[dict]:
    """Возвращает все задания курса с их статусами (для мониторинга)."""
    raw = await mcp_call("tasks_list", {"courseId": course_id})
    try:
        data = json.loads(raw)
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
            result.append({
                "id":     tid,
                "title":  t.get("title", t.get("name", "")),
                "status": item.get("status", ""),
            })
    return result


# ---------------------------------------------------------------------------
# Полный автоматический прогон
# ---------------------------------------------------------------------------

async def run_all(target_ids: list[str] | None = None, course_id: str = COURSE_ID):
    """Решает все todo-задания курса (или только target_ids если указан список).

    Это точка входа для run_pipeline.py — никакого ручного вызова не нужно.
    """
    if target_ids:
        tasks = [(tid, "") for tid in target_ids]
        print(f"[auto] Целевые задания: {target_ids}")
    else:
        tasks = await fetch_todo_tasks(course_id)

    if not tasks:
        print("[auto] Нет заданий для выполнения.")
        return

    results = []
    for i, (task_id, title) in enumerate(tasks, 1):
        print(f"\n[auto] Задание {i}/{len(tasks)}: {task_id[:8]}... {title}")
        try:
            repo_url = await solve(task_id)
            results.append({"task_id": task_id, "status": "ok", "url": repo_url})
        except Exception as e:
            print(f"[auto] ОШИБКА при решении {task_id[:8]}: {e}")
            results.append({"task_id": task_id, "status": "error", "error": str(e)})
        # Пауза между заданиями
        if i < len(tasks):
            print("[auto] Пауза 15с перед следующим заданием...")
            await asyncio.sleep(15)

    print(f"\n{'='*60}")
    print("ИТОГ:")
    for r in results:
        status_icon = "✅" if r["status"] == "ok" else "❌"
        detail = r.get("url") or r.get("error", "")
        print(f"  {status_icon} {r['task_id'][:8]}... → {detail}")
    print('='*60)
    return results


async def solve(task_id: str):
    print(f"\n{'='*60}")
    print(f"Задание: {task_id}")
    print('='*60)

    # 0. Проверяем: первая сдача или пересдача
    print("[0/5] Проверяем статус задания...")
    meta      = await _get_task_meta(task_id)
    is_rework = meta["repo_url"] is not None
    comments  = meta["comments"]
    if is_rework:
        print(f"  ⟳ ПЕРЕСДАЧА (репо уже есть: {meta['repo_url']})")
        if comments:
            print(f"  Комментарии преподавателя: {comments[:200]}")
    else:
        print("  ✦ Первая сдача")

    # 1. Читаем текст задания
    print("[1/5] Читаем текст задания...")
    task_text = await mcp_call("task_text", {"taskId": task_id})
    print(f"  Получено {len(task_text)} символов")

    # 2. Генерируем код (с учётом комментариев если пересдача)
    print("[2/5] Генерируем код (1 LLM-вызов)...")
    solution = await generate(task_text, rework_comments=comments if is_rework else "")
    main_py      = solution.get("main_py", "")
    requirements = solution.get("requirements_txt", "")
    extra        = solution.get("extra_files", {})
    print(f"  main.py: {len(main_py)} символов, requirements.txt: {len(requirements)} символов")

    # 3. Создаём репо (при пересдаче — 409, вернёт существующий URL)
    repo = f"task-{task_id}"
    print(f"[3/5] {'Обновляем' if is_rework else 'Создаём'} репозиторий {repo}...")
    repo_url = gitea_create_repo(repo)
    print(f"  {repo_url}")

    # 4. Пушим файлы
    commit_prefix = "fix:" if is_rework else "add"
    print("[4/5] Пушим файлы...")
    gitea_write(repo, "main.py", main_py, f"{commit_prefix} main.py")
    print("  main.py ✓")
    gitea_write(repo, "requirements.txt", requirements, f"{commit_prefix} requirements.txt")
    print("  requirements.txt ✓")
    for fname, fcontent in extra.items():
        gitea_write(repo, fname, fcontent, f"{commit_prefix} {fname}")
        print(f"  {fname} ✓")

    # 5. Сабмитим
    print("[5/5] Сабмитим...")
    await mcp_call("task_update_answer", {
        "taskId": task_id, "answerType": "link", "content": repo_url,
    })
    print("  task_update_answer ✓")
    await asyncio.sleep(3)
    await mcp_call("task_submit", {"taskId": task_id, "confirmSubmit": True})
    print("  task_submit ✓")

    print(f"\n✅ Готово! Репозиторий: {repo_url}")
    return repo_url


async def main():
    if len(sys.argv) < 2:
        print("Использование: python solve_task.py <task_id>")
        sys.exit(1)
    await solve(sys.argv[1])


if __name__ == "__main__":
    asyncio.run(main())
