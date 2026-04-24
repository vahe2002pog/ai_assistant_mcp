"""
Obsidian vault как единственный RAG-источник.

Структура:
    vault/
      Scenarios/    — пользовательские сценарии (.md с frontmatter)
      Knowledge/    — справочные заметки
      Experience/   — автосохранение задач
      Attachments/  — документы (оригинал + .md-обёртка с текстом)
      .index/       — FAISS индекс (служебный)

Каждая заметка — Markdown с опциональным YAML-frontmatter.
Чанкинг: по заголовкам `##`. Метаданные чанка: {path, folder, name, tags, triggers, ...}.

API:
    reindex(full=False) -> int               — построить/обновить индекс, вернуть число чанков
    search(query, k=5, folder=None) -> list  — вернуть LangChain Document'ы
    search_grouped(query, k_per_folder=3)    — {folder: [Document,...]} с приоритетами
    save_scenario(name, triggers, body, tags=None) -> path
    save_experience(task, result, agent_types=None, app_names=None)
    save_document(original_path, extracted_text, tags=None) -> path
    list_notes(folder) -> list[dict]         — {name, path, frontmatter, preview}
    read_note(rel_path) -> dict              — полное содержимое
    delete_note(rel_path) -> bool
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Iterable, List, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
VAULT_DIR = os.path.join(BASE_DIR, "vault")
INDEX_DIR = os.path.join(VAULT_DIR, ".index")
META_FILE = os.path.join(INDEX_DIR, "files.json")

FOLDERS = ("Scenarios", "Knowledge", "Experience", "Attachments", "WebSearch")
# Приоритет при общем поиске (сначала сценарии пользователя, потом опыт, знания, документы,
# в конце — веб-поиск: свежий, но наименее доверенный источник).
FOLDER_PRIORITY = {"Scenarios": 0, "Experience": 1, "Knowledge": 2, "Attachments": 3, "WebSearch": 4}


# ─────────────────────────────────────────────────────────────────────────────
#  Утилиты
# ─────────────────────────────────────────────────────────────────────────────

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def _ensure_dirs() -> None:
    for f in FOLDERS:
        os.makedirs(os.path.join(VAULT_DIR, f), exist_ok=True)
    os.makedirs(INDEX_DIR, exist_ok=True)


def _slug(text: str, max_len: int = 60) -> str:
    t = re.sub(r"[^\w\- а-яА-ЯёЁ]+", "-", text.strip(), flags=re.UNICODE)
    t = re.sub(r"-+", "-", t).strip("-")
    return (t[:max_len] or "note").lower()


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Грубый YAML-парсер: поддерживает `key: value` и `key: [a, b]`."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    raw = m.group(1)
    body = text[m.end():]
    fm: dict = {}
    for line in raw.splitlines():
        line = line.rstrip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            items: List[str] = []
            for part in _split_csv(inner):
                part = part.strip().strip('"').strip("'")
                if part:
                    items.append(part)
            fm[key] = items
        else:
            fm[key] = val.strip('"').strip("'")
    return fm, body


def _split_csv(s: str) -> Iterable[str]:
    """Разбивает `a, "b, c", d` по запятым с учётом кавычек."""
    buf, in_q = "", None
    for ch in s:
        if in_q:
            buf += ch
            if ch == in_q:
                in_q = None
        elif ch in ('"', "'"):
            buf += ch
            in_q = ch
        elif ch == ",":
            yield buf
            buf = ""
        else:
            buf += ch
    if buf:
        yield buf


def _dump_frontmatter(fm: dict) -> str:
    if not fm:
        return ""
    lines = ["---"]
    for k, v in fm.items():
        if isinstance(v, list):
            body = ", ".join(f'"{x}"' if ("," in str(x) or ":" in str(x)) else str(x) for x in v)
            lines.append(f"{k}: [{body}]")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def _chunk(body: str, name: str) -> List[str]:
    """Разбивает тело заметки по `##`. Если заголовков нет — одна глыба."""
    parts = re.split(r"(?m)^##\s+", body)
    out: List[str] = []
    for i, p in enumerate(parts):
        p = p.strip()
        if not p:
            continue
        # первая часть — текст до первого ##, без префикса
        prefix = f"[{name}] " if i == 0 else f"[{name} / "
        suffix = ""
        if i > 0:
            first_line, _, rest = p.partition("\n")
            prefix = f"[{name} / {first_line.strip()}] "
            p = rest.strip() or first_line.strip()
        out.append(prefix + p + suffix)
    return out or [f"[{name}] {body.strip()}"]


# ─────────────────────────────────────────────────────────────────────────────
#  Индекс
# ─────────────────────────────────────────────────────────────────────────────

def _iter_md_files() -> Iterable[tuple[str, str]]:
    """(abs_path, rel_path) для всех .md в vault (кроме .index/)."""
    for root, dirs, files in os.walk(VAULT_DIR):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fn in files:
            if not fn.lower().endswith(".md"):
                continue
            abs_p = os.path.join(root, fn)
            rel_p = os.path.relpath(abs_p, VAULT_DIR).replace("\\", "/")
            if rel_p == "README.md":
                continue
            yield abs_p, rel_p


def _load_note(abs_path: str, rel_path: str) -> Optional[dict]:
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None
    fm, body = _parse_frontmatter(text)
    folder = rel_path.split("/", 1)[0] if "/" in rel_path else ""
    name = fm.get("name") or os.path.splitext(os.path.basename(rel_path))[0]
    return {
        "abs_path": abs_path,
        "rel_path": rel_path,
        "folder": folder,
        "name": name,
        "frontmatter": fm,
        "body": body,
        "mtime": os.path.getmtime(abs_path),
    }


def _note_to_docs(note: dict):
    from langchain_core.documents import Document

    fm = note["frontmatter"]
    base_meta = {
        "rel_path": note["rel_path"],
        "folder": note["folder"],
        "name": note["name"],
        "tags": ",".join(fm.get("tags", []) if isinstance(fm.get("tags"), list) else []),
        "triggers": " | ".join(fm.get("triggers", []) if isinstance(fm.get("triggers"), list) else []),
    }
    docs = []
    # Триггеры индексируются отдельным «толстым» чанком — чтобы фразы запуска хорошо матчились.
    if base_meta["triggers"]:
        docs.append(Document(page_content=f"[{note['name']} — триггеры] {base_meta['triggers']}", metadata=base_meta))
    for chunk in _chunk(note["body"], note["name"]):
        docs.append(Document(page_content=chunk, metadata=base_meta))
    return docs


def _load_meta() -> dict:
    if not os.path.isfile(META_FILE):
        return {}
    try:
        with open(META_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_meta(meta: dict) -> None:
    with open(META_FILE, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def reindex(full: bool = False) -> int:
    """Строит/обновляет FAISS-индекс. Возвращает количество проиндексированных чанков.

    full=True — пересобирает с нуля.
    full=False — инкрементально: переиндексирует только изменённые/новые/удалённые файлы.
    """
    _ensure_dirs()
    try:
        from langchain_community.vectorstores import FAISS
        from ui_automation.utils import get_hugginface_embedding
    except Exception as e:
        print(f"[vault] reindex skipped: {e}", flush=True)
        return 0

    embeddings = get_hugginface_embedding()
    index_file = os.path.join(INDEX_DIR, "index.faiss")
    meta = {} if full else _load_meta()

    current: dict[str, float] = {}
    notes: List[dict] = []
    for abs_p, rel_p in _iter_md_files():
        current[rel_p] = os.path.getmtime(abs_p)
        if full or meta.get(rel_p) != current[rel_p]:
            n = _load_note(abs_p, rel_p)
            if n:
                notes.append(n)

    removed = [p for p in meta.keys() if p not in current]

    # Если нет изменений — ничего не делаем.
    if not full and not notes and not removed and os.path.isfile(index_file):
        return 0

    # Простая стратегия: при любом удалении — полная пересборка (FAISS плохо удаляет по метаданным).
    if full or removed or not os.path.isfile(index_file):
        all_docs = []
        for abs_p, rel_p in _iter_md_files():
            n = _load_note(abs_p, rel_p)
            if n:
                all_docs.extend(_note_to_docs(n))
        if not all_docs:
            # Пустой vault — снесём старый индекс, если был.
            for f in ("index.faiss", "index.pkl"):
                p = os.path.join(INDEX_DIR, f)
                if os.path.isfile(p):
                    os.remove(p)
            _save_meta(current)
            return 0
        db = FAISS.from_documents(all_docs, embeddings)
        db.save_local(INDEX_DIR)
        _save_meta(current)
        return len(all_docs)

    # Инкрементально: только добавление новых/изменённых (старые версии остаются «мусором»
    # до следующей полной пересборки — при save_scenario/save_experience это приемлемо).
    new_docs = []
    for n in notes:
        new_docs.extend(_note_to_docs(n))
    if not new_docs:
        _save_meta(current)
        return 0
    db = FAISS.load_local(INDEX_DIR, embeddings, allow_dangerous_deserialization=True)
    fresh = FAISS.from_documents(new_docs, embeddings)
    db.merge_from(fresh)
    db.save_local(INDEX_DIR)
    _save_meta(current)
    return len(new_docs)


# ─────────────────────────────────────────────────────────────────────────────
#  Поиск
# ─────────────────────────────────────────────────────────────────────────────

def _load_db():
    try:
        from langchain_community.vectorstores import FAISS
        from ui_automation.utils import get_hugginface_embedding
    except Exception:
        return None
    if not os.path.isfile(os.path.join(INDEX_DIR, "index.faiss")):
        return None
    try:
        return FAISS.load_local(
            INDEX_DIR, get_hugginface_embedding(),
            allow_dangerous_deserialization=True,
        )
    except Exception:
        return None


def search(query: str, k: int = 5, folder: Optional[str] = None) -> list:
    """Семантический поиск. Если folder указан — фильтр по папке."""
    db = _load_db()
    if db is None or not query.strip():
        return []
    try:
        if folder:
            return db.similarity_search(
                query, k=k,
                filter={"folder": folder},
            )
        return db.similarity_search(query, k=k)
    except Exception:
        return []


def search_grouped(query: str, k_per_folder: int = 2) -> dict:
    """Возвращает {folder: [Document,...]} с приоритетами FOLDER_PRIORITY."""
    db = _load_db()
    if db is None or not query.strip():
        return {}
    out: dict[str, list] = {}
    for folder in sorted(FOLDER_PRIORITY, key=lambda f: FOLDER_PRIORITY[f]):
        try:
            docs = db.similarity_search(query, k=k_per_folder, filter={"folder": folder})
        except Exception:
            docs = []
        if docs:
            out[folder] = docs
    return out


def match_scenario_by_trigger(query: str) -> Optional[dict]:
    """Точный матч по фразам-триггерам (case-insensitive substring).
    Возвращает загруженную заметку или None.
    """
    q = query.strip().lower()
    if not q:
        return None
    scen_dir = os.path.join(VAULT_DIR, "Scenarios")
    if not os.path.isdir(scen_dir):
        return None
    for fn in os.listdir(scen_dir):
        if not fn.lower().endswith(".md"):
            continue
        abs_p = os.path.join(scen_dir, fn)
        rel_p = f"Scenarios/{fn}"
        n = _load_note(abs_p, rel_p)
        if not n:
            continue
        triggers = n["frontmatter"].get("triggers") or []
        if not isinstance(triggers, list):
            continue
        for t in triggers:
            t_norm = str(t).strip().lower()
            if t_norm and t_norm in q:
                return n
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  Запись заметок
# ─────────────────────────────────────────────────────────────────────────────

def save_scenario(name: str, triggers: list, body: str, tags: Optional[list] = None) -> str:
    """Создаёт/перезаписывает сценарий в vault/Scenarios/. Возвращает abs-путь."""
    _ensure_dirs()
    fm = {
        "name": name,
        "triggers": list(triggers or []),
        "tags": list(tags or ["scenario"]),
    }
    fname = f"{_slug(name)}.md"
    path = os.path.join(VAULT_DIR, "Scenarios", fname)
    content = _dump_frontmatter(fm) + "\n" + (body or "").strip() + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    try:
        reindex(full=False)
    except Exception:
        pass
    return path


def save_experience(
    task: str,
    result: str,
    agent_types: Optional[list] = None,
    app_names: Optional[list] = None,
) -> Optional[str]:
    """Сохраняет .md с описанием выполненной задачи в vault/Experience/."""
    if not task or not result:
        return None
    _ensure_dirs()
    flat_agents: List[str] = []
    for a in (agent_types or []):
        if isinstance(a, list):
            flat_agents.extend(str(x) for x in a)
        else:
            flat_agents.append(str(a))
    date = time.strftime("%Y-%m-%d")
    fm = {
        "name": task[:80],
        "tags": ["experience"] + flat_agents,
        "date": date,
        "agents": flat_agents,
        "apps": list(app_names or []),
    }
    fname = f"{date}-{_slug(task)}.md"
    path = os.path.join(VAULT_DIR, "Experience", fname)
    if os.path.exists(path):
        fname = f"{date}-{_slug(task)}-{int(time.time())}.md"
        path = os.path.join(VAULT_DIR, "Experience", fname)
    body = f"## Запрос\n\n{task}\n\n## Результат\n\n{result}\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(_dump_frontmatter(fm) + "\n" + body)
    try:
        reindex(full=False)
    except Exception:
        pass
    return path


def save_web_search(
    query: str,
    results: list,
    source: str = "tavily_search",
) -> Optional[str]:
    """Сохраняет результаты веб-поиска в vault/WebSearch/ как .md-заметку.

    results — список dict'ов с ключами title, url, content (как у tavily).
    Заметка содержит агрегированный список ссылок и сниппетов; индексатор
    подхватит её при следующем reindex.
    """
    if not query or not results:
        return None
    _ensure_dirs()
    date = time.strftime("%Y-%m-%d")
    urls = [(r.get("url") or "").strip() for r in results if r.get("url")]
    fm = {
        "name": query[:80],
        "tags": ["web-search", source],
        "date": date,
        "source": source,
        "query": query,
        "urls": urls[:20],
    }
    fname = f"{date}-{_slug(query)}.md"
    path = os.path.join(VAULT_DIR, "WebSearch", fname)
    if os.path.exists(path):
        fname = f"{date}-{_slug(query)}-{int(time.time())}.md"
        path = os.path.join(VAULT_DIR, "WebSearch", fname)

    lines = [f"## Запрос\n\n{query}\n", "## Результаты\n"]
    for r in results:
        title = (r.get("title") or "").strip() or (r.get("url") or "").strip()
        url = (r.get("url") or "").strip()
        snippet = (r.get("content") or "").strip()
        if not (title or url):
            continue
        if url:
            lines.append(f"### [{title}]({url})")
        else:
            lines.append(f"### {title}")
        if snippet:
            lines.append("")
            lines.append(snippet[:1200])
        lines.append("")
    body = "\n".join(lines)
    with open(path, "w", encoding="utf-8") as f:
        f.write(_dump_frontmatter(fm) + "\n" + body)
    try:
        reindex(full=False)
    except Exception:
        pass
    return path


def save_document(original_path: str, extracted_text: str, tags: Optional[list] = None) -> Optional[str]:
    """Создаёт .md-обёртку рядом с оригиналом в vault/Attachments/ и индексирует её.
    original_path — абсолютный путь к уже скопированному в Attachments файлу.
    """
    if not os.path.isfile(original_path):
        return None
    _ensure_dirs()
    base = os.path.splitext(os.path.basename(original_path))[0]
    fm = {
        "name": base,
        "tags": list(tags or ["document"]),
        "source": original_path.replace("\\", "/"),
        "ingested": time.strftime("%Y-%m-%d %H:%M"),
    }
    md_path = os.path.join(VAULT_DIR, "Attachments", f"{base}.md")
    body = "## Извлечённый текст\n\n" + (extracted_text.strip() or "_(пусто)_") + "\n"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(_dump_frontmatter(fm) + "\n" + body)
    try:
        reindex(full=False)
    except Exception:
        pass
    return md_path


# ─────────────────────────────────────────────────────────────────────────────
#  CRUD для UI
# ─────────────────────────────────────────────────────────────────────────────

def list_notes(folder: str) -> list:
    """Список заметок в папке: [{rel_path, name, frontmatter, preview, mtime}, ...]."""
    folder_dir = os.path.join(VAULT_DIR, folder)
    if not os.path.isdir(folder_dir):
        return []
    out = []
    for fn in sorted(os.listdir(folder_dir)):
        if not fn.lower().endswith(".md"):
            continue
        abs_p = os.path.join(folder_dir, fn)
        rel_p = f"{folder}/{fn}"
        n = _load_note(abs_p, rel_p)
        if not n:
            continue
        preview = (n["body"].strip().splitlines() or [""])[0][:200]
        out.append({
            "rel_path": rel_p,
            "name": n["name"],
            "frontmatter": n["frontmatter"],
            "preview": preview,
            "mtime": n["mtime"],
        })
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out


def read_note(rel_path: str) -> Optional[dict]:
    rel_path = rel_path.replace("\\", "/")
    abs_p = os.path.normpath(os.path.join(VAULT_DIR, rel_path))
    if not abs_p.startswith(os.path.abspath(VAULT_DIR)):
        return None
    if not os.path.isfile(abs_p):
        return None
    n = _load_note(abs_p, rel_path)
    if not n:
        return None
    return {
        "rel_path": rel_path,
        "name": n["name"],
        "frontmatter": n["frontmatter"],
        "body": n["body"],
    }


def delete_note(rel_path: str) -> bool:
    rel_path = rel_path.replace("\\", "/")
    abs_p = os.path.normpath(os.path.join(VAULT_DIR, rel_path))
    if not abs_p.startswith(os.path.abspath(VAULT_DIR)):
        return False
    if not os.path.isfile(abs_p):
        return False
    os.remove(abs_p)
    # Для Attachments удаляем, если есть
    if rel_path.startswith("Attachments/"):
        base = os.path.splitext(abs_p)[0]
        for ext in (".pdf", ".docx", ".xlsx", ".pptx", ".txt", ".md"):
            orig = base + ext
            if ext != ".md" and os.path.isfile(orig):
                try:
                    os.remove(orig)
                except OSError:
                    pass
    try:
        reindex(full=True)  # удаление требует полной пересборки
    except Exception:
        pass
    return True


# ─────────────────────────────────────────────────────────────────────────────
#  Форматирование для контекста LLM
# ─────────────────────────────────────────────────────────────────────────────

def format_context(query: str, k_per_folder: int = 2, max_chars: int = 2500) -> str:
    """Собирает блок `[Релевантный опыт]` для вставки в подсказку агента."""
    grouped = search_grouped(query, k_per_folder=k_per_folder)
    if not grouped:
        return ""
    labels = {
        "Scenarios": "Сценарий",
        "Experience": "Опыт",
        "Knowledge": "Знание",
        "Attachments": "Документ",
    }
    lines: List[str] = []
    total = 0
    for folder in sorted(grouped, key=lambda f: FOLDER_PRIORITY.get(f, 99)):
        for doc in grouped[folder]:
            text = doc.page_content.strip()
            if not text:
                continue
            rel = doc.metadata.get("rel_path", "")
            line = f"[{labels.get(folder, folder)} · {rel}] {text}"
            if total + len(line) > max_chars:
                return "\n".join(lines)
            lines.append(line)
            total += len(line)
    return "\n".join(lines)
