"""
WebSocket сервер — мост между MCP tools и Chrome расширением.
Запускается в отдельном потоке. Использует thread-safe concurrent.futures.Future.
"""
import asyncio
import concurrent.futures
import json
import os
import sys
import threading
import uuid
import websockets

_extension_ws = None
_pending: dict = {}           # id -> concurrent.futures.Future
_loop: asyncio.AbstractEventLoop = None
_thread: threading.Thread | None = None
_lock = threading.Lock()
_start_lock = threading.Lock()
_ready = threading.Event()   # сигнал что сервер реально слушает

def _log(msg):
    print(msg, flush=True, file=sys.stderr)


async def _handler(websocket):
    global _extension_ws
    _extension_ws = websocket
    _log("[Bridge] Chrome extension connected")
    print("[Bridge] Chrome extension connected", flush=True, file=sys.stderr)
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                if data.get("type") == "pong":
                    continue
                req_id = data.get("id")
                if req_id:
                    with _lock:
                        fut = _pending.get(req_id)
                    if fut and not fut.done():
                        fut.set_result(data.get("result", {}))
            except Exception as e:
                _log(f"[Bridge] Handler error: {e}")
                print(f"[Bridge] Handler error: {e}", flush=True, file=sys.stderr)
    finally:
        _extension_ws = None
        _log("[Bridge] Chrome extension disconnected")
        print("[Bridge] Chrome extension disconnected", flush=True, file=sys.stderr)


async def _serve():
    try:
        async with websockets.serve(_handler, "127.0.0.1", 9009):
            _log("[Bridge] WebSocket server on ws://127.0.0.1:9009")
            print("[Bridge] WebSocket server on ws://127.0.0.1:9009", flush=True, file=sys.stderr)
            _ready.set()
            await asyncio.Future()
    except Exception as e:
        _log(f"[Bridge] _serve error: {e}")
        print(f"[Bridge] _serve error: {e}", flush=True, file=sys.stderr)
        _ready.set()  # разблокируем даже при ошибке


def _free_port(port: int):
    """На Windows убивает процесс, занимающий порт."""
    if sys.platform != "win32":
        return
    try:
        import subprocess
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True, text=True
        )
        for line in result.stdout.splitlines():
            if f":{port} " in line and "LISTENING" in line:
                parts = line.split()
                pid = int(parts[-1])
                import os as _os
                _os.kill(pid, 9)
                _log(f"[Bridge] Killed PID {pid} holding port {port}")
                import time as _t
                _t.sleep(0.5)
                break
    except Exception as e:
        _log(f"[Bridge] _free_port error: {e}")


def _run_thread():
    global _loop
    # На Windows явно используем SelectorEventLoop для websockets
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    _free_port(9009)
    loop = asyncio.new_event_loop()
    _loop = loop
    asyncio.set_event_loop(loop)
    _log("[Bridge] Thread started, running event loop")
    try:
        loop.run_until_complete(_serve())
    finally:
        if _loop is loop:
            _loop = None
        _ready.set()
        loop.close()


def is_running() -> bool:
    """Возвращает True если WS сервер уже запущен."""
    loop = _loop
    return loop is not None and loop.is_running()


def start_thread():
    """Запускает WS сервер в фоновом потоке. Вызвать один раз при старте."""
    global _thread
    with _start_lock:
        if is_running():
            return
        if _thread is None or not _thread.is_alive():
            _ready.clear()
            _thread = threading.Thread(target=_run_thread, daemon=True, name="ws-bridge")
            _thread.start()
    # Ждём пока сервер реально начнёт слушать (или упадёт)
    _ready.wait(timeout=5.0)


async def send_command(command: str, params: dict = None, timeout: float = 15.0):
    """Отправить команду расширению и дождаться ответа (thread-safe)."""
    # Ждём подключения расширения до 10 секунд
    if _extension_ws is None:
        _log(f"[Bridge] Waiting for extension to connect (command={command})...")
        loop = asyncio.get_event_loop()
        for _ in range(20):
            if _extension_ws is not None:
                break
            await loop.run_in_executor(None, lambda: __import__('time').sleep(0.5))
        if _extension_ws is None or _loop is None:
            raise RuntimeError(
                "Chrome extension not connected. Open Chrome and make sure the extension is active."
            )

    req_id = str(uuid.uuid4())
    fut: concurrent.futures.Future = concurrent.futures.Future()

    with _lock:
        _pending[req_id] = fut

    # Отправляем из потока WS сервера
    asyncio.run_coroutine_threadsafe(
        _extension_ws.send(json.dumps({
            "id": req_id,
            "command": command,
            "params": params or {}
        })),
        _loop
    )

    try:
        # Ждём в отдельном потоке чтобы не блокировать main loop
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, lambda: fut.result(timeout=timeout)
        )
        return result
    except concurrent.futures.TimeoutError:
        raise TimeoutError(f"Command '{command}' timed out after {timeout}s")
    finally:
        with _lock:
            _pending.pop(req_id, None)


if __name__ == "__main__":
    asyncio.run(_serve())
