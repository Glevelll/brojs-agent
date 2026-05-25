"""Инструменты для работы с Gitea (git.brojs.ru) через REST API."""
import base64
import json
import os

import httpx
from dotenv import load_dotenv
from langchain.tools import tool

from src.agent.constants import GITEA_BASE_URL, GITEA_OWNER

load_dotenv()

# ⚠️ ЗАГЛУШКА #3 — замени в .env: GITEA_TOKEN=ваш_токен_на_git.brojs.ru
_GITEA_TOKEN = os.getenv("GITEA_TOKEN", "YOUR_GITEA_TOKEN_HERE")


def _headers() -> dict:
    return {
        "Authorization": f"token {_GITEA_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _get(path: str, **params) -> dict | list:
    resp = httpx.get(
        f"{GITEA_BASE_URL}{path}",
        headers=_headers(),
        params=params,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _post(path: str, data: dict) -> dict:
    resp = httpx.post(
        f"{GITEA_BASE_URL}{path}",
        headers=_headers(),
        json=data,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _put(path: str, data: dict) -> dict:
    resp = httpx.put(
        f"{GITEA_BASE_URL}{path}",
        headers=_headers(),
        json=data,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


@tool()
def gitea_list_repos() -> str:
    """Получить список репозиториев текущего пользователя на git.brojs.ru.
    Также возвращает username — используй его как owner во всех операциях.
    """
    try:
        result = _get("/api/v1/repos/search", limit=50, token=_GITEA_TOKEN)
        repos = result.get("data", result) if isinstance(result, dict) else result
        names = [r.get("name", "") for r in repos if isinstance(r, dict)]
        return (
            f"username (owner): {GITEA_OWNER}\n"
            f"Репозитории ({len(names)}): {', '.join(names) or 'нет'}"
        )
    except Exception as e:
        return f"Ошибка получения репозиториев: {e}"


@tool()
def gitea_create_repo(name: str, private: bool = False, description: str = "") -> str:
    """Создать новый репозиторий на git.brojs.ru.

    Args:
        name: имя репозитория (например task-abc123)
        private: сделать приватным (по умолчанию False)
        description: описание репозитория
    """
    try:
        result = _post(
            "/api/v1/user/repos",
            {
                "name": name,
                "private": private,
                "description": description,
                "auto_init": True,
                "default_branch": "main",
            },
        )
        url = result.get("html_url", f"{GITEA_BASE_URL}/{GITEA_OWNER}/{name}")
        return f"Репозиторий создан: {url}"
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 409:
            return f"Репозиторий уже существует: {GITEA_BASE_URL}/{GITEA_OWNER}/{name}"
        return f"Ошибка создания репозитория: {e.response.text}"
    except Exception as e:
        return f"Ошибка: {e}"


@tool()
def gitea_write_file(
    repo: str,
    path: str,
    content: str,
    message: str,
    owner: str = GITEA_OWNER,
) -> str:
    """Создать или обновить файл в репозитории на git.brojs.ru.
    Автоматически коммитит изменения на сервере — git push НЕ нужен.

    Args:
        repo: имя репозитория (например task-abc123)
        path: путь к файлу (например main.py или src/agent.py)
        content: содержимое файла в виде plain text (НЕ base64)
        message: сообщение коммита
        owner: владелец репозитория (по умолчанию glevelll)
    """
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    endpoint = f"/api/v1/repos/{owner}/{repo}/contents/{path}"

    try:
        # Проверяем существование файла для получения sha
        existing = _get(endpoint)
        sha = existing.get("sha", "")
        result = _put(endpoint, {"message": message, "content": encoded, "sha": sha})
        action = "обновлён"
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            # Файл не существует — создаём
            try:
                result = _post(endpoint, {"message": message, "content": encoded})
                action = "создан"
            except Exception as create_err:
                return f"Ошибка создания файла {path}: {create_err}"
        else:
            return f"Ошибка записи файла {path}: {e.response.text}"
    except Exception as e:
        return f"Ошибка: {e}"

    commit_sha = (result.get("commit") or {}).get("sha", "")[:8]
    return f"Файл {path} {action} в {owner}/{repo} (commit: {commit_sha})\nURL: {GITEA_BASE_URL}/{owner}/{repo}/src/branch/main/{path}"


@tool()
def gitea_get_file(repo: str, path: str, owner: str = GITEA_OWNER) -> str:
    """Получить содержимое файла из репозитория на git.brojs.ru.

    Args:
        repo: имя репозитория
        path: путь к файлу
        owner: владелец репозитория (по умолчанию glevelll)

    Returns:
        JSON-строка с полями content (текст), sha, path
    """
    try:
        result = _get(f"/api/v1/repos/{owner}/{repo}/contents/{path}")
        raw = result.get("content", "")
        # Gitea возвращает base64 с переносами строк
        content = base64.b64decode(raw.replace("\n", "")).decode("utf-8")
        sha = result.get("sha", "")
        return json.dumps({"content": content, "sha": sha, "path": path}, ensure_ascii=False)
    except httpx.HTTPStatusError as e:
        return f"Ошибка получения файла {path}: {e.response.text}"
    except Exception as e:
        return f"Ошибка: {e}"


# Список всех gitea-инструментов для удобного импорта
GITEA_TOOLS = [
    gitea_list_repos,
    gitea_create_repo,
    gitea_write_file,
    gitea_get_file,
]
