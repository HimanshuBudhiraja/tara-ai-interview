import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  ChevronRight,
  Clock,
  FileText,
  MessageSquareQuote,
  Play,
  RefreshCw,
} from "lucide-react";
import {
  adminApi,
  type AssessmentResult,
  type Evaluation,
  type PilotReview,
  type ResultEvidence,
  type ResultQuestion,
  type ResultSkill,
  type ReviewReason,
  type ReviewVerdict,
} from "../lib/adminApi";
import { ApiError } from "../lib/api";
import { Badge, Button, Callout, Spinner } from "@tara/ui/primitives";
import {
  CONFIDENCE,
  DISCUSSION,
  EVIDENCE_TYPE,
  FAILURE_MEANING,
  INTERVIEW_DEPTH_HINT,
  STAGE_LABEL,
  STAGE_SHORT,
  STATUS,
  STRENGTH_LABEL,
  dimensionLabel,
  interviewDepthLabel,
} from "../lib/evaluation";
import {
  Bullets,
  ConfidencePill,
  CriterionGrid,
  DepthPanel,
  DepthSummary,
  Dimensions,
  DiscussionPill,
  EvidenceBlock,
} from "../components/evaluation";
import { cn } from "../lib/cn";

/**
 * The evaluation report for one completed interview.
 *
 * It renders the persisted `deep_evidence_v1` result and nothing else. There is
 * no arithmetic on this screen: the total, the percentage, the rating and the
 * recommendation are read from the evaluation the engine recorded, so what a
 * recruiter reads here is the same object the audit trail names. A frontend
 * that recomputed any of them would be a second scoring system, and the two
 * would eventually disagree in front of a candidate.
 *
 * Nor is there anything to edit. Overriding a recorded evaluation is a real
 * feature with its own audit requirements; a text box that silently changes a
 * hiring document is not that feature.
 *
 * The whole report is two requests — the evaluation and its evidence — fetched
 * together. Scores never flash: until the evaluation is `completed` there is no
 * score object to render, so there is nothing stale to show.
 */
export function EvaluationReport({
  sessionId,
  /** True when the surrounding screen already names the candidate. */
  embedded = false,
}: {
  sessionId: string;
  embedded?: boolean;
}) {
  /** Set when the evaluation is completed and self-consistent. */
  const [result, setResult] = useState<AssessmentResult | null>(null);
  /** Set instead when there is no result yet — pending, running or failed. */
  const [evaluation, setEvaluation] = useState<Evaluation | null>(null);
  const [violations, setViolations] = useState<string[]>([]);
  const [state, setState] = useState<
    "loading" | "ready" | "absent" | "inconsistent" | "error"
  >("loading");
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  const load = useCallback(async () => {
    // ONE request for every state. 200 is a completed, self-consistent
    // assessment; 409 carries either the status envelope (nothing to score yet)
    // or the violations that stopped a contradictory result being shown; 404
    // means nobody has asked for an evaluation.
    try {
      const assessment = await adminApi.evaluationResult(sessionId);
      setResult(assessment);
      setEvaluation(null);
      setState("ready");
    } catch (e) {
      const err = e as ApiError;
      if (err.status === 404) {
        setResult(null);
        setEvaluation(null);
        setState("absent");
        return;
      }
      if (err.status === 409) {
        const detail = err.detail as
          | { evaluation?: Evaluation; violations?: string[] }
          | undefined;
        if (detail?.evaluation) {
          setResult(null);
          setEvaluation(detail.evaluation);
          setState("ready");
          return;
        }
        setViolations(detail?.violations ?? []);
        setError(err.message);
        setState("inconsistent");
        return;
      }
      setError(err.message || "Could not load the evaluation.");
      setState("error");
    }
  }, [sessionId]);

  useEffect(() => {
    setState("loading");
    setResult(null);
    setEvaluation(null);
    setViolations([]);
    setError(null);
    void load();
  }, [load]);

  const run = async () => {
    setRunning(true);
    setError(null);
    try {
      await adminApi.runEvaluation(sessionId);
      await load();
    } catch (e) {
      setError((e as ApiError).message || "Could not run the evaluation.");
    } finally {
      setRunning(false);
    }
  };

  if (state === "loading") {
    return (
      <div className="card flex h-48 items-center justify-center gap-2.5 text-sm text-gray-500">
        <Spinner className="h-4 w-4" />
        Loading the evaluation…
      </div>
    );
  }

  if (state === "error") {
    return (
      <Callout tone="error" title="Couldn't load the evaluation" icon={<AlertTriangle className="h-4 w-4" />}>
        {error}
        <div className="mt-2.5">
          <Button variant="secondary" size="sm" onClick={() => void load()}>
            <RefreshCw className="h-3.5 w-3.5" />
            Try again
          </Button>
        </div>
      </Callout>
    );
  }

  if (state === "inconsistent") {
    return (
      <Callout
        tone="error"
        title="This assessment result was not shown"
        icon={<AlertTriangle className="h-4 w-4" />}
      >
        <p className="leading-relaxed">
          The evaluation's own figures disagree with each other, so the backend refused
          to serve them rather than render a page that says two different things. This
          is a defect to report, not a result about the candidate.
        </p>
        {violations.length > 0 && (
          <ul className="mt-2 space-y-0.5">
            {violations.map((v, i) => (
              <li key={i} className="font-mono text-xs">
                {v}
              </li>
            ))}
          </ul>
        )}
      </Callout>
    );
  }

  if (state === "absent" || (!result && !evaluation)) {
    return (
      <div className="space-y-4">
        {error && <Callout tone="error">{error}</Callout>}
        <section className="card p-6 text-center">
          <FileText className="mx-auto h-5 w-5 text-gray-300" />
          <h2 className="mt-3 text-md font-semibold text-gray-900">
            No evaluation for this interview yet
          </h2>
          <p className="mx-auto mt-1 max-w-[54ch] text-sm leading-relaxed text-gray-500">
            An evaluation is queued when an interview completes, and runs separately from
            the candidate's session. If this interview is complete, you can run it now.
          </p>
          <div className="mt-4">
            <Button onClick={run} loading={running}>
              {!running && <Play className="h-4 w-4" />}
              Run evaluation
            </Button>
          </div>
        </section>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {error && <Callout tone="error">{error}</Callout>}

      {result ? (
        <ResultHeader result={result} embedded={embedded} />
      ) : (
        <Header evaluation={evaluation!} embedded={embedded} />
      )}

      {evaluation?.status === "pending" && (
        <Waiting evaluation={evaluation} onRun={run} running={running} />
      )}
      {evaluation?.status === "running" && <Waiting evaluation={evaluation} />}
      {evaluation?.status === "failed" && (
        <Failed evaluation={evaluation} onRetry={run} retrying={running} />
      )}
      {result && <Completed result={result} />}
    </div>
  );
}

// --------------------------------------------------------------------------- //
//  Header — who, which interview, and where the evaluation stands
// --------------------------------------------------------------------------- //
function Header({
  evaluation,
  embedded,
}: {
  evaluation: Evaluation;
  embedded: boolean;
}) {
  const status = STATUS[evaluation.status] ?? STATUS.pending;
  const details = evaluation.candidate_details;
  const interview = evaluation.interview;
  const session = evaluation.session;
  const depth = interviewDepthLabel(interview.interview_depth);
  const name = details?.name || session.candidate_name || "Candidate";

  return (
    <section className="card p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          {embedded ? (
            <h2 className="text-md font-semibold text-gray-900">Evaluation</h2>
          ) : (
            <h2 className="text-xl font-semibold tracking-tight text-gray-900">{name}</h2>
          )}
          <p className="mt-0.5 text-sm text-gray-500">
            {details?.job_role || interview.role_title}
            {details?.experience_level && <> · {details.experience_level}</>}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={status.tone} dot>
            {status.label}
          </Badge>
          {session.phase === "complete" ? (
            <Badge tone="neutral">Interview complete</Badge>
          ) : (
            <Badge tone="warning">Interview {session.phase || "incomplete"}</Badge>
          )}
        </div>
      </div>

      <dl className="mt-4 grid gap-x-8 gap-y-3 border-t border-gray-100 pt-4 sm:grid-cols-2 lg:grid-cols-4">
        <Fact label="Interview">{interview.title || "—"}</Fact>
        <Fact label="Version">
          <span className="tabular">v{interview.version}</span>
        </Fact>
        <Fact label="Interview depth" hint={INTERVIEW_DEPTH_HINT}>
          {depth || "—"}
        </Fact>
        <Fact label="Questions asked">
          <span className="tabular">{session.questions_asked}</span>
        </Fact>
      </dl>

      <p className="meta mt-3">
        Evaluated by <span className="font-semibold">{evaluation.evaluation_engine_version}</span>{" "}
        against published version v{interview.version}
        {evaluation.attempt > 1 && <> · run {evaluation.attempt}</>}
      </p>
    </section>
  );
}

function Fact({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <dt className="label">{label}</dt>
      <dd className="mt-1 text-base font-medium text-gray-900">{children}</dd>
      {hint && <p className="mt-0.5 text-xs leading-relaxed text-gray-400">{hint}</p>}
    </div>
  );
}

// --------------------------------------------------------------------------- //
//  Pending / running
// --------------------------------------------------------------------------- //
function Waiting({
  evaluation,
  onRun,
  running,
}: {
  evaluation: Evaluation;
  onRun?: () => void;
  running?: boolean;
}) {
  const status = STATUS[evaluation.status] ?? STATUS.pending;
  return (
    <section className="card p-6 text-center">
      {evaluation.status === "running" ? (
        <Spinner className="mx-auto h-5 w-5 text-gray-400" />
      ) : (
        <Clock className="mx-auto h-5 w-5 text-gray-300" />
      )}
      <h2 className="mt-3 text-md font-semibold text-gray-900">{status.label}</h2>
      <p className="mx-auto mt-1 max-w-[56ch] text-sm leading-relaxed text-gray-500">
        {status.meaning} There is no score to show until it does — a partial one would be
        a number nobody produced.
      </p>
      {onRun && (
        <div className="mt-4">
          <Button onClick={onRun} loading={running}>
            {!running && <Play className="h-4 w-4" />}
            Run it now
          </Button>
        </div>
      )}
    </section>
  );
}

// --------------------------------------------------------------------------- //
//  Failed
// --------------------------------------------------------------------------- //
function Failed({
  evaluation,
  onRetry,
  retrying,
}: {
  evaluation: Evaluation;
  onRetry: () => void;
  retrying: boolean;
}) {
  const kind = evaluation.error_kind ?? "model";
  return (
    <section className="card border-error-200 p-5">
      <div className="flex gap-3">
        <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-error-600" />
        <div className="min-w-0">
          <h2 className="text-md font-semibold text-gray-900">Evaluation failed</h2>
          <p className="mt-1 max-w-[70ch] text-sm leading-relaxed text-gray-600">
            {/* The distinction that matters most on this screen. */}
            This is a failure of the evaluation run, not a result about the candidate.
            Nothing about their interview has been judged.
          </p>
          <p className="mt-2 max-w-[70ch] text-sm leading-relaxed text-gray-600">
            {FAILURE_MEANING[kind] ?? FAILURE_MEANING.model}
          </p>
          {evaluation.error && (
            <p className="mt-2 rounded-md bg-gray-50 px-3 py-2 font-mono text-xs leading-relaxed text-gray-600">
              {evaluation.failed_stage && (
                <span className="text-gray-500">{evaluation.failed_stage}: </span>
              )}
              {evaluation.error}
            </p>
          )}
          <div className="mt-3.5 flex items-center gap-2.5">
            <Button variant="secondary" onClick={onRetry} loading={retrying}>
              {!retrying && <RefreshCw className="h-4 w-4" />}
              Run it again
            </Button>
            <span className="meta">Starts a new evaluation run; the failed one is kept.</span>
          </div>
        </div>
      </div>
    </section>
  );
}

// --------------------------------------------------------------------------- //
//  Header, from the result
// --------------------------------------------------------------------------- //
function ResultHeader({
  result,
  embedded,
}: {
  result: AssessmentResult;
  embedded: boolean;
}) {
  const { interview, session, evaluation } = result;
  const depth = interviewDepthLabel(interview.interview_depth);

  return (
    <section className="card p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          {embedded ? (
            <h2 className="text-md font-semibold text-gray-900">Evaluation</h2>
          ) : (
            <h2 className="text-xl font-semibold tracking-tight text-gray-900">
              {session.candidate_name || "Candidate"}
            </h2>
          )}
          <p className="mt-0.5 text-sm text-gray-500">
            {interview.role_title}
            {interview.experience_level && <> · {interview.experience_level}</>}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone="success" dot>
            Evaluated
          </Badge>
          {session.phase === "complete" ? (
            <Badge tone="neutral">Interview complete</Badge>
          ) : (
            <Badge tone="warning">Interview {session.phase || "incomplete"}</Badge>
          )}
        </div>
      </div>

      <dl className="mt-4 grid gap-x-8 gap-y-3 border-t border-gray-100 pt-4 sm:grid-cols-2 lg:grid-cols-4">
        <Fact label="Interview">{interview.title || "—"}</Fact>
        <Fact label="Version">
          <span className="tabular">v{interview.version}</span>
        </Fact>
        <Fact label="Interview depth" hint={INTERVIEW_DEPTH_HINT}>
          {depth || "—"}
        </Fact>
        <Fact label="Questions asked">
          <span className="tabular">{session.questions_asked}</span>
        </Fact>
      </dl>

      <p className="meta mt-3">
        Evaluated by <span className="font-semibold">{evaluation.engine_version}</span>{" "}
        against published version v{interview.version}
        {evaluation.attempt > 1 && <> · run {evaluation.attempt}</>}
        {session.completed_at && (
          <> · completed {new Date(session.completed_at * 1000).toLocaleString()}</>
        )}
      </p>
    </section>
  );
}

// --------------------------------------------------------------------------- //
//  Completed — every figure read, none derived
// --------------------------------------------------------------------------- //
function Completed({ result }: { result: AssessmentResult }) {
  const { overall, coverage, summary, skills, questions, scales } = result;

  const evidenceBySkill = new Map<string, ResultEvidence[]>();
  for (const item of result.evidence) {
    evidenceBySkill.set(item.skill_id, [
      ...(evidenceBySkill.get(item.skill_id) ?? []),
      item,
    ]);
  }
  const questionsById = new Map(questions.map((q) => [q.question_id, q]));

  return (
    <>
      {/* --- overall --- */}
      <section className="card overflow-hidden">
        <div className="flex flex-wrap items-start gap-x-10 gap-y-4 border-b border-gray-200 bg-gray-25 px-5 py-4">
          <div>
            <p className="label">Total score</p>
            <p className="tabular mt-1 text-stat font-semibold leading-none text-gray-900">
              {overall.total_score}
              <span className="ml-1 text-lg font-normal text-gray-400">
                / {overall.max_score}
              </span>
            </p>
            <p className="tabular meta mt-1.5">{overall.percentage}%</p>
          </div>
          <div>
            <p className="label">Overall rating</p>
            <p className="mt-1 text-xl font-semibold leading-none text-gray-900">
              {overall.overall_rating}
            </p>
          </div>
          <div className="min-w-[220px]">
            <p className="label">Recommendation</p>
            <p className="mt-1 text-xl font-semibold leading-tight text-gray-900">
              {overall.recommendation}
            </p>
          </div>
          <CoverageBlock coverage={coverage} />
        </div>

        <div className="px-5 py-4">
          <p className="max-w-[86ch] text-sm leading-relaxed text-gray-600">
            {summary.recommendation_explaination}
          </p>
          {summary.recommendation_boundary?.at_boundary && (
            /* Said out loud rather than smoothed away. Measured on this build:
               five evaluations of one unchanged interview moved the score by two
               points and the recommendation across a threshold, because one
               criterion sat on the line. A reader deciding on the strength of
               the word alone should know when the word is that close to a
               different one. */
            <p className="mt-3 max-w-[86ch] rounded-md border border-warning-200 bg-warning-25 px-3 py-2 text-xs leading-relaxed text-warning-700">
              <span className="font-semibold">
                This recommendation is close to a threshold.
              </span>{" "}
              {sentence(summary.recommendation_boundary.reasons.join("; "))}. The score,
              the coverage and the evidence below do not move with it — read
              those, and treat the recommendation as the summary it is.
            </p>
          )}
          <p className="meta mt-3">
            The score covers {overall.scored_over}. Skills that were only mentioned or
            never asked about are reported as coverage above, not counted against the
            candidate. Every figure here was produced by the evaluation engine and is
            shown unchanged.
          </p>
        </div>

        {/* Two percentages sit side by side above, and they answer different
            questions. Said outright whenever the interview left something
            unassessed — no threshold, because naming the skills makes the
            severity self-evident and a cutoff would be invented. */}
        {coverage.skills_discussed < coverage.skills_total && (
          <div className="border-t border-gray-200 bg-warning-25 px-5 py-3.5">
            <p className="max-w-[86ch] text-sm leading-relaxed text-warning-700">
              <span className="font-semibold">
                This assessment covers {coverage.skills_discussed} of{" "}
                {coverage.skills_total} skills.
              </span>{" "}
              {unassessed(skills).length > 0 && (
                <>
                  The interview did not substantively assess{" "}
                  {unassessed(skills).join(", ")}.{" "}
                </>
              )}
              Coverage is a measure of how complete the interview was, not of how the
              candidate performed — the {overall.percentage}% above is their score on
              what was assessed, and it is not reduced by what was not.
            </p>
          </div>
        )}
      </section>

      {/* --- strengths / improvements --- */}
      {(summary.strengths.length > 0 || summary.areas_for_improvement.length > 0) && (
        <section className="card grid gap-5 p-4 sm:grid-cols-2 sm:p-5">
          <div>
            <h3 className="mb-2 text-md font-semibold text-gray-900">Strengths</h3>
            <Bullets items={summary.strengths} tone="positive" />
          </div>
          <div>
            <h3 className="mb-2 text-md font-semibold text-gray-900">
              Areas for improvement
            </h3>
            <Bullets items={summary.areas_for_improvement} tone="neutral" />
          </div>
        </section>
      )}

      {/* --- skills --- */}
      <section className="space-y-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h3 className="text-md font-semibold text-gray-900">Skill assessment</h3>
          <p className="meta">
            Every skill the published version listed, in the order it lists them.
          </p>
        </div>
        {skills.map((skill) => (
          <SkillCard
            key={skill.skill_id}
            skill={skill}
            criteriaOrder={scales.criteria}
            criterionMax={scales.criterion_max}
            evidence={evidenceBySkill.get(skill.skill_id) ?? []}
            questions={skill.question_ids
              .map((id) => questionsById.get(id))
              .filter((q): q is ResultQuestion => q !== undefined)}
          />
        ))}
      </section>

      {result.integrity.quarantined_evidence > 0 && (
        <Callout tone="warning" title="Some evidence was refused">
          <p className="leading-relaxed">
            {result.integrity.quarantined_evidence}{" "}
            {result.integrity.quarantined_evidence === 1 ? "item was" : "items were"}{" "}
            rejected before being recorded and reached no score. The text is not shown,
            because the most common reason is a quote the candidate never said.
          </p>
        </Callout>
      )}

      <PilotReviewPanel
        sessionId={result.session.session_id}
        evaluationId={result.evaluation.evaluation_id}
        recommendations={result.scales.recommendations}
        aiRecommendation={overall.recommendation}
      />
    </>
  );
}

// --------------------------------------------------------------------------- //
//  Pilot review
// --------------------------------------------------------------------------- //
const REASON_LABEL: Record<ReviewReason, string> = {
  wrong_evidence: "Evidence is wrong or misattributed",
  wrong_skill: "Attributed to the wrong skill",
  wrong_score: "The score does not match the evidence",
  wrong_recommendation: "The recommendation is wrong",
  insufficient_coverage: "Too little was assessed to decide",
  other: "Something else",
};

/** The engine writes reasons as clauses; this one follows a full stop. */
function sentence(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function remembered(key: string): string {
  try {
    return window.localStorage?.getItem(key) ?? "";
  } catch {
    return "";
  }
}

function remember(key: string, value: string): void {
  try {
    window.localStorage?.setItem(key, value);
  } catch {
    /* a name we cannot remember is not a failure worth showing anyone */
  }
}

const VERDICT_LABEL: Record<ReviewVerdict, string> = {
  agree: "Agree",
  disagree: "Disagree",
  needs_review: "Needs review",
};

/**
 * What a pilot reviewer thought — recorded beside the assessment, never inside it.
 *
 * This is calibration data, and it is deliberately not an override: there is no
 * field here that writes a score, and nothing the evaluator reads. A reviewer
 * who disagrees says which part they disagree with, and that category is what
 * becomes a benchmark case later. One recruiter's opinion retuning the product
 * on the spot is the failure mode this shape exists to prevent.
 */
function PilotReviewPanel({
  sessionId,
  evaluationId,
  recommendations,
  aiRecommendation,
}: {
  sessionId: string;
  evaluationId: string;
  recommendations: string[];
  aiRecommendation: string;
}) {
  // Remembering the reviewer's name is a convenience, not state the review
  // depends on — and `localStorage` is not always there (a private window, a
  // browser with site data off, a test runner whose global is a stub). Both
  // directions are guarded so a missing store costs a retyped name, not a
  // screen that will not render.
  const [reviewer, setReviewer] = useState(() => remembered("tara.reviewer"));
  const [verdict, setVerdict] = useState<ReviewVerdict | null>(null);
  const [reasons, setReasons] = useState<ReviewReason[]>([]);
  const [recommendation, setRecommendation] = useState("");
  const [note, setNote] = useState("");
  const [saved, setSaved] = useState<PilotReview[]>([]);
  const [state, setState] = useState<"idle" | "saving" | "error">("idle");
  const [message, setMessage] = useState("");

  useEffect(() => {
    let live = true;
    // Defensive on both sides: the review list is a nicety, and neither a
    // failed request nor an unexpected shape may take the assessment above it
    // off the screen.
    adminApi
      .reviews(sessionId)
      .then((body) => live && setSaved(Array.isArray(body?.reviews) ? body.reviews : []))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [sessionId, evaluationId]);

  const toggleReason = (reason: ReviewReason) =>
    setReasons((prev) =>
      prev.includes(reason) ? prev.filter((r) => r !== reason) : [...prev, reason],
    );

  const submit = async () => {
    if (!verdict || !reviewer.trim()) return;
    setState("saving");
    setMessage("");
    try {
      remember("tara.reviewer", reviewer.trim());
      const row = await adminApi.saveReview(sessionId, {
        reviewer: reviewer.trim(),
        verdict,
        reasons: verdict === "disagree" ? reasons : [],
        recommendation,
        note,
      });
      setSaved((prev) => [...prev.filter((r) => r.reviewer !== row.reviewer), row]);
      setState("idle");
      setMessage("Recorded. It changes nothing about the assessment above.");
      setNote("");
    } catch (error) {
      setState("error");
      setMessage(
        error instanceof ApiError ? error.message : "That could not be saved.",
      );
    }
  };

  const blocked = !verdict || !reviewer.trim() || (verdict === "disagree" && reasons.length === 0);

  return (
    <section className="card p-4 sm:p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-md font-semibold text-gray-900">Pilot review</h3>
        <p className="meta">
          Calibration data. Recorded beside this assessment — it cannot change the
          score, the recommendation or the evidence.
        </p>
      </div>

      <div className="mt-3.5 flex flex-wrap items-center gap-2">
        <input
          className="field w-56"
          placeholder="Your name"
          value={reviewer}
          onChange={(e) => setReviewer(e.target.value)}
          aria-label="Reviewer name"
        />
        <div className="flex gap-1.5" role="group" aria-label="Your verdict">
          {(Object.keys(VERDICT_LABEL) as ReviewVerdict[]).map((value) => (
            <button
              key={value}
              type="button"
              aria-pressed={verdict === value}
              onClick={() => setVerdict(value)}
              className={cn(
                "rounded-md border px-3 py-1.5 text-sm font-medium transition-colors",
                verdict === value
                  ? "border-brand-500 bg-brand-50 text-brand-700"
                  : "border-gray-300 bg-surface text-gray-700 hover:bg-gray-50",
              )}
            >
              {VERDICT_LABEL[value]}
            </button>
          ))}
        </div>
      </div>

      {verdict === "disagree" && (
        <fieldset className="mt-3.5">
          <legend className="label mb-1.5">
            What is wrong? (at least one — a disagreement with no category is one
            nobody can act on)
          </legend>
          <div className="flex flex-wrap gap-1.5">
            {(Object.keys(REASON_LABEL) as ReviewReason[]).map((reason) => (
              <button
                key={reason}
                type="button"
                aria-pressed={reasons.includes(reason)}
                onClick={() => toggleReason(reason)}
                className={cn(
                  "rounded-md border px-2.5 py-1 text-xs font-medium transition-colors",
                  reasons.includes(reason)
                    ? "border-warning-300 bg-warning-25 text-warning-700"
                    : "border-gray-300 bg-surface text-gray-600 hover:bg-gray-50",
                )}
              >
                {REASON_LABEL[reason]}
              </button>
            ))}
          </div>
        </fieldset>
      )}

      {verdict !== null && (
        <div className="mt-3.5 grid gap-3 sm:grid-cols-2">
          <label className="block">
            <span className="label">What would you have recommended?</span>
            <select
              className="field-select mt-1 h-9 w-full text-base"
              value={recommendation}
              onChange={(e) => setRecommendation(e.target.value)}
            >
              <option value="">— no view —</option>
              {(recommendations ?? []).map((value) => (
                <option key={value} value={value}>
                  {value}
                  {value === aiRecommendation ? " (Tara's)" : ""}
                </option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="label">Anything a future benchmark case should capture?</span>
            <textarea
              className="field-textarea mt-1 h-[38px] w-full resize-y"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              maxLength={1000}
            />
          </label>
        </div>
      )}

      <div className="mt-3.5 flex flex-wrap items-center gap-2.5">
        <Button onClick={submit} disabled={blocked} loading={state === "saving"}>
          Record review
        </Button>
        {message && (
          <span
            className={cn(
              "text-sm",
              state === "error" ? "text-error-600" : "text-gray-500",
            )}
          >
            {message}
          </span>
        )}
      </div>

      {saved.length > 0 && (
        <ul className="mt-4 space-y-1.5 border-t border-gray-200 pt-3.5">
          {saved.map((row) => (
            <li key={row.reviewer} className="text-sm text-gray-600">
              <span className="font-medium text-gray-900">{row.reviewer}</span>{" "}
              {VERDICT_LABEL[row.verdict]}
              {row.reasons.length > 0 && (
                <> — {row.reasons.map((r) => REASON_LABEL[r]).join(", ")}</>
              )}
              {row.recommendation && row.recommendation !== row.ai_recommendation && (
                <> · would have said “{row.recommendation}”</>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** The skills the interview never substantively assessed, in published order. */
function unassessed(skills: ResultSkill[]): string[] {
  return skills
    .filter((s) => s.discussion_status !== "discussed")
    .map((s) => s.skill_name);
}

/**
 * Coverage, sitting beside the score and never inside it.
 *
 * It answers a different question — "how much of the assessment did we get
 * to?" — and folding it into the score is what once made a candidate who
 * answered one skill superbly read as Poor.
 */
function CoverageBlock({ coverage }: { coverage: AssessmentResult["coverage"] }) {
  const parts = [
    coverage.skills_mentioned > 0 && `${coverage.skills_mentioned} mentioned`,
    coverage.skills_not_discussed > 0 && `${coverage.skills_not_discussed} not asked`,
  ].filter(Boolean) as string[];

  return (
    <div>
      <p className="label">Interview coverage</p>
      <p className="tabular mt-1 text-xl font-semibold leading-none text-gray-900">
        {coverage.skills_discussed}
        <span className="text-sm font-normal text-gray-400">
          {" "}
          of {coverage.skills_total} skills
        </span>
      </p>
      <p className="meta mt-1.5">
        {coverage.coverage_percentage}% assessed
        {parts.length > 0 && <> · {parts.join(", ")}</>}
      </p>
    </div>
  );
}

// --------------------------------------------------------------------------- //
//  One skill
// --------------------------------------------------------------------------- //
function SkillCard({
  skill,
  criteriaOrder,
  criterionMax,
  evidence,
  questions,
}: {
  skill: ResultSkill;
  criteriaOrder: string[];
  criterionMax: number;
  evidence: ResultEvidence[];
  questions: ResultQuestion[];
}) {
  const discussion = DISCUSSION[skill.discussion_status] ?? DISCUSSION.not_discussed;
  const confidence =
    CONFIDENCE[skill.depth.evidence_confidence] ?? CONFIDENCE.insufficient;
  const notDiscussed = skill.discussion_status === "not_discussed";

  return (
    <article
      className={cn(
        "card overflow-hidden",
        // A coverage gap is drawn as an absence, never as a failing grade.
        notDiscussed && "border-dashed bg-gray-25",
      )}
    >
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-gray-200 px-4 py-3">
        <div className="min-w-0">
          <h4 className="text-md font-semibold text-gray-900">{skill.skill_name}</h4>
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            <DiscussionPill status={skill.discussion_status} />
            <ConfidencePill confidence={skill.depth.evidence_confidence} />
            <DepthSummary depth={skill.depth} />
          </div>
        </div>
        {!notDiscussed && (
          <div className="shrink-0 text-right">
            <p className="tabular text-xl font-semibold leading-none text-gray-900">
              {skill.score}
              {/* The maximum comes from the payload. Nothing here knows 25. */}
              <span className="ml-0.5 text-sm font-normal text-gray-400">
                /{skill.max_score}
              </span>
            </p>
            {!skill.counts_toward_overall_score && (
              <p className="meta mt-1">not counted in the total</p>
            )}
          </div>
        )}
      </div>

      <div className="space-y-4 px-4 py-4">
        <p className="max-w-[86ch] text-sm leading-relaxed text-gray-600">
          {skill.remarks}
        </p>

        <p className="rounded-md border border-gray-200 bg-gray-25 px-3 py-2 text-xs leading-relaxed text-gray-500">
          {discussion.meaning}
          {skill.discussion_status !== "discussed" && <> {confidence.meaning}</>}
        </p>

        {!notDiscussed && (
          <>
            {/* The scale is always 0-5 — `criterion_ceiling` says what was
                reachable at this status, and the muted styling carries that. */}
            <CriterionGrid
              criteria={skill.criteria}
              order={criteriaOrder}
              max={criterionMax}
              muted={skill.discussion_status !== "discussed"}
            />
            <DepthPanel depth={skill.depth} status={skill.discussion_status} />
            <Dimensions depth={skill.depth} />
            <QuestionDrilldown questions={questions} evidence={evidence} />
          </>
        )}
      </div>
    </article>
  );
}

/**
 * The drill-down: what was asked, what came back, and the evidence taken from it.
 *
 * Grouped by question rather than by evidence item, because "why did Tara say
 * this?" is answered by reading the exchange, not a list of quotes detached
 * from what prompted them.
 */
function QuestionDrilldown({
  questions,
  evidence,
}: {
  questions: ResultQuestion[];
  evidence: ResultEvidence[];
}) {
  if (questions.length === 0) {
    return <EvidenceBlock items={evidence} skillName="" />;
  }
  return (
    <details className="group">
      <summary
        className={cn(
          "flex cursor-pointer list-none items-center gap-1.5 rounded-sm py-1 text-sm font-semibold",
          "text-brand-600 outline-none hover:text-brand-700",
          "focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2",
        )}
      >
        <ChevronRight className="h-3.5 w-3.5 transition-transform group-open:rotate-90" />
        Questions and evidence
        <span className="font-normal text-gray-400">
          ({questions.length} {questions.length === 1 ? "question" : "questions"},{" "}
          {evidence.length} {evidence.length === 1 ? "item" : "items"})
        </span>
      </summary>

      <div className="mt-3 space-y-4">
        {questions.map((question) => {
          const mine = evidence.filter((e) => e.question_id === question.question_id);
          return (
            <div
              key={question.question_id}
              className="rounded-md border border-gray-200 bg-surface"
            >
              <div className="border-b border-gray-100 px-3.5 py-2.5">
                <p className="label">Question</p>
                <p className="mt-0.5 text-sm leading-relaxed text-gray-900">
                  {question.question_text}
                </p>
                <p className="meta mt-1.5">
                  Reached {STAGE_SHORT[question.depth_reached] ?? question.depth_reached} ·
                  Demonstrated{" "}
                  {STAGE_SHORT[question.depth_demonstrated] ?? question.depth_demonstrated}
                  {question.probe_count > 0 && (
                    <>
                      {" "}
                      · {question.probe_count} follow-up
                      {question.probe_count === 1 ? "" : "s"}
                    </>
                  )}
                </p>
              </div>

              <ol className="divide-y divide-gray-100">
                {question.turns.map((turn) => (
                  <li key={turn.turn_id} className="px-3.5 py-3">
                    <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
                      <Badge tone="neutral">
                        {STAGE_LABEL[turn.stage] ?? turn.stage}
                      </Badge>
                      {turn.excluded && (
                        <Badge tone="warning">
                          Excluded{turn.excluded_reason ? ` · ${turn.excluded_reason}` : ""}
                        </Badge>
                      )}
                    </div>
                    {/* The first turn's prompt IS the question, already shown
                        above. Only the follow-ups add something to read. */}
                    {turn.prompt && turn.prompt !== question.question_text && (
                      <p className="mb-1 text-xs leading-relaxed text-gray-500">
                        {turn.prompt}
                      </p>
                    )}
                    <p
                      className={cn(
                        "text-sm leading-relaxed",
                        turn.excluded ? "text-gray-400 line-through" : "text-gray-700",
                      )}
                    >
                      {turn.answer || <span className="italic text-gray-400">No answer</span>}
                    </p>
                  </li>
                ))}
              </ol>

              {mine.length > 0 && (
                <div className="border-t border-gray-100 bg-gray-25 px-3.5 py-3">
                  <p className="label mb-2">Evidence taken from this question</p>
                  <ul className="space-y-2.5">
                    {mine.map((item) => (
                      <li key={item.turn_id + item.candidate_quote}>
                        <div className="mb-1 flex flex-wrap items-center gap-1.5">
                          <Badge tone="info">{dimensionLabel(item.dimension)}</Badge>
                          <Badge
                            tone={
                              (EVIDENCE_TYPE[item.evidence_type] ?? EVIDENCE_TYPE.unclear)
                                .tone
                            }
                          >
                            {(EVIDENCE_TYPE[item.evidence_type] ?? EVIDENCE_TYPE.unclear)
                              .label}
                          </Badge>
                          <span className="meta">
                            {STRENGTH_LABEL[item.evidence_strength] ??
                              item.evidence_strength}{" "}
                            · supports {item.supports_criterion}
                          </span>
                        </div>
                        <blockquote className="flex gap-2 border-l-2 border-gray-200 pl-3">
                          <MessageSquareQuote className="mt-0.5 h-3.5 w-3.5 shrink-0 text-gray-300" />
                          <p className="text-sm leading-relaxed text-gray-700">
                            “{item.candidate_quote}”
                          </p>
                        </blockquote>
                        <p className="meta mt-1">Turn {item.turn_id}</p>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </details>
  );
}
