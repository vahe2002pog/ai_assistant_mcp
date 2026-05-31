"""Local portable Ollama process and model download helpers."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from typing import Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OLLAMA_DIR = os.path.join(ROOT, "utils", "ollama")
OLLAMA_EXE = os.path.join(OLLAMA_DIR, "ollama.exe")
OLLAMA_HOST = "http://127.0.0.1:11435"
OLLAMA_ENV_HOST = "127.0.0.1:11435"


def _default_models_dir() -> str:
    override = os.environ.get("COMPASS_OLLAMA_MODELS")
    if override:
        return override
    program_data = os.environ.get("PROGRAMDATA")
    if getattr(sys, "frozen", False) and program_data:
        return os.path.join(program_data, "Compass", "ollama", "models")
    return os.path.join(OLLAMA_DIR, "models")


OLLAMA_MODELS_DIR = _default_models_dir()

RECOMMENDED_MODELS = ("qwen3.5:2b", "qwen3.5:4b", "qwen3.5:9b")
RECOMMENDED_MODEL_SIZES = {
    "qwen3.5:2b": "2.7GB",
    "qwen3.5:4b": "3.4GB",
    "qwen3.5:9b": "6.6GB",
}

_lock = threading.RLock()
_proc: Optional[subprocess.Popen] = None
_pull_proc: Optional[subprocess.Popen] = None
_pull: dict = {"active": False, "model": "", "status": "", "error": ""}


def _clean_error(text: str) -> str:
    raw = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", str(text or "")).strip()
    lower = raw.lower()
    if not raw:
        return "Ollama вернула пустую ошибку."
    if "invalid model name" in lower:
        return (
            "Некорректное имя модели. Используйте формат вроде "
            "`qwen3.5:4b`, `llama3.2:3b` или `namespace/model:tag`."
        )
    if "file does not exist" in lower or "not found" in lower or "404" in lower:
        return "Модель не найдена в Ollama Library. Проверьте название и тег модели."
    if "already downloading" in lower:
        return "Другая модель уже загружается. Остановите текущую загрузку или дождитесь её завершения."
    if "connection refused" in lower or "actively refused" in lower:
        return "Не удалось подключиться к встроенной Ollama. Попробуйте ещё раз через несколько секунд."
    if "context deadline exceeded" in lower or "timeout" in lower:
        return "Ollama не успела ответить. Проверьте интернет-соединение и повторите загрузку."
    if "pull model manifest" in lower:
        return "Не удалось получить описание модели. Проверьте название модели и доступ к интернету."
    return raw[-600:]


def executable() -> str:
    return OLLAMA_EXE if os.path.isfile(OLLAMA_EXE) else "ollama"


def _url(path: str) -> str:
    return OLLAMA_HOST.rstrip("/") + path


def is_running(timeout: float = 0.4) -> bool:
    try:
        req = urllib.request.Request(_url("/api/tags"), headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status < 500
    except Exception:
        return False


def _env() -> dict:
    env = os.environ.copy()
    env["OLLAMA_HOST"] = OLLAMA_ENV_HOST
    env["OLLAMA_MODELS"] = OLLAMA_MODELS_DIR
    return env


def ensure_running(timeout: float = 15.0) -> bool:
    global _proc
    if is_running():
        return True
    exe = executable()
    if exe == "ollama" and not os.path.isfile(OLLAMA_EXE):
        # Let PATH resolution try later, but this message is useful in status.
        pass
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    with _lock:
        if _proc is not None and _proc.poll() is None:
            pass
        else:
            os.makedirs(OLLAMA_MODELS_DIR, exist_ok=True)
            _proc = subprocess.Popen(
                [exe, "serve"],
                cwd=OLLAMA_DIR if os.path.isdir(OLLAMA_DIR) else ROOT,
                env=_env(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=flags,
            )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_running():
            return True
        time.sleep(0.25)
    return is_running()


def stop_if_owned() -> bool:
    global _proc
    with _lock:
        proc = _proc
        _proc = None
    if proc is None:
        return False
    if proc.poll() is not None:
        return True
    try:
        proc.terminate()
        try:
            proc.wait(timeout=4)
        except subprocess.TimeoutExpired:
            proc.kill()
        return True
    except Exception:
        return False


def _format_size(size: object) -> str:
    try:
        value = float(size)
    except Exception:
        return ""
    if value <= 0:
        return ""
    units = ("B", "KB", "MB", "GB", "TB")
    idx = 0
    while value >= 1024 and idx < len(units) - 1:
        value /= 1024
        idx += 1
    if idx <= 1:
        return f"{int(value)} {units[idx]}"
    return f"{value:.1f} {units[idx]}"


def _format_eta(value: str) -> str:
    text = (value or "").strip()
    parts = re.findall(r"(\d+)\s*([hms])", text, flags=re.IGNORECASE)
    if not parts:
        return text
    labels = {"h": "ч", "m": "м", "s": "с"}
    return " ".join(f"{num}{labels.get(unit.lower(), unit)}" for num, unit in parts)


def _compact_pull_status(text: str) -> str:
    raw = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", str(text or ""))
    raw = raw.replace("\r", " ").replace("\n", " ")
    raw = re.sub(r"\s+", " ", raw).strip()
    if not raw:
        return ""
    matches = re.findall(
        r"(\d+(?:\.\d+)?\s*[KMGT]?B)\s*/\s*(\d+(?:\.\d+)?\s*[KMGT]?B)"
        r".*?(\d+h\d+m\d+s|\d+h\d+m|\d+m\d+s|\d+h|\d+m|\d+s)",
        raw,
        flags=re.IGNORECASE,
    )
    if matches:
        done, total, eta = matches[-1]
        return f"{done.replace(' ', '')} / {total.replace(' ', '')} · {_format_eta(eta)}"
    return ""


def _size_from_name(name: str) -> str:
    if name in RECOMMENDED_MODEL_SIZES:
        return RECOMMENDED_MODEL_SIZES[name]
    match = re.search(r":(\d+(?:\.\d+)?)([bB])(?:[-_].*)?$", name or "")
    if match:
        value = match.group(1).rstrip("0").rstrip(".")
        return f"{value}B"
    return ""


def _tags_by_name() -> dict[str, dict]:
    if not is_running():
        return {}
    try:
        req = urllib.request.Request(_url("/api/tags"), headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return {}
    items = {}
    for item in data.get("models") or []:
        name = item.get("name")
        if name:
            items[str(name)] = item
    return items


def installed_models() -> list[str]:
    return sorted(_tags_by_name())


def list_model_entries() -> list[dict]:
    tags = _tags_by_name()
    installed = set(tags)
    ids = list(installed)
    for name in RECOMMENDED_MODELS:
        if name not in installed:
            ids.append(name)
    with _lock:
        active = bool(_pull.get("active"))
        active_model = str(_pull.get("model") or "")
        error = str(_pull.get("error") or "")
    if active and active_model and active_model not in ids:
        ids.append(active_model)
    entries = []
    for name in ids:
        raw_size = (tags.get(name) or {}).get("size")
        size_label = RECOMMENDED_MODEL_SIZES.get(name) or _format_size(raw_size) or _size_from_name(name)
        entries.append({
            "id": name,
            "vision": False,
            "installed": name in installed,
            "recommended": name in RECOMMENDED_MODELS,
            "size": raw_size or None,
            "size_label": size_label,
            "state": (
                "downloading" if active and name == active_model
                else "installed" if name in installed
                else "error" if error and name == active_model
                else "idle"
            ),
            "error": error if error and name == active_model else "",
        })
    return entries


def pull_model(model: str) -> dict:
    global _pull_proc
    model = (model or "").strip()
    if not model:
        return {"ok": False, "error": "Введите название модели."}
    with _lock:
        if _pull.get("active"):
            return {"ok": False, "error": _clean_error("already downloading")}
        _pull.update({"active": True, "model": model, "status": "Запускаю Ollama...", "error": ""})

    def worker() -> None:
        global _pull_proc
        try:
            if not ensure_running():
                raise RuntimeError("Ollama не запустилась")
            with _lock:
                _pull["status"] = f"Загружаю {model}..."
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            proc = subprocess.Popen(
                [executable(), "pull", model],
                cwd=OLLAMA_DIR if os.path.isdir(OLLAMA_DIR) else ROOT,
                env=_env(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=flags,
            )
            with _lock:
                _pull_proc = proc
            output_parts = []
            chunk = ""
            assert proc.stdout is not None
            while True:
                ch = proc.stdout.read(1)
                if ch == "" and proc.poll() is not None:
                    break
                if not ch:
                    time.sleep(0.05)
                    continue
                output_parts.append(ch)
                if ch in ("\r", "\n"):
                    compact = _compact_pull_status(chunk)
                    if compact:
                        with _lock:
                            _pull["status"] = compact
                    chunk = ""
                else:
                    chunk += ch
                    compact = _compact_pull_status(chunk)
                    if compact:
                        with _lock:
                            _pull["status"] = compact
            compact = _compact_pull_status(chunk)
            if compact:
                with _lock:
                    _pull["status"] = compact
            stdout = "".join(output_parts)
            stderr = ""
            if proc.returncode != 0:
                err = (stderr or stdout or "ollama pull failed").strip()
                raise RuntimeError(_clean_error(err))
            with _lock:
                _pull_proc = None
                _pull.update({"active": False, "status": f"{model} загружена", "error": ""})
        except Exception as e:
            with _lock:
                _pull_proc = None
                _pull.update({"active": False, "status": "", "error": _clean_error(str(e))})

    threading.Thread(target=worker, daemon=True, name="ollama-pull").start()
    return {"ok": True, **snapshot()}


def cancel_pull() -> dict:
    global _pull_proc
    with _lock:
        proc = _pull_proc
        model = _pull.get("model") or ""
        _pull_proc = None
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        except Exception:
            pass
    with _lock:
        _pull.update({"active": False, "model": model, "status": "Загрузка остановлена", "error": ""})
    return {"ok": True, **snapshot()}


def delete_model(model: str) -> dict:
    model = (model or "").strip()
    if not model:
        return {"ok": False, "error": "Выберите модель для удаления."}
    if not ensure_running():
        return {"ok": False, "error": "Ollama не запустилась"}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    proc = subprocess.run(
        [executable(), "rm", model],
        cwd=OLLAMA_DIR if os.path.isdir(OLLAMA_DIR) else ROOT,
        env=_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=flags,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "ollama rm failed").strip()
        return {"ok": False, "error": _clean_error(err), **snapshot()}
    return {"ok": True, **snapshot()}


def snapshot() -> dict:
    with _lock:
        pull = dict(_pull)
        owned_pid = _proc.pid if _proc is not None and _proc.poll() is None else None
    return {
        "available": os.path.isfile(OLLAMA_EXE) or shutil.which("ollama") is not None,
        "exe": executable(),
        "host": OLLAMA_HOST,
        "running": is_running(),
        "owned_pid": owned_pid,
        "recommended": list(RECOMMENDED_MODELS),
        "installed": installed_models(),
        "models": list_model_entries(),
        "pull": pull,
    }
