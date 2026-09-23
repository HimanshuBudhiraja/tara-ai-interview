"""The knowledge base — what the agent is PERMITTED to know.

A separate layer from the scenario, and the distinction matters more than it
first looks:

    knowledge base   the product supports SSO, API integration and SCIM
    scenario         the customer believes implementation will be difficult

The scenario says what is happening. The knowledge base says what is true. An
agent asked "does this integrate with Okta?" answers from the knowledge base,
in character, inside the scenario — and if the knowledge base does not say, the
character does not know. That last clause is the whole reason this is a
first-class layer rather than a paragraph of the persona: "do not make things
up" is unenforceable advice, while "here is the set of facts you have, and
anything outside it you do not know" is a boundary.

Two properties that are easy to lose and expensive to lose
----------------------------------------------------------
**Versioned.** A knowledge base is the most likely part of a simulation to
change — prices move, policies get rewritten. A session pins the version it
ran against, so a report from March can be read knowing exactly which price
list the buyer was arguing from.

**Deterministic retrieval.** Which passages get injected is decided by a scored,
reproducible rule, and the ids of the passages used are recorded on the session.
An embedding index that is re-trained, re-chunked or drifts would mean replaying
a session grounds it on different facts than the ones it was assessed against —
which quietly makes the replay a different assessment.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

#: Where a passage came from. Recorded so a reviewer reading a transcript can
#: tell "the buyer quoted the price list" from "the buyer quoted the playbook".
SOURCE_KINDS = ("document", "url", "text", "policy", "playbook", "pricing", "faq")

_WORD = re.compile(r"[a-z0-9']+")
_STOP = {
    "the", "and", "for", "are", "but", "not", "you", "all", "can", "her", "was",
    "one", "our", "out", "has", "him", "his", "how", "its", "may", "new", "now",
    "old", "see", "two", "who", "did", "get", "use", "way", "she", "they", "this",
    "that", "with", "from", "have", "what", "when", "your", "will", "been", "were",
}


def _terms(text: str) -> set[str]:
    return {w for w in _WORD.findall((text or "").lower()) if len(w) > 2 and w not in _STOP}


@dataclass
class KnowledgePassage:
    """One retrievable fact or paragraph.

    Chunked by the author, not by the platform. A passage is the unit a person
    decided was one idea, which produces better grounding than a character
    count and — more importantly — means a reviewer can be shown exactly what
    the agent was working from.
    """

    id: str
    text: str
    #: Words that should pull this passage in even when they are not in its
    #: text. "Okta" belongs on the SSO passage whether or not it is mentioned.
    tags: list[str] = field(default_factory=list)

    def score(self, query_terms: set[str]) -> int:
        """Overlap with the query. Tags count double — an author tagging a
        passage is a stronger signal than a word happening to appear in it."""
        own = _terms(self.text)
        tagged = _terms(" ".join(self.tags))
        return len(own & query_terms) + 2 * len(tagged & query_terms)


@dataclass
class KnowledgeSource:
    id: str
    title: str
    kind: str = "document"
    passages: list[KnowledgePassage] = field(default_factory=list)

    def validate(self) -> list[str]:
        problems: list[str] = []
        if self.kind not in SOURCE_KINDS:
            problems.append(f"Source '{self.id}' has unknown kind '{self.kind}'.")
        if not self.passages:
            problems.append(f"Source '{self.id}' has no passages.")
        return problems


@dataclass
class KnowledgeBase:
    kb_id: str = ""
    version: int = 1
    name: str = ""
    description: str = ""
    sources: list[KnowledgeSource] = field(default_factory=list)

    #: What the agent must say when asked something outside the base. Authored,
    #: because the alternative is the model improvising a refusal — and the
    #: improvised ones either break character ("I don't have that information")
    #: or invent the answer.
    out_of_scope_line: str = "I don't know that, to be honest."

    def all_passages(self) -> list[tuple[KnowledgeSource, KnowledgePassage]]:
        return [(s, p) for s in self.sources for p in s.passages]

    def passage(self, passage_id: str) -> KnowledgePassage | None:
        return next((p for _, p in self.all_passages() if p.id == passage_id), None)

    def select(
        self, query: str, limit: int = 6, allowed_sources: list[str] | None = None
    ) -> list[tuple[KnowledgeSource, KnowledgePassage]]:
        """The passages to put in front of the agent for this turn.

        Deterministic: scored by overlap, ties broken by passage id, so the same
        query against the same version always yields the same passages in the
        same order. Sorting by score alone would leave ties to dict ordering,
        which is stable within a process and not across a redeploy — and a
        replay that grounds on different facts is a different assessment.

        `allowed_sources` is the scenario-level boundary. A discovery scenario
        can hold back the price list so the buyer cannot be made to quote a
        number the seller was supposed to establish value before naming.
        """
        terms = _terms(query)
        if not terms:
            return []
        pool = [
            (s, p) for s, p in self.all_passages()
            if allowed_sources is None or s.id in allowed_sources
        ]
        scored = [(s, p, p.score(terms)) for s, p in pool]
        hits = [(s, p, n) for s, p, n in scored if n > 0]
        hits.sort(key=lambda row: (-row[2], row[1].id))
        return [(s, p) for s, p, _ in hits[:limit]]

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not self.sources:
            problems.append("A knowledge base with no sources grounds nothing.")
        seen: set[str] = set()
        for source in self.sources:
            problems.extend(source.validate())
            for passage in source.passages:
                if passage.id in seen:
                    problems.append(f"Duplicate passage id '{passage.id}'.")
                seen.add(passage.id)
        return problems

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "KnowledgeBase":
        data = dict(d)
        sources = []
        for raw in data.pop("sources", []) or []:
            raw = dict(raw)
            passages = [KnowledgePassage(**p) for p in raw.pop("passages", []) or []]
            source = KnowledgeSource(**raw)
            source.passages = passages
            sources.append(source)
        known = set(KnowledgeBase.__dataclass_fields__)
        kb = KnowledgeBase(**{k: v for k, v in data.items() if k in known})
        kb.sources = sources
        return kb
