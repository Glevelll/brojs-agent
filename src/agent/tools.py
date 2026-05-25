"""Git-инструменты и веб-поиск для агента."""
import os
import subprocess
from pathlib import Path

import httpx
from dotenv import load_dotenv
from langchain.tools import tool
from markdownify import markdownify

from src.agent.constants import AGENT_WORKSPACE_DIR, GITEA_BASE_URL

load_dotenv()

# ---------------------------------------------------------------------------
# Web tools
# ---------------------------------------------------------------------------

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
}

_HTTP_CLIENT = httpx.Client(timeout=30.0, follow_redirects=True, headers=_BROWSER_HEADERS)


@tool()
def get_page_content(url: str) -> str:
    """Получить текстовое содержимое веб-страницы по URL."""
    try:
        resp = _HTTP_CLIENT.get(url)
        if resp.status_code != 200:
            return f"Ошибка {resp.status_code} при загрузке {url}"
        return markdownify(resp.text)
    except Exception as e:
        return f"Не удалось загрузить страницу: {e}"


@tool()
def web_search(query: str, max_results: int = 5) -> str:
    """Поиск информации в интернете через Tavily.

    Args:
        query: поисковый запрос
        max_results: максимальное количество результатов
    """
    tavily_key = os.getenv("TAVILY_API_KEY", "")
    if not tavily_key:
        return (
            "Поиск недоступен: TAVILY_API_KEY не задан. "
            "Добавь ключ в .env для использования web_search."
        )
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=tavily_key)
        resp = client.search(query, max_results=max_results)
        parts = []
        for r in resp.get("results", []):
            parts.append(
                f"**{r.get('title', '')}**\n"
                f"url: {r.get('url', '')}\n"
                f"content: {r.get('content', '')}\n"
            )
        return "\n".join(parts) if parts else "Ничего не найдено"
    except Exception as e:
        return f"Ошибка поиска: {e}"


# ---------------------------------------------------------------------------
# Git tools (работают с agent_workspace)
# ---------------------------------------------------------------------------

def _cwd(subdir: str | None = None) -> str:
    if subdir:
        return str(AGENT_WORKSPACE_DIR / subdir)
    return str(AGENT_WORKSPACE_DIR)


def _inject_token(url: str) -> str:
    """Вставляет GITEA_TOKEN в URL для аутентификации при git push/pull."""
    token = os.getenv("GITEA_TOKEN", "")
    if token and GITEA_BASE_URL.replace("https://", "") in url and "@" not in url:
        return url.replace("https://", f"https://oauth2:{token}@")
    return url


@tool()
def git_clone(url: str, depth: int = 1) -> str:
    """Клонировать Git-репозиторий в agent_workspace.

    Args:
        url: URL репозитория (например https://git.brojs.ru/glevelll/task-abc)
        depth: глубина клонирования (по умолчанию 1 — только последний коммит)
    """
    auth_url = _inject_token(url)
    result = subprocess.run(
        ["git", "clone", "--depth", str(depth), auth_url],
        capture_output=True,
        text=True,
        cwd=str(AGENT_WORKSPACE_DIR),
    )
    if result.returncode != 0:
        return f"Ошибка клонирования: {result.stderr}"
    return f"Репозиторий клонирован в agent_workspace: {result.stdout or 'OK'}"


@tool()
def git_pull(path: str | None = None) -> str:
    """Обновить репозиторий (git pull).

    Args:
        path: подпапка в agent_workspace (например task-abc)
    """
    result = subprocess.run(
        ["git", "pull"],
        capture_output=True,
        text=True,
        cwd=_cwd(path),
    )
    if result.returncode != 0:
        return f"Ошибка pull: {result.stderr}"
    return result.stdout or "Уже актуально"


@tool()
def git_status(path: str | None = None) -> str:
    """Показать статус репозитория.

    Args:
        path: подпапка в agent_workspace
    """
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        cwd=_cwd(path),
    )
    return result.stdout or "Нет изменений"


@tool()
def git_add_and_commit(message: str, path: str | None = None) -> str:
    """Добавить все изменения и создать коммит.

    Args:
        message: сообщение коммита
        path: подпапка в agent_workspace
    """
    cwd = _cwd(path)
    add = subprocess.run(["git", "add", "."], capture_output=True, text=True, cwd=cwd)
    if add.returncode != 0:
        return f"Ошибка git add: {add.stderr}"

    commit = subprocess.run(
        ["git", "commit", "-m", message],
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    if commit.returncode != 0:
        return f"Ошибка git commit: {commit.stderr}"
    return commit.stdout


@tool()
def git_push(path: str | None = None) -> str:
    """Отправить коммиты в удалённый репозиторий (git push).

    Args:
        path: подпапка в agent_workspace
    """
    result = subprocess.run(
        ["git", "push"],
        capture_output=True,
        text=True,
        cwd=_cwd(path),
    )
    if result.returncode != 0:
        return f"Ошибка push: {result.stderr}"
    return result.stdout or "Push выполнен успешно"


# Удобный список всех git-инструментов
GIT_TOOLS = [git_clone, git_pull, git_status, git_add_and_commit, git_push]
WEB_TOOLS = [web_search, get_page_content]
