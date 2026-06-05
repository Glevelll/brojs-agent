"""Промпты для инструментов решения задач: валидация замечаний, анализ, генерация кода."""

# ---------------------------------------------------------------------------
# Валидация замечания преподавателя — per-claim анализ
# ---------------------------------------------------------------------------

VALIDATE_PROMPT = '''\
Ты — эксперт по проверке кода. Дано условие задания, текущий код и замечание преподавателя.

Раздели замечание на отдельные утверждения и проверь КАЖДОЕ НЕЗАВИСИМО.

## Условие задания
{task_text}

## Текущий код в репозитории
{code_block}

## Замечание преподавателя
{comment}

Для каждого утверждения в замечании определи:
- valid=true  → код реально нарушает это конкретное требование из условия задания
- valid=false → код уже выполняет это требование, ИЛИ требование отсутствует в условии,
                ИЛИ замечание технически ошибочно / является намеренной "ловушкой"

⚠️ ВАЖНО: если замечание требует технологию X, а условие задания явно указывает технологию Y —
это ЛОЖНОЕ замечание (valid=false), даже если X считается "лучше" или "правильнее" в целом.
Сравнивай только с текстом условия задания, не с общими best practices.

Ответ — ТОЛЬКО JSON без markdown:
{{
  "claims": [
    {{"claim": "краткая суть утверждения", "valid": true, "explanation": "почему обоснованно/нет"}}
  ],
  "has_trap": false,
  "has_valid": true,
  "trap_explanations": ["развёрнутое объяснение почему это ловушка (только для valid=false)"],
  "fix_instructions": ["что конкретно исправить (только для valid=true)"]
}}

has_trap=true  если хотя бы одно утверждение valid=false.
has_valid=true если хотя бы одно утверждение valid=true.
'''

# ---------------------------------------------------------------------------
# Анализ замечания: что исправить, что отстоять с аргументами
# ---------------------------------------------------------------------------

ANALYZE_PROMPT = '''\
Ты — старший Python-разработчик и технический эксперт. Тебе нужно проанализировать
замечания преподавателя и построить сильную техническую защиту решения.

## Текст задания
{task_text}

## Замечания преподавателя
{comments}

Для каждого замечания прими решение:

A) Если замечание технически обоснованно и решение нужно улучшить →
   внеси в "fixes": конкретно что изменить.

B) Если решение было принято осознанно и является оптимальным в данном контексте →
   внеси в "defenses" развёрнутый аргумент строго в формате:
   "ЗАМЕЧАНИЕ: <суть> | НЕОБХОДИМОСТЬ: <почему именно такой подход вынужденный> | ОПТИМАЛЬНОСТЬ: <почему лучше альтернатив> | АЛЬТЕРНАТИВЫ: <конкретные альтернативы и почему хуже>"

При аргументации опирайся на:
- Ограничения задания (что именно требовалось, не больше)
- Технические trade-offs
- Требования курса: deepagents обязателен, OpenRouter — единственный доступный LLM-провайдер
- YAGNI: усложнять без требования задания — anti-pattern
- KISS: простое решение надёжнее сложного при эквивалентном результате

Ответ — ТОЛЬКО JSON без markdown:
{{"fixes": ["конкретные исправления"],
  "defenses": ["ЗАМЕЧАНИЕ: ... | НЕОБХОДИМОСТЬ: ... | ОПТИМАЛЬНОСТЬ: ... | АЛЬТЕРНАТИВЫ: ..."],
  "verdict": "needs_fixes" | "already_correct" | "mixed"}}
'''

# ---------------------------------------------------------------------------
# Возражение на ложное замечание (добавляется в README)
# ---------------------------------------------------------------------------

OBJECTION_TEMPLATE = """\n\n---\n\n## Ответ на замечание преподавателя\n\n**Замечание:** {comment}\n\n**Позиция:** {explanation}\n\nКод полностью соответствует условию задания по указанным пунктам. Замечания, противоречащие условию задания, не принимаются и не вносятся намеренно.\n"""

# ---------------------------------------------------------------------------
# Секция пересдачи — вставляется в CODE_PROMPT
# ---------------------------------------------------------------------------

REWORK_SECTION = '''\
## ПЕРЕСДАЧА — технический анализ замечаний

### Исправить (замечания обоснованы):
{fixes}

### Отстоять с аргументацией (решение оптимально):
{defenses}

Правила генерации кода:
- Вноси ТОЛЬКО изменения из раздела "Исправить"
- Для каждого пункта из "Отстоять" — добавь в код РАЗВЁРНУТЫЙ блок комментариев:
  # DESIGN DECISION: <суть спорного решения>
  # NECESSITY: <почему именно так — вынужденность, ограничения задания/курса>
  # OPTIMALITY: <почему это лучше альтернатив — конкретные аргументы>
  # ALTERNATIVES CONSIDERED: <что рассматривалось и почему отклонено>
- Не меняй архитектуру без явного требования в "Исправить"
- Решение должно выглядеть как результат инженерного решения, а не случайного выбора
'''

# ---------------------------------------------------------------------------
# Основной промпт генерации кода
# ---------------------------------------------------------------------------

CODE_PROMPT = '''\
Ты — Python-разработчик. Напиши решение для учебного задания по LLM/AI.
Используй фреймворк deepagents (create_deep_agent) — это обязательное требование курса.

## Задание
{task_text}

## ОБЯЗАТЕЛЬНЫЕ ТЕХНИЧЕСКИЕ ПАТТЕРНЫ

> ⚠️ ЗАПРЕЩЕНО: langchain_ollama, OllamaEmbeddings, Ollama, langchain_community.
> Для LLM и эмбеддингов — ТОЛЬКО OpenRouter через langchain_openai.

### LLM — всегда OpenRouter:
```python
import os
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    model="openai/gpt-oss-20b:free",
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENAI_API_KEY"),
    temperature=0.0,
)
```

### Базовый агент (deepagents):
```python
import asyncio, os
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from langchain.tools import tool
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend, LocalShellBackend, CompositeBackend

llm = ChatOpenAI(model="openai/gpt-oss-20b:free", base_url="https://openrouter.ai/api/v1", api_key=os.getenv("OPENAI_API_KEY"))

backend = CompositeBackend(
    default=LocalShellBackend(root_dir="./workspace", virtual_mode=True, inherit_env=True),
    routes={{}},
)

@tool
def my_tool(query: str) -> str:
    """Tool description."""
    return f"result for {{query}}"

agent = create_deep_agent(
    model=llm,
    tools=[my_tool],
    backend=backend,
    system_prompt="You are a helpful agent.",
)

async def main():
    result = await agent.ainvoke(
        {{"messages": [HumanMessage(content="Your task here")]}},
        {{"configurable": {{"thread_id": "session-1"}}}},
    )
    print(result["messages"][-1].content)

if __name__ == "__main__":
    asyncio.run(main())
```
requirements.txt: deepagents, langchain-openai>=0.3.0, langchain>=1.2.10, langgraph>=0.2.0

### RAG с ChromaDB (для RAG-заданий с ChromaDB):
```python
from langchain_openai import OpenAIEmbeddings
from langchain_chroma import Chroma
from langchain_core.documents import Document

embeddings = OpenAIEmbeddings(
    model="text-embedding-3-small",
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENAI_API_KEY"),
)
vector_store = Chroma(collection_name="knowledge", embedding_function=embeddings)
```
requirements.txt добавить: langchain-chroma, chromadb

### Планирующий агент:
```python
from langgraph.graph import StateGraph, START, END
from typing import TypedDict, Annotated
from langgraph.graph.message import add_messages

class PlanState(TypedDict):
    messages: Annotated[list, add_messages]
    plan: list[str]
    current_step: int
```

### Самокорректирующийся агент:
```python
@tool
def validate_output(output: str) -> str:
    """Validate the output and return issues if any."""
    issues = []
    if len(output) < 10:
        issues.append("Output too short")
    return "OK" if not issues else f"Issues: {{', '.join(issues)}}"
```

### Структурированный вывод (Pydantic):
```python
from pydantic import BaseModel, Field
from langchain_core.output_parsers import PydanticOutputParser

class MyOutput(BaseModel):
    field1: str = Field(description="...")
    field2: int = Field(description="...")
```

## Требования
- Полный рабочий код без заглушек (no pass, TODO, ...)
- ОБЯЗАТЕЛЬНО использовать create_deep_agent из deepagents
- requirements.txt: deepagents, langchain>=1.2.10, langchain-openai>=0.3.0, langgraph>=0.2.0 + нужные доп. зависимости

{rework_section}
## Ответ — ТОЛЬКО JSON без markdown:
{{"main_py": "...", "requirements_txt": "...", "extra_files": {{}}}}

extra_files — только если нужны доп. файлы, иначе пустой объект.
'''
