"""Спецификации субагентов (без инструментов — добавляются в agent.py)."""
from src.agent.llm import llm
from src.agent.prompts import (
    homework_doing_instructions,
    journal_tasks_submissions_instructions,
    research_instructions,
)

subagent_specs_without_tools: list[dict] = [
    {
        "name": "web_search",
        "description": (
            "Ищет информацию в интернете, находит URL и открывает страницы, "
            "чтобы извлекать факты только из реально прочитанного контента"
        ),
        "model": llm,
        "system_prompt": research_instructions,
    },
    {
        "name": "homework_doing",
        "description": (
            "Выполняет домашние задания: читает условие, пишет код, "
            "создаёт репозиторий на git.brojs.ru, тестирует решение и сдаёт на проверку"
        ),
        "model": llm,
        "system_prompt": homework_doing_instructions,
    },
    {
        "name": "journal_bh_tasks_submissions",
        "description": (
            "BroJS Journal: задания и сдачи — список заданий, чтение задания, "
            "проверка статуса, установка ответа, отправка на проверку"
        ),
        "model": llm,
        "system_prompt": journal_tasks_submissions_instructions,
    },
]
