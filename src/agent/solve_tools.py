"""
Инструменты агента для решения задач.

Агент вызывает эти инструменты САМОСТОЯТЕЛЬНО — Python не управляет порядком.
Каждый инструмент — специализированный LLM-субагент со своим промптом.

Субагенты:
  validate_teacher_comment  — per-claim валидация замечания преподавателя
  generate_code_solution    — генерация кода (первая сдача или пересдача)
"""
import asyncio
import base64
import json
import os

import httpx
from langchain.tools import tool

from src.agent.constants import GITEA_BASE_URL, GITEA_OWNER
from src.agent.llm import llm
from src.agent.solve_prompts import (
    ANALYZE_PROMPT,
    CODE_PROMPT,
    REWORK_SECTION,
    VALIDATE_PROMPT,
)

_GITEA_TOKEN = os.getenv("GITEA_TOKEN", "")
_CODE_EXTS   = (".py", ".js", ".ts", ".sh", ".sql", ".md")
_BACKOFF     = [30, 60, 120]


# ---------------------------------------------------------------------------
# Вспомогательные функции (не инструменты)
# ---------------------------------------------------------------------------

def _gh() -> dict:
    return {"Authorization": f"token {_GITEA_TOKEN}", "Content-Type": "application/json"}


def _read_repo_files(repo: str) -> dict[str, str]:
    """Читает все кодовые файлы из корня репозитория на Gitea."""
    files: dict[str, str] = {}
    url_root = f"{GITEA_BASE_URL}/api/v1/repos/{GITEA_OWNER}/{repo}/contents"
    try:
        with httpx.Client(timeout=30) as c:
            r = c.get(url_root, headers=_gh())
            if r.status_code != 200:
                return files
            for item in r.json():
                if item.get("type") != "file":
                    continue
                if not any(item["name"].endswith(ext) for ext in _CODE_EXTS):
                    continue
                fr = c.get(
                    f"{GITEA_BASE_URL}/api/v1/repos/{GITEA_OWNER}/{repo}/contents/{item['name']}",
                    headers=_gh(),
                )
                if fr.status_code == 200:
                    raw = fr.json().get("content", "")
                    files[item["name"]] = base64.b64decode(raw.replace("\n", "")).decode(
                        "utf-8", errors="replace"
                    )
    except Exception:
        pass
    return files


def _parse_llm_json(raw: str) -> dict:
    """Убирает markdown-обёртку и парсит JSON из ответа LLM."""
    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    return json.loads(text)


async def _llm_call_with_retry(prompt: str, max_attempts: int = 4) -> str:
    """Вызов LLM с повтором при 429."""
    for attempt in range(1, max_attempts + 1):
        try:
            resp = await llm.ainvoke(prompt)
            return resp.content
        except Exception as e:
            if "429" in str(e) and attempt < max_attempts:
                wait = _BACKOFF[min(attempt - 1, len(_BACKOFF) - 1)]
                print(f"  [solve_tools] 429, жду {wait}с (попытка {attempt})...")
                await asyncio.sleep(wait)
            else:
                raise


# ---------------------------------------------------------------------------
# Субагент 1: Валидатор замечаний преподавателя
# ---------------------------------------------------------------------------

@tool
async def validate_teacher_comment(
    task_text: str,
    repo_name: str,
    teacher_comment: str,
) -> str:
    """[СУБАГЕНТ-ВАЛИДАТОР] Анализирует каждый пункт замечания преподавателя НЕЗАВИСИМО.

    Читает текущий код из Gitea репозитория и сверяет каждое утверждение
    с условием задания. Отличает ловушки от реальных ошибок.

    Args:
        task_text:        полный текст условия задания
        repo_name:        имя репозитория (например task-6a1864f7fd30e81cf3...)
        teacher_comment:  замечание преподавателя

    Returns:
        JSON: {
          "has_trap":          true если есть ложные пункты,
          "has_valid":         true если есть реальные ошибки,
          "claims":            список {claim, valid, explanation},
          "trap_explanations": объяснения ложных пунктов (для README),
          "fix_instructions":  что конкретно исправить (для реальных ошибок)
        }
    """
    print(f"  [ВАЛИДАТОР] Проверяю замечание для {repo_name}...")

    # Читаем код из Gitea
    code_files = _read_repo_files(repo_name)
    if code_files:
        code_block = "\n\n".join(
            f"### {fn}\n```\n{content[:2000]}\n```"
            for fn, content in code_files.items()
        )
        print(f"  [ВАЛИДАТОР] Прочитано файлов: {', '.join(code_files.keys())}")
    else:
        code_block = "(репозиторий пуст или файлы не найдены)"
        print(f"  [ВАЛИДАТОР] ⚠️  Файлы в {repo_name} не найдены")

    prompt = VALIDATE_PROMPT.format(
        task_text=task_text,
        code_block=code_block,
        comment=teacher_comment,
    )

    try:
        raw = await _llm_call_with_retry(prompt)
        result = _parse_llm_json(raw)
        result.setdefault("has_trap", False)
        result.setdefault("has_valid", True)
        result.setdefault("claims", [])
        result.setdefault("trap_explanations", [])
        result.setdefault("fix_instructions", [])

        # Логируем результат
        for cl in result["claims"]:
            tag = "❌ ЛОВУШКА" if not cl.get("valid") else "✓ обоснованно"
            print(f"    {tag}: {cl.get('claim', '')[:70]}")
        print(f"  [ВАЛИДАТОР] has_trap={result['has_trap']}, has_valid={result['has_valid']}")

        return json.dumps(result, ensure_ascii=False)

    except Exception as e:
        print(f"  [ВАЛИДАТОР] Ошибка: {e} — считаем замечание обоснованным")
        fallback = {
            "has_trap": False,
            "has_valid": True,
            "claims": [],
            "trap_explanations": [],
            "fix_instructions": [teacher_comment],
        }
        return json.dumps(fallback, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Субагент 2: Кодер — генерирует решение
# ---------------------------------------------------------------------------

@tool
async def generate_code_solution(
    task_text: str,
    fix_instructions: str = "",
    defense_context: str = "",
) -> str:
    """[СУБАГЕНТ-КОДЕР] Генерирует полное решение задания.

    При пересдаче принимает что исправить и что отстоять с DESIGN DECISION аргументами.
    Запрещено использовать Ollama — только OpenRouter через langchain_openai.

    Args:
        task_text:        полный текст условия задания
        fix_instructions: что конкретно исправить (для пересдачи, иначе "")
        defense_context:  что отстоять с DESIGN DECISION комментариями (иначе "")

    Returns:
        JSON: {
          "main_py":          содержимое main.py,
          "requirements_txt": содержимое requirements.txt,
          "extra_files":      доп. файлы {имя: содержимое} или {}
        }
    """
    is_rework = bool(fix_instructions or defense_context)
    mode = "ПЕРЕСДАЧА" if is_rework else "первая сдача"
    print(f"  [КОДЕР] Генерирую решение ({mode})...")

    if is_rework:
        # Строим секцию пересдачи
        fixes    = [fix_instructions]    if fix_instructions    else []
        defenses = [defense_context]     if defense_context     else []
        rework_section = REWORK_SECTION.format(
            fixes    = "\n".join(f"- {f}" for f in fixes)    or "— нет",
            defenses = "\n".join(f"- {d}" for d in defenses) or "— нет",
        )
    else:
        rework_section = ""

    prompt = CODE_PROMPT.format(task_text=task_text, rework_section=rework_section)

    for attempt in range(1, 6):
        try:
            print(f"  [КОДЕР] LLM вызов (попытка {attempt})...")
            raw = await _llm_call_with_retry(prompt, max_attempts=3)
            result = _parse_llm_json(raw)
            if "main_py" in result:
                main_size = len(result.get("main_py", ""))
                req_size  = len(result.get("requirements_txt", ""))
                extra     = list(result.get("extra_files", {}).keys())
                print(f"  [КОДЕР] ✅ main.py={main_size}с, requirements.txt={req_size}с"
                      + (f", extra={extra}" if extra else ""))
                return json.dumps(result, ensure_ascii=False)
        except json.JSONDecodeError:
            print(f"  [КОДЕР] JSON parse error на попытке {attempt}, повтор...")
            if attempt == 5:
                raise
        except Exception as e:
            if attempt < 5:
                wait = _BACKOFF[min(attempt - 1, len(_BACKOFF) - 1)]
                print(f"  [КОДЕР] Ошибка: {e}, жду {wait}с...")
                await asyncio.sleep(wait)
            else:
                raise

    raise RuntimeError("Не удалось сгенерировать код после 5 попыток")


# ---------------------------------------------------------------------------
# Список инструментов для импорта в agent.py
# ---------------------------------------------------------------------------

SOLVE_TOOLS = [validate_teacher_comment, generate_code_solution]
