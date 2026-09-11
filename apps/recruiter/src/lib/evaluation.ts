import type {
  DepthStage,
  DiscussionStatus,
  EvidenceConfidence,
  EvidenceType,
} from "./adminApi";

/**
 * The report's vocabulary: labels, tone, and the sentences that keep two
 * different ideas from being read as one.
 *
 * Everything here is presentation. There is no arithmetic in this file and
 * there must never be: the total, the percentage, the rating and the
 * recommendation are computed by the evaluation engine and rendered verbatim.
 * A frontend that recomputes any of them is a second scoring system that will
 * quietly disagree with the first.
 */

/** The canonical five. Order is the contract's, not a preference. */
export const CRITERIA = [
  "Accuracy",
  "Depth",
  "Clarity",
  "Problem-Solving",
  "Communication",
] as const;
export type Criterion = (typeof CRITERIA)[number];

/**
 * A last-resort default for the criterion scale.
 *
 * The scale now arrives in the payload (`scales.criterion_max`) and that is
 * what every component uses. This exists only so a label can be produced when
 * a caller has no payload to hand, and it is never used to derive a total, a
 * maximum or a percentage — all of those are computed by the backend.
 */
export const CRITERION_MAX = 5;

// --------------------------------------------------------------------------- //
//  Investigation stage — direct / probed / deep_probed
// --------------------------------------------------------------------------- //
export const STAGES: DepthStage[] = ["direct", "probed", "deep_probed"];

export const STAGE_LABEL: Record<DepthStage, string> = {
  direct: "Direct answer",
  probed: "Probed once",
  deep_probed: "Probed twice",
};

export const STAGE_SHORT: Record<DepthStage, string> = {
  direct: "Direct",
  probed: "Probed",
  deep_probed: "Deep probed",
};

export function stageIndex(stage: string): number {
  const i = STAGES.indexOf(stage as DepthStage);
  return i < 0 ? 0 : i;
}

export type DepthRelation = "matched" | "exceeded" | "short_of";

/**
 * How the two depth values sit against each other.
 *
 * `exceeded` is not "better than expected" and `short_of` is not "failed" —
 * they describe two different measurements landing in different places, which
 * is the single thing this report exists to make legible.
 */
export function depthRelation(reached: string, demonstrated: string): DepthRelation {
  const r = stageIndex(reached);
  const d = stageIndex(demonstrated);
  if (d > r) return "exceeded";
  if (d < r) return "short_of";
  return "matched";
}

/**
 * The sentence under a skill's two depth values.
 *
 * Deliberately never says a deeper stage is a better result. Reaching the
 * deepest rung usually means earlier answers left something unresolved, and a
 * candidate who settled a skill in one answer has not done worse than one who
 * needed two follow-ups.
 */
export function depthSentence(
  reached: string,
  demonstrated: string,
  status?: DiscussionStatus,
): string {
  // A skill that was only mentioned has too little behind it for either depth
  // figure to carry a conclusion. Saying "the answer already carried the deeper
  // material" about six words would be the report overclaiming on the
  // candidate's behalf, which is as wrong as underclaiming.
  if (status === "mentioned") {
    return (
      "Both figures rest on very little: the skill was mentioned rather than " +
      "substantively evaluated, so neither says much about what the candidate can do."
    );
  }
  switch (depthRelation(reached, demonstrated)) {
    case "exceeded":
      return (
        "The evidence went further than the interview needed to probe — the answer " +
        "already carried the deeper material, so Tara had no reason to keep asking."
      );
    case "short_of":
      return (
        "Tara investigated further than the evidence reached. The follow-ups did not " +
        "establish deeper capability than the first answer did."
      );
    default:
      return "The evidence reached the same depth the interview investigated.";
  }
}

/** The configured scope of the interview — a different idea from the ladder. */
export function interviewDepthLabel(depth: string): string {
  const known: Record<string, string> = {
    short: "Short",
    medium: "Medium",
    deep: "Deep",
  };
  return known[(depth || "").toLowerCase()] ?? "";
}

export const INTERVIEW_DEPTH_HINT =
  "How much ground this interview was configured to cover. Separate from how far " +
  "any one question was followed up.";

// --------------------------------------------------------------------------- //
//  Discussion status
// --------------------------------------------------------------------------- //
type Tone = "neutral" | "brand" | "success" | "warning" | "error" | "info";

export const DISCUSSION: Record<
  DiscussionStatus,
  { label: string; tone: Tone; meaning: string }
> = {
  discussed: {
    label: "Discussed",
    tone: "brand",
    meaning: "The skill was put to the candidate and substantively answered.",
  },
  mentioned: {
    label: "Mentioned only",
    tone: "warning",
    meaning:
      "The skill came up without being substantively evaluated. The scores below are " +
      "capped by that, and say more about the interview than about the candidate.",
  },
  not_discussed: {
    // Not an "error" tone anywhere in the report: a skill nobody asked about is
    // a gap in the conversation, not a finding about the person.
    label: "Not discussed",
    tone: "neutral",
    meaning:
      "This skill was never put to the candidate, so the interview established nothing " +
      "about it either way. This is a coverage gap, not a weakness.",
  },
};

// --------------------------------------------------------------------------- //
//  Evidence confidence — how much weight a row can carry, NOT capability
// --------------------------------------------------------------------------- //
export const CONFIDENCE: Record<
  EvidenceConfidence,
  { label: string; tone: Tone; meaning: string }
> = {
  high: {
    label: "High confidence",
    tone: "success",
    meaning: "Several pieces of strong, directly relevant evidence.",
  },
  medium: {
    label: "Medium confidence",
    tone: "info",
    meaning: "Enough evidence to judge, with less of it than the strongest rows.",
  },
  low: {
    label: "Low confidence",
    tone: "warning",
    meaning:
      "Thin, unclear or contradictory evidence. Low confidence is a statement about " +
      "what the interview established — not about the candidate's ability.",
  },
  insufficient: {
    label: "Insufficient evidence",
    tone: "neutral",
    meaning:
      "The interview did not establish enough to evaluate this skill. Read this as " +
      "“we do not know”, never as “they cannot do it”.",
  },
};

// --------------------------------------------------------------------------- //
//  Evidence dimensions — descriptors, never a sixth score
// --------------------------------------------------------------------------- //
export const DIMENSION_LABEL: Record<string, string> = {
  conceptual_understanding: "Conceptual understanding",
  practical_application: "Practical application",
  reasoning: "Reasoning",
  trade_offs: "Trade-offs",
  edge_cases: "Edge cases",
  production_judgment: "Production judgment",
};

/** Unknown dimensions render as themselves rather than being dropped. */
export function dimensionLabel(dimension: string): string {
  return (
    DIMENSION_LABEL[dimension] ??
    dimension.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase())
  );
}

export const EVIDENCE_TYPE: Record<EvidenceType, { label: string; tone: Tone }> = {
  supported: { label: "Supported", tone: "success" },
  partial: { label: "Partial", tone: "warning" },
  contradicted: { label: "Contradicted", tone: "error" },
  unclear: { label: "Unclear", tone: "neutral" },
  missing: { label: "Missing", tone: "neutral" },
};

export const STRENGTH_LABEL: Record<string, string> = {
  strong: "Strong",
  moderate: "Moderate",
  weak: "Weak",
};

// --------------------------------------------------------------------------- //
//  Evaluation status
// --------------------------------------------------------------------------- //
export const STATUS: Record<
  string,
  { label: string; tone: Tone; meaning: string }
> = {
  pending: {
    label: "Evaluation queued",
    tone: "info",
    meaning:
      "The interview is complete. The evaluation has been queued and has not run yet.",
  },
  running: {
    label: "Evaluation running",
    tone: "info",
    meaning: "The evaluation is being generated. Nothing is scored until it finishes.",
  },
  completed: { label: "Evaluated", tone: "success", meaning: "" },
  failed: {
    label: "Evaluation failed",
    tone: "error",
    meaning:
      "The evaluation could not be produced. This is a failure of the evaluation run, " +
      "not a result about the candidate.",
  },
};

export const FAILURE_MEANING: Record<string, string> = {
  model: "The model could not extract evidence from the transcript. Running it again may work.",
  validation:
    "The result did not pass the checks that let it be recorded, so it was refused rather " +
    "than saved. Running it again unchanged will refuse it again.",
};

/** A screen-reader label for a criterion, so a bar is never the only signal. */
export function criterionLabel(
  label: string,
  value: number,
  max: number = CRITERION_MAX,
): string {
  return `${label}: ${value} out of ${max}`;
}
