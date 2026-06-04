"""Запуск пайплайна для выполнения заданий курса."""
import asyncio
import os
import traceback

os.environ["PYTHONIOENCODING"] = "utf-8"
# Обходим локальный прокси для OpenRouter (иначе SSL-ошибка)
os.environ["NO_PROXY"] = "openrouter.ai,platform.brojs.ru,git.brojs.ru," + os.environ.get("NO_PROXY", "")

from dotenv import load_dotenv
load_dotenv()

from src.agent.graph.pipeline import pipeline, TaskInfo, process_one_task, route, PipelineState

# Целевые задания (None = все todo-задания автоматически)
TARGET_IDS = [
    "6a1864f78a94f887e50d46da",  # Экзамен: RAG-агент с ChromaDB и веб-поиском
]


async def main():
    print("=== Запуск пайплайна BroJS ===")

    if TARGET_IDS:
        tasks = [TaskInfo(id=tid, title="", status="todo") for tid in TARGET_IDS]
        print(f"Целевые задания: {TARGET_IDS}")
        initial_state = {
            "tasks": tasks,
            "current_index": 0,
            "results": [],
            "errors": [],
        }
        from langgraph.graph import StateGraph, START
        builder = StateGraph(PipelineState)
        builder.add_node("process_one_task", process_one_task)
        builder.add_edge(START, "process_one_task")
        builder.add_conditional_edges(
            "process_one_task", route,
            {"process_one_task": "process_one_task", "__end__": "__end__"}
        )
        targeted_pipeline = builder.compile()
        try:
            result = await targeted_pipeline.ainvoke(
                initial_state,
                {"configurable": {"thread_id": "pipeline-targeted"}},
            )
        except Exception as e:
            print(f"КРИТИЧЕСКАЯ ОШИБКА: {e}")
            traceback.print_exc()
            return
    else:
        try:
            result = await pipeline.ainvoke(
                {"tasks": [], "current_index": 0, "results": [], "errors": []},
                {"configurable": {"thread_id": "pipeline-main"}},
            )
        except Exception as e:
            print(f"КРИТИЧЕСКАЯ ОШИБКА: {e}")
            traceback.print_exc()
            return

    print("\n=== Результат пайплайна ===")
    for r in result.get("results", []):
        print(f"  Task {r['task_id'][:8]}: {r['status']} (mode={r['mode']}, retries={r['retries']})")
    for e in result.get("errors", []):
        print(f"  ОШИБКА: {e}")
    print("=== Готово ===")


asyncio.run(main())
