import asyncio
import ast
import json
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
    """Асинхронный цикл ввода/вывода консоли. Работает с `graph.astream`.

    Args:
        graph: скомпилированный workflow graph
        config: конфиг для `graph.astream`
    """
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


async def process_graph_stream(graph, config, user_input):
    """Обрабатывает поток событий графа с таймаутом для каждого узла и инструмента."""
    current_node = "unknown"
    last_tool_success = None  # Отслеживаем последний успешный результат инструмента
    
    async for event in graph.astream({"messages": [HumanMessage(content=user_input)]}, config=config):
        for node, values in event.items():
            current_node = node
            
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
                if msg.content:
                    print(f"\n[executor] {msg.content}")

            elif node == "tools":
                raw_output = values["messages"][-1].content
                tool_text = str(raw_output)
                if isinstance(raw_output, list) and len(raw_output) > 0 and isinstance(raw_output[0], dict):
                    tool_text = raw_output[0].get("text", str(raw_output))
                elif isinstance(raw_output, str) and raw_output.startswith("[{"):
                    try:
                        parsed = ast.literal_eval(raw_output)
                        tool_text = parsed[0]["text"]
                    except: 
                        pass

                # Парсим результат инструмента
                try:
                    result_data = json.loads(tool_text) if tool_text.startswith("{") else None
                    # Проверяем что это словарь, а не список
                    if result_data and isinstance(result_data, dict):
                        # Показываем только успешные вызовы инструментов
                        if result_data.get("success") is True:
                            action = result_data.get('data', {})
                            if isinstance(action, dict):
                                action_text = action.get('action', 'успех')
                            else:
                                action_text = 'успех'
                            print(f"  [✓] Инструмент выполнен: {action_text}")
                            last_tool_success = result_data
                        elif result_data.get("error"):
                            # Не выводим ошибки инструментов (LLM их обработает)
                            pass
                except (json.JSONDecodeError, TypeError, AttributeError):
                    # Если не JSON или другие ошибки парсинга - просто игнорируем
                    pass

                # Обработка команд инструментов с таймаутом 15 сек для каждого
                await _handle_tool_command(tool_text)

            else:
                print(f"  [Узел] {node}: {values}")


async def _handle_tool_command(tool_text: str):
    """Обрабатывает команду инструмента с таймаутом."""
    TOOL_TIMEOUT = 15.0  # Таймаут для каждого инструмента
    
    if "__OPEN_FILE_COMMAND__:" in tool_text:
        path_to_open = tool_text.split("__OPEN_FILE_COMMAND__:")[1].strip()
        try:
            res = await asyncio.wait_for(
                asyncio.to_thread(open_file, path_to_open),
                timeout=TOOL_TIMEOUT
            )
            print(f"  [Система] Успех: Файл открыт ({res})")
        except asyncio.TimeoutError:
            print(f"  [Система] ⏱️ Таймаут: Не удалось открыть файл за {TOOL_TIMEOUT} сек")
        except Exception as e:
            print(f"  [Система] Ошибка при открытии файла: {e}")

    elif "__OPEN_FOLDER_COMMAND__:" in tool_text:
        folder_to_open = tool_text.split("__OPEN_FOLDER_COMMAND__:")[1].strip()
        try:
            res = await asyncio.wait_for(
                asyncio.to_thread(open_folder, folder_to_open),
                timeout=TOOL_TIMEOUT
            )
            print(f"[Система] Успех: Папка открыта ({res})")
        except asyncio.TimeoutError:
            print(f"  [Система] ⏱️ Таймаут: Не удалось открыть папку за {TOOL_TIMEOUT} сек")
        except Exception as e:
            print(f"  [Система] Ошибка при открытии папки: {e}")

    elif "__OPEN_APP_COMMAND__:" in tool_text:
        app_to_open = tool_text.split("__OPEN_APP_COMMAND__:")[1].strip()
        try:
            res = await asyncio.wait_for(
                asyncio.to_thread(open_app, app_to_open),
                timeout=TOOL_TIMEOUT
            )
            print(f"  [Система] Успех: Приложение запущено ({res})")
        except asyncio.TimeoutError:
            print(f"  [Система] ⏱️ Таймаут: Не удалось запустить приложение за {TOOL_TIMEOUT} сек")
        except Exception as e:
            print(f"  [Система] Ошибка при запуске приложения: {e}")

    elif "__OPEN_URL_COMMAND__:" in tool_text:
        url_to_open = tool_text.split("__OPEN_URL_COMMAND__:")[1].strip()
        try:
            res = await asyncio.wait_for(
                asyncio.to_thread(open_url, url_to_open),
                timeout=TOOL_TIMEOUT
            )
            print(f"[Система] Успех: Открыт веб-сайт ({res})")
        except asyncio.TimeoutError:
            print(f"  [Система] ⏱️ Таймаут: Не удалось открыть сайт за {TOOL_TIMEOUT} сек")
        except Exception as e:
            print(f"  [Система] Ошибка при открытии сайта: {e}")

    elif "__VOLUME_COMMAND__:" in tool_text:
        try:
            _, action, amount = tool_text.split(":")
            await asyncio.wait_for(
                asyncio.to_thread(handle_volume_command, action, float(amount)),
                timeout=TOOL_TIMEOUT
            )
            print(f"[Система] Громкость изменена: {action}")
        except asyncio.TimeoutError:
            print(f"  [Система] ⏱️ Таймаут: Команда громкости не выполнена за {TOOL_TIMEOUT} сек")
        except Exception as e:
            print(f"[Система] Ошибка звука: {e}")

    elif "__MEDIA_COMMAND__:" in tool_text:
        try:
            action = tool_text.split(":")[1]
            await asyncio.wait_for(
                asyncio.to_thread(handle_media_command, action),
                timeout=TOOL_TIMEOUT
            )
            print(f"[Система] Медиа команда: {action}")
        except asyncio.TimeoutError:
            print(f"  [Система] ⏱️ Таймаут: Медиа команда не выполнена за {TOOL_TIMEOUT} сек")
        except Exception as e:
            print(f"[Система] Ошибка медиа: {e}")

    else:
        print(f"  [Система] Результат модуля: {tool_text}")
