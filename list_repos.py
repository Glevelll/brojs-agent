from dotenv import load_dotenv
load_dotenv()
from src.agent.gitea_tools import _get
from src.agent.constants import GITEA_OWNER

result = _get("/api/v1/repos/search", limit=50, token="")
repos = result.get("data", result) if isinstance(result, dict) else result
for r in sorted(repos, key=lambda x: x.get("updated", ""), reverse=True):
    print(f"  {r.get('name',''):40}  {r.get('updated','')[:10]}")
