# Tara AI Interview

An AI-powered conversational assessment platform. A recruiter turns a job
description into a published, versioned interview; a candidate has a spoken
conversation with Tara; a reviewer gets evidence and a decision trail.

Two experiences, one backend, one repository.

```bash
make setup        # python venv + npm workspaces
make api          # backend            → http://localhost:8000
make candidate    # candidate app      → http://localhost:5173/?invite=demo
make recruiter    # recruiter console  → http://localhost:5174/recruiter
```

Or build both bundles and serve everything from one process:

```bash
npm run build && make api
# candidate  http://localhost:8000/?invite=demo
# recruiter  http://localhost:8000/recruiter
```

No `.env` is required. With no API key the whole product still runs end to end
on a deterministic mock — a key changes the quality of the follow-ups, never the
shape of the flow.

| | |
| --- | --- |
| Architecture | [ARCHITECTURE.md](ARCHITECTURE.md) |
| What is built, what is not | [BUILD_STATUS.md](BUILD_STATUS.md) |
| Which model powers what | [MODEL_EVALUATION.md](MODEL_EVALUATION.md) |
| Authentication, tenancy, the API access matrix | [PRODUCTION_SECURITY_MATRIX.md](PRODUCTION_SECURITY_MATRIX.md) |
| Retention, erasure, what is stored and for how long | [DATA_LIFECYCLE.md](DATA_LIFECYCLE.md) |
| Deploying it, and what that currently cannot promise | [DEPLOYMENT.md](DEPLOYMENT.md) · [PRODUCTION_RUNBOOK.md](PRODUCTION_RUNBOOK.md) |
| How the depth measure was calibrated | [DEPTH_CALIBRATION.md](DEPTH_CALIBRATION.md) |

---

## Layout

```
apps/
  candidate/        welcome → system check → interview → complete
  recruiter/        dashboard, interviews, candidates, review, question bank
services/
  api/              candidate + recruiter routers on one FastAPI app
  orchestrator/     the turn loop — the only unit holding a session
  ai/               the AI Model Gateway and six workloads
  data/             interviews, versions, invitations, sessions, audit
  evaluation/       deterministic scoring + analytics
packages/
  types/            domain entities, InterviewDefinition, target SQL schema
  schemas/          structured-output schemas + a small validator
  ui/               the design system both apps build from
content/            authored question banks (shipped)
data/               runtime state (never committed)
evals/              the model evaluation harness
tests/              1181 offline, plus a provider-dependent suite
```

## Testing

```bash
python -m pytest -q            # 1181 offline tests, ~2.5 min, no server, no key
python -m pytest -q -m live    # the provider-dependent suite (needs a key)
npm run -w @tara/recruiter test  # 87 console tests
make personas                  # watch a whole interview in the terminal
```

Everything offline runs against a deterministic mock: an autouse fixture sets
`TARA_LLM=mock` and clears the gateway singleton, so no test can reach a
provider whatever ran before it.

`strong` finishes in ~14 turns with ~6 follow-ups; `thin` takes ~24 with ~16.
That gap is the adaptive behaviour.

Some of the suites worth reading on their own:

| Suite | Tests | What it pins down |
| --- | --- | --- |
| `test_lifecycle.py` | 78 | the whole journey, immutability, rejoin, idempotency |
| `test_security.py` | 68 | 401 vs 403 vs 404, tenant isolation, candidate tokens, enumeration |
| `test_deployment.py` | 44 | liveness vs readiness, config validation, backup round trip |
| `test_data_lifecycle.py` | 43 | retention deadlines, erasure across the graph, failure states |

## Two things worth knowing before reading the code

**A published interview never changes.** Editing one produces a new version.
Candidates are pinned to the version they were invited to, so a session recorded
in March can be replayed and re-scored against the criteria it was judged on.

**The only runtime-generated text a candidate hears is the follow-up probe**, and
it passes format, legality and relevance gates before it can be spoken. A
rejected probe falls back to the question's authored bank — a bad generation
degrades to a safe question, never to silence.

## Security

The recruiter API requires a verified principal in every deployment. One
dependency guards every recruiter router, running five named checks —
authenticated, organization member, role allowed, resource owner, candidate
invitation scope — so adding a route cannot accidentally add an open one.

Three properties are worth stating because they shaped the design:

**The backend is authoritative.** The console has a sign-in screen and a route
gate, and neither is a security control. Curling the API directly reaches a
`401`, not a transcript.

**Ownership derives from one anchor.** `InterviewConfig.organization_id` is the
only authoritative tenant field; sessions, invitations, evaluations and versions
all resolve to it. A duplicated tenant id is a second source of truth, and the
first time the two disagree the disagreement is a breach.

**Another tenant's resource answers `404`, identically to one that does not
exist.** Confirming that an id is real tells an attacker which ids are worth
attacking.

`services/security/matrix.py` derives each of the 83 routes' real access class
from its mounted dependency graph and compares it against a hand-written table,
so a new route fails the suite until someone classifies it. Full treatment,
including the limitations, in
[PRODUCTION_SECURITY_MATRIX.md](PRODUCTION_SECURITY_MATRIX.md).

## Known limitations

Stated here rather than discovered later:

* **Persistence is file-backed JSON on a single host.** Correct under
  concurrency — atomic writes, per-file locks — and not a multi-host
  architecture. `packages/types/schema.sql` is the relational target; no code
  reads it yet. This blocks horizontal scaling and permits a single-host
  deployment with explicit safeguards ([DEPLOYMENT.md](DEPLOYMENT.md)).
* **Evaluation is synchronous**, in a worker thread of the request.
  `tools/evaluation_worker.py` recovers records a crash left pending; it is not
  a queue.
* **No deployed environment.** Every deployment artefact exists and is tested
  locally; nothing has been verified against a provisioned host
  ([STAGING_ACCEPTANCE_REPORT.md](STAGING_ACCEPTANCE_REPORT.md)).
* **Backups are taken by nothing.** `tools/backup.py` snapshots, verifies and
  restores the data directory, and no schedule runs it.
* **English only.** The language list is served from what the runtime actually
  supports rather than from an aspiration.
