import asyncio, json, os
os.environ["NO_PROXY"] = "openrouter.ai,platform.brojs.ru,git.brojs.ru," + os.environ.get("NO_PROXY", "")
from dotenv import load_dotenv
load_dotenv()
from src.agent.mcp_client import load_journal_toolsets, JOURNAL_PREFIX
from src.agent.constants import COURSE_ID

async def main():
    j = load_journal_toolsets()
    tool = next(t for t in j.tasks_submissions_tools if t.name == f"{JOURNAL_PREFIX}tasks_list")
    raw = await tool.ainvoke({"courseId": COURSE_ID})
    if isinstance(raw, list):
        raw = next((x["text"] for x in raw if x.get("type") == "text"), str(raw))
    data = json.loads(raw) if isinstance(raw, str) else raw
    items = data.get("tasks", data) if isinstance(data, dict) else data
    for item in items:
        t = item.get("task", item) if isinstance(item, dict) else {}
        tid = t.get("id", "")
        status = item.get("status", "")
        title = t.get("title", "")
        if status == "todo":
            print(f"TODO  {tid}  {title}")

asyncio.run(main())
