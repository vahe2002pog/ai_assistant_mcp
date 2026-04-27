"""
ResponseFormatter — структурирует итоговый ответ ассистента.

Выход:
  AssistantResponse(
      voice="короткая фраза для озвучки",
      screen=ScreenData(blocks=[...])
  )

Типы блоков: text | list | table | links | files
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import openai

from ui_automation import llm_config as _llm

# ─── Модели данных ────────────────────────────────────────────────────────────

@dataclass
class TextBlock:
    text: str
    title: Optional[str] = None
    type: str = "text"

@dataclass
class ListBlock:
    items: List[str]
    title: Optional[str] = None
    type: str = "list"

@dataclass
class TableBlock:
    rows: List[Dict[str, Any]]
    title: Optional[str] = None
    type: str = "table"

@dataclass
class LinksBlock:
    links: List[str]
    title: Optional[str] = None
    type: str = "links"

@dataclass
class FilesBlock:
    file_paths: List[str]
    title: Optional[str] = None
    type: str = "files"

Block = TextBlock | ListBlock | TableBlock | LinksBlock | FilesBlock

@dataclass
class ScreenData:
    blocks: List[Block] = field(default_factory=list)

@dataclass
class AssistantResponse:
    voice: str
    screen: ScreenData = field(default_factory=ScreenData)
    used_sources: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        blocks = []
        for b in self.screen.blocks:
            d = {"type": b.type}
            if b.title:
                d["title"] = b.title
            if isinstance(b, TextBlock):
                d["text"] = b.text
            elif isinstance(b, ListBlock):
                d["items"] = b.items
            elif isinstance(b, TableBlock):
                d["rows"] = b.rows
            elif isinstance(b, LinksBlock):
                d["links"] = b.links
            elif isinstance(b, FilesBlock):
                d["file_paths"] = b.file_paths
            blocks.append(d)
        out = {"voice": self.voice, "screen": {"blocks": blocks}}
        if self.used_sources:
            out["used_sources"] = list(self.used_sources)
        return out


# ─── Системный промпт форматтера ──────────────────────────────────────────────

_FORMAT_SYSTEM = """/no_think
Ты — форматтер ответов ассистента.
Тебе дан сырой результат выполнения задачи и запрос пользователя.
Тебе нужно сформировать финальный структурированный ответ.

Верни ТОЛЬКО валидный JSON без пояснений:
{
  "voice": "...",
  "screen": {
    "blocks": [...]
  },
  "used_sources": ["https://..."]
}

Правила:
- voice: одно предложение до 10 слов — главный ответ на запрос. Это то, что будет озвучено.
- screen.blocks: массив блоков. Оставь пустым [], если нет явных структурированных данных для отображения.

Типы блоков:
  {"type": "text", "title": "...", "text": "длинный текст"}
  {"type": "list", "title": "...", "items": ["элемент1", "элемент2"]}
  {"type": "table", "title": "...", "rows": [{"Колонка1": "значение", "Колонка2": "значение"}]}
  {"type": "links", "title": "...", "links": ["https://..."]}
  {"type": "files", "title": "...", "file_paths": ["C:\\\\path\\\\file.txt"]}

- title — необязательный короткий заголовок блока.
- Используй list для перечислений, table для табличных данных, links для URL, files для путей к файлам.
- Не дублируй voice в блоках экрана.
- Если результат — простое подтверждение (сделано, открыто, запущено), blocks = [].
- ВАЖНО: не теряй содержимое. Если в сыром результате есть факты, таблицы, перечисления,
  ссылки — они должны попасть в blocks целиком. Нельзя сокращать или выбрасывать данные;
  переформатируй в подходящие блоки (list/table/links/text), но сохраняй всё.

used_sources:
- Список URL ИЗ предоставленного раздела «Доступные источники», ИЗ КОТОРЫХ ты реально
  взял хотя бы один факт, попавший в voice или blocks.
- НЕ включай URL, чьи факты ты не использовал (например, страница из выдачи оказалась
  не по теме, словарём или дублем).
- Если веб-источников не было — верни [].
- Не выдумывай URL: бери только из списка ниже.
"""


# ─── Форматтер ────────────────────────────────────────────────────────────────

class ResponseFormatter:
    """Превращает сырой строковый результат агента в AssistantResponse."""

    def __init__(self) -> None:
        pass

    def format(self, raw: str, user_query: str = "",
               available_sources: Optional[List[Dict[str, str]]] = None) -> AssistantResponse:
        raw = self._sanitize(raw)
        available_sources = available_sources or []

        """
        Форматирует сырой результат в структурированный ответ.

        Args:
            raw: сырой текст от агента
            user_query: исходный запрос пользователя (для контекста)

        Returns:
            AssistantResponse с полями voice и screen.blocks
        """
        # Если ответ уже развёрнутый/структурированный (markdown-таблицы, заголовки,
        # длинные списки) — не переформатируем его через LLM: локальная модель
        # склонна сжимать такие ответы и терять данные. Отдаём raw как текстовый
        # блок и просим LLM только короткую фразу для voice.
        if self._is_structured(raw):
            return self._passthrough(raw, user_query, available_sources)

        sources_block = ""
        if available_sources:
            lines = []
            for s in available_sources:
                t = (s.get("title") or "").strip()
                u = s.get("url") or ""
                if not u:
                    continue
                lines.append(f"- {u}" + (f" — {t}" if t else ""))
            if lines:
                sources_block = "\nДоступные источники (только из этого списка можно ставить в used_sources):\n" + "\n".join(lines) + "\n"

        prompt = (
            f"Запрос пользователя: {user_query}\n\n"
            f"Результат выполнения:\n{raw}\n"
            f"{sources_block}\n"
            "Сформируй структурированный ответ по правилам. JSON:"
        )

        print("\n" + "=" * 20 + " [ResponseFormatter] RAW INPUT " + "=" * 20, flush=True)
        print(raw, flush=True)
        print("=" * 70, flush=True)

        try:
            resp = _llm.get_client().chat.completions.create(
                model=_llm.get_model(),
                messages=[
                    {"role": "system", "content": _FORMAT_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=16384,
                extra_body=_llm.get_extra_body(),
            )
            choice = resp.choices[0]
            content = choice.message.content or ""
            finish = getattr(choice, "finish_reason", None)
            print(f"\n===== [ResponseFormatter] LLM OUTPUT (finish_reason={finish}) =====",
                  flush=True)
            print(content, flush=True)
            print("=" * 70, flush=True)
            # Если модель упёрлась в лимит — JSON почти наверняка обрезан,
            # лучше отдать полный raw, чем потерять данные.
            if finish == "length":
                return self._fallback(raw)
            return self._parse(content.strip(), raw)
        except Exception as e:
            print(f"[ResponseFormatter] LLM call failed: {e!r}", flush=True)
            return self._fallback(raw)

    def _parse(self, content: str, raw: str) -> AssistantResponse:
        """Парсит JSON-ответ LLM в AssistantResponse."""
        # Извлекаем JSON-объект из ответа
        start = content.find("{")
        end = content.rfind("}") + 1
        if start == -1 or end == 0:
            return self._fallback(raw)

        try:
            data = json.loads(content[start:end])
        except json.JSONDecodeError:
            return self._fallback(raw)

        voice = str(data.get("voice", raw)).strip()
        screen_data = data.get("screen", {})
        raw_blocks = screen_data.get("blocks", []) if isinstance(screen_data, dict) else []

        blocks: List[Block] = []
        for b in raw_blocks:
            if not isinstance(b, dict):
                continue
            block = self._parse_block(b)
            if block is not None:
                blocks.append(block)

        used = []
        for u in data.get("used_sources", []) or []:
            if isinstance(u, str) and u.startswith(("http://", "https://")):
                used.append(u)

        return AssistantResponse(voice=voice, screen=ScreenData(blocks=blocks),
                                 used_sources=used)

    def _parse_block(self, b: Dict) -> Optional[Block]:
        """Преобразует словарь блока в типизированный объект."""
        t = b.get("type", "")
        title = b.get("title") or None

        if t == "text":
            text = b.get("text", "")
            if not text:
                return None
            return TextBlock(text=text, title=title)

        if t == "list":
            items = b.get("items", [])
            if not items:
                return None
            return ListBlock(items=[str(i) for i in items], title=title)

        if t == "table":
            rows = b.get("rows", [])
            if not rows:
                return None
            return TableBlock(rows=rows, title=title)

        if t == "links":
            links = b.get("links", [])
            if not links:
                return None
            return LinksBlock(links=[str(l) for l in links], title=title)

        if t == "files":
            file_paths = b.get("file_paths", [])
            if not file_paths:
                return None
            return FilesBlock(file_paths=[str(p) for p in file_paths], title=title)

        return None

    @staticmethod
    def _sanitize(raw: str) -> str:
        """Вырезает служебный мета-хвост, который иногда приклеивает LLM:
        строки `task_done: ...`, трейлинг-комментарии про источники данных,
        висячие разделители `---`."""
        if not raw:
            return raw
        s = raw

        # 1) Всё от первого `task_done:` до конца.
        m = re.search(r"(?im)^[ \t]*task_done\s*:.*$", s)
        if m:
            s = s[:m.start()]

        # 2) Трейлинг-параграф-пояснение «Собранные данные …» / «Источник …» и т.п.
        s = re.sub(
            r"(?ims)\n+(?:---+\s*\n+)?"
            r"(?:собранн[аыео]\w*\s+данны\w*|источник\w*|данны\w+\s+получен\w+)"
            r"[^\n]*(?:\n(?!\n)[^\n]*)*\s*$",
            "",
            s,
        )

        # 3) Висячие разделители в конце (несколько `---` подряд).
        s = re.sub(r"(?m)(?:^\s*-{3,}\s*$\n?)+\Z", "", s)

        return s.rstrip() + ("\n" if raw.endswith("\n") else "")

    @staticmethod
    def _is_structured(raw: str) -> bool:
        """Эвристика: ответ уже оформлен (таблица/заголовки/длинный список) или просто длинный."""
        if not raw:
            return False
        r = raw.strip()
        if len(r) >= 600:
            return True
        has_table = ("|" in r and r.count("\n") >= 2 and "---" in r)
        has_heading = any(line.lstrip().startswith("#") for line in r.splitlines())
        bullet_count = sum(1 for line in r.splitlines()
                           if line.lstrip().startswith(("- ", "* ", "• "))
                           or re.match(r"^\s*\d+[\.\)]\s", line))
        return has_table or has_heading or bullet_count >= 4

    def _passthrough(self, raw: str, user_query: str,
                     available_sources: Optional[List[Dict[str, str]]] = None) -> AssistantResponse:
        """Разбираем markdown в нативные блоки (таблицы/списки/текст).
        Для длинных/уже отформатированных ответов LLM-форматтер не дёргаем,
        поэтому used_sources вычисляем эвристически (sources.filter_used)."""
        voice = self._short_voice(raw, user_query) or self._first_sentence(raw)
        blocks = self._markdown_to_blocks(raw)
        if not blocks:
            blocks = [TextBlock(text=raw.strip())]
        used: List[str] = []
        if available_sources:
            try:
                from ui_automation import sources as _sources
                joined = "\n".join(
                    [voice] +
                    [b.text for b in blocks if isinstance(b, TextBlock)]
                )
                used = _sources.filter_used(joined)
            except Exception:
                used = []
        return AssistantResponse(voice=voice, screen=ScreenData(blocks=blocks),
                                 used_sources=used)

    @staticmethod
    def _markdown_to_blocks(raw: str) -> List[Block]:
        """Минимальный markdown → blocks: pipe-table → TableBlock, группы текста → TextBlock."""
        lines = raw.splitlines()
        blocks: List[Block] = []
        text_buf: List[str] = []

        def flush_text():
            if not text_buf:
                return
            t = "\n".join(text_buf).strip()
            text_buf.clear()
            if t:
                blocks.append(TextBlock(text=t))

        i = 0
        n = len(lines)
        while i < n:
            line = lines[i]
            # pipe-table: строка с |, следом разделитель | --- | --- |
            if ("|" in line and i + 1 < n
                    and re.match(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$", lines[i + 1])):
                flush_text()
                header = [c.strip() for c in line.strip().strip("|").split("|")]
                rows: List[Dict[str, Any]] = []
                j = i + 2
                while j < n and "|" in lines[j] and lines[j].strip():
                    cells = [c.strip() for c in lines[j].strip().strip("|").split("|")]
                    if len(cells) < len(header):
                        cells += [""] * (len(header) - len(cells))
                    rows.append({header[k]: cells[k] for k in range(len(header))})
                    j += 1
                if rows:
                    blocks.append(TableBlock(rows=rows))
                i = j
                continue
            text_buf.append(line)
            i += 1

        flush_text()
        return blocks

    def _short_voice(self, raw: str, user_query: str) -> str:
        """Один короткий запрос к LLM только ради voice-фразы (до 10 слов)."""
        try:
            resp = _llm.get_client().chat.completions.create(
                model=_llm.get_model(),
                messages=[
                    {"role": "system", "content":
                        "/no_think\nВыдай ровно одно предложение до 10 слов — "
                        "краткое резюме ответа для озвучки. Без кавычек, без префиксов."},
                    {"role": "user", "content":
                        f"Запрос: {user_query}\n\nОтвет:\n{raw[:3000]}"},
                ],
                temperature=0.1,
                max_tokens=60,
                extra_body=_llm.get_extra_body(),
            )
            txt = (resp.choices[0].message.content or "").strip()
            # снимаем возможные кавычки и обрезаем до одного предложения
            txt = txt.strip().strip('"\'«»`').splitlines()[0] if txt else ""
            return txt[:200]
        except Exception:
            return ""

    @staticmethod
    def _first_sentence(raw: str) -> str:
        s = re.split(r"[.!?\n]", raw.strip())[0].strip()
        return (s[:200] if s else raw[:200]) or raw

    def _fallback(self, raw: str) -> AssistantResponse:
        """Фолбэк без LLM — короткое предложение в voice, полный raw в text-блоке."""
        sentence = re.split(r"[.!?\n]", raw.strip())[0].strip()
        voice = (sentence[:200] if sentence else raw[:200]) or raw

        # Всегда кладём полный текст в screen, чтобы ничего не терять.
        blocks: List[Block] = [TextBlock(text=raw)] if raw.strip() else []
        return AssistantResponse(voice=voice, screen=ScreenData(blocks=blocks))
