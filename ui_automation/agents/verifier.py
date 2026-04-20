"""
Verifier — «глаза» Controller'а. Принимает StepSpec + StepResult и проверяет
через Vision-LLM, действительно ли ожидаемый результат виден на экране.

Критическое свойство: Verifier — это ДРУГОЙ агент, не тот, кто выполнил
действие. Это устраняет confirmation bias.

Используется только для шагов с `requires_verification=True` и только после
SUCCESS от worker'а (верификация провала бессмысленна).
"""
from __future__ import annotations

import json
import os
from typing import Optional

import openai

from ui_automation import llm_config as _llm, utils
from ui_automation.agents.contracts import (
    StepResult, StepSpec, VerificationResult, VerificationVerdict,
)


_VERIFY_SYSTEM = (
    "/no_think\n"
    "Ты — верификатор. На скриншоте — текущее состояние экрана ПОСЛЕ "
    "действия агента. Сравни ожидаемый результат с тем, что видишь.\n"
    "Верни ТОЛЬКО JSON:\n"
    "{\"verdict\": \"confirmed\" | \"rejected\" | \"uncertain\", "
    "\"reason\": \"краткое объяснение\"}\n"
    "Правила:\n"
    "• confirmed — ожидаемое состояние чётко видно.\n"
    "• rejected — видно, что действие НЕ привело к ожидаемому результату "
    "(например, ошибка, то же состояние что и до, неверное окно).\n"
    "• uncertain — экрана/данных не хватает для уверенного вывода.\n"
    "Не угадывай: при сомнениях — uncertain."
)


class Verifier:
    """Stateless Vision-проверяльщик."""

    def verify(self, step: StepSpec, result: StepResult) -> VerificationResult:
        expected = step.expected_outcome or step.parameters.get("task", "")
        if not expected:
            return VerificationResult(
                step_id=step.step_id,
                verdict=VerificationVerdict.UNCERTAIN,
                reason="no expected_outcome provided",
            )

        b64, path = self._screenshot()
        if not b64:
            return VerificationResult(
                step_id=step.step_id,
                verdict=VerificationVerdict.UNCERTAIN,
                reason="screenshot failed",
            )

        user_text = (
            f"Ожидаемый результат: {expected}\n"
            f"Сообщение исполнителя: {result.summary}"
        )
        try:
            resp = _llm.get_client().chat.completions.create(
                model=_llm.get_model(),
                messages=[
                    {"role": "system", "content": _VERIFY_SYSTEM},
                    {"role": "user", "content": [
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/png;base64,{b64}"}},
                        {"type": "text", "text": user_text},
                    ]},
                ],
                temperature=0.0,
                max_tokens=200,
                extra_body=_llm.get_extra_body(),
                response_format={"type": "json_object"},
            )
            raw = (resp.choices[0].message.content or "").strip()
            return self._parse(raw, step.step_id, path)
        except openai.APIConnectionError:
            return VerificationResult(
                step_id=step.step_id,
                verdict=VerificationVerdict.UNCERTAIN,
                reason="LLM unreachable",
                screenshot_path=path,
            )
        except Exception as e:
            utils.print_with_color(f"[Verifier] error: {e}", "yellow")
            return VerificationResult(
                step_id=step.step_id,
                verdict=VerificationVerdict.UNCERTAIN,
                reason=f"verifier exception: {e}",
                screenshot_path=path,
            )

    # ── Internal ──────────────────────────────────────────────────────────────

    @staticmethod
    def _screenshot():
        try:
            from mcp_modules.tools_vision import capture_base64
        except Exception:
            return "", None
        try:
            b64 = capture_base64("")
            return b64, None
        except Exception as e:
            utils.print_with_color(f"[Verifier] screenshot failed: {e}", "yellow")
            return "", None

    @staticmethod
    def _parse(raw: str, step_id: str, path: Optional[str]) -> VerificationResult:
        try:
            o_s, o_e = raw.find("{"), raw.rfind("}") + 1
            obj = json.loads(raw[o_s:o_e]) if o_s != -1 else {}
            verdict_str = str(obj.get("verdict", "uncertain")).lower().strip()
            reason = str(obj.get("reason", ""))
            mapping = {
                "confirmed": VerificationVerdict.CONFIRMED,
                "rejected":  VerificationVerdict.REJECTED,
                "uncertain": VerificationVerdict.UNCERTAIN,
            }
            verdict = mapping.get(verdict_str, VerificationVerdict.UNCERTAIN)
        except Exception:
            verdict = VerificationVerdict.UNCERTAIN
            reason = f"parse error: {raw}"
        return VerificationResult(
            step_id=step_id,
            verdict=verdict,
            reason=reason,
            screenshot_path=path,
        )


__all__ = ["Verifier"]
