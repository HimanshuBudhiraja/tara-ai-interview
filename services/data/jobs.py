"""Jobs — the recruiter's input, kept as a record in its own right.

Until now the job details lived on the interview. They are separated because
they have different lifetimes: one job can be interviewed several ways (a short
screen and a deep technical round), and a regeneration has to replay the
*original* input rather than whatever the interview has been edited into since.

The JD and the additional information are recruiter-authored free text that goes
into a model prompt. They are stored verbatim — the fencing happens at the
prompt boundary, not here, because mangling what someone typed on the way into
the database makes the record useless for the audit it exists for.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from packages.types import new_id
from services import config

#: Languages the product can actually conduct an interview in today. Deliberately
#: short: a dropdown offering forty languages the runtime cannot speak is a
#: promise the interview breaks.
SUPPORTED_LANGUAGES: dict[str, str] = {
    "en": "English",
}

#: Where in the hiring process this interview sits. It is not decoration: the
#: stage decides what Tara assesses, how long the interview runs, and how hard
#: she pushes — a prescreen that probes like a staff-level technical round
#: wastes a candidate's evening and tells the recruiter nothing they needed.
FUNNEL_STAGES: dict[str, str] = {
    "prescreening": "Prescreening",
    "technical": "Technical",
    "advance_technical": "Advanced technical",
}

#: stage → (interview_type, difficulty). The interview type is a starting
#: point the recruiter can move on the review screen. The difficulty is not
#: offered there: "how hard should this round push" is the question choosing
#: the round already answered, and a prescreen at `hard` is not a prescreen.
STAGE_SHAPE: dict[str, tuple[str, str]] = {
    "prescreening": ("short", "easy"),
    "technical": ("medium", "medium"),
    "advance_technical": ("deep", "hard"),
}

MAX_JD_CHARS = 40_000
MAX_ADDITIONAL_CHARS = 8_000
MAX_TITLE_CHARS = 200
MAX_EXPERIENCE_YEARS = 50


@dataclass
class Job:
    id: str
    title: str
    description: str = ""            # the JD, verbatim
    additional_information: str = ""  # the recruiter's extra context, verbatim
    language: str = "en"
    experience_from: int = 0
    experience_to: int = 0
    funnel_stage: str = "technical"
    org_id: str = ""
    created_by: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Job":
        known = set(Job.__dataclass_fields__)
        return Job(**{k: v for k, v in d.items() if k in known})


# --------------------------------------------------------------------------- #
#  Validation — server-side, and authoritative
#
#  The browser's version of these rules exists to make the form pleasant. This
#  one exists because the browser's can be skipped with a curl.
# --------------------------------------------------------------------------- #
class ValidationError(ValueError):
    """Field-level problems, all of them at once.

    A form that reports one error per submission is a form nobody finishes, so
    every problem is collected before anything is raised.
    """

    def __init__(self, errors: dict[str, str]) -> None:
        self.errors = errors
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))


def validate(
    *,
    title: str,
    experience_from: Any,
    experience_to: Any,
    language: str,
    job_description: str,
    additional_information: str = "",
    funnel_stage: str = "technical",
) -> dict[str, Any]:
    """Check the recruiter's input and return it normalised."""
    errors: dict[str, str] = {}

    clean_stage = (funnel_stage or "").strip().lower()
    if clean_stage not in FUNNEL_STAGES:
        errors["funnel_stage"] = (
            "Choose the stage this interview is for: "
            + ", ".join(FUNNEL_STAGES.values()) + "."
        )

    clean_title = (title or "").strip()
    if not clean_title:
        errors["title"] = "Give the role a title — it's what candidates see on their invitation."
    elif len(clean_title) > MAX_TITLE_CHARS:
        errors["title"] = f"Keep the title under {MAX_TITLE_CHARS} characters."

    def _years(value: Any, field_name: str, label: str) -> int | None:
        if value is None or value == "":
            errors[field_name] = f"{label} is required."
            return None
        try:
            years = int(value)
        except (TypeError, ValueError):
            errors[field_name] = f"{label} must be a number of years."
            return None
        if years < 0 or years > MAX_EXPERIENCE_YEARS:
            errors[field_name] = f"{label} must be between 0 and {MAX_EXPERIENCE_YEARS} years."
            return None
        return years

    years_from = _years(experience_from, "experience_from", "Experience from")
    years_to = _years(experience_to, "experience_to", "Experience to")
    if years_from is not None and years_to is not None and years_from > years_to:
        errors["experience_to"] = (
            f"The range runs backwards — {years_from} to {years_to} years. "
            f"Set the upper bound to at least {years_from}."
        )

    clean_language = (language or "").strip()
    if clean_language not in SUPPORTED_LANGUAGES:
        supported = ", ".join(SUPPORTED_LANGUAGES.values())
        errors["language"] = f"Tara can currently interview in: {supported}."

    clean_jd = (job_description or "").strip()
    if not clean_jd:
        errors["job_description"] = (
            "Paste the job description — it's what the analysis reads to work out "
            "what this role needs."
        )
    elif len(clean_jd) > MAX_JD_CHARS:
        errors["job_description"] = (
            f"That job description is {len(clean_jd):,} characters. "
            f"Trim it to {MAX_JD_CHARS:,} or fewer."
        )

    clean_extra = (additional_information or "").strip()
    if len(clean_extra) > MAX_ADDITIONAL_CHARS:
        errors["additional_information"] = (
            f"Keep additional information under {MAX_ADDITIONAL_CHARS:,} characters."
        )

    if errors:
        raise ValidationError(errors)

    return {
        "title": clean_title,
        "experience_from": years_from,
        "experience_to": years_to,
        "language": clean_language,
        "job_description": clean_jd,
        "additional_information": clean_extra,
        "funnel_stage": clean_stage,
    }


# --------------------------------------------------------------------------- #
#  Store
# --------------------------------------------------------------------------- #
_PATH = config.DATA_DIR / "jobs.json"


def _read_all() -> dict[str, Job]:
    if not _PATH.exists():
        return {}
    raw = json.loads(_PATH.read_text(encoding="utf-8"))
    return {k: Job.from_dict(v) for k, v in raw.items()}


def _write_all(rows: dict[str, Job]) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _PATH.with_suffix(".tmp")
    tmp.write_text(
        json.dumps({k: v.to_dict() for k, v in rows.items()}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    tmp.replace(_PATH)


def get(job_id: str) -> Job | None:
    return _read_all().get(job_id)


def list_all() -> list[Job]:
    return sorted(_read_all().values(), key=lambda j: j.created_at, reverse=True)


def save(job: Job) -> Job:
    job.updated_at = time.time()
    rows = _read_all()
    rows[job.id] = job
    _write_all(rows)
    return job


def create(**fields: Any) -> Job:
    """Create from already-validated fields."""
    return save(
        Job(
            id=new_id("job"),
            title=fields["title"],
            description=fields["job_description"],
            additional_information=fields.get("additional_information", ""),
            language=fields["language"],
            experience_from=fields["experience_from"],
            experience_to=fields["experience_to"],
            # Who owns it, from the authenticated principal. A job is the root
            # of an interview's ownership chain when one is created from a JD.
            funnel_stage=fields.get("funnel_stage", "technical"),
            org_id=fields.get("org_id", ""),
            created_by=fields.get("created_by", ""),
        )
    )
