import asyncio
import os
import subprocess
import sys
import time
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.checkpoint.memory import MemorySaver
from langchain_ollama import ChatOllama
from models import AssistantResponse
from loop import run_loop
from agents import create_graph
from config import MODEL_NAMES, FORMATTER_MODEL, system_prompt, formatter_prompt
from utils import build_mcp_config, set_system_volume

MODEL_NAME = MODEL_NAMES[0]

os.environ["NO_PROXY"] = "localhost,127.0.0.1,::1"
os.environ["no_proxy"] = "localhost,127.0.0.1,::1"

def start_ws_bridge():
    """Запускает ws_bridge.py как отдельный постоянный процесс."""
    import urllib.request
    try:
        urllib.request.urlopen("http://127.0.0.1:9010/status", timeout=1)
        return None
    except Exception:
        pass
    proc = subprocess.Popen(
        [sys.executable, "browser_extension/ws_bridge.py"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(1.5)
    return proc


async def main():
    start_ws_bridge()
    print("Инициализация ассистента...")

    config = build_mcp_config()
    client = MultiServerMCPClient(config)

    # Открываем сессии для каждого сервера вручную
    sessions = {}
    exit_stack = asyncio.Queue()  # просто для хранения cm

    cms = []
    for server_name in config.keys():
        cm = client.session(server_name)
        session = await cm.__aenter__()
        sessions[server_name] = (cm, session)
        cms.append(cm)

    try:
        tools = await client.get_tools()
        executor_llm = ChatOllama(
            model=MODEL_NAME,
            system=system_prompt,
            temperature=0.1,
            top_p=0.8,
            repeat_penalty=1.1,
            stop=["\n", "User:", "Ассистент:"]
        )
        formatter_llm = ChatOllama(
            model=FORMATTER_MODEL,
            temperature=0,
            num_predict=512,
            format=AssistantResponse.model_json_schema(),
        )

        memory = MemorySaver()
        graph, graph_config = create_graph(
            executor_llm,
            formatter_llm,
            tools,
            memory,
            AssistantResponse.model_json_schema(),
            formatter_prompt,
        )

        await run_loop(graph, graph_config)

    finally:
        # Закрываем все сессии при выходе
        for cm, session in sessions.values():
            await cm.__aexit__(None, None, None)

if __name__ == "__main__":
    asyncio.run(main())