# Data lifecycle

What Tara stores, why, for how long, and what happens when it goes. Written
against the code as it stands — every claim here corresponds to a named test in
`tests/test_data_lifecycle.py` (43 tests) or to a command whose output is quoted.

Two things this document deliberately does **not** say:

* It does not claim regulatory compliance. A deletion mechanism is not GDPR,
  CCPA or anything else; those are obligations about process, notice and lawful
  basis, most of which live outside a codebase. What is here is a working
  erasure path, an enforced retention period, and an honest account of both.
* It does not claim erasure the implementation cannot guarantee. §13 lists the
  copies this system does not control.

Throughout, **implemented** means "in the code and covered by a test", and
**infrastructure dependency** means "the code is ready and something outside the
repository has to be configured".

---

## 1. Data inventory

Every persisted object, established by reading the code and the on-disk data —
not from an architecture diagram.

### The actual dependency graph

```
Organization ──┬── User ──── LoginSession
               │
               └── InterviewConfig ─────── Job (org_id)
                      │  (the ONE tenancy anchor)
                      ├── InterviewVersion  (immutable, published)
                      │
                      └── Invitation  ← the candidate anchor
                             │
                             └── SessionState
                                   ├── transcript  (in the session file)
                                   ├── records     (answers, probes, coverage)
                                   ├── session trail   data/audit/{sid}.jsonl
                                   └── EvaluationRecord
                                         ├── snapshot  ← A FROZEN TRANSCRIPT
                                         ├── evidence  (verbatim quotes)
                                         ├── result    (scores, report)
                                         └── Review    (pilot_reviews/)
```

Two things differ from the diagram in the brief, and both matter:

* **There is no separate `Turn`, `Transcript`, `CandidateRecord` or `Evidence`
  table.** Turns and the transcript are lists *inside* the session document;
  evidence is a list inside the evaluation document. So "delete the turns" is
  not a separate operation — deleting the session file removes them.
* **`EvaluationRecord.snapshot` is a complete second copy of the transcript**,
  frozen at evaluation time so a result is reproducible. This is the copy an
  erasure would miss. `test_the_frozen_evaluation_snapshot_goes_too` exists
  because of it.

### Every file

| Location | Contents | Candidate data? |
| --- | --- | --- |
| `data/organizations.json` | tenant id, name, status | no |
| `data/users.json` | recruiter email, scrypt hash, role | no (recruiter) |
| `data/auth_sessions.json` | opaque login tokens, timestamps | no |
| `data/jobs.json` | job records, `org_id` | no |
| `data/interviews.json` | drafts: JD, skills, tasks, `organization_id` | no |
| `data/interview_versions.json` | published, immutable definitions + questions | no |
| `data/invites.json` | **`candidate_name`, `candidate_id`, `recipient`, `note`**, token, lifecycle | **yes** |
| `data/sessions/{sid}.json` | **verbatim transcript, every answer, `candidate_name`**, `session_grant`, `invite_token` | **yes** |
| `data/audit/{sid}.jsonl` | the decision trail: classifier labels, word counts, **Tara's generated probes** | **partly** — see below |
| `data/evaluations/{eid}.json` | **`snapshot.turns` (frozen transcript)**, **`evidence[].quote`**, `result`, `model_meta` | **yes** |
| `data/evaluations/.locks/{sid}.lock` | the session id | no content |
| `data/pilot_reviews/{eid}__{reviewer}.json` | a reviewer's note plus the score they saw | **derived** |
| `data/pilot_runs.json` | run label, notes, `organization_id` | no |
| `data/audit/_product.jsonl` | security + product trail | see §10 |

**The session trail holds no verbatim answers.** Verified: the classifier writes
`intent`, `depth`, `affect`, `covered` and a word count. It *does* hold Tara's
generated probes, which are written from an answer and can paraphrase it
closely — which is why the trail is deleted with the transcript rather than kept.

**Not persisted anywhere:** audio (never stored; the transport is Retell's and
the assessment engine never receives it), uploaded files (the product has no
upload), Redis (not used — see §8), and any browser-side copy beyond the page
the candidate is currently on.

---

## 2. Data classification

| Class | Examples | Owner | Purpose | Sensitivity | Retention | On erasure |
| --- | --- | --- | --- | --- | --- | --- |
| **Candidate** | name, contact, transcript, answers, evidence quotes, evaluation result | the candidate; held by the organization | conduct and evidence one assessment | high | `CANDIDATE_DATA_RETENTION_DAYS` (180) | erased; invitation row redacted to a tombstone |
| **Recruiter / organization** | recruiter identity, org, job description, interview draft and configuration | the organization | run hiring | medium | kept while the organization exists | untouched |
| **Product configuration** | published `InterviewVersion`, questions, skills, tasks, scoring configuration | the product | make an assessment reproducible and defensible | low | kept — immutability is the point | **untouched** (§7) |
| **Audit / security** | actor, action, resource, timestamp, login events | the deployment | prove what happened | medium; no candidate content | `AUDIT_RETENTION_DAYS` (400) | rows kept, outcome fields redacted (§10) |

The four classes are drawn from what the repository actually stores. No legal
category is asserted, because nothing in the product requirements establishes
one.

---

## 3. Retention policy

One place: `services/config.py`. Nothing else in the codebase holds a duration.

| Variable | Default | What it governs |
| --- | --- | --- |
| `CANDIDATE_DATA_RETENTION_DAYS` | 180 | the candidate record, and therefore the whole subtree |
| `TRANSCRIPT_RETENTION_DAYS` | 90 | the verbatim transcript specifically; clamped to the candidate period |
| `EVALUATION_RETENTION_DAYS` | 180 | evaluations, evidence, results; clamped to the candidate period |
| `AUDIT_RETENTION_DAYS` | 400 | the security trail — deliberately longer than everything it describes |
| `RETENTION_SWEEP_INTERVAL_HOURS` | 24 | **not a retention period** — how often cleanup is expected to run |

These are **pilot defaults, and they are choices**. Nothing in the product
specification names a duration, so 180 days was picked to cover a hiring round
plus the window in which a rejected candidate might be reconsidered, and the
transcript's 90 is shorter because it is the most sensitive thing held and the
least useful after a decision. A deployment with a legal or contractual
obligation sets its own; a value of 0 or an unparseable one falls back to the
default rather than meaning "delete immediately".

**Retention period ≠ cleanup interval.** The period is policy: when a record
*becomes* eligible. The interval is implementation: how often something looks.
A record that is eligible and has not been swept is still eligible, and
`retention.eligible()` will say so — because eligibility is computed from
timestamps on every call, never read from a flag someone had to remember to set.
`test_a_record_past_its_deadline_becomes_eligible_without_anyone_marking_it`.

**Currently a single period drives erasure.** `TRANSCRIPT_RETENTION_DAYS` and
`EVALUATION_RETENTION_DAYS` are defined, clamped and reported, but the erasure
path deletes the candidate subtree as one unit on the candidate deadline. A
staged expiry — transcript at 90 days, result at 180 — is a known limitation
(§14), not a claim.

---

## 4. Lifecycle states

Five, on `Invite.lifecycle`:

```
ACTIVE ──────────► RETENTION_ELIGIBLE ──┐
   │  (the clock)                       │
   │                                    ├──► DELETED
   └──► DELETION_REQUESTED ─────────────┤     (verified empty)
              (a human asked)           │
                                        └──► DELETION_FAILED ──► retry ──► DELETED
                                              (data still present)
```

**Why a second field rather than reusing `Invite.status`.** `status` is about
whether the *invitation* can be used (`created → active → in_progress →
complete`, or `revoked`); `lifecycle` is about whether the *data* still exists.
A finished interview is `complete` for as long as the record is kept and
`deleted` afterwards. Collapsing them would make "this candidate finished" and
"this candidate's transcript is gone" the same fact.

**Why the invitation is the anchor.** It is the only row that exists across the
whole candidate lifecycle: before a session, after one, and after the session is
erased. A candidate who never opened their link still has a name on file, and
still needs a deadline.

`ACTIVE` versus `RETENTION_ELIGIBLE` is **computed** from the clock; the three
terminal states are **read** from the record. That asymmetry is deliberate:
"deleted" is a fact someone wrote down, "eligible" is a fact about the current
time, and computing it means a sweep that has not run cannot make an expired
record look retained.

**`DELETION_FAILED` is what makes the rest trustworthy.** Without it, a partial
deletion has to be reported as success (a lie) or as nothing (a silent leak).

---

## 5. Retention calculation

`services/data/retention.py`.

```
anchor_time(invite)  = max(created_at, completed_at, revoked_at)
deadline(invite)     = anchor_time + CANDIDATE_DATA_RETENTION_DAYS × 86400
is_expired(invite)   = now >= deadline
```

Latest-wins among the three moments **the server itself recorded**, because the
clock should start when the record stopped changing: `completed_at` for the
normal case, `revoked_at` when a recruiter withdrew it, `created_at` for an
invitation nobody ever opened — which otherwise would never become eligible at
all.

Deliberately **not** used: browser timestamps, candidate-supplied values, and
any "last viewed" time. A candidate who could influence their own deadline could
keep their transcript alive indefinitely or have it deleted before a decision
was made; a deadline that moves when a recruiter opens a report never arrives
for a popular candidate.

Deterministic: the same record and the same policy always give the same number.
`test_the_deadline_is_deterministic_and_comes_from_server_timestamps`.

---

## 6. Deletion workflow

`services/data/erasure.py`. Server-authoritative, idempotent, verified,
auditable, retryable.

```
erase(token)
   │
   ├─ already DELETED?  →  verify anyway; return already_deleted
   ├─ attempts += 1, persist DELETION_REQUESTED
   │
   ├─ remove evaluations        (each holds a frozen transcript + quotes)
   ├─ remove pilot reviews      (they carry the score that was on screen)
   ├─ remove session state      (the transcript and every answer)
   ├─ remove session trail      (classifier labels and generated probes)
   ├─ remove evaluation lock
   ├─ redact product-log outcomes
   ├─ redact the invitation row (tombstone, revoked)
   │
   ├─ verify()  →  re-reads every location
   │      ├─ empty      → DELETED,        audit CANDIDATE_DATA_ERASED
   │      └─ not empty  → DELETION_FAILED, audit CANDIDATE_DATA_ERASURE_FAILED
   └─ any exception → DELETION_FAILED with an operator-language reason
```

**Verification re-reads the stores** rather than trusting the return values of
the deletions — "delete said it worked" is the claim under test.

**Idempotency** in the way that matters: a second call does not fail, does not
recreate anything, and does not write a second audit event
(`test_running_erasure_twice_is_safe`). A record marked `deleted` whose data is
still present is **reopened** rather than short-circuited
(`test_a_record_marked_deleted_that_still_holds_data_is_reopened`).

**The invitation becomes a tombstone rather than disappearing.** `candidate_name`,
`candidate_id`, `recipient`, `note` and `session_id` are cleared and the row is
revoked. Three reasons the row stays: the audit trail references this token and
a dangling reference proves nothing; a removed row could be re-created by a
replayed request; and the token must stay revoked so the emailed link is dead
(`test_the_erased_link_can_no_longer_start_an_interview`).

---

## 7. Dependency deletion order

**Leaves first, anchor last.**

```
evaluations → pilot reviews → session state → session trail
→ lock → product-log redaction → invitation
```

The invitation is marked `deleted` only after verification passes. A crash half
way through leaves a record in `deletion_requested` with its dependents partly
gone — which the next sweep finds and finishes. The opposite order would mark
the anchor deleted and lose the pointer to whatever remained.

### What survives, and why

**Published `InterviewVersion` is never touched.** A candidate sitting an
interview does not make the interview theirs. Deleting v1 because one of forty
candidates asked for erasure would destroy the definition the other thirty-nine
were assessed against — and the evidence that the assessment was fair. Verified
by checksum: `test_the_published_interview_version_survives_erasure`.

Also untouched: organizations, users, jobs, interview drafts, pilot runs, and
every other candidate's data (`test_other_candidates_are_untouched`).

---

## 8. Cache and transient state

**There is no Redis.** `REDIS_URL` is read in `config.py` and is empty; nothing
in the codebase connects to it. It is not introduced here because the
architecture document mentions it — the phase brief explicitly says not to.

Transient state, in full:

| Object | Created | TTL | Deleted |
| --- | --- | --- | --- |
| `.tmp` files from atomic writes | during any store write | none — they are renamed over the target within the same call | never observed to persist; asserted by `test_no_transient_file_outlives_the_data_it_describes` |
| `data/evaluations/.locks/{sid}.lock` | when an evaluation is requested | none | by erasure (`test_the_evaluation_lock_for_an_erased_session_is_gone`) |
| Login sessions | at sign-in | 12h idle / 7d absolute, enforced on read | pruned on access; revoked on logout |
| Candidate `session_grant` | at session start | lives in the session document | with the session |
| In-process rate-limit counters | per request | one window (60–300s) | on process restart |

Candidate session state **is** persisted between turns and rejoins — that is the
file-backed session document, and it is inside the lifecycle, not outside it.

---

## 9. Authorization

No new machinery. The lifecycle routes are mounted under the recruiter prefix,
so they inherit `security.recruiter_scope`, and `{token}` is one of the path
parameters `authz.RESOLVERS` resolves — tenant ownership is checked before any
handler runs.

```
authenticated actor  →  organization member  →  role allows `delete`
   →  invitation belongs to that organization  →  erasure
```

| Route | Class | Who |
| --- | --- | --- |
| `GET /api/recruiter/retention` | RECRUITER_AUTHENTICATED | any signed-in role |
| `GET /api/recruiter/retention/candidates` | RECRUITER_AUTHENTICATED | any signed-in role |
| `POST /api/recruiter/retention/sweep` | RECRUITER_AUTHENTICATED + `delete` | admin |
| `GET /api/recruiter/candidates/{token}/data` | RECRUITER_ORGANIZATION_SCOPED | any signed-in role |
| `DELETE /api/recruiter/candidates/{token}/data` | RECRUITER_ORGANIZATION_SCOPED + `delete` | **admin only** |

**Only an administrator can erase.** The guard maps `DELETE` to the `delete`
capability, which only `admin` carries. Not a new role — the repository already
draws this line for deleting an interview: a recruiter can publish and invite,
an administrator can destroy records.

**Cross-tenant deletion is impossible**, and the refusal is a non-disclosing
`404` matching the tenancy model in `PRODUCTION_SECURITY_MATRIX.md` §3.
`test_one_organization_cannot_erase_anothers_candidate` attempts A → delete B
and then asserts B's session, trail, evaluations, invitation and report are all
intact and still readable by B's own recruiter.

A candidate cannot reach any of it: `401` on every route, with or without their
invitation token (`test_a_candidate_cannot_erase_anything`).

---

## 10. Audit behaviour

Three events, on the **product** log — never on the session's own trail, because
that trail is one of the things erasure deletes, and recording the erasure there
would delete the proof along with the data.

| Event | Recorded |
| --- | --- |
| `CANDIDATE_DATA_ERASED` | actor, organization, session id, interview id, per-location removal counts, attempt number, truncated token |
| `CANDIDATE_DATA_ERASURE_FAILED` | the same, plus the locations still holding data and an operator-language reason |
| `RETENTION_SWEEP_COMPLETED` | eligible / erased / failed counts and duration |

What never appears: transcripts, answers, evidence quotes, candidate names, full
tokens (the subject is `abcd1234…`), API keys, authorization headers.
`test_erasure_writes_an_audit_event_that_carries_no_candidate_content` asserts
each of those by name, and
`test_a_failure_reason_carries_no_candidate_content` injects an exception whose
*payload quotes the candidate* — the realistic leak — and checks it does not
reach the log.

**The product log is redacted, not truncated.** Every row survives, in place, in
order; only the fields naming an assessment outcome are replaced with
`[erased]`: `total_score`, `maximum_possible_score`, `overall_rating`,
`recommendation`, `percentage`, `skills`, `evidence_items`, `band`,
`band_label`. Rewriting an append-only log is a real trade-off and it is made
deliberately: a trail with rows cut out of it cannot prove a deletion happened,
and a trail that keeps a candidate's score is a surviving fragment of the record
that was erased. The rewrite is atomic, and a torn line is left exactly as
found. `test_the_product_audit_log_keeps_its_events_and_loses_the_outcome`.

---

## 11. Failure and retry

A partial deletion is **never** reported as complete.

* Any exception → `DELETION_FAILED`, with `deletion_error` (the exception's
  *type*, never its payload) and `deletion_remaining` (location names only).
* Verification finding anything → `DELETION_FAILED` with the locations named.
* The API answers `409`, not `200`, with the same detail
  (`test_the_api_refuses_to_report_success_on_a_partial_deletion`).
* A failed record **stays in the sweep**: `DELETION_FAILED` is in `SWEEPABLE`,
  because a failed deletion is outstanding work and a sweep that skipped it
  would abandon the one record that needs attention.
* Retrying is just calling again. `test_a_failed_deletion_is_retryable_and_completes`
  injects one transient failure and confirms the second attempt finishes,
  increments `deletion_attempts`, and verifies clean.

Nothing is swallowed: every failure writes state, writes an audit event, and
returns `ok=False`.

---

## 12. Operational cleanup

```bash
python -m tools.retention_cleanup --status     # counts, deletes nothing
python -m tools.retention_cleanup --dry-run    # what would go
python -m tools.retention_cleanup              # do it
python -m tools.retention_cleanup --token <t>  # one candidate, on request
```

Exit `0` when nothing is eligible or everything verified clean; exit `1` when
any erasure failed, so a scheduler can alert on it.

Safe to run twice, and safe to run while another copy is running: every erasure
is idempotent and the stores serialise their own writes.

**It does not schedule itself.** Cron, a systemd timer or the platform's
scheduler runs it — building a scheduler into a process that may run as several
replicas would mean several sweeps a day per replica. Setting that schedule is
an **infrastructure dependency**; `RETENTION_SWEEP_INTERVAL_HOURS` documents the
expected cadence (24h), and `POST /api/recruiter/retention/sweep` gives an
administrator the same operation from a browser.

### Retention simulation

`test_the_retention_simulation` builds one of each interesting record and runs a
single sweep with one injected storage failure:

| Record | Before | Eligible? | Cleanup result | After |
| --- | --- | --- | --- | --- |
| fresh candidate | active, data present | no | not touched | active, data present |
| expired candidate | active, past deadline | yes | erased + verified | `deleted`, nothing remains |
| already deleted | `deleted` | no | not swept again | `deleted`, nothing remains |
| failed deletion | active, past deadline | yes | **failed**, reason recorded | `deletion_failed`, data still present |
| candidate with evaluation | active, past deadline | yes | erased incl. snapshot + evidence | `deleted` |
| candidate without evaluation | active, past deadline | yes | erased | `deleted` |
| invited, never sat | active, past deadline | yes | tombstoned | `deleted` |

A second sweep, with the fault removed, erases the failed one — one line in the
same test.

---

## 13. Known infrastructure dependencies

Things the code is ready for and something outside the repository must do.
None of these are claimed as done.

| Dependency | Status |
| --- | --- |
| **A scheduler to run the sweep** | INFRASTRUCTURE DEPENDENCY — nothing runs `tools.retention_cleanup` automatically today |
| **Backups** | NOT VERIFIED — no backup is configured, and a backup taken before an erasure would contain the erased data. Any backup policy has to include an erasure-propagation rule; there is none to describe yet |
| **Filesystem-level deletion** | The code unlinks files. Whether the bytes are recoverable from the underlying disk, snapshot or volume is the storage layer's property, not this application's |
| **Log shipping** | If a deployment ships stdout elsewhere, the boot banner's demo link and any platform-level request logging are outside this lifecycle |
| **Retell (voice transport)** | Audio never reaches Tara's storage, but the transport provider's own retention is theirs. Not verified here |
| **OpenRouter** | Prompts containing candidate answers are sent to the provider at evaluation time. Their retention is governed by the provider's policy, not by this deletion path |

The last two are the honest limit on the word "erasure": **Tara can erase its own
copies, and cannot erase a provider's.**

---

## 14. Known limitations

1. **One period drives erasure.** `TRANSCRIPT_RETENTION_DAYS` and
   `EVALUATION_RETENTION_DAYS` are configured, clamped and reported, but the
   subtree is deleted as one unit on the candidate deadline. Staged expiry is
   not implemented.
2. **No candidate-facing privacy controls.** A candidate cannot request erasure
   themselves; the phase brief rules it out, and the candidate experience is
   unchanged. Requests arrive through a recruiter or an administrator.
3. **The sweep is single-process.** Safe to run concurrently, but there is no
   claiming or leasing — two sweeps do duplicate work rather than sharing it.
4. **Erasure is not undoable.** There is no soft-delete window and no restore.
5. **The audit trail is never pruned.** `AUDIT_RETENTION_DAYS` is defined and
   reported; nothing enforces it. The trail grows without bound.
6. **Redaction rewrites the product log.** Atomic and order-preserving, but a
   rewrite nonetheless — an append-only log on a WORM volume would refuse it.
7. **`data/invites.json` carries ~5,100 orphaned rows** from a test-harness
   defect fixed in the previous phase (the suite wrote to the real data
   directory). They point at interviews that no longer exist, so they are
   invisible to the product and to tenancy; they were left in place rather than
   deleted unilaterally. `python -m tools.retention_cleanup --status` counts
   them.
