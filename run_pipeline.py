"""Запуск пайплайна для автоматического выполнения заданий курса.

Использование:
    python run_pipeline.py           # решить все todo-задания автоматически
    python run_pipeline.py <id1> <id2>  # решить конкретные задания по ID
"""
import asyncio
import io
import os
import sys

os.environ["PYTHONIOENCODING"] = "utf-8"
# Перенастраиваем stdout/stderr на UTF-8 уже после старта Python
# Используем reconfigure() — безопасно при любом типе перенаправления вывода.
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
os.environ["PYTHONUNBUFFERED"] = "1"
os.environ["NO_PROXY"] = (
    "openrouter.ai,platform.brojs.ru,git.brojs.ru,"
    + os.environ.get("NO_PROXY", "")
)

from dotenv import load_dotenv
load_dotenv()

from solve_task import run_all

# Задания из командной строки или жёстко заданный список.
# Оставь пустым [] — тогда агент сам возьмёт все todo-задания с BroJS.
TARGET_IDS: list[str] = []

async def main():
    # Командная строка имеет приоритет над TARGET_IDS
    cli_ids = sys.argv[1:] if len(sys.argv) > 1 else None
    ids = cli_ids or TARGET_IDS or None   # None = автоматически все todo
    await run_all(target_ids=ids)

if __name__ == "__main__":
    asyncio.run(main())
