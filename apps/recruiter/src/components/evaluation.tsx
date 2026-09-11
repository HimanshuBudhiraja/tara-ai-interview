import { Check, ChevronRight, Circle, MessageSquareQuote, Minus } from "lucide-react";
import { Badge } from "@tara/ui/primitives";
import type {
  DepthEvaluation,
  DiscussionStatus,
  EvidenceConfidence,
  ResultEvidence,
} from "../lib/adminApi";
import {
  CONFIDENCE,
  DISCUSSION,
  EVIDENCE_TYPE,
  STAGE_LABEL,
  STAGE_SHORT,
  STRENGTH_LABEL,
  criterionLabel,
  depthRelation,
  depthSentence,
  dimensionLabel,
} from "../lib/evaluation";
import { cn } from "../lib/cn";

/**
 * The report's parts.
 *
 * Two rules run through all of them. Nothing here computes anything — every
 * number is rendered exactly as the evaluation engine recorded it. And nothing
 * here says a thing with colour alone: every status carries its own word, so
 * "mentioned" and "not discussed" are distinguishable in greyscale, at 200%
 * zoom, and to a screen reader.
 */

// --------------------------------------------------------------------------- //
//  Criteria
// --------------------------------------------------------------------------- //
/**
 * One criterion as five segments and its number.
 *
 * Segments rather than a bar because the scale is five whole points, and a
 * continuous bar invites reading 3.5 into something that can only be 3 or 4.
 */
export function CriterionScore({
  label,
  value,
  max,
  muted,
}: {
  label: string;
  value: number;
  /** From the payload's `scales.criterion_max` — never a constant in here. */
  max: number;
  muted?: boolean;
}) {
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2">
        <span className="label truncate">{label}</span>
        <span
          className={cn(
            "tabular text-sm font-semibold",
            muted ? "text-gray-400" : "text-gray-900",
          )}
        >
          {value}
          <span className="text-xs font-normal text-gray-400">/{max}</span>
        </span>
      </div>
      <div
        className="mt-1.5 flex gap-0.5"
        role="img"
        aria-label={criterionLabel(label, value, max)}
      >
        {Array.from({ length: max }, (_, i) => (
          <span
            key={i}
            className={cn(
              "h-1.5 flex-1 rounded-full",
              i < value ? (muted ? "bg-gray-300" : "bg-brand-500") : "bg-gray-200",
            )}
          />
        ))}
      </div>
    </div>
  );
}

export function CriterionGrid({
  criteria,
  order,
  max,
  muted,
}: {
  criteria: Record<string, number>;
  /** The canonical order, from the payload. Never sorted or re-derived here. */
  order: string[];
  max: number;
  muted?: boolean;
}) {
  return (
    <div className="grid gap-x-5 gap-y-3 sm:grid-cols-3 lg:grid-cols-5">
      {order.map((label) => (
        <CriterionScore
          key={label}
          label={label}
          value={criteria[label] ?? 0}
          max={max}
          muted={muted}
        />
      ))}
    </div>
  );
}

// --------------------------------------------------------------------------- //
//  Status pills
// --------------------------------------------------------------------------- //
export function DiscussionPill({ status }: { status: DiscussionStatus }) {
  const meta = DISCUSSION[status] ?? DISCUSSION.not_discussed;
  return (
    <Badge tone={meta.tone} dot>
      {meta.label}
    </Badge>
  );
}

export function ConfidencePill({ confidence }: { confidence: EvidenceConfidence }) {
  const meta = CONFIDENCE[confidence] ?? CONFIDENCE.insufficient;
  return (
    <Badge tone={meta.tone} className="whitespace-nowrap">
      {meta.label}
    </Badge>
  );
}

// --------------------------------------------------------------------------- //
//  Depth
// --------------------------------------------------------------------------- //
/**
 * The two depth values, kept visibly apart.
 *
 * They are laid out as two labelled facts with their definitions attached,
 * never as one arrow or one combined figure — collapsing them is the specific
 * misreading this whole section exists to prevent.
 */
export function DepthPanel({
  depth,
  status,
}: {
  depth: DepthEvaluation;
  status?: DiscussionStatus;
}) {
  const relation = depthRelation(depth.depth_reached, depth.depth_demonstrated);
  return (
    <div className="rounded-md border border-gray-200 bg-gray-25 p-3.5">
      <div className="grid gap-3.5 sm:grid-cols-2">
        <div>
          <p className="label">Depth reached</p>
          <p className="mt-1 text-base font-semibold text-gray-900">
            {STAGE_LABEL[depth.depth_reached] ?? depth.depth_reached}
          </p>
          <p className="mt-0.5 text-xs leading-relaxed text-gray-500">
            How far Tara investigated this skill.
          </p>
        </div>
        <div className="sm:border-l sm:border-gray-200 sm:pl-3.5">
          <p className="label">Depth demonstrated</p>
          <p className="mt-1 text-base font-semibold text-gray-900">
            {STAGE_LABEL[depth.depth_demonstrated] ?? depth.depth_demonstrated}
          </p>
          <p className="mt-0.5 text-xs leading-relaxed text-gray-500">
            How far the candidate's own evidence went.
          </p>
        </div>
      </div>

      <p
        className={cn(
          "mt-3 border-t border-gray-200 pt-3 text-xs leading-relaxed",
          relation === "matched" ? "text-gray-500" : "text-gray-600",
        )}
      >
        {depthSentence(depth.depth_reached, depth.depth_demonstrated, status)}
      </p>
    </div>
  );
}

/** Depth as a compact pair, for the collapsed row header. */
export function DepthSummary({ depth }: { depth: DepthEvaluation }) {
  return (
    <span className="meta whitespace-nowrap">
      Reached {STAGE_SHORT[depth.depth_reached] ?? depth.depth_reached} · Demonstrated{" "}
      {STAGE_SHORT[depth.depth_demonstrated] ?? depth.depth_demonstrated}
    </span>
  );
}

// --------------------------------------------------------------------------- //
//  Evidence dimensions
// --------------------------------------------------------------------------- //
/**
 * What kind of evidence exists, and what does not.
 *
 * Descriptors, never a sixth score: a missing dimension is a thing the
 * conversation did not surface, and only the five criteria carry numbers.
 */
export function Dimensions({ depth }: { depth: DepthEvaluation }) {
  const shown = depth.dimensions_demonstrated ?? [];
  const missing = depth.dimensions_missing ?? [];
  if (shown.length === 0 && missing.length === 0) return null;

  return (
    <div className="grid gap-3.5 sm:grid-cols-2">
      <div>
        <p className="label mb-1.5">Demonstrated evidence</p>
        {shown.length === 0 ? (
          <p className="text-sm text-gray-400">None recorded.</p>
        ) : (
          <ul className="space-y-1">
            {shown.map((d) => (
              <li key={d} className="flex items-start gap-2 text-sm text-gray-700">
                <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-success-600" strokeWidth={3} />
                {dimensionLabel(d)}
              </li>
            ))}
          </ul>
        )}
      </div>
      <div>
        <p className="label mb-1.5">Not surfaced by this interview</p>
        {missing.length === 0 ? (
          <p className="text-sm text-gray-400">—</p>
        ) : (
          <ul className="space-y-1">
            {missing.map((d) => (
              <li key={d} className="flex items-start gap-2 text-sm text-gray-500">
                <Circle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-gray-300" strokeWidth={2.5} />
                {dimensionLabel(d)}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- //
//  Evidence
// --------------------------------------------------------------------------- //
/**
 * The validated evidence behind one skill.
 *
 * `<details>` rather than a bespoke disclosure: it is keyboard-operable and
 * announced correctly without any of it being reimplemented here.
 *
 * Every quote is verbatim and was checked against the transcript server-side
 * before it was persisted. Nothing on this screen edits, trims or tidies one —
 * a quotation attributed to a real person has to be what they said.
 */
export function EvidenceBlock({
  items,
  skillName,
}: {
  items: ResultEvidence[];
  skillName: string;
}) {
  if (items.length === 0) {
    return (
      <p className="text-sm text-gray-500">
        No validated evidence was recorded for this skill.
      </p>
    );
  }

  const contradicted = items.filter((i) => i.evidence_type === "contradicted");
  const byQuestion = new Map<string, ResultEvidence[]>();
  for (const item of items) {
    const key = item.question_id;
    byQuestion.set(key, [...(byQuestion.get(key) ?? []), item]);
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
        Evidence
        <span className="font-normal text-gray-400">
          ({items.length} {items.length === 1 ? "item" : "items"})
        </span>
      </summary>

      <div className="mt-3 space-y-3">
        {contradicted.length > 0 && (
          <p className="rounded-md border border-warning-200 bg-warning-25 px-3 py-2 text-xs leading-relaxed text-warning-700">
            Evidence for {skillName} contains conflicting statements. Both are shown below,
            in the candidate's own words. The evaluation has already weighed them — this
            note is so you can read them yourself, not something to resolve here.
          </p>
        )}

        {[...byQuestion.entries()].map(([questionId, group]) => (
          <div key={questionId} className="rounded-md border border-gray-200 bg-surface">
            <div className="border-b border-gray-100 px-3.5 py-2.5">
              <p className="label">Question</p>
              <p className="mt-0.5 text-sm leading-relaxed text-gray-900">
                {group[0].question_text || <span className="text-gray-400">—</span>}
              </p>
            </div>
            <ul className="divide-y divide-gray-100">
              {group.map((item) => (
                <li key={item.turn_id + item.candidate_quote} className="px-3.5 py-3">
                  <div className="mb-2 flex flex-wrap items-center gap-1.5">
                    <Badge tone="neutral">
                      {STAGE_LABEL[item.stage] ?? item.stage}
                    </Badge>
                    <Badge tone="info">{dimensionLabel(item.dimension)}</Badge>
                    <Badge tone={(EVIDENCE_TYPE[item.evidence_type] ?? EVIDENCE_TYPE.unclear).tone}>
                      {(EVIDENCE_TYPE[item.evidence_type] ?? EVIDENCE_TYPE.unclear).label}
                    </Badge>
                    <span className="meta">
                      {STRENGTH_LABEL[item.evidence_strength] ?? item.evidence_strength} · supports{" "}
                      {item.supports_criterion}
                    </span>
                  </div>
                  <blockquote className="flex gap-2 border-l-2 border-gray-200 pl-3">
                    <MessageSquareQuote className="mt-0.5 h-3.5 w-3.5 shrink-0 text-gray-300" />
                    <p className="text-sm leading-relaxed text-gray-700">
                      “{item.candidate_quote}”
                    </p>
                  </blockquote>
                  <p className="meta mt-1.5">
                    Turn {item.turn_id}
                    {item.task_id && <> · task {item.task_id}</>}
                  </p>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </details>
  );
}

/** A bulleted list that renders nothing rather than an empty box. */
export function Bullets({
  items,
  tone,
}: {
  items: string[];
  tone: "positive" | "neutral";
}) {
  if (items.length === 0) {
    return <p className="text-sm text-gray-400">None recorded.</p>;
  }
  return (
    <ul className="space-y-1.5">
      {items.map((text, i) => (
        <li key={i} className="flex gap-2 text-sm leading-relaxed text-gray-700">
          {tone === "positive" ? (
            <Check className="mt-1 h-3 w-3 shrink-0 text-success-600" strokeWidth={3} />
          ) : (
            <Minus className="mt-1 h-3 w-3 shrink-0 text-gray-300" strokeWidth={3} />
          )}
          {text}
        </li>
      ))}
    </ul>
  );
}
