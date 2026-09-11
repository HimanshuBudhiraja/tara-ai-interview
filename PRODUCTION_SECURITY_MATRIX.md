# Production security matrix

What Tara enforces, where it is enforced, and how each claim was checked. Written
after the authorization phase, against the code as it stands — every statement
here corresponds to a named test in `tests/test_security.py` (95 tests) or to a
command whose output is quoted.

Two ideas run through all of it, and they are worth stating before the tables:

**The backend is authoritative.** The recruiter console has a sign-in screen and
a route gate, and neither is a security control. Editing the bundle, or curling
the API directly, reaches a `401` rather than a transcript. Hiding a screen is
presentation; refusing a request is security.

**The principal comes only from verified state.** It is rebuilt from the server's
own session store on every request. No header, query parameter, body field or
piece of frontend state can name the caller or their organization —
`test_identity_cannot_be_asserted_by_the_client` presents four such attempts and
every one gets a `401`.

---

## 1. Identity model

| Concept | Where it lives | Notes |
| --- | --- | --- |
| Organization | `data/organizations.json` (`services/data/accounts.py`) | The tenant. One `organization_id`, active or suspended. |
| User | `data/users.json` | A recruiter-side human: email, scrypt password hash, role, status, organization. Never a candidate. |
| Login session | `data/auth_sessions.json` | Server-side and revocable. The cookie holds an opaque 43-byte token; every attribute of the principal is read from this store, not from the cookie. |
| Candidate | An `Invitation` row plus a `SessionState` | Deliberately **not** a user. A candidate has no account, no password and no way to sign in, because "the candidate has an account" is a class of feature — password resets, enumeration, credential stuffing — that the product does not need. |
| Candidate credential | `invite_token` (43 bytes) and `session_grant` (minted at start) | Two secrets, not one. See §6. |

Roles and what they carry (`accounts.CAPABILITIES`):

| Role | read | write | publish | invite | delete | audit | admin |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `admin` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `recruiter` | ✅ | ✅ | ✅ | ✅ | — | — | — |
| `viewer` | ✅ | — | — | — | — | — | — |

## 2. Authentication

* **Password hashing** — scrypt from the standard library, `n=2^14, r=8, p=1`,
  32-byte key, per-user random salt, stored as `scrypt$n$r$p$salt$hash`.
  Verification is constant-time (`hmac.compare_digest`); a malformed stored hash
  verifies as `False` rather than raising.
* **No user enumeration** — `accounts.authenticate` returns one `None` for every
  failure, and hashes against a dummy record when the address is unknown so the
  timing of "no such user" matches "wrong password".
  `test_an_unknown_address_and_a_wrong_password_are_indistinguishable` asserts
  the two responses are byte-identical.
* **Sessions** — opaque token, 12-hour idle timeout, 7-day absolute timeout,
  both enforced server-side on read. Logout revokes the row, so a captured
  cookie is dead afterwards
  (`test_logging_out_revokes_the_session_server_side` replays it and gets `401`).
* **Disabling a user takes effect immediately** — the principal is rebuilt from
  the user record per request, so a live session stops working the moment the
  account is disabled
  (`test_a_disabled_user_cannot_sign_in_and_their_sessions_stop_working`).
* **Cookie attributes** — `HttpOnly`, `SameSite=Lax`, `Secure` whenever
  `TARA_COOKIES_SECURE` or a production environment is set, `Path=/`. One
  function (`principal.candidate_cookie_kwargs`) sets both cookies, so neither
  can drift insecure on its own.
* **`Authorization: Bearer <session token>`** is accepted as an alternative to
  the cookie, for clients that have no cookie jar. It is the same token and the
  same store — not a second credential type.
* **A future IdP** — `Authenticator` is a Protocol with one implementation
  (`PasswordAuthenticator`). SSO replaces that class; nothing above it changes.

## 3. Authorization model

Five named checks, in this order:

```
AUTHENTICATED               there is a verified principal at all           → 401
ORGANIZATION_MEMBER         the principal's organization is active         → 403
ROLE_ALLOWED                the role carries the capability for the method → 403
RESOURCE_OWNER              the resource belongs to that organization      → 404
CANDIDATE_INVITATION_SCOPE  a candidate reaching only their own session    → 404
```

Enforced by one dependency, `security.recruiter_scope`, mounted on every
recruiter router in `services/api/app.py` rather than written into handlers.
Adding a route cannot accidentally add an unprotected one, and
`test_no_recruiter_route_is_reachable_without_the_guard` walks the live route
table to prove it — for the `/api/admin` alias too.

**Ownership derives through one anchor.** `InterviewConfig.organization_id` is
the only authoritative tenant field; sessions, invitations, evaluations and
versions all resolve to it through `interview_id`. A duplicated tenant id would
be a second source of truth, and the first time the two disagreed the
disagreement would be a breach.

```
Invitation ─┐
Session ────┼─► interview_id ─► InterviewConfig.organization_id
Evaluation ─┘
InterviewVersion ─► interview_id ─► …
```

**401 vs 403 vs 404.** `401` means "we do not know who you are" and carries
`WWW-Authenticate: Bearer`, so a client knows signing in would help. `403` means
"we know, and your role does not allow this" — visible on purpose, because
hiding it would only confuse a legitimate colleague. `404` means "not yours, or
not there", and the two are indistinguishable by design:
`test_a_foreign_resource_and_a_nonexistent_one_answer_identically` asserts equal
status *and* equal body.

## 4. API access matrix

75 routes, every one classified. The classification is not a document that can
drift: `services/security/matrix.py` derives each route's real class from its
mounted dependency graph and compares it with the declared table, and
`test_every_route_is_classified_and_matches_what_the_code_enforces` fails on any
disagreement — including a new route nobody classified.

Regenerate with:

```bash
python -c "from services.api.app import app; from services.security import matrix; print(matrix.table(app))"
```

| Method | Path | Access class | Enforced by |
| --- | --- | --- | --- |
| GET | `/admin` | INTERNAL_ONLY | — (no credential) |
| GET | `/admin/{full_path:path}` | INTERNAL_ONLY | — (no credential) |
| POST | `/api/auth/login` | PUBLIC | `rate_limit[login]` |
| POST | `/api/auth/logout` | PUBLIC | handler: revokes whatever session the caller presents and always succeeds |
| GET | `/api/auth/me` | RECRUITER_AUTHENTICATED | `current_principal` |
| GET | `/api/demo/prompts` | PUBLIC | — (no credential) |
| GET | `/api/health` | PUBLIC | — (no credential) |
| GET | `/api/invite/{token}` | CANDIDATE_TOKEN_SCOPED | handler: invites.get(token) — an unguessable 43-byte token IS the credential |
| POST | `/api/invite/{token}/precheck` | CANDIDATE_TOKEN_SCOPED | handler: same token, read-only |
| GET | `/api/recruiter/candidates` | RECRUITER_AUTHENTICATED | `recruiter_scope` |
| POST | `/api/recruiter/candidates` | RECRUITER_AUTHENTICATED | `recruiter_scope` |
| POST | `/api/recruiter/compare` | RECRUITER_AUTHENTICATED | `recruiter_scope` |
| GET | `/api/recruiter/evaluations/{evaluation_id}` | RECRUITER_ORGANIZATION_SCOPED | `recruiter_scope` |
| GET | `/api/recruiter/evaluations/{evaluation_id}/result` | RECRUITER_ORGANIZATION_SCOPED | `recruiter_scope` |
| GET | `/api/recruiter/fairness` | RECRUITER_AUTHENTICATED | `recruiter_scope` |
| GET | `/api/recruiter/interviews` | RECRUITER_AUTHENTICATED | `recruiter_scope` |
| POST | `/api/recruiter/interviews` | RECRUITER_AUTHENTICATED | `recruiter_scope` |
| POST | `/api/recruiter/interviews/generate` | RECRUITER_AUTHENTICATED | `rate_limit[generation]`, `recruiter_scope` |
| DELETE | `/api/recruiter/interviews/{interview_id}` | RECRUITER_ORGANIZATION_SCOPED | `recruiter_scope` |
| GET | `/api/recruiter/interviews/{interview_id}` | RECRUITER_ORGANIZATION_SCOPED | `recruiter_scope` |
| PATCH | `/api/recruiter/interviews/{interview_id}` | RECRUITER_ORGANIZATION_SCOPED | `recruiter_scope` |
| GET | `/api/recruiter/interviews/{interview_id}/draft` | RECRUITER_ORGANIZATION_SCOPED | `recruiter_scope` |
| PATCH | `/api/recruiter/interviews/{interview_id}/draft` | RECRUITER_ORGANIZATION_SCOPED | `recruiter_scope` |
| POST | `/api/recruiter/interviews/{interview_id}/extract` | RECRUITER_ORGANIZATION_SCOPED | `recruiter_scope` |
| GET | `/api/recruiter/interviews/{interview_id}/invitations` | RECRUITER_ORGANIZATION_SCOPED | `recruiter_scope` |
| POST | `/api/recruiter/interviews/{interview_id}/invitations` | RECRUITER_ORGANIZATION_SCOPED | `recruiter_scope` |
| POST | `/api/recruiter/interviews/{interview_id}/invitations/open-link` | RECRUITER_ORGANIZATION_SCOPED | `recruiter_scope` |
| DELETE | `/api/recruiter/interviews/{interview_id}/invitations/{token}` | RECRUITER_ORGANIZATION_SCOPED | `recruiter_scope` |
| POST | `/api/recruiter/interviews/{interview_id}/publish` | RECRUITER_ORGANIZATION_SCOPED | `recruiter_scope` |
| GET | `/api/recruiter/interviews/{interview_id}/publish/check` | RECRUITER_ORGANIZATION_SCOPED | `recruiter_scope` |
| GET | `/api/recruiter/interviews/{interview_id}/questions` | RECRUITER_ORGANIZATION_SCOPED | `recruiter_scope` |
| POST | `/api/recruiter/interviews/{interview_id}/questions` | RECRUITER_ORGANIZATION_SCOPED | `recruiter_scope` |
| POST | `/api/recruiter/interviews/{interview_id}/questions/generate` | RECRUITER_ORGANIZATION_SCOPED | `rate_limit[generation]`, `recruiter_scope` |
| GET | `/api/recruiter/interviews/{interview_id}/questions/validate` | RECRUITER_ORGANIZATION_SCOPED | `recruiter_scope` |
| DELETE | `/api/recruiter/interviews/{interview_id}/questions/{question_id}` | RECRUITER_ORGANIZATION_SCOPED | `recruiter_scope` |
| PATCH | `/api/recruiter/interviews/{interview_id}/questions/{question_id}` | RECRUITER_ORGANIZATION_SCOPED | `recruiter_scope` |
| POST | `/api/recruiter/interviews/{interview_id}/questions/{question_id}/regenerate` | RECRUITER_ORGANIZATION_SCOPED | `recruiter_scope` |
| POST | `/api/recruiter/interviews/{interview_id}/regenerate` | RECRUITER_ORGANIZATION_SCOPED | `rate_limit[generation]`, `recruiter_scope` |
| POST | `/api/recruiter/interviews/{interview_id}/reset_skills` | RECRUITER_ORGANIZATION_SCOPED | `recruiter_scope` |
| GET | `/api/recruiter/interviews/{interview_id}/results` | RECRUITER_ORGANIZATION_SCOPED | `recruiter_scope` |
| POST | `/api/recruiter/interviews/{interview_id}/test_run` | RECRUITER_ORGANIZATION_SCOPED | `recruiter_scope` |
| GET | `/api/recruiter/interviews/{interview_id}/versions` | RECRUITER_ORGANIZATION_SCOPED | `recruiter_scope` |
| GET | `/api/recruiter/interviews/{interview_id}/versions/{version}` | RECRUITER_ORGANIZATION_SCOPED | `recruiter_scope` |
| GET | `/api/recruiter/languages` | RECRUITER_AUTHENTICATED | `recruiter_scope` |
| GET | `/api/recruiter/overview` | RECRUITER_AUTHENTICATED | `recruiter_scope` |
| GET | `/api/recruiter/pilot/dataset` | RECRUITER_AUTHENTICATED | `recruiter_scope` |
| GET | `/api/recruiter/pilot/runs` | RECRUITER_AUTHENTICATED | `recruiter_scope` |
| POST | `/api/recruiter/pilot/runs` | RECRUITER_AUTHENTICATED | `recruiter_scope` |
| POST | `/api/recruiter/pilot/runs/{pilot_run_id}/stop` | RECRUITER_AUTHENTICATED | `recruiter_scope` |
| GET | `/api/recruiter/pilot/stability` | RECRUITER_AUTHENTICATED | `recruiter_scope` |
| GET | `/api/recruiter/pilot/summary` | RECRUITER_AUTHENTICATED | `recruiter_scope` |
| GET | `/api/recruiter/pool` | RECRUITER_AUTHENTICATED | `recruiter_scope` |
| GET | `/api/recruiter/sessions/{session_id}` | RECRUITER_ORGANIZATION_SCOPED | `recruiter_scope` |
| GET | `/api/recruiter/sessions/{session_id}/evaluation` | RECRUITER_ORGANIZATION_SCOPED | `recruiter_scope` |
| POST | `/api/recruiter/sessions/{session_id}/evaluation` | RECRUITER_ORGANIZATION_SCOPED | `rate_limit[evaluation]`, `recruiter_scope` |
| GET | `/api/recruiter/sessions/{session_id}/evaluation/evidence` | RECRUITER_ORGANIZATION_SCOPED | `recruiter_scope` |
| GET | `/api/recruiter/sessions/{session_id}/evaluation/result` | RECRUITER_ORGANIZATION_SCOPED | `recruiter_scope` |
| GET | `/api/recruiter/sessions/{session_id}/evaluations` | RECRUITER_ORGANIZATION_SCOPED | `recruiter_scope` |
| GET | `/api/recruiter/sessions/{session_id}/review` | RECRUITER_ORGANIZATION_SCOPED | `recruiter_scope` |
| POST | `/api/recruiter/sessions/{session_id}/review` | RECRUITER_ORGANIZATION_SCOPED | `recruiter_scope` |
| GET | `/api/recruiter/sessions/{session_id}/score` | RECRUITER_ORGANIZATION_SCOPED | `recruiter_scope` |
| GET | `/api/recruiter/sessions/{session_id}/trail` | RECRUITER_ORGANIZATION_SCOPED | `recruiter_scope` |
| POST | `/api/session/start` | CANDIDATE_TOKEN_SCOPED | handler: invites.can_start(token) before any session is minted |
| GET | `/api/session/{session_id}` | CANDIDATE_TOKEN_SCOPED | `candidate_scope` |
| POST | `/api/session/{session_id}/turn` | CANDIDATE_TOKEN_SCOPED | `candidate_scope`, `rate_limit[turn]` |
| MOUNT | `/assets` | INTERNAL_ONLY | — (no credential) |
| GET | `/docs` | INTERNAL_ONLY | — (no credential) |
| GET | `/docs/oauth2-redirect` | INTERNAL_ONLY | — (no credential) |
| GET | `/openapi.json` | INTERNAL_ONLY | — (no credential) |
| GET | `/recruiter` | INTERNAL_ONLY | — (no credential) |
| MOUNT | `/recruiter/assets` | INTERNAL_ONLY | — (no credential) |
| GET | `/recruiter/{full_path:path}` | INTERNAL_ONLY | — (no credential) |
| GET | `/redoc` | INTERNAL_ONLY | — (no credential) |
| WS | `/ws/interview/{session_id}` | CANDIDATE_TOKEN_SCOPED | handler: _socket_is_authorised(ws, state) as the first act of the handler |
| GET | `/{full_path:path}` | INTERNAL_ONLY | — (no credential) |
## 5. Tenant isolation

Detail routes resolve the path id through `authz` and answer `404` when it
belongs to another organization. List and aggregate routes are filtered, which
is the half that is easy to forget: a list endpoint returning every row leaks as
much as a detail endpoint with no check.

| Surface | How it is scoped | Test |
| --- | --- | --- |
| Interview detail / edit / delete / publish / questions / versions / invitations | `enforce_path_ownership` → `authz.interview` | `test_another_organizations_interview_is_not_found` (13 method/path pairs) |
| Interview list | `authz.visible_interviews` | `test_the_interview_list_shows_only_one_organization` |
| Candidate list | filtered by `visible_interview_ids` | `test_the_candidate_list_shows_only_one_organizations_candidates` |
| Overview counters | same filter | `test_the_overview_counts_only_one_organization` |
| Session review / trail / score / evaluation / evidence / review | `authz.session` | `test_another_organizations_session_report_and_trail_are_not_found` |
| Evaluation by id | `authz.evaluation` | `test_another_organizations_evaluation_cannot_be_read_by_id` |
| Invitation revocation | `authz.invitation` | `test_an_invitation_token_from_another_organization_cannot_be_revoked` |
| Comparison (`session_ids` in the body) | handler resolves every id; one foreign id refuses the whole request | `test_body_named_sessions_are_all_checked_not_just_the_first` |
| Fairness (`interview_id` in the query) | handler resolves it; unscoped form uses `visible_sessions` | `test_a_query_parameter_cannot_name_another_organizations_interview` |
| Invite creation (`interview_id` in the body) | handler resolves it | `test_a_request_body_cannot_name_another_organizations_interview` |
| Pilot runs and reports | `organization_id` on `PilotRun`; reports filtered | `test_another_organizations_pilot_run_cannot_be_listed_or_stopped` |

**Three of these were defects found and fixed during this phase**, all of the
same shape — a resource id arriving somewhere the central path guard cannot see:

| Defect | Effect before the fix |
| --- | --- |
| `GET /api/recruiter/fairness?interview_id=` | Any signed-in recruiter could read another organization's fairness audit, including blocked-probe text and candidate names. |
| `POST /api/recruiter/candidates` (`interview_id` in the body) | Any signed-in recruiter could mint a working invitation into another organization's published interview. |
| Pilot runs (no tenant field at all) | One organization could list another's run labels and notes, and stop their open run. |

## 6. Candidate token security

A candidate holds two secrets, and the split is the point.

* **`invite_token`** — 43 URL-safe bytes from `secrets.token_urlsafe(32)`. It is
  the credential in the emailed link. It encodes nothing: not the interview, not
  the candidate, not a counter
  (`test_ids_are_not_sequential_and_not_derived_from_their_contents`).
* **`session_grant`** — minted server-side when the session starts and set as an
  `HttpOnly` cookie. Reading or advancing a session requires it (or the
  invitation token as a fallback, for a client with no cookie).

Why two: "the session id is hard to guess" is not authorization. The grant binds
a browser to one session, so knowing another candidate's session id achieves
nothing — `test_candidate_a_cannot_reach_candidate_bs_session` presents A's
grant *and* A's token against B's session across the read route, the turn route
and the WebSocket, and all three refuse.

| Attack | Result | Test |
| --- | --- | --- |
| No credential at all | `404` | `test_a_session_with_no_credential_at_all_is_not_found` |
| Malformed / random / traversal / null-byte token | `404`/`410`/`422` | `test_a_malformed_or_random_invitation_token_is_refused` (10 cases) |
| Expired invitation | `410` | `test_an_expired_invitation_cannot_start_or_resume` |
| Revoked invitation | `410` | `test_a_revoked_invitation_cannot_start_a_session` |
| Replaying a completed invitation | `410` | `test_a_completed_invitation_cannot_be_sat_again` |
| Candidate A → candidate B's session (HTTP and WebSocket) | `404` / socket `error` | `test_candidate_a_cannot_reach_candidate_bs_session` |
| A's grant used from another browser | opens A's session only, never B's | `test_a_candidate_grant_is_bound_to_one_session` |
| Candidate → any recruiter route, with token as cookie, bearer, header or query | `401` | `test_a_candidate_credential_is_not_a_recruiter_credential` |
| Candidate reading their own score | `401` on recruiter routes; own payload carries no verdict | `test_a_candidate_cannot_read_their_own_evaluation_or_report` |
| Candidate reading another candidate's identity | absent from the payload | `test_the_candidate_api_never_reveals_another_candidates_identity` |

## 7. Privilege escalation

| Attempt | Result | Test |
| --- | --- | --- |
| Viewer writes (edit, publish, create) | `403`, message names the role | `test_a_viewer_may_read_and_may_not_write` |
| Recruiter deletes an interview | `403`; an admin in the same organization succeeds | `test_a_recruiter_may_publish_and_may_not_delete` |
| Candidate credential on a recruiter route | `401` | `test_a_candidate_credential_is_not_a_recruiter_credential` |
| Valid session used against another organization | `404` both directions | `test_a_session_cookie_from_another_organization_stays_in_its_own_organization` |

Capability is derived from the HTTP method (`GET`→`read`, `POST`/`PATCH`/`PUT`→
`write`, `DELETE`→`delete`), so a new route is role-checked without anyone
remembering to add it.

## 8. Enumeration and non-disclosure

* A foreign resource and a nonexistent one return identical status and identical
  body.
* Ids are unguessable and carry no structure: invitation tokens are 43 random
  bytes, session ids 32 hex characters, evaluation ids `ev_` + random.
* Guessing around a real id (`iv_1`, `iv_0001`, `IV_ACME`, `iv_acme ` …) finds
  nothing — `test_guessing_around_a_real_id_finds_nothing`.
* Error bodies carry no implementation detail. `test_error_bodies_carry_no_implementation_detail`
  scans four refusals for `traceback`, `/Users/`, `site-packages`, `.py"`,
  `sqlite`, `json.decoder`, `keyerror`, `scrypt` and `data/`.

## 9. Rate limiting

Fixed-window, in-process, **fails open** — a bug in the limiter must never end a
candidate's interview (`test_the_limiter_fails_open_rather_than_breaking_an_interview`).

| Bucket | Limit | Why |
| --- | --- | --- |
| `login` | 10 / 60 s per client | Slows credential stuffing without locking anyone out. |
| `invitation` | 60 / 60 s per client | Token guessing. |
| `turn` | 120 / 60 s per client+session | Generous: a real candidate must never hit it. |
| `evaluation` | 20 / 300 s | Cost. |
| `generation` | 30 / 300 s | Cost. |

In-process is a stated limitation: it is per-worker, so N workers means N×limit,
and it resets on restart. See §14.

## 10. Audit security

Every recruiter action records actor, actor type, organization, subject type and
subject id. `audit.emit` derives them from the request's principal via a
contextvar, so forty call sites do not each have to remember — and that
contextvar is read **only** by the audit writer, never for an authorization
decision (`services/security/context.py`).

Authentication events: `LOGIN_SUCCEEDED`, `LOGIN_FAILED`, `LOGOUT`,
`ACCESS_DENIED`. A failed login records a masked address (`so…@example.test`),
never the attempted password.

What never reaches the trail, asserted by `test_no_credential_reaches_the_audit_trail`
over the whole log: invitation tokens, session grants, session cookies,
passwords, password hashes, Authorization headers.

## 11. Secrets

`python -m tools.secrets_audit` — exits non-zero on any finding, and runs as
part of the suite (`test_the_secrets_audit_finds_nothing_in_the_built_bundles_or_the_trail`).
Four places, because they are the four ways a secret has actually escaped from
systems like this one:

1. the built frontend bundles;
2. frontend source (any `import.meta.env.VITE_*` read is a finding — it is
   inlined at build time, so it is not a place a credential can live);
3. what the API sends unauthenticated clients;
4. the audit trail.

Current output, with a provider key configured:

```
Tara secrets audit
  environment      : development
  provider key     : set (73 chars)
  retell key       : unset
  cookies secure   : False
  allowed origins  : *

No findings. Nothing sensitive in the bundles, the public responses,
or the audit trail.
```

The diagnostic prints presence and length, never a prefix: "it starts with
sk-or-v1" reads as caution while still narrowing an attacker's search.

`.env` is gitignored; `.env.example` carries no values. Keys are read in
`services/config.py` and nowhere else.

**Two development conveniences were found to be production exposures in this
phase, and both are now gated on `TARA_ENV`:**

| Finding | Why it mattered | Fix |
| --- | --- | --- |
| `invites.ensure_demo_invite()` minted a never-expiring invitation on the well-known token `demo` on every boot | An unauthenticated way into a real interview under the default organization, guessable by anyone | Not created in production; `require_production_configuration()` reports a usable one by name if a data directory is promoted carrying it |
| `GET /api/demo/prompts` served `content/demo_answers.json` unauthenticated | Those are model answers keyed by the authored item ids of the default interview — an answer key for anyone sitting it | Returns empty in production |

## 12. Production configuration

`config.require_production_configuration()` returns a list of problems, printed
at startup. In production it requires: `ALLOWED_ORIGINS` is not `*`; cookies are
`Secure`; at least one account exists; no usable `demo` invitation.

`RECRUITER_AUTH_REQUIRED` additionally makes the recruiter API refuse to serve
at all (`503`) when no account exists — because serving it to a system nobody
can sign into means the only thing between the internet and every transcript is
that no login exists yet, which is not a control.

CORS: `allow_credentials` is not set, so browsers will not send or expose
credentialed cross-origin responses. Combined with `SameSite=Lax` (which blocks
cross-site POSTs) this is the CSRF position. A dedicated CSRF token is not
implemented — see §15.

## 13. Test coverage

| Suite | Count |
| --- | --- |
| `tests/test_security.py` | 95 |
| Full non-live suite | 1048 passed, 11 deselected |
| Recruiter console (vitest) | 87, including 8 for the session gate |

Existing suites were adapted by giving their fixtures a real organization and a
real sign-in (`tests/conftest.py`: `tenant`, `sign_in`) rather than by adding a
bypass to production code. A switch that turned authorization off for tests
would mean the suite proved something the deployment does not do.

## 14. Verified in the browser

Against a restarted server on `localhost:8000` with the built bundles, the
console on `:5174` and the candidate app on `:5173`:

| Check | Result |
| --- | --- |
| Console with no session | Sign-in screen; no navigation, no tables behind it |
| Sign in | Console renders; sidebar shows the address, organization and role |
| Session cookie readable by page script | No — `document.cookie` is empty (HttpOnly) |
| Sign out | Returns to the sign-in screen; the token is dead server-side |
| Candidates → session review, signed in | `200` |
| Same session read anonymously | `401` on `/`, `/evaluation`, `/trail`, `/score` |
| Candidate link, clean session, no login | Welcome and consent render with the correct interview |
| Every recruiter route from the candidate's browser | `401` — including with the invitation token as a query parameter and as `X-Candidate-Token` |
| `GET /api/invite/{token}` from the candidate's browser | `200`, their own invitation only |

One thing the walkthrough caught that no unit test would: **a stale
pre-authorization server process was still serving port 8000**, answering
`/api/recruiter/interviews` with `200` to anonymous callers. The code was
correct; the running process predated it. That is a deployment concern rather
than a code one, and it belongs in a restart procedure.

## 15. Known limitations

Stated plainly, because a matrix that lists only what works is not a security
document.

1. **Rate limiting is in-process.** Per worker, and lost on restart. Real limits
   belong at the edge or in a shared store.
2. **No CSRF token.** The position rests on `SameSite=Lax` plus the absence of
   `allow_credentials` in CORS. That is sound for the current same-origin
   topology and should be revisited before any cross-origin frontend.
3. **Password login only.** `Authenticator` is the seam for SSO; no IdP is
   wired. There is no MFA, no password-reset flow and no password-rotation
   policy.
4. **No account self-service.** Users are created with `tools.make_user`. There
   is no invitation flow for colleagues, and no UI for disabling an account.
5. **Sessions are file-backed** like everything else. Revocation is immediate,
   but the store is a JSON file with a lock, not a database.
6. **One organization per user.** A person who recruits for two tenants needs
   two accounts.
7. **Audit retention is unbounded.** The trail grows forever and nothing rotates
   it.
8. **The legacy analytics path returns `500` for generated interviews.** Found
   during this phase: `/interviews/{id}/results`, `/fairness?interview_id=`,
   `/compare` and `/sessions/{id}/score` raise `KeyError` for any interview
   whose questions were generated, because `analytics` scores against the
   authored role pool instead of the session's pinned definition. Unrelated to
   authorization, out of this phase's scope, and tracked separately. Two tests
   note it where their positive control is affected.
9. **`data/invites.json` accumulated ~5,100 orphaned rows** because
   `invites._PATH` binds at import and no test fixture redirected it — so the
   suite had been reading and writing the real data directory. The fixture is
   fixed; the existing rows are orphaned (their interviews no longer exist) and
   were left in place rather than deleted without the owner's say-so.
