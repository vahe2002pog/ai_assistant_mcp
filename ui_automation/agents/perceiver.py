"""
Perceiver — собирает актуальное состояние мира ПОСЛЕ каждого шага,
чтобы Planner мог решить, что делать дальше.

Ключевое: foreground-окно резолвится через Win32 `GetForegroundWindow`,
а не через пустой `_find_win("")` в pywinauto (который возвращает первое
UIA-окно — часто Панель задач). И UIA-дерево собирается ИМЕННО для
активного окна, а не «какого-то».
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

from ui_automation import utils
from ui_automation.agents.contracts import AgentType, StepResult, StepSpec


class Perceiver:
    MAX_PERCEPTION_CHARS = 3500

    def perceive(
        self,
        last_step: Optional[StepSpec],
        last_result: Optional[StepResult],
    ) -> str:
        agent = last_step.agent if last_step else None
        try:
            if agent is None:
                text = self._initial()
            elif agent == AgentType.SYSTEM:
                text = self._system()
            elif agent == AgentType.BROWSER:
                text = self._browser()
            elif agent == AgentType.VISION:
                text = (last_result.summary if last_result else "")
            else:  # chat / web
                text = (last_result.summary if last_result else "")
        except Exception as e:
            utils.print_with_color(f"[Perceiver] error: {e}", "yellow")
            text = f"(восприятие не удалось: {e})"

        return text

    # ── Foreground / window enumeration ───────────────────────────────────────

    @staticmethod
    def _foreground_info() -> Tuple[Optional[int], str, str]:
        """(hwnd, title, class) активного окна — через Win32, надёжно."""
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return None, "", ""
            length = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = (buf.value or "").strip()
            clsbuf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, clsbuf, 256)
            cls = (clsbuf.value or "").strip()
            return hwnd, title, cls
        except Exception:
            return None, "", ""

    @staticmethod
    def _enum_windows() -> List[Tuple[str, str]]:
        """Полный список видимых окон через EnumWindows (быстрее и полнее UIA)."""
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            out: List[Tuple[str, str]] = []

            @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            def _cb(hwnd, _lp):
                try:
                    if not user32.IsWindowVisible(hwnd):
                        return True
                    ln = user32.GetWindowTextLengthW(hwnd)
                    if ln == 0:
                        return True
                    tb = ctypes.create_unicode_buffer(ln + 1)
                    user32.GetWindowTextW(hwnd, tb, ln + 1)
                    t = (tb.value or "").strip()
                    if not t:
                        return True
                    cb = ctypes.create_unicode_buffer(256)
                    user32.GetClassNameW(hwnd, cb, 256)
                    out.append((t, (cb.value or "").strip()))
                except Exception:
                    pass
                return True

            user32.EnumWindows(_cb, 0)
            # убираем дубликаты по заголовку, порядок сохраняем
            seen, uniq = set(), []
            for t, c in out:
                if t in seen:
                    continue
                seen.add(t)
                uniq.append((t, c))
            return uniq
        except Exception:
            return []

    @staticmethod
    def _browser_tabs_brief() -> str:
        """Короткий список вкладок через расширение (если есть)."""
        try:
            from mcp_modules.tools_browser import _send_sync
            resp = _send_sync("get_all_tabs", None, timeout=1.5) or {}
            tabs = resp.get("tabs") or []
            if not tabs:
                return ""
            lines = ["[browser tabs]"]
            for t in tabs:
                mark = "*" if t.get("active") else " "
                lines.append(f"  {mark} {(t.get('title') or '')} — {(t.get('url') or '')}")
            return "\n".join(lines)
        except Exception:
            return ""

    # ── Source-specific ───────────────────────────────────────────────────────

    def _windows_block(self) -> str:
        wins = self._enum_windows()
        if not wins:
            return ""
        lines = [f"• '{t}' [{c}]" for t, c in wins]
        return "[открытые окна]\n" + "\n".join(lines)

    def _initial(self) -> str:
        _, title, cls = self._foreground_info()
        blocks = [f"[foreground]\n'{title}' [{cls}]" if title else "[foreground]\n—"]
        win_block = self._windows_block()
        if win_block:
            blocks.append(win_block)
        tabs = self._browser_tabs_brief()
        if tabs:
            blocks.append(tabs)
        return "\n\n".join(blocks)

    def _system(self) -> str:
        """После system-шага — UIA-дерево ИМЕННО активного окна."""
        _, title, cls = self._foreground_info()
        blocks = [f"[foreground]\n'{title}' [{cls}]" if title else "[foreground]\n—"]

        if not title:
            return "\n\n".join(blocks)

        # Экранируем спецсимволы regex, чтобы 'Word' не матчил случайности.
        title_re = re.escape(title)

        try:
            from mcp_modules.tools_uiautomation import ui_list_interactive
        except Exception:
            return "\n\n".join(blocks)

        # Собираем ВСЕ интерактивные элементы дерева одним списком: кнопки,
        # пункты меню, вкладки, ListItem'ы (как «Новый документ» в Word),
        # ссылки, поля ввода и т.д. — плоская выборка, отсортированная по
        # позиции. Planner/worker выбирает нужный по тексту, а не по типу.
        try:
            data = ui_list_interactive(title_re=title_re, max_items=1000) or ""
        except Exception:
            data = ""
        if data and "не найден" not in data.lower():
            blocks.append(f"[интерактивные элементы]\n{data}")

        # Список окон — полезен, если планировщику надо переключиться.
        win_block = self._windows_block()
        if win_block:
            blocks.append(win_block)

        return "\n\n".join(blocks)

    @staticmethod
    def _browser() -> str:
        try:
            from mcp_modules.tools_browser import _send_sync
        except Exception:
            return ""
        try:
            res = _send_sync("getState", {}, timeout=8.0) or {}
            if isinstance(res, dict):
                state = res.get("state", res)
                import json
                return json.dumps(state, ensure_ascii=False)
            return str(res)
        except Exception as e:
            return f"(browser state недоступен: {e})"


__all__ = ["Perceiver"]
