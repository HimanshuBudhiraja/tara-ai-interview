"""Named extractor configurations, so a benchmark result can name what produced it.

The evidence extractor's system prompt is one long string. Comparing candidate
wordings of it needs three things the previous phase did not have: a stable
identifier per wording, a guarantee that the shipped wording is byte-identical
to what production sends, and a record of which wording produced which number.

So a configuration is not a copy of the prompt — it is a list of **edits** to
the shipped prompt:

    depth_cfg_current    no edits. Byte-identical to `evidence.SYSTEM`.
    depth_cfg_reverted   the phase-17 wording that measured better on length
                         and speech-to-text and worse on the gold benchmark.
    depth_cfg_rubric_v1  only the edits the dataset reconciliation supports.

Every edit is an (anchor, replacement) pair applied to the base text, and a
missing anchor raises rather than silently doing nothing. That makes each
configuration a diff a reviewer can read, and makes it impossible for a
configuration to drift away from the shipped prompt without the drift being
visible in this file.

Selection is by environment variable — `TARA_DEPTH_CFG` — read at call time.
Production sets nothing and gets `depth_cfg_current`. Nothing in the evaluator's
business logic branches on the identifier: the only thing a configuration
changes is the words in the prompt.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

ENV = "TARA_DEPTH_CFG"
DEFAULT = "depth_cfg_current"


class ConfigError(RuntimeError):
    """A configuration that cannot be applied to the current prompt.

    Raised rather than skipped: an edit whose anchor has moved would otherwise
    produce a run labelled with a configuration it did not use.
    """


@dataclass(frozen=True)
class DepthConfig:
    id: str
    summary: str
    #: (anchor, replacement). The anchor must appear exactly once in the base.
    edits: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    def apply(self, base: str) -> str:
        text = base
        for anchor, replacement in self.edits:
            found = text.count(anchor)
            if found != 1:
                raise ConfigError(
                    f"{self.id}: anchor appears {found} times, expected once: "
                    f"{anchor[:70]!r}"
                )
            text = text.replace(anchor, replacement, 1)
        return text


# --------------------------------------------------------------------------- #
#  The edits, each traceable to a reconciliation finding
# --------------------------------------------------------------------------- #
#: Phase 17's `evidence_strength` rubric, in the wording that measured 4/4 on
#: length neutrality and 2/2 on speech-to-text. Its cost was measured too: the
#: configurations containing it sat at 7-9 gold-benchmark material errors
#: against a control of 4-6, and the deep-tier vocabulary inside a field
#: definition that applies to every item is the suspected mechanism.
_STRENGTH_ANCHOR = '''- "evidence_strength": strong | moderate | weak. Nothing else — these three belong
  to this field alone and appear in no other list.'''

_STRENGTH_FULL = '''- "evidence_strength": strong | moderate | weak. Nothing else — these three belong
  to this field alone and appear in no other list. How firmly THIS QUOTE establishes
  THAT DIMENSION:

      strong    the quote establishes it on its own. A reader needs no charity and no
                inference: the mechanism is described, the instance is specific, the cost
                is named, the failure is stated.
      moderate  it points at the dimension but the reader has to supply something — an
                unnamed example, an implied consequence, a decision whose reason is left
                to be guessed at.
      weak      a gesture in the direction of the dimension. An acknowledgement that
                edge cases exist, a trade-off named as a concept, a claim about
                themselves.

  Grade it on SUBSTANCE only. It is not a judgement of how the answer was expressed:
  garbled transcription, missing punctuation, repeated words, filler and false starts
  are the microphone's doing and never lower the strength of what was said. A specific
  failure mode stated in a broken sentence is strong evidence of that failure mode.
  Nor is it a judgement of length: one clause naming what a decision cost is strong,
  and three sentences of definition are not.'''

#: The same field, with the neutrality rules and WITHOUT the deep-tier examples.
#: This is the half the reconciliation supports: transcription damage and answer
#: length must not move a strength grade, and nothing about that requires naming
#: costs and failures inside a definition that applies to all six dimensions.
_STRENGTH_NEUTRAL = '''- "evidence_strength": strong | moderate | weak. Nothing else — these three belong
  to this field alone and appear in no other list. It records how firmly THIS QUOTE
  establishes THE DIMENSION YOU TAGGED IT WITH — no more than that.

  Grade it on SUBSTANCE only. It is not a judgement of how the answer was expressed:
  garbled transcription, missing punctuation, repeated words, filler and false starts
  are the microphone's doing and never lower the strength of what was said. Nor is it
  a judgement of length: a single precise clause can be strong, and three sentences of
  definition are not.'''

_DENSE_ANCHOR = '''- Record what is THERE. If the candidate said little, return few items — do not pad.'''

_DENSE_FULL = '''- Record what is THERE. If the candidate said little, return few items — do not pad.
- One sentence can carry more than one dimension, and a short answer is where that happens.
  "Treat the constraint violation as a duplicate, not an error, or the customer sees a
  failure for a payment that went through" is a mechanism AND a named customer consequence:
  that is two items, not one item at the shallower reading. Splitting a dense clause is not
  padding — collapsing it understates what the candidate said.'''

_REASONING_ANCHOR = '''platitude — "cache invalidation is the hard part", "you have
                             to be careful up front" — reasoning about anything.'''

_REASONING_JARGON = '''platitude — "cache invalidation is the hard part", "you have
                             to be careful up front" — reasoning about anything. A purpose
                             clause attached to a TECHNOLOGY NAME is the same thing wearing
                             jargon: "CQRS so reads and writes scale independently" and
                             "logs for the detail, metrics for the trend" define what the
                             things are for. Definitions are `conceptual_understanding`,
                             however current the vocabulary.'''

_TRADEOFF_ANCHOR = '''                             saying what it cost is.'''

_TRADEOFF_CONCEPT = '''                             saying what it cost is. Naming the CONCEPT is furthest of all
                             from demonstrating one: "caching is a trade-off and you have to
                             think about the trade-offs" is the word, not the thing.'''

_PJ_ANCHOR = '''      production_judgment    needs a NAMED consequence or constraint. "It depends on the
                             provider", "it varies by company", "you want it to scale" name
                             none. "An OOM in the cache is an outage in checkout" names one.'''

#: Phase 17's wording. It failed its own targets and is the suspected cause of
#: two `probed → deep_probed` regressions, because naming "COST" made a cost
#: mentioned in passing a better fit for the deepest tier.
_PJ_REVERTED = '''      production_judgment    needs a NAMED COST, LIMIT, CONSTRAINT or IRREVERSIBLE step.
                             "It depends on the provider", "it varies by company", "you
                             want it to scale" name none. "An OOM in the cache is an outage
                             in checkout" names one. A measured BENEFIT is not one: "the hit
                             rate was eighty per cent, which took the load off the service"
                             is a result they achieved — practical application — and
                             "unmatched rows went to a queue" is a process they followed.
                             The question is what the decision COST or what bound it, not
                             what it improved.'''

#: The reconciliation's line, and the one the rubric is written around: a
#: consequence is production judgement when it CHANGED THE DECISION. That is
#: what separates "an OOM in the cache is an outage in checkout, so the limit is
#: set below the pod's" from "adding an index the planner ignores costs write
#: throughput" — the second is a reason for a step, the first bounded a choice.
_PJ_RUBRIC = '''      production_judgment    needs a consequence that CHANGED WHAT THEY DID. "It depends
                             on the provider", "it varies by company", "you want it to
                             scale" name nothing. "An OOM in the cache is an outage in
                             checkout, so the limit is set well below the pod's" names a
                             consequence AND the decision it bounded.
                             Three things that are NOT production judgement, however
                             operational they sound:
                               * a predicted improvement — "that would absorb most of the
                                 traffic", "it would scale better";
                               * a measured benefit — "the hit rate was eighty per cent,
                                 which took the load off the service" is a result they
                                 achieved, which is `practical_application`;
                               * a cost mentioned to justify a step — "adding an index the
                                 planner ignores costs write throughput" is the logic of a
                                 choice, which is `reasoning`.
                             The test is whether the answer says what they did, refused to
                             do, or had to accept BECAUSE of the consequence.'''

_APPLICATION_ANCHOR = '''      practical_application  needs something THEY DID — a project, a decision they made,
                             a number they measured. Naming a tool is not doing it:
                             "Redis is the standard choice" is generic knowledge, and
                             "we cached the forty hottest SKUs in Redis" is application.'''

#: The reconciliation's second line: an intention is not an application. A
#: conditional answer can still be `reasoning` — this closes only the door
#: between "I would look at it" and "I have done it".
_APPLICATION_RUBRIC = '''      practical_application  needs something THEY DID, or a mechanism specified at
                             implementation resolution FOR THIS PROBLEM — a project, a
                             decision they made, a number they measured, or the actual
                             placement and sequence they would build. Naming a tool is not
                             doing it: "Redis is the standard choice" is generic knowledge,
                             and "we cached the forty hottest SKUs in Redis" is application.
                             An INTENTION is not application either: "I would look at which
                             lookups repeat" and "I'd check the dashboards" name a step and
                             commit to nothing. If the answer is a plan, it earns this tag
                             only where the plan says HOW — which mechanism, written where,
                             doing what on the second request.'''


CONFIGURATIONS: dict[str, DepthConfig] = {
    "depth_cfg_current": DepthConfig(
        id="depth_cfg_current",
        summary="the shipped prompt, unmodified",
    ),
    "depth_cfg_reverted": DepthConfig(
        id="depth_cfg_reverted",
        summary=(
            "phase 17's reverted wording: the strength rubric, the jargon and "
            "trade-off-concept discriminators, the sharpened production_judgment, "
            "and the dense-clause rule"
        ),
        edits=(
            (_STRENGTH_ANCHOR, _STRENGTH_FULL),
            (_DENSE_ANCHOR, _DENSE_FULL),
            (_REASONING_ANCHOR, _REASONING_JARGON),
            (_TRADEOFF_ANCHOR, _TRADEOFF_CONCEPT),
            (_PJ_ANCHOR, _PJ_REVERTED),
        ),
    ),
    "depth_cfg_rubric_v1": DepthConfig(
        id="depth_cfg_rubric_v1",
        summary=(
            "only what the reconciliation supports: production judgement must have "
            "changed a decision, an intention is not an application, transcription "
            "damage and length never move a strength grade, and a dense clause is "
            "two items"
        ),
        edits=(
            (_PJ_ANCHOR, _PJ_RUBRIC),
            (_APPLICATION_ANCHOR, _APPLICATION_RUBRIC),
            (_STRENGTH_ANCHOR, _STRENGTH_NEUTRAL),
            (_DENSE_ANCHOR, _DENSE_FULL),
        ),
    ),
}


def resolve(name: str | None = None) -> DepthConfig:
    """The configuration this call should use.

    Read at call time, never cached: a benchmark run switches configuration
    between calls in one process, and a value captured at import would silently
    label every run with the first one.
    """
    key = (name or os.environ.get(ENV) or DEFAULT).strip()
    config = CONFIGURATIONS.get(key)
    if config is None:
        raise ConfigError(
            f"unknown depth configuration {key!r}; known: "
            + ", ".join(sorted(CONFIGURATIONS))
        )
    return config
