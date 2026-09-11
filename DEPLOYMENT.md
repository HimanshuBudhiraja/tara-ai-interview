# Deployment

How Tara is actually built, configured and run — as the repository stands today,
not as it is intended to end up. Where something is designed but not wired, it
says so; where something needs infrastructure this repository cannot provide, it
says that too.

**There is no deployed environment yet.** Everything below has been exercised
locally and in the automated suite. Nothing here has been verified against a
provisioned host, and no claim is made that it has.

Status vocabulary, used consistently:

| Label | Meaning |
| --- | --- |
| **IMPLEMENTED** | in the code, exercised by a test or a command whose output is quoted |
| **CONFIGURED, NOT DEPLOYED** | the artefact exists and is correct; nothing has run it in a real environment |
| **NOT IMPLEMENTED** | does not exist. Named here so it is not mistaken for an oversight |
| **INFRASTRUCTURE DEPENDENCY** | the application is ready; something outside this repository must be set up |

---

## Component inventory

| Component | Current implementation | Verified? | Production-ready? | Known limitation |
| --- | --- | --- | --- | --- |
| Candidate frontend | React 19 + Vite, built to `apps/candidate/dist`, served at `/` by the API | ✅ build + 1122-test suite | yes | none |
| Recruiter frontend | React 19 + Vite, built to `apps/recruiter/dist`, served at `/recruiter` | ✅ build, typecheck, 87 vitest | yes | none |
| Backend API | FastAPI on uvicorn, `services.api.app:app` | ✅ suite + local run | yes | single process; see persistence |
| Candidate runtime | In-process orchestrator; HTTP turns plus a WebSocket at `/ws/interview/{id}` | ✅ suite | yes | server-affinity, see below |
| Evaluation execution | **Synchronous**, in a worker thread of the request | ✅ suite | with caveats | a slow model holds the request open |
| Evaluation drain worker | `tools/evaluation_worker.py` — recovers `pending` records | ✅ 3 tests | yes | not the primary path; see §Evaluation |
| AI Model Gateway | `services/ai/gateway.py`, six workload slots, retries, telemetry | ✅ suite | yes | live verification BLOCKED (§Provider) |
| Persistence | **File-backed JSON under `data/`** | ✅ suite | **single host only** | see §Durable persistence status |
| Redis | **Not used.** `REDIS_URL` is read and unused | n/a | n/a | nothing to configure |
| Object/file storage | **Not used.** No uploads exist in the product | n/a | n/a | — |
| Retention & erasure | `services/data/retention.py`, `erasure.py`, `tools/retention_cleanup.py` | ✅ 43 tests | yes | nothing schedules the sweep |
| Health / readiness | `/api/health`, `/api/ready` | ✅ 8 tests | yes | readiness does not test the provider, by design |
| Observability | Request ids, JSON logs, stable error categories | ✅ 7 tests | yes | no metrics backend |
| Container image | `Dockerfile`, multi-stage, one image serves both bundles | CONFIGURED, NOT DEPLOYED | yes | never built in CI |
| Compose stack | `docker-compose.yml` | CONFIGURED, NOT DEPLOYED | dev only | `postgres`/`redis` are in an `infra` profile nothing reads |
| Migrations | **NOT IMPLEMENTED** | — | — | `packages/types/schema.sql` is a target, not a migration |
| CI/CD | **NOT IMPLEMENTED** | — | — | no pipeline in the repository |
| HTTPS | INFRASTRUCTURE DEPENDENCY | — | — | terminate at a proxy; the app speaks HTTP |
| Backup / restore | `tools/backup.py` — snapshot, verify, restore | ✅ 8 tests + a real 9.7 MB round trip | tool: yes | scheduling, offsite copy and encryption are infrastructure |

---

## Environments

### local / development — IMPLEMENTED

Three processes, or one container.

```bash
# API on :8000 — file-backed storage, deterministic mock if no key is set
python -m uvicorn services.api.app:app --reload --port 8000

# The two frontends, each proxying /api to :8000
npm run dev:candidate     # :5173
npm run dev:recruiter     # :5174
```

Or `docker compose up api`, which builds both bundles into the image and serves
everything from `:8000`.

`TARA_ENV` defaults to `development`, which means: cookies without `Secure` (a
`Secure` cookie is not sent over `http://localhost`, so nothing would work),
`ALLOWED_ORIGINS=*`, the `demo` invitation seeded on boot, and the demo answer
overlay served. All four are refused in production.

### test / CI — IMPLEMENTED locally, NOT IMPLEMENTED as a pipeline

The suite runs offline by construction. `tests/conftest.py` redirects every
store into a temporary directory, and an autouse fixture sets `TARA_LLM=mock`
and clears the gateway singleton so **no non-live test can reach a provider**,
whatever ran before it.

```bash
python -m pytest -q                    # 1122 passed, 11 deselected
python -m pytest -q -m live            # provider-dependent; currently blocked
npm run -w @tara/recruiter test        # 87
npx tsc -b apps/recruiter apps/candidate
npm run build
```

There is no CI pipeline. Running these is currently a manual act.

### staging — NOT DEPLOYED

Intended shape: identical to production, `TARA_ENV=staging` (which
`is_production()` treats as production, so the same checks apply), its own data
volume, its own provider key with a low budget, and no real candidate data.

### production — NOT DEPLOYED

Intended shape in §Minimum deployment envelope. Nothing has been provisioned.

---

## Application components

```
                    ┌─────────────────────────────────────┐
   Candidate  ──────►                                     │
   browser          │  HTTPS terminator (reverse proxy)   │  INFRASTRUCTURE
                    │  — not part of this repository      │  DEPENDENCY
   Recruiter  ──────►                                     │
   browser          └──────────────────┬──────────────────┘
                                       │ HTTP
                    ┌──────────────────▼──────────────────┐
                    │  uvicorn · services.api.app:app     │
                    │                                     │
                    │  /                candidate bundle  │
                    │  /recruiter       recruiter bundle  │
                    │  /api/*           the API           │
                    │  /ws/interview/*  the turn socket   │
                    │                                     │
                    │  orchestrator (in process)          │
                    │  evaluation     (in a request thread)│
                    │  AI Model Gateway                   │
                    └───────┬─────────────────────┬───────┘
                            │                     │
                  ┌─────────▼────────┐   ┌────────▼─────────┐
                  │  data/  (JSON)   │   │  OpenRouter      │
                  │  PERSISTENT      │   │  (or any OpenAI- │
                  │  VOLUME REQUIRED │   │  compatible URL) │
                  └─────────▲────────┘   └──────────────────┘
                            │
                  ┌─────────┴──────────────────────────────┐
                  │  tools/evaluation_worker.py  (drain)   │
                  │  tools/retention_cleanup.py  (sweep)   │
                  │  — same host, same volume              │
                  └────────────────────────────────────────┘
```

**One origin.** Both bundles and the API are served by the same process, so the
candidate never makes a cross-origin request and the recruiter console needs no
CORS in production. That is why `ALLOWED_ORIGINS` can be a short explicit list.

**The WebSocket matters.** `/ws/interview/{session_id}` carries the live turn
loop. A proxy that does not upgrade it leaves a deployment where every ordinary
HTTP request works and interviews silently fall back or fail. It must be
configured explicitly, and it must be `wss://` behind TLS.

**Server affinity.** Session state is a file on the host's disk. Two application
instances behind a round-robin load balancer would each see only the sessions
they created. See §Durable persistence status.

---

## Configuration

Every setting is read in `services/config.py` and nowhere else. `.env.example`
documents all of them with defaults; nothing below contains a real value.

### Required in production

| Variable | Purpose | Validated at startup |
| --- | --- | --- |
| `TARA_ENV` | `production` / `staging` turns on the production contract | — |
| `TARA_DATA_DIR` | where all state lives; must be a persistent volume | ✅ writable, and refused if ephemeral |
| `ALLOWED_ORIGINS` | explicit origins; `*` is refused | ✅ |
| `TARA_COOKIES_SECURE` | `Secure` on session cookies | ✅ defaults on in production |
| `OPENROUTER_API_KEY` | provider credential, server-side only | ✅ presence only, never a call |
| `RECRUITER_AUTH_REQUIRED` | close the recruiter API when no account exists | ✅ |

### Model slots

Six independent slots, each falling back to `TARA_MODEL_DEFAULT`. Business logic
never names a provider or a model — it names a `Workload`, and the gateway
resolves it.

```
INTERVIEW_DESIGNER_MODEL    QUESTION_GENERATOR_MODEL    ANSWER_CLASSIFIER_MODEL
FOLLOWUP_GENERATOR_MODEL    SCORING_MODEL               REPORT_GENERATOR_MODEL
```

Startup refuses a slot that resolves to an empty string. It does **not** check
that a model name is real — that needs a provider call, and configuration
validation must not cost money or fail during an outage.

### Secrets

`OPENROUTER_API_KEY`, `RETELL_API_KEY`, `TARA_BOOTSTRAP_PASSWORD`. All read
server-side; none reachable from a browser.

* `.env` is gitignored; `.env.example` carries no values.
* `python -m tools.secrets_audit` scans the built bundles, the frontend source,
  the public API responses and the audit trail, and exits non-zero on a finding.
  It also runs as a test.
* Production refuses to boot with `TARA_BOOTSTRAP_PASSWORD` still set — it is
  read once at first boot, and leaving it in the environment leaves a password
  where anything that can read the process environment can read it.
* Secret **delivery** is an INFRASTRUCTURE DEPENDENCY. The application reads
  environment variables; where they come from — a secret manager, a mounted
  file, the platform's own store — is the platform's business. There is no
  secret-manager integration in this repository.

### Retention and logging

`CANDIDATE_DATA_RETENTION_DAYS` (180), `TRANSCRIPT_RETENTION_DAYS` (90),
`EVALUATION_RETENTION_DAYS` (180), `AUDIT_RETENTION_DAYS` (400),
`RETENTION_SWEEP_INTERVAL_HOURS` (24, documentation not enforcement).
Validated at startup for internal consistency. Full treatment in
[DATA_LIFECYCLE.md](DATA_LIFECYCLE.md) §3.

`TARA_LOG_FORMAT` (`json` | `text`), `TARA_LOG_LEVEL`. Production refuses
`DEBUG`: debug logging has not been audited for candidate content.

`TARA_VERSION` names the build in health responses and every log line.

---

## Health and readiness

Two endpoints, two different questions. Both are public — a load balancer probes
before it has a credential — and both are written to be safe unauthenticated.

### `GET /api/health` — liveness

* **Purpose:** is this process alive?
* **Dependency checks:** none. No filesystem, no database, no provider.
* **Healthy:** `200` — `{"ok": true, "service", "version", "uptime_sec"}`
* **Unhealthy:** the process does not answer. There is no unhealthy body.

Deliberately dependency-free. A probe that restarts the container because a
dependency is down turns a five-minute blip into a crash loop, and a liveness
probe that calls a model costs money every ten seconds forever.
`test_liveness_does_not_depend_on_the_provider_or_the_data_directory` breaks
both and asserts `200`.

### `GET /api/ready` — readiness

* **Purpose:** can this process serve a request?
* **Healthy:** `200`, `status: "ok"` or `"degraded"`
* **Unhealthy:** `503`, `status: "unavailable"`

| Check | What it does | Verdict when it fails |
| --- | --- | --- |
| `storage` | writes and removes a probe file — not a permission bit, because a read-only mount and a full disk both pass that | `unavailable` |
| `question_pool` | the authored pool loaded and is non-empty | `unavailable` / `degraded` |
| `accounts` | at least one account exists | `unavailable` if `RECRUITER_AUTH_REQUIRED`, else `degraded` |
| `configuration` | `require_production_configuration()` is empty | `unavailable` |
| `database` | reports that `DATABASE_URL` is unused | never fails — see below |
| `ai_provider` | reports configured / not configured, `"checked": false` | **never fails, never called** |

**Readiness does not validate the AI provider, and it does not validate a
database.** Both are deliberate and both are limitations worth stating plainly:

* The provider is reported, never tested. An interview can start, a candidate
  can answer, and an evaluation can be queued while the provider is down; only
  execution is blocked, and that has its own failure state. A readiness probe
  that called the model would take Tara out of rotation for an outage it can
  survive — and would spend real money on every probe.
* `DATABASE_URL` is read and unused. Readiness must not claim a dependency the
  application does not have.

Neither endpoint leaks a path, an origin, a setting or a secret. The
configuration check reports a *count* of problems; the problems themselves go to
the startup log. `test_neither_probe_leaks_a_path_a_secret_or_a_configuration_value`.

---

## Deployment sequence

Every step is **MANUAL — NOT AUTOMATED**. There is no pipeline, no deploy
script, and no platform configuration in this repository. Exact commands are in
[PRODUCTION_RUNBOOK.md](PRODUCTION_RUNBOOK.md).

| # | Step | Automated? | Command / mechanism |
| --- | --- | --- | --- |
| 1 | Build image | ❌ manual | `docker build -t tara:<version> .` (bundles build inside it) |
| 2 | Validate configuration | ⚠️ at startup | the API prints every problem and readiness answers `503` |
| 3 | Apply migrations | **n/a** | none exist; the data directory is the schema |
| 4 | Provision the volume | ❌ manual | must exist and be persistent before first boot |
| 5 | Deploy backend | ❌ manual | `docker run` / compose / the platform's own mechanism |
| 6 | Create the first admin | ❌ manual, once | `python -m tools.make_user --email … --admin` |
| 7 | Deploy worker | ❌ manual, optional | `python -m tools.evaluation_worker` — see §Evaluation |
| 8 | Deploy frontend | ✅ implicit | the bundles are inside the image; there is no separate deploy |
| 9 | Verify health | ⚠️ semi | `curl /api/health` then `/api/ready` |
| 10 | Smoke test | ❌ manual | the runbook's list; the last step is provider-BLOCKED |
| 11 | Enable traffic | ❌ manual | the proxy's business |

Step 3 is worth reading twice: **there is nothing to migrate, because there is
no database.** That is not a simplification — it is the limitation described
next.

---

## Durable persistence status

> **The current application persistence layer is file-backed JSON and is
> suitable for local/single-host validation but is not the final durable
> multi-instance production persistence architecture.**

Every piece of core state — organizations, users, login sessions, jobs,
interviews, published versions, invitations, sessions, transcripts, evaluations,
evidence, results and audit events — is a JSON file or a directory of JSON files
under `TARA_DATA_DIR`. Writes are atomic (temp file plus rename) and guarded by
a per-file re-entrant lock plus `fcntl.flock`, which makes them safe against
concurrent *threads and processes on one host*. That is the extent of the
guarantee.

### Consequences, stated individually

| Consequence | What it means in practice |
| --- | --- |
| **Single-host dependency** | The application and its data are on the same machine. Losing the host loses the deployment until the volume is reattached. |
| **No horizontal scaling** | A second instance cannot see the first's sessions. Two instances behind a load balancer produce candidates whose interviews vanish between turns. |
| **Host-local state** | Nothing is shared. There is no shared cache, no shared queue, no shared lock outside the filesystem. |
| **Weaker disaster recovery** | Recovery means restoring a filesystem, not a database with a transaction log. There is no point-in-time recovery and no replication. |
| **Deployment replacement risk** | An immutable-infrastructure deploy that replaces the host **destroys all state** unless the volume is external and reattached. On a platform with ephemeral disks this is a total data loss on every deploy — which is why startup refuses an ephemeral `TARA_DATA_DIR`. |
| **Concurrency limits** | Correct under concurrency, not fast under it. Every read of a store parses the whole file; `invites.json` at 5,000 rows is ~3 MB parsed per read. Verified safe at 3 concurrent interviews (PILOT_READINESS.md); not characterised beyond that. |
| **Backup is the only durability** | With no replica and no WAL, an untaken backup means an unrecoverable loss. Nothing takes one — see §Backup and restore. |
| **Instances are not interchangeable** | The core assumption of a modern deployment — that any instance can serve any request — does not hold. Routing must be to one instance. |

### The relational schema

> **A relational target schema exists in `packages/types/schema.sql`, but a
> production Postgres migration has not been implemented.**

`packages/types/schema.sql` is 277 lines defining 18 tables — `organization`,
`app_user`, `job`, `interview`, `interview_version`, `skill`, `task`,
`task_skill`, `question`, `candidate`, `invitation`, `interview_session`,
`interview_turn`, `evidence`, `skill_score`, `interview_result`, `report`,
`audit_event`. `docker-compose.yml` applies it to a Postgres container in an
`infra` profile.

**No application code reads or writes it.** `DATABASE_URL` is parsed in
`config.py` and used nowhere. Having the file provides exactly one thing: a
reviewable target shape. It provides **no** durability, no transactions, no
replication and no multi-host capability. A deployment that starts the
`postgres` profile gets an empty database that nothing talks to.

### Status

```
CURRENT STATUS: PRODUCTION BLOCKER FOR MULTI-HOST / HIGH-AVAILABILITY DEPLOYMENT
```

This is a blocker for **that** shape of deployment, and it is important not to
overstate it:

| Deployment shape | Verdict |
| --- | --- |
| **Single-host controlled deployment** | **Possible**, with the explicit safeguards below |
| **Multi-host / horizontally scaled production** | **Blocked** until durable shared persistence is implemented |

Safeguards a single-host deployment must have, all of them operational rather
than code:

1. An **external persistent volume** for `TARA_DATA_DIR`, surviving container
   replacement. Startup refuses an ephemeral path, which catches the obvious
   mistake but cannot detect every one.
2. **Exactly one application instance.** No autoscaling, no rolling deploy that
   runs two instances at once against the same volume from different hosts.
3. A **backup** of that volume, taken and restore-tested by the operator, since
   the application takes none.
4. **Accepted downtime** during deploys: stop, swap, start. There is no
   zero-downtime path for a single-instance stateful process.
5. A **bounded pilot size**. Concurrency is verified at 3 simultaneous
   interviews and not characterised above that.

---

## Minimum deployment envelope

### Backend

* **Process model:** one uvicorn process. Multiple workers on one host share the
  volume and the file locks correctly; multiple *hosts* do not.
* **Persistent volume:** mounted at `TARA_DATA_DIR`, surviving container
  replacement. Non-negotiable — it is the database.
* **Supervision:** the platform restarts the process. There is no supervisor in
  the image; `CMD` is a single uvicorn invocation.
* **Environment / secrets:** environment variables. No secret-manager
  integration.
* **HTTPS:** terminated at a reverse proxy. The application speaks HTTP and
  never terminates TLS. The proxy must forward WebSocket upgrades.
* **Health checks:** liveness `/api/health`, readiness `/api/ready`.

### Frontend

* **Build:** `npm run build`, inside the image. Both bundles are static.
* **Serving:** by the same API process, from the same origin. No CDN, no
  separate host, no separate deploy.
* **API origin:** relative paths — there is no `VITE_API_URL`, and no
  `import.meta.env.VITE_*` exists anywhere in the frontend source (asserted by
  `test_no_frontend_source_file_reads_a_secret_through_the_build`). A bundle
  cannot be built pointing at the wrong backend, because it does not name one.
* **HTTPS:** inherited from the proxy.
* Verified: no built bundle contains `localhost`, `127.0.0.1`, `:5173` or
  `:5174` (`test_no_built_bundle_hardcodes_a_development_host`).

### Evaluation processing

**Evaluation is synchronous.** This is the honest description, and it differs
from the queue-and-worker diagram in the deployment brief.

```
recruiter clicks Evaluate
   → POST /api/recruiter/sessions/{id}/evaluation
   → jobs.request()      persists a PENDING record with a frozen snapshot
   → jobs.run()          executes it, in a worker thread of that request
   → the response carries the finished record
```

What this means operationally:

* A slow model holds the HTTP request open. The gateway's per-workload timeout
  bounds it; a full evaluation is minutes, not seconds.
* If the process dies mid-evaluation, the record is left `pending` or `running`.
* There is **no queue** and no broker. Nothing to provision.

`tools/evaluation_worker.py` is a **drain and recovery** process, not the
primary path. It executes records that are already `pending` — the ones a crash
or an interrupted request left behind — and it supports:

| Property | How |
| --- | --- |
| Claiming | `jobs.run()` takes a per-session threading lock plus `fcntl.flock` and re-reads the record inside it |
| Idempotency | two workers on one record produce one evaluation and one no-op — never two "current" results |
| Retry | a failure lands `FAILED` with a `failed_stage`; re-running the record is the retry |
| Bounded retry | `--max-attempts` (default 3) — a record that has failed three times is something to investigate, not to keep paying for |
| Failure visibility | `--status` reports counts by state and the oldest wait |
| Graceful shutdown | SIGTERM/SIGINT stop the loop **after** the record in flight finishes; sleep is sliced so a stop is noticed within 250 ms |

Running it is optional for a single-instance deployment and recommended: without
it, a record orphaned by a restart stays `pending` forever and the recruiter sees
an evaluation that never arrives.

### Persistence

Covered above. The envelope requirement is: **a persistent volume, a backup, and
one instance.**

### AI provider

* **Gateway:** all six workloads go through `services/ai/gateway.py`. No
  evaluator or interview code contains a provider credential, a base URL or a
  model name — `test_every_workload_that_reads_candidate_text_fences_it` and the
  workload enum enforce the boundary.
* **Key handling:** `OPENROUTER_API_KEY`, read in `config.py`, server-side only,
  never in a bundle, never in a log, never in an audit record.
* **Timeouts:** per workload, defaulting from `TARA_LLM_TIMEOUT` (12 s). A fast
  classification and a slow evaluation do not share a limit.
* **Retries:** 3 attempts with backoff, for transport failures, 429 and 5xx
  only. A 4xx that is not 429 is not retried — retrying an auth failure or an
  exhausted budget just spends the same error three times.
* **Failure behaviour:** every call resolves to a `CallStatus` —
  `SUCCESS`, `MODEL_ERROR`, `SCHEMA_ERROR`, `OUTPUT_TRUNCATED`,
  `TRANSPORT_ERROR`, `AUTH_ERROR`, `RATE_LIMIT_ERROR`, `TIMEOUT` — recorded in
  telemetry. **There is no silent fallback**: the gateway never substitutes a
  different model or provider, and a failed evaluation is `FAILED` with a
  stage, never a partial result presented as a whole one.
* **Cost controls:** every loop that can call a model is bounded by
  configuration rather than by candidate behaviour —
  `TARA_QUESTION_BUDGET` × (1 + probes + reasks + clarifies) is the ceiling on
  an interview, asserted ≤ 100 calls by
  `test_an_interview_cannot_make_unbounded_provider_calls`. Evaluation and
  generation are rate-limited per client, and a repeated evaluation request
  returns the existing record rather than running a second pass.

> **The live provider key is currently exhausted.** `tools/check_provider.py`
> and the `-m live` suite both return
> `403 Key limit exceeded (total limit)`. Live provider verification is
> therefore **unavailable** until the provider budget is restored. This is an
> external budget condition, not an application failure, and it has not been
> worked around: no fallback provider has been configured, no model has been
> changed, and no result has been stubbed.

---

## Rollback

**No rollback has been executed or tested**, because there is nothing to roll
back from. What follows is the procedure the current architecture supports, with
its real constraints. Operational steps are in
[PRODUCTION_RUNBOOK.md](PRODUCTION_RUNBOOK.md).

| Artefact | Rollback | Constraint |
| --- | --- | --- |
| **Application** | Redeploy the previous image tag | Requires tagged images. The repository does not build or push them — INFRASTRUCTURE DEPENDENCY |
| **Frontend** | Rolls back with the application | The bundles are inside the image; they cannot be rolled back separately, and cannot drift from the API |
| **Configuration** | Restore the previous environment and restart | Not versioned by this repository. Whatever holds the environment must version it |
| **Worker** | Stop it; restart the previous image | Safe at any time — records stay `pending` and the next worker picks them up |
| **Data** | **Not rolled back** | See below |

### Data compatibility

Stores read unknown fields tolerantly: every `from_dict` filters to known
dataclass fields, so a **newer** file read by an **older** build drops fields it
does not recognise rather than failing. That makes a rollback survivable for
additive changes, which is what the changes so far have been.

It is not a guarantee. A rollback across a change that altered the *meaning* of
a field, or that removed one an older build requires, is not safe, and nothing
detects that automatically. **There is no schema version stamped on the data
directory** — a known limitation.

**Never restore a data volume to roll back an application deploy.** Doing so
un-deletes candidate data that erasure removed, resurrects invitations that were
revoked, and destroys every interview that happened in between. The application
and its data roll back on different clocks, and only the application should.

### In-flight candidate sessions

Stopping the API ends every open WebSocket. What happens next:

* Session state is on disk and up to date to the last completed turn — turn
  handling persists before responding.
* The candidate's page shows a connection error, and the invitation link
  rejoins the same session within `REJOIN_WINDOW_SEC` (default 3600 s).
* Rejoin is bounded and server-authoritative: it cannot skip a question, answer
  an old turn, or replay a consumed one.
* A turn in flight at the moment of the stop is lost. The candidate answers it
  again on rejoin. `applied_turns` makes a retried turn idempotent, so a
  duplicate delivery is not applied twice.

**A deploy is not invisible to a candidate mid-interview.** They will see a
reconnect. There is no drain-then-swap, because there is only one instance.
Deploy when nobody is interviewing, or accept the reconnect.

### Evaluation jobs

Safe across a rollback and a restart. A record in `pending` stays `pending`; one
in `running` when the process died is re-run by the worker or by an explicit
re-request. Because `run()` re-reads inside the lock and supersedes rather than
mutates, a re-run produces a new record, never two "current" results.

---

## Backup and restore

```
TOOL:        IMPLEMENTED   python -m tools.backup
SCHEDULING:  NOT IMPLEMENTED            — nothing runs it
OFFSITE:     INFRASTRUCTURE DEPENDENCY  — it writes a local file
ENCRYPTION:  INFRASTRUCTURE DEPENDENCY  — the archive is not encrypted
```

`tools/backup.py` takes a snapshot of `TARA_DATA_DIR`, verifies it, and restores
it. Every archive carries a manifest with a per-file SHA-256, so verification
checks the **content** rather than that the file opens — a truncated gzip or a
flipped bit only shows up when the bytes are read, and finding out during a
restore is finding out too late.

```bash
python -m tools.backup --create /backups --quiesced
python -m tools.backup --list /backups
python -m tools.backup --verify /backups/tara-20260909T181508Z.tar.gz
python -m tools.backup --restore /backups/tara-….tar.gz --into /srv/tara/data
```

Exercised end to end against this repository's real data directory: 176 files,
9.7 MB, created → verified → restored → the restored copy read back through the
stores. Eight tests cover the round trip, corruption detection, refusing to
overwrite a populated directory, path-traversal defence in the archive, and
re-running the retention sweep against a restored directory.

| Item | Status |
| --- | --- |
| **What** | The whole of `TARA_DATA_DIR`. Transient files (`*.tmp`, probe files) are excluded; everything else is included, because the stores reference each other by id and a partial copy is worse than none |
| **Frequency** | At least daily. The recovery-point objective is exactly the interval — there is no WAL and no replica to close the gap. **Nothing schedules it**; add a cron entry or a timer |
| **Consistency** | Writes are atomic per file, **not** across files. `--quiesced` asserts the API is stopped and records the snapshot as point-in-time; without it the manifest records `live` and carries a warning. A live snapshot is much better than none and is not transactional, and the archive says which it is rather than leaving a restorer to assume |
| **Encryption** | **Required at rest, and not done here.** The archive contains verbatim candidate transcripts, evaluation evidence and scrypt password hashes. Encrypting it is the storage layer's job |
| **Offsite** | The tool writes a local file. Getting it somewhere that survives losing the host is the deployment's job |
| **Retention** | Shorter than, or equal to, the erasure SLA. See the conflict below |
| **Restore** | Stop the API, `--restore`, start it. Refuses a damaged archive, refuses a non-empty target without `--force`, and moves the previous contents aside rather than deleting them |
| **Verification in a real deployment** | **NOT VERIFIED — INFRASTRUCTURE DEPENDENCY.** The round trip is verified locally against real data; restoring into a provisioned environment cannot be tested from this repository |

### Backups and erasure conflict

A backup taken before an erasure **contains the erased data**. Restoring it
resurrects a candidate whose record was deleted. This repository cannot solve
that: it does not control the backups. A deployment that offers erasure must
either keep backup retention shorter than its erasure SLA, or re-run
`python -m tools.retention_cleanup` after any restore. This is documented in
[DATA_LIFECYCLE.md](DATA_LIFECYCLE.md) §13 and repeated here because it is the
easiest guarantee in the system to break by accident.

---

## Known limitations

1. **File-backed persistence.** Single-host, no horizontal scaling, no
   point-in-time recovery. The dominant limitation; §Durable persistence status.
2. **Backups are taken by nothing.** The tool exists and is tested; no
   schedule runs it, the archive is unencrypted, and it is written locally.
   A deployment that does not arrange all three has no durability.
3. **No CI/CD, no deploy automation, no image registry.** Every step is manual.
4. **No migrations.** There is no schema to migrate and no version stamped on
   the data directory.
5. **Evaluation is synchronous.** A slow model holds a request open; the worker
   is recovery, not a queue.
6. **No metrics backend.** Structured logs and request ids exist; nothing
   aggregates them, and there are no dashboards or alerts.
7. **HTTPS, WebSocket upgrade and process supervision are all infrastructure
   dependencies.** Correct configuration is assumed and unverified.
8. **Live provider verification is blocked** by an exhausted key.
9. **Not load-tested.** Verified at 3 concurrent interviews; unknown above that.
10. **In-process rate limiting.** Per worker, lost on restart.
