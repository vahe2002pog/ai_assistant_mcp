import ast
import asyncio
import os
import json
import subprocess
import webbrowser
from typing import TypedDict, Annotated, List, Optional, Dict, Any, Literal
from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, START, END, add_messages
from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.memory import MemorySaver
from langchain_ollama import ChatOllama
from langchain_mcp_adapters.client import MultiServerMCPClient
from config import MODEL_NAMES, system_prompt, formatter_prompt

MODEL_NAME = MODEL_NAMES[0]

os.environ["NO_PROXY"] = "localhost,127.0.0.1,::1"
os.environ["no_proxy"] = "localhost,127.0.0.1,::1"

class ScreenBlock(BaseModel):
    type: Literal["text", "list", "table", "links", "files"]

    title: Optional[str] = None

    text: Optional[str] = None

    items: Optional[List[str]] = None

    rows: Optional[List[Dict[str, Any]]] = None

    links: Optional[List[str]] = None

    file_paths: Optional[List[str]] = None


class ScreenData(BaseModel):
    blocks: List[ScreenBlock]


class AssistantResponse(BaseModel):
    voice: str = Field(
        description="Короткий ответ для синтеза речи (1 предложение, до 8 слов)"
    )

    screen: Optional[ScreenData] = None

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
    
    tools = await get_all_tools()
    print(f"Подключено инструментов: {len(tools)}")
    
    # 3. Инициализация Ollama: executor и formatter
    executor_llm = ChatOllama(
        model=MODEL_NAME,
        system=system_prompt,
        temperature=0.1,
        top_p=0.8,
        repeat_penalty=1.1,
        stop=["\n", "User:", "Ассистент:"]
    )


    formatter_llm = ChatOllama(
        model=MODEL_NAME,
        temperature=0,
        format=AssistantResponse.model_json_schema(),
    )
    
    memory = MemorySaver()
    
    def executor_node(state: State):
        return {
            "messages": [
                executor_llm.bind_tools(tools).invoke(state["messages"])
            ]
        }

    def formatter_node(state: State):
        user_message = None
        assistant_message = None

        # ищем последний запрос пользователя
        for msg in reversed(state["messages"]):
            if msg.type == "human":
                user_message = msg.content
                break

        # последний ответ ассистента
        assistant_message = state["messages"][-1].content

        prompt = f"""
            {formatter_prompt}

            Запрос пользователя:
            {user_message}

            Ответ ассистента:
            {assistant_message}
        """

        result = formatter_llm.invoke([
            HumanMessage(content=prompt)
        ])

        return {"messages": [result]}

    workflow = StateGraph(State)
    workflow.add_node("executor", executor_node)
    workflow.add_node("tools", ToolNode(tools))
    workflow.add_node("formatter", formatter_node)
    workflow.add_edge(START, "executor")
    workflow.add_conditional_edges(
        "executor",
        lambda state: "tools" if state["messages"][-1].tool_calls else "formatter"
    )
    workflow.add_edge("tools", "executor")
    workflow.add_edge("formatter", END)
    
    graph = workflow.compile(checkpointer=memory)
    config = {"configurable": {"thread_id": "pc_agent_session"}}

    print("Ассистент готов! (Введите 'exit' для выхода)")
    
    while True:
        try:
            user_input = input("\nВы: ")
        except (KeyboardInterrupt, EOFError): break
        if user_input.lower() in['exit', 'quit', 'выход']: break
        if not user_input.strip(): continue
        
        async for event in graph.astream({"messages": [HumanMessage(content=user_input)]}, config=config):
            for node, values in event.items():
                if node == "formatter":
                    msg = values["messages"][-1]
                    # Если есть текстовый ответ (форматированный JSON)
                    if msg.content:
                        try:
                            data = json.loads(msg.content)

                            print(f"\n🗣  Голос: {data.get('voice', '')}")

                            # Вывод экранной части (новая блоковая структура)
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
                    # Краткий отладочный вывод сырого ответа исполнителя
                    msg = values["messages"][-1]
                    if msg.content:
                        print(f"\n[executor] {msg.content}")
                        
                elif node == "tools":
                    raw_output = values["messages"][-1].content
                    
                    tool_text = str(raw_output)
                    if isinstance(raw_output, list) and len(raw_output) > 0 and isinstance(raw_output[0], dict):
                        tool_text = raw_output[0].get('text', str(raw_output))
                    elif isinstance(raw_output, str) and raw_output.startswith("[{"):
                        try:
                            parsed = ast.literal_eval(raw_output)
                            tool_text = parsed[0]['text']
                        except: pass

                    # ПЕРЕХВАТ КОМАНД
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
                            print(f"[Система] Успех: Папка открыта ({folder_to_open})")
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
                            webbrowser.open(url_to_open)
                            print(f"[Система] Успех: Открыт веб-сайт ({url_to_open})")
                        except Exception as e:
                            print(f"  [Система] Ошибка при открытии сайта: {e}")

                    else:
                        print(f"  [Система] Результат модуля: {tool_text}")

if __name__ == "__main__":
    asyncio.run(main())