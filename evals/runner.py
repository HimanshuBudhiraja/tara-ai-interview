"""`TaraEvaluationRunner` — one test case, one workload, one model.

It calls the **production prompts** through the **production gateway** with the
model overridden. That is the whole design: evaluating a prompt the product does
not ship would measure something nobody will ever run.

Isolation from production is by construction rather than by convention:

  * nothing here writes an interview, a session, a version or an invitation;
  * telemetry is written under the `_eval` audit id, so an evaluation never
    appears on a candidate's decision trail;
  * the gateway's per-workload model config is never mutated — the model is
    passed per call.

A failed call is a recorded result with `success: False`, not an exception. A
model id that has been retired from the OpenRouter catalogue should show up as a
row in the table saying so, not as a crash forty cases into a run.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

from packages.schemas import SCHEMAS, SchemaError, validate
from services.ai.gateway import (
    AIError,
    CallStatus,
    OutputTruncated,
    ProviderUnavailable,
    RateLimited,
    TransportError,
    Workload,
    get_gateway,
    workload_config,
)

EVAL_AUDIT_ID = "_eval"


@dataclass
class EvalResult:
    """One (workload, model, test case) observation."""

    workload: str
    model: str
    test_case_id: str
    success: bool = False
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    raw_output: Any = None
    parsed_output: dict[str, Any] | None = None
    validation_errors: list[str] = field(default_factory=list)
    #: The request never reached the provider. Excluded from a model's quality
    #: score entirely — a DNS failure is evidence about the network, and letting
    #: it count against a model would be a lie in a document used to choose one.
    transport_failure: bool = False
    retries: int = 0
    #: One of CallStatus. "It failed" is not one thing, and only MODEL_ERROR and
    #: SCHEMA_ERROR are evidence about the model.
    status: str = "SUCCESS"
    reasoning_tokens: int = 0
    finish_reason: str = ""

    # Grading, filled in by a grader. Kept separate from the call result so a
    # recorded run can be re-graded later without paying for the tokens again.
    checks: dict[str, bool] = field(default_factory=dict)
    score: float | None = None
    notes: list[str] = field(default_factory=list)
    cost_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


#: workload → (system prompt, user-payload builder, schema key)
#: The prompts are imported from the production workloads, never copied.
def _prompts() -> dict[Workload, tuple[str, str]]:
    from services.ai.workloads import (
        answer_classifier,
        followup_generator,
        interview_designer,
        question_generator,
        report_generator,
        scoring_engine,
    )

    return {
        Workload.ANSWER_CLASSIFIER: (answer_classifier.SYSTEM, "answer_classifier"),
        Workload.FOLLOWUP_GENERATOR: (followup_generator.SYSTEM, "followup_generator"),
        Workload.INTERVIEW_DESIGNER: (interview_designer._SYSTEM, "interview_designer"),
        Workload.QUESTION_GENERATOR: (question_generator.SYSTEM, "question_generator"),
        Workload.SCORING: (scoring_engine.SYSTEM, "scoring"),
        Workload.REPORT_GENERATOR: (report_generator.SYSTEM, "report_generator"),
    }


class TaraEvaluationRunner:
    """Run one case against one model, and record everything about it."""

    def __init__(self, gateway=None) -> None:
        self.gateway = gateway or get_gateway()

    def run(
        self,
        workload: Workload,
        model: str,
        test_case_id: str,
        user_payload: str,
        *,
        schema: dict[str, Any] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> EvalResult:
        """One case, one model.

        `max_tokens` overrides the workload default. Design-time workloads are
        given headroom by the harness, because a benchmark that truncates a
        verbose model is measuring the ceiling rather than the model.
        """
        system, schema_key = _prompts()[workload]
        schema = schema or SCHEMAS[schema_key]

        result = EvalResult(
            workload=workload.value, model=model, test_case_id=test_case_id
        )

        if not self.gateway.live:
            result.status = CallStatus.AUTH_ERROR.value
            result.transport_failure = True
            result.validation_errors.append(
                "no provider configured — set OPENROUTER_API_KEY to run an evaluation"
            )
            return result

        started = time.perf_counter()
        generation = None
        try:
            generation = self.gateway.generate_structured(
                workload,
                system,
                user_payload,
                schema,
                schema_name=schema_key,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                session_id=EVAL_AUDIT_ID,
            )
            result.success = True
            result.raw_output = generation.text
            result.parsed_output = generation.data
            result.latency_ms = generation.latency_ms
            result.input_tokens = generation.usage.prompt_tokens
            result.output_tokens = generation.usage.completion_tokens
            result.retries = generation.retries
            result.reasoning_tokens = generation.usage.reasoning_tokens
            result.finish_reason = generation.finish_reason
            result.status = CallStatus.SUCCESS.value
        except OutputTruncated as exc:
            # The ceiling, not the model. Excluded from quality scoring and
            # reported separately so the fix (raise the limit) is obvious.
            result.status = CallStatus.OUTPUT_TRUNCATED.value
            result.success = False
            result.latency_ms = int((time.perf_counter() - started) * 1000)
            result.validation_errors.append(f"truncated: {str(exc)[:200]}")
        except RateLimited as exc:
            result.status = CallStatus.RATE_LIMIT_ERROR.value
            result.success = False
            result.transport_failure = True
            result.latency_ms = int((time.perf_counter() - started) * 1000)
            result.validation_errors.append(f"rate limited: {str(exc)[:200]}")
        except ProviderUnavailable as exc:
            result.status = CallStatus.AUTH_ERROR.value
            result.success = False
            result.transport_failure = True
            result.latency_ms = int((time.perf_counter() - started) * 1000)
            result.validation_errors.append(f"provider unavailable: {str(exc)[:200]}")
        except TransportError as exc:
            result.status = (
                CallStatus.TIMEOUT.value if "timeout" in str(exc).lower()
                else CallStatus.TRANSPORT_ERROR.value
            )
            result.success = False
            result.transport_failure = True
            result.latency_ms = int((time.perf_counter() - started) * 1000)
            result.validation_errors.append(f"transport: {str(exc)[:200]}")
        except AIError as exc:
            # A dead model id, a provider error, unparseable output, or a
            # response that did not match the schema. All are things a model can
            # do to you in production, so all are recorded, not raised.
            result.status = (
                CallStatus.SCHEMA_ERROR.value if "expected" in str(exc).lower()
                else CallStatus.MODEL_ERROR.value
            )
            result.success = False
            result.latency_ms = int((time.perf_counter() - started) * 1000)
            result.validation_errors.append(str(exc)[:400])

        return result

    # ------------------------------------------------------------------ #
    def validate_against(self, result: EvalResult, schema: dict[str, Any]) -> bool:
        """Re-validate a parsed output against a stricter schema than the call used."""
        if result.parsed_output is None:
            return False
        try:
            validate(result.parsed_output, schema)
            return True
        except SchemaError as exc:
            result.validation_errors.append(str(exc))
            return False
