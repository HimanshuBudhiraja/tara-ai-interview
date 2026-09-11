# Model recommendation

Generated 2026-09-06 16:28 UTC. Evidence: `MODEL_EVALUATION.md`.

**Production model changed: NO.**

Nothing in this file has been applied. The six `*_MODEL` environment variables are unset, so every workload runs on `TARA_MODEL_DEFAULT`. Applying a recommendation is a deliberate edit to `.env` followed by a re-run of the candidate suite — a classifier change moves how often Tara probes, which is a change to the interview, not to a dependency.

Current production configuration:

| Workload | Model in production |
| --- | --- |
| answer_classifier | `openai/gpt-4.1-mini` |
| followup_generator | `openai/gpt-4.1-mini` |
| interview_designer | `openai/gpt-4.1-mini` |
| question_generator | `openai/gpt-4.1-mini` |
| scoring | `openai/gpt-4.1-mini` |
| report_generator | `openai/gpt-4.1-mini` |

## How a recommendation is reached

Two different priority orders, because the workloads are not the same job (§18):

- **Runtime** (answer classifier, follow-up generator): correctness → safety → latency → cost. A candidate is sitting in silence waiting for Tara to speak, so a model past its latency budget is ruled out however well it scores.
- **Design-time** (interview designer, question generator, scoring, report): correctness → evidence and rubric adherence → structured-output reliability → safety → cost → latency. Nobody is waiting, so reasoning quality outranks speed.

Three rules are applied mechanically: a safety failure disqualifies; a runtime model over its p95 budget is ineligible; a model that returned nothing usable is ineligible. Everything else is a human call, and where the evidence is missing this file says so rather than guessing.

> ### ⚠ This run predates the current prompts
>
> The prompt and payload for **answer classifier**, **followup generator**, **interview designer**, **question generator**, **report generator**, **scoring** have changed since these results were measured — the prompt-injection fix rewrote both. Every affected workload is reported as `INSUFFICIENT_DATA` rather than ranked, because a recommendation drawn from a benchmark of different software is worse than no recommendation.
>
> Re-run with `make eval && make recommend` to replace it.

## Recommendations at a glance

| Workload | Recommended model | Alternatives | Confidence | Reason |
| --- | --- | --- | --- | --- |
| Answer Classifier | *not determined* | — | `INSUFFICIENT_DATA` | Measured against a prompt that has since changed. These results describe the software before the prompt-injection fix and cannot support a recommendation. |
| Followup Generator | *not determined* | — | `INSUFFICIENT_DATA` | Measured against a prompt that has since changed. These results describe the software before the prompt-injection fix and cannot support a recommendation. |
| Interview Designer | *not determined* | — | `INSUFFICIENT_DATA` | Measured against a prompt that has since changed. These results describe the software before the prompt-injection fix and cannot support a recommendation. |
| Question Generator | *not determined* | — | `INSUFFICIENT_DATA` | Measured against a prompt that has since changed. These results describe the software before the prompt-injection fix and cannot support a recommendation. |
| Scoring | *not determined* | — | `INSUFFICIENT_DATA` | Measured against a prompt that has since changed. These results describe the software before the prompt-injection fix and cannot support a recommendation. |
| Report Generator | *not determined* | — | `INSUFFICIENT_DATA` | Measured against a prompt that has since changed. These results describe the software before the prompt-injection fix and cannot support a recommendation. |

## The benchmark behind this

- **Run:** 2026-09-06 15:33 UTC
- **Models tested:** Claude Haiku 4.5, Claude Sonnet 5, GPT-4.1 mini, GPT-5 nano, GPT-5.1, Gemini 2.5 Flash Lite, Gemini 2.5 Pro
- **Datasets:** `evals/datasets/` — answer_classifier (15), followup_generator (10), interview_designer (5), question_generator (5), scoring (10), report_generator (5), plus 35 adversarial cases
- **Calls:** 232 total · 232 counted · 0 excluded

## Answer Classifier

**Recommended model:** not determined — measured against a prompt that has since changed.

**Confidence:** `INSUFFICIENT_DATA` — Measured against a prompt that has since changed. These results describe the software before the prompt-injection fix and cannot support a recommendation.

## Followup Generator

**Recommended model:** not determined — measured against a prompt that has since changed.

**Confidence:** `INSUFFICIENT_DATA` — Measured against a prompt that has since changed. These results describe the software before the prompt-injection fix and cannot support a recommendation.

## Interview Designer

**Recommended model:** not determined — measured against a prompt that has since changed.

**Confidence:** `INSUFFICIENT_DATA` — Measured against a prompt that has since changed. These results describe the software before the prompt-injection fix and cannot support a recommendation.

## Question Generator

**Recommended model:** not determined — measured against a prompt that has since changed.

**Confidence:** `INSUFFICIENT_DATA` — Measured against a prompt that has since changed. These results describe the software before the prompt-injection fix and cannot support a recommendation.

## Scoring

**Recommended model:** not determined — measured against a prompt that has since changed.

**Confidence:** `INSUFFICIENT_DATA` — Measured against a prompt that has since changed. These results describe the software before the prompt-injection fix and cannot support a recommendation.

## Report Generator

**Recommended model:** not determined — measured against a prompt that has since changed.

**Confidence:** `INSUFFICIENT_DATA` — Measured against a prompt that has since changed. These results describe the software before the prompt-injection fix and cannot support a recommendation.

---

## ⚠ This recommendation is incomplete

No recommendation was produced for: **answer classifier**, **followup generator**, **interview designer**, **question generator**, **scoring**, **report generator**.

A recommendation must not be assembled from a partial run. Finish the evaluation and regenerate:

```bash
python -m evals.cli check-models   # are the ids still live?
python -m evals.cli run --all
python -m evals.cli recommend
```

---

## Applying a decision

```bash
# in .env — nothing here is set automatically
ANSWER_CLASSIFIER_MODEL=
FOLLOWUP_GENERATOR_MODEL=
INTERVIEW_DESIGNER_MODEL=
QUESTION_GENERATOR_MODEL=
SCORING_MODEL=
REPORT_GENERATOR_MODEL=
```

Then run `make test` and `pytest -m server` before and after. A classifier change moves how often Tara follows up, and that is a change to the interview.
