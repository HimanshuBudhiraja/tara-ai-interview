# Pilot report

**Run:** `pilot_653f6ba305` — simulation — three concurrent candidates
**Engine:** `deep_evidence_v2` · **Model:** `openai/gpt-4.1-mini`

Counts, rates and durations only. No candidate content appears in this file.

## Alerts

None. Every proposed threshold held.

## Candidate experience

- attempted **3** · completed **3** (100%) · dropped **0**
- device check reported **3** · failed **0** · chose text **3**
- rejoins **0** · repeats **4** · clarifications **3** · skips **0** · silences **3**
- per answered question: repeat 0.17, clarify 0.12, skip 0.00
- flagged turns **0** · runtime errors **0**

| Measure | n | mean | median | p95 | max |
| --- | --- | --- | --- | --- | --- |
| interview duration (s) | 3 | 47.78 | 58.66 | — | 65.72 |

## Evaluation quality

- requested **3** · completed **3** (100%) · failed **0** (0%)
- failures by stage: none
- evidence checked **47** · untraceable **0** · traceability **1.000**
- evaluations with zero evidence **0** · quarantine rate **0.00**
- recommendations: Proceed to next round ×1, Needs further evaluation ×1, Not suitable for this role ×1

| Measure | n | mean | median | p95 | max |
| --- | --- | --- | --- | --- | --- |
| evidence items | 3 | 15.67 | 7.00 | — | 34.00 |
| quarantined items | 3 | 0.00 | 0.00 | — | 0.00 |
| repairs | 3 | 0.00 | 0.00 | — | 0.00 |
| coverage % | 3 | 72.23 | 100.00 | — | 100.00 |
| score % | 3 | 50.90 | 46.70 | — | 74.00 |

## Recommendation stability

- snapshots evaluated more than once: **0**
- recommendation flips: **0** (rate 0.00)

## Cost and latency

| Measure | n | mean | median | p95 | max |
| --- | --- | --- | --- | --- | --- |
| evaluation latency (s) | 3 | 31.87 | 25.40 | — | 44.89 |
| model calls per evaluation | 3 | 13.67 | 14.00 | — | 14.00 |
| prompt tokens | 3 | 28475.00 | 28664.00 | — | 30324.00 |
| completion tokens | 3 | 2068.00 | 1348.00 | — | 3619.00 |
| evaluation cost (USD) | 3 | 0.01 | 0.01 | — | 0.02 |
| runtime calls per interview | 3 | 32.00 | 40.00 | — | 44.00 |
| runtime latency (s) | 3 | 46.88 | 57.61 | — | 64.49 |
| runtime cost (USD) | 3 | 0.01 | 0.02 | — | 0.02 |
| stage · evidence_extraction (s) | 3 | 17.92 | 12.47 | — | 29.64 |
| stage · result_assembly (s) | 3 | 0.01 | 0.01 | — | 0.01 |
| stage · skill_assessment (s) | 3 | 14.00 | 13.79 | — | 15.32 |

- mean cost of one complete interview: **$0.028**
- slowest single model call: **6134 ms**
- provider error rate: **0.000**
- p95 is withheld: fewer than 20 evaluations. `max` is reported instead, and no production figure should be extrapolated from this sample.

## Human review

- reviewed **0** by **0** reviewer(s) · agree **0** · disagree **0** · needs review **0**
- agreement rate **0.00** · recommendation agreement **0.00** (of 0 stated)
- disagreement categories: none

A disagreement is a review signal, not a verdict about the evaluator. It goes to root-cause analysis and a benchmark case — never straight into the scoring rules.

## Dataset

3 completed interview(s). Full rows in the JSON beside this file.

| session | persona | score | coverage | recommendation | evidence | latency (s) | cost | review |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `2d3d565d` | strong | 111/150 (74.0%) | 100.0% | Proceed to next round | 34 | 44.89 | $0.01792 | — |
| `6b1092b5` | thin | 8/25 (32.0%) | 16.7% | Needs further evaluation | 6 | 25.33 | $0.01344 | — |
| `68ee68f5` | messy | 70/150 (46.7%) | 100.0% | Not suitable for this role | 7 | 25.4 | $0.01273 | — |
