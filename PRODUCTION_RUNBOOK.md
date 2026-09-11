# Production runbook

Operational procedures for running Tara. Commands are copied from the
repository and are real; where a step is not automated it says
**MANUAL STEP — NOT AUTOMATED** rather than implying a script exists.

Architecture and its limitations: [DEPLOYMENT.md](DEPLOYMENT.md).
Security model: [PRODUCTION_SECURITY_MATRIX.md](PRODUCTION_SECURITY_MATRIX.md).
Retention and erasure: [DATA_LIFECYCLE.md](DATA_LIFECYCLE.md).

Two facts that shape everything below:

* **All state is a directory of JSON files.** `TARA_DATA_DIR` is the database.
  Losing it loses everything; restoring it un-deletes everything.
* **One instance.** Not a preference — two instances on different hosts cannot
  see each other's sessions.

---

## Pre-deployment checklist

```text
[ ] Correct environment selected      TARA_ENV=production (or staging)
[ ] Secrets configured                OPENROUTER_API_KEY set; TARA_BOOTSTRAP_PASSWORD *unset* after first boot
[ ] AI provider budget verified       python -m tools.check_provider
[ ] Model slots verified              six slots resolve; startup refuses an empty one
[ ] Persistent volume verified        TARA_DATA_DIR on external storage, survives container replacement
[ ] Backup verified                   python -m tools.backup --create /backups --quiesced  (nothing schedules it)
[ ] Retention configuration verified  python -m tools.retention_cleanup --status
[ ] CORS verified                     ALLOWED_ORIGINS explicit; '*' is refused
[ ] HTTPS verified                    proxy terminates TLS and forwards WebSocket upgrades
[ ] Health endpoint verified          curl -fsS https://<host>/api/health
[ ] Readiness endpoint verified       curl -fsS https://<host>/api/ready
[ ] Frontend build verified           npm run build
[ ] Backend build verified            docker build -t tara:<version> .
[ ] Full suite green                  python -m pytest -q
[ ] Secrets audit clean               python -m tools.secrets_audit
```

The last four are the only ones this repository can verify for you.

---

## Deployment procedure

**MANUAL STEP — NOT AUTOMATED** applies to every step. There is no pipeline, no
deploy script and no platform configuration in this repository.

### 1. Verify locally before touching anything

```bash
python -m pytest -q
npx tsc -b apps/recruiter apps/candidate
npm run build
python -m tools.secrets_audit
```

All four must pass. The secrets audit reads the bundles you just built, so run
it after the build, not before.

### 2. Build the image

```bash
docker build -t tara:$(git rev-parse --short HEAD) .
```

Both frontends build inside the image, so there is no separate frontend deploy
and the bundles cannot drift from the API. Tag with something you can roll back
to — rollback is "run the previous tag", and it needs a previous tag to exist.

### 3. Confirm the volume before first boot

**MANUAL STEP — NOT AUTOMATED.** `TARA_DATA_DIR` must be an external persistent
volume. Startup refuses `/tmp`, `/var/tmp`, `/dev/shm` and `/run`, which catches
the obvious mistake but cannot detect every ephemeral disk.

If this is wrong, every interview, evaluation and audit record is destroyed on
the next deploy, and you find out afterwards.

### 4. Run

```bash
docker run -d --name tara \
  -p 8000:8000 \
  -v /srv/tara/data:/app/data \
  -e TARA_ENV=production \
  -e TARA_DATA_DIR=/app/data \
  -e TARA_VERSION=$(git rev-parse --short HEAD) \
  -e ALLOWED_ORIGINS=https://hire.example.com \
  -e RECRUITER_AUTH_REQUIRED=true \
  -e OPENROUTER_API_KEY=<from your secret store> \
  tara:<version>
```

Watch the startup log. It prints every production-configuration problem it
found, prefixed `[boot] ⚠ production configuration:`. **A deployment that
prints any of those is misconfigured** — readiness will answer `503` and it
should not receive traffic.

### 5. Create the first administrator

```bash
docker exec -it tara python -m tools.make_user --email you@example.com --admin
```

Reads the password from a prompt, never from the command line. With
`RECRUITER_AUTH_REQUIRED=true` and no account, the recruiter API answers `503`
by design — that is the intended state until this step runs.

### 6. Start the evaluation worker (recommended)

```bash
docker exec -d tara python -m tools.evaluation_worker --interval 10
```

Evaluation runs synchronously in the request; the worker recovers records left
`pending` by a crash or an interrupted request. Without it those records stay
pending forever and the recruiter waits for a report that never arrives.

### 7. Schedule the retention sweep

**MANUAL STEP — NOT AUTOMATED.** Nothing schedules it. Add a cron entry, a
systemd timer or the platform's scheduler, daily:

```bash
0 3 * * *  docker exec tara python -m tools.retention_cleanup
```

Exit `1` means at least one erasure failed. Alert on it.

### 8. Verify

```bash
curl -fsS https://<host>/api/health   # {"ok": true, ...}
curl -fsS https://<host>/api/ready    # "status": "ok"
```

Then the smoke test.

### 9. Enable traffic

**MANUAL STEP — NOT AUTOMATED.** The proxy's business. Confirm it forwards
WebSocket upgrades to `/ws/interview/*` before you do — an interview that
cannot open its socket looks like an application bug and is not one.

---

## Smoke test

Run in order. Stop at the first failure.

| # | Step | How | Expected |
| --- | --- | --- | --- |
| 1 | Backend health | `curl -fsS https://<host>/api/health` | `200`, `"ok": true`, **and `version` equal to the tag you just deployed** |
| 2 | Backend readiness | `curl -fsS https://<host>/api/ready` | `200`, `"status": "ok"`, every check `ok` |
| 3 | Frontend loads | open `https://<host>/recruiter` | the sign-in screen, no console errors |
| 4 | Recruiter authentication | sign in as the admin from step 5 | the console renders; the sidebar names your address and organization |
| 5 | Unauthenticated API is closed | `curl -o /dev/null -w '%{http_code}' https://<host>/api/recruiter/interviews` | `401` |
| 6 | Create / open interview | console → Create AI Interview → paste a JD → generate | skills, tasks and questions appear and persist on reload |
| 7 | Publish | Publish & invite | a version number; the draft can then change without changing it |
| 8 | Candidate invitation | create a candidate link | a 43-character token; the row appears under Candidates |
| 9 | Candidate entry | open the link in a clean browser | welcome and consent, correct role, **no** sign-in |
| 10 | Interview turn | answer one question | Tara replies; a follow-up or the next question |
| 11 | WebSocket | check the browser network panel | `/ws/interview/…` upgraded, not failing back |
| 12 | Completion | finish the interview | the completion screen; **no score, no recommendation** |
| 13 | Evaluation | console → the candidate → Evaluate | **BLOCKED — provider budget** |
| 14 | Recruiter report | open the report | **BLOCKED — provider budget** |

Steps 13 and 14 cannot pass while the provider returns `403 Key limit exceeded`.
Do not substitute a stub, and do not record them as passed. Steps 1–12 are
provider-independent and must all pass.

**Unblocking 13–14:** restore the OpenRouter account's budget, then
`python -m tools.check_provider --smoke`. When that reports a successful
structured call, re-run steps 13 and 14.

---

## Failure handling

### Backend unavailable

```bash
docker ps -a | grep tara            # is it running?
docker logs --tail 200 tara         # what did it say on the way down?
curl -fsS localhost:8000/api/health # is it the app or the proxy?
```

Health answers whenever the process runs, so a failing health check with a
running container means the process is wedged, not that a dependency is down.
Restart: `docker restart tara`. State is on the volume; in-flight candidates
reconnect (see *Candidate session interruption*).

### Frontend unavailable

The bundles are inside the image and served by the API. A failing frontend with
a healthy API means the proxy, not the build. Confirm with
`curl -fsS https://<host>/recruiter | head -c 200` — you should see HTML.

### AI provider unavailable

**More is affected than evaluation.** Designing a new interview calls the
model, so with no provider budget:

| Capability | Effect |
| --- | --- |
| Design a new interview (JD → skills/tasks) | **blocked** — `503`, readable message |
| Generate questions | **blocked** — depends on the design |
| Publish / invite against a NEW interview | blocked, by the above |
| Invite a candidate to an ALREADY-published interview | **works** |
| The whole candidate interview | **works** — authored probe banks and the deterministic classifier |
| Evaluation and the report | **blocked**, failing loudly |

So an outage costs more than delayed reports: no new roles can be set up. A
pilot with its interviews already published keeps running.

Failures are classified: `error_kind: "provider"` is the account or the network
— budget, key, throttle, timeout — and `"model"` is the model answering
unusably. Check the first before reading a prompt.

```bash
python -m tools.check_provider              # credentials, balance, model, structured output
docker exec tara python -m tools.evaluation_worker --status
```

Do **not** switch provider or model to work around it. The gateway never
substitutes silently, and neither should an operator under pressure — a report
produced by a different model is not comparable with the ones beside it.

### Evaluation failure

```bash
docker exec tara python -m tools.evaluation_worker --status
```

Reports counts by state and the oldest wait. A record in `failed` carries
`error_kind` and `failed_stage` (`evidence_extraction`, `skill_assessment`,
`integrity_gate`, `result_assembly`) — the stage is what tells you whether to
retry or investigate. Re-run by re-requesting the evaluation from the console;
it supersedes rather than mutates, so the old record stays readable.

A `running` record with no worker alive was orphaned by a crash. Starting the
worker recovers it.

### Persistence failure

`/api/ready` answers `503` with `storage: unavailable`. Usually a full disk or a
volume that failed to mount.

```bash
df -h /srv/tara/data
docker exec tara ls -la /app/data
```

Do not delete files to free space. `data/audit/` is the largest directory and it
is the security trail.

### Redis failure

Not applicable. Redis is not used.

### Corrupted state

Writes are atomic (temp file plus rename), so a torn file is unlikely. If one
happens, the stores fail closed — a `json.JSONDecodeError` on read, not a
silently empty store — except the audit reader, which skips a torn final line
and returns the rest.

```bash
docker exec tara python -c "import json,pathlib; [json.loads(p.read_text()) for p in pathlib.Path('/app/data').glob('*.json')]"
```

Restore that file from backup. If there is no backup, the data is gone —
see §Backup and restore in DEPLOYMENT.md.

### Candidate session interruption

Expected and handled. The candidate reopens their invitation link and rejoins
the same session within `REJOIN_WINDOW_SEC` (default 3600 s). State is
authoritative on the server; rejoin cannot skip a question, answer an old turn
or replay a consumed one. A turn in flight when the process stopped is answered
again — `applied_turns` makes a retried turn idempotent.

If a candidate reports a lost interview, find their session:

```bash
docker exec tara python -c "
from services.data import invites
i = invites.get('<token>')
print(i.status, i.session_id, i.lifecycle)"
```

### Deployment failure

Roll back (below). Do not "fix forward" on a stateful single-instance
deployment while candidates are mid-interview.

### A stale process serving an old build

Caught three times during development, and each time it looked like an
application defect: an older process still bound to the port, answering `404`
for routes that exist in the current build, not echoing request ids, and
reporting a superseded error vocabulary.

```bash
curl -fsS https://<host>/api/health | grep '"version"'
```

If `version` is not the tag you deployed, you are looking at the wrong process.
Nothing downstream of that check is worth debugging until it matches.

### Health check failure

Distinguish the two:

* `/api/health` failing → the process. Restart.
* `/api/ready` failing → a dependency. The body names which check and why; the
  configuration check reports a *count* and the detail is in the startup log.

---

## Rollback procedure

### When

Roll back when the new version is worse than the old one and the fix is not
immediate: the API will not start, readiness will not clear, authentication is
broken, candidates cannot complete, or evaluations fail systematically.

Do **not** roll back for a provider outage — that is not the deployment.

### What

| Artefact | Action |
| --- | --- |
| Application + frontend | `docker stop tara && docker rm tara`, then run the previous tag with the previous environment |
| Configuration | Restore the previous environment variables. Not versioned here — whatever holds them must version them |
| Worker | Stop it, start the previous image's. Safe at any point; records stay `pending` |
| **Data** | **Do not roll back.** See below |

### What must not be rolled back destructively

**Never restore a data volume to undo an application deploy.** It un-deletes
candidate data that erasure removed, resurrects revoked invitations, and
destroys every interview that happened since the snapshot. The application and
its data roll back on different clocks; only the application should.

Data written by a newer build is readable by an older one for additive changes —
every `from_dict` filters to known fields, so unknown ones are dropped rather
than fatal. That is not a guarantee across a change that altered a field's
meaning, and nothing detects it: there is no schema version on the data
directory.

### Verify the rollback

```bash
curl -fsS https://<host>/api/health | grep '"version"'   # the OLD version
curl -fsS https://<host>/api/ready                        # "status": "ok"
```

Then smoke steps 3–12. Then check the count of interviews is what it was — a
rollback that also reverted data shows up here.

### In-flight interviews

They reconnect. Same behaviour as any restart: the candidate sees a connection
error, reopens the link, and resumes at the last completed turn. There is no
drain-then-swap, because there is one instance. Roll back when nobody is
interviewing if you can; accept the reconnect if you cannot.

---

## Data lifecycle operations

Implementation and rationale in [DATA_LIFECYCLE.md](DATA_LIFECYCLE.md).

### Retention sweep

```bash
docker exec tara python -m tools.retention_cleanup --status    # counts, deletes nothing
docker exec tara python -m tools.retention_cleanup --dry-run   # what would go
docker exec tara python -m tools.retention_cleanup             # do it
```

Exit `0` = nothing eligible, or everything erased and verified. Exit `1` = at
least one erasure failed. Safe to run twice and safe to run concurrently.

### Deletion on request

```bash
docker exec tara python -m tools.retention_cleanup --token <invitation-token>
```

Or `DELETE /api/recruiter/candidates/{token}/data` from the console session.
**Administrators only** — the guard maps `DELETE` to the `delete` capability,
which only `admin` carries. A recruiter gets `403`; another organization gets
`404`.

### Verify an erasure

```bash
docker exec tara python -c "
from services.data import erasure
print(erasure.verify('<token>'))"
```

`[]` means nothing remains. Anything else lists the locations still holding
data. This re-reads the stores rather than trusting what the deletion returned.

### The frozen evaluation snapshot

**Read this before writing any deletion tooling of your own.**
`EvaluationRecord.snapshot.turns` is a **complete second copy of the
transcript**, frozen at evaluation time so a result stays reproducible, and
`evidence[].quote` holds verbatim candidate words. Deleting the session file
leaves both untouched.

`erasure.erase()` removes evaluations *first*, before the session, for exactly
this reason. Any script that deletes candidate data must do the same, or it will
leave a full transcript behind while reporting success.

### `DELETION_FAILED`

A partial erasure is never reported as complete. The record lands in
`deletion_failed` with `deletion_error` (the exception *type*, never its
payload) and `deletion_remaining` (location names only). The API answers `409`,
not `200`.

```bash
docker exec tara python -m tools.retention_cleanup --status   # "Failed deletions"
```

Retrying is just running the sweep again — a failed record stays eligible
precisely so it is not abandoned.

### Audit trail

Erasure writes `CANDIDATE_DATA_ERASED` or `CANDIDATE_DATA_ERASURE_FAILED` to the
product log, never to the session's own trail — that trail is one of the things
erasure deletes. The product log keeps every row; only the fields naming an
assessment outcome are replaced with `[erased]`.

### Backups and erasure

A backup taken before an erasure **contains the erased data**. Either keep
backup retention shorter than your erasure SLA, or re-run
`python -m tools.retention_cleanup` after any restore. Nothing enforces this.

---

## Backup and restore

The tool exists and is tested; **nothing schedules it**, the archive is not
encrypted, and it is written to a local path. All three are the deployment's
responsibility.

### Take one

```bash
# Preferred: point-in-time, with the API stopped.
docker stop tara
docker run --rm -v /srv/tara/data:/app/data -v /backups:/backups \
  tara:<version> python -m tools.backup --create /backups --quiesced
docker start tara

# Or live, if downtime is not acceptable. Recorded as `live` in the manifest,
# with a warning: writes are atomic per file, not across files.
docker exec tara python -m tools.backup --create /backups
```

Both verify the archive immediately after writing it. Exit `1` means the
snapshot is bad — treat it as no backup at all.

**MANUAL STEP — NOT AUTOMATED.** Schedule it daily and copy the archive
somewhere that survives losing the host:

```bash
30 2 * * *  docker exec tara python -m tools.backup --create /backups
```

### Check what you have

```bash
docker exec tara python -m tools.backup --list /backups
docker exec tara python -m tools.backup --verify /backups/tara-<stamp>.tar.gz
```

`--verify` reads every byte and checks it against the manifest's per-file
SHA-256. Run it on the copy at its destination, not only on the one you just
wrote — the interesting corruption happens in transit.

### Restore

```bash
docker stop tara
docker run --rm -v /srv/tara/data:/app/data -v /backups:/backups \
  tara:<version> python -m tools.backup \
  --restore /backups/tara-<stamp>.tar.gz --into /app/data --force
docker start tara
curl -fsS https://<host>/api/ready
```

`--force` is needed over a non-empty directory. The previous contents are
**moved aside**, not deleted, into `data.displaced-<timestamp>` — if you
restored the wrong archive, that directory is how you undo it. Remove it once
you are satisfied; it holds candidate data and it is inside no lifecycle.

A damaged archive is refused rather than half-restored.

### After every restore

```bash
docker exec tara python -m tools.retention_cleanup
```

**A snapshot taken before an erasure contains the erased data.** Restoring it
resurrects candidates whose records were deleted. The restore path prints this
warning; running the sweep is what acts on it. Keep snapshot retention shorter
than your erasure SLA, or this becomes a standing breach of the guarantee.

---

## Security operations

Model and evidence in [PRODUCTION_SECURITY_MATRIX.md](PRODUCTION_SECURITY_MATRIX.md).

### Accounts

```bash
docker exec -it tara python -m tools.make_user --email x@example.com --admin
docker exec -it tara python -m tools.make_user --list
docker exec -it tara python -m tools.make_user --email x@example.com --disable
```

Disabling takes effect immediately — the principal is rebuilt from the user
record on every request, so a live session stops working at once.

### Revoke a compromised candidate invitation

```bash
docker exec tara python -c "
from services.data import invites
print(invites.revoke('<token>').effective_status)"
```

A revoked invitation cannot start a session (`410`). If the interview is already
underway and the concern is the transcript rather than the link, erase the data
(above).

### Suspected recruiter account compromise

1. Disable the account (above) — every live session for it dies immediately.
2. Read what it did: the product audit log records actor, organization, action
   and subject for every recruiter action.
3. Rotate: create a replacement account; there is no password reset flow.
4. Check tenant isolation held — cross-tenant reads answer `404` and are
   asserted by 95 security tests, but confirm the audit shows no action outside
   the expected organization.

### Provider credential leak

1. Revoke the key at the provider.
2. Issue a new one, update the environment, restart.
3. `python -m tools.secrets_audit` — confirms nothing sensitive is in the
   bundles, the public responses or the audit trail.
4. The key never reaches a browser or a log, so a leak is upstream of the
   application; check wherever the environment is stored.

### Logs

JSON lines to stdout. They carry request id, route **template**, status,
duration and a stable error category — never a password, an authorization
header, a candidate token, a session grant, a transcript, an answer or a
candidate name. Query parameters are an allow-list, so `?token=` never reaches a
log line.

Confirm after any logging change:

```bash
python -m pytest -q tests/test_deployment.py -k log
```

---

## Staging acceptance

The acceptance matrix is a command, not a checklist to work through by hand:

```bash
python -m tools.staging_acceptance \
  --base-url https://staging.example.com \
  --admin-email you@example.com \
  --tenant-b-email admin@second-org.example \
  --tenant-b-interview iv_xxxxxxxx \
  --out staging-acceptance.json
```

Exit `0` all clear · `1` at least one FAIL · `2` usage or unreachable. It
refuses a localhost target unless `--allow-local` is passed, and stamps every
result from such a run `local` — a local pass is not a staging pass.

Three rows it cannot see from outside the deployment, each with its own step:

```bash
# restart survival — two phases, with a real restart between them
python -m tools.staging_acceptance --base-url https://… --phase before
<restart the service>
python -m tools.staging_acceptance --base-url https://… --phase after

# backup and restore — run ON the host, against the real data directory
python -m tools.backup --create /backups --quiesced
python -m tools.backup --verify /backups/tara-<stamp>.tar.gz

# rollback — needs two tagged images; see §Rollback procedure
```

### Reading a failure

`BLOCKED` means an external dependency, most often the provider budget, and is
not a defect against the deployment. `NOT TESTED` means the harness could not
reach it — a missing second tenant, or a row that needs host access. Only
`FAIL` is a defect, and a `P0` prints a `STOP` line.

---

## Provider incident procedure

The gateway resolves every call to a `CallStatus` and **never silently
substitutes a model or a provider**. Neither should an operator.

| Symptom | `CallStatus` | Meaning | Action |
| --- | --- | --- | --- |
| `401` / `403` | `AUTH_ERROR` | key, permission or **billing** | `tools/check_provider`. Not retried — retrying an auth failure spends the same error three times |
| `403 Key limit exceeded` | `AUTH_ERROR` → `error_kind: "provider"` | **budget exhausted — the current state** | Restore the budget. Do not switch providers |
| `429` | `RATE_LIMIT_ERROR` | rate limited, after 3 retries with backoff | Reduce concurrency; raise the provider's limit |
| `5xx` | `TRANSPORT_ERROR` | provider-side, retried 3× | Wait. Evaluations fail with a stage and are re-runnable |
| Timeout | `TIMEOUT` | no answer within the workload's limit | Check provider status. Raise `TARA_LLM_TIMEOUT` only with a reason |
| Malformed structured output | `SCHEMA_ERROR` | answered, not in the declared shape | **Do not relax the schema.** Validation is what stops a malformed result becoming a hiring document. Record it; it is evidence about the model |
| Truncated | `OUTPUT_TRUNCATED` | ran out of tokens mid-answer | Raise the workload's `max_tokens`; do not accept the partial |

In every case the evaluation lands in `failed` with a `failed_stage`, the
candidate stays `complete`, and the recruiter sees no report rather than a wrong
one. That is the intended behaviour — **a completed interview is not presented
as a completed evaluation until a readable result exists.**

### Current state

```text
BLOCKED — 403 Key limit exceeded (total limit)
```

Live provider verification is unavailable. Unblock by restoring the OpenRouter
account's budget, then:

```bash
python -m tools.check_provider --smoke     # one real structured call
python -m pytest -q -m live                # the provider-dependent suite
```
