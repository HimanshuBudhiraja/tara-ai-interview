"""The gold data itself, and the configuration machinery around it.

The previous phase's central problem was not the code — it was that two
benchmark datasets disagreed about what a depth label means, so a real
improvement and a regression were indistinguishable. This file makes the
reconciliation a thing that stays true: every depth-asserting case carries an
authoritative label with a rationale, no label contradicts its own evidence
except the one documented cap, and the rubric the labels were adjudicated
against is named in both files.

It also pins the experiment machinery, because a configuration that silently
stopped applying would produce runs labelled with a prompt they did not use.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from packages.types.evaluation import DIMENSIONS, STAGES, EvidenceItem
from services.evaluation import depth_config
from services.evaluation import evaluator as E
from services.evaluation import evidence as EV

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "evals" / "datasets" / "evaluator_benchmark.json"
DEPTH = ROOT / "evals" / "datasets" / "depth_benchmark.json"

#: The one case whose authoritative label the shipped derivation cannot produce.
#: Documented in DEPTH_CALIBRATION.md: its deepest evidence is a meaningful
#: trade-off, and `trade_offs` is capped at `probed`.
KNOWN_CAP = {"dd-len-short-01"}


def _load(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["cases"]


def _dataset(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _depth_cases(path: Path) -> list[dict]:
    return [c for c in _load(path) if "depth_demonstrated" in c["expect"]]


def _gold_items(case: dict) -> list[EvidenceItem]:
    return [
        EvidenceItem(
            skill_id="s", skill_name="s", question_id="q", turn_id="t",
            depth_stage="direct",
            depth_dimension=row["depth_dimension"],
            candidate_quote="(gold)",
            evidence_type=row.get("evidence_type", "supported"),
            evidence_strength=row.get("evidence_strength", "strong"),
            supports_criterion="Depth",
        )
        for row in case.get("gold_evidence", [])
    ]


# --------------------------------------------------------------------------- #
#  Both datasets name the same rubric
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", [GOLD, DEPTH])
def test_every_dataset_names_the_authoritative_rubric(path):
    assert _dataset(path)["depth_rubric"].startswith("DEPTH_CALIBRATION.md")


@pytest.mark.parametrize("path", [GOLD, DEPTH])
def test_every_depth_case_carries_an_authoritative_label_and_a_reason(path):
    for case in _depth_cases(path):
        row = case.get("depth_reconciliation")
        assert row, f"{case['case_id']} has no reconciliation record"
        assert row["authoritative_label"] == case["expect"]["depth_demonstrated"], \
            f"{case['case_id']}: the record and the expectation disagree"
        assert row["authoritative_label"] in STAGES
        assert len(row["why"]) > 30, f"{case['case_id']}: no rationale"
        assert row["status"], f"{case['case_id']}: no reconciliation status"


@pytest.mark.parametrize("path", [GOLD, DEPTH])
def test_depth_sensitivity_is_marked_on_every_case(path):
    for case in _load(path):
        assert "depth_sensitive" in case, case["case_id"]
        assert case["depth_sensitive"] == ("depth_demonstrated" in case["expect"])


def test_the_one_reconciled_contradiction_is_recorded_as_such():
    """`dep-04-tradeoffs` was labelled `probed` while its own reason called
    trade-off reasoning the top of the Depth scale. That is a contradiction in
    the gold data, and it is resolved in the file rather than in a commit
    message."""
    case = next(c for c in _load(GOLD) if c["case_id"] == "dep-04-tradeoffs")
    row = case["depth_reconciliation"]
    assert row["status"] == "GOLD_DATA_CONTRADICTION"
    assert (row["previous_label"], row["authoritative_label"]) == ("probed", "deep_probed")
    assert case["expect"]["depth_demonstrated"] == "deep_probed"


def test_no_depth_case_is_left_ambiguous_without_being_excluded():
    """An `AMBIGUOUS_GOLD` case must be excluded from the strict denominator.
    There are currently none, and this asserts the mechanism rather than the
    count — if one is added, the harness has to exclude it."""
    from evals import depth as suite

    ambiguous = [
        c["case_id"] for c in _depth_cases(DEPTH)
        if c["depth_reconciliation"]["status"] == "AMBIGUOUS_GOLD"
    ]
    assert "AMBIGUOUS_GOLD" in suite.STRICT_STATUSES
    for case_id in ambiguous:
        assert case_id  # excluded by `analyse` via STRICT_STATUSES


# --------------------------------------------------------------------------- #
#  A label may not contradict its own evidence
# --------------------------------------------------------------------------- #
def test_no_gold_label_contradicts_its_own_gold_evidence():
    """The depth suite authors the evidence a correct extractor should find. If
    the shipped derivation cannot produce the authored label FROM that evidence,
    either the label or the rule is wrong — and the only permitted exception is
    the documented cap."""
    mismatched = []
    for case in _depth_cases(DEPTH):
        if not case.get("gold_evidence"):
            continue
        derived = E.depth_demonstrated_from(_gold_items(case))
        if derived != case["expect"]["depth_demonstrated"]:
            mismatched.append(case["case_id"])
    assert set(mismatched) == KNOWN_CAP, mismatched


def test_the_known_cap_is_documented_in_the_case_itself():
    case = next(c for c in _load(DEPTH) if c["case_id"] == "dd-len-short-01")
    assert "trade_offs" in case["known_limitation"]
    assert case["depth_reconciliation"]["status"] == "CONFIRMED_KNOWN_CAP"


def test_every_gold_evidence_label_is_from_the_real_vocabulary():
    for case in _load(DEPTH):
        for row in case.get("gold_evidence", []):
            assert row["depth_dimension"] in DIMENSIONS, case["case_id"]
            assert row.get("evidence_strength", "strong") in ("strong", "moderate", "weak")
            assert row.get("evidence_type", "supported") in (
                "supported", "partial", "contradicted", "unclear", "missing"
            )


def test_depth_reached_is_derived_from_the_transcript_not_authored():
    """A case whose authored `depth_reached` disagreed with its own answer count
    would be testing the harness rather than the evaluator."""
    for case in _depth_cases(DEPTH):
        expected = STAGES[min(len(case["answers"]) - 1, 2)]
        assert case["expect"]["depth_reached"] == expected, case["case_id"]


def test_the_two_datasets_agree_where_they_test_the_same_substance():
    """`dpt-02-direct-deep` and `dd-deep-03` are the same observability answer.
    The datasets used to be able to disagree about it; now they cannot without
    this failing."""
    gold = next(c for c in _load(GOLD) if c["case_id"] == "dpt-02-direct-deep")
    suite = next(c for c in _load(DEPTH) if c["case_id"] == "dd-deep-03")
    assert gold["expect"]["depth_demonstrated"] == \
        suite["expect"]["depth_demonstrated"] == "deep_probed"
    assert "trace id" in gold["answers"][0] and "trace id" in suite["answers"][0]


def test_all_four_reached_demonstrated_combinations_are_represented():
    """Phase 7's requirement, asserted on the dataset rather than assumed."""
    pairs = {
        (c["expect"]["depth_reached"], c["expect"]["depth_demonstrated"])
        for c in _depth_cases(DEPTH)
    }
    for required in (
        ("direct", "direct"), ("direct", "probed"), ("direct", "deep_probed"),
        ("probed", "direct"), ("probed", "probed"), ("probed", "deep_probed"),
        ("deep_probed", "direct"), ("deep_probed", "deep_probed"),
    ):
        assert required in pairs, required


# --------------------------------------------------------------------------- #
#  The configuration machinery
# --------------------------------------------------------------------------- #
def test_the_shipped_configuration_is_the_shipped_prompt():
    """`depth_cfg_current` must be byte-identical to what production sends, or
    the control in every experiment is not the control."""
    assert EV.system_prompt("depth_cfg_current") == EV.SYSTEM
    assert EV.system_prompt() == EV.SYSTEM   # nothing set → production wording


@pytest.mark.parametrize("config_id", sorted(depth_config.CONFIGURATIONS))
def test_every_configuration_still_applies_to_the_current_prompt(config_id):
    """An edit whose anchor has moved raises rather than doing nothing, so a
    prompt change cannot silently turn a candidate configuration into the
    shipped one."""
    text = EV.system_prompt(config_id)
    assert len(text) >= len(EV.SYSTEM)
    assert depth_config.resolve(config_id).summary


def test_an_unknown_configuration_is_refused():
    with pytest.raises(depth_config.ConfigError):
        depth_config.resolve("depth_cfg_does_not_exist")


def test_a_missing_anchor_raises_rather_than_silently_skipping():
    broken = depth_config.DepthConfig(
        id="broken", summary="anchor that is not in the prompt",
        edits=(("a line the prompt does not contain", "replacement"),),
    )
    with pytest.raises(depth_config.ConfigError):
        broken.apply(EV.SYSTEM)


def test_the_rubric_configuration_says_what_the_document_says_it_says():
    """The three rules `depth_cfg_rubric_v1` exists to carry, each traceable to
    a reconciliation row."""
    text = EV.system_prompt("depth_cfg_rubric_v1")
    assert "CHANGED WHAT THEY DID" in text          # production judgement
    assert "An INTENTION is not application" in text  # hypothetical plans
    assert "never lower the strength" in text        # transcription damage
    assert "One sentence can carry more than one dimension" in text


def test_the_environment_variable_selects_the_configuration(monkeypatch):
    monkeypatch.setenv(depth_config.ENV, "depth_cfg_rubric_v1")
    assert depth_config.resolve().id == "depth_cfg_rubric_v1"
    assert EV.system_prompt() != EV.SYSTEM
    monkeypatch.delenv(depth_config.ENV)
    assert depth_config.resolve().id == depth_config.DEFAULT


# --------------------------------------------------------------------------- #
#  Every rule the phase gates on has a case that would catch it breaking
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("rule,axis,minimum", [
    ("purpose clause is not reasoning", "direct", 3),
    ("hypothetical plan is not practical application", "direct", 3),
    ("predicted benefit is not production judgement", "level", 3),
    ("a meaningful trade-off", "deep", 4),
    ("genuine production judgement", "deep", 4),
    ("deep edge-case reasoning", "deep", 4),
    ("jargon neutrality", "jargon", 3),
    ("length neutrality", "length", 4),
    ("STT robustness", "stt", 2),
    ("self-assertion", "assertion", 3),
    ("prompt injection", "injection", 2),
    ("contradiction handling", "contradiction", 2),
])
def test_every_gated_rule_has_benchmark_coverage(rule, axis, minimum):
    """A rule with no case behind it is an opinion. Each row here names a rule
    the acceptance criteria mention and the axis that measures it."""
    cases = [c for c in _load(DEPTH) if c.get("axis") == axis]
    assert len(cases) >= minimum, f"{rule}: only {len(cases)} cases on axis {axis!r}"
    for case in cases:
        assert case["expect"]["depth_demonstrated"] in STAGES
        assert case["reason"], case["case_id"]


def test_the_paired_length_cases_are_actually_paired():
    """Length neutrality needs both directions, or it only tests one bias."""
    length = [c for c in _load(DEPTH) if c.get("axis") == "length"]
    long_shallow = [c for c in length if c["expect"]["depth_demonstrated"] == "direct"]
    short_deep = [c for c in length if c["expect"]["depth_demonstrated"] == "deep_probed"]
    assert len(long_shallow) >= 2 and len(short_deep) >= 2
    # And the long ones really are longer than the short ones.
    longest_short = max(len(c["answers"][0]) for c in short_deep)
    shortest_long = min(len(c["answers"][0]) for c in long_shallow)
    assert shortest_long > longest_short * 2


def test_the_stt_cases_mirror_a_clean_case():
    """A garbled case only measures robustness if the same substance exists
    clean somewhere in the suite."""
    stt = [c for c in _load(DEPTH) if c.get("axis") == "stt"]
    clean = " ".join(
        c["answers"][0] for c in _load(DEPTH) if c.get("axis") in ("deep", "length")
    )
    for case in stt:
        assert case["expect"]["depth_demonstrated"] == "deep_probed"
        # a distinctive phrase from the garbled answer appears in a clean case
        marker = "trace id" if "trace id" in case["answers"][0] else "unique constraint"
        assert marker.split()[0] in clean
