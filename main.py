import ast
import asyncio
import os
import subprocess
import webbrowser
from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, START, END, add_messages
from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.memory import MemorySaver
from langchain_ollama import ChatOllama
from langchain_mcp_adapters.client import MultiServerMCPClient
from typing import TypedDict, Annotated
from config import MODEL_NAMES, system_prompt

MODEL_NAME = MODEL_NAMES[0]

os.environ["NO_PROXY"] = "localhost,127.0.0.1,::1"
os.environ["no_proxy"] = "localhost,127.0.0.1,::1"

# Состояние графа
class State(TypedDict):
    messages: Annotated[list, add_messages]

async def get_all_tools():
    mcp_config = {
        "pc_modules": {
            "command": "python",
            "args": ["launch_mcp.py"], 
            "transport": "stdio",
        }
    }
    mcp_client = MultiServerMCPClient(mcp_config)
    return await mcp_client.get_tools()

async def main():
    print("Инициализация ассистента...")
    
    # 1. Инструменты
    tools = await get_all_tools()
    print(f"Подключено инструментов: {len(tools)}")
    
    # 2. Инициализация Ollama
    llm = ChatOllama(model=MODEL_NAME, system = system_prompt, temperature=0.1)
    
    # 3. Агент
    memory = MemorySaver()
    
    def agent_node(state: State):
        return {"messages": [llm.bind_tools(tools).invoke(state["messages"])]}

    workflow = StateGraph(State)
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", ToolNode(tools))
    workflow.add_edge(START, "agent")
    workflow.add_conditional_edges("agent", lambda state: "tools" if state["messages"][-1].tool_calls else END)
    workflow.add_edge("tools", "agent")
    
    graph = workflow.compile(checkpointer=memory)
    config = {"configurable": {"thread_id": "pc_agent_session"}}

    print("Ассистент готов! (Введите 'exit' для выхода)")
    
    while True:
        try:
            user_input = input("\nВы: ")
        except (KeyboardInterrupt, EOFError): break
        if user_input.lower() in ['exit', 'quit', 'выход']: break
        if not user_input.strip(): continue
        
        async for event in graph.astream({"messages": [HumanMessage(content=user_input)]}, config=config):
            for node, values in event.items():
                if node == "agent":
                    msg = values["messages"][-1]
                    if msg.content:
                        print(f"Ассистент: {msg.content}")
                        
                elif node == "tools":
                    # Получаем сырой ответ инструмента
                    raw_output = values["messages"][-1].content
                    
                    # Извлекаем текст (т.к. MCP может возвращать данные в виде JSON списка)
                    tool_text = str(raw_output)
                    if isinstance(raw_output, list) and len(raw_output) > 0 and isinstance(raw_output[0], dict):
                        tool_text = raw_output[0].get('text', str(raw_output))
                    elif isinstance(raw_output, str) and raw_output.startswith("[{"):
                        try:
                            parsed = ast.literal_eval(raw_output)
                            tool_text = parsed[0]['text']
                        except: pass

                    # ПЕРЕХВАТ КОМАНД: Выполняем действия в главном потоке main.py
                    if "__OPEN_FILE_COMMAND__:" in tool_text:
                        path_to_open = tool_text.split("__OPEN_FILE_COMMAND__:")[1].strip()
                        try:
                            os.startfile(path_to_open)
                            print(f"  [Система] Успех: Файл открыт ({path_to_open})")
                        except Exception as e:
                            print(f"  [Система] Ошибка при открытии файла: {e}")
                            
                    elif "__OPEN_FOLDER_COMMAND__:" in tool_text:
                        folder_to_open = tool_text.split("__OPEN_FOLDER_COMMAND__:")[1].strip()
                        try:
                            os.startfile(folder_to_open)
                            print(f"  [Система] Успех: Папка открыта ({folder_to_open})")
                        except Exception as e:
                            print(f"  [Система] Ошибка при открытии папки: {e}")
                            
                    elif "__OPEN_APP_COMMAND__:" in tool_text:
                        app_to_open = tool_text.split("__OPEN_APP_COMMAND__:")[1].strip()
                        try:
                            subprocess.Popen(f'start "" "{app_to_open}"', shell=True)
                            print(f"  [Система] Успех: Приложение запущено ({app_to_open})")
                        except Exception as e:
                             print(f"  [Система] Ошибка при запуске приложения: {e}")
                             
                    elif "__OPEN_URL_COMMAND__:" in tool_text:
                        url_to_open = tool_text.split("__OPEN_URL_COMMAND__:")[1].strip()
                        try:
                            # Открываем ссылку через webbrowser, чтобы использовался браузер по умолчанию
                            webbrowser.open(url_to_open)
                            print(f"  [Система] Успех: Открыт веб-сайт ({url_to_open})")
                        except Exception as e:
                            print(f"  [Система] Ошибка при открытии сайта: {e}")

                    else:
                        # Если это обычный ответ (например, список файлов), просто выводим его
                        print(f"  [Система] Результат модуля: {tool_text}")

if __name__ == "__main__":
    asyncio.run(main())