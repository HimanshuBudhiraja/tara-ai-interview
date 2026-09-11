# Controlled pilot readiness

**Verdict: READY FOR CONTROLLED INTERNAL PILOT.**
Not ready for an external pilot. The blocker is access control and candidate-data
retention, not the assessment.

How the pilot is then run, measured and stopped is
[PILOT_PROTOCOL.md](PILOT_PROTOCOL.md); its numbers are
[PILOT_REPORT.md](PILOT_REPORT.md).

This document is the evidence behind that sentence. It was produced by testing
the whole lifecycle — recruiter → design → questions → publish → invitation →
candidate → interview → evaluation → result → recruiter — including what happens
when each stage fails. Nothing here is a claim about a stage that was not run.

---

## 1. What was verified, and how

| Layer | Evidence |
| --- | --- |
| Deterministic lifecycle | `tests/test_lifecycle.py` — 83 tests, no provider called |
| Whole backend | 820 passed, 11 deselected (`pytest -q`) |
| Recruiter console | 70 passed (`vitest run` in `apps/recruiter`) |
| Real provider | 7 passed (`pytest -m live`), plus 5 real interviews and 6 real evaluations through OpenRouter |
| Browser | recruiter report and session review driven in a real browser against the running API |

The lifecycle file runs the two models from their deterministic stubs and the
runtime brain offline, so a red run means the lifecycle broke rather than a model
having an opinion. The real-provider evidence is separate and is reported with
the numbers it actually produced.

---

## 2. Real runs

Five interviews were held end to end against the running server on the published
CSR screen (`iv_default` v1, medium, 8 questions), and evaluated through
OpenRouter on `openai/gpt-4.1-mini`.

| Run | Score | Rating | Coverage | Recommendation | Eval calls | Model latency | Model cost |
| --- | --- | --- | --- | --- | --- | --- | --- |
| strong | 109/150 (72.7%) | Good | 6/6 (100%) | Needs further evaluation | 14 | 142.1 s | $0.0174 |
| strong — forced re-run of the same snapshot | 111/150 (74.0%) | Good | 6/6 (100%) | **Proceed to next round** | 14 | 44.0 s | $0.0174 |
| strong — second candidate, same script | 110/150 (73.3%) | Good | 6/6 (100%) | Needs further evaluation | 14 | 44.6 s | $0.0174 |
| thin | 13/25 (52.0%) | Average | 1/6 (16.7%), 5 mentioned | Needs further evaluation | 14 | 24.9 s | $0.0134 |
| messy | 66/150 (44.0%) | Average | 6/6 (100%) | Not suitable for this role | 13 | 27.2 s | $0.0129 |
| hostile input | 22/50 (44.0%) | Average | 2/6 (33.3%) | Needs further evaluation | 4 | 8.9 s | $0.0039 |

One complete interview, measured end to end with per-session telemetry: **12
runtime model calls (19.4 s total, slowest 2.5 s, $0.0057) + 14 evaluation calls
(44.6 s, $0.0174) = $0.0231 of model spend.** Voice transport is not included.

Average evaluation model latency across the six runs: **48.6 s** (min 8.9 s, max
142.1 s). The 142 s run is one provider-side outlier: a single extraction call
took 101.8 s while that run's median call was 2.9 s. Nothing was retried and
nothing failed.

### The finding that shapes the recommendation

The forced re-run above read the **same frozen snapshot** with the **same engine
version** and the **same model**, and moved:

    Communication clarity · Depth   1 → 2
    total                          109 → 111   (1.3 points of 150)
    recommendation                 Needs further evaluation → Proceed to next round

The recommendation rule is deterministic; the model's criterion score is not. One
criterion moving by one point crossed the `any_severe` guard (`min(criteria) <= 1`
on any discussed skill), which is a step function, and the verdict flipped. The
scores were stable to ~1%. **The recommendation is not stable at the boundary.**

That is a limitation to run the pilot with, not a defect to paper over: the rule
was chosen deliberately, and softening it to make re-runs agree would be tuning a
hiring rule to a stability metric. What the pilot does instead is in §6.

---

## 3. Readiness matrix

| Area | Status | Evidence | Blocker? |
| --- | --- | --- | --- |
| Recruiter workflow | PASS | job → design → questions → publish → invitation exercised end to end in `test_lifecycle.py` and by hand | No |
| Question generation | PASS WITH LIMITATION | real generation is model-dependent; a total failure is a 502 with nothing saved and an audit line. Quality is Phase-12's benchmark, not this phase's | No |
| Publishing | PASS | idempotent, atomic, validated; a published row is never overwritten; a failed publish leaves no version | No |
| Candidate runtime | PASS | silence / repeat / clarify / skip / probe ladder / budget / pool exhaustion all bounded and terminating | No |
| Rejoin | PASS | same session, same item, no duplicate answer, no accidental advance; past the window the offer stops but the session survives | No |
| Evaluation | PASS | pending → running → completed \| failed; one logical evaluation per session under concurrency | No |
| Evidence integrity | PASS | every quote verbatim from the transcript; flagged turns cannot become evidence; quarantine keeps reasons, not text | No |
| Scoring | PASS | backend, API and browser agree exactly; the browser computes nothing | No |
| Coverage | PASS | discussed / mentioned / not-discussed reported separately from the score, never folded into it | No |
| Recommendation | PASS WITH LIMITATION | deterministic given the scores, but flips at the `any_severe` boundary when the model moves one criterion by one point (§2) | No — pilot constraint |
| Result API | PASS | one request; a self-contradicting result is a 409, never a page | No |
| Recruiter report | PASS | numbers rendered are byte-identical to the persisted result | No |
| Snapshot immutability | PASS | draft edited every way a recruiter can, mid-interview: candidate, evaluation and report all stayed on v1 | No |
| Idempotency | PASS | turn ids applied once; completion once; evaluation requests resolve to one record; result reads byte-identical | No |
| Failure recovery | PASS | eight injected failures; none produced a partial `completed` (§4) | No |
| Security | **PASS WITH LIMITATION** | no secrets in source, logs, data or bundles; no XSS; injection flagged and excluded — **but the recruiter API has no authentication** | **Yes, for external candidates** |
| Privacy | **PASS WITH LIMITATION** | transcripts, evidence and snapshots are stored in plain JSON on disk with **no retention or erasure path** | **Yes, for external candidates** |
| Observability | PASS | every failure names interview, session, evaluation, stage, model, provider, attempt and duration; runtime telemetry is per session | No |
| Provider reliability | PASS WITH LIMITATION | no silent fallback; provider/model recorded on success and failure. One 102 s call in six runs — the provider is a single point of failure with no second route | No |
| Cost visibility | PASS | tokens, calls and latency per evaluation on the record and the wire; $0.023 per interview measured | No |

---

## 4. Injected failures

Each was forced at the seam where it would really happen.

| Stage | Injected | Result |
| --- | --- | --- |
| Question generation | every slot unusable | 502, retryable, nothing saved, `QUESTION_GENERATION_FAILED` on the trail, interview still publishable after a retry |
| Publish | definition with no questions | refused, no version minted, invitations refused too |
| Candidate answer write | `OSError` inside the turn | the socket asks the candidate again; the session file is byte-identical; `turn_failed` on the trail |
| Evaluation queue | snapshot build raises | the interview still ends cleanly; no record; `evaluation_request_failed`; the recruiter can request it later |
| Evidence extraction | extractor raises | `failed`, `error_kind=model`, `failed_stage=evidence_extraction`, no result |
| Skill assessment | judge raises | `failed`, `error_kind=model`, `failed_stage=skill_assessment` |
| Integrity gate | violation raised | `failed`, `error_kind=validation`, `failed_stage=integrity_gate` |
| Result assembly | result contradicts itself | `failed`, `error_kind=validation`, `failed_stage=result_assembly` — **the record never reaches `completed`** |
| Result persistence | write fails after the model ran | loud failure; the record is not left `completed`; the report is a 409 |
| Provider absent | no key and no stub | `failed` with `error_kind=model`. Never a quiet score |

A failed record carries no result payload at all, so nothing that reads
`record.result` can show a refused evaluation.

---

## 5. Security and privacy

**Verified**

* No key-shaped literal anywhere in `services`, `packages`, `apps/*/src`, `evals`,
  `tools` or `content`; the only one is a fake in a test.
* `OPENROUTER_API_KEY` is read in `services/config.py` and used in exactly one
  place, `gateway._call`. Neither built bundle contains it.
* No whole invitation token appears in any audit file (2,155 invitations checked).
* No candidate answer text on the product log or the session decision trail.
* Candidate surfaces carry no score, recommendation, rating, expected signal or
  evaluation criterion.
* Hostile transcript through the real pipeline: `<script>` and `onerror=` render
  as visible text, nothing executed, no element created, a planted
  `{"total_score":125}` reached no score, and the injection turn was flagged
  (`override attempt`), frozen as flagged in the snapshot, and produced no
  evidence.
* No `dangerouslySetInnerHTML` in either app.
* A session id from another interview is refused (403); an unknown one is a 404.

**Not solved**

* **The recruiter API has no authentication.** Anyone who can reach the port can
  read every transcript and every assessment. `RECRUITER_AUTH_REQUIRED=true`
  closes the namespace with a 503 — it is a stop, not a login. The console shows a
  banner saying so.
* **No retention or erasure path.** Transcripts, evidence, snapshots and audit
  trails are plain JSON on disk and are never expired, anonymised or deleted.
  There is a session-delete primitive and no product surface for it.
* The invitation token is stored inside the session file, so filesystem access is
  equivalent to holding the candidate's credential.
* No compliance claim is made. Nothing here has been assessed against GDPR, SOC 2
  or ISO.

---

## 6. The pilot

Deliberately narrow, and shaped by §2 and §5.

    Who              iMocha employees only, interviewing as themselves or from a
                     script. No external candidates.
    Interviews       One published version at a time, on the CSR screen or one
                     internally authored role. Short or medium scope.
    Concurrency      At most 3 candidates at once, single process, one host.
    Models           openai/gpt-4.1-mini for every workload, as configured. No
                     model change mid-pilot: a change makes runs incomparable.
    Deployment       Localhost or a private network. If it is reachable from
                     anywhere else, set RECRUITER_AUTH_REQUIRED=true first.
    Consent          Participants are told the interview is recorded, evaluated
                     by a model, stored indefinitely and readable by anyone with
                     network access to the console.

**How the recommendation is used.** As one input, shown next to the score, the
coverage and the evidence — never on its own, and never as an automated decision.
A recruiter reads the skill rows and the quotes. Nobody is rejected on the
strength of the recommendation string.

**Do not re-run an evaluation casually.** A re-run can produce a different
recommendation from the same transcript (§2). Both attempts are kept and visible;
if a re-run is needed, both go in the file.

**Monitoring, daily**

* `data/evaluations/*.json` — anything `failed`: read `failed_stage` and `error`.
* Anything `running` for more than 15 minutes. A new request past that ceiling
  supersedes it and retries, so this is recoverable, but a recurrence means the
  process is dying mid-run.
* `EVALUATION_COMPLETED` lines: `duration_ms` and `model_latency_ms`. A jump means
  the provider, not the code.
* Per-interview model spend: about $0.023 for a text interview. A large deviation
  is worth understanding before the next batch.
* Any `candidate_turn_flagged` line — a real candidate tripping the injection
  scanner is worth a human reading the turn.

**Stop the pilot if** two evaluations disagree materially on the same transcript,
any recruiter-visible score cannot be traced to a quote, an evaluation reaches
`completed` with an unreadable report, a candidate loses answered work, or the
console is reachable from outside the private network without auth.

---

## 7. Rollback

There is no deployment platform here, and none was invented. The rollback is
practical for what this repository actually is.

1. **Stop new candidates.** Revoke the outstanding invitations
   (`DELETE /api/recruiter/interviews/{id}/invitations/{token}`) or turn off the
   open link.
   Sessions already under way are deliberately not killed — pulling the interview
   out from under someone mid-answer is worse than letting it finish.
2. **Stop the evaluator without stopping the interviews.** Unset
   `OPENROUTER_API_KEY` and restart. Interviews keep running on the heuristic
   classifier; evaluations fail loudly with `error_kind=model` and can be re-run
   later from their frozen snapshots. Nothing invents a score.
3. **Close the console.** `RECRUITER_AUTH_REQUIRED=true` and restart: the whole
   recruiter namespace answers 503.
4. **Quarantine the results.** Every evaluation is a file in `data/evaluations/`.
   Move them aside; the snapshots that produced them go with them, so any
   decision can be reconstructed or re-run later.
5. **Roll the code back.** The product is a Python package plus two Vite apps
   with no migrations: check out the previous revision and restart. `data/` is
   forward-compatible — unknown fields are ignored on read.
6. **If a score was wrong.** Do not edit the record. Force a re-evaluation; the
   old one is superseded, kept, and visible in the history, which is what a
   hiring file has to be able to show.

---

## 8. Known limitations carried into the pilot

* Recommendation instability at the `any_severe` boundary (§2).
* Model calibration: the Phase-12 gold benchmark still shows 4–8 material errors
  across identical runs. That is the evaluator's judgement quality, unchanged by
  this phase.
* An improvement bullet quotes the model's own remark, which can be positively
  worded for a below-bar skill. It now leads with the measured score so the label
  cannot be contradicted by the sentence, but the sentence is still the model's.
* One process, one host. The per-session evaluation lock covers threads and
  processes on one machine, not a second host.
* A recruiter clicking "evaluate" while an evaluation is running waits for it
  rather than starting a second one — correct, but it can be a slow request.
* An answer spoken while the socket is reconnecting is dropped, not duplicated;
  the candidate is asked again.
* The legacy cue-coverage scorer still 500s for generated interviews —
  re-confirmed today: `KeyError: 'q_c9e14a6959b0'`, because it looks a generated
  question id up in the authored pool. Pre-existing, untouched by this phase, and
  the console's Cue-coverage tab explains itself rather than spinning. The
  evidence-based evaluation, which is what a recruiter reads, is unaffected.
