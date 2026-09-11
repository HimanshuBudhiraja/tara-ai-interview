# Pilot protocol

How the controlled internal pilot is run, measured, reviewed and stopped.

The pilot's job is **not** to show that Tara works. It is to find where Tara is
wrong, unstable, slow, expensive or unsafe while the cost of finding out is
three colleagues and a private network. Every number below exists because a
specific decision depends on it; anything that could not actually be collected
is named as not collected rather than estimated.

Pilot constraints are unchanged from [PILOT_READINESS.md](PILOT_READINESS.md) §6:
iMocha employees only, one published interview configuration, one configured
model, private network, at most three concurrent candidates, and the
recommendation never used on its own.

---

## 1. What the pilot can measure, and what it cannot

Everything in the scorecard is derived from records the product already writes.
Nothing here added an event stream of its own, because a metric with its own
pipeline is a metric that will eventually disagree with the product.

| Source | What it answers |
| --- | --- |
| `data/sessions/*.json` | who sat what, which version, how long, how many questions, the transcript |
| `data/audit/<session>.jsonl` | question selection, every classification, silences, repeats, clarifications, skips, injection flags, and every model call with its latency and tokens |
| `data/audit/_product.jsonl` | interviews created / generated / published, invitations, sessions created, device checks, and the whole evaluation lifecycle |
| `data/evaluations/*.json` | the frozen snapshot, scores, coverage, evidence, quarantine, repairs, model provenance, per-stage timings |
| `data/pilot_runs.json` | which batch a session belongs to |
| `data/pilot_reviews/*.json` | what a human thought of a completed assessment |

**Added in this phase, because the funnel could not otherwise be closed:**

* `POST /api/invite/{token}/precheck` — the candidate app reports how the device
  check went (`ready` / `text` / `failed`). Advisory only: it gates nothing, the
  server never trusts it, and it carries no device identifiers.
* `pilot_run_id` on the session and the evaluation record.
* Per-stage evaluation timings (`model_meta.stage_ms`).
* Runtime model telemetry on the candidate's own trail rather than `_system`.

**Still not measurable, and not pretended otherwise:**

* Anything about a candidate who never opened the link. The invitation records
  `opened_at`, so "opened and never started" is visible; "never opened" is not
  distinguishable from "never delivered", because delivery is not implemented.
* Voice quality, audio dropouts and TTS/STT failures. The browser owns the voice
  channel and reports only the pre-interview check outcome.
* Whether a recruiter's disagreement was right. Captured, counted, never scored.

---

## 2. The scorecard

`GET /api/recruiter/pilot/summary` returns all of it; `python -m tools.pilot_report`
writes it to `PILOT_REPORT.md` plus machine-readable JSON.

### Candidate experience

| Metric | How it is derived |
| --- | --- |
| interviews attempted / completed / completion rate | sessions, and `phase == "complete"` |
| drop-off rate | started and not completed (a session inside its rejoin window may still return) |
| device-check reports, failures, chose-text | `SYSTEM_CHECK_REPORTED` events |
| rejoin frequency | `resumed` events per session |
| repeat / clarify / skip / silence frequency | `repeated`, `clarified`, `item_skipped`, `silence` events, reported per answered question as well as absolute |
| average interview duration | `completed_at − created_at`, mean / median / max |
| unexpected runtime errors | `turn_failed`, `llm_read_failed`, `probe_generation_failed` |
| flagged turns | `candidate_turn_flagged` — an injection attempt by a real candidate is worth a human reading the turn |

### Evaluation quality

| Metric | How it is derived |
| --- | --- |
| evaluation completion / failure rate | evaluation records by status |
| failures by stage and kind | `failed_stage` × `error_kind` — extraction, assessment, integrity gate, result assembly |
| evidence validation failures | quarantined items per evaluation, and the share of evaluations with any |
| evidence traceability | every persisted quote re-checked against its own frozen transcript **using the gate's own `quote_is_real`** |
| evidence volume | items per evaluation, and evaluations with none at all |
| repairs | criteria the code corrected without guessing |
| coverage and score distributions | mean / median / max of both, kept apart |
| recommendation distribution | counts per allowed recommendation |
| recommendation instability | repeated evaluations of one frozen snapshot that disagree (§4 below) |
| calibration disagreement | not in this scorecard: it is the gold benchmark's number, in [EVALUATOR_CALIBRATION.md](EVALUATOR_CALIBRATION.md), and re-running it is a separate, deliberate act |

### Recruiter usefulness

Only what the product can actually observe:

| Metric | How it is derived |
| --- | --- |
| report load success | a completed evaluation whose result assembles and validates; a contradiction is a 409, and `completed` now means the report was built before the record was written |
| report completeness | the result contract's own validation: every skill the version listed, every question, every turn, every quote resolvable |
| evidence / skill / coverage / recommendation usefulness | **from reviewers, not inferred.** A reviewer records agree / disagree / needs review, and a disagreement names which part: `wrong_evidence`, `wrong_skill`, `wrong_score`, `wrong_recommendation`, `insufficient_coverage`, `other` |
| recruiter override | there is none by design. A reviewer may record the recommendation they would have made; it is stored beside Tara's and never over it |

---

## 3. Minimum telemetry per completed interview

One row in `GET /api/recruiter/pilot/dataset` answers all of it:

    pilot_run_id · session_id · evaluation_id · interview_id · interview_version
    engine_version · snapshot_checksum · attempt · superseded
    provider · model · completed_at · evaluation_latency_sec · stage_sec
    model_calls · prompt_tokens · completion_tokens · cost_usd
    score / max_score / percentage / rating
    coverage_percentage · skills_discussed · skills_total
    recommendation · evidence_count · quarantined_count · repairs_count
    validation_errors · persona · channel
    human_review · human_disagreement_reason · human_recommendation

What is **not** in it: the candidate's name, the invitation token, any quote, any
answer, any remark. The session id is the key back into the full record for
whoever is entitled to read it.

---

## 4. Recommendation instability

**Measured, on this build.** Five forced re-evaluations of one frozen snapshot —
same published version, same transcript, same `deep_evidence_v2`, same
`openai/gpt-4.1-mini`:

| Run | Score | Rating | Severe flag | Recommendation |
| --- | --- | --- | --- | --- |
| 1 | 112/150 (74.7%) | Good | false | Proceed to next round |
| 2 | 114/150 (76.0%) | Good | **true** (Communication clarity) | Needs further evaluation |
| 3 | 112/150 (74.7%) | Good | false | Proceed to next round |
| 4 | 115/150 (76.7%) | Good | **true** (Communication clarity) | Needs further evaluation |
| 5 | 115/150 (76.7%) | Good | **true** (Communication clarity) | Needs further evaluation |

Ten criteria moved across those runs, every one of them by exactly one point.
Only one mattered: `Communication clarity · Depth` sat at 2 twice and 1 three
times, and `any_severe` is a step at `min(criteria) <= 1`. Discussion status did
not move at all; `depth_demonstrated` moved on one skill; evidence count moved
30 → 31.

**Root cause, classified:** model criterion variance of ±1 — which the benchmark
already treats as within tolerance — amplified by a deterministic step threshold
with no tolerance, applied to the noisiest statistic available (the minimum of
five subjective judgements). Not a code defect, not evidence fabrication, and
not a scoring-aggregation error: the totals were stable to 2 points in 150.

**Classification rule, now implemented rather than argued about.** A
recommendation that differs on identical frozen input is:

* `boundary_instability` — the score spread is ≤ 2 percentage points, so the
  threshold moved, not the reading;
* `scoring_variance` — a larger spread, meaning the two runs genuinely read the
  interview differently.

`GET /api/recruiter/pilot/stability` reports both, and the pilot's alert on any
flip has a proposed threshold of zero.

**What was done about it: disclosure, not smoothing.** The result now carries
`summary.recommendation_boundary`, derived beside the rules in
`evaluator.boundary_of`, which walks the same branches in the same order so a
threshold is only flagged when it is the one the recommendation actually turned
on. The recruiter report says, in the recommendation block: *this recommendation
is close to a threshold*, names the skill, and points the reader at the score,
the coverage and the evidence. On the five runs above it fires every time and
names Communication clarity; on the thin and messy candidates — whose verdicts
came from the coverage gate and from a majority below the bar — it stays silent.

**Proposed mitigations, none implemented.** Each would change who gets
recommended, so each needs the benchmark and a regression run first (§7):

1. **Corroborated severity** — require two criteria at or below the severe mark,
   or the same criterion severe on two skills. Smallest change; would have made
   all five runs "Proceed", which is a real hiring-policy change, not a bug fix.
2. **Severity on a less noisy statistic** — e.g. a skill score at or below 8/25
   rather than any single criterion at 1. Stabler, but a single catastrophic
   criterion would stop blocking.
3. **Stronger calibration** — reduce the ±1 criterion variance at source. The
   most valuable and the most work; the gold benchmark is the instrument.
4. **Coverage-weighted confidence** — already partly present: the coverage gate
   fires before any of this.

Recommendation for the pilot: run with option 0 — disclosure — and collect
reviewer verdicts on the boundary cases. A rule change made on one interview's
evidence is exactly the overfitting §13 forbids.

---

## 5. The recruiter review protocol

For every pilot assessment, in this order. It is deliberately about what is on
the screen: no hidden reasoning, no prompts and no model deliberation are shown
to anyone, so nothing in this checklist depends on them.

**Evidence**

1. Read the quotes before the scores. Does each one say what the candidate
   actually said, in their words?
2. Is the quote relevant to the skill it is filed under?
3. Is it attributed to the right skill and the right question?

**Scoring**

4. Are the five criteria plausible for the evidence shown — not "would I have
   given the same number", but "could a reasonable reviewer defend this"?
5. Is the expectation right for the level? A senior answer and a junior answer
   should not score the same.
6. Does the score reflect what was *demonstrated*, rather than what was claimed?

**Coverage**

7. Were the skills that matter for the role actually assessed?
8. Is low coverage obvious on the page, or did you have to look for it?

**Recommendation**

9. Is it justified by the score, the coverage and the evidence together?
10. Would you have reached the same decision yourself? If not, say what you
    would have said instead.
11. If the report says the recommendation is close to a threshold, treat the
    word as advisory and answer 9 and 10 from the evidence.

Then record the verdict: **Pilot review** at the foot of the report, or
`POST /api/recruiter/sessions/{id}/review`. A disagreement must carry at least
one category — a disagreement nobody can categorise is one nobody can act on.

---

## 6. What happens to a disagreement

    Pilot observation → review → root-cause analysis → controlled change
                     → benchmark → regression → pilot comparison

Reviewer feedback **never** changes scoring, the recommendation rules, a prompt,
the skill mapping or the question pool on its own. There is no code path from a
review to the evaluator, and `test_a_review_cannot_change_the_assessment` holds
that line: the evaluation record and the served result are byte-identical before
and after a disagreement is recorded.

What a disagreement produces is a candidate benchmark case. `wrong_evidence` and
`wrong_skill` point at extraction; `wrong_score` at the judge or the
expectations; `wrong_recommendation` at the rules or the boundary; and
`insufficient_coverage` at question selection rather than the evaluator at all.
Human disagreement is a review signal, not proof the evaluator is wrong — two
recruiters disagree with each other, too.

---

## 7. Monitoring and alerts

`python -m tools.pilot_report` after each batch. The report lists every proposed
threshold that was crossed; there is no paging system, because the pilot is three
candidates on one host and the person who would be paged is the person running it.

**Proposed thresholds** (`services/pilot/metrics.THRESHOLDS`). Every one is a
guess chosen to be obviously wrong rather than subtly wrong, and the pilot's own
data is what should replace them.

| Alert | Kind | Proposed | Why this number |
| --- | --- | --- | --- |
| `evaluation_failure_rate` | technical | > 10% | baseline and simulation both ran at 0% |
| `interview_completion_rate` | technical | < 80% | six of six scripted interviews completed |
| `evaluation_latency` | technical | p95 > 120 s | slowest real evaluation measured is 49.8 s |
| `provider_error_rate` | technical | > 5% | measured 0% across 13 real evaluations |
| `runtime_errors` | technical | any | none observed |
| `untraceable_evidence` | quality | any | this is the fabrication check and a stop condition |
| `evidence_quarantine_rate` | quality | > 10% | 0% in both runs |
| `zero_evidence_rate` | quality | > 10% | every completed evaluation produced evidence |
| `recommendation_instability` | quality | any flip | one flip observed and understood (§4) |
| `reviewer_disagreement_rate` | quality | > 30% | no human data yet — this one is a placeholder and should be set from the first ten reviews |

---

## 8. Cost and latency

Measured, not extrapolated. Thirteen real evaluations and six real interviews on
`openai/gpt-4.1-mini` through OpenRouter.

| Measure | Baseline (sequential) | Simulation (3 concurrent) |
| --- | --- | --- |
| evaluation latency mean / max | 42.4 s / 49.8 s | 31.9 s / 44.9 s |
| model calls per evaluation | 13.9 | 13.7 |
| evaluation cost | $0.016 | $0.015 |
| runtime latency per interview | 47.8 s | 46.9 s |
| whole interview, model spend | **$0.029** | **$0.028** |
| slowest single call | 5,975 ms | 6,134 ms |
| provider errors | 0 | 0 |

**Where the time goes.** Evidence extraction dominates: 27.2 s of the mean 42.4 s
baseline evaluation, and 17.9 s of 31.9 s under concurrency. Skill judgement is
14–15 s. Result assembly is 8–13 **milliseconds** — the assembly and validation
layer is free. Latency is a function of the number of provider calls (one
extraction per answered question, one judgement per skill), not of application
code, so the only real lever is fewer or cheaper calls. Nothing is being
optimised on this evidence.

p95 is withheld below twenty runs, and `_stats` enforces that rather than
leaving it to whoever writes the summary. The 102-second single call seen in the
previous phase did **not** reproduce in thirteen runs; it stands as one
provider-side outlier, not a property of the system.

---

## 9. Stop conditions

**Pause immediately, no discussion** — any one of these:

* a persisted quote that is not in its own frozen transcript (fabricated
  evidence). `untraceable_evidence` in the pilot report, and the check runs the
  gate's own rule;
* any candidate's data appearing in another candidate's session, result or
  report;
* candidate state corruption: a lost answer, a duplicated turn, or a session
  advancing without an answer;
* a candidate receiving anything other than their pinned published version, or a
  published version changing;
* an evaluation whose persisted result contradicts itself, or a `completed`
  record whose report cannot be built;
* any security issue: the console reachable off the private network without
  auth, a secret in a log or a bundle, an invitation token in plaintext where it
  should not be.

**Review thresholds** — pause and analyse, do not necessarily stop. All
**proposed**, and to be reset from the pilot's own numbers:

| Condition | Proposed threshold |
| --- | --- |
| evaluator disagreement | > 30% of reviewed assessments, or any two reviewers disagreeing with the same assessment for the same reason |
| recommendation instability | any flip on identical input that is **not** classified `boundary_instability`, or more than one boundary flip in five re-runs |
| evaluation failure rate | > 10% of runs, or two consecutive failures at the same stage |
| candidate completion failure | < 80% of started interviews, or any interview that cannot be completed twice |

---

## 10. Rollback, verified

The plan is [PILOT_READINESS.md](PILOT_READINESS.md) §7. It was executed against
the running server rather than described:

| Step | Result |
| --- | --- |
| Revoke an outstanding invitation | invite 200 → 410; session start 410 |
| Completed results survive | all three simulation results still 200 |
| Stop the pilot run | `stopped_at` set, `active: false`, new sessions attributed to no run |
| Disable evaluation, keep interviewing | with no provider: interview completed in 16 turns on the heuristic classifier; evaluation `failed / model / "no provider configured"`; result 409. Nothing fabricated, nothing partial |
| Close the console | `RECRUITER_AUTH_REQUIRED=true` → recruiter API 503 while the candidate invite path stayed 200 |
| Audit retained | 4,283 → 4,311 lines, append-only, nothing rewritten |

No pilot data is deleted by any step. A wrong score is corrected by a forced
re-evaluation, which supersedes and keeps the old record.

---

## 11. Single host, single process

Unchanged in this phase, and now written down precisely.

**Process-local state** — rebuildable, lost on restart, correct only because
there is one process:

* the orchestrator's per-version pool cache and the authored-pool cache;
* the gateway and runtime-brain singletons, and the HTTP client inside them;
* the per-session evaluation locks in `jobs._LOCKS`;
* the file-store instance in `sessions._STORE`;
* the per-file locks in `jsonfile._LOCKS`.

**Durable state** — everything a decision depends on: sessions, evaluations with
their frozen snapshots, published versions, interviews, invitations, audit
trails, pilot runs and reviews.

**What breaks with two hosts:**

1. **Duplicate evaluations and double spend.** `jobs` serialises per session with
   an in-process lock plus `flock` on a shared path. Two hosts do not share that
   path, and `flock` over NFS is not dependable, so both would create a record
   and both would call the model.
2. **Lost writes on the whole-file stores.** `interviews.json`, `invites.json`
   and `pilot_runs.json` are read-modify-write over an entire file. Within one
   process that is now serialised by `jsonfile.guarded` — added in this phase
   after noticing that three concurrent interviews each update their invitation
   twice. Across processes it is last-writer-wins, and a lost invitation row is a
   candidate who cannot sit their interview.
3. **Interleaved turns.** Session files are written atomically per turn, so a
   torn file is impossible, but two hosts advancing one session would drop a
   turn (last write wins).
4. **A split audit trail.** Two hosts on separate disks produce two partial
   trails for one interview.

**Why the pilot is safe as restricted:** one uvicorn process on one host; each
candidate's turn loop touches only their own session file; evaluations are
serialised per session; the whole-file stores are now serialised in-process; and
three concurrent candidates were run end to end with no cross-contamination, no
duplicate evaluation, no lost invitation and no state collision.

**Backlog, in order:** a durable store (Postgres) for the whole-file
repositories → Redis or a database advisory lock for the evaluation lock → a
worker process draining `run_pending` → then, and only then, more than one host.

---

## 12. The legacy scorer, for reviewers

`GET /api/recruiter/sessions/{id}/score` is the **old cue-coverage scorer**, kept
alive for the authored CSR pool. It returns 500 for interviews whose questions
were generated, because it looks a generated question id up in the authored pool
(`KeyError`). Re-confirmed today:

| Session | Interview | legacy `/score` | canonical `/evaluation` | canonical `/evaluation/result` |
| --- | --- | --- | --- | --- |
| pilot simulation | `iv_default` (authored) | 200 | 200 | 200 |
| earlier generated interview | `iv_b4a95d973b` (generated) | **500** | 404 (never requested) | 404 |

**It is not the evaluator.** The canonical, evidence-based path — everything in
this document — is a different subsystem with a different audit vocabulary
(`EVALUATION_*` rather than `SCORE_GENERATED`). A reviewer who sees the
Cue-coverage tab fail should read the Evaluation tab, which is the assessment.
Not fixed, because it does not affect the pilot's interview.
