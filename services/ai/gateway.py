"""The AI Model Gateway — every model call in the product goes through here.

One place, for four reasons that all cost real money or real trust when they
are scattered instead:

  * **Credentials.** The key is read once, from `services.config`, and never
    leaves this module. Nothing else in the codebase can accidentally log it or
    hand it to a browser.
  * **Model choice.** A workload asks for `Workload.ANSWER_CLASSIFIER`, not for
    a model name. Benchmarking a different model is an env var, not a diff.
  * **Structure.** Every call that expects JSON declares a schema, and the
    result is validated on the way back — because provider-side enforcement is
    a per-model capability, not a guarantee.
  * **Telemetry.** request_id, workload, model, latency, tokens, success or
    failure, on every call, in the audit log. Without it, "the interview felt
    slow" is unanswerable.

The gateway is model-agnostic on purpose: it degrades from constrained decoding
to plain JSON mode to best-effort extraction rather than assuming any of them.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import httpx

from packages.schemas import SchemaError, validate
from services import config
from services.data import audit


class CallStatus(str, Enum):
    """Why a call ended the way it did.

    "It failed" is not one thing, and treating it as one produced two wrong
    reports during development: a DNS drop that condemned every model, and an
    exhausted API key that condemned four more. Only MODEL_ERROR and
    SCHEMA_ERROR are evidence about a model. OUTPUT_TRUNCATED is evidence about
    the token ceiling. The rest are evidence about the infrastructure.
    """

    SUCCESS = "SUCCESS"
    MODEL_ERROR = "MODEL_ERROR"            # reached the model; it answered unusably
    SCHEMA_ERROR = "SCHEMA_ERROR"          # answered, but not in the declared shape
    OUTPUT_TRUNCATED = "OUTPUT_TRUNCATED"  # ran out of tokens mid-answer
    TRANSPORT_ERROR = "TRANSPORT_ERROR"    # never reached the provider
    AUTH_ERROR = "AUTH_ERROR"              # 401/402/403 — key, billing, permission
    RATE_LIMIT_ERROR = "RATE_LIMIT_ERROR"  # 429, after retries
    TIMEOUT = "TIMEOUT"                    # the provider did not answer in time

    @property
    def blames_the_model(self) -> bool:
        return self in (CallStatus.MODEL_ERROR, CallStatus.SCHEMA_ERROR)

    @property
    def is_infrastructure(self) -> bool:
        return self in (
            CallStatus.TRANSPORT_ERROR, CallStatus.AUTH_ERROR,
            CallStatus.RATE_LIMIT_ERROR, CallStatus.TIMEOUT,
        )


class Workload(str, Enum):
    """The six AI boundaries in the product. Each is independently configured."""

    INTERVIEW_DESIGNER = "interview_designer"
    QUESTION_GENERATOR = "question_generator"
    ANSWER_CLASSIFIER = "answer_classifier"
    FOLLOWUP_GENERATOR = "followup_generator"
    SCORING = "scoring"
    REPORT_GENERATOR = "report_generator"


class AIError(RuntimeError):
    """A model call that did not produce something usable."""


class OutputTruncated(AIError):
    """The model ran out of tokens mid-answer.

    Not a quality failure. A model cut off at the ceiling has not failed the
    task — it was not given room to finish it. Raising it distinctly is what
    stops a benchmark measuring token starvation and calling it reasoning.
    """


class RateLimited(AIError):
    """429 after every retry. Not a model failure — the account is throttled."""


class ProviderUnavailable(AIError):
    """The provider refused the request for a reason that is not about the model.

    An exhausted key limit, an expired key, or a suspended account answers 401,
    402 or 403 to EVERY model equally. During development this drained a key
    mid-run and the harness recorded 91 "model failures" — a table that would
    have condemned four models for an account balance.
    """


class TransportError(AIError):
    """The request never reached the provider — DNS, connection, timeout.

    Separated from `AIError` because the two mean opposite things about a model.
    A malformed response is evidence about the model; a DNS failure is evidence
    about the network, and counting it against the model would be a lie in a
    document used to choose one.
    """


@dataclass(frozen=True)
class WorkloadConfig:
    workload: Workload
    model: str
    temperature: float
    max_tokens: int
    timeout_sec: float


# Defaults per workload. Latency budgets differ by an order of magnitude:
# classifying a turn happens while a candidate is sitting in silence waiting for
# Tara to speak, and designing an interview happens while a recruiter is looking
# at a spinner they expected.
_DEFAULTS: dict[Workload, tuple[float, int, float]] = {
    #                       temperature, max_tokens, timeout
    Workload.INTERVIEW_DESIGNER: (0.3, 4000, 90.0),
    Workload.QUESTION_GENERATOR: (0.4, 4000, 90.0),
    Workload.ANSWER_CLASSIFIER: (0.2, 400, config.LLM_TIMEOUT_SEC),
    Workload.FOLLOWUP_GENERATOR: (0.5, 160, config.LLM_TIMEOUT_SEC),
    Workload.SCORING: (0.2, 3000, 90.0),
    Workload.REPORT_GENERATOR: (0.4, 3000, 90.0),
}

_MODELS: dict[Workload, str] = {
    Workload.INTERVIEW_DESIGNER: config.INTERVIEW_DESIGNER_MODEL,
    Workload.QUESTION_GENERATOR: config.QUESTION_GENERATOR_MODEL,
    Workload.ANSWER_CLASSIFIER: config.ANSWER_CLASSIFIER_MODEL,
    Workload.FOLLOWUP_GENERATOR: config.FOLLOWUP_GENERATOR_MODEL,
    Workload.SCORING: config.SCORING_MODEL,
    Workload.REPORT_GENERATOR: config.REPORT_GENERATOR_MODEL,
}


def workload_config(workload: Workload) -> WorkloadConfig:
    temperature, max_tokens, timeout = _DEFAULTS[workload]
    return WorkloadConfig(
        workload=workload,
        model=_MODELS[workload],
        temperature=temperature,
        max_tokens=max_tokens,
        timeout_sec=timeout,
    )


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    #: Reasoning models spend tokens thinking before they write. Recorded
    #: separately because comparing a reasoning model to a non-reasoning one on
    #: visible output tokens alone understates what the call actually cost — and
    #: because a workload ceiling that ignores them starves the model rather
    #: than measuring it.
    reasoning_tokens: int = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class Generation:
    """One call's result plus everything needed to explain it afterwards."""

    request_id: str
    workload: str
    model: str
    text: str = ""
    data: dict[str, Any] | None = None
    latency_ms: int = 0
    usage: Usage = field(default_factory=Usage)
    success: bool = True
    error: str = ""
    structured_mode: str = ""       # json_schema | json_object | text | mock
    retries: int = 0
    status: str = CallStatus.SUCCESS.value
    finish_reason: str = ""
    #: True when the request never reached the provider. Kept separate so an
    #: evaluation can exclude infrastructure failures from a model's score.
    transport_failure: bool = False

    def meta(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "workload": self.workload,
            "model": self.model,
            "latency_ms": self.latency_ms,
            "prompt_tokens": self.usage.prompt_tokens,
            "completion_tokens": self.usage.completion_tokens,
            "total_tokens": self.usage.total,
            "success": self.success,
            "status": self.status,
            "structured_mode": self.structured_mode,
            **({"reasoning_tokens": self.usage.reasoning_tokens}
               if self.usage.reasoning_tokens else {}),
            **({"finish_reason": self.finish_reason} if self.finish_reason else {}),
            **({"retries": self.retries} if self.retries else {}),
            **({"transport_failure": True} if self.transport_failure else {}),
            **({"error": self.error} if self.error else {}),
        }


def _extract_json(text: str) -> dict[str, Any]:
    """Last-resort parse for a model that wrapped its JSON in prose or fences."""
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.S)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    raise AIError(f"model did not return JSON: {text[:200]!r}")


class AIModelGateway:
    """The only thing in the product that talks to a model provider."""

    def __init__(self, session_id: str = "_system") -> None:
        # session_id is where telemetry lands, so a slow interview can be
        # explained from the same trail that explains its decisions.
        self.session_id = session_id
        self._client: httpx.Client | None = None
        self.live = config.llm_is_live()

    # ------------------------------------------------------------------ #
    @property
    def name(self) -> str:
        return "openrouter" if self.live else "mock"

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=config.LLM_TIMEOUT_SEC)
        return self._client

    # ------------------------------------------------------------------ #
    #  Public surface
    # ------------------------------------------------------------------ #
    def generate(
        self,
        workload: Workload,
        system: str,
        user: str,
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout_sec: float | None = None,
        session_id: str | None = None,
    ) -> Generation:
        """Free-text generation. Returns a `Generation`; never raises for a
        provider error — inspect `.success`, because a failed model call must
        degrade the feature, not the request."""
        return self._call(
            workload, system, user, None,
            model=model, temperature=temperature, max_tokens=max_tokens,
            timeout_sec=timeout_sec, session_id=session_id,
        )

    def generate_structured(
        self,
        workload: Workload,
        system: str,
        user: str,
        schema: dict[str, Any],
        *,
        schema_name: str = "response",
        accept_schema: dict[str, Any] | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout_sec: float | None = None,
        session_id: str | None = None,
    ) -> Generation:
        """JSON generation, constrained by `schema` where the model supports it
        and validated against it in every case.

        Raises `AIError` when the response cannot be parsed or fails the schema:
        a caller asking for structure has no use for a shape it can't read.

        `accept_schema` lets a caller ASK for more than it will REFUSE over.
        `schema` is always what the provider is sent, so the model still learns
        the full vocabulary; when `accept_schema` is given, that is what the
        reply is checked against.

        It exists for one shape of problem: a schema whose enums sit inside an
        array. Validation there is all-or-nothing, so a single item using a
        value from a neighbouring list destroys every good item beside it. A
        caller that validates its items individually — and can say which one was
        wrong, and why — should accept the envelope here and do that work
        itself. A caller that passes nothing gets the strict behaviour, which is
        still the right default for everything that is not a list.
        """
        result = self._call(
            workload, system, user, (schema, schema_name),
            model=model, temperature=temperature, max_tokens=max_tokens,
            timeout_sec=timeout_sec, session_id=session_id,
        )
        if not result.success:
            # Preserve WHY it failed. A caller that cannot tell an exhausted key
            # from a bad generation will record one as the other — which is
            # exactly how 91 refused requests once became 91 "model failures".
            if result.status == CallStatus.AUTH_ERROR.value:
                raise ProviderUnavailable(result.error or "provider unavailable")
            if result.status == CallStatus.RATE_LIMIT_ERROR.value:
                raise RateLimited(result.error or "rate limited")
            if result.transport_failure:
                raise TransportError(result.error or "transport failure")
            if result.status == CallStatus.OUTPUT_TRUNCATED.value:
                raise OutputTruncated(result.error or "output truncated")
            raise AIError(result.error or "model call failed")
        try:
            data = _extract_json(result.text)
            validate(data, accept_schema or schema)
        except (AIError, SchemaError) as exc:
            result.success = False
            result.status = (
                CallStatus.SCHEMA_ERROR.value if isinstance(exc, SchemaError)
                else CallStatus.MODEL_ERROR.value
            )
            result.error = str(exc)[:300]
            audit.ai_call(session_id or self.session_id, **result.meta())
            raise AIError(str(exc)) from exc
        result.data = data
        return result

    # ------------------------------------------------------------------ #
    #  Internals
    # ------------------------------------------------------------------ #
    def _call(
        self,
        workload: Workload,
        system: str,
        user: str,
        structured: tuple[dict[str, Any], str] | None,
        *,
        model: str | None,
        temperature: float | None,
        max_tokens: int | None,
        timeout_sec: float | None,
        session_id: str | None,
    ) -> Generation:
        cfg = workload_config(workload)
        chosen_model = model or cfg.model
        request_id = "req_" + uuid.uuid4().hex[:12]
        started = time.perf_counter()
        result = Generation(
            request_id=request_id, workload=workload.value, model=chosen_model
        )

        if not self.live:
            result.success = False
            result.status = CallStatus.AUTH_ERROR.value
            result.structured_mode = "mock"
            result.error = "no provider configured"
            result.latency_ms = 0
            audit.ai_call(session_id or self.session_id, **result.meta())
            return result

        body: dict[str, Any] = {
            "model": chosen_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": cfg.temperature if temperature is None else temperature,
            "max_tokens": cfg.max_tokens if max_tokens is None else max_tokens,
        }
        if structured is not None:
            schema, schema_name = structured
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": False, "schema": schema},
            }
            result.structured_mode = "json_schema"

        headers = {
            "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        }
        if config.OPENROUTER_APP_URL:
            headers["HTTP-Referer"] = config.OPENROUTER_APP_URL
        if config.OPENROUTER_APP_NAME:
            headers["X-Title"] = config.OPENROUTER_APP_NAME

        timeout = cfg.timeout_sec if timeout_sec is None else timeout_sec

        try:
            response = self._post_with_retry(body, headers, timeout, result)
            # Not every model on OpenRouter accepts a json_schema response
            # format, and the ones that don't answer 4xx rather than ignoring
            # it. Fall back to plain JSON mode and let local validation do the
            # work — the gateway must stay model-agnostic (§12).
            if structured is not None and response.status_code in (400, 404, 422):
                body["response_format"] = {"type": "json_object"}
                result.structured_mode = "json_object"
                response = self._post_with_retry(body, headers, timeout, result)

            if response.status_code in (401, 402, 403):
                raise ProviderUnavailable(f"{response.status_code}: {response.text[:200]}")
            if response.status_code >= 300:
                raise AIError(f"{response.status_code}: {response.text[:200]}")

            payload = response.json()
            choice = (payload.get("choices") or [{}])[0]
            result.text = (choice.get("message") or {}).get("content") or ""
            result.finish_reason = (
                choice.get("finish_reason") or choice.get("native_finish_reason") or ""
            )
            usage = payload.get("usage") or {}
            details = usage.get("completion_tokens_details") or {}
            result.usage = Usage(
                prompt_tokens=int(usage.get("prompt_tokens") or 0),
                completion_tokens=int(usage.get("completion_tokens") or 0),
                reasoning_tokens=int(details.get("reasoning_tokens") or 0),
            )
            if not structured:
                result.structured_mode = "text"

            # The provider says it stopped because it ran out of room. That is a
            # ceiling problem, not a quality problem: a model cut off mid-JSON
            # has not failed the task, it was not given space to finish it.
            if result.finish_reason in ("length", "max_tokens", "MAX_TOKENS"):
                result.status = CallStatus.OUTPUT_TRUNCATED.value
                result.success = False
                result.error = (
                    f"output truncated at {result.usage.completion_tokens} completion tokens"
                    + (f" ({result.usage.reasoning_tokens} of them reasoning)"
                       if result.usage.reasoning_tokens else "")
                )
        except ProviderUnavailable as exc:
            result.success = False
            result.transport_failure = True
            result.status = CallStatus.AUTH_ERROR.value
            result.error = str(exc)[:300]
        except RateLimited as exc:
            result.success = False
            result.transport_failure = True
            result.status = CallStatus.RATE_LIMIT_ERROR.value
            result.error = str(exc)[:300]
        except TransportError as exc:
            result.success = False
            result.transport_failure = True
            result.status = (
                CallStatus.TIMEOUT.value if "timeout" in str(exc).lower()
                else CallStatus.TRANSPORT_ERROR.value
            )
            result.error = str(exc)[:300]
        except Exception as exc:  # noqa: BLE001 — a provider must never break a turn
            result.success = False
            result.status = CallStatus.MODEL_ERROR.value
            result.error = str(exc)[:300]

        result.latency_ms = int((time.perf_counter() - started) * 1000)
        audit.ai_call(session_id or self.session_id, **result.meta())
        return result

    def _post(self, body: dict[str, Any], headers: dict[str, str], timeout: float):
        return self._http().post(
            f"{config.OPENROUTER_BASE_URL}/chat/completions",
            headers=headers,
            json=body,
            timeout=timeout,
        )

    #: Transient network failures, retried with backoff. A DNS blip or a reset
    #: connection is not a verdict on the model — during development one dropped
    #: 206 of a 232-call evaluation and would have reported every model as broken.
    TRANSPORT_ERRORS = (
        httpx.ConnectError,
        httpx.ConnectTimeout,
        httpx.ReadTimeout,
        httpx.WriteTimeout,
        httpx.PoolTimeout,
        httpx.RemoteProtocolError,
        httpx.ReadError,
        httpx.WriteError,
    )
    RETRIES = 3

    def _post_with_retry(
        self, body: dict[str, Any], headers: dict[str, str], timeout: float, result: "Generation"
    ):
        """POST, retrying transport failures and 429/5xx with backoff.

        Deliberately does NOT retry a 4xx other than 429: a bad request will be
        just as bad the second time, and retrying it wastes a candidate's
        patience during a live turn.
        """
        last: Exception | None = None
        for attempt in range(self.RETRIES):
            if attempt:
                time.sleep(min(2 ** attempt * 0.5, 4.0))
                result.retries = attempt
            try:
                response = self._post(body, headers, timeout)
            except self.TRANSPORT_ERRORS as exc:
                last = exc
                continue
            if response.status_code == 429:
                last = RateLimited(f"429: {response.text[:120]}")
                continue
            if response.status_code >= 500:
                last = AIError(f"{response.status_code}: {response.text[:120]}")
                continue
            return response
        if isinstance(last, RateLimited):
            raise last
        raise TransportError(str(last) if last else "request failed")


_GATEWAY: AIModelGateway | None = None


def get_gateway() -> AIModelGateway:
    global _GATEWAY
    if _GATEWAY is None:
        _GATEWAY = AIModelGateway()
    return _GATEWAY
