"""The runtime's view of the AI — two calls, both safe to fail.

The orchestrator asks for exactly two things during an interview: read this
turn, and write me a follow-up. It gets them from here rather than from the
gateway directly, so the turn loop never has to know about workloads, schemas,
or whether a provider is configured at all.

`LLMError` and `get_llm()` keep the names the candidate runtime has always used.
That is not nostalgia — it means the orchestrator, the JD analysis and the
recruiter API moved into this repository without a single call-site change, and
a preserved runtime with no diff is a preserved runtime you can actually trust.
"""
from __future__ import annotations

from typing import Any

from services.ai.gateway import AIError, Workload, get_gateway
from services.ai.workloads import answer_classifier, followup_generator


class LLMError(RuntimeError):
    """Kept for callers that predate the gateway."""


class RuntimeBrain:
    """Adapter: workloads in, the runtime's two-method interface out."""

    def __init__(self, session_id: str = "_system") -> None:
        self.session_id = session_id

    @property
    def name(self) -> str:
        return get_gateway().name

    def read_answer(
        self, question: str, answer: str, looking_for: list[str], session_id: str = ""
    ) -> dict[str, Any]:
        # The caller names the session so the gateway's telemetry lands on THAT
        # candidate's trail. Without it every runtime call was written to
        # `_system`, and "which call made this interview feel slow" could only be
        # answered by correlating timestamps across every session at once.
        return answer_classifier.read_answer(
            question, answer, looking_for, session_id=session_id or self.session_id
        )

    def write_probe(
        self, question: str, answer: str, missing: list[str], quote: str,
        asked_already: list[str], session_id: str = "",
    ) -> dict[str, Any]:
        return followup_generator.write_probe(
            question, answer, missing, quote, asked_already,
            session_id=session_id or self.session_id,
        )

    def complete_json(self, system: str, user: str, max_tokens: int = 4000) -> dict[str, Any]:
        """A free-form structured call for design-time work.

        Kept for the interview designer, which has its own schema and its own
        prompt. Raises `LLMError` with no provider so the caller can say
        "add a key to analyse a job description" rather than showing a stack.
        """
        if not get_gateway().live:
            raise LLMError("no model provider configured — set OPENROUTER_API_KEY")
        try:
            result = get_gateway().generate(
                Workload.INTERVIEW_DESIGNER,
                system,
                user,
                max_tokens=max_tokens,
                session_id=self.session_id,
            )
            if not result.success:
                raise LLMError(result.error or "model call failed")
            from services.ai.gateway import _extract_json

            return _extract_json(result.text)
        except AIError as exc:
            raise LLMError(str(exc)) from exc


_BRAIN: RuntimeBrain | None = None


def get_llm() -> RuntimeBrain:
    global _BRAIN
    if _BRAIN is None:
        _BRAIN = RuntimeBrain()
    return _BRAIN
