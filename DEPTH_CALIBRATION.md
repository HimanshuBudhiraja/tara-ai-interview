# `depth_demonstrated` — calibration

**Gate: DEPTH CALIBRATION REQUIRES FURTHER WORK.**

Not because the measurement failed. Because it succeeded far enough to identify
a materially better configuration, and the provider budget ran out before the
two checks that would let it ship: the gold-benchmark regression and the
strong/thin/messy re-run. The key's $10 limit is exhausted (`$10.0121` used),
and every live call since has returned `403 Key limit exceeded`.

Everything below is measured. Nothing in the shipped evaluator changed.

---

## 1. Executive summary

* **One authoritative rubric now exists** (§3), and both benchmark datasets have
  been reconciled against it (§4). 84 depth-asserting cases carry an
  authoritative label, a status and a written rationale, enforced by tests.
* **One gold contradiction was found and resolved.** `dep-04-tradeoffs` was
  labelled `probed` while its own stated reason called trade-off reasoning "the
  top of the Depth scale". Flagged `GOLD_DATA_CONTRADICTION`, resolved to
  `deep_probed`. No case was deleted; none needed the `AMBIGUOUS_GOLD` escape.
* **Three extractor configurations were run five times each** over frozen input
  — 690 case-runs, 1,380 provider calls, no schema errors, no silent fallback.
* **`depth_cfg_reverted` materially outperforms the shipped prompt**: exact depth
  agreement **0.743** (0.696–0.783) against **0.665** (0.630–0.674). The ranges
  do not overlap across five runs each. Material errors 11.8 against 15.4, also
  non-overlapping. Length neutrality 0.45 → 0.80 and speech-to-text robustness
  0.10 → 0.80, the two axes that failed the previous gate.
* **`depth_cfg_rubric_v1` — my own minimal, reconciliation-derived edits — did
  not work**: agreement identical to the shipped prompt (0.665), repeatability
  worse (0.739 against 0.870), length worse (0.25). It made the extractor more
  conservative: deep precision 0.844 against 0.699, deep recall 0.447 against
  0.600. Recorded rather than quietly dropped.
* **The `trade_offs` promotion was measured twice and rejected twice.**
  Re-derived offline from the stored labels of all 15 runs, promoting it to the
  deep tier raises agreement (0.665 → 0.709 shipped, 0.743 → 0.791 candidate)
  and breaks contradiction handling (0.80 → 0.30, 0.70 → 0.50): a retracted
  trade-off starts carrying the deepest stage. Phase 17 had already measured it
  costing five gold-benchmark cases.
* **Repeatability is better than the previous phase reported**, on a larger
  sample: 40/46 cases identical across five runs (0.870) for the shipped
  configuration, against the 8/12 = 0.667 measured on twelve cases and three
  runs. The candidate configuration is worse on this axis: 36/46 = 0.783.
* **Nothing was changed in the shipped evaluator.** No prompt, no derivation, no
  criteria, no recommendation, no coverage. The candidate configuration exists
  as a named, tested, one-environment-variable switch awaiting the two blocked
  checks.

---

## 2. The previous failure, and what this phase did about it

Phase 17 ended `DEPTH CALIBRATION REQUIRES FURTHER WORK` with three named next
steps. All three were attempted:

| Phase 17 said | This phase |
| --- | --- |
| reconcile the two datasets' depth labels under one written rubric | done — §3, §4, §5, enforced by `tests/test_depth_datasets.py` |
| run each candidate configuration five times | done — §7, §8. The one-run comparisons that made phase 17 inconclusive are now five-run comparisons with non-overlapping ranges |
| compare `SCORING_MODEL` candidates | **blocked** — the key's budget was exhausted before this step (§12) |

---

## 3. Authoritative depth rubric

One rubric. Where a case is disputed, this is what decides it, and every
reconciliation row in §4 cites the clause it applied.

### The two figures are different measurements

    depth_reached        how far the INTERVIEW investigated   — counted from the turns
    depth_demonstrated   how far the CANDIDATE's evidence goes — derived from the evidence

Neither is derived from the other, and the second is never derived from the
score, the answer's length, or the number of probes.

### `direct` — the candidate shows they understand the thing

A correct definition, a correct mechanism in general, an accurate explanation of
why or how something works, correct terminology.

It does **not** become `probed` because the answer also contains:

* a purpose clause — "we log the request id **so you can find** the failing
  requests" says what logging is for;
* a consequence mentioned in passing;
* a predicted effect — "a read-through cache **would absorb** most of the
  traffic";
* a short example, a keyword, or a technology name;
* a generic "so that…".

A general rule stated correctly is `direct`: "you index the columns in the WHERE
clause and the ones you sort by" is how indexing works, not a design for the
query in front of them.

### `probed` — the candidate shows applied understanding

One of:

* **something they did** — a project, a decision they made, a number they
  measured, an instance they handled;
* **a mechanism at implementation resolution for THIS problem** — which
  mechanism, written where, doing what on the second request. A plan in the
  conditional qualifies if it commits to how; an intention does not. "I'd look
  at which lookups repeat" and "I'd check the dashboards" name a step and commit
  to nothing;
* **reasoning that weighs one option against another**, with the logic stated;
* **some trade-offs** — options named and one chosen with a reason, without
  saying what was given up.

### `deep_probed` — the candidate shows engineering judgement

The answer engages with what goes wrong or what it costs, **and lets that change
the decision**. One of:

* a specific failure mode analysed — what breaks, and what they do about it;
* a production, scale, reliability or security constraint that bounded a choice;
* an operational consequence they had to accept, plan around, or refuse;
* a meaningful trade-off — an alternative weighed with what was given up named;
* competing considerations resolved with an explicit rationale.

**The deciding test for a consequence** is whether the answer says what they
did, refused to do, or had to accept *because* of it. "An OOM in that cache is an
outage in checkout, so the limit is set well below the pod's" bounded a
decision. "Adding an index the planner ignores costs write throughput" is a
reason for a step — `reasoning`, not production judgement.

Depth is **not** established by: jargon, however current; seniority language;
answer length; a claim about themselves; or the interviewer having probed. Depth
does **not** require architecture vocabulary — a debugging answer demonstrates
it through a diagnosis sequence and the failure mode of the obvious fix, and a
stakeholder-communication answer through the consequence of the wrong framing.

### How the rubric reaches the code

The extractor labels each quote with a dimension, a type and a strength. The
derivation maps them:

    conceptual_understanding → direct
    practical_application    → probed
    reasoning                → probed
    trade_offs               → probed        (§9 — measured, capped, documented)
    edge_cases               → deep_probed
    production_judgment      → deep_probed

Only `supported` items count; `weak` carries nothing; anything deeper than
`direct` requires `strong`; the result is the deepest qualifying item.

---

## 4. Dataset reconciliation

Two datasets assert `depth_demonstrated`: `evals/datasets/evaluator_benchmark.json`
(38 cases) and `evals/datasets/depth_benchmark.json` (46 cases). Every one now
carries `depth_reconciliation` — status, previous label, authoritative label,
rationale — and both files name the rubric.

21 cases were adjudicated individually by reading the answer, the question, the
skill and the authored evidence. The rest were undisputed and are recorded as
`CONFIRMED_UNDISPUTED`.

| Case | Dataset | Was | Authoritative | Status | Deciding clause |
| --- | --- | --- | --- | --- | --- |
| `dep-04-tradeoffs` | gold | probed | **deep_probed** | GOLD_DATA_CONTRADICTION | alternative weighed, 60s staleness given up, rejected option's cost stated → competing considerations with rationale |
| `acc-01-correct` | gold | probed | probed | CONFIRMED | mechanism at implementation resolution for this problem |
| `len-01-short-excellent` | gold | probed | probed | CONFIRMED | same clause; length is not the test |
| `dep-02-conceptual` | gold | direct | direct | CONFIRMED | pattern named, effect predicted, no mechanism detail |
| `cla-01-structured` | gold | probed | probed | CONFIRMED_BOUNDARY | "costs write throughput" is a reason for a step |
| `com-01-direct` | gold | probed | probed | CONFIRMED_BOUNDARY | the decision is the stakeholder's; nothing was given up |
| `com-02-adequate` | gold | direct | direct | CONFIRMED | accurate explanation, purpose clause |
| `ps-02-basic-troubleshooting` | gold | probed | probed | CONFIRMED | verification, not a bounded decision |
| `dpt-01-direct-direct` | gold | direct | direct | CONFIRMED | purpose clause |
| `dpt-02-direct-deep` | gold | deep_probed | deep_probed | CONFIRMED | sampling cost bounded the decision |
| `dd-len-short-01` | depth | deep_probed | deep_probed | CONFIRMED_KNOWN_CAP | meaningful trade-off; the implementation caps it (§9) |
| `dd-deep-03` | depth | deep_probed | deep_probed | CONFIRMED | retention cost bounded the sampling decision |
| `dd-indep-deep-03` | depth | deep_probed | deep_probed | CONFIRMED | names what they would not do and why |
| `dd-indep-deepdeep-02` | depth | deep_probed | deep_probed | CONFIRMED | failure mode of the obvious fix changed the remedy |
| `dd-probed-04` | depth | probed | probed | CONFIRMED | options and a reason, nothing given up |
| `dd-level-junior` / `-mid` / `-senior` | depth | probed | probed | CONFIRMED | a measured benefit is practical application; the label is level-invariant because the words are |
| `dd-direct-04` | depth | direct | direct | CONFIRMED | a general rule, not a design |
| `dd-probed-01`, `dd-probed-03` | depth | probed | probed | CONFIRMED_FIXTURE_REPAIRED | phase 17 removed clauses that put them on the boundary |

**Ambiguous cases: none.** Every disputed case was decidable by a clause of the
rubric, so the strict denominator is the full 46. The mechanism for excluding an
`AMBIGUOUS_GOLD` case exists and is tested; it is simply unused.

**Gold-label changes: one.** Recorded in the dataset, in
`evals/results/depth_reconciliation.json`, and pinned by
`test_the_one_reconciled_contradiction_is_recorded_as_such`.

---

## 5. Gold-label integrity, as a test

`tests/test_depth_datasets.py` (36 tests) holds the reconciliation in place:

* every depth-asserting case has an authoritative label, a status and a
  rationale longer than a phrase;
* no label contradicts its own `gold_evidence` under the derivation — the only
  permitted exception is the documented cap, asserted by name;
* `depth_reached` equals the value derived from the answer count, so a case
  cannot disagree with itself;
* the two datasets agree where they test the same substance (`dpt-02` and
  `dd-deep-03` are the same observability answer);
* all eight reached/demonstrated combinations are present in the suite;
* every rule the acceptance criteria name has at least the expected number of
  cases behind it, the length cases really are paired by length, and the
  speech-to-text cases mirror a clean case.

---

## 6. Candidate configurations

A configuration is a list of **edits to the shipped prompt**, not a copy of it
(`services/evaluation/depth_config.py`). A missing anchor raises, so a
configuration cannot silently decay into the shipped one, and
`depth_cfg_current` is asserted byte-identical to `evidence.SYSTEM`.

| id | what it changes |
| --- | --- |
| `depth_cfg_current` | nothing. The shipped prompt, and the control |
| `depth_cfg_reverted` | phase 17's reverted wording, reconstructed from that phase's record: the `evidence_strength` rubric, the jargon and trade-off-concept discriminators, the sharpened `production_judgment`, and the dense-clause rule |
| `depth_cfg_rubric_v1` | only what the reconciliation supports: production judgement must have *changed a decision*; an intention is not an application; transcription damage and length never move a strength grade; a dense clause is two items |

Selection is `TARA_DEPTH_CFG`, read at call time. Production sets nothing.
Nothing in the evaluator branches on the identifier.

---

## 7. Five-run methodology

For each configuration: five runs over the identical frozen input — same 46
cases, same answers, same questions, same snapshot construction, same model
(`openai/gpt-4.1-mini` via the gateway), same temperature, same gateway
settings. 46 cases × 2 calls × 5 runs × 3 configurations = **1,380 provider
calls**, 0 schema errors, 0 silent fallbacks, provider and resolved model
recorded on every call.

Per case per run: gold depth, predicted depth, agreement, the full evidence
labels (dimension, type, strength, criterion, quote). Per configuration: exact
agreement, per-class precision and recall, material errors, repeatability
(identical across all five runs) and pairwise repeatability, the six neutrality
axes, and the Phase-6 taxonomy — each reported as mean, min, max and standard
deviation rather than a mean alone.

Storing the labels is what makes §9 possible: a derivation rule can be scored
over evidence already paid for, with no further calls.

---

## 8. Results by configuration

Five runs each, 46 strict cases, `openai/gpt-4.1-mini`.

| | `depth_cfg_current` | `depth_cfg_reverted` | `depth_cfg_rubric_v1` |
| --- | --- | --- | --- |
| exact depth agreement | 0.665 (0.630–0.674, sd 0.017) | **0.743 (0.696–0.783, sd 0.032)** | 0.665 (0.652–0.674, sd 0.011) |
| material errors | 15.4 (15–17) | **11.8 (10–14)** | 15.4 (15–16) |
| repeatability (all five identical) | **40/46 = 0.870** | 36/46 = 0.783 | 34/46 = 0.739 |
| pairwise repeatability | **0.928** | 0.891 | 0.863 |
| direct precision / recall | 0.909 / 0.778 | **1.000 / 0.867** | 0.909 / 0.778 |
| probed precision / recall | 0.400 / 0.582 | 0.469 / 0.545 | 0.417 / **0.818** |
| deep precision / recall | 0.699 / 0.600 | 0.716 / **0.741** | **0.844** / 0.447 |
| length neutrality | 0.45 | **0.80** | 0.25 |
| jargon neutrality | 0.667 | 0.80 | **0.933** |
| self-assertion neutrality | 1.00 | 1.00 | 1.00 |
| speech-to-text robustness | 0.10 | **0.80** | 0.40 |
| injection robustness | 1.00 | 1.00 | 1.00 |
| contradiction handling | **0.80** | 0.70 | 0.50 |

**The comparison that matters.** `depth_cfg_reverted`'s worst run (0.696) is
better than `depth_cfg_current`'s best (0.674), and its worst material-error
count (14) is better than the shipped prompt's best (15). Across five runs each
that is not a lucky draw — it is the finding phase 17 could not establish at one
run per configuration.

**Its cost.** Repeatability falls from 0.870 to 0.783 and contradiction handling
from 0.80 to 0.70. The first is the honest trade: a prompt that finds more of
the deep evidence also finds it less consistently.

**`depth_cfg_rubric_v1` is a negative result.** My reconciliation-derived edits —
each individually defensible — made the extractor more conservative: it gained
deep precision (0.844) and jargon neutrality (0.933) and lost deep recall
(0.447), length neutrality (0.25) and repeatability (0.739), for no net
agreement. Reported because a phase that only reports what worked is not a
measurement.

---

## 9. Error taxonomy

Every failed (case, run) is classified as exactly one of the six labels, in a
fixed order, by `evals/depth.classify_failure`. A case is only a derivation
failure if the rule cannot produce the authoritative label **from the case's own
authored evidence** — so an extractor mistake can never be recorded as a rule
defect, or the reverse.

| class | `current` | `reverted` | `rubric_v1` |
| --- | --- | --- | --- |
| EXTRACTION_FALSE_POSITIVE | 9 | 11 | 8 |
| EXTRACTION_FALSE_NEGATIVE | 8 | 5 | 12 |
| DERIVATION_RULE_ERROR | 1 | 1 | 1 |
| MODEL_VARIANCE | 1 | 0 | 1 |
| GOLD_DATA_ERROR | 0 | 0 | 0 |
| AMBIGUOUS_GOLD | 0 | 0 | 0 |

**One derivation error, in every configuration, and it is the documented cap**
(`dd-len-short-01`). Everything else is extraction. That is the phase's central
structural result: after reconciliation, the deterministic layer accounts for
one failure out of forty-six, and the labels it reads account for the rest.

### `trade_offs` and `production_judgment` — Phase 8

The brief asks whether `trade_offs` should stay at `probed`, move to the deep
tier, or be treated as deep in some deterministic context. All three were scored
**offline over the stored labels of all fifteen runs**, with no further provider
calls:

| derivation rule | agreement on `current` | on `reverted` | on `rubric_v1` | contradiction axis |
| --- | --- | --- | --- | --- |
| shipped — `trade_offs` = probed | 0.665 | 0.743 | 0.665 | 0.80 / 0.70 / 0.50 |
| `trade_offs` = deep | 0.709 | **0.791** | 0.743 | **0.30 / 0.50** |
| `trade_offs` = deep if corroborated by another strong non-conceptual item | 0.722 | 0.791 | 0.743 | — |
| deep tier requires two strong deep items | 0.587 | 0.626 | 0.557 | — |

* **Promotion raises agreement and breaks contradiction handling.** A retracted
  trade-off — stated in one turn and withdrawn in the next — starts carrying the
  deepest stage, because the extractor does not reliably mark the withdrawn item
  `contradicted`. Contradiction handling falls from 0.80 to 0.30 on the shipped
  prompt. Combined with phase 17's measurement of five gold-benchmark cases
  lost, that is two independent costs against one benefit. **Not shipped.**
* **Context-dependent treatment is not justified.** Requiring corroboration
  performs the same as unconditional promotion (identical on two of three
  configurations), so the extra rule buys nothing measurable. The brief's
  condition for implementing it is not met.
* **Making the deep tier harder is clearly worse** — 0.557–0.626 — so a single
  genuine failure mode still establishes deep depth.
* **`production_judgment`**: the rubric's line ("the consequence changed what
  they did") was implemented as a prompt edit in `depth_cfg_rubric_v1` and
  measured. It raised deep precision from 0.699 to 0.844 and cut deep recall
  from 0.600 to 0.447. The line is right; used as a prompt instruction on this
  model it trades more than it gains. It stays in the rubric as the adjudication
  standard for gold labels, and out of the shipped prompt.

---

## 10. Repeatability analysis

| | cases | runs | identical across all runs | pairwise |
| --- | --- | --- | --- | --- |
| phase 17's measurement | 12 | 3 | 8/12 = 0.667 | not measured |
| `depth_cfg_current` | 46 | 5 | **40/46 = 0.870** | 0.928 |
| `depth_cfg_reverted` | 46 | 5 | 36/46 = 0.783 | 0.891 |
| `depth_cfg_rubric_v1` | 46 | 5 | 34/46 = 0.739 | 0.863 |

The previous 0.667 was measured on twelve cases chosen for being
depth-sensitive; on the full suite the shipped configuration is stable on 40 of
46 cases across five runs. So the instability is real but narrower than the
previous phase's headline implied — and it is concentrated: the six unstable
cases under `depth_cfg_current` are the ones where a single clause can be read
as either a reason or a consequence.

The deterministic half contributes none of it: `test_the_derivation_is_deterministic_over_repeated_calls`
applies the rule 200 times to the same labels and to their reverse order.

---

## 11. `SCORING_MODEL` comparison — not performed

**Blocked by provider budget.** The harness is built and takes `--model`, which
overrides the gateway's workload table for the experiment only and never touches
business logic; the gateway records the model it actually used on every call.
The plan was three runs each of `anthropic/claude-sonnet-5` ($0.58/run) and
`openai/gpt-5.1` ($0.43/run) against the same frozen suite.

The key's limit was reached during the gold-benchmark regression runs. No model
comparison was run, and none is reported. Manufacturing a comparison from
partial runs would be worse than the gap.

    python -m evals.depth --config depth_cfg_reverted --runs 3 --model anthropic/claude-sonnet-5
    python -m evals.depth --config depth_cfg_reverted --runs 3 --model openai/gpt-5.1

---

## 12. Real-provider results, and what the budget stopped

**Completed on the real provider** (`openrouter`, `openai/gpt-4.1-mini`,
`json_schema` structured output, no silent fallback, 0 schema errors):

* the fifteen configuration runs in §8 — 1,380 calls;
* structured output, evidence labels, depth derivation, repeatability, the
  speech-to-text cases, the paired long/shallow and short/deep cases, prompt
  injection, contradiction, and all eight reached/demonstrated combinations,
  all as part of those runs.

**Blocked when the key hit its limit:**

| check | status | cost to complete |
| --- | --- | --- |
| gold-benchmark regression, 3 runs × 2 configurations | six runs started and are **discarded** — they executed while the key was failing, producing 29–62 material errors and empty evidence. Not evidence about anything | ~$0.7 |
| `SCORING_MODEL` comparison | not started | ~$3.0 |
| strong / thin / messy persona re-run | not started | ~$0.3 |

The discarded runs were deleted rather than reported. The failure signature is
unambiguous: `403 Key limit exceeded (total limit)`, empty extraction, and every
criterion at the `discussed` floor of 1.

---

## 13. Strong / thin / messy — not re-run

**Blocked by the same budget.** No persona regression was run in this phase, and
none is claimed. What is known from the previous phase's run, on the same
shipped configuration that is still in place: the strong persona produced four
distinct reached/demonstrated pairs across six skills, and thin and messy
produced `deep_probed → direct` on all six — probed to the bottom of the ladder,
demonstrating nothing past the first answer.

Since the shipped evaluator is byte-identical to what produced those results,
they remain the current description of persona behaviour. They are not evidence
about the candidate configuration, which has never been run on a persona.

---

## 14. Recruiter-report verification

Verified in the browser against real persisted evaluations, on the running app.

| combination | where | what the report says |
| --- | --- | --- |
| `direct → direct` | Communication clarity, session `cc49db79` | "The evidence reached the same depth the interview investigated." |
| `direct → probed` | Customer empathy, same report | "The evidence went further than the interview needed to probe…" |
| `direct → deep_probed` | Policy judgment, same report | as above |
| `probed → probed` | Troubleshooting, same report | "…the same depth the interview investigated." |
| `deep_probed → probed` | De-escalation, same report | "Tara investigated further than the evidence reached." |
| `deep_probed → direct` | Customer empathy, session `868d1fe2` | "Tara investigated further than the evidence reached. The follow-ups did not establish deeper capability than the first answer did." |
| `deep_probed → deep_probed` | no pilot transcript has produced it | covered by console test: the sentence is an agreement, not an achievement |

Every skill card showed both labels and both captions — "How far Tara
investigated this skill" and "How far the candidate's own evidence went". No
wording implies that being probed deeply means deep capability, and a console
test now asserts that across all nine combinations.

---

## 15. Remaining limitations

**Fixed this phase**

* Two datasets with conflicting depth labels, one of which contradicted its own
  stated reason. Now one rubric, one authoritative label per case, both enforced.
* One-run configuration comparisons. Now five runs with non-overlapping ranges.
* No way to attribute a benchmark number to a prompt. Now a named configuration
  recorded with every result.
* `trade_offs` and `production_judgment` treated as open questions. Both are now
  measured, decided, and pinned by tests that record the numbers.

**Known limitations, carried**

* The shipped configuration's length neutrality (0.45) and speech-to-text
  robustness (0.10) remain poor. The fix is measured and unshipped.
* `trade_offs` alone is capped at `probed` — one case in forty-six, understated
  deliberately, visible on the recruiter's row under demonstrated evidence.
* Extraction accounts for every failure but one. Depth agreement on this model is
  ~0.67 shipped and ~0.74 with the candidate configuration; neither is a signal
  to publish a per-skill depth figure as fact without the evidence beside it,
  which the report already does.
* Level-relativity is still absent from the criteria: the same answer scored
  20/25 at junior, mid and senior in the previous phase. Out of scope here.

**Not verified**

* Gold-benchmark regression for `depth_cfg_reverted` under the reconciled labels.
* Any `SCORING_MODEL` other than `openai/gpt-4.1-mini`.
* Persona behaviour under the candidate configuration.
* The live opt-in test suite: 3 of 7 pass, 4 fail with `403 Key limit exceeded`.

---

## 16. Exact recommendation for the next engineering phase

1. **Top up the provider key.** Everything below needs about **$4** of budget.
2. **Run the gold-benchmark regression** — the one check that stands between the
   measured improvement and shipping it:

       TARA_DEPTH_CFG=depth_cfg_current  python -m evals.benchmark --live --tag cur1   # ×3
       TARA_DEPTH_CFG=depth_cfg_reverted python -m evals.benchmark --live --tag rev1   # ×3

   Run them **one at a time** — six concurrent runs is what exhausted the key.
   Compare material errors and `agreement.depth_demonstrated`, now measured
   against the reconciled labels that fixed `dep-04`.
3. **If the gold benchmark does not regress, promote `depth_cfg_reverted`** by
   folding its edits into `evidence.SYSTEM` and making `depth_cfg_current` the
   new base. Expected: agreement 0.665 → 0.743, length 0.45 → 0.80,
   speech-to-text 0.10 → 0.80, repeatability 0.870 → 0.783.
4. **Then re-run the personas** and confirm scores move only where evidence
   calibration legitimately changed them.
5. **Then compare models** (§11). The hypothesis worth testing is that the
   residual is model capability rather than prompt wording: after reconciliation,
   45 of 46 cases are extraction, not derivation.
6. **Do not touch** `recommend`, `any_severe`, `boundary_of`, coverage, the
   criteria, or the candidate runtime. Recommendation instability remains the
   separate, documented problem it was.

---

## 17. Final promotion decision

**NO PROMOTION. The live verification could not be run.**

### Provider budget, checked first and checked twice

Before any benchmark call, the configured provider was inspected, and then a
single real structured call was made through the gateway rather than trusting a
balance endpoint:

    provider base url   https://openrouter.ai/api/v1
    evaluator slot      scoring
    configured model    openai/gpt-4.1-mini   (SCORING_MODEL)
    temperature         0.2      max_tokens 3000
    credentials         present and used by the gateway · len=73 · sha256:27fbb035
    balance             limit $10 · used $10.012125504 · remaining -$0.0121
    model availability  openai/gpt-4.1-mini is listed by the provider
    smoke call          ProviderUnavailable — 403 "Key limit exceeded (total limit)"
    silent fallback     none — the gateway raised rather than substituting a model

The key is the same one that produced every measurement above
(`sha256:27fbb035`), and it is exhausted. No stub was substituted, no benchmark
was altered to avoid calls, and no number in this section is estimated.

### What that blocks

| required check | status |
| --- | --- |
| gold-benchmark regression, one configuration at a time, three configurations | **not run** |
| comparison of the regression against the five-run means | **not possible** |
| strong / thin / messy persona regression | **not run** |
| promotion decision under §5's quality, product-behaviour and persona conditions | **cannot be evaluated** |
| browser verification of all nine reached/demonstrated combinations | deferred — §8 gates it on promotion; six of nine were verified on real data in the previous run |
| `SCORING_MODEL` comparison | deliberately not run, and now formally **deferred** (§11) |

`depth_cfg_reverted` therefore stays a named, tested candidate. The shipped
configuration is unchanged, `system_prompt()` still returns `evidence.SYSTEM`
byte-for-byte, and nothing about production behaviour moved in this phase.

### Configuration selected

    NO PROMOTION — depth_cfg_current remains the default

### Why

The evidence for `depth_cfg_reverted` is strong and unchanged: five runs each,
its worst run better than the shipped prompt's best on both agreement and
material errors, and it fixes the two axes that failed the previous gate. What
is missing is the check that stopped phase 17 — whether the gain on the depth
suite costs anything on the gold benchmark. Promoting without it would repeat
exactly the mistake that phase was reverted for, with a better-measured
candidate but the same blind spot.

### The regression that is still owed, verbatim

Run these **one at a time**. Six concurrent runs is what exhausted the key, and
the six that executed while it was failing produced 29–62 material errors and
empty evidence — they were discarded, not reported.

    TARA_DEPTH_CFG=depth_cfg_current    python -m evals.benchmark --live --tag cur1
    TARA_DEPTH_CFG=depth_cfg_current    python -m evals.benchmark --live --tag cur2
    TARA_DEPTH_CFG=depth_cfg_current    python -m evals.benchmark --live --tag cur3
    TARA_DEPTH_CFG=depth_cfg_reverted   python -m evals.benchmark --live --tag rev1
    TARA_DEPTH_CFG=depth_cfg_reverted   python -m evals.benchmark --live --tag rev2
    TARA_DEPTH_CFG=depth_cfg_reverted   python -m evals.benchmark --live --tag rev3
    TARA_DEPTH_CFG=depth_cfg_rubric_v1  python -m evals.benchmark --live --tag rub1

Then, only if `depth_cfg_reverted` still leads on `material_errors` and
`agreement.depth_demonstrated`:

    TARA_DEPTH_CFG=depth_cfg_reverted python -m tools.pilot_drive --concurrent strong thin messy

Cost at the measured rate: about **$0.8** for the seven gold runs and **$0.3**
for the personas. A `SCORING_MODEL` comparison would be a further ~$3 and is
not needed to decide promotion.

### Promotion mechanics, when the checks pass

Minimal, and reversible:

1. fold `depth_cfg_reverted`'s five edits into `evidence.SYSTEM`;
2. leave `depth_cfg_current` in `CONFIGURATIONS` as the **previous** wording, so
   the rollback is `TARA_DEPTH_CFG=depth_cfg_previous` and the history stays in
   the file rather than in a commit message;
3. add a test asserting the default resolves to the promoted wording;
4. **do not** bump `ENGINE_VERSION` or `RESULT_CONTRACT_VERSION`: the policy is
   that the engine version changes when the same transcript would score
   differently. A prompt that extracts evidence more accurately does change
   scores on some transcripts, so this is the one judgement call in the
   promotion — the honest reading is that it **does** qualify, and the bump
   should be made deliberately at promotion time with the persona diff in hand,
   not pre-emptively now;
5. touch nothing in `recommend`, `any_severe`, `boundary_of`, coverage, the
   criteria, or the candidate runtime.

### Rollback

Two levels, both already in place:

* **Configuration**: `TARA_DEPTH_CFG=depth_cfg_current` restores the shipped
  wording exactly — `test_the_shipped_configuration_is_the_shipped_prompt`
  asserts it is byte-identical to `evidence.SYSTEM`. After a promotion the same
  switch names the previous wording.
* **Code**: no evaluator logic changed in this phase, so reverting the phase is
  reverting a benchmark harness, a dataset reconciliation and a document.

### Remaining limitations, unchanged and not hidden

* `trade_offs` alone is capped at `probed` — one case in forty-six, understated
  deliberately, with the trade-off still visible on the recruiter's row.
* Depth agreement is ~0.665 shipped and ~0.743 for the candidate. Neither
  supports publishing a per-skill depth figure without its evidence beside it,
  which the report already does.
* Repeatability: 40/46 identical across five runs shipped, 36/46 for the
  candidate. The candidate is *worse* on this axis, and that is part of what the
  gold regression is meant to weigh.
* Criteria remain level-invariant: the same answer scored 20/25 at junior, mid
  and senior. A criteria problem, out of scope here.
* Model variance is the residual: after reconciliation, 45 of 46 failures are
  extraction rather than derivation.

---

**DEPTH CALIBRATION BLOCKED — PROVIDER BUDGET**
**NO PROMOTION**
