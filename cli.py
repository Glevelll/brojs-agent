"""
CLI для brojs-agent.

Использование:
  python cli.py solve <task_id>   — решить одно задание
  python cli.py run               — решить все todo-задания курса
  python cli.py status            — проверить статусы заданий
"""
import asyncio
import os
import sys

# Обходим локальный прокси
os.environ["NO_PROXY"] = (
    "openrouter.ai,platform.brojs.ru,git.brojs.ru,"
    + os.environ.get("NO_PROXY", "")
)

# UTF-8 на Windows
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from dotenv import load_dotenv
load_dotenv()


# ---------------------------------------------------------------------------
# Команды
# ---------------------------------------------------------------------------

async def cmd_solve(task_id: str) -> None:
    """Решить одно задание."""
    from langchain_core.messages import HumanMessage
    from src.agent.agent import homework_direct_agent

    print(f"[cli] Решаю задание {task_id[:8]}...")
    result = await homework_direct_agent.ainvoke(
        {"messages": [HumanMessage(content=f"Реши задание taskId={task_id}")]},
        {"configurable": {"thread_id": f"cli-{task_id}"}},
    )
    final = result["messages"][-1]
    print(f"\n{'='*60}")
    print(final.content if hasattr(final, "content") else str(final))
    print('='*60)


async def cmd_run() -> None:
    """Решить все todo-задания курса (LLM-оркестратор управляет всем)."""
    import time
    from langchain_core.messages import HumanMessage
    from src.agent.agent import agent

    prompt = (
        "Выполни все задания со статусом todo в курсе KFU-26-1 "
        "(courseId=698b49da77cb6d4d2e43ce78).\n\n"
        "Шаги:\n"
        "1. Получи список заданий через mcp__journal-bh-professor__tasks_list\n"
        "2. Для каждого задания со статусом todo вызови solve_task(task_id=...)\n"
        "3. Выполняй строго по одному заданию, жди результата перед следующим\n"
        "4. Доложи итоговые результаты"
    )

    print("[cli] Агент-оркестратор запущен (LLM управляет всем)...")
    result = await agent.ainvoke(
        {"messages": [HumanMessage(content=prompt)]},
        {"configurable": {"thread_id": f"run-all-{int(time.time())}"}},
    )

    final = (result.get("messages") or [{}])[-1]
    print(f"\n{'='*60}")
    print(getattr(final, "content", str(final)))
    print('='*60)


async def cmd_status() -> None:
    """Проверить статусы всех заданий."""
    from src.agent.mcp_client import load_journal_toolsets
    import json

    STATUS_EMOJI = {
        "done":             "✅",
        "ready_for_review": "🔍",
        "in_progress":      "🔄",
        "todo":             "📋",
        "rejected":         "❌",
    }

    print("[cli] Получаю список заданий...")
    journal   = load_journal_toolsets()
    tools     = {t.name: t for t in journal.tasks_submissions_tools}
    tool      = tools.get("mcp__journal-bh-professor__tasks_list") \
                or next((v for k, v in tools.items() if "tasks_list" in k), None)
    if not tool:
        print(f"Ошибка: инструмент tasks_list не найден. Доступны: {list(tools.keys())}")
        return

    raw   = await tool.ainvoke({"courseId": "698b49da77cb6d4d2e43ce78"})
    text  = next((x["text"] for x in raw if x.get("type") == "text"), str(raw)) if isinstance(raw, list) else str(raw)

    try:
        data  = json.loads(text)
        items = data.get("tasks", data) if isinstance(data, dict) else data
    except (json.JSONDecodeError, TypeError):
        items = []

    if not items:
        print("Нет данных о заданиях")
        return

    print(f"\n{'Статус':<22} {'ID':>10}  Название")
    print("-" * 80)
    counts: dict[str, int] = {}
    for item in items:
        t      = item.get("task", item) if isinstance(item, dict) else {}
        tid    = t.get("id", "")
        status = item.get("status", "")
        title  = t.get("title", t.get("name", ""))
        emoji  = STATUS_EMOJI.get(status, "❓")
        print(f"  {emoji} {status:<18} {tid[:8]}...  {title}")
        counts[status] = counts.get(status, 0) + 1

    print()
    for s, n in counts.items():
        print(f"  {STATUS_EMOJI.get(s,'❓')} {s}: {n}")


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1].lower()

    if cmd == "solve":
        if len(sys.argv) < 3:
            print("Использование: python cli.py solve <task_id>")
            sys.exit(1)
        asyncio.run(cmd_solve(sys.argv[2]))

    elif cmd == "run":
        asyncio.run(cmd_run())

    elif cmd == "status":
        asyncio.run(cmd_status())

    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
