"""The AI Model Gateway: configuration, structure, and telemetry.

None of these tests call a provider. What they check is everything that goes
wrong *around* a model call — a workload pointed at the wrong model, a response
that doesn't match its schema, a failure that takes the request down with it —
because those are the failures that actually happen in production.
"""
from __future__ import annotations

import pytest

from packages.schemas import (
    ANSWER_CLASSIFICATION,
    INTERVIEW_DESIGN,
    SchemaError,
    validate,
)
from services.ai.gateway import AIError, AIModelGateway, Workload, workload_config


# --------------------------------------------------------------------------- #
#  Configuration
# --------------------------------------------------------------------------- #
def test_every_workload_is_independently_configured():
    """Six boundaries, six knobs. One global model name would force the slowest
    workload's choice onto the fastest one."""
    for workload in Workload:
        cfg = workload_config(workload)
        assert cfg.model, f"{workload.value} has no model"
        assert cfg.max_tokens > 0
        assert cfg.timeout_sec > 0
        assert 0 <= cfg.temperature <= 1


def test_runtime_workloads_have_tighter_budgets_than_design_time_ones():
    """Classifying a turn happens while a candidate sits in silence. Designing
    an interview happens while a recruiter looks at a spinner they expected."""
    runtime = workload_config(Workload.ANSWER_CLASSIFIER)
    design = workload_config(Workload.INTERVIEW_DESIGNER)
    assert runtime.timeout_sec <= design.timeout_sec
    assert runtime.max_tokens < design.max_tokens


def test_model_choice_comes_from_the_environment(monkeypatch):
    from services import config
    from services.ai import gateway

    monkeypatch.setitem(gateway._MODELS, Workload.SCORING, "anthropic/claude-sonnet-5")
    assert workload_config(Workload.SCORING).model == "anthropic/claude-sonnet-5"
    assert config.SCORING_MODEL, "SCORING_MODEL must resolve to something"


def test_the_gateway_never_exposes_the_key():
    """The one property that must hold no matter what else breaks."""
    from services import config

    gw = AIModelGateway()
    surface = " ".join(
        str(getattr(gw, name)) for name in dir(gw) if not name.startswith("_")
    )
    if config.OPENROUTER_API_KEY:
        assert config.OPENROUTER_API_KEY not in surface


# --------------------------------------------------------------------------- #
#  Structured responses
# --------------------------------------------------------------------------- #
def test_a_well_formed_classification_validates():
    validate(
        {
            "intent": "answer",
            "depth": "partial",
            "covered": ["names a specific trade-off"],
            "missing": [],
            "affect": "neutral",
            "quote": "I'd check what changed first",
        },
        ANSWER_CLASSIFICATION,
    )


@pytest.mark.parametrize(
    "bad,because",
    [
        ({"depth": "partial", "covered": [], "missing": [], "affect": "neutral"},
         "missing required key 'intent'"),
        ({"intent": "waffle", "depth": "partial", "covered": [], "missing": [],
          "affect": "neutral"}, "an intent the runtime has no branch for"),
        ({"intent": "answer", "depth": "partial", "covered": "empathy", "missing": [],
          "affect": "neutral"}, "covered must be a list, not a string"),
        ({"intent": "answer", "depth": "partial", "covered": [], "missing": [],
          "affect": "confident"}, "affect outside the closed set"),
    ],
)
def test_malformed_classifications_are_rejected(bad, because):
    with pytest.raises(SchemaError):
        validate(bad, ANSWER_CLASSIFICATION)
    assert because  # documents why each case matters


def test_schema_errors_name_the_path():
    """"Invalid response" costs someone twenty minutes with a debugger."""
    with pytest.raises(SchemaError) as exc:
        validate(
            {"outcomes": ["x"], "skills": [{"name": "Empathy", "priority": "critical"}],
             "tasks": []},
            INTERVIEW_DESIGN,
        )
    assert "skills[0].priority" in str(exc.value)


def test_integer_bounds_are_enforced():
    with pytest.raises(SchemaError) as exc:
        validate(
            {"outcomes": ["x"],
             "skills": [{"name": "Empathy", "priority": "high", "proficiency_target": 9}],
             "tasks": []},
            INTERVIEW_DESIGN,
        )
    assert "proficiency_target" in str(exc.value)


def test_booleans_are_not_accepted_where_a_number_is_required():
    """bool is a subclass of int in Python. A schema asking for a level must not
    silently accept True."""
    with pytest.raises(SchemaError):
        validate({"level": True}, {"type": "object", "properties": {"level": {"type": "number"}}})


# --------------------------------------------------------------------------- #
#  Failure behaviour
# --------------------------------------------------------------------------- #
def test_with_no_provider_generate_fails_softly_and_structured_raises(monkeypatch):
    """A missing key must degrade the feature, not the request.

    `generate` reports failure on the result so a caller can fall back;
    `generate_structured` raises, because a caller that asked for a shape has no
    use for one that isn't there.
    """
    gw = AIModelGateway()
    monkeypatch.setattr(gw, "live", False)

    result = gw.generate(Workload.FOLLOWUP_GENERATOR, "system", "user")
    assert result.success is False
    assert result.structured_mode == "mock"
    assert result.request_id

    with pytest.raises(AIError):
        gw.generate_structured(
            Workload.ANSWER_CLASSIFIER, "system", "user", ANSWER_CLASSIFICATION
        )


def test_telemetry_carries_everything_needed_to_explain_a_slow_turn(monkeypatch):
    gw = AIModelGateway()
    monkeypatch.setattr(gw, "live", False)
    meta = gw.generate(Workload.ANSWER_CLASSIFIER, "s", "u").meta()

    for field in ("request_id", "workload", "model", "latency_ms", "total_tokens", "success"):
        assert field in meta, f"telemetry is missing {field}"
    assert meta["workload"] == "answer_classifier"
