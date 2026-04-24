# Компас - AI Assistant

Локальный голосовой/текстовый ассистент для Windows с агентной архитектурой
(ReAct: Perceiver → Planner → Worker → Verifier).

## Возможности

- Управление ПК: запуск приложений, открытие файлов/ссылок/закладок, громкость, медиа.
- UI-автоматизация Windows через UIA (кнопки, меню, поля ввода, окна).
- Автоматизация браузера через собственное расширение (WebSocket-мост).
- Работа с Microsoft Office через COM: Word, Excel, PowerPoint, Outlook.
- Vision-агент: скриншоты + multimodal LLM для верификации и «посмотри на экран».
- Web-поиск (Tavily), чтение страниц.
- RAG: опыт прошлых задач, knowledge base, демонстрации, документы.
- Два транспорта: локальный чат (CLI/веб-UI) и MCP-сервер (stdio) для внешних клиентов.

## Быстрый старт

```bash
pip install -r requirements

# 1. Запусти локальный LLM-сервер (llama.cpp / Ollama / любой OpenAI-совместимый)
#    на http://localhost:8000/v1, либо используй желаемого провайдера из списка.

# 2. Построй RAG-индекс (один раз)
python rag_indexer.py

# 3. Запусти ассистента
python main.py              # интерактивный чат
python main.py -r "открой chrome"   # единичный запрос
python main.py --web        # веб-UI в браузере
python main.py --app        # десктоп-окно (pywebview)
python main.py --mcp-only   # только MCP-сервер (stdio)
python main.py --list-tools # список всех доступных тулов
```

## Конфигурация

`.env` в корне проекта:

```
TAVILY_API_KEY=...#https://app.tavily.com/home
HF_HUB_OFFLINE=1
```

Провайдер, модель, vision-модель можно переключать на лету через веб-UI
(`/api/config`, `/api/models`) — без перезапуска. Поддерживаются
llama.cpp, Ollama, OpenAI, Anthropic, Gemini, OpenRouter, DeepSeek, Yandex AI Studio.

## Архитектура (кратко)

| Компонент | Ответственность |
|-----------|-----------------|
| `Planner` | Генерирует следующий шаг (`StepSpec`) по текущему восприятию. |
| `Perceiver` | Собирает состояние: UIA-дерево активного окна / DOM-снимок / summary. |
| `Controller` | ReAct-цикл: perceive → plan → act → verify. |
| Workers | `ToolAgent`, `BrowserAgent`, `VisionAgent`, чат-воркер. |
| `Verifier` | Vision-LLM сверяет `expected_outcome` со скриншотом. |
| `TraceStore` | JSONL-логи в `traces/YYYY-MM-DD.jsonl` + метрики. |

## Тулы

Каждый тул — функция с `@mcp.tool` в `mcp_modules/tools_*.py`. Тот же код
работает и как MCP-инструмент (stdio), и как tool-call в чат-режиме.

Группы: `tools_apps`, `tools_browser`, `tools_files`, `tools_office`,
`tools_system`, `tools_uiautomation`, `tools_vision`, `tools_web`, `tools_weather`.

Добавить тул:
1. Новая `@mcp.tool`-функция в подходящем `tools_*.py`.
2. Добавить модуль в `TOOLS_MODULES` нужного воркера
   (`tool_agent.py` / `browser_agent.py` / `vision_agent.py`).
3. Добавить модуль в `run_mcp_server()` и `list_tools()` в `main.py`.

## Браузерное расширение

Chrome-расширение в `browser_extension/extension/` + WebSocket-сервер на
`ws://127.0.0.1:9009` (`browser_extension/ws_server.py`). Мост стартует в
daemon-потоке при запуске чата.

## Структура

```
main.py                    — точка входа, режимы запуска
web_server.py              — HTTP/SSE сервер для UI
mcp_modules/tools_*.py     — все тулы
ui_automation/agents/      — Planner / Perceiver / Controller / Verifier / Workers
ui_automation/rag/         — FAISS-индексы опыта и знаний
ui_automation/llm_config.py — runtime-конфиг LLM, список моделей
browser_extension/         — Chrome-расширение + WS-мост
vectordb/                  — RAG-индексы (experience, knowledge, demonstration)
traces/                    — JSONL-логи запусков
webui/                     — HTML/CSS/JS веб-UI
```
