"""
CLI-скрипт для построения FAISS-индекса из JSON-файлов базы знаний.

Использование:
    python rag_indexer.py                          # индексирует vectordb/knowledge → vectordb/knowledge_index
    python rag_indexer.py --source путь/к/json    # кастомный источник
    python rag_indexer.py --output путь/к/индексу  # кастомный выход
    python rag_indexer.py --incremental            # добавляет к существующему индексу

Формат JSON-файлов:
    {
        "request": "описание задачи",
        "guidance": ["шаг 1", "шаг 2", ...]
    }
"""
import argparse
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DEFAULT_SOURCE = os.path.join(BASE_DIR, "vectordb", "knowledge")
DEFAULT_OUTPUT = os.path.join(BASE_DIR, "vectordb", "knowledge_index")


def load_json_documents(source_dir: str) -> list:
    """Загружает все JSON-файлы из папки и конвертирует в LangChain Documents."""
    from langchain_core.documents import Document

    documents = []
    if not os.path.isdir(source_dir):
        print(f"[Ошибка] Папка не найдена: {source_dir}")
        return documents

    for fname in sorted(os.listdir(source_dir)):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(source_dir, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"  [Пропуск] {fname}: {e}")
            continue

        request = data.get("request", "").strip()
        guidance = data.get("guidance", [])
        if not request:
            print(f"  [Пропуск] {fname}: нет поля 'request'")
            continue

        guidance_text = "\n".join(str(s) for s in guidance)
        metadata = {
            "title": request,
            "summary": request,
            "text": guidance_text,
            "source": fname,
        }
        documents.append(Document(page_content=request, metadata=metadata))
        print(f"  ✓ {fname}: {request}")

    return documents


def build_index(source_dir: str, output_dir: str, incremental: bool = False) -> None:
    """Строит FAISS-индекс из JSON-документов и сохраняет в output_dir."""
    print(f"\n[RAG Indexer] Источник: {source_dir}")
    print(f"[RAG Indexer] Выход:    {output_dir}")
    print(f"[RAG Indexer] Режим:    {'инкрементальный' if incremental else 'полная пересборка'}\n")

    # Добавляем корень проекта в sys.path для импорта ui_automation
    if BASE_DIR not in sys.path:
        sys.path.insert(0, BASE_DIR)

    print("Загрузка документов…")
    documents = load_json_documents(source_dir)
    if not documents:
        print("[Ошибка] Нет документов для индексации.")
        sys.exit(1)
    print(f"\nЗагружено документов: {len(documents)}")

    print("\nЗагрузка модели эмбеддингов (sentence-transformers)…")
    try:
        from ui_automation.utils import get_hugginface_embedding
        embeddings = get_hugginface_embedding()
    except Exception as e:
        print(f"[Ошибка] Не удалось загрузить эмбеддинги: {e}")
        sys.exit(1)

    print("Создание FAISS-индекса…")
    try:
        from langchain_community.vectorstores import FAISS
        db = FAISS.from_documents(documents, embeddings)
    except Exception as e:
        print(f"[Ошибка] Не удалось создать FAISS: {e}")
        sys.exit(1)

    # Инкрементальное обновление
    index_file = os.path.join(output_dir, "index.faiss")
    if incremental and os.path.isfile(index_file):
        print("Объединение с существующим индексом…")
        try:
            prev_db = FAISS.load_local(
                output_dir, embeddings, allow_dangerous_deserialization=True
            )
            db.merge_from(prev_db)
            print(f"  Объединено с {index_file}")
        except Exception as e:
            print(f"  [Предупреждение] Не удалось загрузить предыдущий индекс: {e}")

    os.makedirs(output_dir, exist_ok=True)
    db.save_local(output_dir)
    print(f"\n[RAG Indexer] Индекс сохранён: {output_dir}")
    print(f"[RAG Indexer] Файлы: index.faiss, index.pkl")


def main():
    parser = argparse.ArgumentParser(
        description="Построение FAISS-индекса из JSON-файлов базы знаний"
    )
    parser.add_argument(
        "--source", default=DEFAULT_SOURCE,
        help=f"Папка с JSON-файлами (по умолчанию: {DEFAULT_SOURCE})"
    )
    parser.add_argument(
        "--output", default=DEFAULT_OUTPUT,
        help=f"Папка для сохранения индекса (по умолчанию: {DEFAULT_OUTPUT})"
    )
    parser.add_argument(
        "--incremental", action="store_true",
        help="Добавить к существующему индексу вместо полной пересборки"
    )
    args = parser.parse_args()
    build_index(args.source, args.output, args.incremental)


if __name__ == "__main__":
    main()
