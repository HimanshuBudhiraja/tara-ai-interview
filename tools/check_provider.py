"""Verify the provider path before anything expensive runs against it.

Answers, in order, the questions that a failed evaluation makes you ask
afterwards — and answers them for the price of one tiny call:

    credentials      is a key present, and is it the one the gateway uses?
    balance          does the provider say the account can pay?
    model            is the configured model real and reachable?
    structured out   does this model honour the mechanism evaluation relies on?
    fallback         did the gateway quietly answer from a different model?

Everything goes through the SAME centralized gateway the evaluator uses, so a
pass here is evidence about the path production takes rather than about a
parallel one built for the check.

No secret is printed, returned or logged. The key is shown only as its length
and a fingerprint of its last four characters' hash, which is enough to answer
"is this the same key as yesterday" and useless for anything else.

    python tools/check_provider.py            # verify only
    python tools/check_provider.py --smoke    # also make one real structured call
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.schemas import EVIDENCE_EXTRACTION, SchemaError, validate  # noqa: E402
from services import config  # noqa: E402
from services.ai.gateway import (  # noqa: E402
    AIError,
    Workload,
    get_gateway,
    workload_config,
)

VERIFIED, NOT_VERIFIED, NOT_AVAILABLE = "verified", "not verified", "not available"

#: The workload the evaluator's extraction and judging both run on. Read from
#: the enum rather than named as a string, so this check cannot drift from the
#: slot it is supposed to be checking.
SLOT = Workload.SCORING


def fingerprint(secret: str) -> str:
    """Enough to tell two keys apart. Not enough to be one."""
    if not secret:
        return "(absent)"
    digest = hashlib.sha256(secret.encode()).hexdigest()[:8]
    return f"len={len(secret)} sha256:{digest}"


def _get(path: str, timeout: float = 20.0):
    import httpx

    return httpx.get(
        f"{config.OPENROUTER_BASE_URL}{path}",
        headers={"Authorization": f"Bearer {config.OPENROUTER_API_KEY}"},
        timeout=timeout,
    )


# --------------------------------------------------------------------------- #
#  Checks
# --------------------------------------------------------------------------- #
def check_credentials() -> dict[str, Any]:
    key = config.OPENROUTER_API_KEY
    if not key:
        return {"status": NOT_VERIFIED, "detail": "no OPENROUTER_API_KEY in the environment"}
    if not get_gateway().live:
        return {
            "status": NOT_VERIFIED,
            "detail": f"a key is present but the gateway is not live "
                      f"(TARA_LLM={config.LLM_PROVIDER})",
        }
    return {
        "status": VERIFIED,
        "detail": f"present and used by the gateway · {fingerprint(key)}",
    }


def check_balance() -> dict[str, Any]:
    """OpenRouter exposes /key. Anything it does not tell us stays unclaimed."""
    try:
        response = _get("/key")
    except Exception as exc:  # noqa: BLE001
        return {"status": NOT_VERIFIED, "detail": f"could not reach the provider: {exc}"}
    if response.status_code in (401, 402, 403):
        return {
            "status": NOT_VERIFIED,
            "detail": f"provider refused the credential ({response.status_code})",
        }
    if response.status_code >= 300:
        return {"status": NOT_AVAILABLE,
                "detail": f"provider returned {response.status_code}"}

    data = (response.json() or {}).get("data") or {}
    limit, usage = data.get("limit"), data.get("usage")
    if limit is None:
        # A key with no cap. The provider is not telling us a balance, so we do
        # not report one — "unlimited" and "unknown" look identical from here.
        return {
            "status": NOT_AVAILABLE,
            "detail": "the key reports no spending limit, so a remaining balance "
                      "could not be independently verified through the provider API"
                      + (f" (usage so far ${usage})" if usage is not None else ""),
            "usage": usage,
        }
    remaining = limit - (usage or 0)
    return {
        "status": VERIFIED if remaining > 0 else NOT_VERIFIED,
        "detail": f"limit ${limit}, used ${usage}, remaining ${remaining:.4f}",
        "remaining": remaining,
    }


def check_model(model: str) -> dict[str, Any]:
    try:
        response = _get("/models", timeout=30.0)
    except Exception as exc:  # noqa: BLE001
        return {"status": NOT_VERIFIED, "detail": f"could not reach the provider: {exc}"}
    if response.status_code >= 300:
        return {"status": NOT_AVAILABLE, "detail": f"provider returned {response.status_code}"}
    ids = {row.get("id") for row in (response.json() or {}).get("data") or []}
    if model in ids:
        return {"status": VERIFIED, "detail": f"{model} is listed by the provider"}
    return {
        "status": NOT_VERIFIED,
        "detail": f"{model} is NOT in the provider's model list ({len(ids)} models listed)",
    }


def smoke(verbose: bool = True) -> dict[str, Any]:
    """One real structured call on the evaluator's slot, through the gateway.

    Deliberately shaped like evidence extraction — same workload, same schema,
    same `generate_structured` entry point — because the thing being tested is
    that path, not the provider in general. A payload that exercised a different
    mechanism would prove something we do not need to know.
    """
    cfg = workload_config(SLOT)
    system = (
        "You extract evidence from an interview transcript. Return STRICT JSON "
        '{"evidence": [...]}. Use EXACTLY the field values you are given below.\n\n'
        "EVIDENCE DIMENSIONS (lower_snake_case, for depth_dimension): "
        "conceptual_understanding, practical_application, reasoning, trade_offs, "
        "edge_cases, production_judgment\n"
        "SCORING CRITERIA (Title-Case, for supports_criterion): Accuracy, Depth, "
        "Clarity, Problem-Solving, Communication\n\n"
        "These are two different lists. A dimension name is never a criterion."
    )
    user = (
        'Turn [turn_id: t1] the candidate said: "We key the capture by the provider '
        'reference so a retry finds the existing row." Extract one evidence item: '
        'turn_id, candidate_quote (verbatim), depth_dimension, evidence_type, '
        'evidence_strength, supports_criterion.'
    )

    started = time.perf_counter()
    outcome: dict[str, Any] = {
        "workload": SLOT.value,
        "configured_model": cfg.model,
        "provider": "openrouter",
    }
    try:
        result = get_gateway().generate_structured(
            SLOT, system, user, EVIDENCE_EXTRACTION,
            schema_name="evidence_extraction",
            # A ceiling small enough that a mistake is cheap and large enough
            # that a correct answer is not truncated into looking like one.
            max_tokens=400, session_id="_provider_check",
        )
    except AIError as exc:
        outcome.update({
            "request": "failed", "schema": NOT_VERIFIED,
            "error": str(exc)[:300],
            "latency_ms": int((time.perf_counter() - started) * 1000),
        })
        return outcome

    outcome.update({
        "request": "ok",
        "resolved_model": result.model,
        "structured_mode": result.structured_mode,
        "latency_ms": result.latency_ms,
        "prompt_tokens": result.usage.prompt_tokens,
        "completion_tokens": result.usage.completion_tokens,
        "retries": result.retries,
    })
    try:
        validate(result.data or {}, EVIDENCE_EXTRACTION)
        outcome["schema"] = VERIFIED
    except SchemaError as exc:
        outcome["schema"] = NOT_VERIFIED
        outcome["schema_error"] = str(exc)[:200]

    items = (result.data or {}).get("evidence") or []
    outcome["items"] = len(items)
    criteria = [i.get("supports_criterion") for i in items]
    dimensions = [i.get("depth_dimension") for i in items]
    outcome["supports_criterion"] = criteria
    outcome["depth_dimension"] = dimensions
    outcome["vocabulary_kept_apart"] = all(
        c in ("Accuracy", "Depth", "Clarity", "Problem-Solving", "Communication")
        for c in criteria
    ) and all(d in (
        "conceptual_understanding", "practical_application", "reasoning",
        "trade_offs", "edge_cases", "production_judgment") for d in dimensions)
    # The gateway does not switch models on its own — the only substitution it
    # can make is json_schema → json_object. Checked rather than assumed.
    outcome["silent_model_fallback"] = (
        result.model != cfg.model if result.model else "unknown"
    )
    if verbose:
        print(json.dumps(outcome, indent=2))
    return outcome


# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke", action="store_true",
                        help="also make one real structured call (costs tokens)")
    args = parser.parse_args()

    cfg = workload_config(SLOT)
    print(f"provider base url  : {config.OPENROUTER_BASE_URL}")
    print(f"evaluator slot     : {SLOT.value}")
    print(f"configured model   : {cfg.model}")
    print(f"  (from SCORING_MODEL / TARA_MODEL_DEEP / TARA_MODEL_DEFAULT)")
    print()

    credentials = check_credentials()
    print(f"credentials        : {credentials['status']} — {credentials['detail']}")
    if credentials["status"] != VERIFIED:
        print("\nStopping: nothing further can be verified without a usable credential.")
        return 1

    balance = check_balance()
    print(f"balance            : {balance['status']} — {balance['detail']}")

    model = check_model(cfg.model)
    print(f"model availability : {model['status']} — {model['detail']}")

    if not args.smoke:
        print("\nRe-run with --smoke to make one real structured call on this slot.")
        return 0
    if model["status"] == NOT_VERIFIED:
        print("\nStopping before the smoke test: the configured model is not available.")
        return 1

    print("\n--- smoke test: one structured call through the gateway ---")
    result = smoke()
    ok = result.get("request") == "ok" and result.get("schema") == VERIFIED
    print(f"\nstructured output  : {result.get('schema')}")
    print(f"silent fallback    : {result.get('silent_model_fallback')}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
