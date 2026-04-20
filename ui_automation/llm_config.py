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
    "custom": {
        "label": "Свой (OpenAI-совместимый)",
        "base_url": "",
        "api_key":  "",
        "extra_body": {},
    },
}

_lock = threading.Lock()
_state: Optional[dict] = None
_client: Optional[openai.OpenAI] = None
_client_key: Optional[tuple] = None


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
    s.setdefault("provider", prov_name)
    s.setdefault("base_url", os.environ.get("API_BASE", prov["base_url"]))
    s.setdefault("api_key",  os.environ.get("API_KEY",  prov["api_key"]))
    s.setdefault("model",    os.environ.get("API_MODEL", "Qwen3.5-9B-abliterated-vision-Q4_K_M"))
    _state = s
    # Зеркалим в env, чтобы старый код, читающий os.environ, видел актуальные значения.
    os.environ["API_BASE"]  = s["base_url"]
    os.environ["API_KEY"]   = s["api_key"]
    os.environ["API_MODEL"] = s["model"]
    return s


def _persist() -> None:
    try:
        with open(_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(_state, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def get() -> dict:
    with _lock:
        return dict(_load())


def get_provider() -> str:
    return get()["provider"]


def get_model() -> str:
    return get()["model"]


def get_base_url() -> str:
    return get()["base_url"]


def get_extra_body() -> dict:
    prov = PROVIDERS.get(get_provider(), PROVIDERS["llamacpp"])
    return dict(prov["extra_body"])


def get_client() -> openai.OpenAI:
    global _client, _client_key
    with _lock:
        s = _load()
        key = (s["base_url"], s["api_key"])
        if _client is None or _client_key != key:
            _client = openai.OpenAI(base_url=s["base_url"], api_key=s["api_key"])
            _client_key = key
        return _client


def set_config(provider: Optional[str] = None,
               model: Optional[str] = None,
               base_url: Optional[str] = None,
               api_key: Optional[str] = None) -> dict:
    """Обновляет конфигурацию. При смене провайдера без явного base_url/api_key
    подставляются дефолты выбранного провайдера."""
    global _client, _client_key
    with _lock:
        s = _load()
        if provider and provider in PROVIDERS:
            if provider != s.get("provider"):
                prov = PROVIDERS[provider]
                s["base_url"] = base_url or prov["base_url"]
                s["api_key"]  = api_key  or prov["api_key"]
            s["provider"] = provider
        if base_url:
            s["base_url"] = base_url
        if api_key:
            s["api_key"] = api_key
        if model:
            s["model"] = model
        os.environ["API_BASE"]  = s["base_url"]
        os.environ["API_KEY"]   = s["api_key"]
        os.environ["API_MODEL"] = s["model"]
        _client = None
        _client_key = None
        _persist()
        return dict(s)


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
        (free if is_free else paid).append(mid)
    free.sort(); paid.sort()
    groups = []
    if free: groups.append({"label": "Бесплатные", "models": free})
    if paid: groups.append({"label": "Платные", "models": paid})
    return groups or None


def list_models(base_url: Optional[str] = None,
                api_key: Optional[str] = None) -> List[str]:
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

    # 1) OpenAI-совместимый
    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    data = _fetch(url + "/models", headers)
    if data:
        items = data.get("data") or data.get("models") or []
        names = [it.get("id") or it.get("name") for it in items if isinstance(it, dict)]
        names = [n for n in names if n]
        if names:
            return sorted(set(names))

    # 2) Anthropic
    if "anthropic.com" in url and key:
        data = _fetch(url + "/models", {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "Accept": "application/json",
        })
        if data:
            items = data.get("data") or []
            names = [it.get("id") for it in items if it.get("id")]
            if names:
                return sorted(set(names))

    # 2b) Gemini native (generativelanguage.googleapis.com)
    if "generativelanguage.googleapis.com" in url and key:
        gem_host = url.split("/v1beta", 1)[0] + "/v1beta"
        data = _fetch(gem_host + "/models?key=" + urllib.parse.quote(key),
                      {"Accept": "application/json"})
        if data:
            names = []
            for m in data.get("models") or []:
                nm = m.get("name") or ""
                if nm.startswith("models/"):
                    nm = nm[len("models/"):]
                methods = m.get("supportedGenerationMethods") or []
                if nm and (not methods or "generateContent" in methods):
                    names.append(nm)
            if names:
                return sorted(set(names))

    # 3) Ollama-native
    host = url[:-3] if url.endswith("/v1") else url
    data = _fetch(host + "/api/tags", {"Accept": "application/json"})
    if data:
        return sorted({m.get("name") for m in (data.get("models") or []) if m.get("name")})
    return []
