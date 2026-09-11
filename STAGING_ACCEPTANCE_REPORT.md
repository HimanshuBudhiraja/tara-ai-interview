# Staging acceptance report

**Date:** 2026-09-10
**Objective:** provision a real staging environment and perform the first actual deployment.
**Outcome:** no staging environment could be provisioned. The deployment was not performed.

```
FINAL GATE: STAGING DEPLOYMENT BLOCKED
```

This report says what was inspected, what was built, what was verified, and —
at length, because it is the point — what was *not*. No row below is marked
`PASS` on the strength of a local test.

---

## 1. Why it is blocked

A staging deployment needs three things this machine does not have: somewhere to
run, a way to build, and a funded account to pay for either.

| Prerequisite | State | Detail |
| --- | --- | --- |
| **Container runtime** | absent | `docker`, `podman`, `nerdctl`, `colima`, `lima`, `finch` — none installed; no docker socket. The image cannot be built here. (`brew` is present and `kern.hv_support=1`, so one *could* be installed.) |
| **Funded cloud project** | absent | Google Cloud is authenticated with two projects. **`billingEnabled: false` on both**, and **all three billing accounts report `OPEN: False`.** Cloud Build, Artifact Registry, Cloud Run and Compute Engine are therefore all unavailable. |
| **A host** | absent | No `~/.ssh/config`, two `known_hosts` entries. No pre-existing staging box. |
| **HTTPS endpoint** | absent | Follows from having no host. |
| **AI provider budget** | exhausted | `403 Key limit exceeded (total limit)` — $10.012 used of a $10.00 limit, remaining **−$0.0121**. |

`vercel` is authenticated  and is **not** a viable target
for this application: the backend is a stateful FastAPI process that needs a
persistent volume, a long-lived WebSocket for the interview turn loop, and a
request budget long enough for a synchronous multi-minute evaluation. Vercel
provides none of those. It could host the static bundles, but the API already
serves them from the same origin, so that would deploy the half that is not the
problem.

**The blocking action is not mine.** Reopening a billing account and linking it
to a project is an account-owner decision with a cost attached. Nothing in this
phase was going to change that, and provisioning paid infrastructure without
being asked would have been the wrong call regardless.

---

## 2. Acceptance matrix

Assessed **for staging**. Every row is `BLOCKED` or `NOT TESTED` because there
is no deployment to assess. The right-hand column names what would unblock it.

| Area | Result | Evidence | Blocking issue |
| --- | --- | --- | --- |
| Infrastructure | BLOCKED | billing disabled on both GCP projects; all three billing accounts closed | link a funded billing account |
| Container | BLOCKED | no container runtime on this host; image never built | install a runtime, or use Cloud Build (needs billing) |
| Backend | NOT TESTED | never deployed | infrastructure |
| Frontend | NOT TESTED | never deployed; bundles build locally | infrastructure |
| HTTPS | BLOCKED | no endpoint to terminate TLS at | infrastructure |
| Persistence | NOT TESTED | no staging volume; restart-survival never run against one | infrastructure |
| Backup | NOT TESTED | `tools/backup.py` never run against a staging data directory | infrastructure |
| Restore | NOT TESTED | never restored into a staging target | infrastructure |
| Authentication | NOT TESTED | *for staging.* Exercised locally — see §4 | infrastructure |
| Authorization | NOT TESTED | *for staging.* Exercised locally — see §4 | infrastructure |
| Tenant isolation | NOT TESTED | *for staging.* Cross-tenant row needs a second staging tenant | infrastructure |
| Recruiter flow | BLOCKED | designing an interview needs the AI provider; budget exhausted — see §5 | provider budget |
| Candidate flow | NOT TESTED | *for staging.* Exercised locally end to end — see §4 | infrastructure |
| Rejoin | NOT TESTED | *for staging.* Exercised locally | infrastructure |
| Evaluation | BLOCKED | `403 Key limit exceeded`; fails cleanly without fabricating a result | provider budget |
| Report | BLOCKED | no readable result while evaluation is blocked | provider budget |
| Data erasure | NOT TESTED | *for staging.* Exercised locally — 5 locations cleared, verified empty | infrastructure |
| Observability | NOT TESTED | no deployed log stream to read | infrastructure |
| Rollback | NOT TESTED | needs two tagged images and an orchestrator | infrastructure |
| Security | NOT TESTED | *for staging.* Smoke suite ran locally — see §4 | infrastructure |
| AI provider | BLOCKED | budget exhausted, verified twice today | provider budget |

---

## 3. What was built

Since the deployment could not be performed, the work went into making it
executable and honest when it can be.

### `tools/staging_acceptance.py`

The acceptance matrix as a command. Twenty-one rows' worth of checks against a
deployed base URL, emitting JSON so the report is transcribed rather than
recalled.

```bash
python -m tools.staging_acceptance --base-url https://staging.example.com \
    --admin-email you@example.com --tenant-b-email admin@second-org.example \
    --out staging-acceptance.json
```

Two design decisions worth stating:

* **It refuses to run against localhost** unless `--allow-local` is passed, and
  every result from such a run is stamped `local`. A local pass is not a staging
  pass, and the difference is the entire subject of this phase.
* **It never claims a row it did not exercise.** No second tenant configured →
  `NOT TESTED`, not `PASS`. Provider budget exhausted → `BLOCKED`, not `FAIL`.
  Seven rows it structurally cannot see from outside — image digest, restart
  survival, backup, restore, rollback, log contents, infrastructure — report
  `NOT TESTED` with the command that would establish each.

It also supports the restart-persistence test as two phases (`--phase before`,
restart the service, `--phase after`), because a harness cannot restart the
thing it is testing.

### Provider failure classification — a real defect, found and fixed

The harness's first real run surfaced this: **every failure inside evidence
extraction or skill assessment was recorded as `error_kind: "model"`** — an
exhausted budget, a revoked key, a throttled account, a timeout, a network
failure, all of them. The gateway already distinguishes these (it raises
`ProviderUnavailable`, `RateLimited`, and carries a `CallStatus` with an
`is_infrastructure` property); the evaluation layer caught bare `Exception` and
threw the distinction away.

The operational consequence is precise: an operator paged at 3am, reading a wall
of `error_kind=model`, goes looking at prompts and calibration. The actual cause
was that the account was out of credit — a five-minute fix they never reach.

Fixed by adding `evaluations.PROVIDER_FAILURE` and classifying the exception at
the four failure sites. Three existing tests asserted `MODEL_FAILURE` for
conditions that are plainly the provider — one injects `"provider returned
402"` (Payment Required), one injects `AIError("no credit")`, one runs with no
provider configured at all. All three encoded the old behaviour; their
substance — *never becomes a completed evaluation*, *fails rather than scoring
zero*, *a visible failure not a quiet score* — is unchanged and still passes.

Ten new tests cover the classification, including that an exhausted budget
fails the evaluation with an empty result and an unreadable report rather than
fabricating one.

This is the only production-code change in this phase, and it is observability,
not behaviour: a failure is still a failure, still not fabricated, still
retryable. Only the label became accurate.

---

## 4. Local harness self-test — NOT staging evidence

The harness was validated against a local instance so that it is known to work
before it is pointed at a real deployment. **These results are stamped `local`
and are recorded here as evidence about the harness, not about staging.** Every
staging row above remains `NOT TESTED`.

```
Staging acceptance — http://localhost:8000
⚠  LOCAL TARGET: results are stamped `local` and are NOT staging evidence.

  BLOCKED: 3   FAIL: 1   NOT TESTED: 9   PASS: 17   PASS WITH LIMITATIONS: 1
  defects  P0: 0  P1: 2  P2: 1  P3: 0
```

What passed locally, and is therefore worth pointing at a deployment:

| Check | Local result |
| --- | --- |
| Liveness, readiness | PASS — readiness reports 6 checks and does not call the provider |
| Request correlation | PASS — a client-supplied id is echoed; one is minted when absent |
| Both bundles served | PASS — no `localhost`, `:5173`, `:5174` or mixed content |
| Public surface carries no secret | PASS — no key, hash, filesystem path or stack trace |
| Unauthenticated recruiter API | PASS — 5 paths, all 401 |
| Bad credentials | PASS — wrong password and unknown address produce identical 401s |
| Resource enumeration | PASS — every guessed id returns an identical 404 |
| Candidate journey | PASS — 15 turns covering substantive, thin, clarify, repeat, skip and silence; a probe was issued; no score, rating or recommendation in the candidate payload |
| Rejoin | PASS — a completed interview cannot be re-sat (410) |
| Candidate token isolation | PASS — A cannot read or write B's session with A's grant or A's token; no candidate credential reaches the recruiter API |
| Hostile candidate input | PASS — 5 payloads (XSS, prompt injection, SQL, null bytes, 20 KB) handled without a 5xx or a stack trace |
| Malformed API input | PASS — 4 malformed payloads rejected without leaking internals |
| Erasure requires a principal | PASS — unauthenticated erasure refused (401) |
| Erase → verify | PASS — 5 locations cleared, verified empty, every stale session URL 404 |

The one local `FAIL` is **"development seed is absent"** — the local target
serves the demo answer key, which is correct for `TARA_ENV=development` and is
gated off in production and staging. The check is right; the target is a
development environment.

The three local `BLOCKED` rows are the provider: designing an interview,
evaluating one, and reading the report.

### An operational hazard worth recording

Three separate times in this work, a **stale server process** was still serving
port 8000 with an older build — answering `404` for `/api/ready`, not echoing
request ids, and reporting the pre-fix `error_kind`. Each time it looked like an
application defect and was not. `/api/health` now reports `version`, and
comparing it against the deployed tag is the cheapest way to catch this. It is
in the runbook's smoke test as step 1 for exactly this reason.

---

## 5. The provider blocks more than evaluation

Verified today, and worth stating because it was not obvious: **the exhausted
key blocks the recruiter flow, not just the report.**

```
POST /api/recruiter/interviews/{id}/extract
→ 503 "Couldn't analyse the job description — the language model is
      unavailable. (403: Key limit exceeded (total limit))"
```

Question generation depends on the design, so it follows. Consequently, with no
provider budget:

* a recruiter **cannot design a new interview**;
* therefore cannot generate questions, publish, or invite against a new one;
* a candidate **can** still be invited to an **already-published** interview,
  and the whole candidate journey works — that path calls no model it cannot
  fall back from;
* evaluation and the report are blocked.

The failure itself is correct and is the behaviour this architecture is supposed
to have: a clean 503 with a readable message, no fallback provider, no
substituted model, no fabricated design. The harness classifies it `BLOCKED`
rather than `FAIL` for that reason.

---

## 6. Defects

| Severity | Count | Detail |
| --- | --- | --- |
| **P0** | 0 | No cross-tenant exposure, no candidate-data exposure, no authentication bypass, no false successful deletion. |
| **P1** | 1 | Provider-failure misclassification — **fixed in this phase.** Every provider failure was recorded as a model failure, sending an operator to the wrong place during an incident. |
| **P2** | 1 | Designing a new interview is provider-dependent with no degraded path. Not a defect to fix here — it is inherent to an AI interview designer — but it belongs in the runbook, because it means "the AI is down" costs more than "reports are delayed". |
| **P3** | 0 | — |

No P0 or P1 remains open.

---

## 7. What would unblock this

In order. The first is the only one that needs a decision rather than a command.

1. **Reopen a billing account and link it to a project.** Account-owner action,
   with a cost. Everything else follows from it.
2. **Enable the Compute Engine API** and provision the minimum envelope:
   one `e2-small`, a persistent disk for `TARA_DATA_DIR`, and a reverse proxy
   terminating TLS and forwarding WebSocket upgrades. A single VM with an
   attached disk — not Cloud Run — because the persistence layer needs
   `fcntl.flock` and atomic renames on a real filesystem, which GCS FUSE does
   not provide, and because exactly one instance must ever run.
3. **Build the image with Cloud Build** (`gcloud builds submit`), which removes
   the local-Docker problem entirely, and record the digest.
4. **Restore the OpenRouter budget.** Independent of the above, and required
   before the recruiter design flow, evaluation and the report can be verified
   at all.
5. **Run the harness** against the deployed URL, plus the three things it
   cannot do from outside: the two-phase restart test, a backup and restore on
   the host, and a rollback between two tagged images.

Estimated cost of (2) at pilot scale is a few dollars a month for the VM and
disk; Cloud Build has a free daily allowance. That is a statement about order of
magnitude, not a quote.

---

## 8. Regression state

Nothing in this phase weakened an existing control. Re-verified, not assumed:

| Suite | Result |
| --- | --- |
| Full suite, random order | **1159 passed**, 11 deselected |
| Full suite, alphabetical | **1159 passed**, 11 deselected |
| `tests/test_security.py` | 95 |
| `tests/test_data_lifecycle.py` | 43 |
| `tests/test_deployment.py` | 65 |
| Recruiter console | 87 |
| Typechecks, production build | PASS |
| `tools.secrets_audit` | no findings |
| Access matrix | 81 routes, 0 discrepancies |
| Live provider suite | **BLOCKED** — 4 failed on `403 Key limit exceeded` |

`PRODUCTION AUTHORIZATION PASSED` and `PRODUCTION DATA LIFECYCLE PASSED` both
still hold.
