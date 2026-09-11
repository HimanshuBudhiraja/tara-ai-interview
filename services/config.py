"""Configuration — one place that reads the environment.

Everything has a working default so the product boots with no setup at all: no
database, no Redis, no API key. Real credentials change quality and durability,
never the shape of the flow. That property is what makes the whole thing
testable, and it is worth protecting.

Secrets are read here and nowhere else, and they never leave the server. The
browser is handed models' *names* at most, never keys.
"""
from __future__ import annotations

import os
from pathlib import Path

SERVICES_DIR = Path(__file__).resolve().parent
ROOT_DIR = SERVICES_DIR.parent
# Two directories, and the split is not cosmetic.
#
#   content/  authored question banks and demo copy. Shipped with the product,
#             read-only at runtime, checked into source control.
#   data/     sessions, audit trails, invitations, published versions. Written
#             constantly, never checked in, and mounted as a volume in Docker so
#             a redeploy does not lose a candidate's place.
#
# They were one directory once, which meant a test that redirected storage also
# lost the question pool.
CONTENT_DIR = Path(os.environ.get("TARA_CONTENT_DIR", ROOT_DIR / "content")).resolve()
DATA_DIR = Path(os.environ.get("TARA_DATA_DIR", ROOT_DIR / "data")).resolve()
SESSION_DIR = DATA_DIR / "sessions"
AUDIT_DIR = DATA_DIR / "audit"


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        # Strip trailing inline comments, then quotes.
        val = value.split("#")[0].strip().strip("'\"") if " #" in value else value.strip().strip("'\"")
        os.environ.setdefault(key.strip(), val)


_load_env_file(ROOT_DIR / ".env")


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return default


# --------------------------------------------------------------------------- #
#  Durable stores
# --------------------------------------------------------------------------- #
# Both are optional today: the repositories in services/data are file-backed and
# the session store is on disk. These are read so the wiring exists in one place
# when the durable store lands, rather than being invented then.
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
REDIS_URL = os.environ.get("REDIS_URL", "").strip()


# --------------------------------------------------------------------------- #
#  AI provider
# --------------------------------------------------------------------------- #
# OpenRouter is the default provider, but the gateway speaks the OpenAI-
# compatible chat API, so OpenAI, an Azure proxy, or a local vLLM all work by
# changing the base URL. OPENAI_* names are accepted for continuity with the
# candidate prototype's existing .env files.
OPENROUTER_API_KEY = _first("OPENROUTER_API_KEY", "OPENAI_API_KEY")
OPENROUTER_BASE_URL = _first(
    "OPENROUTER_BASE_URL", "OPENAI_BASE_URL", default="https://openrouter.ai/api/v1"
).rstrip("/")

# "auto" uses the real API when a key is present, otherwise the deterministic mock.
LLM_PROVIDER = _first("TARA_LLM", default="auto").lower()
LLM_TIMEOUT_SEC = float(os.environ.get("TARA_LLM_TIMEOUT", "12"))

# Optional attribution headers OpenRouter uses for its dashboards.
OPENROUTER_APP_URL = os.environ.get("OPENROUTER_APP_URL", "").strip()
OPENROUTER_APP_NAME = os.environ.get("OPENROUTER_APP_NAME", "Tara AI Interview").strip()


def llm_is_live() -> bool:
    if LLM_PROVIDER == "mock":
        return False
    return bool(OPENROUTER_API_KEY)


# --------------------------------------------------------------------------- #
#  Per-workload models
#
#  Deliberately NOT decided yet. Each workload has different latency and
#  reasoning needs — classifying a turn while a candidate waits is not the same
#  job as designing an interview from a JD — so each gets its own knob and we
#  benchmark before choosing. A single global model name would force the
#  slowest workload's choice onto the fastest one.
# --------------------------------------------------------------------------- #
_DEFAULT_MODEL = _first("TARA_MODEL_DEFAULT", "TARA_MODEL_FAST", default="openai/gpt-4.1-mini")

INTERVIEW_DESIGNER_MODEL = _first("INTERVIEW_DESIGNER_MODEL", "TARA_MODEL_DEEP", default=_DEFAULT_MODEL)
QUESTION_GENERATOR_MODEL = _first("QUESTION_GENERATOR_MODEL", "TARA_MODEL_DEEP", default=_DEFAULT_MODEL)
ANSWER_CLASSIFIER_MODEL = _first("ANSWER_CLASSIFIER_MODEL", "TARA_MODEL_FAST", default=_DEFAULT_MODEL)
FOLLOWUP_GENERATOR_MODEL = _first("FOLLOWUP_GENERATOR_MODEL", "TARA_MODEL_FAST", default=_DEFAULT_MODEL)
SCORING_MODEL = _first("SCORING_MODEL", "TARA_MODEL_DEEP", default=_DEFAULT_MODEL)
REPORT_GENERATOR_MODEL = _first("REPORT_GENERATOR_MODEL", "TARA_MODEL_DEEP", default=_DEFAULT_MODEL)

# Kept for the runtime modules that still read them by these names.
MODEL_FAST = ANSWER_CLASSIFIER_MODEL
MODEL_DEEP = INTERVIEW_DESIGNER_MODEL


# --------------------------------------------------------------------------- #
#  Interview shape (defaults; a published version overrides all of these)
# --------------------------------------------------------------------------- #
ROLE = os.environ.get("TARA_ROLE", "customer_support_rep")
QUESTION_BUDGET = int(os.environ.get("TARA_QUESTION_BUDGET", "8"))
MAX_PROBES_PER_ITEM = int(os.environ.get("TARA_MAX_PROBES", "2"))
MAX_REASKS_PER_ITEM = int(os.environ.get("TARA_MAX_REASKS", "2"))
MAX_CLARIFIES_PER_ITEM = int(os.environ.get("TARA_MAX_CLARIFIES", "2"))
MIN_ANSWER_CHARS = int(os.environ.get("TARA_MIN_ANSWER_CHARS", "12"))
REJOIN_WINDOW_SEC = int(_first("REJOIN_WINDOW_SEC", "TARA_REJOIN_WINDOW_SEC", default="3600"))


# --------------------------------------------------------------------------- #
#  Behaviour toggles
# --------------------------------------------------------------------------- #
EMPATHY_ENABLED = _flag("TARA_EMPATHY", True)
LLM_PROBES_ENABLED = (
    _flag("LLM_PROBES_ENABLED", True) if "LLM_PROBES_ENABLED" in os.environ
    else _flag("TARA_LLM_PROBES", True)
)


# --------------------------------------------------------------------------- #
#  Voice transport (Retell). The candidate client never sees these.
# --------------------------------------------------------------------------- #
RETELL_API_KEY = os.environ.get("RETELL_API_KEY", "").strip()
RETELL_AGENT_ID = os.environ.get("RETELL_AGENT_ID", "").strip()


# --------------------------------------------------------------------------- #
#  Pilot
# --------------------------------------------------------------------------- #
# Names the pilot run new sessions are attributed to. Empty in normal use: the
# recruiter console opens a run and every session started while it is open is
# stamped with it. Set explicitly when a scripted batch has to name its own run
# — a baseline sweep, say — so its results stay separable from the pilot's.
PILOT_RUN_ID = os.environ.get("TARA_PILOT_RUN", "").strip()


# --------------------------------------------------------------------------- #
#  Security posture
# --------------------------------------------------------------------------- #
# Which deployment this is. `production` turns on the checks that would be
# obstructive on a laptop and are indispensable anywhere else — see
# `require_production_configuration`.
ENVIRONMENT = _first("TARA_ENV", default="development").lower()


def is_production() -> bool:
    return ENVIRONMENT in {"production", "staging"}


# Recruiter routes now require an authenticated session in every deployment.
# The flag survives with a narrower meaning: it is the one that says "and refuse
# to serve them at all if authentication is somehow not configured", which is
# what a production deployment wants and what a first-boot laptop does not.
RECRUITER_AUTH_REQUIRED = _flag("RECRUITER_AUTH_REQUIRED", False)

# Session cookies carry `Secure` unless this is plain-HTTP local development.
# A Secure cookie is simply not sent over http://localhost, so defaulting it on
# would make a fresh clone unable to log in.
COOKIES_SECURE = _flag("TARA_COOKIES_SECURE", is_production())

# The first administrator, for a deployment that has no users yet. Read once at
# startup, used once, and never stored: the account it creates is the only thing
# that persists. Leaving them unset on a fresh install is fine — the boot log
# says how to create the first user with `python -m tools.make_user`.
BOOTSTRAP_EMAIL = os.environ.get("TARA_BOOTSTRAP_EMAIL", "").strip()
BOOTSTRAP_PASSWORD = os.environ.get("TARA_BOOTSTRAP_PASSWORD", "")
BOOTSTRAP_ORG = os.environ.get("TARA_BOOTSTRAP_ORG", "").strip()


#: The development seed's invitation token. Named here so the production check
#: and the seeder cannot drift apart.
DEMO_TOKEN = "demo"


# --------------------------------------------------------------------------- #
#  Deployment identity
#
#  What is running, so a health check and a log line can say which build
#  produced them. Set by the deployment — a git sha, a tag, a build number —
#  and "dev" when nobody said.
# --------------------------------------------------------------------------- #
SERVICE_VERSION = _first("TARA_VERSION", "GIT_SHA", "SOURCE_VERSION", default="dev")

# --------------------------------------------------------------------------- #
#  Logging
# --------------------------------------------------------------------------- #
#: `json` for anything that ships logs; `text` for a terminal.
LOG_FORMAT = _first("TARA_LOG_FORMAT", default="json").lower()
LOG_LEVEL = _first("TARA_LOG_LEVEL", default="INFO").upper()

#: The origins the browser actually loads Tara from. Used to build candidate
#: links and to state, in one place, what the deployment's public addresses are.
PUBLIC_URL = _first("TARA_PUBLIC_URL", default="").rstrip("/")
RECRUITER_URL = _first("TARA_RECRUITER_URL", default="").rstrip("/")


# --------------------------------------------------------------------------- #
#  Data retention
#
#  One place, on purpose. Retention periods scattered across services are
#  retention periods that disagree, and the first time they disagree a record
#  survives because one service thought another owned it.
#
#  These are the PILOT defaults, and they are choices rather than requirements:
#  nothing in the product specification names a duration, so they are set to
#  what a hiring process actually needs and left configurable. A deployment with
#  a legal or contractual obligation sets its own.
#
#  Retention period ≠ cleanup interval. The period is policy: when a record
#  BECOMES eligible for deletion. The interval is implementation: how often
#  something looks for eligible records. A record that is eligible and has not
#  been swept is still eligible, and `retention.eligible()` will say so.
# --------------------------------------------------------------------------- #
def _days(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    # 0 would mean "delete immediately", which is never what a typo means.
    return value if value > 0 else default


#: The candidate's own data: their name, their invitation, their transcript.
#: 180 days covers a hiring round plus the window in which a rejected candidate
#: might reasonably be reconsidered for another role.
CANDIDATE_DATA_RETENTION_DAYS = _days("CANDIDATE_DATA_RETENTION_DAYS", 180)

#: The verbatim transcript specifically. Shorter by default than the rest of the
#: candidate record, because it is the most sensitive thing Tara holds and the
#: least useful after a decision is made. Clamped to the candidate period: a
#: transcript cannot outlive the candidate record it belongs to.
TRANSCRIPT_RETENTION_DAYS = min(
    _days("TRANSCRIPT_RETENTION_DAYS", 90), CANDIDATE_DATA_RETENTION_DAYS
)

#: Evaluations, evidence and results. Same clock as the candidate record —
#: an evaluation without its transcript is not a defensible hiring document.
EVALUATION_RETENTION_DAYS = min(
    _days("EVALUATION_RETENTION_DAYS", 180), CANDIDATE_DATA_RETENTION_DAYS
)

#: The security trail. Longer than everything it describes, deliberately: the
#: whole point of an audit log is to answer questions after the data is gone.
#: It holds no candidate content — see DATA_LIFECYCLE.md §2.
AUDIT_RETENTION_DAYS = _days("AUDIT_RETENTION_DAYS", 400)

#: How often the cleanup mechanism is expected to run. Not a retention period —
#: it is documentation for the operator, and it is what makes "eligible but not
#: yet swept" a bounded window rather than an unknown one.
RETENTION_SWEEP_INTERVAL_HOURS = _days("RETENTION_SWEEP_INTERVAL_HOURS", 24)


def require_production_configuration() -> list[str]:
    """What must be true before this is allowed to serve real candidates.

    Returned as a list rather than raised so the caller decides: the API refuses
    to boot on it, and a diagnostic can print it. Nothing here prints a secret —
    only whether one is present.
    """
    problems: list[str] = []
    if not is_production():
        return problems
    if "*" in ALLOWED_ORIGINS:
        problems.append(
            "ALLOWED_ORIGINS is '*'. Name the recruiter and candidate origins "
            "explicitly in production."
        )
    if not COOKIES_SECURE:
        problems.append(
            "TARA_COOKIES_SECURE is off. Session cookies must be Secure when "
            "served over HTTPS."
        )
    from services.data import accounts

    if not accounts.any_user_exists():
        problems.append(
            "No users exist, so nobody can sign in. Create the first "
            "administrator with `python -m tools.make_user`."
        )

    from services.data import invites

    # The development seed mints a never-expiring invitation on the well-known
    # token "demo". In production that is an unauthenticated way into a real
    # interview under the default organization, guessable by anyone. Boot does
    # not create it there, but a deployment promoted from a dev data directory
    # would still be carrying one.
    demo = invites.get(DEMO_TOKEN)
    if demo is not None and demo.effective_status not in ("revoked", "expired"):
        problems.append(
            f"A usable {DEMO_TOKEN!r} invitation exists. Revoke it — it is a "
            "well-known credential for a real interview."
        )

    problems += _provider_problems()
    problems += _storage_problems()
    problems += _retention_problems()
    problems += _debug_problems()
    return problems


def _provider_problems() -> list[str]:
    """The AI configuration, checked WITHOUT calling anything.

    Deliberately offline: a configuration check that makes a provider call
    costs money on every boot and every readiness probe, and it fails for
    reasons — an outage, an exhausted budget — that are not configuration
    problems. What is checked is that a key is present and that every model slot
    resolves to a name. Whether that name works is a runtime question with its
    own failure states.
    """
    problems: list[str] = []
    if not OPENROUTER_API_KEY:
        problems.append(
            "No provider API key. Interviews still run on the deterministic "
            "mock, but no evaluation can complete. Set OPENROUTER_API_KEY."
        )
    unresolved = [
        name for name, value in (
            ("INTERVIEW_DESIGNER_MODEL", INTERVIEW_DESIGNER_MODEL),
            ("QUESTION_GENERATOR_MODEL", QUESTION_GENERATOR_MODEL),
            ("ANSWER_CLASSIFIER_MODEL", ANSWER_CLASSIFIER_MODEL),
            ("FOLLOWUP_GENERATOR_MODEL", FOLLOWUP_GENERATOR_MODEL),
            ("SCORING_MODEL", SCORING_MODEL),
            ("REPORT_GENERATOR_MODEL", REPORT_GENERATOR_MODEL),
        ) if not (value or "").strip()
    ]
    if unresolved:
        problems.append(
            "Model slots resolve to nothing: " + ", ".join(unresolved)
            + ". Set them, or set TARA_MODEL_DEFAULT."
        )
    if not OPENROUTER_BASE_URL.startswith("https://"):
        problems.append(
            "The provider base URL is not HTTPS. Candidate answers travel over "
            "it, so plaintext is not an option."
        )
    return problems


#: Paths a container writes to that do not survive a restart. Detecting an
#: ephemeral data directory is the difference between "we redeployed" and "every
#: interview and every evaluation is gone".
_EPHEMERAL_PREFIXES = (
    "/tmp", "/private/tmp",     # /tmp is a symlink to /private/tmp on macOS
    "/var/tmp", "/private/var/tmp",
    "/dev/shm", "/run",
)


def _storage_problems() -> list[str]:
    """Is the data directory usable, and does it look like it will survive?

    Persistence is file-backed and single-host (see DEPLOYMENT.md §"Durable
    persistence status"), so the directory IS the database. Two things are
    checkable without knowing the platform: that it can be written, and that it
    is not somewhere obviously ephemeral.
    """
    problems: list[str] = []

    # Ephemerality first. A path can be both ephemeral and unwritable
    # (`/dev/shm` on a Mac), and "this will not survive a restart" is the more
    # useful of the two messages.
    #
    # Both the configured path and its resolved form are checked, because `/tmp`
    # is a symlink to `/private/tmp` on macOS and looking at only one of them
    # silently misses the most common ephemeral directory there.
    candidates = {str(DATA_DIR), str(DATA_DIR.resolve())}
    if any(
        path == prefix or path.startswith(prefix + "/")
        for path in candidates
        for prefix in _EPHEMERAL_PREFIXES
    ):
        problems.append(
            "TARA_DATA_DIR points somewhere ephemeral. Core state is file-backed, "
            "so a restart there loses every interview, evaluation and audit "
            "record. Mount a persistent volume."
        )

    probe = DATA_DIR / ".config-check"
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        problems.append(
            f"The data directory is not writable ({type(exc).__name__}). Every "
            "session, evaluation and audit record is written there."
        )
    return problems


def _retention_problems() -> list[str]:
    problems: list[str] = []
    if CANDIDATE_DATA_RETENTION_DAYS <= 0:
        problems.append("CANDIDATE_DATA_RETENTION_DAYS must be a positive number of days.")
    if TRANSCRIPT_RETENTION_DAYS > CANDIDATE_DATA_RETENTION_DAYS:
        # Cannot normally happen — the value is clamped on read — but a
        # deployment that edits these by hand should be told rather than have a
        # transcript quietly outlive the record it belongs to.
        problems.append(
            "TRANSCRIPT_RETENTION_DAYS exceeds CANDIDATE_DATA_RETENTION_DAYS. A "
            "transcript cannot outlive the candidate record it belongs to."
        )
    if EVALUATION_RETENTION_DAYS > CANDIDATE_DATA_RETENTION_DAYS:
        problems.append(
            "EVALUATION_RETENTION_DAYS exceeds CANDIDATE_DATA_RETENTION_DAYS."
        )
    if AUDIT_RETENTION_DAYS < CANDIDATE_DATA_RETENTION_DAYS:
        problems.append(
            "AUDIT_RETENTION_DAYS is shorter than the candidate retention period. "
            "The trail exists to answer questions after the data is gone."
        )
    return problems


def _debug_problems() -> list[str]:
    """Development conveniences that are exposures in production."""
    problems: list[str] = []
    if LLM_PROVIDER == "mock":
        problems.append(
            "TARA_LLM is 'mock'. A production deployment would run interviews "
            "against the deterministic stub and no evaluation would be real."
        )
    if BOOTSTRAP_PASSWORD:
        problems.append(
            "TARA_BOOTSTRAP_PASSWORD is still set. It is read once at first "
            "boot; leaving it in the environment leaves a password where "
            "anything that can read the process environment can read it."
        )
    if LOG_LEVEL == "DEBUG":
        problems.append(
            "TARA_LOG_LEVEL is DEBUG. Debug logging is not audited for candidate "
            "content and is not safe to ship."
        )
    return problems
ALLOWED_ORIGINS = [
    o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "*").split(",") if o.strip()
]

SESSION_DIR.mkdir(parents=True, exist_ok=True)
AUDIT_DIR.mkdir(parents=True, exist_ok=True)
