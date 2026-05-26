"""Инициализация LLM через BroJS Inference API (токены учитываются на платформе)."""
import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv()

# Используем BroJS Inference API — тот же JOURNAL_TOKEN (jrnl_...)
# Токены учитываются на платформе platform.brojs.ru
llm = ChatOpenAI(
    model="openai/gpt-oss-20b:free",
    base_url="https://platform.brojs.ru/jrnl-bh/api/inference/v1",
    api_key=os.getenv("JOURNAL_TOKEN", "YOUR_JOURNAL_TOKEN_HERE"),
    temperature=0.0,
)
