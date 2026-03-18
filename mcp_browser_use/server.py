import asyncio
import os
import sys
import traceback
from typing import List, Optional

import logging
logging.getLogger().addHandler(logging.NullHandler())
logging.getLogger().propagate = False

from mcp_browser_use.agent.custom_prompts import (
    CustomAgentMessagePrompt,
    CustomSystemPrompt,
)

from browser_use import BrowserConfig
from browser_use.browser.context import BrowserContextConfig, BrowserContextWindowSize
from fastmcp.server import FastMCP
from main_content_extractor import MainContentExtractor
from mcp.types import TextContent

from mcp_browser_use.agent.custom_agent import CustomAgent
from mcp_browser_use.browser.custom_browser import CustomBrowser
from mcp_browser_use.controller.custom_controller import CustomController
from mcp_browser_use.utils import utils
from mcp_browser_use.utils.agent_state import AgentState

# Global references for single "running agent" approach
_global_agent = None
_global_browser = None
_global_browser_context = None
_global_agent_state = AgentState()

app = FastMCP("mcp_browser_use")


def get_env_bool(key: str, default: bool = False) -> bool:
    """Get boolean value from environment variable."""
    return os.getenv(key, str(default)).lower() in ("true", "1", "yes")


async def _safe_cleanup():
    """Safely clean up browser resources"""
    global _global_browser, _global_agent_state, _global_browser_context, _global_agent

    try:
        if _global_agent_state:
            try:
                await _global_agent_state.request_stop()
            except Exception:
                pass

        if _global_browser_context:
            try:
                await _global_browser_context.close()
            except Exception:
                pass

        if _global_browser:
            try:
                await _global_browser.close()
            except Exception:
                pass

    except Exception as e:
        # Log the error, but don't re-raise
        print(f"Error during cleanup: {e}", file=sys.stderr)
    finally:
        # Reset global variables
        _global_browser = None
        _global_browser_context = None
        _global_agent_state = AgentState()
        _global_agent = None


@app.tool()
async def run_browser_agent(task: str, add_infos: str = "") -> str:
    """Handle run-browser-agent tool calls."""
    global _global_agent, _global_browser, _global_browser_context, _global_agent_state

    try:
        # Clear any previous agent stop signals
        _global_agent_state.clear_stop()

        # Get browser configuration
        headless = get_env_bool("BROWSER_HEADLESS", True)
        disable_security = get_env_bool("BROWSER_DISABLE_SECURITY", False)
        window_w = int(os.getenv("BROWSER_WINDOW_WIDTH", "1280"))
        window_h = int(os.getenv("BROWSER_WINDOW_HEIGHT", "720"))

        # Get agent configuration
        model_provider = os.getenv("MCP_MODEL_PROVIDER", "openrouter")
        model_name = os.getenv("MCP_MODEL_NAME", "openai/o3-mini-high")
        temperature = float(os.getenv("MCP_TEMPERATURE", "0.7"))
        max_steps = int(os.getenv("MCP_MAX_STEPS", "100"))
        use_vision = get_env_bool("MCP_USE_VISION", True)
        max_actions_per_step = int(os.getenv("MCP_MAX_ACTIONS_PER_STEP", "5"))
        tool_calling_method = os.getenv("MCP_TOOL_CALLING_METHOD", "auto")

        # Configure browser window size
        extra_chromium_args = [f"--window-size={window_w},{window_h}"]

        # Initialize browser if needed
        if not _global_browser:
            _global_browser = CustomBrowser(
                config=BrowserConfig(
                    headless=headless,
                    disable_security=disable_security,
                    extra_chromium_args=extra_chromium_args,
                )
            )

        # Initialize browser context if needed
        if not _global_browser_context:
            _global_browser_context = await _global_browser.new_context(
                config=BrowserContextConfig(
                    trace_path=os.getenv("BROWSER_TRACE_PATH"),
                    save_recording_path=os.getenv("BROWSER_RECORDING_PATH"),
                    no_viewport=False,
                    browser_window_size=BrowserContextWindowSize(
                        width=window_w, height=window_h
                    ),
                )
            )

        # Prepare LLM
        llm = utils.get_llm_model(
            provider=model_provider, model_name=model_name, temperature=temperature
        )

        # Create controller and agent
        controller = CustomController()
        _global_agent = CustomAgent(
            task=task,
            add_infos=add_infos,
            use_vision=use_vision,
            llm=llm,
            browser=_global_browser,
            browser_context=_global_browser_context,
            controller=controller,
            system_prompt_class=CustomSystemPrompt,
            agent_prompt_class=CustomAgentMessagePrompt,
            max_actions_per_step=max_actions_per_step,
            agent_state=_global_agent_state,
            tool_calling_method=tool_calling_method,
        )

        # Run agent with improved error handling
        try:
            history = await _global_agent.run(max_steps=max_steps)
            final_result = (
                history.final_result()
                or f"No final result. Possibly incomplete. {history}"
            )
            return final_result
        except asyncio.CancelledError:
            return "Task was cancelled"
        except Exception as e:
            logging.error(f"Agent run error: {str(e)}\n{traceback.format_exc()}")
            return f"Error during task execution: {str(e)}"

    except Exception as e:
        logging.error(f"run-browser-agent error: {str(e)}\n{traceback.format_exc()}")
        return f"Error during task execution: {str(e)}"

    finally:
        asyncio.create_task(_safe_cleanup())


async def _ensure_browser():
    """Initialize browser and context if not already running."""
    global _global_browser, _global_browser_context

    headless = get_env_bool("BROWSER_HEADLESS", True)
    disable_security = get_env_bool("BROWSER_DISABLE_SECURITY", False)
    window_w = int(os.getenv("BROWSER_WINDOW_WIDTH", "1280"))
    window_h = int(os.getenv("BROWSER_WINDOW_HEIGHT", "720"))

    if not _global_browser:
        _global_browser = CustomBrowser(
            config=BrowserConfig(
                headless=headless,
                disable_security=disable_security,
                extra_chromium_args=[f"--window-size={window_w},{window_h}"],
            )
        )

    if not _global_browser_context:
        _global_browser_context = await _global_browser.new_context(
            config=BrowserContextConfig(
                no_viewport=False,
                browser_window_size=BrowserContextWindowSize(width=window_w, height=window_h),
            )
        )

    return _global_browser_context


@app.tool()
async def browser_navigate(url: str) -> str:
    """Navigate browser to the given URL."""
    ctx = await _ensure_browser()
    page = await ctx.get_current_page()
    await page.goto(url)
    await page.wait_for_load_state()
    return f"Navigated to {url}"


@app.tool()
async def browser_get_state() -> str:
    """Get current browser state: URL, title, and interactive elements with their indexes.
    Always call this before browser_click or browser_input_text to get fresh element indexes."""
    ctx = await _ensure_browser()
    state = await ctx.get_state()

    lines = [
        f"URL: {state.url}",
        f"Title: {state.title}",
        f"Tabs: {len(state.tabs)}",
        "",
        "Interactive elements (use index with browser_click / browser_input_text):",
    ]

    for idx, elem in state.selector_map.items():
        tag = elem.tag_name
        text = elem.get_all_text_till_next_clickable_element(max_depth=2).strip()
        attrs = elem.attributes or {}
        placeholder = attrs.get("placeholder", "")
        elem_type = attrs.get("type", "")
        desc = text or placeholder or elem_type or tag
        lines.append(f"  [{idx}] <{tag}> {desc[:100]}")

    return "\n".join(lines)


@app.tool()
async def browser_click(index: int) -> str:
    """Click an interactive element by its index. Get indexes from browser_get_state."""
    ctx = await _ensure_browser()
    session = await ctx.get_session()
    state = session.cached_state

    if index not in state.selector_map:
        return f"Error: element [{index}] not found. Call browser_get_state to refresh."

    element_node = state.selector_map[index]
    await ctx._click_element_node(element_node)
    text = element_node.get_all_text_till_next_clickable_element(max_depth=2).strip()
    return f"Clicked [{index}]: {text[:100]}"


@app.tool()
async def browser_input_text(index: int, text: str) -> str:
    """Type text into an input field by its index. Get indexes from browser_get_state."""
    ctx = await _ensure_browser()
    session = await ctx.get_session()
    state = session.cached_state

    if index not in state.selector_map:
        return f"Error: element [{index}] not found. Call browser_get_state to refresh."

    element_node = state.selector_map[index]
    await ctx._input_text_element_node(element_node, text)
    return f'Typed "{text}" into element [{index}]'


@app.tool()
async def browser_search_google(query: str) -> str:
    """Search Google for the given query in the current tab."""
    ctx = await _ensure_browser()
    page = await ctx.get_current_page()
    await page.goto(f"https://www.google.com/search?q={query}&udm=14")
    await page.wait_for_load_state()
    return f'Searched Google for "{query}"'


@app.tool()
async def browser_extract_content(include_links: bool = False) -> str:
    """Extract text content from the current page. Set include_links=True to get markdown with links."""
    ctx = await _ensure_browser()
    page = await ctx.get_current_page()
    output_format = "markdown" if include_links else "text"
    content = MainContentExtractor.extract(
        html=await page.content(),
        output_format=output_format,
    )
    return content


@app.tool()
async def browser_scroll_down(amount: Optional[int] = None) -> str:
    """Scroll down the current page. Optionally specify pixel amount."""
    ctx = await _ensure_browser()
    page = await ctx.get_current_page()
    if amount is not None:
        await page.evaluate(f"window.scrollBy(0, {amount});")
        return f"Scrolled down {amount} pixels"
    else:
        await page.keyboard.press("PageDown")
        return "Scrolled down one page"


@app.tool()
async def browser_scroll_up(amount: Optional[int] = None) -> str:
    """Scroll up the current page. Optionally specify pixel amount."""
    ctx = await _ensure_browser()
    page = await ctx.get_current_page()
    if amount is not None:
        await page.evaluate(f"window.scrollBy(0, -{amount});")
        return f"Scrolled up {amount} pixels"
    else:
        await page.keyboard.press("PageUp")
        return "Scrolled up one page"


@app.tool()
async def browser_go_back() -> str:
    """Navigate back in browser history."""
    ctx = await _ensure_browser()
    await ctx.go_back()
    return "Navigated back"


@app.tool()
async def browser_send_keys(keys: str) -> str:
    """Send keyboard keys or shortcuts. Examples: 'Enter', 'Escape', 'Control+a', 'Control+Shift+T'."""
    ctx = await _ensure_browser()
    page = await ctx.get_current_page()
    await page.keyboard.press(keys)
    return f"Sent keys: {keys}"


@app.tool()
async def browser_open_tab(url: str) -> str:
    """Open a URL in a new browser tab."""
    ctx = await _ensure_browser()
    await ctx.create_new_tab(url)
    return f"Opened new tab: {url}"


@app.tool()
async def browser_switch_tab(page_id: int) -> str:
    """Switch to a browser tab by its ID (visible in browser_get_state output)."""
    ctx = await _ensure_browser()
    await ctx.switch_to_tab(page_id)
    page = await ctx.get_current_page()
    await page.wait_for_load_state()
    return f"Switched to tab {page_id}"


@app.tool()
async def browser_close() -> str:
    """Close the browser and release all resources."""
    await _safe_cleanup()
    return "Browser closed"


def main():
    app.run()


if __name__ == "__main__":
    main()
