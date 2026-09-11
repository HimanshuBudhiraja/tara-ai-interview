"""The real provider, on the evaluator's own slot. Opt-in: `pytest -m live`.

Excluded from a default run because these spend money — the repository's
existing convention for tests that need something a laptop does not have (see
the `server` marker). What they answer is the question no offline test can:
does the model this product is configured to use actually honour the contract
the evaluator sends it?

Nothing here is a quality measurement. It checks the path, not the judgement.
"""
from __future__ import annotations

import pytest

from packages.schemas import EVIDENCE_EXTRACTION, SchemaError, validate
from packages.types.evaluation import CRITERIA, DIMENSIONS
from services import config
from services.ai.gateway import Workload, get_gateway, workload_config
from tools import check_provider

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def gateway_live():
    if not config.llm_is_live():
        pytest.skip("no provider configured")
    return get_gateway()


def test_the_evaluator_slot_resolves_to_the_configured_model(gateway_live):
    """The model must come from configuration, never from business logic."""
    cfg = workload_config(Workload.SCORING)
    assert cfg.model == config.SCORING_MODEL
    assert cfg.model, "the scoring workload has no model configured"


def test_the_credential_is_present_and_used_by_the_gateway(gateway_live):
    result = check_provider.check_credentials()
    assert result["status"] == check_provider.VERIFIED, result["detail"]
    # The fingerprint is all a diagnostic is allowed to see.
    assert config.OPENROUTER_API_KEY not in result["detail"]


def test_the_configured_model_is_available_from_the_provider(gateway_live):
    cfg = workload_config(Workload.SCORING)
    result = check_provider.check_model(cfg.model)
    assert result["status"] == check_provider.VERIFIED, result["detail"]


def test_structured_output_works_on_this_model_and_does_not_fall_back(gateway_live):
    outcome = check_provider.smoke(verbose=False)
    assert outcome["request"] == "ok", outcome.get("error")
    assert outcome["schema"] == check_provider.VERIFIED, outcome.get("schema_error")
    # The gateway may degrade json_schema → json_object. It must never answer
    # from a different model than the one configured.
    assert outcome["silent_model_fallback"] is False
    assert outcome["resolved_model"] == outcome["configured_model"]
    assert outcome["structured_mode"] in ("json_schema", "json_object")


def test_the_model_keeps_the_two_vocabularies_apart(gateway_live):
    """The live failure this phase fixed, asserted against the live model."""
    outcome = check_provider.smoke(verbose=False)
    assert outcome["items"] >= 1
    for criterion in outcome["supports_criterion"]:
        assert criterion in CRITERIA, criterion
    for dimension in outcome["depth_dimension"]:
        assert dimension in DIMENSIONS, dimension
    assert outcome["vocabulary_kept_apart"] is True


def test_the_response_validates_against_the_schema_the_evaluator_sends(gateway_live):
    cfg = workload_config(Workload.SCORING)
    assert cfg.model
    outcome = check_provider.smoke(verbose=False)
    assert outcome["schema"] == check_provider.VERIFIED
    # And the schema itself is the one production uses, not a relaxed copy.
    try:
        validate({"evidence": []}, EVIDENCE_EXTRACTION)
    except SchemaError:  # pragma: no cover
        pytest.fail("the production schema rejects an empty extraction")
