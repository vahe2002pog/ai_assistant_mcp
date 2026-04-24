"""Единая runtime-конфигурация LLM: провайдер, базовый URL, модель.

Позволяет переключать сервер (llama.cpp / Ollama) и модель на лету — все
агенты берут клиента и имя модели через эти функции, поэтому изменения
вступают в силу сразу, без перезапуска.

Состояние хранится в llm_config.json в корне проекта.
"""
from __future__ import annotations

import json
import os
import threading
import urllib.parse
import urllib.request
from typing import List, Optional

import openai

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STATE_PATH = os.path.join(_ROOT, "llm_config.json")

PROVIDERS = {
    "llamacpp": {
        "label": "llama.cpp",
        "base_url": "http://localhost:8000/v1",
        "api_key":  "llama",
        "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
    },
    "ollama": {
        "label": "Ollama",
        "base_url": "http://localhost:11434/v1",
        "api_key":  "ollama",
        "extra_body": {"think": False, "chat_template_kwargs": {"enable_thinking": False}},
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "api_key":  "",
        "extra_body": {},
    },
    "anthropic": {
        "label": "Anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "api_key":  "",
        "extra_body": {},
    },
    "gemini": {
        "label": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "api_key":  "",
        "extra_body": {},
    },
    "openrouter": {
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key":  "",
        "extra_body": {},
    },
    "mistral": {
        "label": "Mistral",
        "base_url": "https://api.mistral.ai/v1",
        "api_key":  "",
        "extra_body": {},
    },
    "groq": {
        "label": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "api_key":  "",
        "extra_body": {},
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "api_key":  "",
        "extra_body": {},
    },
    "yandex": {
        "label": "Yandex AI Studio",
        "base_url": "https://ai.api.cloud.yandex.net/v1",
        "api_key":  "",
        "extra_body": {},
    },
    "custom": {
        "label": "Свой (OpenAI-совместимый)",
        "base_url": "",
        "api_key":  "",
        "extra_body": {},
    },
}

_lock = threading.RLock()
_state: Optional[dict] = None
_client: Optional[openai.OpenAI] = None
_client_key: Optional[tuple] = None
_vision_client: Optional[openai.OpenAI] = None
_vision_client_key: Optional[tuple] = None


def _load() -> dict:
    global _state
    if _state is not None:
        return _state
    s: dict = {}
    try:
        with open(_STATE_PATH, "r", encoding="utf-8") as f:
            s = json.load(f) or {}
    except Exception:
        s = {}
    prov_name = s.get("provider") or os.environ.get("LLM_PROVIDER", "llamacpp")
    if prov_name not in PROVIDERS:
        prov_name = "llamacpp"
    prov = PROVIDERS[prov_name]

    # Миграция: если в llm_config.json остался api_key/folder — переносим в БД и
    # очищаем файл (секреты больше не хранятся в открытом виде).
    legacy_key = s.pop("api_key", None)
    legacy_folder = s.pop("folder", None)
    if legacy_key or legacy_folder:
        try:
            import database as _db
            _db.provider_key_set(prov_name,
                                 api_key=legacy_key or None,
                                 folder=legacy_folder if legacy_folder is not None else None)
        except Exception:
            pass

    s.setdefault("provider", prov_name)
    s.setdefault("base_url", os.environ.get("API_BASE", prov["base_url"]))
    s.setdefault("model",    os.environ.get("API_MODEL", "Qwen3.5-9B-abliterated-vision-Q4_K_M"))
    s.setdefault("vision_model", os.environ.get("API_VISION_MODEL", ""))
    # vision_provider/vision_base_url: пусто = использовать основные
    s.setdefault("vision_provider", "")
    s.setdefault("vision_base_url", "")

    # api_key/folder — из БД (шифрованно); для локальных провайдеров подставляем
    # технический ключ по умолчанию ("llama"/"ollama"), чтобы OpenAI SDK не ругался.
    api_key, folder = _provider_secrets(prov_name)
    if not api_key:
        api_key = os.environ.get("API_KEY") or prov["api_key"]
    if not folder:
        folder = os.environ.get("YANDEX_CLOUD_FOLDER", "")
    s["api_key"] = api_key
    s["folder"] = folder

    _state = s
    # Зеркалим в env, чтобы старый код, читающий os.environ, видел актуальные значения.
    os.environ["API_BASE"]  = s["base_url"]
    os.environ["API_KEY"]   = s["api_key"]
    os.environ["API_MODEL"] = s["model"]

    # Перезаписываем файл без секретов (однократно после миграции).
    if legacy_key is not None or legacy_folder is not None:
        _persist()
    return s


def _provider_secrets(provider: str) -> tuple:
    try:
        import database as _db
        return _db.provider_key_get(provider)
    except Exception:
        return "", ""


def _persist() -> None:
    """Пишет в JSON только несекретные поля (провайдер/URL/модель).
    api_key и folder живут в БД под DPAPI-шифром."""
    try:
        safe = {k: v for k, v in (_state or {}).items() if k not in ("api_key", "folder")}
        with open(_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(safe, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def get() -> dict:
    with _lock:
        return dict(_load())


def get_provider() -> str:
    return get()["provider"]


def _yandex_format(model: str, folder: str) -> str:
    """Yandex AI Studio требует `gpt://<folder>/<model>` для OpenAI-совместимого
    эндпойнта. Если имя уже в полном виде (gpt://…, ft://…) — не трогаем."""
    m = (model or "").strip()
    if not m or "://" in m or not folder:
        return m
    return f"gpt://{folder}/{m}"


def get_model() -> str:
    s = get()
    if s.get("provider") == "yandex":
        return _yandex_format(s["model"], s.get("folder", ""))
    return s["model"]


def _vision_view(s: dict) -> dict:
    """Эффективные настройки vision-клиента.
    Если vision_provider пусто — всё берётся из основных настроек.
    Если задан другой провайдер — base_url/api_key/folder/extra_body
    подтягиваются из его дефолтов + сохранённых секретов БД.
    """
    vp = (s.get("vision_provider") or "").strip()
    if not vp or vp == s.get("provider"):
        # Один провайдер на оба клиента.
        return {
            "provider": s.get("provider"),
            "base_url": s["base_url"],
            "api_key":  s["api_key"],
            "folder":   s.get("folder", ""),
            "extra_body": dict(PROVIDERS.get(s.get("provider"), {}).get("extra_body", {})),
        }
    prov = PROVIDERS.get(vp) or PROVIDERS["custom"]
    key, folder = _provider_secrets(vp)
    base = (s.get("vision_base_url") or "").strip() or prov["base_url"]
    if not key:
        key = prov["api_key"]
    return {
        "provider": vp,
        "base_url": base,
        "api_key":  key,
        "folder":   folder or "",
        "extra_body": dict(prov.get("extra_body", {})),
    }


def get_vision_model() -> str:
    """Модель для vision-задач (VisionAgent, Verifier).

    Если задана отдельная `vision_model` — возвращает её; иначе фолбэк на
    основную модель (работает для случая, когда основная и так vision-capable).
    """
    s = get()
    vm = (s.get("vision_model") or "").strip()
    name = vm or s["model"]
    v = _vision_view(s)
    if v["provider"] == "yandex":
        return _yandex_format(name, v["folder"])
    return name


def get_vision_extra_body() -> dict:
    return _vision_view(get())["extra_body"]


def get_vision_client() -> openai.OpenAI:
    """OpenAI-клиент для vision-вызовов. Если vision-провайдер не задан или
    совпадает с основным — возвращает основной клиент (без дублирования)."""
    global _vision_client, _vision_client_key
    with _lock:
        s = _load()
        v = _vision_view(s)
        same_as_main = (v["base_url"] == s["base_url"]
                        and v["api_key"]  == s["api_key"]
                        and (v["folder"] if v["provider"] == "yandex" else "")
                           == (s.get("folder", "") if s.get("provider") == "yandex" else ""))
        if same_as_main:
            # Переиспользуем основной — без отдельного кэша.
            return get_client()
        folder = v["folder"] if v["provider"] == "yandex" else ""
        key = (v["base_url"], v["api_key"], folder)
        if _vision_client is None or _vision_client_key != key:
            kwargs = {"base_url": v["base_url"], "api_key": v["api_key"]}
            if folder:
                kwargs["project"] = folder
            _vision_client = openai.OpenAI(**kwargs)
            _vision_client_key = key
        return _vision_client


def get_base_url() -> str:
    return get()["base_url"]


def get_extra_body() -> dict:
    prov = PROVIDERS.get(get_provider(), PROVIDERS["llamacpp"])
    return dict(prov["extra_body"])


def get_client() -> openai.OpenAI:
    global _client, _client_key
    with _lock:
        s = _load()
        folder = s.get("folder", "") if s.get("provider") == "yandex" else ""
        key = (s["base_url"], s["api_key"], folder)
        if _client is None or _client_key != key:
            kwargs = {"base_url": s["base_url"], "api_key": s["api_key"]}
            if folder:
                kwargs["project"] = folder
            _client = openai.OpenAI(**kwargs)
            _client_key = key
        return _client


def set_config(provider: Optional[str] = None,
               model: Optional[str] = None,
               base_url: Optional[str] = None,
               api_key: Optional[str] = None,
               vision_model: Optional[str] = None,
               folder: Optional[str] = None,
               vision_provider: Optional[str] = None,
               vision_base_url: Optional[str] = None,
               vision_api_key: Optional[str] = None) -> dict:
    """Обновляет конфигурацию. При смене провайдера без явного base_url/api_key
    подставляются дефолты выбранного провайдера."""
    global _client, _client_key, _vision_client, _vision_client_key
    with _lock:
        s = _load()
        if provider and provider in PROVIDERS:
            if provider != s.get("provider"):
                prov = PROVIDERS[provider]
                s["base_url"] = base_url or prov["base_url"]
                # Подтягиваем ранее сохранённые секреты для выбранного провайдера.
                stored_key, stored_folder = _provider_secrets(provider)
                s["api_key"] = api_key or stored_key or prov["api_key"]
                s["folder"] = (folder if folder is not None else stored_folder) or ""
            s["provider"] = provider
        if base_url:
            s["base_url"] = base_url
        if api_key:
            s["api_key"] = api_key
        if model:
            s["model"] = model
        if vision_model is not None:
            s["vision_model"] = vision_model.strip()
        if folder is not None:
            s["folder"] = folder.strip()

        # Vision-провайдер: пусто = использовать основной.
        if vision_provider is not None:
            vp = vision_provider.strip()
            if vp and vp not in PROVIDERS:
                vp = ""
            if vp and vp != s.get("vision_provider"):
                prov = PROVIDERS[vp]
                s["vision_base_url"] = (vision_base_url or "").strip() or prov["base_url"]
            s["vision_provider"] = vp
            if not vp:
                s["vision_base_url"] = ""
        if vision_base_url is not None:
            s["vision_base_url"] = vision_base_url.strip()
        if vision_api_key:
            # Секрет сохраняем под ключом того провайдера, который выбран для vision.
            vp = (s.get("vision_provider") or "").strip()
            if vp:
                try:
                    import database as _db
                    _db.provider_key_set(vp, api_key=vision_api_key)
                except Exception:
                    pass

        # Сохраняем секреты в БД (шифрованно) — только если пользователь что-то
        # ввёл, чтобы не перезаписать технический дефолт ("llama"/"ollama").
        try:
            import database as _db
            cur_prov = s["provider"]
            new_key = api_key if api_key else None
            new_folder = s.get("folder", "") if folder is not None else None
            if new_key or new_folder is not None:
                _db.provider_key_set(cur_prov, api_key=new_key, folder=new_folder)
        except Exception:
            pass

        os.environ["API_BASE"]  = s["base_url"]
        os.environ["API_KEY"]   = s["api_key"]
        os.environ["API_MODEL"] = s["model"]
        _client = None
        _client_key = None
        _vision_client = None
        _vision_client_key = None
        _persist()
        return dict(s)


# Паттерны имён моделей, которые практически всегда поддерживают изображения.
_VISION_NAME_PATTERNS = (
    "vision", "vl-", "-vl", "llava", "bakllava", "moondream", "pixtral",
    "minicpm-v", "cogvlm", "qwen2-vl", "qwen2.5-vl", "qwen3-vl",
    "gemma-3", "gemma3", "phi-3.5-vision", "phi-4-multimodal",
    "internvl", "idefics", "fuyu", "nanollava", "deepseek-vl",
    "claude-3", "claude-4", "claude-sonnet", "claude-opus", "claude-haiku",
    "gpt-4o", "gpt-4.1", "gpt-4-turbo", "gpt-4-vision", "gpt-5", "o1", "o3", "o4",
    "gemini-1.5", "gemini-2", "gemini-pro-vision",
    "grok-vision", "grok-2-vision", "grok-4",
)
# Модели, у которых в имени есть "vision"-подобные слова, но изображений они НЕ видят.
_VISION_NEGATIVE = ("embed", "whisper", "tts", "audio", "coder")


def _guess_vision_by_name(name: str) -> bool:
    n = (name or "").lower()
    if any(tok in n for tok in _VISION_NEGATIVE):
        return False
    return any(tok in n for tok in _VISION_NAME_PATTERNS)


def list_model_groups(base_url: Optional[str] = None,
                      api_key: Optional[str] = None) -> Optional[List[dict]]:
    """Для OpenRouter возвращает модели, сгруппированные по бесплатным/платным.
    Для остальных провайдеров — None (используйте list_models)."""
    cfg = get()
    url = (base_url or cfg["base_url"]).rstrip("/")
    key = api_key if api_key is not None else cfg.get("api_key", "")
    if "openrouter.ai" not in url:
        return None
    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        req = urllib.request.Request(url + "/models", headers=headers)
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        return None
    free, paid = [], []
    for it in data.get("data") or []:
        mid = it.get("id")
        if not mid:
            continue
        pricing = it.get("pricing") or {}
        def _z(v):
            try: return float(v) == 0.0
            except Exception: return False
        is_free = mid.endswith(":free") or (_z(pricing.get("prompt")) and _z(pricing.get("completion")))
        arch = it.get("architecture") or {}
        modalities = arch.get("input_modalities") or arch.get("modality") or []
        if isinstance(modalities, str):
            vision = "image" in modalities.lower()
        else:
            vision = any("image" in str(m).lower() for m in modalities)
        if not vision:
            vision = _guess_vision_by_name(mid)
        entry = {"id": mid, "vision": vision}
        (free if is_free else paid).append(entry)
    free.sort(key=lambda x: x["id"])
    paid.sort(key=lambda x: x["id"])
    groups = []
    if free: groups.append({"label": "Бесплатные", "models": free})
    if paid: groups.append({"label": "Платные", "models": paid})
    return groups or None


def list_models(base_url: Optional[str] = None,
                api_key: Optional[str] = None) -> List[dict]:
    """Запрашивает у провайдера список моделей.

    Пробует:
      1) OpenAI-совместимый /models (с Bearer-токеном, если задан);
      2) Anthropic /v1/models (x-api-key + anthropic-version);
      3) Ollama-native /api/tags.
    """
    cfg = get()
    url = (base_url or cfg["base_url"]).rstrip("/")
    key = api_key if api_key is not None else cfg.get("api_key", "")

    def _fetch(u: str, headers: dict) -> Optional[dict]:
        try:
            req = urllib.request.Request(u, headers=headers)
            with urllib.request.urlopen(req, timeout=8) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            return None

    def _dedup_sort(entries: List[dict]) -> List[dict]:
        seen: dict = {}
        for e in entries:
            if e["id"] not in seen:
                seen[e["id"]] = e
        return sorted(seen.values(), key=lambda x: x["id"])

    # 1) OpenAI-совместимый
    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    data = _fetch(url + "/models", headers)
    if data:
        items = data.get("data") or data.get("models") or []
        entries: List[dict] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            nm = it.get("id") or it.get("name")
            if not nm:
                continue
            arch = it.get("architecture") or {}
            mods = arch.get("input_modalities") or arch.get("modality") or []
            if isinstance(mods, str):
                vision = "image" in mods.lower()
            else:
                vision = any("image" in str(m).lower() for m in mods)
            if not vision:
                vision = _guess_vision_by_name(nm)
            entries.append({"id": nm, "vision": vision})
        if entries:
            return _dedup_sort(entries)

    # 2) Anthropic
    if "anthropic.com" in url and key:
        data = _fetch(url + "/models", {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "Accept": "application/json",
        })
        if data:
            items = data.get("data") or []
            entries = [{"id": it["id"], "vision": _guess_vision_by_name(it["id"])}
                       for it in items if it.get("id")]
            if entries:
                return _dedup_sort(entries)

    # 2b) Gemini native (generativelanguage.googleapis.com)
    if "generativelanguage.googleapis.com" in url and key:
        gem_host = url.split("/v1beta", 1)[0] + "/v1beta"
        data = _fetch(gem_host + "/models?key=" + urllib.parse.quote(key),
                      {"Accept": "application/json"})
        if data:
            entries = []
            for m in data.get("models") or []:
                nm = m.get("name") or ""
                if nm.startswith("models/"):
                    nm = nm[len("models/"):]
                methods = m.get("supportedGenerationMethods") or []
                if not nm or (methods and "generateContent" not in methods):
                    continue
                entries.append({"id": nm, "vision": _guess_vision_by_name(nm)})
            if entries:
                return _dedup_sort(entries)

    # 3) Ollama-native
    host = url[:-3] if url.endswith("/v1") else url
    data = _fetch(host + "/api/tags", {"Accept": "application/json"})
    if data:
        entries = []
        for m in data.get("models") or []:
            nm = m.get("name")
            if not nm:
                continue
            # У Ollama есть details.families — "clip"/"mllama" указывают на зрение.
            det = m.get("details") or {}
            fams = det.get("families") or []
            vision = any(str(f).lower() in ("clip", "mllama", "vision") for f in fams)
            if not vision:
                vision = _guess_vision_by_name(nm)
            entries.append({"id": nm, "vision": vision})
        if entries:
            return _dedup_sort(entries)
    return []
