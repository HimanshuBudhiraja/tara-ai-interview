"""The skill master: the controlled vocabulary a skill's DOMAIN comes from.

A skill's **name** stays free text, in the job description's own words. That is
deliberate and it is not laziness: renaming "Idempotent capture design" to
"Software Engineering" because a catalogue said so is how a recruiter stops
recognising their own role in the output, and the name is what the candidate's
report is read against.

What is controlled is the **domain**. Without one, every skill is an island: two
interviews for the same role invent two different ids for the same competency,
nothing can be counted across interviews, and there is no way to ask "how do
candidates do on reliability skills across every backend role we run?".

So: free-text name, catalogued domain. `content/skill_master.json` is the
catalogue — shipped, read-only at runtime, versioned, next to the question
banks for the same reason they are there.

## Resolution

`resolve()` proposes a domain from the skill's own words. It is a keyword match
and it is honest about being one: it returns a *suggestion* the recruiter can
override in the configuration screen, and `""` when nothing matches well enough
rather than forcing the nearest domain. A wrong domain silently applied is
worse than an empty one visibly asking to be filled.

Matching is longest-marker-first, so "problem solving" beats "solving" and
"data model" beats "data". Ties go to the domain with the longer matched
marker, then to declaration order — deterministic either way, because a
proposal that changes between runs is a proposal nobody can review.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from services import config

#: What a domain can be grouped under. Not a second taxonomy — a grouping for
#: the picker and for rollup reporting.
CATEGORIES = ("technical", "functional", "behavioural")


@dataclass(frozen=True)
class Domain:
    id: str
    label: str
    category: str
    description: str
    matches: tuple[str, ...]

    def public(self) -> dict[str, Any]:
        # `matches` is not published: it is matching machinery, and a recruiter
        # choosing a domain does not need to see the keyword list behind it.
        return {
            "id": self.id,
            "label": self.label,
            "category": self.category,
            "description": self.description,
        }


class SkillMasterError(RuntimeError):
    """The catalogue is missing or malformed. Fatal at startup, not at runtime."""


@lru_cache(maxsize=1)
def _load() -> tuple[str, tuple[Domain, ...]]:
    path = config.CONTENT_DIR / "skill_master.json"
    if not path.exists():
        raise SkillMasterError(f"no skill master at {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    domains = tuple(
        Domain(
            id=d["id"],
            label=d["label"],
            category=d.get("category", "functional"),
            description=d.get("description", ""),
            matches=tuple(m.lower() for m in d.get("matches", [])),
        )
        for d in raw.get("domains", [])
    )
    if not domains:
        raise SkillMasterError("the skill master defines no domains")
    seen = [d.id for d in domains]
    if len(seen) != len(set(seen)):
        raise SkillMasterError("the skill master has duplicate domain ids")
    return raw.get("version", "unknown"), domains


def version() -> str:
    return _load()[0]


def domains() -> tuple[Domain, ...]:
    return _load()[1]


def get(domain_id: str) -> Domain | None:
    return next((d for d in domains() if d.id == domain_id), None)


def is_valid(domain_id: str) -> bool:
    """Empty is valid — it means "not yet chosen", which is a real state."""
    return not domain_id or get(domain_id) is not None


def label_of(domain_id: str) -> str:
    domain = get(domain_id)
    return domain.label if domain else ""


def catalogue() -> dict[str, Any]:
    """What the configuration screen's picker is built from."""
    return {
        "version": version(),
        "categories": list(CATEGORIES),
        "domains": [d.public() for d in domains()],
    }


def resolve(*texts: str) -> str:
    """Propose a domain from a skill's own words. `""` when nothing fits.

    Pass the name first and anything else that describes it — the description,
    the assessment scope. Later arguments are still matched, but a marker found
    in the NAME wins over a longer one found in a description, because the name
    is what the skill is and the rest is commentary.
    """
    # Field order is authority, not a weighting. Weighting the fields and
    # comparing scores across them looked reasonable and was wrong: a long
    # marker in a description ("kubernetes", 10 characters) outscored a short
    # one in the name ("test", 4), so a skill CALLED "Test automation" filed
    # itself under Cloud & Infrastructure because its scope mentioned
    # deployment. The name decides when the name says anything at all.
    for text in texts:
        lowered = (text or "").lower()
        if not lowered:
            continue
        best_id, best_len = "", 0
        for domain in domains():
            for marker in domain.matches:
                # Within one field, the longest marker wins: "data model"
                # should beat "data", and "problem solving" should not be
                # decided by whichever domain happens to be declared first.
                if marker in lowered and len(marker) > best_len:
                    best_id, best_len = domain.id, len(marker)
        if best_id:
            return best_id
    return ""


def apply_to(skill: Any) -> str:
    """Fill a skill's domain if it has none. Returns the domain in force.

    Never overwrites a domain already set — a recruiter's correction outranks a
    keyword match, and this runs again every time the draft is saved.
    """
    current = (getattr(skill, "domain", "") or "").strip()
    if current and is_valid(current):
        return current
    proposed = resolve(
        getattr(skill, "name", ""),
        getattr(skill, "assessment_scope", ""),
        getattr(skill, "description", ""),
    )
    try:
        skill.domain = proposed
    except AttributeError:  # pragma: no cover — a frozen skill-like object
        pass
    return proposed
