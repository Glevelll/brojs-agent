"""
Инструмент solve_task — надёжное выполнение одного задания.

Агент-оркестратор вызывает его для каждого todo-задания.
Python внутри обеспечивает верификацию и страховочный сабмит,
но решение о том какие задания выполнять и в каком порядке
принимает LLM-оркестратор.
"""
from __future__ import annotations

import asyncio

from langchain.tools import tool
from langchain_core.messages import HumanMessage


@tool
async def solve_task(task_id: str) -> str:
    """Полностью выполнить одно задание курса: написать код, запушить в репо, сдать.

    Автоматически определяет режим:
    - Первая сдача: читает условие, генерирует код с нуля
    - Пересдача: читает замечания, исправляет или защищает решение

    После выполнения верифицирует репозиторий и гарантированно сдаёт задание.

    Args:
        task_id: ID задания из tasks_list (например 6a22c713fd30e81cf315ea04)

    Returns:
        Строка с результатом: "OK ..." или "ERROR ..."
    """
    # Ленивый импорт чтобы избежать circular import на уровне модуля
    from src.agent.agent import homework_direct_agent, rework_agent
    from src.agent.graph.pipeline import (
        MAX_RETRIES,
        _existing_repo_url,
        _fix_prompt,
        _force_submit,
        _invoke_with_retry,
        _is_submitted,
        _needs_retry,
        _task_json,
        _task_text,
        _verify_repo,
        TaskInfo,
    )

    # Небольшая пауза — снижает давление на rate limit
    await asyncio.sleep(5)

    # Определяем: первая сдача или пересдача
    repo_url  = await _existing_repo_url(task_id)
    is_rework = repo_url is not None

    if is_rework:
        data     = await _task_json(task_id)
        comments = data.get("comments") or data.get("feedback", "")
        prompt   = (
            f"Пересдача задания.\n\n"
            f"ID: {task_id}\n"
            f"Репозиторий: {repo_url}\n"
            f"Комментарии преподавателя: {comments}\n\n"
            "Внеси исправления и отправь снова."
        )
        agent_to_use = rework_agent
    else:
        task_text = await _task_text(task_id)
        prompt    = (
            f"Выполни задание.\n\n"
            f"ID: {task_id}\n\n"
            f"Текст задания:\n{task_text}\n\n"
            "Первая сдача. Напиши код с нуля."
        )
        agent_to_use = homework_direct_agent

    try:
        result = await _invoke_with_retry(
            agent_to_use,
            {"messages": [HumanMessage(content=prompt)]},
            {"configurable": {"thread_id": f"runner-{task_id}"}},
        )

        # Верификация репозитория (только первая сдача)
        repo_name = f"task-{task_id}"
        retries   = 0
        if not is_rework:
            task_info    = TaskInfo(id=task_id, title="", status="")
            verification = await _verify_repo(repo_name)
            while _needs_retry(verification) and retries < MAX_RETRIES:
                retries += 1
                fix_msg = _fix_prompt(task_info, repo_name, verification)
                result  = await _invoke_with_retry(
                    agent_to_use,
                    {"messages": [HumanMessage(content=fix_msg)]},
                    {"configurable": {"thread_id": f"runner-{task_id}-retry-{retries}"}},
                )
                verification = await _verify_repo(repo_name)

        # Гарантированный сабмит если агент не сдал сам
        if not await _is_submitted(task_id):
            await _force_submit(task_id)

        mode = "пересдача" if is_rework else "первая сдача"
        return f"OK: задание {task_id[:8]}... выполнено ({mode}, retries={retries})"

    except Exception as e:
        return f"ERROR: задание {task_id[:8]}...: {type(e).__name__}: {e}"
