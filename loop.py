import asyncio
import json
import urllib.request
from langchain_core.messages import HumanMessage
from utils import handle_tool_command


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

    # Открытые окна (COM требует вызов из отдельного потока)
    try:
        import asyncio as _aio

        def _get_windows():
            import uiautomation as auto
            auto.SetGlobalSearchTimeout(2)
            windows = [w for w in auto.GetRootControl().GetChildren()
                       if w.ControlTypeName == "WindowControl" and w.Name]
            return [w.Name[:40] for w in windows[:12]]

        names = await _aio.to_thread(_get_windows)
        if names:
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
            if node == "executor":
                msg = values["messages"][-1]
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    for tc in msg.tool_calls:
                        args_str = ", ".join(f"{k}={repr(v)}" for k, v in tc.get("args", {}).items())
                        print(f"  → {tc['name']}({args_str})")

            elif node == "formatter":
                msg = values["messages"][-1]
                if msg.content:
                    print(f"\nАссистент: {msg.content}")

            elif node == "tools":
                for msg in values.get("messages", []):
                    raw_output = msg.content
                    tool_text = str(raw_output)
                    if isinstance(raw_output, list) and raw_output and isinstance(raw_output[0], dict):
                        tool_text = raw_output[0].get("text", str(raw_output))
                    elif isinstance(raw_output, str) and raw_output.startswith("[{"):
                        try:
                            import ast as _ast
                            parsed = _ast.literal_eval(raw_output)
                            tool_text = parsed[0]["text"]
                        except Exception:
                            pass
                    await handle_tool_command(tool_text)



