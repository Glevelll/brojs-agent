"""
Streamlit UI для brojs-agent.

Запуск: streamlit run ui.py
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
.status-ok   { background:#052e16; border:1px solid #22c55e; color:#4ade80; padding:10px 16px; border-radius:8px; }
.status-warn { background:#1c1917; border:1px solid #f59e0b; color:#fbbf24; padding:10px 16px; border-radius:8px; }
.status-err  { background:#1c0a0a; border:1px solid #ef4444; color:#f87171; padding:10px 16px; border-radius:8px; }

.chat-user { background:#1e3a5f; border-radius:12px 12px 2px 12px; padding:10px 14px; margin:6px 0; }
.chat-agent { background:#1a1a2e; border-radius:12px 12px 12px 2px; padding:10px 14px; margin:6px 0; }
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
# Кэш агента
# ---------------------------------------------------------------------------

@st.cache_resource(show_spinner="Инициализация агента (~30с)...")
def get_agent():
    from src.agent.agent import homework_direct_agent
    return homework_direct_agent


@st.cache_resource(show_spinner="Загрузка pipeline...")
def get_pipeline():
    from src.agent.graph.pipeline import pipeline
    return pipeline

# ---------------------------------------------------------------------------
# Callback — перехватывает события агента и шлёт в очередь
# ---------------------------------------------------------------------------

_SOLVE_TOOLS = {"validate_teacher_comment", "generate_code_solution"}
_JOURNAL_PREFIX = "mcp__journal-bh-professor__"


class AgentCallback(BaseCallbackHandler):
    def __init__(self, q: queue.Queue):
        self.q = q

    def _ts(self) -> str:
        return datetime.now().strftime("%H:%M:%S")

    def on_tool_start(self, serialized, input_str, **kwargs):
        name = serialized.get("name", "?")
        try:
            args = json.loads(str(input_str)) if isinstance(input_str, str) else input_str
        except Exception:
            args = {}
        self.q.put({"t": "tool_start", "ts": self._ts(), "name": name, "args": args})

    def on_tool_end(self, output, **kwargs):
        self.q.put({"t": "tool_end", "ts": self._ts(), "output": str(output)[:300]})

    def on_tool_error(self, error, **kwargs):
        self.q.put({"t": "tool_error", "ts": self._ts(), "msg": str(error)[:200]})

    def on_llm_start(self, *a, **kw):
        self.q.put({"t": "thinking", "ts": self._ts()})

    def on_llm_end(self, response, **kwargs):
        try:
            text = response.generations[0][0].text[:120]
            self.q.put({"t": "llm_end", "ts": self._ts(), "preview": text})
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Запуск агента в фоне
# ---------------------------------------------------------------------------

def _run_agent_thread(
    agent, messages, config, q: queue.Queue, cb: AgentCallback,
    stop_flag: threading.Event,
):
    from src.agent.middlewares.retry_on_rate_limit import set_ui_event_queue
    set_ui_event_queue(q)
    async def _inner():
        try:
            result = await agent.ainvoke(messages, {**config, "callbacks": [cb]})
            q.put({"t": "done", "result": result})
        except Exception as e:
            q.put({"t": "fatal", "msg": str(e)})
        finally:
            set_ui_event_queue(None)
    if not stop_flag.is_set():
        asyncio.run(_inner())


# ---------------------------------------------------------------------------
# Рендер одного события в лог
# ---------------------------------------------------------------------------

def _render_event(ev: dict) -> str:
    ts   = ev.get("ts", "")
    kind = ev.get("t", "")

    if kind == "thinking":
        return f'<div class="thinking">💭 {ts} модель думает...</div>'

    if kind == "tool_start":
        name = ev["name"]
        args = ev.get("args", {})
        short = name.replace(_JOURNAL_PREFIX, "mcp::")
        # Определяем тип инструмента
        if name in _SOLVE_TOOLS:
            cls  = "tool-subagent"
            icon = "🧠"
            label = f"[субагент] {short}"
        elif "gitea" in name:
            cls  = "tool-call"
            icon = "📦"
            label = short
        elif "mcp::" in short or "journal" in name:
            cls  = "tool-call"
            icon = "📡"
            label = short
        else:
            cls  = "tool-call"
            icon = "🔧"
            label = short

        # Показываем ключевые аргументы
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
        name    = ev.get("name", "?")
        pause   = ev.get("pause", 30)
        attempt = ev.get("attempt", 1)
        mx      = ev.get("max", 5)
        return (f'<div class="thinking" style="color:#f59e0b">'
                f'⏳ {ts} 429 rate limit — {name} · жду {pause}с '
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


# ---------------------------------------------------------------------------
# Вкладки
# ---------------------------------------------------------------------------

tab_chat, tab_pipeline, tab_status = st.tabs(["💬 Чат с агентом", "⚡ Pipeline", "📊 Статус заданий"])


# ══════════════════════════════════════════════════════════════════════════
# ВК 1 — ЧАТ
# ══════════════════════════════════════════════════════════════════════════

with tab_chat:
    st.caption("Общайся с агентом: задай вопрос, попроси решить задание или разобрать ситуацию.")

    # Инициализация session state
    for key, default in [
        ("chat_history",   []),
        ("chat_events",    []),
        ("chat_thread_id", f"ui-{int(time.time())}"),
        ("chat_running",   False),
        ("_chat_last_event_t", time.time()),
    ]:
        if key not in st.session_state:
            st.session_state[key] = default

    # Показываем историю
    for msg in st.session_state.chat_history:
        role, text = msg["role"], msg["text"]
        if role == "user":
            st.markdown(f'<div class="chat-user">👤 {text}</div>', unsafe_allow_html=True)
        else:
            st.markdown(f'<div class="chat-agent">🤖 {text}</div>', unsafe_allow_html=True)

    # ── АГЕНТ РАБОТАЕТ ──────────────────────────────────────────────────────
    if st.session_state.chat_running:
        thread: threading.Thread = st.session_state.get("_chat_thread")
        evq: queue.Queue         = st.session_state.get("_chat_queue")

        # Считываем новые события из очереди
        new_events: list[dict] = []
        final_result = None
        fatal        = None

        if evq:
            while not evq.empty():
                ev = evq.get_nowait()
                if ev["t"] == "done":
                    final_result = ev["result"]
                elif ev["t"] == "fatal":
                    fatal = ev["msg"]
                else:
                    new_events.append(ev)

        if new_events:
            st.session_state.chat_events.extend(new_events)
            st.session_state["_chat_last_event_t"] = time.time()

        # Агент завершил работу?
        agent_done = (final_result is not None or fatal is not None
                      or (thread is not None and not thread.is_alive() and
                          (evq is None or evq.empty())))

        if agent_done:
            st.session_state.chat_running = False
            if fatal:
                st.session_state.chat_history.append(
                    {"role": "agent", "text": f"⚠️ Ошибка: {fatal}"})
            elif final_result:
                msgs  = final_result.get("messages", [])
                last  = msgs[-1] if msgs else None
                reply = last.content if last and hasattr(last, "content") else "Готово."
                st.session_state.chat_history.append({"role": "agent", "text": reply})
            st.rerun()

        # Показываем накопленные события
        if st.session_state.chat_events:
            html = "".join(_render_event(e) for e in st.session_state.chat_events[-80:])
            st.markdown(
                f'<div style="background:#0b0f1a;border-radius:8px;padding:10px;'
                f'max-height:260px;overflow-y:auto">{html}</div>',
                unsafe_allow_html=True,
            )

        # Статус
        idle = time.time() - st.session_state["_chat_last_event_t"]
        if idle > 15:
            st.markdown(
                f'<div class="thinking" style="color:#f59e0b">'
                f'⏳ Агент работает... ({int(idle)}с без событий — возможно rate limit)</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown('<div class="thinking">💭 Агент думает...</div>',
                        unsafe_allow_html=True)

        # Кнопка Стоп — работает, потому что НЕ внутри while-цикла
        if st.button("🛑 Остановить", key="chat_stop_btn"):
            stop_flag = st.session_state.get("_chat_stop_flag")
            if stop_flag:
                stop_flag.set()
            st.session_state.chat_running = False
            st.session_state.chat_history.append(
                {"role": "agent", "text": "Остановлено пользователем."})
            st.rerun()

        # Следующая итерация опроса через 0.5с
        time.sleep(0.5)
        st.rerun()

    # ── ВВОД (агент не работает) ─────────────────────────────────────────────
    else:
        # Лог прошлого запроса
        if st.session_state.chat_events:
            with st.expander(
                f"🔍 Лог инструментов ({len(st.session_state.chat_events)} событий)",
                expanded=False,
            ):
                html = "".join(_render_event(e) for e in st.session_state.chat_events[-80:])
                st.markdown(f'<div style="max-height:300px;overflow-y:auto">{html}</div>',
                            unsafe_allow_html=True)

        col_input, col_btn = st.columns([5, 1])
        with col_input:
            user_input = st.text_input(
                "Сообщение",
                placeholder='Например: "Реши задание 6a1864f7..." или "Какие задания у меня есть?"',
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
            config    = {"configurable": {"thread_id": st.session_state.chat_thread_id}}
            agent     = get_agent()
            evq       = queue.Queue()
            stop_flag = threading.Event()
            cb        = AgentCallback(evq)

            thread = threading.Thread(
                target=_run_agent_thread,
                args=(agent, {"messages": lc_messages}, config, evq, cb, stop_flag),
                daemon=True,
            )

            # Сохраняем в session_state — переживут rerun
            st.session_state["_chat_thread"]     = thread
            st.session_state["_chat_queue"]      = evq
            st.session_state["_chat_stop_flag"]  = stop_flag
            st.session_state["_chat_last_event_t"] = time.time()
            st.session_state.chat_running        = True

            thread.start()
            st.rerun()  # → переходим в ветку "АГЕНТ РАБОТАЕТ"

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
    st.caption("Автоматически решает все todo-задания курса по очереди.")

    col1, col2 = st.columns([3, 1])
    with col1:
        task_id_input = st.text_input(
            "Task ID (оставь пустым — решить все todo)",
            placeholder="6a1864f7fd30e81cf3146d65",
            label_visibility="visible",
        )
    with col2:
        st.write("")
        run_btn = st.button("▶ Запустить", type="primary", use_container_width=True)

    # Инициализация pipeline session state
    for key, default in [
        ("pipe_running",       False),
        ("pipe_events",        []),
        ("_pipe_last_event_t", time.time()),
        ("_pipe_task_id",      ""),
        ("_pipe_result",       None),
        ("_pipe_fatal",        None),
    ]:
        if key not in st.session_state:
            st.session_state[key] = default

    if run_btn and task_id_input.strip():
        task_id  = task_id_input.strip()
        repo_url = f"https://git.brojs.ru/{GITEA_OWNER}/task-{task_id}"
        prompt   = f"Реши задание taskId={task_id} курса 698b49da77cb6d4d2e43ce78"
        config   = {"configurable": {"thread_id": f"pipe-{task_id}-{int(time.time())}"}}
        messages = {"messages": [HumanMessage(content=prompt)]}
        agent    = get_agent()

        evq2      = queue.Queue()
        stop_flag2 = threading.Event()
        cb2        = AgentCallback(evq2)

        thread2 = threading.Thread(
            target=_run_agent_thread,
            args=(agent, messages, config, evq2, cb2, stop_flag2),
            daemon=True,
        )

        st.session_state["_pipe_thread"]      = thread2
        st.session_state["_pipe_queue"]       = evq2
        st.session_state["_pipe_stop_flag"]   = stop_flag2
        st.session_state["_pipe_task_id"]     = task_id
        st.session_state["_pipe_last_event_t"] = time.time()
        st.session_state.pipe_events          = []
        st.session_state.pipe_running         = True
        st.session_state["_pipe_result"]      = None
        st.session_state["_pipe_fatal"]       = None

        thread2.start()
        st.rerun()

    elif run_btn and not task_id_input.strip():
        st.info("Введи Task ID выше или используй раздел «Запустить все todo-задания»")

    # ── PIPELINE РАБОТАЕТ ────────────────────────────────────────────────────
    if st.session_state.pipe_running:
        thread2 = st.session_state.get("_pipe_thread")
        evq2    = st.session_state.get("_pipe_queue")
        task_id = st.session_state.get("_pipe_task_id", "")

        new_ev2: list[dict] = []
        final2 = fatal2 = None

        if evq2:
            while not evq2.empty():
                ev = evq2.get_nowait()
                if ev["t"] == "done":
                    final2 = ev["result"]
                elif ev["t"] == "fatal":
                    fatal2 = ev["msg"]
                else:
                    new_ev2.append(ev)

        if new_ev2:
            st.session_state.pipe_events.extend(new_ev2)
            st.session_state["_pipe_last_event_t"] = time.time()

        pipe_done = (final2 is not None or fatal2 is not None
                     or (thread2 is not None and not thread2.is_alive()
                         and (evq2 is None or evq2.empty())))

        if pipe_done:
            st.session_state.pipe_running    = False
            st.session_state["_pipe_result"] = final2
            st.session_state["_pipe_fatal"]  = fatal2
            st.rerun()

        # Живой лог
        if st.session_state.pipe_events:
            html = "".join(_render_event(e) for e in st.session_state.pipe_events[-60:])
            st.markdown(
                f'<div style="background:#0b0f1a;border-radius:8px;padding:10px;'
                f'max-height:300px;overflow-y:auto">{html}</div>',
                unsafe_allow_html=True,
            )

        idle2 = time.time() - st.session_state["_pipe_last_event_t"]
        if idle2 > 15:
            st.markdown(
                f'<div class="thinking" style="color:#f59e0b">'
                f'⏳ Агент работает... ({int(idle2)}с — возможно rate limit)</div>',
                unsafe_allow_html=True)
        else:
            st.markdown(f'<div class="thinking">💭 Решаю {task_id[:8]}...</div>',
                        unsafe_allow_html=True)

        if st.button("🛑 Остановить", key="pipe_stop_btn"):
            sf = st.session_state.get("_pipe_stop_flag")
            if sf:
                sf.set()
            st.session_state.pipe_running   = False
            st.session_state["_pipe_fatal"] = "Остановлено пользователем"
            st.rerun()

        time.sleep(0.5)
        st.rerun()

    # ── РЕЗУЛЬТАТ ────────────────────────────────────────────────────────────
    elif st.session_state.get("_pipe_fatal") or st.session_state.get("_pipe_result"):
        task_id  = st.session_state.get("_pipe_task_id", "")
        repo_url = f"https://git.brojs.ru/{GITEA_OWNER}/task-{task_id}"
        fatal2   = st.session_state.get("_pipe_fatal")
        final2   = st.session_state.get("_pipe_result")

        if fatal2:
            st.markdown(f'<div class="status-err">❌ {fatal2[:300]}</div>',
                        unsafe_allow_html=True)
        else:
            st.markdown(
                f'<div class="status-ok">✅ Готово! '
                f'<a href="{repo_url}" target="_blank" style="color:#4ade80">'
                f'Открыть репозиторий ↗</a></div>',
                unsafe_allow_html=True)

        if st.session_state.pipe_events:
            with st.expander(
                f"🔍 Лог ({len(st.session_state.pipe_events)} событий)", expanded=False
            ):
                html = "".join(_render_event(e) for e in st.session_state.pipe_events[-80:])
                st.markdown(f'<div style="max-height:300px;overflow-y:auto">{html}</div>',
                            unsafe_allow_html=True)

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
                    j = load_journal_toolsets()
                    # Инструменты имеют префикс mcp__journal-bh-professor__
                    tools = {t.name: t for t in j.tasks_submissions_tools}
                    full_name = "mcp__journal-bh-professor__tasks_list"
                    t = tools.get(full_name)
                    if not t:
                        # fallback: ищем по любому имени содержащему tasks_list
                        t = next((v for k, v in tools.items() if "tasks_list" in k), None)
                    if not t:
                        st.warning(f"Инструмент tasks_list не найден. Доступны: {list(tools.keys())}")
                        return []
                    raw  = await t.ainvoke({"courseId": "698b49da77cb6d4d2e43ce78"})
                    text = next((x["text"] for x in raw if x.get("type") == "text"), str(raw)) if isinstance(raw, list) else str(raw)
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
                "": STATUS_EMOJI.get(status, "❓"),
                "Статус":   status,
                "ID":       tid[:12] + "...",
                "Название": title,
                "Репо":     f"https://git.brojs.ru/{GITEA_OWNER}/task-{tid}",
            })

        st.dataframe(rows, use_container_width=True, hide_index=True)

        st.divider()
        cols = st.columns(len(counts))
        for col, (s, n) in zip(cols, counts.items()):
            col.metric(f"{STATUS_EMOJI.get(s,'❓')} {s}", n)
    else:
        st.info("Нажми «Обновить статусы» чтобы загрузить данные.")
