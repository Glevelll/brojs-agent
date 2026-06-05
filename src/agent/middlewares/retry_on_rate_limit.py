"""Middleware: повторяет вызов инструмента при 429 Rate Limit."""
from __future__ import annotations

import asyncio
import queue
import time
from typing import Any

from langchain.agents.middleware import AgentMiddleware, AgentState


_PAUSE = 30   # секунд ожидания при 429
_TRIES = 5    # максимум попыток

# Глобальный канал событий для UI (устанавливается из ui.py перед запуском агента).
# Если None — события просто не отправляются (CLI-режим).
_ui_event_queue: queue.Queue | None = None


def set_ui_event_queue(q: queue.Queue | None) -> None:
    """Вызывается из ui.py чтобы подключить очередь событий."""
    global _ui_event_queue
    _ui_event_queue = q


def _emit(event: dict) -> None:
    if _ui_event_queue is not None:
        try:
            _ui_event_queue.put_nowait(event)
        except Exception:
            pass


def _is_429(exc: Exception) -> bool:
    msg = str(exc)
    return "429" in msg or "rate" in msg.lower()


class RetryOnRateLimitMiddleware(AgentMiddleware[AgentState[Any], Any]):
    """Перехватывает 429 от любого инструмента и повторяет с паузой.
    Отправляет события rate_limit_wait / rate_limit_retry в UI-очередь.
    """

    def wrap_tool_call(self, request, handler):
        for attempt in range(1, _TRIES + 1):
            try:
                return handler(request)
            except Exception as e:
                if _is_429(e) and attempt < _TRIES:
                    name = request.tool_call.get("name", "")
                    print(f"[retry-mw] {name} → 429, жду {_PAUSE}с (попытка {attempt}/{_TRIES})...")
                    _emit({"t": "rate_limit_wait", "name": name,
                           "pause": _PAUSE, "attempt": attempt, "max": _TRIES,
                           "ts": _now()})
                    time.sleep(_PAUSE)
                    _emit({"t": "rate_limit_retry", "name": name,
                           "attempt": attempt + 1, "ts": _now()})
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
                    _emit({"t": "rate_limit_wait", "name": name,
                           "pause": _PAUSE, "attempt": attempt, "max": _TRIES,
                           "ts": _now()})
                    await asyncio.sleep(_PAUSE)
                    _emit({"t": "rate_limit_retry", "name": name,
                           "attempt": attempt + 1, "ts": _now()})
                else:
                    raise


def _now() -> str:
    from datetime import datetime
    return datetime.now().strftime("%H:%M:%S")
