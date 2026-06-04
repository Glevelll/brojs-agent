"""Middleware: повторяет вызов инструмента при 429 Rate Limit."""
from __future__ import annotations

import asyncio
from typing import Any

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import ToolMessage


_PAUSE = 30   # секунд ожидания при 429
_TRIES = 5    # максимум попыток


def _is_429(exc: Exception) -> bool:
    msg = str(exc)
    return "429" in msg or "rate" in msg.lower()


class RetryOnRateLimitMiddleware(AgentMiddleware[AgentState[Any], Any]):
    """Перехватывает 429 от любого инструмента и повторяет с паузой."""

    def wrap_tool_call(self, request, handler):
        for attempt in range(1, _TRIES + 1):
            try:
                return handler(request)
            except Exception as e:
                if _is_429(e) and attempt < _TRIES:
                    name = request.tool_call.get("name", "")
                    print(f"[retry-mw] {name} → 429, жду {_PAUSE}с (попытка {attempt}/{_TRIES})...")
                    import time
                    time.sleep(_PAUSE)
                else:
                    raise

    async def awrap_tool_call(self, request, handler):
        for attempt in range(1, _TRIES + 1):
            try:
                return await handler(request)
            except Exception as e:
                if _is_429(e) and attempt < _TRIES:
                    name = request.tool_call.get("name", "")
                    print(f"[retry-mw] {name} → 429, жду {_PAUSE}с (попытка {attempt}/{_TRIES})...")
                    await asyncio.sleep(_PAUSE)
                else:
                    raise
