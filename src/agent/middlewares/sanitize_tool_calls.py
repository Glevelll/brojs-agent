"""Middleware: блокирует вызовы несуществующих инструментов."""
from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import ToolMessage


class SanitizeToolCallsMiddleware(AgentMiddleware[AgentState[Any], Any]):
    """Перехватывает вызовы инструментов с неизвестными именами и возвращает
    понятное сообщение об ошибке вместо падения рантайма."""

    def __init__(self, known_tools: set[str]):
        self.known_tools = known_tools

    def _reject(self, request) -> ToolMessage:
        name = request.tool_call.get("name", "")
        available = sorted(self.known_tools)
        return ToolMessage(
            content=(
                f"Инструмент '{name}' не существует. "
                f"Доступные инструменты: {available}. "
                "Исправь имя инструмента и попробуй снова."
            ),
            tool_call_id=request.tool_call["id"],
            name=name,
        )

    def wrap_tool_call(self, request, handler):
        name = request.tool_call.get("name", "")
        if name not in self.known_tools:
            return self._reject(request)
        return handler(request)

    async def awrap_tool_call(self, request, handler):
        name = request.tool_call.get("name", "")
        if name not in self.known_tools:
            return self._reject(request)
        return await handler(request)
