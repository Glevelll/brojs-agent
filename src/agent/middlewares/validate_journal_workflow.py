"""Middleware: валидация порядка вызовов journal-инструментов.

Проблема: модель иногда вызывает task_submit без предварительного
task_update_answer, что приводит к ошибке 'answer is empty'.
Этот middleware перехватывает task_submit и проверяет историю сообщений.
"""
from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import ToolMessage
from langchain_core.tools.base import ToolException

_NO_UPDATE_MSG = (
    "Нельзя вызывать task_submit без предварительного task_update_answer.\n\n"
    "Правильный порядок:\n"
    '1. task_update_answer(taskId="TASK_ID", answerType="link", content="<URL репозитория>")\n'
    '2. task_submit(taskId="TASK_ID", confirmSubmit=true)'
)

_SUBMIT_ERR_MSG = (
    "task_submit завершился ошибкой: {error}\n\n"
    "Убедись что task_update_answer был вызван с правильными параметрами "
    '(answerType="link", content="<URL>"), затем повтори task_submit.'
)

_JOURNAL_ERR_TEMPLATE = "Ошибка journal-инструмента ({tool}): {error}"


def _find_prior_update_answer(messages: list, task_id: str) -> bool:
    """Проверяет, был ли task_update_answer вызван для данного taskId."""
    for msg in reversed(messages):
        if not hasattr(msg, "tool_calls") or not msg.tool_calls:
            continue
        for tc in msg.tool_calls:
            name = tc.get("name", "")
            args = tc.get("args", {})
            if "task_update_answer" in name and args.get("taskId") == task_id:
                return True
    return False


class ValidateJournalWorkflowMiddleware(AgentMiddleware[AgentState[Any], Any]):
    """Предотвращает task_submit без предварительного task_update_answer."""

    # ---- sync ----

    def _safe_submit(self, request, handler) -> ToolMessage:
        task_id = request.tool_call.get("args", {}).get("taskId", "")
        tool_name = request.tool_call.get("name", "")
        messages = request.state.get("messages", [])

        if not _find_prior_update_answer(messages, task_id):
            return ToolMessage(
                content=_NO_UPDATE_MSG.replace("TASK_ID", task_id),
                tool_call_id=request.tool_call["id"],
                name=tool_name,
            )
        try:
            return handler(request)
        except ToolException as e:
            return ToolMessage(
                content=_SUBMIT_ERR_MSG.format(error=e),
                tool_call_id=request.tool_call["id"],
                name=tool_name,
            )

    def _safe_journal_call(self, request, handler):
        try:
            return handler(request)
        except ToolException as e:
            tool_name = request.tool_call.get("name", "")
            return ToolMessage(
                content=_JOURNAL_ERR_TEMPLATE.format(tool=tool_name, error=e),
                tool_call_id=request.tool_call["id"],
                name=tool_name,
            )

    def wrap_tool_call(self, request, handler):
        name = request.tool_call.get("name", "")
        if "task_submit" in name:
            return self._safe_submit(request, handler)
        if "mcp__journal-bh-professor__" in name:
            return self._safe_journal_call(request, handler)
        return handler(request)

    # ---- async ----

    async def _async_safe_submit(self, request, handler) -> ToolMessage:
        task_id = request.tool_call.get("args", {}).get("taskId", "")
        tool_name = request.tool_call.get("name", "")
        messages = request.state.get("messages", [])

        if not _find_prior_update_answer(messages, task_id):
            return ToolMessage(
                content=_NO_UPDATE_MSG.replace("TASK_ID", task_id),
                tool_call_id=request.tool_call["id"],
                name=tool_name,
            )
        try:
            return await handler(request)
        except ToolException as e:
            return ToolMessage(
                content=_SUBMIT_ERR_MSG.format(error=e),
                tool_call_id=request.tool_call["id"],
                name=tool_name,
            )

    async def _async_safe_journal_call(self, request, handler):
        try:
            return await handler(request)
        except ToolException as e:
            tool_name = request.tool_call.get("name", "")
            return ToolMessage(
                content=_JOURNAL_ERR_TEMPLATE.format(tool=tool_name, error=e),
                tool_call_id=request.tool_call["id"],
                name=tool_name,
            )

    async def awrap_tool_call(self, request, handler):
        name = request.tool_call.get("name", "")
        if "task_submit" in name:
            return await self._async_safe_submit(request, handler)
        if "mcp__journal-bh-professor__" in name:
            return await self._async_safe_journal_call(request, handler)
        return await handler(request)
