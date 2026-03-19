"""
Автономный WebSocket-мост между Chrome расширением и MCP tools.
Запускается один раз из main.py и живёт пока работает ассистент.
- Порт 9009: WebSocket для Chrome расширения
- Порт 9010: HTTP API для MCP tools
"""
import asyncio
import json
import sys
import uuid
from aiohttp import web
import websockets

_extension_ws = None
_pending: dict = {}


async def _ws_handler(websocket):
    global _extension_ws
    _extension_ws = websocket
    print("[Bridge] Chrome extension connected", flush=True)
    try:
        async for message in websocket:
            data = json.loads(message)
            if data.get("type") == "pong":
                continue
            req_id = data.get("id")
            if req_id and req_id in _pending:
                _pending[req_id].set_result(data.get("result", {}))
    except Exception as e:
        print(f"[Bridge] WS error: {e}", flush=True)
    finally:
        _extension_ws = None
        print("[Bridge] Chrome extension disconnected", flush=True)


async def _http_command(request):
    body = await request.json()
    command = body.get("command")
    params = body.get("params", {})
    timeout = body.get("timeout", 15.0)

    if _extension_ws is None:
        return web.json_response({"error": "Chrome extension not connected"}, status=503)

    req_id = str(uuid.uuid4())
    loop = asyncio.get_event_loop()
    fut = loop.create_future()
    _pending[req_id] = fut

    try:
        await _extension_ws.send(json.dumps({
            "id": req_id,
            "command": command,
            "params": params,
        }))
        result = await asyncio.wait_for(fut, timeout=timeout)
        return web.json_response(result)
    except asyncio.TimeoutError:
        return web.json_response({"error": f"Command '{command}' timed out"}, status=504)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)
    finally:
        _pending.pop(req_id, None)


async def _http_status(request):
    return web.json_response({"connected": _extension_ws is not None})


async def main():
    # HTTP сервер для MCP tools
    app = web.Application()
    app.router.add_post("/command", _http_command)
    app.router.add_get("/status", _http_status)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 9010)
    await site.start()
    print("[Bridge] HTTP API on http://127.0.0.1:9010", flush=True)

    # WebSocket сервер для расширения
    async with websockets.serve(_ws_handler, "127.0.0.1", 9009):
        print("[Bridge] WebSocket on ws://127.0.0.1:9009", flush=True)
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
