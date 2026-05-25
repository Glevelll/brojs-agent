# brojs-agent

AI-агент для автоматического выполнения заданий курса KFU-26-1 на platform.brojs.ru.

## Что делает агент

1. Читает незакрытые задания из журнала через BroJS MCP
2. Пишет Python-решение для каждого задания
3. Создаёт репозиторий на `git.brojs.ru/glevelll/task-<id>`
4. Коммитит решение через Gitea API
5. Отправляет ссылку на репозиторий как ответ
6. Сдаёт задание на проверку
7. При пересдаче — клонирует, исправляет, пушит снова

## Стек

- Python 3.11+
- [deepagents](https://github.com/langchain-ai/deepagents) — мультиагентный фреймворк
- [LangGraph](https://github.com/langchain-ai/langgraph) — граф пайплайна
- [LangChain](https://python.langchain.com/) — инструменты и MCP-адаптеры
- OpenRouter → `gpt-oss-20b:free`
- Gitea REST API (git.brojs.ru)
- BroJS Journal MCP (platform.brojs.ru)

## Установка

```bash
# Клонировать репозиторий
git clone https://github.com/Glevelll/brojs-agent.git
cd brojs-agent

# Установить зависимости
pip install -r requirements.txt
# или через uv:
# uv sync

# Создать .env из шаблона
cp .env.example .env
# Отредактировать .env — заменить заглушки на реальные ключи
```

## Настройка `.env`

```env
OPENAI_API_KEY=sk-or-...        # OpenRouter ключ
JOURNAL_TOKEN=jrnl_...          # BroJS токен (генерируется на platform.brojs.ru)
GITEA_TOKEN=...                 # Токен на git.brojs.ru
TAVILY_API_KEY=...              # Опционально, для web_search
```

### Где взять токены

| Токен | Где получить |
|-------|-------------|
| `OPENAI_API_KEY` | https://openrouter.ai/settings/keys |
| `JOURNAL_TOKEN` | platform.brojs.ru → профиль → API токены |
| `GITEA_TOKEN` | git.brojs.ru → Settings → Applications → Access Tokens |
| `TAVILY_API_KEY` | https://tavily.com (опционально) |

> ⚠️ **ВАЖНО**: Никогда не коммить `.env` в репозиторий! Он добавлен в `.gitignore`.

## Запуск

### Запуск пайплайна (выполнить все незакрытые задания)

```bash
python -c "
import asyncio
from src.agent.graph.pipeline import pipeline
from langchain_core.messages import HumanMessage

async def main():
    result = await pipeline.ainvoke(
        {'tasks': [], 'current_index': 0, 'results': [], 'errors': []},
        {'configurable': {'thread_id': 'pipeline-run-1'}}
    )
    print(f'Выполнено: {len(result[\"results\"])} заданий')
    print(f'Ошибки: {result[\"errors\"]}')

asyncio.run(main())
"
```

### LangGraph dev server (UI в браузере)

```bash
# Установить langgraph-cli
pip install "langgraph-cli[inmem]"

# Запустить dev server
langgraph dev --allow-blocking --port 2024
# Studio UI: https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024
```

### Интерактивный чат с главным агентом

```bash
python -c "
import asyncio
from src.agent.agent import agent
from langchain_core.messages import HumanMessage

async def main():
    config = {'configurable': {'thread_id': 'chat-1'}}
    while True:
        user = input('Вы: ')
        if user == 'exit': break
        result = await agent.ainvoke(
            {'messages': [HumanMessage(content=user)]}, config
        )
        print('Агент:', result['messages'][-1].content)

asyncio.run(main())
"
```

## Структура проекта

```
brojs-agent/
├── .env.example              # шаблон переменных окружения
├── .gitignore
├── requirements.txt
├── pyproject.toml
├── langgraph.json
├── agent.py                  # точка входа для langgraph dev
└── src/
    └── agent/
        ├── agent.py          # создание агентов (main, homework, rework)
        ├── constants.py      # COURSE_ID, GITEA_OWNER, пути
        ├── llm.py            # LLM через OpenRouter
        ├── gitea_tools.py    # Gitea REST API инструменты
        ├── mcp_client.py     # BroJS Journal MCP клиент
        ├── tools.py          # git + web инструменты
        ├── prompts.py        # системные промпты
        ├── subagents.py      # спецификации субагентов
        ├── middlewares/
        │   ├── sanitize_tool_calls.py       # блокировка несуществующих инструментов
        │   └── validate_journal_workflow.py # порядок task_update_answer → task_submit
        ├── graph/
        │   └── pipeline.py   # LangGraph пайплайн по всем заданиям
        └── agent_workspace/  # рабочая директория агента (клонированные репо)
```

## Заглушки в коде

| Файл | Переменная | Что заменить |
|------|------------|-------------|
| `src/agent/llm.py` | `OPENAI_API_KEY` | OpenRouter ключ в `.env` |
| `src/agent/mcp_client.py` | `JOURNAL_TOKEN` | BroJS токен в `.env` |
| `src/agent/gitea_tools.py` | `GITEA_TOKEN` | Gitea токен в `.env` |
