"""Запуск пайплайна для выполнения заданий курса."""
import asyncio
import sys
import os

os.environ["PYTHONIOENCODING"] = "utf-8"

from dotenv import load_dotenv
load_dotenv()

from langchain_core.messages import HumanMessage
from src.agent.graph.pipeline import pipeline


async def main():
    print("=== Запуск пайплайна BroJS ===")
    result = await pipeline.ainvoke(
        {
            "tasks": [],
            "current_index": 0,
            "results": [],
            "errors": [],
        },
        {"configurable": {"thread_id": "pipeline-main"}},
    )
    print("\n=== Результат пайплайна ===")
    for r in result.get("results", []):
        print(f"  Task {r['task_id'][:8]}: {r['status']} (mode={r['mode']}, retries={r['retries']})")
    for e in result.get("errors", []):
        print(f"  ОШИБКА: {e}")
    print("=== Готово ===")


asyncio.run(main())
