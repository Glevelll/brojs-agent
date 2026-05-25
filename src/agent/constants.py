"""Константы проекта: пути, ID курса, Gitea-настройки."""
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent

AGENT_WORKSPACE_DIR = PACKAGE_DIR / "agent_workspace"
BUNDLED_SKILLS_DIR  = PACKAGE_DIR / "skills"

AGENTS_MD_FILENAME = "AGENTS.md"
AGENTS_MD_VFS_PATH = "/AGENTS.md"
SKILLS_VFS_MOUNT   = "/skills/"

# ID курса KFU-26-1 на platform.brojs.ru
COURSE_ID = "698b49da77cb6d4d2e43ce78"

# Gitea
GITEA_OWNER    = "glevelll"
GITEA_BASE_URL = "https://git.brojs.ru"

AGENTS_MD_SEED = """\
# Память агента

Здесь хранятся заметки, которые стоит помнить между сообщениями
(предпочтения, договорённости, факты о задании).
Обновляй этот файл через `edit_file`, когда нужно что-то зафиксировать.
"""


def ensure_agents_md_file() -> None:
    """Создаёт workspace и начальный AGENTS.md, если файла ещё нет."""
    AGENT_WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    host_path = AGENT_WORKSPACE_DIR / AGENTS_MD_FILENAME
    if not host_path.exists():
        host_path.write_text(AGENTS_MD_SEED, encoding="utf-8")
