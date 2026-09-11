"""Reconstructing an interview as stage-tagged turns.

The runtime already records everything needed; nothing here changes it. An
`ItemRecord` holds the question, the answers in order, and the probes asked
between them, which is exactly the probe ladder:

    question  ─►  answers[0]        stage: direct
    probe 1   ─►  answers[1]        stage: probed
    probe 2   ─►  answers[2]        stage: deep_probed

`max_probes` (default 2) caps the ladder at three rungs, which is where the
three depth stages come from. A question the candidate answered well enough at
the first rung has no second rung — and that is the runtime working, not the
candidate underperforming.

Two things this module refuses to pass on:

  * a turn the injection scanner flagged, which cannot become evidence;
  * anything that is not the candidate speaking.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from packages.types.evaluation import STAGES, DepthStage, deeper_of
from services.orchestrator import guardrails
from services.orchestrator.state import ItemRecord, SessionState


@dataclass
class Turn:
    """One candidate answer, and the rung of the ladder it came from."""

    turn_id: str
    question_id: str
    question_text: str
    prompt_text: str          # the question or probe this answered
    answer: str
    depth_stage: DepthStage
    skill_id: str = ""
    task_id: str = ""
    #: True when `scan_candidate_turn` flagged it. Excluded from evidence, kept
    #: visible so a reviewer can see it happened rather than wondering why a
    #: skill looks thin.
    flagged: bool = False
    flag_reason: str = ""

    @property
    def usable(self) -> bool:
        return bool(self.answer.strip()) and not self.flagged


@dataclass
class QuestionTranscript:
    question_id: str
    question_text: str
    skill_id: str
    task_id: str
    turns: list[Turn] = field(default_factory=list)
    #: How far the INTERVIEW went on this question. A fact about the
    #: conversation, not a judgement about the candidate.
    depth_reached: DepthStage = "direct"
    answered: bool = False

    def usable_turns(self) -> list[Turn]:
        return [t for t in self.turns if t.usable]


@dataclass
class InterviewTranscript:
    session_id: str
    interview_id: str
    interview_version: int
    candidate_name: str
    questions: list[QuestionTranscript] = field(default_factory=list)

    def for_skill(self, skill_id: str) -> list[QuestionTranscript]:
        return [q for q in self.questions if q.skill_id == skill_id]

    def depth_reached_for(self, skill_id: str) -> DepthStage:
        """The deepest rung the interview got to on any of a skill's questions."""
        reached: DepthStage = "direct"
        for question in self.for_skill(skill_id):
            if question.answered:
                reached = deeper_of(reached, question.depth_reached)
        return reached

    def all_quotes(self) -> str:
        """Everything the candidate said, for verbatim quote checking."""
        return "\n".join(t.answer for q in self.questions for t in q.turns)

    def turn(self, turn_id: str) -> Turn | None:
        return next(
            (t for q in self.questions for t in q.turns if t.turn_id == turn_id), None
        )


def _stage_for(index: int) -> DepthStage:
    """Rung `index` of the ladder, clamped.

    A configuration allowing more than two probes would put later answers past
    the deepest named stage; they are counted as `deep_probed` rather than
    inventing a fourth stage the contract does not have.
    """
    return STAGES[min(index, len(STAGES) - 1)]


def build(
    state: SessionState, definition: Any, *, scan: bool = True
) -> InterviewTranscript:
    """Reconstruct the interview from the session and its published version.

    `definition` is the immutable `InterviewDefinition` the candidate actually
    sat — never the current draft, so an interview is always evaluated against
    the questions and criteria it was conducted under.
    """
    by_question = {q.id: q for q in definition.questions}
    transcript = InterviewTranscript(
        session_id=state.session_id,
        interview_id=state.interview_id,
        interview_version=state.interview_version,
        candidate_name=state.candidate_name,
    )

    for item_id in state.asked_item_ids:
        record: ItemRecord | None = state.records.get(item_id)
        if record is None:
            continue
        published = by_question.get(item_id)

        question = QuestionTranscript(
            question_id=item_id,
            question_text=record.prompt,
            skill_id=(published.skill_id if published else record.competency),
            task_id=(published.task_id if published else ""),
        )

        for index, answer in enumerate(record.answers):
            # answers[0] follows the question; answers[n] follows probe n.
            prompt = (
                record.prompt if index == 0
                else record.probes_asked[index - 1]
                if index - 1 < len(record.probes_asked)
                else record.prompt
            )
            stage = _stage_for(index)
            turn = Turn(
                turn_id=f"{item_id}#{index}",
                question_id=item_id,
                question_text=record.prompt,
                prompt_text=prompt,
                answer=answer,
                depth_stage=stage,
                skill_id=question.skill_id,
                task_id=question.task_id,
            )
            if scan:
                verdict = guardrails.scan_candidate_turn(answer)
                # A turn that tried to instruct the system cannot also be
                # evidence that the candidate demonstrated something. Same rule
                # the runtime applies to `covered`, applied again here because
                # this is a second path into a score.
                turn.flagged = verdict.suspicious
                turn.flag_reason = verdict.reason
            question.turns.append(turn)

            if turn.usable:
                question.answered = True
                question.depth_reached = deeper_of(question.depth_reached, stage)

        transcript.questions.append(question)

    return transcript
