"""
Streamlit UI для brojs-agent.

Запуск: python -m streamlit run ui.py
"""
import asyncio
import json
import os
import queue
import threading
import time
from datetime import datetime

os.environ["NO_PROXY"] = (
    "openrouter.ai,platform.brojs.ru,git.brojs.ru,"
    + os.environ.get("NO_PROXY", "")
)

import streamlit as st
from dotenv import load_dotenv
from langchain_core.callbacks.base import BaseCallbackHandler
from langchain_core.messages import AIMessage, HumanMessage

load_dotenv()

# ---------------------------------------------------------------------------
# Конфигурация страницы
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="BroJS Agent",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="collapsed",
)

GITEA_OWNER = os.getenv("GITEA_OWNER", "glevelll")

# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

st.markdown("""
<style>
.agent-header {
    background: linear-gradient(135deg, #1a1a2e 0%, #0f3460 100%);
    border-radius: 12px; padding: 18px 24px; margin-bottom: 16px;
    border: 1px solid #16213e;
}
.agent-title { font-size: 1.6em; font-weight: bold; color: #e2e8f0; margin: 0; }
.agent-sub   { color: #64748b; font-size: .85em; margin-top: 4px; }

.tool-call {
    background: #0f172a; border-left: 3px solid #3b82f6;
    border-radius: 6px; padding: 6px 12px; margin: 3px 0;
    font-family: monospace; font-size: .82em; color: #93c5fd;
}
.tool-result {
    background: #052e16; border-left: 3px solid #22c55e;
    border-radius: 6px; padding: 6px 12px; margin: 3px 0;
    font-family: monospace; font-size: .78em; color: #86efac;
}
.tool-subagent {
    background: #1e1b4b; border-left: 3px solid #818cf8;
    border-radius: 6px; padding: 6px 12px; margin: 3px 0;
    font-family: monospace; font-size: .82em; color: #c4b5fd;
}
.thinking {
    color: #64748b; font-style: italic; font-size: .82em; padding: 4px 0;
}
.status-ok   { background:#052e16; border:1px solid #22c55e; color:#4ade80;
               padding:10px 16px; border-radius:8px; }
.status-warn { background:#1c1917; border:1px solid #f59e0b; color:#fbbf24;
               padding:10px 16px; border-radius:8px; }
.status-err  { background:#1c0a0a; border:1px solid #ef4444; color:#f87171;
               padding:10px 16px; border-radius:8px; }
.chat-user  { background:#1e3a5f; border-radius:12px 12px 2px 12px;
              padding:10px 14px; margin:6px 0; }
.chat-agent { background:#1a1a2e; border-radius:12px 12px 12px 2px;
              padding:10px 14px; margin:6px 0; }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Заголовок
# ---------------------------------------------------------------------------

st.markdown("""
<div class="agent-header">
  <div class="agent-title">🤖 BroJS Agent</div>
  <div class="agent-sub">Агентная система выполнения заданий · KFU-26-1 · platform.brojs.ru</div>
</div>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Кэш агента — загружается один раз при первом обращении
# ---------------------------------------------------------------------------

@st.cache_resource(show_spinner="⏳ Инициализация агента (подключение к MCP)...")
def get_agent():
    from src.agent.agent import homework_direct_agent
    return homework_direct_agent


@st.cache_resource(show_spinner="⏳ Загрузка pipeline...")
def get_pipeline():
    from src.agent.graph.pipeline import pipeline
    return pipeline

# ---------------------------------------------------------------------------
# Сборщик событий агента (синхронный — не нужна очередь)
# ---------------------------------------------------------------------------

_SOLVE_TOOLS    = {"validate_teacher_comment", "generate_code_solution"}
_JOURNAL_PREFIX = "mcp__journal-bh-professor__"


class AgentEventCollector(BaseCallbackHandler):
    """Накапливает события агента в список во время синхронного вызова."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def _ts(self) -> str:
        return datetime.now().strftime("%H:%M:%S")

    def _add(self, ev: dict) -> None:
        self.events.append(ev)

    def on_tool_start(self, serialized, input_str, **kwargs):
        name = serialized.get("name", "?")
        try:
            args = json.loads(str(input_str)) if isinstance(input_str, str) else input_str
        except Exception:
            args = {}
        self._add({"t": "tool_start", "ts": self._ts(), "name": name, "args": args})

    def on_tool_end(self, output, **kwargs):
        self._add({"t": "tool_end", "ts": self._ts(), "output": str(output)[:300]})

    def on_tool_error(self, error, **kwargs):
        self._add({"t": "tool_error", "ts": self._ts(), "msg": str(error)[:200]})

    def on_llm_start(self, *a, **kw):
        self._add({"t": "thinking", "ts": self._ts()})

    def on_llm_end(self, response, **kwargs):
        try:
            text = response.generations[0][0].text[:120]
            self._add({"t": "llm_end", "ts": self._ts(), "preview": text})
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Запуск агента — блокирующий вызов в отдельном потоке
# (избегаем конфликтов с event loop Streamlit)
# ---------------------------------------------------------------------------

def _invoke_agent(
    agent, messages: dict, config: dict
) -> tuple[dict | None, list[dict], str | None]:
    """
    Запускает агента синхронно. Блокирует поток до завершения.
    Возвращает (result, events, error_message).
    """
    from src.agent.middlewares.retry_on_rate_limit import set_ui_event_queue

    collector  = AgentEventCollector()
    # Очередь для rate-limit событий из middleware
    rl_queue: queue.Queue = queue.Queue()
    set_ui_event_queue(rl_queue)

    result: dict | None = None
    error:  str  | None = None

    def _thread_fn() -> None:
        nonlocal result, error

        async def _inner() -> None:
            nonlocal result, error
            try:
                r = await agent.ainvoke(messages, {**config, "callbacks": [collector]})
                result = r
            except Exception as e:
                error = str(e)

        asyncio.run(_inner())

    t = threading.Thread(target=_thread_fn, daemon=True)
    t.start()
    t.join()           # ждём завершения (UI показывает spinner)

    set_ui_event_queue(None)

    # Переносим rate-limit события в список collector'а
    while not rl_queue.empty():
        try:
            collector.events.append(rl_queue.get_nowait())
        except Exception:
            break

    return result, collector.events, error


# ---------------------------------------------------------------------------
# Рендер одного события в HTML
# ---------------------------------------------------------------------------

def _render_event(ev: dict) -> str:
    ts   = ev.get("ts", "")
    kind = ev.get("t", "")

    if kind == "thinking":
        return f'<div class="thinking">💭 {ts} модель думает...</div>'

    if kind == "tool_start":
        name  = ev["name"]
        args  = ev.get("args", {})
        short = name.replace(_JOURNAL_PREFIX, "mcp::")

        if name in _SOLVE_TOOLS:
            cls, icon, label = "tool-subagent", "🧠", f"[субагент] {short}"
        elif "gitea" in name:
            cls, icon, label = "tool-call", "📦", short
        elif "mcp::" in short or "journal" in name:
            cls, icon, label = "tool-call", "📡", short
        else:
            cls, icon, label = "tool-call", "🔧", short

        hint = ""
        for key in ("taskId", "path", "repo", "repo_name", "name"):
            if key in args:
                hint = f' <span style="opacity:.6">{args[key]}</span>'
                break

        return f'<div class="{cls}">{icon} {ts} {label}{hint}</div>'

    if kind == "tool_end":
        out = ev["output"].replace("<", "&lt;").replace(">", "&gt;")[:200]
        return f'<div class="tool-result">↳ {out}</div>'

    if kind == "tool_error":
        msg = ev["msg"].replace("<", "&lt;")
        return f'<div class="tool-result" style="border-color:#ef4444;color:#f87171">⚠ {msg}</div>'

    if kind == "rate_limit_wait":
        name, pause, attempt, mx = (
            ev.get("name", "?"), ev.get("pause", 30),
            ev.get("attempt", 1), ev.get("max", 5),
        )
        return (f'<div class="thinking" style="color:#f59e0b">'
                f'⏳ {ts} 429 rate limit — {name} · ждал {pause}с '
                f'(попытка {attempt}/{mx})</div>')

    if kind == "rate_limit_retry":
        name    = ev.get("name", "?")
        attempt = ev.get("attempt", 2)
        return (f'<div class="thinking" style="color:#86efac">'
                f'🔄 {ts} повтор {name} (попытка {attempt})...</div>')

    if kind == "llm_end":
        preview = ev.get("preview", "").replace("<", "&lt;")[:100]
        return f'<div class="thinking">✏ {ts} {preview}...</div>'

    return ""


def _show_events(events: list[dict], expanded: bool = False) -> None:
    if not events:
        return
    with st.expander(f"🔍 Лог инструментов ({len(events)} событий)", expanded=expanded):
        html = "".join(_render_event(e) for e in events[-80:])
        st.markdown(f'<div style="max-height:320px;overflow-y:auto">{html}</div>',
                    unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Вкладки
# ---------------------------------------------------------------------------

tab_chat, tab_pipeline, tab_status = st.tabs(
    ["💬 Чат с агентом", "⚡ Pipeline", "📊 Статус заданий"]
)


# ══════════════════════════════════════════════════════════════════════════
# ВК 1 — ЧАТ
# ══════════════════════════════════════════════════════════════════════════

with tab_chat:
    st.caption("Пиши агенту напрямую. Пока он работает — показывается spinner.")

    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []
    if "chat_events" not in st.session_state:
        st.session_state.chat_events = []
    if "chat_thread_id" not in st.session_state:
        st.session_state.chat_thread_id = f"ui-{int(time.time())}"

    # История сообщений
    for msg in st.session_state.chat_history:
        role, text = msg["role"], msg["text"]
        if role == "user":
            st.markdown(f'<div class="chat-user">👤 {text}</div>',
                        unsafe_allow_html=True)
        else:
            st.markdown(f'<div class="chat-agent">🤖 {text}</div>',
                        unsafe_allow_html=True)

    # Лог предыдущего запроса
    _show_events(st.session_state.chat_events)

    # Поле ввода
    col_input, col_btn = st.columns([5, 1])
    with col_input:
        user_input = st.text_input(
            "Сообщение",
            placeholder='"Реши задание 6a1864f7..." или "Какие задания у меня есть?"',
            label_visibility="collapsed",
            key="chat_input",
        )
    with col_btn:
        send = st.button("Отправить", use_container_width=True, type="primary")

    if send and user_input.strip():
        msg_text = user_input.strip()
        st.session_state.chat_history.append({"role": "user", "text": msg_text})
        st.session_state.chat_events = []

        lc_messages = [
            HumanMessage(content=m["text"]) if m["role"] == "user"
            else AIMessage(content=m["text"])
            for m in st.session_state.chat_history
        ]
        config = {"configurable": {"thread_id": st.session_state.chat_thread_id}}

        try:
            agent = get_agent()
        except Exception as e:
            st.error(f"Ошибка инициализации агента: {e}")
            st.stop()

        with st.spinner("🤖 Агент работает... (это может занять несколько минут)"):
            result, events, error = _invoke_agent(
                agent, {"messages": lc_messages}, config
            )

        st.session_state.chat_events = events

        if error:
            st.session_state.chat_history.append(
                {"role": "agent", "text": f"⚠️ Ошибка: {error}"}
            )
        elif result:
            msgs  = result.get("messages", [])
            last  = msgs[-1] if msgs else None
            reply = last.content if last and hasattr(last, "content") else "Готово."
            st.session_state.chat_history.append({"role": "agent", "text": reply})
        else:
            st.session_state.chat_history.append(
                {"role": "agent", "text": "Агент завершил работу без ответа."}
            )

        st.rerun()

    if st.session_state.chat_history:
        if st.button("🗑 Очистить чат"):
            st.session_state.chat_history   = []
            st.session_state.chat_events    = []
            st.session_state.chat_thread_id = f"ui-{int(time.time())}"
            st.rerun()


# ══════════════════════════════════════════════════════════════════════════
# ВК 2 — PIPELINE
# ══════════════════════════════════════════════════════════════════════════

with tab_pipeline:
    st.caption("Запусти конкретное задание по ID или все todo-задания сразу.")

    if "pipe_events" not in st.session_state:
        st.session_state.pipe_events = []
    if "pipe_result_text" not in st.session_state:
        st.session_state.pipe_result_text = ""

    col1, col2 = st.columns([3, 1])
    with col1:
        task_id_input = st.text_input(
            "Task ID",
            placeholder="6a1864f7fd30e81cf3146d65",
            label_visibility="visible",
        )
    with col2:
        st.write("")
        run_btn = st.button("▶ Запустить", type="primary", use_container_width=True)

    # Показываем результат предыдущего запуска
    if st.session_state.pipe_result_text:
        st.markdown(st.session_state.pipe_result_text, unsafe_allow_html=True)

    _show_events(st.session_state.pipe_events)

    if run_btn:
        if not task_id_input.strip():
            st.warning("Введи Task ID")
        else:
            task_id  = task_id_input.strip()
            repo_url = f"https://git.brojs.ru/{GITEA_OWNER}/task-{task_id}"
            prompt   = f"Реши задание taskId={task_id} курса 698b49da77cb6d4d2e43ce78"
            config   = {"configurable": {"thread_id": f"pipe-{task_id}-{int(time.time())}"}}
            messages = {"messages": [HumanMessage(content=prompt)]}

            st.session_state.pipe_events      = []
            st.session_state.pipe_result_text = ""

            try:
                agent = get_agent()
            except Exception as e:
                st.error(f"Ошибка инициализации агента: {e}")
                st.stop()

            with st.spinner(f"🤖 Решаю задание {task_id[:8]}..."):
                result, events, error = _invoke_agent(agent, messages, config)

            st.session_state.pipe_events = events

            if error:
                st.session_state.pipe_result_text = (
                    f'<div class="status-err">❌ Ошибка: {error[:400]}</div>'
                )
            else:
                st.session_state.pipe_result_text = (
                    f'<div class="status-ok">✅ Готово! '
                    f'<a href="{repo_url}" target="_blank" style="color:#4ade80">'
                    f'Открыть репозиторий ↗</a></div>'
                )

            st.rerun()

    st.divider()
    st.subheader("Запустить все todo-задания")
    if st.button("⚡ Запустить pipeline для всех заданий", use_container_width=True):
        pl = get_pipeline()
        with st.spinner("Pipeline работает... (может занять несколько минут)"):
            try:
                res = asyncio.run(pl.ainvoke(
                    {"tasks": [], "current_index": 0, "results": [], "errors": []}
                ))
                results = res.get("results", [])
                errors  = res.get("errors", [])

                md = [f"### Результат: {len(results)} заданий\n"]
                for r in results:
                    tid  = r.get("task_id", "")
                    url  = f"https://git.brojs.ru/{GITEA_OWNER}/task-{tid}"
                    icon = "✅" if r.get("status") == "ok" else "❌"
                    md.append(f"- {icon} `{tid[:8]}...` — [{r.get('status','')}]({url})")
                if errors:
                    md.append(f"\n**Ошибки ({len(errors)}):**")
                    for e in errors:
                        md.append(f"- {e}")
                st.markdown("\n".join(md))
            except Exception as e:
                st.error(str(e))


# ══════════════════════════════════════════════════════════════════════════
# ВК 3 — СТАТУС
# ══════════════════════════════════════════════════════════════════════════

with tab_status:
    st.caption("Статусы всех заданий курса KFU-26-1.")

    if st.button("🔄 Обновить статусы", type="primary"):
        with st.spinner("Загружаю статусы..."):
            try:
                from src.agent.mcp_client import load_journal_toolsets

                async def _fetch():
                    j     = load_journal_toolsets()
                    tools = {t.name: t for t in j.tasks_submissions_tools}
                    full_name = "mcp__journal-bh-professor__tasks_list"
                    t = tools.get(full_name) or next(
                        (v for k, v in tools.items() if "tasks_list" in k), None
                    )
                    if not t:
                        st.warning(f"tasks_list не найден. Доступны: {list(tools.keys())}")
                        return []
                    raw  = await t.ainvoke({"courseId": "698b49da77cb6d4d2e43ce78"})
                    text = (
                        next((x["text"] for x in raw if x.get("type") == "text"), str(raw))
                        if isinstance(raw, list) else str(raw)
                    )
                    data = json.loads(text)
                    return data.get("tasks", data) if isinstance(data, dict) else data

                items = asyncio.run(_fetch())
                st.session_state["task_statuses"] = items
            except Exception as e:
                st.error(str(e))
                items = []

    items = st.session_state.get("task_statuses", [])

    STATUS_EMOJI = {
        "done":             "✅",
        "ready_for_review": "🔍",
        "in_progress":      "🔄",
        "todo":             "📋",
        "rejected":         "❌",
    }

    if items:
        counts: dict[str, int] = {}
        rows = []
        for item in items:
            t      = item.get("task", item) if isinstance(item, dict) else {}
            tid    = t.get("id", "")
            status = item.get("status", "")
            title  = t.get("title", t.get("name", ""))
            counts[status] = counts.get(status, 0) + 1
            rows.append({
                "":         STATUS_EMOJI.get(status, "❓"),
                "Статус":   status,
                "ID":       tid[:12] + "...",
                "Название": title,
                "Репо":     f"https://git.brojs.ru/{GITEA_OWNER}/task-{tid}",
            })

        st.dataframe(rows, use_container_width=True, hide_index=True)

        st.divider()
        cols = st.columns(len(counts))
        for col, (s, n) in zip(cols, counts.items()):
            col.metric(f"{STATUS_EMOJI.get(s, '❓')} {s}", n)
    else:
        st.info("Нажми «Обновить статусы» чтобы загрузить данные.")
