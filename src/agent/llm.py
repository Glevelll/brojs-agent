"""Инициализация LLM через OpenRouter."""
import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv()

# ⚠️ ЗАГЛУШКА #1 — замени в .env: OPENAI_API_KEY=sk-or-xxxxxxxx
# Файл .env.example показывает все нужные переменные.
llm = ChatOpenAI(
    model="openai/gpt-oss-20b:free",
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENAI_API_KEY", "YOUR_OPENROUTER_KEY_HERE"),
    temperature=0.0,
)
