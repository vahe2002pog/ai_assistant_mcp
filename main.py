import asyncio
import os
from typing import List, Optional, Dict, Any, Literal
from models import AssistantResponse
from loop import run_loop
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langchain_ollama import ChatOllama
from utils import get_all_tools, set_system_volume
from agents import create_graph
from config import MODEL_NAMES, system_prompt, formatter_prompt

MODEL_NAME = MODEL_NAMES[0]

os.environ["NO_PROXY"] = "localhost,127.0.0.1,::1"
os.environ["no_proxy"] = "localhost,127.0.0.1,::1"

# Состояние графа
async def main():
    print("Инициализация ассистента...")
    
    tools = await get_all_tools()
    print(f"Подключено инструментов: {len(tools)}")
    
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

    # создаём граф выполнения (в agents.create_graph)
    graph, config = create_graph(
        executor_llm,
        formatter_llm,
        tools,
        memory,
        AssistantResponse.model_json_schema(),
        formatter_prompt,
    )

    # Передаём цикл обработки в модуль console
    await run_loop(graph, config)

if __name__ == "__main__":
    asyncio.run(main())