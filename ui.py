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
    from src.agent.agent import agent
    return agent


@st.cache_resource(show_spinner="Загрузка главного агента...")
def get_main_agent():
    from src.agent.agent import agent
    return agent

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

def _run_agent_thread(agent, messages, config, q: queue.Queue, cb: AgentCallback):
    async def _inner():
        try:
            result = await agent.ainvoke(messages, {**config, "callbacks": [cb]})
            q.put({"t": "done", "result": result})
        except Exception as e:
            q.put({"t": "fatal", "msg": str(e)})
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

    # История сообщений
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []
    if "chat_events" not in st.session_state:
        st.session_state.chat_events = []
    if "chat_thread_id" not in st.session_state:
        st.session_state.chat_thread_id = f"ui-{int(time.time())}"

    # Показываем историю
    for msg in st.session_state.chat_history:
        role = msg["role"]
        text = msg["text"]
        if role == "user":
            st.markdown(f'<div class="chat-user">👤 {text}</div>', unsafe_allow_html=True)
        else:
            st.markdown(f'<div class="chat-agent">🤖 {text}</div>', unsafe_allow_html=True)

    # Лог событий (раскрывающийся)
    if st.session_state.chat_events:
        with st.expander(f"🔍 Лог инструментов ({len(st.session_state.chat_events)} событий)", expanded=False):
            html = "".join(_render_event(e) for e in st.session_state.chat_events[-80:])
            st.markdown(f'<div style="max-height:300px;overflow-y:auto">{html}</div>',
                        unsafe_allow_html=True)

    # Ввод
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

        # Строим историю сообщений для агента
        lc_messages = []
        for m in st.session_state.chat_history:
            if m["role"] == "user":
                lc_messages.append(HumanMessage(content=m["text"]))
            else:
                lc_messages.append(AIMessage(content=m["text"]))

        config = {"configurable": {"thread_id": st.session_state.chat_thread_id}}
        agent  = get_agent()

        # Placeholders для обновления в реальном времени
        events_ph = st.empty()
        status_ph = st.empty()

        evq: queue.Queue = queue.Queue()
        cb  = AgentCallback(evq)
        all_events: list[dict] = []

        thread = threading.Thread(
            target=_run_agent_thread,
            args=(agent, {"messages": lc_messages}, config, evq, cb),
            daemon=True,
        )
        thread.start()

        final_result = None
        fatal        = None

        while thread.is_alive() or not evq.empty():
            changed = False
            while not evq.empty():
                ev = evq.get_nowait()
                if ev["t"] in ("done", "fatal"):
                    if ev["t"] == "done":
                        final_result = ev["result"]
                    else:
                        fatal = ev["msg"]
                else:
                    all_events.append(ev)
                    changed = True

            if changed and all_events:
                html = "".join(_render_event(e) for e in all_events[-60:])
                events_ph.markdown(
                    f'<div style="background:#0b0f1a;border-radius:8px;padding:10px;'
                    f'max-height:250px;overflow-y:auto">{html}</div>',
                    unsafe_allow_html=True,
                )
            time.sleep(0.15)

        events_ph.empty()
        st.session_state.chat_events = all_events

        if fatal:
            st.session_state.chat_history.append({"role": "agent", "text": f"⚠️ Ошибка: {fatal}"})
        elif final_result:
            msgs = final_result.get("messages", [])
            last = msgs[-1] if msgs else None
            reply = last.content if last and hasattr(last, "content") else "Готово."
            st.session_state.chat_history.append({"role": "agent", "text": reply})

        st.rerun()

    # Кнопка очистки
    if st.session_state.chat_history:
        if st.button("🗑 Очистить чат"):
            st.session_state.chat_history = []
            st.session_state.chat_events  = []
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

    if run_btn:
        result_ph  = st.empty()
        events_ph2 = st.empty()
        agent      = get_agent()

        if task_id_input.strip():
            # Одно задание
            task_id = task_id_input.strip()
            repo_url = f"https://git.brojs.ru/{GITEA_OWNER}/task-{task_id}"
            prompt   = f"Реши задание taskId={task_id} курса 698b49da77cb6d4d2e43ce78"
            config   = {"configurable": {"thread_id": f"pipe-{task_id}-{int(time.time())}"}}
            messages = {"messages": [HumanMessage(content=prompt)]}
        else:
            result_ph.info("Pipeline для всех todo-заданий — используй раздел ниже")
            st.stop()

        evq2: queue.Queue = queue.Queue()
        cb2  = AgentCallback(evq2)
        all_events2: list[dict] = []

        thread2 = threading.Thread(
            target=_run_agent_thread,
            args=(agent, messages, config, evq2, cb2),
            daemon=True,
        )
        thread2.start()

        final2 = None
        fatal2 = None

        with st.spinner(f"Агент решает {task_id[:8]}..."):
            while thread2.is_alive() or not evq2.empty():
                while not evq2.empty():
                    ev = evq2.get_nowait()
                    if ev["t"] == "done":
                        final2 = ev["result"]
                    elif ev["t"] == "fatal":
                        fatal2 = ev["msg"]
                    else:
                        all_events2.append(ev)

                if all_events2:
                    html = "".join(_render_event(e) for e in all_events2[-50:])
                    events_ph2.markdown(
                        f'<div style="background:#0b0f1a;border-radius:8px;padding:10px;'
                        f'max-height:300px;overflow-y:auto">{html}</div>',
                        unsafe_allow_html=True,
                    )
                time.sleep(0.15)

        if fatal2:
            result_ph.markdown(
                f'<div class="status-err">❌ Ошибка: {fatal2[:300]}</div>',
                unsafe_allow_html=True,
            )
        elif final2:
            result_ph.markdown(
                f'<div class="status-ok">✅ Готово! '
                f'<a href="{repo_url}" target="_blank" style="color:#4ade80">Открыть репозиторий</a>'
                f'</div>',
                unsafe_allow_html=True,
            )

    st.divider()
    st.subheader("Запустить все todo-задания")
    if st.button("⚡ Запустить агент для всех заданий", use_container_width=True):
        main_ag  = get_main_agent()
        evq_pl   = queue.Queue()
        cb_pl    = AgentCallback(evq_pl)
        all_pl_events: list[dict] = []
        _pl_state = {"result": None, "error": None}

        run_prompt = (
            "Выполни все задания со статусом todo в курсе KFU-26-1 "
            "(courseId=698b49da77cb6d4d2e43ce78).\n\n"
            "Шаги:\n"
            "1. Получи список заданий через mcp__journal-bh-professor__tasks_list\n"
            "2. Для каждого задания со статусом todo вызови solve_task(task_id=...)\n"
            "3. Выполняй строго по одному заданию, жди результата перед следующим\n"
            "4. Доложи итоговые результаты"
        )

        def _run_all():
            async def _inner():
                try:
                    result = await main_ag.ainvoke(
                        {"messages": [HumanMessage(content=run_prompt)]},
                        {
                            "configurable": {"thread_id": f"ui-run-all-{int(time.time())}"},
                            "callbacks": [cb_pl],
                        },
                    )
                    _pl_state["result"] = result
                    evq_pl.put({"t": "done", "result": result})
                except Exception as e:
                    _pl_state["error"] = str(e)
                    evq_pl.put({"t": "fatal", "msg": str(e)})
            asyncio.run(_inner())

        t_pl = threading.Thread(target=_run_all, daemon=True)
        t_pl.start()

        events_pl_ph = st.empty()
        with st.spinner("Агент-оркестратор работает... (LLM управляет всем)"):
            while t_pl.is_alive() or not evq_pl.empty():
                while not evq_pl.empty():
                    ev = evq_pl.get_nowait()
                    if ev["t"] not in ("done", "fatal"):
                        all_pl_events.append(ev)
                if all_pl_events:
                    html = "".join(_render_event(e) for e in all_pl_events[-60:])
                    events_pl_ph.markdown(
                        f'<div style="background:#0b0f1a;border-radius:8px;padding:10px;'
                        f'max-height:300px;overflow-y:auto">{html}</div>',
                        unsafe_allow_html=True,
                    )
                time.sleep(0.15)

        events_pl_ph.empty()

        if all_pl_events:
            with st.expander(f"🔍 Лог агента ({len(all_pl_events)} событий)", expanded=False):
                html = "".join(_render_event(e) for e in all_pl_events[-80:])
                st.markdown(f'<div style="max-height:300px;overflow-y:auto">{html}</div>',
                            unsafe_allow_html=True)

        if _pl_state["error"]:
            st.error(_pl_state["error"])
        elif _pl_state["result"]:
            msgs   = _pl_state["result"].get("messages", [])
            last   = msgs[-1] if msgs else None
            reply  = last.content if last and hasattr(last, "content") else "Готово."
            st.markdown(
                f'<div class="status-ok">✅ Агент завершил работу:<br>{reply[:600]}</div>',
                unsafe_allow_html=True,
            )


# ══════════════════════════════════════════════════════════════════════════
# ВК 3 — СТАТУС
# ══════════════════════════════════════════════════════════════════════════

with tab_status:
    st.caption("Статусы всех заданий курса KFU-26-1.")

    if st.button("🔄 Обновить статусы", type="primary"):
        with st.spinner("Загружаю статусы..."):
            from src.agent.mcp_client import load_journal_toolsets

            async def _fetch():
                j = load_journal_toolsets()
                tools = {t.name: t for t in j.tasks_submissions_tools}
                full_name = "mcp__journal-bh-professor__tasks_list"
                t = tools.get(full_name) or next(
                    (v for k, v in tools.items() if "tasks_list" in k), None
                )
                if not t:
                    return [], f"tasks_list не найден. Доступны: {list(tools.keys())}"
                raw  = await t.ainvoke({"courseId": "698b49da77cb6d4d2e43ce78"})
                text = next((x["text"] for x in raw if x.get("type") == "text"), str(raw)) if isinstance(raw, list) else str(raw)
                data = json.loads(text)
                return (data.get("tasks", data) if isinstance(data, dict) else data), None

            _state = {"items": [], "error": None}

            def _run_fetch():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    _state["items"], _state["error"] = loop.run_until_complete(_fetch())
                except Exception as e:
                    _state["error"] = str(e)
                finally:
                    loop.close()

            t = threading.Thread(target=_run_fetch, daemon=True)
            t.start()
            t.join()

            if _state["error"]:
                st.error(_state["error"])
            else:
                st.session_state["task_statuses"] = _state["items"]
                st.rerun()

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
