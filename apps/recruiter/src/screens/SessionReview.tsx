import { useEffect, useState } from "react";
import { ArrowLeft, Check, Minus, ScrollText, ShieldCheck, ShieldX } from "lucide-react";
import { adminApi, type SessionReview as Review, type SessionScore, type Trail } from "../lib/adminApi";
import { navigate } from "../lib/route";
import { Badge, Callout } from "@tara/ui/primitives";
import { DifficultyPill, mmss } from "../components/RecruiterShell";
import { BandPill, LevelBar } from "../components/score";
import { EvaluationReport } from "./EvaluationReport";
import { cn } from "../lib/cn";

/**
 * Reviewing one candidate's interview — every view of it, in one place.
 *
 * It opens on the evaluation, because that is what a recruiter came for. The
 * other four tabs are what makes the evaluation checkable rather than something
 * to take on trust: the cue-coverage assessment (a different measure, kept
 * separate), the exchange as it happened, the raw transcript, and the full
 * decision trail behind every question Tara chose.
 *
 * No number on this screen is produced here. The evaluation tab renders the
 * persisted result; the coverage tab renders the cue-coverage scorer's. Nothing
 * in the console computes a score, and there is no control that edits one.
 *
 * The "evidenced" lists on the Exchange tab are a model's reading of the answer,
 * not a judgement of the candidate — labelled as such on the page, because a
 * reviewer who mistakes one for the other is exactly the failure this design is
 * trying to prevent.
 */
type Tab = "evaluation" | "assessment" | "evidence" | "transcript" | "trail";

export function SessionReview({ id, initialTab = "evaluation" }: { id: string; initialTab?: Tab }) {
  const [review, setReview] = useState<Review | null>(null);
  const [trail, setTrail] = useState<Trail | null>(null);
  const [score, setScore] = useState<SessionScore | null>(null);
  const [scoreError, setScoreError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>(initialTab);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    adminApi.session(id).then(setReview).catch((e) => setError(e.message));
    adminApi.trail(id).then(setTrail).catch(() => undefined);
    // The cue-coverage scorer reads the authored question pool, so it has
    // nothing to say about a session run on a generated interview. That is a
    // limitation of the old scorer, not a failure of this screen — but an
    // unexplained skeleton that never resolves is worse than saying so.
    adminApi
      .score(id)
      .then((s) => {
        setScore(s);
        setScoreError(null);
      })
      .catch((e) => setScoreError(e.message));
  }, [id]);

  if (error) return <Callout tone="error">{error}</Callout>;
  if (!review) return <div className="card h-64 animate-pulse bg-gray-50" />;

  return (
    <div className="space-y-5">
      <button
        onClick={() => navigate({ name: "candidates" })}
        className="inline-flex items-center gap-1.5 text-sm font-medium text-gray-500 transition-colors hover:text-gray-900"
      >
        <ArrowLeft className="h-3.5 w-3.5" />
        All candidates
      </button>

      {/* --- header --- */}
      <section className="card p-4 sm:p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 className="text-xl font-semibold tracking-tight text-gray-900">
              {review.candidate_name}
            </h2>
            <p className="mt-0.5 text-sm text-gray-500">
              {review.role_title}
              {review.interview_title && <> · {review.interview_title}</>}
            </p>
          </div>
          <div className="flex flex-wrap gap-x-8 gap-y-3 text-sm">
            <Fact label="Answered">
              {review.items.filter((i) => i.answered).length} of {review.items.length}
            </Fact>
            <Fact label="Duration">{mmss(review.duration_sec)}</Fact>
            <Fact label="Channel">{review.channel === "voice" ? "Voice" : "Typed"}</Fact>
            <Fact label="Status">
              <span className="capitalize">
                {review.phase === "complete" ? "Complete" : review.phase}
              </span>
            </Fact>
          </div>
        </div>

        <div className="mt-4 flex flex-wrap gap-1.5 border-t border-gray-100 pt-4">
          {review.coverage.map((c) => (
            <span
              key={c.id}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-sm border px-1.5 py-0.5 text-2xs font-semibold",
                c.asked >= c.target
                  ? "border-success-200 bg-success-50 text-success-700"
                  : c.asked > 0
                    ? "border-gray-200 bg-gray-50 text-gray-600"
                    : "border-gray-200 bg-gray-25 text-gray-400",
              )}
            >
              {c.label}
              <span className="tabular opacity-70">
                {c.asked}/{c.target}
              </span>
            </span>
          ))}
        </div>

        {Object.keys(review.accommodations || {}).some(
          (k) => (review.accommodations as any)[k],
        ) && (
          <p className="mt-3 text-xs text-gray-500">
            Adjustments requested:{" "}
            {Object.entries(review.accommodations)
              .filter(([, v]) => v)
              .map(([k]) => k.replace(/_/g, " "))
              .join(", ")}
          </p>
        )}
      </section>

      {/* --- tabs --- */}
      <div className="flex gap-1 border-b border-gray-200">
        {(
          [
            ["evaluation", "Evaluation"],
            // The cue-coverage scorer, unchanged and still here. It answers a
            // different question from the evaluation — which authored cues each
            // answer covered — and the two are kept as separate tabs rather
            // than merged into something that is neither.
            ["assessment", "Cue coverage"],
            ["evidence", "Exchange"],
            ["transcript", "Transcript"],
            ["trail", "Decision trail"],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={cn(
              "-mb-px border-b-2 px-3 py-2 text-sm font-semibold transition-colors",
              tab === key
                ? "border-brand-500 text-gray-900"
                : "border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700",
            )}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "evaluation" && <EvaluationReport sessionId={id} embedded />}
      {tab === "assessment" && <Assessment score={score} error={scoreError} />}
      {tab === "evidence" && <Evidence review={review} />}
      {tab === "transcript" && <Transcript review={review} />}
      {tab === "trail" && <TrailView trail={trail} />}
    </div>
  );
}

// --------------------------------------------------------------------------- //
/**
 * The assessment.
 *
 * A number sits at the top of this tab, which is a meaningful change: until now
 * the console refused to show one. It is safe to show only because of what
 * surrounds it — the required level next to every skill, a confidence figure
 * that can say "don't rely on this", and the Evidence tab one click away with
 * the actual words. A score with none of that is a verdict pretending to be a
 * measurement.
 *
 * The wording throughout is "indication", never "hire" or "reject". The system
 * informs a decision it is not entitled to make.
 */
function Assessment({ score, error }: { score: SessionScore | null; error: string | null }) {
  if (error) {
    return (
      <Callout tone="info" title="No cue-coverage assessment for this interview">
        This measure scores answers against the authored question bank, so it only
        applies to interviews drawn from it. The evaluation on the first tab is
        independent of it and is unaffected.
      </Callout>
    );
  }
  if (!score) return <div className="card h-64 animate-pulse bg-gray-50" />;

  if (!score.scored) {
    return (
      <Callout tone="warning" title="Not enough evidence to assess">
        {score.note}
      </Callout>
    );
  }

  const lowConfidence = score.confidence < 0.55;

  return (
    <div className="space-y-4">
      <section className="card overflow-hidden">
        <div className="flex flex-wrap items-center gap-x-8 gap-y-4 border-b border-gray-200 bg-gray-25 px-5 py-4">
          <div>
            <p className="label">Demonstrated level</p>
            <p className="tabular mt-1 text-stat font-semibold leading-none text-gray-900">
              {score.composite?.toFixed(2)}
              <span className="ml-1 text-lg font-normal text-gray-400">/ 4</span>
            </p>
          </div>
          <div>
            <p className="label">Indication</p>
            <p className="mt-1.5">
              <BandPill band={score.band} label={score.band_label} />
            </p>
          </div>
          <div>
            <p className="label">Targets met</p>
            <p className="tabular mt-1 text-xl font-semibold leading-none text-gray-900">
              {Math.round(score.met_ratio * 100)}%
            </p>
          </div>
          <div>
            <p className="label">Confidence</p>
            <p
              className={cn(
                "tabular mt-1 text-xl font-semibold leading-none",
                lowConfidence ? "text-warning-700" : "text-gray-900",
              )}
            >
              {Math.round(score.confidence * 100)}%
            </p>
          </div>
          {score.excluded_items > 0 && (
            <div>
              <p className="label">Excluded</p>
              <p className="tabular mt-1 text-xl font-semibold leading-none text-gray-900">
                {score.excluded_items}
              </p>
            </div>
          )}
        </div>

        <div className="px-5 py-3">
          <p className="text-sm leading-relaxed text-gray-600">
            This informs the decision; it does not make it. Levels come from which authored evidence
            cues each answer covered — never from accent, tone, fluency, or how much was said. Read
            the Evidence tab before relying on any of it.
          </p>
        </div>
      </section>

      {score.note && <Callout tone="warning">{score.note}</Callout>}

      <section className="card overflow-hidden">
        <div className="border-b border-gray-200 px-4 py-3">
          <h2 className="text-md font-semibold text-gray-900">Skill by skill</h2>
          <p className="meta mt-0.5">
            The marker on each bar is the level this role asked for.
          </p>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[620px] text-left text-sm">
            <thead className="table-head">
              <tr>
                <th className="px-4 py-2 font-semibold">Skill</th>
                <th className="px-4 py-2 font-semibold">Priority</th>
                <th className="px-4 py-2 font-semibold">Demonstrated vs required</th>
                <th className="px-4 py-2 font-semibold">Confidence</th>
              </tr>
            </thead>
            <tbody>
              {score.skills.map((s) => (
                <tr key={s.competency_id} className="table-row">
                  <td className="px-4 py-2.5">
                    <span className="block font-medium text-gray-900">{s.name}</span>
                    {s.note && <span className="meta mt-0.5 block">{s.note}</span>}
                  </td>
                  <td className="px-4 py-2.5">
                    <Badge tone={s.priority === "high" ? "brand" : "neutral"}>{s.priority}</Badge>
                  </td>
                  <td className="px-4 py-2.5">
                    <LevelBar level={s.level} target={s.target} />
                  </td>
                  <td className="px-4 py-2.5">
                    {s.level == null ? (
                      <span className="text-xs text-gray-400">—</span>
                    ) : s.flagged ? (
                      <span className="text-xs font-semibold text-warning-700">Low</span>
                    ) : (
                      <span className="tabular text-xs text-gray-500">
                        {Math.round(s.confidence * 100)}%
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

function Evidence({ review }: { review: Review }) {
  return (
    <div className="space-y-4">
      <Callout tone="info">
        Nothing here is a score. The "shown" and "not shown" lists are a model's reading of what each
        answer contained, kept so you can check it against the words yourself — the judgement is
        yours.
      </Callout>

      {review.items.map((item, n) => (
        <section key={item.item_id} className="card overflow-hidden">
          <div className="flex flex-wrap items-center gap-2.5 border-b border-gray-200 bg-gray-25 px-4 py-2.5">
            <span className="tabular grid h-5 w-5 shrink-0 place-items-center rounded-xs bg-gray-200 text-2xs font-bold text-gray-600">
              {n + 1}
            </span>
            <span className="text-sm font-semibold text-gray-700">
              {item.skills.length ? item.skills.join(" · ") : item.competency_label}
            </span>
            <DifficultyPill level={item.difficulty} />
            <span className="meta ml-auto">
              {item.probes_asked.length > 0 &&
                `${item.probes_asked.length} follow-up${item.probes_asked.length === 1 ? "" : "s"}`}
              {item.reasks > 0 && ` · repeated ${item.reasks}×`}
              {item.clarifies > 0 && ` · clarified ${item.clarifies}×`}
              {item.seconds != null && ` · ${mmss(item.seconds)}`}
            </span>
          </div>

          <div className="space-y-3.5 px-4 py-4">
            {item.exchange.map((turn, i) => (
              <div key={i}>
                <p
                  className={cn(
                    "label mb-1",
                    turn.role === "answer" ? "text-gray-400" : "text-brand-600",
                  )}
                >
                  {turn.role === "answer" ? "Candidate" : turn.role === "probe" ? "Follow-up" : "Tara"}
                </p>
                <p
                  className={cn(
                    "text-base leading-relaxed",
                    turn.role === "answer" ? "text-gray-700" : "font-medium text-gray-900",
                  )}
                >
                  {turn.text}
                </p>
              </div>
            ))}
            {!item.answered && (
              <p className="rounded-md bg-gray-50 px-3 py-2 text-sm text-gray-500">
                No answer recorded. Tara moved on rather than holding them on a question they
                couldn't hear or didn't understand — this is not counted against the candidate.
              </p>
            )}
          </div>

          {item.answered && (
            <div className="grid gap-4 border-t border-gray-100 bg-gray-25 px-4 py-3.5 sm:grid-cols-2">
              <Cues title="Shown in the answer" cues={item.evidenced} good />
              <Cues title="Not shown" cues={item.not_evidenced} />
            </div>
          )}
        </section>
      ))}
    </div>
  );
}

function Cues({ title, cues, good }: { title: string; cues: string[]; good?: boolean }) {
  return (
    <div>
      <p className="label mb-1.5">{title}</p>
      {cues.length === 0 ? (
        <p className="text-sm text-gray-400">—</p>
      ) : (
        <ul className="space-y-1">
          {cues.map((c) => (
            <li key={c} className="flex gap-2 text-sm leading-relaxed text-gray-600">
              {good ? (
                <Check className="mt-1 h-3 w-3 shrink-0 text-success-600" strokeWidth={3} />
              ) : (
                <Minus className="mt-1 h-3 w-3 shrink-0 text-gray-300" strokeWidth={3} />
              )}
              {c}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Transcript({ review }: { review: Review }) {
  return (
    <div className="card space-y-4 p-5">
      {review.transcript.map((t, i) => (
        <div key={i}>
          <p
            className={cn("label mb-1", t.speaker === "tara" ? "text-brand-600" : "text-gray-400")}
          >
            {t.speaker === "tara" ? `Tara${t.kind ? ` · ${t.kind}` : ""}` : review.candidate_name}
          </p>
          <p
            className={cn(
              "whitespace-pre-wrap text-base leading-relaxed",
              t.speaker === "tara" ? "text-gray-900" : "text-gray-600",
            )}
          >
            {t.text}
          </p>
        </div>
      ))}
    </div>
  );
}

function TrailView({ trail }: { trail: Trail | null }) {
  if (!trail) return <p className="text-sm text-gray-400">No trail recorded for this session.</p>;

  const LABELS: Record<string, string> = {
    session_started: "Interview started",
    item_selected: "Question selected",
    answer_read: "Answer read",
    probe_generated: "Follow-up written",
    probe_fallback: "Follow-up taken from the authored bank",
    probe_exhausted: "No follow-up left to ask",
    probe_generation_failed: "Follow-up generation failed",
    silence: "Silence",
    repeated: "Question repeated",
    clarified: "Question rephrased",
    item_skipped: "Candidate skipped",
    item_unanswered: "Left unanswered",
    device_help_offered: "Talked through a microphone problem",
    resumed: "Reconnected",
    socket_disconnected: "Connection dropped",
    session_complete: "Interview complete",
    turn_failed: "Turn failed",
  };

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <TrailStat label="Questions asked" value={trail.summary.items_selected} />
        <TrailStat label="Follow-ups written" value={trail.summary.probes_generated} />
        <TrailStat
          label="Blocked by guardrails"
          value={trail.summary.probes_blocked}
          tone={trail.summary.probes_blocked > 0 ? "warn" : undefined}
        />
        <TrailStat label="From authored bank" value={trail.summary.probes_from_bank} />
      </div>

      <div className="card overflow-hidden">
        <div className="flex items-center gap-2 border-b border-gray-200 bg-gray-25 px-4 py-2.5">
          <ScrollText className="h-4 w-4 text-gray-400" />
          <p className="text-sm font-semibold text-gray-900">Every decision, in order</p>
          <p className="meta ml-auto">Append-only — never rewritten</p>
        </div>
        <ol className="divide-y divide-gray-100">
          {trail.events.map((e, i) => {
            const guard = e.guardrail as { ok?: boolean; gate?: string; reason?: string } | undefined;
            return (
              <li key={i} className="flex gap-3 px-4 py-2">
                <span className="tabular w-[54px] shrink-0 text-xs text-gray-400">
                  {new Date(e.at * 1000).toLocaleTimeString([], {
                    hour: "2-digit",
                    minute: "2-digit",
                    second: "2-digit",
                  })}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-gray-800">
                    {LABELS[e.event] ?? e.event}
                    {typeof e.item_id === "string" && (
                      <span className="ml-1.5 font-normal text-gray-400">{e.item_id}</span>
                    )}
                  </p>
                  {typeof e.probe === "string" && (
                    <p className="mt-0.5 text-sm italic leading-relaxed text-gray-600">
                      "{e.probe}"
                    </p>
                  )}
                  {guard && (
                    <p
                      className={cn(
                        "mt-1 inline-flex items-center gap-1.5 rounded px-1.5 py-0.5 text-xs font-medium",
                        guard.ok
                          ? "bg-success-50 text-success-700"
                          : "bg-error-50 text-error-700",
                      )}
                    >
                      {guard.ok ? (
                        <>
                          <ShieldCheck className="h-3 w-3" /> passed all guardrails
                        </>
                      ) : (
                        <>
                          <ShieldX className="h-3 w-3" /> blocked · {guard.gate} · {guard.reason}
                        </>
                      )}
                    </p>
                  )}
                  {e.event === "answer_read" && (
                    <p className="mt-0.5 text-xs text-gray-500">
                      read as <strong>{String(e.intent)}</strong>, {String(e.depth)} ·{" "}
                      {String(e.words)} words
                    </p>
                  )}
                  {typeof e.reason === "string" && e.event === "item_unanswered" && (
                    <p className="mt-0.5 text-xs text-gray-500">reason: {e.reason}</p>
                  )}
                  {typeof e.competency === "string" && (
                    <p className="mt-0.5 text-xs text-gray-500">
                      {e.competency} · {String(e.difficulty)} · position {String(e.position)}
                    </p>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      </div>
    </div>
  );
}

function TrailStat({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone?: "warn";
}) {
  return (
    <div className={cn("card p-3.5", tone === "warn" && "border-warning-200 bg-warning-25")}>
      <p className="tabular text-2xl font-semibold leading-none text-gray-900">{value}</p>
      <p className="label mt-1.5">{label}</p>
    </div>
  );
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="label">{label}</p>
      <p className="mt-1 text-base font-medium text-gray-900">{children}</p>
    </div>
  );
}
