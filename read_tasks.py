import asyncio, json, os
os.environ["NO_PROXY"] = "openrouter.ai,platform.brojs.ru,git.brojs.ru," + os.environ.get("NO_PROXY", "")
from dotenv import load_dotenv
load_dotenv()
from src.agent.mcp_client import load_journal_toolsets, JOURNAL_PREFIX

TASK_IDS = [
    "6a1864fd",  # Планирующий агент
    "6a186500",  # Структурированный вывод (Pydantic)
    "6a1864f7",  # RAG-агент с ChromaDB
    "6a1864fa",  # Самокорректирующийся агент
]

async def main():
    j = load_journal_toolsets()
    text_tool = next(t for t in j.tasks_submissions_tools if t.name == f"{JOURNAL_PREFIX}task_text")
    for tid in TASK_IDS:
        raw = await text_tool.ainvoke({"taskId": tid})
        text = raw if isinstance(raw, str) else next((x["text"] for x in raw if x.get("type") == "text"), str(raw))
        print(f"\n{'='*60}")
        print(f"ЗАДАНИЕ {tid}")
        print('='*60)
        print(text)

asyncio.run(main())
