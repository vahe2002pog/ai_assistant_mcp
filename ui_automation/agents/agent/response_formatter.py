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

_API_BASE  = os.environ.get("API_BASE",  "http://localhost:8000/v1")
_API_KEY   = os.environ.get("API_KEY",   "llama")
_API_MODEL = os.environ.get("API_MODEL", "Qwen3.5-9B-abliterated-vision-Q4_K_M")
_NO_THINK  = {"chat_template_kwargs": {"enable_thinking": False}}

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
        return {"voice": self.voice, "screen": {"blocks": blocks}}


# ─── Системный промпт форматтера ──────────────────────────────────────────────

_FORMAT_SYSTEM = """Ты — форматтер ответов ассистента.
Тебе дан сырой результат выполнения задачи и запрос пользователя.
Тебе нужно сформировать финальный структурированный ответ.

Верни ТОЛЬКО валидный JSON без пояснений:
{
  "voice": "...",
  "screen": {
    "blocks": [...]
  }
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
"""


# ─── Форматтер ────────────────────────────────────────────────────────────────

class ResponseFormatter:
    """Превращает сырой строковый результат агента в AssistantResponse."""

    def __init__(self) -> None:
        self._client = openai.OpenAI(base_url=_API_BASE, api_key=_API_KEY)

    def format(self, raw: str, user_query: str = "") -> AssistantResponse:
        """
        Форматирует сырой результат в структурированный ответ.

        Args:
            raw: сырой текст от агента
            user_query: исходный запрос пользователя (для контекста)

        Returns:
            AssistantResponse с полями voice и screen.blocks
        """
        prompt = (
            f"Запрос пользователя: {user_query}\n\n"
            f"Результат выполнения:\n{raw}\n\n"
            "Сформируй структурированный ответ по правилам. JSON:"
        )

        try:
            resp = self._client.chat.completions.create(
                model=_API_MODEL,
                messages=[
                    {"role": "system", "content": _FORMAT_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=1024,
                extra_body=_NO_THINK,
            )
            content = resp.choices[0].message.content.strip()
            return self._parse(content, raw)
        except Exception:
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

        voice = str(data.get("voice", raw[:80])).strip()
        screen_data = data.get("screen", {})
        raw_blocks = screen_data.get("blocks", []) if isinstance(screen_data, dict) else []

        blocks: List[Block] = []
        for b in raw_blocks:
            if not isinstance(b, dict):
                continue
            block = self._parse_block(b)
            if block is not None:
                blocks.append(block)

        return AssistantResponse(voice=voice, screen=ScreenData(blocks=blocks))

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

    def _fallback(self, raw: str) -> AssistantResponse:
        """Минимальный fallback без LLM — берём первое предложение как voice."""
        sentence = re.split(r"[.!?\n]", raw.strip())[0].strip()
        voice = sentence[:120] if sentence else raw[:120]

        # Эвристика: если результат длинный — показываем как text-блок
        blocks: List[Block] = []
        if len(raw) > 150:
            blocks.append(TextBlock(text=raw))

        return AssistantResponse(voice=voice, screen=ScreenData(blocks=blocks))
