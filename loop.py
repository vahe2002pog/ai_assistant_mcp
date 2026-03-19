import asyncio
import json
import urllib.request
from langchain_core.messages import HumanMessage
from utils import (
    open_file,
    open_folder,
    open_app,
    open_url,
    handle_media_command,
    handle_volume_command,
)


async def run_loop(graph, config):
    print("Ассистент готов! (Введите 'exit' для выхода)")

    while True:
        try:
            user_input = await asyncio.to_thread(input, "\nВы: ")
        except (KeyboardInterrupt, EOFError):
            break
        if user_input.lower() in ["exit", "quit", "выход"]:
            break
        if not user_input.strip():
            continue

        await process_graph_stream(graph, config, user_input)


async def gather_context() -> str:
    """Собирает текущий контекст системы: открытые окна и активный браузер."""
    parts = []

    # Открытые окна
    try:
        import uiautomation as auto
        windows = [w for w in auto.GetRootControl().GetChildren()
                   if w.ControlTypeName == "WindowControl" and w.Name]
        if windows:
            names = [w.Name[:40] for w in windows[:8]]
            parts.append("Окна: " + ", ".join(names))
    except Exception:
        pass

    # Активный браузер
    try:
        body = json.dumps({"command": "get_state", "params": {}, "timeout": 5}).encode()
        req = urllib.request.Request(
            "http://127.0.0.1:9010/command",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            state = json.loads(resp.read())
        if "url" in state:
            parts.append(f"Браузер: {state['url']}")
    except Exception:
        pass

    return "\n".join(parts)


async def process_graph_stream(graph, config, user_input):
    context = await gather_context()
    if context:
        print(f"\n[Контекст]\n{context}")
        message = f"{user_input}\n\n[Контекст: {context}]"
    else:
        message = user_input

    async for event in graph.astream(
        {"messages": [HumanMessage(content=message)]},
        config=config,
    ):
        for node, values in event.items():
            if node == "formatter":
                msg = values["messages"][-1]
                if msg.content:
                    try:
                        data = json.loads(msg.content)
                        print(f"\n🗣  Голос: {data.get('voice', '')}")

                        screen = data.get("screen")
                        if screen and screen.get("blocks"):
                            print("🖥  Экран:")
                            for block in screen["blocks"]:
                                block_type = block.get("type")

                                if block_type == "text":
                                    print(f"    {block.get('text')}")

                                elif block_type == "list":
                                    if block.get("title"):
                                        print(f"    {block['title']}")
                                    for item in block.get("items", []):
                                        print(f"      • {item}")

                                elif block_type == "table":
                                    if block.get("title"):
                                        print(f"    {block['title']}")
                                    for row in block.get("rows", []):
                                        print(f"      {row}")

                                elif block_type == "links":
                                    for link in block.get("links", []):
                                        print(f"      🔗 {link}")

                                elif block_type == "files":
                                    for path in block.get("file_paths", []):
                                        print(f"      📁 {path}")
                    except Exception:
                        print(f"\nАссистент (Raw): {msg.content}")

            elif node == "executor":
                msg = values["messages"][-1]
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    for tc in msg.tool_calls:
                        args_str = ", ".join(f"{k}={repr(v)}" for k, v in tc.get("args", {}).items())
                        print(f"  → {tc['name']}({args_str})")

            elif node == "tools":
                raw_output = values["messages"][-1].content
                tool_text = str(raw_output)
                if isinstance(raw_output, list) and len(raw_output) > 0 and isinstance(raw_output[0], dict):
                    tool_text = raw_output[0].get("text", str(raw_output))
                elif isinstance(raw_output, str) and raw_output.startswith("[{"):
                    try:
                        parsed = ast.literal_eval(raw_output)
                        tool_text = parsed[0]["text"]
                    except Exception:
                        pass

                await _handle_tool_command(tool_text)



async def _handle_tool_command(tool_text: str):
    TOOL_TIMEOUT = 15.0

    if "__OPEN_FILE_COMMAND__:" in tool_text:
        path_to_open = tool_text.split("__OPEN_FILE_COMMAND__:")[1].strip()
        try:
            res = await asyncio.wait_for(asyncio.to_thread(open_file, path_to_open), timeout=TOOL_TIMEOUT)
            print(f"  [Система] Успех: Файл открыт ({res})")
        except asyncio.TimeoutError:
            print(f"  [Система] Таймаут: открытие файла")
        except Exception as e:
            print(f"  [Система] Ошибка: {e}")

    elif "__OPEN_FOLDER_COMMAND__:" in tool_text:
        folder_to_open = tool_text.split("__OPEN_FOLDER_COMMAND__:")[1].strip()
        try:
            res = await asyncio.wait_for(asyncio.to_thread(open_folder, folder_to_open), timeout=TOOL_TIMEOUT)
            print(f"[Система] Успех: Папка открыта ({res})")
        except asyncio.TimeoutError:
            print(f"  [Система] Таймаут: открытие папки")
        except Exception as e:
            print(f"  [Система] Ошибка: {e}")

    elif "__OPEN_APP_COMMAND__:" in tool_text:
        app_to_open = tool_text.split("__OPEN_APP_COMMAND__:")[1].strip()
        try:
            res = await asyncio.wait_for(asyncio.to_thread(open_app, app_to_open), timeout=TOOL_TIMEOUT)
            print(f"  [Система] Успех: Приложение запущено ({res})")
        except asyncio.TimeoutError:
            print(f"  [Система] Таймаут: запуск приложения")
        except Exception as e:
            print(f"  [Система] Ошибка: {e}")

    elif "__OPEN_URL_COMMAND__:" in tool_text:
        url_to_open = tool_text.split("__OPEN_URL_COMMAND__:")[1].strip()
        try:
            res = await asyncio.wait_for(asyncio.to_thread(open_url, url_to_open), timeout=TOOL_TIMEOUT)
            print(f"[Система] Успех: Открыт веб-сайт ({res})")
        except asyncio.TimeoutError:
            print(f"  [Система] Таймаут: открытие сайта")
        except Exception as e:
            print(f"  [Система] Ошибка: {e}")

    elif "__VOLUME_COMMAND__:" in tool_text:
        try:
            _, action, amount = tool_text.split(":")
            await asyncio.wait_for(
                asyncio.to_thread(handle_volume_command, action, float(amount)),
                timeout=TOOL_TIMEOUT,
            )
            print(f"[Система] Громкость изменена: {action}")
        except asyncio.TimeoutError:
            print(f"  [Система] Таймаут: команда громкости")
        except Exception as e:
            print(f"[Система] Ошибка звука: {e}")

    elif "__MEDIA_COMMAND__:" in tool_text:
        try:
            action = tool_text.split(":")[1]
            await asyncio.wait_for(
                asyncio.to_thread(handle_media_command, action),
                timeout=TOOL_TIMEOUT,
            )
            print(f"[Система] Медиа команда: {action}")
        except asyncio.TimeoutError:
            print(f"  [Система] Таймаут: медиа команда")
        except Exception as e:
            print(f"[Система] Ошибка медиа: {e}")

    else:
        print(f"  [Система] Результат модуля: {tool_text}")
