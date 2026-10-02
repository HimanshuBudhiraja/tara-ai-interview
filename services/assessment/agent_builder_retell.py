"""An Agent Builder agent, as one call to the one generic Retell agent.

Every agent a recruiter builds runs on the same Retell agent
(`RETELL_AGENT_BUILDER_AGENT_ID`). Its global prompt is written once, with
`{{placeholders}}`, in `content/retell/tara_agent_builder_prompt.md` (section C)
and pasted into Retell. A built agent is therefore a configuration, never a new
Retell agent. Each call carries:

    retell_llm_dynamic_variables   the 18 strings in `VARIABLES`
    agent_override.agent           voice and language from Persona → Voice,
                                   the length cap from Persona → Follow-up depth
    agent_override.retell_llm      who speaks first, and the opening line

What crosses and what does not
------------------------------
Everything the agent needs to RUN the conversation crosses. Nothing the scorer
needs to JUDGE it crosses: no rubric names, anchors or weights, and no question
tags (a tag is a rubric name). The participant-facing description stays out
too, because the participant read it before the call. `leaked_cues` is the
check. It runs on every payload the API builds and refuses to send one that
fails.

Voice and length come from Persona and nothing else. Voice is the Persona →
Voice choice. Length is Persona → Follow-up depth (Light 10, Probing 20,
Deep dive 30 minutes), with Ending deciding only the hard cap. Neither is
derived from difficulty, language or the number of questions. The user decided
that on 2026-10-01, and `test_agent_builder.py` holds it in place.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

from services import config

PROMPT_PATH = config.ROOT_DIR / "content" / "retell" / "tara_agent_builder_prompt.md"
_SECTION_C = "## C. General prompt (paste everything below this line into Retell)"

#: The variable contract. The global prompt uses all of these except
#: `max_minutes`, which only sets the call's length cap. All are always sent,
#: as strings: a placeholder with no value is left in the prompt literally.
VARIABLES: tuple[str, ...] = (
    "agent_name", "agent_role", "agent_style", "agent_title", "scenario_type",
    "participant_role", "skills_focus", "difficulty", "follow_up_depth",
    "adaptive_followups", "conversation_instructions", "question_bank",
    "opening_line", "closing_line", "target_minutes", "ending_mode",
    "max_minutes", "language", "candidate_name", "resume_context",
)

DIFFICULTIES = ("Friendly", "Realistic", "Tough")
DEPTHS = ("Light", "Probing", "Deep dive")
ENDINGS = ("Tara decides", "Hard time limit", "No end time")
SPEAKERS = ("Tara opens", "Participant opens")
#: Voice only. Video and typed (Chat) conversations were removed on 2026-10-02.
FORMATS = ("Voice only",)
SOUNDS = ("None", "Phone ring", "Video join", "Doorbell")
#: Proctoring is two Yes/No settings (image proctoring, Safe Assessment
#: Browser). Fields only: set as defaults in Advanced and per invitation, saved
#: with each session, and applied by the separate proctoring suite. Nothing here
#: changes the conversation or is sent to the voice agent.
PROCTORING_KEYS = ("image_proctoring", "safe_browser")
#: Builder test calls are real calls, kept short.
TEST_CALL_MINUTES = 5
LANGUAGES = ("English", "Hindi", "French", "Spanish", "German")

#: Persona → Follow-up depth sets the conversation length. Nothing else does.
DEPTH_MINUTES: dict[str, int] = {"Light": 10, "Probing": 20, "Deep dive": 30}
#: How many questions a draft gets at each depth, so the plan fits the time.
DEPTH_QUESTIONS: dict[str, int] = {"Light": 4, "Probing": 6, "Deep dive": 7}


@dataclass(frozen=True)
class Voice:
    key: str
    label: str
    language: str
    locale: str
    gender: str
    default_id: str

    @property
    def voice_id(self) -> str:
        """The Retell voice id. An env var `RETELL_VOICE_<KEY>` overrides the
        default, so a voice can be swapped without a deploy."""
        return os.environ.get(f"RETELL_VOICE_{self.key.upper()}", "").strip() or self.default_id


#: Persona → Voice. Every id below was read from Retell `GET /list-voices` on
#: 2026-10-01. Retell has no Arabic voice, so Arabic is not offered. Hindi uses
#: the two Indian ElevenLabs voices with `hi-IN`; test it before a client relies on it.
VOICES: tuple[Voice, ...] = (
    Voice("grace", "Grace — warm, US English", "English", "en-US", "f", "11labs-Grace"),
    Voice("adrian", "Adrian — friendly, US English", "English", "en-US", "m", "11labs-Adrian"),
    Voice("willa", "Willa — crisp, UK English", "English", "en-GB", "f", "11labs-Willa"),
    Voice("anthony", "Anthony — steady, UK English", "English", "en-GB", "m", "11labs-Anthony"),
    Voice("monika", "Monika — clear, Indian English", "English", "en-IN", "f", "11labs-Monika"),
    Voice("amritanshu", "Amritanshu — calm, Indian English", "English", "en-IN", "m", "11labs-Amritanshu"),
    Voice("monika_hi", "Monika — Hindi", "Hindi", "hi-IN", "f", "11labs-Monika"),
    Voice("amritanshu_hi", "Amritanshu — Hindi", "Hindi", "hi-IN", "m", "11labs-Amritanshu"),
    Voice("emma", "Emma — poised, French", "French", "fr-FR", "f", "cartesia-Emma"),
    Voice("pierre", "Pierre — steady, French", "French", "fr-FR", "m", "cartesia-Pierre"),
    Voice("isabel", "Isabel — bright, Spanish", "Spanish", "es-ES", "f", "cartesia-Isabel"),
    Voice("santiago", "Santiago — measured, Spanish", "Spanish", "es-ES", "m", "11labs-Santiago"),
    Voice("carola", "Carola — direct, German", "German", "de-DE", "f", "11labs-Carola"),
    Voice("max", "Max — calm, German", "German", "de-DE", "m", "minimax-Max"),
)
VOICE_KEYS = tuple(v.key for v in VOICES)
DEFAULT_VOICE = "grace"


def voice(key: str) -> Voice:
    return next((v for v in VOICES if v.key == key), VOICES[0])


# --------------------------------------------------------------------------- #
#  Length
# --------------------------------------------------------------------------- #
def lengths(cfg: dict[str, Any]) -> tuple[int, int]:
    """(target minutes, cap minutes) from Follow-up depth and Ending."""
    target = DEPTH_MINUTES.get(cfg.get("depth") or "", DEPTH_MINUTES["Probing"])
    ending = cfg.get("ending") or ENDINGS[0]
    if ending == "Hard time limit":
        cap = target
    elif ending == "No end time":
        cap = 60
    else:
        cap = target + 5
    return target, cap


# --------------------------------------------------------------------------- #
#  The prompt
# --------------------------------------------------------------------------- #
def general_prompt() -> str:
    """Section C of the prompt file: exactly what sits in Retell."""
    text = PROMPT_PATH.read_text()
    if _SECTION_C not in text:
        raise RuntimeError(f"{PROMPT_PATH.name} has no section C heading")
    return text.split(_SECTION_C, 1)[1].strip()


#: Variables Retell fills itself. The prompt may use them; we never send them.
SYSTEM_VARIABLES = frozenset({"session_duration", "session_duration_ms", "current_time", "call_id"})


def prompt_variables(text: str | None = None) -> set[str]:
    return set(re.findall(r"\{\{(\w+)\}\}", text if text is not None else general_prompt()))


def render(variables: dict[str, str], text: str | None = None) -> str:
    """The prompt with every placeholder filled, as Retell will see it."""
    out = text if text is not None else general_prompt()
    for k, v in variables.items():
        out = out.replace("{{" + k + "}}", v)
    # Retell fills its system variables during a call; a typed rehearsal has no clock.
    return out.replace("{{session_duration}}", "not tracked in a typed test")


# --------------------------------------------------------------------------- #
#  The call
# --------------------------------------------------------------------------- #
def _s(value: Any) -> str:
    return "" if value is None else str(value).strip()


#: A dropped call resumes with at most this much of what was already said.
RESUME_CHARS = 3500


def resume_text(transcript: list[dict[str, str]], persona: str) -> str:
    """What was said before a call dropped, as the agent should read it."""
    lines = [f"{persona if t.get('role') == 'agent' else 'Participant'}: {_s(t.get('text'))}"
             for t in transcript if _s(t.get("text"))]
    return "\n".join(lines)[-RESUME_CHARS:] or "none"


def dynamic_variables(row: dict[str, Any], candidate_name: str = "not given",
                      resume_context: str = "none") -> dict[str, str]:
    """The 20 strings sent as `retell_llm_dynamic_variables`."""
    agent, cfg, fields = row["agent"], row["cfg"], row.get("fields") or {}
    persona = agent.get("persona") or {}
    target, cap = lengths(cfg)
    user_first = cfg.get("speaker") == "Participant opens"
    out = {
        "agent_name": _s(persona.get("name")),
        "agent_role": _s(persona.get("role")),
        "agent_style": _s(persona.get("style")),
        "agent_title": _s(agent.get("title")),
        "scenario_type": _s(agent.get("type_label")),
        "participant_role": _s(fields.get("role")),
        "skills_focus": _s(fields.get("skills")),
        "difficulty": _s(cfg.get("tone")) or "Realistic",
        "follow_up_depth": _s(cfg.get("depth")) or "Probing",
        "adaptive_followups": "on" if cfg.get("followups", True) else "off",
        "conversation_instructions": _s(agent.get("instructions")) + exhibits_text(agent.get("exhibits") or []),
        "question_bank": "\n".join(
            f"{i}. {_s(q.get('text'))}" for i, q in enumerate(agent.get("questions") or [], 1)
            if _s(q.get("text"))
        ),
        "opening_line": "" if user_first else _s(agent.get("opening_line")),
        "closing_line": _s(agent.get("closing_line")),
        "target_minutes": str(target),
        "ending_mode": _s(cfg.get("ending")) or ENDINGS[0],
        "max_minutes": str(cap),
        "language": _s(cfg.get("language")) or voice(cfg.get("voice") or "").language,
        "candidate_name": _s(candidate_name) or "not given",
        "resume_context": _s(resume_context) or "none",
    }
    assert set(out) == set(VARIABLES), "variable contract drifted"
    # The participant only ever meets the persona. Instructions, questions and
    # lines are written in the builder as "Tara does X"; in the call that would
    # make the voice say "I'm Tara". So every "Tara" becomes the persona's first
    # name. The participant's own name and the resume transcript are left alone.
    first = persona_first(persona.get("name"))
    for k in out:
        if k not in ("candidate_name", "resume_context"):
            out[k] = no_interview(_TARA.sub(first, out[k]))
    return out


_TARA = re.compile(r"\bTara\b")
_TITLES = {"dr", "mr", "mrs", "ms", "miss", "prof", "sir"}


_INTERVIEW = re.compile(r"\b(interview)(s?)\b", re.I)
_AN_INTERVIEW = re.compile(r"\b(a)n(\s+)(?=interview\b)", re.I)


def no_interview(text: str) -> str:
    """The product says "conversation", never "interview" (user decision, 2026-10-03)."""
    def sub(m: re.Match) -> str:
        word = "conversation" + m.group(2)
        return word.capitalize() if m.group(1)[0].isupper() else word
    return _INTERVIEW.sub(sub, _AN_INTERVIEW.sub(r"\1\2", text))


def exhibits_text(exhibits: list[dict[str, Any]]) -> str:
    """What the persona knows about the exhibits on the participant's screen."""
    if not exhibits:
        return ""
    lines = []
    for i, e in enumerate(exhibits, 1):
        line = f"Exhibit {i}, {e.get('title', '')}"
        c = e.get("chart")
        if c and c.get("labels"):
            unit = (" " + c["unit"]) if c.get("unit") else ""
            pts = ", ".join(f"{lab}: {val:g}{unit}" for lab, val in zip(c["labels"], c["values"]))
            line += f" ({c.get('type', 'bar')} chart: {pts})"
        if e.get("description"):
            line += f". {e['description']}"
        lines.append(line)
    return ("\n\nExhibits the participant can see on their screen. When you want them to look at one, say its name, "
            "for example \"take a look at Exhibit 1\", and it opens for them. Talk about it only using what is written here:\n"
            + "\n".join("- " + x for x in lines))


def persona_first(name: Any) -> str:
    """The persona's first name ("Dr. Maya Rao" -> "Maya"); "the persona" if none."""
    words = [w for w in _s(name).split() if w.rstrip(".").lower() not in _TITLES]
    return words[0] if words else "the persona"


#: Turn-taking tuned on real interview calls (the "it races / talks over me"
#: fixes). Sent on every call, so they hold whatever the agent in Retell is set to.
TURN_TAKING = {
    # 0.8: replies come soon after the participant stops, as in a real
    # conversation. 0.4 left a noticeable pause before every reply.
    "responsiveness": 0.8,
    # 0.7: the participant can cut in and Tara yields, as a person would;
    # background noise and short sounds still don't stop her mid-sentence.
    "interruption_sensitivity": 0.7,
    "enable_backchannel": True,
    # 0.2: an occasional "mm-hmm" on longer answers. 0.7 put one over almost
    # every sentence, which participants heard as being talked over.
    "backchannel_frequency": 0.2,
    "backchannel_words": ["mm-hmm", "I see", "right"],
}

#: The participant's speaking-speed choice on the overview screen.
SPEEDS = {"slow": 0.85, "normal": 1.0, "fast": 1.15}


def web_call_body(row: dict[str, Any], agent_id: str, webhook_url: str = "", *,
                  candidate_name: str = "not given", resume: list[dict[str, str]] | None = None,
                  speed: str = "normal", metadata: dict[str, Any] | None = None,
                  engine: str | None = None) -> dict[str, Any]:
    """The body for Retell's `POST /v2/create-web-call`.

    `resume` is the transcript so far when a dropped call reconnects: the agent
    is told what was said and opens with a short "we got cut off" instead of
    its opening line.
    """
    cfg = row["cfg"]
    v = voice(cfg.get("voice") or DEFAULT_VOICE)
    persona = _s((row["agent"].get("persona") or {}).get("name")) or "Tara"
    variables = dynamic_variables(row, candidate_name, resume_text(resume, persona) if resume else "none")
    _, cap = lengths(cfg)
    user_first = cfg.get("speaker") == "Participant opens"
    if resume:
        variables["opening_line"] = "Sorry about that, we got cut off. Let's pick up where we left off."
        user_first = False
    agent_override: dict[str, Any] = {
        "voice_id": v.voice_id,
        "language": v.locale,
        # Two minutes over the cap so a closing line is never cut mid-word.
        "max_call_duration_ms": (cap + 2) * 60_000,
        **TURN_TAKING,
    }
    if speed in SPEEDS and speed != "normal":
        agent_override["voice_speed"] = SPEEDS[speed]
    # A single-prompt agent takes its opening as a per-call begin message. A
    # conversation flow has none: its global prompt speaks {{opening_line}}
    # first, word for word, so the variable carries the opening (or "" / the
    # reconnect line), and only who speaks first is overridden.
    engine = engine or ("flow" if config.RETELL_AGENT_BUILDER_FLOW_ID else "llm")
    starter = "user" if user_first else "agent"
    llm_override = ({"conversation_flow": {"start_speaker": starter}} if engine == "flow"
                    else {"retell_llm": {"start_speaker": starter, "begin_message": variables["opening_line"]}})
    if webhook_url:
        agent_override["webhook_url"] = webhook_url
    return {
        "agent_id": agent_id,
        "agent_override": {"agent": agent_override, **llm_override},
        "retell_llm_dynamic_variables": variables,
        "metadata": {
            "source": "tara-agent-builder",
            "agent_id": row.get("agent_id", ""),
            "version": int(row.get("version") or 0),
            **(metadata or {}),
        },
    }


def assessment_content(row: dict[str, Any]) -> list[str]:
    """Every string that belongs to the scorer and nobody else.

    That is each skill's anchor ("what a 5 looks like"). Skill NAMES are not
    secret: they are sent on purpose as {{skills_focus}}, so the voice agent
    knows what the conversation should bring out, and question tags are the
    same names. Treating long names as secret refused every agent whose skill
    happened to be called "Cross-Functional Collaboration".
    """
    agent = row.get("agent") or {}
    out = [_s(r.get("anchor")) for r in agent.get("rubric") or []]
    # Short strings are skipped: a one-line anchor fragment can coincide with
    # ordinary words. A check that fires on coincidence gets switched off.
    return sorted({s for s in out if len(s) > 24})


def leaked_cues(body: dict[str, Any], row: dict[str, Any]) -> list[str]:
    blob = json.dumps(body).lower()
    return [cue for cue in assessment_content(row) if cue.lower() in blob]
