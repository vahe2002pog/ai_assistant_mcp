"""
Сохранение результатов tavily-поиска в FAISS-индекс vectordb/web_search.
Каждый результат (title/url/snippet) индексируется отдельным документом,
а сам запрос сохраняется как агрегированный документ, чтобы похожие запросы
в будущем находили уже найденные ссылки.
"""
from __future__ import annotations

import os
import time
import threading
from typing import List

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_WEB_SEARCH_DB_PATH = os.path.join(BASE_DIR, "vectordb", "web_search")

_SAVE_LOCK = threading.Lock()


def _build_documents(query: str, results: list, source: str):
    from langchain_core.documents import Document

    docs = []
    ts = int(time.time())

    # Агрегированный документ по запросу — используется при похожем запросе в будущем.
    summary_lines = [f"Запрос: {query}"]
    for r in results:
        title = (r.get("title") or "").strip()
        url = (r.get("url") or "").strip()
        snippet = (r.get("content") or "").strip()
        if title or url:
            summary_lines.append(f"- {title} :: {url}")
        if snippet:
            # Коротко, чтобы агрегат не раздувать
            summary_lines.append(f"  {snippet[:300]}")

    docs.append(Document(
        page_content="\n".join(summary_lines),
        metadata={"query": query, "kind": "summary", "source": source, "timestamp": ts},
    ))

    # Отдельные документы по каждому результату — для точечного поиска по содержанию.
    for r in results:
        title = (r.get("title") or "").strip()
        url = (r.get("url") or "").strip()
        snippet = (r.get("content") or "").strip()
        if not (snippet or title):
            continue
        content = f"{title}\n{url}\n{snippet}".strip()
        docs.append(Document(
            page_content=content,
            metadata={
                "query": query,
                "kind": "result",
                "url": url,
                "title": title,
                "score": r.get("score", 0),
                "source": source,
                "timestamp": ts,
            },
        ))

    return docs


def save_search_results(query: str, results: list, source: str = "tavily_search") -> None:
    """
    Инкрементально сохраняет результаты поиска в vectordb/web_search.
    Безопасен для фонового вызова: глотает все исключения.
    """
    if not query or not results:
        return
    try:
        from langchain_community.vectorstores import FAISS
        from ui_automation.utils import get_hugginface_embedding

        docs = _build_documents(query, results, source)
        if not docs:
            return

        embeddings = get_hugginface_embedding()

        with _SAVE_LOCK:
            os.makedirs(_WEB_SEARCH_DB_PATH, exist_ok=True)
            index_file = os.path.join(_WEB_SEARCH_DB_PATH, "index.faiss")
            if os.path.isfile(index_file):
                existing = FAISS.load_local(
                    _WEB_SEARCH_DB_PATH, embeddings, allow_dangerous_deserialization=True,
                )
                new_db = FAISS.from_documents(docs, embeddings)
                existing.merge_from(new_db)
                existing.save_local(_WEB_SEARCH_DB_PATH)
            else:
                db = FAISS.from_documents(docs, embeddings)
                db.save_local(_WEB_SEARCH_DB_PATH)
    except Exception as e:
        try:
            from ui_automation.utils import print_with_color
            print_with_color(f"[RAG] Ошибка сохранения веб-поиска: {e}", "yellow")
        except Exception:
            pass

    # Параллельно пишем заметку в vault/WebSearch — она проиндексируется
    # через общий vault-FAISS и будет участвовать в search_grouped().
    try:
        from ui_automation.rag import vault_manager as _vm
        _vm.save_web_search(query, results, source=source)
    except Exception as e:
        try:
            from ui_automation.utils import print_with_color
            print_with_color(f"[vault] Ошибка сохранения web-search в vault: {e}", "yellow")
        except Exception:
            pass


def save_search_results_async(query: str, results: list, source: str = "tavily_search") -> None:
    """Запускает сохранение в демон-потоке — не блокирует ответ инструмента."""
    try:
        t = threading.Thread(
            target=save_search_results,
            args=(query, results, source),
            daemon=True,
        )
        t.start()
    except Exception:
        pass


def save_extract_results(urls: List[str], results: list) -> None:
    """Сохраняет результаты tavily_extract как полноценные документы."""
    if not results:
        return
    normalized = []
    for item in results:
        normalized.append({
            "title": item.get("url", ""),
            "url": item.get("url", ""),
            "content": item.get("content", ""),
        })
    save_search_results_async(
        query="extract: " + ", ".join(urls or []),
        results=normalized,
        source="tavily_extract",
    )
