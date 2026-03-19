import asyncio
import os
import subprocess
import sys
import time
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_ollama import ChatOllama
from loop import run_loop
from subagents import create_main_agent
from config import MODEL_NAMES
from utils import build_mcp_config

MODEL_NAME = MODEL_NAMES[0]

os.environ["NO_PROXY"] = "localhost,127.0.0.1,::1"
os.environ["no_proxy"] = "localhost,127.0.0.1,::1"

def start_ws_bridge():
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


def scan_apps():
    from app_scanner import scan_and_save
    count = scan_and_save()
    print(f"Найдено приложений: {count}")


async def main():
    start_ws_bridge()
    print("Инициализация ассистента...")
    await asyncio.to_thread(scan_apps)

    config = build_mcp_config()
    client = MultiServerMCPClient(config)

    sessions = {}
    cms = []
    for server_name in config.keys():
        cm = client.session(server_name)
        session = await cm.__aenter__()
        sessions[server_name] = (cm, session)
        cms.append(cm)

    try:
        all_tools = await client.get_tools()

        llm = ChatOllama(
            model=MODEL_NAME,
            temperature=0.1,
            top_p=0.8,
            repeat_penalty=1.1,
        )

        graph = create_main_agent(all_tools, llm)
        graph_config = {"configurable": {"thread_id": "pc_agent_session"}}

        await run_loop(graph, graph_config)

    finally:
        for cm, session in sessions.values():
            await cm.__aexit__(None, None, None)

if __name__ == "__main__":
    asyncio.run(main())
