"""Консольный интерфейс для управления агентом BroJS.

Использование:
    python console.py

Команды:
    tasks          — показать все задания и статусы
    solve <id>     — решить конкретное задание по ID
    run            — решить все todo-задания автоматически
    exit           — выйти
"""
import asyncio
import os

os.environ["NO_PROXY"] = "openrouter.ai,platform.brojs.ru,git.brojs.ru," + os.environ.get("NO_PROXY", "")

from dotenv import load_dotenv
load_dotenv()

from solve_task import fetch_todo_tasks, fetch_all_tasks, solve, run_all

HELP = """
Команды:
  tasks          — список заданий и их статусы
  solve <id>     — решить задание по полному ID
  run            — решить все todo-задания автоматически
  help           — показать это сообщение
  exit           — выйти
"""


async def main():
    print("=" * 50)
    print("  BroJS Agent Console")
    print("=" * 50)
    print(HELP)

    while True:
        try:
            cmd = input(">> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nВыход.")
            break

        if not cmd:
            continue

        if cmd in ("exit", "quit", "выход"):
            print("Выход.")
            break

        elif cmd in ("help", "?"):
            print(HELP)

        elif cmd == "tasks":
            tasks = await fetch_all_tasks()
            if not tasks:
                print("  Заданий не найдено.")
            else:
                print(f"\n  {'ID':10}  {'Статус':20}  Название")
                print(f"  {'-'*10}  {'-'*20}  {'-'*40}")
                for t in tasks:
                    print(f"  {t['id'][:8]}...  {t['status']:20}  {t['title']}")
                print()

        elif cmd.startswith("solve "):
            task_id = cmd.split(" ", 1)[1].strip()
            if not task_id:
                print("  Укажи ID задания: solve <id>")
            else:
                await solve(task_id)

        elif cmd == "run":
            await run_all()

        else:
            print(f"  Неизвестная команда: '{cmd}'. Введи 'help' для справки.")


if __name__ == "__main__":
    asyncio.run(main())
