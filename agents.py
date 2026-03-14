from typing import Any, TypedDict, Annotated, List
from langgraph.graph import StateGraph, START, END, add_messages
from langgraph.prebuilt import ToolNode
from langchain_core.messages import HumanMessage


class State(TypedDict):
    messages: Annotated[List, add_messages]


def create_graph(executor_llm, formatter_llm, tools, memory, formatter_schema, formatter_prompt=None):
    """Создаёт и компилирует граф выполнения для ассистента.

    Возвращает скомпилированный граф и конфиг для запуска.
    """
    # executor node — использует bound executor_llm
    def executor_node(state: dict):
        return {"messages": [executor_llm.bind_tools(tools).invoke(state["messages"]) ]}

    def formatter_node(state: dict):
        user_message = None
        for msg in reversed(state["messages"]):
            if msg.type == "human":
                user_message = msg.content
                break

        assistant_message = state["messages"][-1].content

        fp = formatter_prompt or ""
        prompt = f"""
            {fp}

            Запрос пользователя:
            {user_message}

            Ответ ассистента:
            {assistant_message}
        """

        result = formatter_llm.invoke([HumanMessage(content=prompt)])
        return {"messages": [result]}

    workflow = StateGraph(State)
    workflow.add_node("executor", executor_node)
    workflow.add_node("tools", ToolNode(tools))
    workflow.add_node("formatter", formatter_node)
    workflow.add_edge(START, "executor")
    workflow.add_conditional_edges(
        "executor",
        lambda state: "tools" if state["messages"][-1].tool_calls else "formatter",
    )
    workflow.add_edge("tools", "executor")
    workflow.add_edge("formatter", END)

    graph = workflow.compile(checkpointer=memory)
    config = {"configurable": {"thread_id": "pc_agent_session"}}
    return graph, config
