"""
Менеджер опыта для RAG-системы.
Сохраняет результаты выполненных задач в FAISS-индекс vectordb/experience.
После накопления опыта — похожие задачи получают релевантный контекст из истории.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import List

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_EXPERIENCE_DB_PATH = os.path.join(BASE_DIR, "vectordb", "experience")

_API_BASE  = os.environ.get("API_BASE",  "http://localhost:8000/v1")
_API_KEY   = os.environ.get("API_KEY",   "llama")
_API_MODEL = os.environ.get("API_MODEL", "local")

_SUMMARIZE_PROMPT = """Ты анализируешь выполненную задачу ИИ-ассистента для Windows.
На основе запроса и результата сформируй краткое резюме опыта.

Верни ТОЛЬКО JSON без пояснений:
{
  "summary": "Краткое описание того что было сделано и как (1-2 предложения)",
  "tips": ["Совет 1 для похожих задач", "Совет 2", "Совет 3"]
}"""


def _summarize_with_llm(task: str, result: str) -> dict:
    """
    Опциональная LLM-суммаризация завершённой задачи.
    Возвращает {"summary": str, "tips": list} или fallback-значения при ошибке.
    """
    try:
        import openai
        client = openai.OpenAI(base_url=_API_BASE, api_key=_API_KEY)
        user_msg = f"Запрос: {task}\n\nРезультат: {result}"
        resp = client.chat.completions.create(
            model=_API_MODEL,
            messages=[
                {"role": "system", "content": _SUMMARIZE_PROMPT},
                {"role": "user",   "content": user_msg},
            ],
            temperature=0,
            max_tokens=512,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        raw = (resp.choices[0].message.content or "").strip()
        # Убираем <think>...</think>
        raw = re.sub(r"<think>[\s\S]*?</think>", "", raw, flags=re.DOTALL).strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```\w*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw).strip()
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            data = json.loads(m.group())
            return {
                "summary": data.get("summary", result),
                "tips": data.get("tips", []),
            }
    except Exception:
        pass
    return {"summary": result, "tips": []}


def save_experience(
    task: str,
    result: str,
    agent_types: list,
    app_names: list | None = None,
    use_llm: bool = True,
) -> None:
    """
    Сохраняет опыт выполнения задачи в vectordb/experience.
    Вызывается из HostAgent.dispatch() после каждого успешного выполнения.

    :param task: Исходный запрос пользователя.
    :param result: Итоговый результат выполнения.
    :param agent_types: Список типов агентов, которые участвовали (["system"], ["browser", "web"]).
    :param app_names: Имена приложений, с которыми работали.
    :param use_llm: Использовать LLM для суммаризации (по умолчанию True).
    """
    if not task or not result:
        return

    try:
        from langchain_core.documents import Document
        from langchain_community.vectorstores import FAISS
        from ui_automation.utils import get_hugginface_embedding

        # Суммаризация
        if use_llm:
            summary_data = _summarize_with_llm(task, result)
        else:
            summary_data = {"summary": result, "tips": []}

        # Формируем метаданные
        flat_agents = []
        for a in agent_types:
            if isinstance(a, list):
                flat_agents.extend(a)
            else:
                flat_agents.append(str(a))

        metadata = {
            "request": task,
            "summary": summary_data["summary"],
            "tips": " | ".join(summary_data["tips"]) if summary_data["tips"] else "",
            "agent_types": ", ".join(flat_agents),
            "app_list": ", ".join(app_names or []),
            "timestamp": int(time.time()),
        }

        doc = Document(page_content=task, metadata=metadata)
        embeddings = get_hugginface_embedding()

        os.makedirs(_EXPERIENCE_DB_PATH, exist_ok=True)

        index_file = os.path.join(_EXPERIENCE_DB_PATH, "index.faiss")
        if os.path.isfile(index_file):
            # Инкрементальное обновление
            existing_db = FAISS.load_local(
                _EXPERIENCE_DB_PATH, embeddings, allow_dangerous_deserialization=True
            )
            new_db = FAISS.from_documents([doc], embeddings)
            existing_db.merge_from(new_db)
            existing_db.save_local(_EXPERIENCE_DB_PATH)
        else:
            db = FAISS.from_documents([doc], embeddings)
            db.save_local(_EXPERIENCE_DB_PATH)

    except Exception as e:
        # Не прерываем работу ассистента из-за ошибки сохранения опыта
        try:
            from ui_automation.utils import print_with_color
            print_with_color(f"[RAG] Ошибка сохранения опыта: {e}", "yellow")
        except Exception:
            pass


def retrieve_experience(query: str, top_k: int = 3) -> List:
    """
    Извлекает релевантный опыт из vectordb/experience.

    :param query: Запрос пользователя.
    :param top_k: Количество возвращаемых документов.
    :return: Список LangChain Document.
    """
    index_file = os.path.join(_EXPERIENCE_DB_PATH, "index.faiss")
    if not os.path.isfile(index_file):
        return []
    try:
        from langchain_community.vectorstores import FAISS
        from ui_automation.utils import get_hugginface_embedding
        db = FAISS.load_local(
            _EXPERIENCE_DB_PATH, get_hugginface_embedding(),
            allow_dangerous_deserialization=True,
        )
        return db.similarity_search(query, top_k)
    except Exception:
        return []
