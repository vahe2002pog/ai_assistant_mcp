"""
Обёртка над vault_manager для совместимости со старыми вызовами.
Весь реальный сторедж — в ui_automation/rag/vault_manager.py (Obsidian vault).
"""
from __future__ import annotations

from typing import List

from . import vault_manager


def save_experience(
    task: str,
    result: str,
    agent_types: list,
    app_names: list | None = None,
    use_llm: bool = True,  # параметр сохранён для обратной совместимости, игнорируется
) -> None:
    try:
        vault_manager.save_experience(task, result, agent_types=agent_types, app_names=app_names)
    except Exception as e:
        try:
            from ui_automation.utils import print_with_color
            print_with_color(f"[vault] Ошибка сохранения опыта: {e}", "yellow")
        except Exception:
            pass


def retrieve_experience(query: str, top_k: int = 3) -> List:
    try:
        return vault_manager.search(query, k=top_k, folder="Experience")
    except Exception:
        return []
