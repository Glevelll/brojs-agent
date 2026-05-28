import asyncio, json
from dotenv import load_dotenv
load_dotenv()
from src.agent.mcp_client import load_journal_toolsets, JOURNAL_PREFIX
from src.agent.constants import COURSE_ID


async def main():
    j = load_journal_toolsets()
    tool = next((t for t in j.tasks_submissions_tools if t.name == f"{JOURNAL_PREFIX}tasks_list"), None)
    if not tool:
        print("tasks_list not found")
        return
    raw = await tool.ainvoke({"courseId": COURSE_ID})
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("type") == "text":
                raw = item.get("text", "")
                break
    data = json.loads(raw) if isinstance(raw, str) else raw
    items = data.get("tasks", data) if isinstance(data, dict) else data
    for item in items:
        t = item.get("task", item) if isinstance(item, dict) else {}
        tid = t.get("id", "")
        status = item.get("status", "")
        title = t.get("title", "")
        print(f"  {tid[:8]}  {status:25}  {title}")


asyncio.run(main())
