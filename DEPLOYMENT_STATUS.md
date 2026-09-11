# Deployment status

The state of Tara's deployment readiness, area by area, with the evidence for
each. Written to be checked rather than believed: every "PASS" names a command
whose output was read, and every limitation is stated in the same table as the
status it qualifies.

**Assessed against:** the repository as it stands, on a developer machine.
**Not assessed against:** any deployed environment. None exists.

Categories used exactly as defined:

| Category | Meaning |
| --- | --- |
| **PASS** | works, verified, no material caveat |
| **PASS WITH LIMITATIONS** | works, verified, with a caveat that changes how it may be operated |
| **BLOCKED** | cannot be verified because of an external dependency |
| **FAIL** | does not work, or does not exist where it needs to |

---

## Summary

| Area | Status | Evidence | Limitation |
| --- | --- | --- | --- |
| Auth | PASS | Prompt 20 gate; 95 tests in `tests/test_security.py`; 81 routes classified, 0 matrix discrepancies | — |
| Data lifecycle | PASS | Prompt 21 gate; 43 tests in `tests/test_data_lifecycle.py` | Nothing schedules the sweep |
| Backend build | PASS WITH LIMITATIONS | `Dockerfile` multi-stage; 1148 tests; app boots and serves locally | **Image never built** — Docker is not installed on this host, so the build is unverified |
| Frontend build | PASS | `npm run build` ✅; `npx tsc -b apps/recruiter apps/candidate` ✅; 87 vitest; no `localhost`/`:5173`/`:5174` in any bundle | — |
| Configuration | PASS | 16 checks in `require_production_configuration()`, each with its own test; refuses `*` CORS, insecure cookies, missing key, unresolved model slot, non-HTTPS provider URL, ephemeral data dir, inconsistent retention, mock provider, leftover bootstrap password, DEBUG logging | Validation is offline by design — it never confirms a model name is real |
| Health/readiness | PASS | `/api/health` and `/api/ready`; 8 tests including liveness surviving a dead provider and an unwritable data dir | Readiness does not test the provider or a database, deliberately |
| Persistence | PASS WITH LIMITATIONS | 1148 tests against the real stores; atomic writes plus per-file locks | **File-backed JSON, single host.** No horizontal scaling, no PITR, instances not interchangeable |
| Backup/restore | PASS WITH LIMITATIONS | `tools/backup.py`; 8 tests; real round trip — 176 files, 9.7 MB, created → verified → restored → read back | **Nothing schedules it**; archive is unencrypted and written locally; restore into a provisioned environment NOT VERIFIED |
| Observability | PASS | Request ids end to end, JSON logs, 8 stable error categories; 7 tests including "no credential or candidate content in a log line" | No metrics backend, no dashboards, no alerts |
| Evaluation execution | PASS WITH LIMITATIONS | Suite; `tools/evaluation_worker.py` with 3 tests | **Synchronous** — a slow model holds the request open. The worker is recovery, not a queue |
| AI provider | BLOCKED | `403 Key limit exceeded (total limit)`; budget $10.012 used of $10.00, remaining −$0.0121; live suite 4 failed / 3 passed | Provider budget. Not worked around |
| Deployment automation | FAIL | No pipeline, no deploy script, no registry, no platform config in the repository | Every step is manual |
| Rollback | PASS WITH LIMITATIONS | Procedure documented and reasoned; stores tolerate unknown fields on read | **Never executed.** Requires tagged images, which nothing builds. No schema version on the data directory |
| HTTPS / TLS | BLOCKED | Application speaks HTTP; terminates nothing | No environment to terminate TLS in |
| Migrations | n/a | `packages/types/schema.sql` exists; no code reads it | Nothing to migrate — there is no database |
| Deployed environment | FAIL | None provisioned | Nothing below the application layer has been verified in place |

---

## Verification run

Every command below was executed and its output read.

### Backend

```
python -m pytest -q                        1148 passed, 11 deselected   (random order)
python -m pytest -q                        1148 passed, 11 deselected   (random order, again)
python -m pytest -q -p no:randomly         1148 passed, 11 deselected   (alphabetical)
```

Three orderings, because the previous phase found a shared-singleton leak that
only appeared in one of them. Targeted:

```
tests/test_security.py                     95 passed
tests/test_data_lifecycle.py               43 passed
tests/test_deployment.py                   54 passed
tests/test_injection_defence.py            51 passed
test_injection_defence + test_lifecycle   134 passed   (the isolation regression)
```

### Frontend

```
npx tsc -b apps/recruiter                  PASS
npx tsc -b apps/candidate                  PASS
npm run -w @tara/recruiter test            87 passed (3 files)
npm run build                              PASS
```

### Static and configuration validation

```
python -m tools.secrets_audit              No findings
matrix.audit(app)                          81 routes, 0 discrepancies
python -m tools.retention_cleanup --status exit 0
python -m tools.evaluation_worker --status exit 1 — 7 failed, 1 running (from the exhausted-key runs)
python -m tools.backup --create …          176 files, 9.7 MB, ✓ verified
```

The worker's exit 1 is correct behaviour: it is reporting real failed
evaluations left behind when the provider budget ran out, which is exactly what
that exit code is for.

### Live provider

```
python -m tools.check_provider
  credentials        verified — present and used by the gateway
  balance            NOT verified — limit $10, used $10.012125504, remaining $-0.0121
  model availability verified — openai/gpt-4.1-mini is listed by the provider

python -m tools.check_provider --smoke
  structured output  not verified
  → 403 Key limit exceeded (total limit)

python -m pytest -q -m live                4 failed, 3 passed, 1144 deselected
```

```
LIVE PROVIDER VERIFICATION: BLOCKED — 403 Key limit exceeded
```

This is an external budget condition, not an application failure, and it has
not been worked around: no fallback provider, no model substitution, no relaxed
schema validation, no stubbed result. The three passing live tests are the ones
that check configuration rather than make a call.

**To unblock:** restore the OpenRouter account's budget, then
`python -m tools.check_provider --smoke` followed by `python -m pytest -q -m live`.

---

## Regression protection

The two previous gates are intact and were re-verified as part of this run, not
assumed.

### `PRODUCTION AUTHORIZATION PASSED` — holds

95 tests. Unchanged: central router-level guard, one ownership anchor,
non-disclosing 404s for cross-tenant reads, candidate session grants, the
machine-checked access matrix (which caught all five new routes added in this
phase before they could ship unclassified).

### `PRODUCTION DATA LIFECYCLE PASSED` — holds

43 tests. Every named control still enforced:

| Control | Still true |
| --- | --- |
| Server-side timestamps only | ✅ `anchor_time` reads `created_at`/`completed_at`/`revoked_at` |
| Centralized retention policy | ✅ one place in `config.py` |
| Lifecycle states | ✅ five, computed for eligibility, stored for terminal states |
| Complete graph traversal | ✅ evaluations → reviews → session → trail → lock → log → invitation |
| Evaluation snapshot deletion | ✅ the frozen transcript goes with the session |
| Audit redaction | ✅ rows kept, outcome fields replaced |
| Post-deletion byte verification | ✅ `verify()` re-reads every location |
| `DELETION_FAILED` on incomplete erasure | ✅ and it stays in the sweep |
| No false `200 deleted` | ✅ the API answers `409` with the locations named |

One addition strengthens it: `test_a_restored_snapshot_can_still_be_swept_for_retention`
restores a pre-erasure backup, confirms the candidate's data is back, and
confirms the sweep removes it again — so the erasure guarantee does not quietly
depend on nobody ever restoring anything.

---

## What would change these statuses

| To move | From | Needs |
| --- | --- | --- |
| Backend build | PASS WITH LIMITATIONS → PASS | A host with Docker; build the image and run the suite inside it |
| Persistence | PASS WITH LIMITATIONS → PASS | The Postgres migration. Explicitly out of scope for this phase |
| Backup/restore | PASS WITH LIMITATIONS → PASS | A schedule, an encrypted offsite destination, and one restore drill in a real environment |
| AI provider | BLOCKED → PASS | Provider budget restored |
| Deployment automation | FAIL → PASS | A pipeline that builds, tags, pushes and deploys |
| Rollback | PASS WITH LIMITATIONS → PASS | Tagged images in a registry, and one executed rollback drill |
| HTTPS | BLOCKED → PASS | A provisioned environment with TLS termination |
| Deployed environment | FAIL → PASS | Provision one |

---

## Gate

```
PRODUCTION DEPLOYMENT READY WITH LIMITATIONS
```

**Why not READY.** No environment has been deployed, nothing is automated, the
container image has never been built, HTTPS and process supervision are
unverified assumptions, and live provider verification is blocked. "Ready"
would claim things nobody has observed.

**Why not BLOCKED.** The rule is that a fundamental safety requirement being
*missing* forces BLOCKED. Working through them individually:

| Requirement | Present? |
| --- | --- |
| Persistent storage | ✅ required, documented, and startup **refuses** an ephemeral data directory |
| Backup and restore | ✅ implemented, tested, round-tripped against real data. Scheduling and offsite copy remain the operator's, and are named as such |
| Secrets | ✅ server-side only, audited by a tool that runs in the suite, and production refuses a leftover bootstrap password |
| Health checks | ✅ liveness and readiness, split correctly, 8 tests |
| Authentication and authorization | ✅ Prompt 20 gate, 95 tests |
| Data lifecycle | ✅ Prompt 21 gate, 43 tests |

None is missing. The durable-database gap is real and it constrains the *shape*
of a deployment rather than forbidding one:

```
single-host controlled deployment     viable, with the five safeguards below
multi-host / horizontally scaled      BLOCKED until durable shared persistence exists
```

**A single-host controlled deployment is viable only with all five of:**

1. An external persistent volume for `TARA_DATA_DIR`.
2. Exactly one application instance — no autoscaling, no two-instance rolling deploy.
3. A scheduled, encrypted, offsite backup, with one restore actually rehearsed.
4. Accepted downtime during deploys; candidates mid-interview will reconnect.
5. A bounded pilot size — concurrency is verified at 3 simultaneous interviews
   and uncharacterised above that.

Without all five, the honest status for that deployment is BLOCKED, and this
document should not be read as saying otherwise.
