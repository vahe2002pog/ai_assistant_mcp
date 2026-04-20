"""
TraceStore — запись ExecutionTrace в JSONL на диск. Один файл в день,
по строке на задачу. Нужен для реплея, отладки и метрик.

Лёгкий: без БД, без блокировок между процессами (одна строка = один json.dumps).
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from typing import Optional

from ui_automation.agents.contracts import ExecutionTrace, StepStatus


_DEFAULT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "traces")
_LOCK = threading.Lock()


class TraceStore:
    def __init__(self, dir_path: Optional[str] = None) -> None:
        self.dir_path = os.path.abspath(dir_path or _DEFAULT_DIR)
        os.makedirs(self.dir_path, exist_ok=True)

    def save(self, trace: ExecutionTrace) -> str:
        day = datetime.now().strftime("%Y-%m-%d")
        path = os.path.join(self.dir_path, f"{day}.jsonl")
        line = trace.model_dump_json(exclude_none=False)
        with _LOCK:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        return path

    # ── Metrics ───────────────────────────────────────────────────────────────

    def recent_metrics(self, day: Optional[str] = None) -> dict:
        """Агрегирует метрики по одному дневному файлу: success rate, средняя
        длина плана, частота реплана (через retries), доля отказов верификатора."""
        day = day or datetime.now().strftime("%Y-%m-%d")
        path = os.path.join(self.dir_path, f"{day}.jsonl")
        if not os.path.exists(path):
            return {}

        total = 0
        success = 0
        plan_lens = 0
        retries = 0
        verify_rejected = 0
        verify_total = 0

        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                total += 1
                if d.get("final_status") == StepStatus.SUCCESS.value:
                    success += 1
                plan_lens += len(d.get("plan", {}).get("steps", []))
                for r in d.get("step_results", []):
                    retries += r.get("retries_used", 0)
                for v in d.get("verifications", []):
                    verify_total += 1
                    if v.get("verdict") == "rejected":
                        verify_rejected += 1

        return {
            "day": day,
            "tasks": total,
            "success_rate": (success / total) if total else 0.0,
            "avg_plan_len": (plan_lens / total) if total else 0.0,
            "total_retries": retries,
            "verify_reject_rate": (verify_rejected / verify_total) if verify_total else 0.0,
        }


__all__ = ["TraceStore"]
