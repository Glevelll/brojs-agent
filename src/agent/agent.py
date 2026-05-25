"""Создание агентов: главный оркестратор, исполнитель ДЗ, агент пересдачи."""
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, LocalShellBackend

from src.agent.constants import (
    AGENT_WORKSPACE_DIR,
    AGENTS_MD_VFS_PATH,
    BUNDLED_SKILLS_DIR,
    SKILLS_VFS_MOUNT,
    ensure_agents_md_file,
)
from src.agent.gitea_tools import GITEA_TOOLS
from src.agent.llm import llm
from src.agent.mcp_client import load_journal_toolsets
from src.agent.middlewares import SanitizeToolCallsMiddleware, ValidateJournalWorkflowMiddleware
from src.agent.prompts import (
    homework_doing_instructions,
    main_agent_instructions,
    rework_instructions,
)
from src.agent.subagents import subagent_specs_without_tools
from src.agent.tools import GIT_TOOLS, WEB_TOOLS

# ---------------------------------------------------------------------------
# Инициализация
# ---------------------------------------------------------------------------

ensure_agents_md_file()

journal = load_journal_toolsets()
_journal_tools = list(journal.courses_lessons_tools) + list(journal.tasks_submissions_tools)

print(f"=== Загружено: journal={len(_journal_tools)}, gitea={len(GITEA_TOOLS)}, git={len(GIT_TOOLS)} ===")

# ---------------------------------------------------------------------------
# Бэкенды (виртуальная файловая система агента)
# ---------------------------------------------------------------------------

_workspace_backend = LocalShellBackend(
    root_dir=str(AGENT_WORKSPACE_DIR),
    virtual_mode=True,
    inherit_env=True,
)
_skills_backend = FilesystemBackend(
    root_dir=str(BUNDLED_SKILLS_DIR),
    virtual_mode=True,
)
_composite_backend = CompositeBackend(
    default=_workspace_backend,
    routes={SKILLS_VFS_MOUNT: _skills_backend},
)

# ---------------------------------------------------------------------------
# Наборы инструментов
# ---------------------------------------------------------------------------

_homework_tools = [*GIT_TOOLS, *GITEA_TOOLS, *WEB_TOOLS, *_journal_tools]
_web_tools      = WEB_TOOLS

_subagent_tool_map = {
    "web_search":                  _web_tools,
    "homework_doing":              _homework_tools,
    "journal_bh_tasks_submissions": _journal_tools,
}

# Имена всех инструментов для SanitizeToolCallsMiddleware
_BUILTIN = {
    "write_todos", "ls", "read_file", "write_file", "edit_file",
    "glob", "grep", "execute", "task",
}
_gitea_names   = {t.name for t in GITEA_TOOLS}
_journal_names = {t.name for t in _journal_tools}
_git_names     = {t.name for t in GIT_TOOLS}
_web_names     = {t.name for t in WEB_TOOLS}

_main_tool_names = _BUILTIN | _gitea_names

_subagent_tool_names: dict[str, set[str]] = {
    "web_search":                  _BUILTIN | _web_names,
    "homework_doing":              _BUILTIN | _gitea_names | _journal_names | _git_names | _web_names,
    "journal_bh_tasks_submissions": _BUILTIN | _journal_names,
}

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

def _make_subagent_middleware(name: str) -> list:
    mw = [SanitizeToolCallsMiddleware(known_tools=_subagent_tool_names[name])]
    if name in ("homework_doing", "journal_bh_tasks_submissions"):
        mw.append(ValidateJournalWorkflowMiddleware())
    return mw


# ---------------------------------------------------------------------------
# Субагенты с инструментами и middleware
# ---------------------------------------------------------------------------

subagents = [
    {
        **spec,
        "tools":      _subagent_tool_map[spec["name"]],
        "middleware": _make_subagent_middleware(spec["name"]),
    }
    for spec in subagent_specs_without_tools
]

# ---------------------------------------------------------------------------
# Главный агент
# ---------------------------------------------------------------------------

agent = create_deep_agent(
    model=llm,
    tools=list(GITEA_TOOLS),
    system_prompt=main_agent_instructions,
    backend=_composite_backend,
    memory=[AGENTS_MD_VFS_PATH],
    subagents=subagents,
    middleware=[SanitizeToolCallsMiddleware(known_tools=_main_tool_names)],
)

# ---------------------------------------------------------------------------
# Агент прямого выполнения ДЗ (без субагентов, все инструменты сразу)
# ---------------------------------------------------------------------------

homework_direct_agent = create_deep_agent(
    model=llm,
    tools=_homework_tools,
    system_prompt=homework_doing_instructions,
    backend=_composite_backend,
    middleware=[
        SanitizeToolCallsMiddleware(known_tools=_subagent_tool_names["homework_doing"]),
        ValidateJournalWorkflowMiddleware(),
    ],
)

# ---------------------------------------------------------------------------
# Агент пересдачи
# ---------------------------------------------------------------------------

rework_agent = create_deep_agent(
    model=llm,
    tools=_homework_tools,
    system_prompt=rework_instructions,
    backend=_composite_backend,
    middleware=[
        SanitizeToolCallsMiddleware(known_tools=_subagent_tool_names["homework_doing"]),
        ValidateJournalWorkflowMiddleware(),
    ],
)
