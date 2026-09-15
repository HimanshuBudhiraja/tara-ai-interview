import { ApiError } from "./api";
import { announceSessionLost } from "./auth";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      ...init,
      // The session is an HttpOnly cookie, so it has to be sent — explicitly,
      // rather than relying on the same-origin default holding for every build.
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError("Can't reach the server. Is the backend running on :8000?", 0);
  }
  if (res.status === 401) {
    // The server is the only thing that knows a session has ended — it expires,
    // an administrator revokes it, the account is disabled. Whatever the reason,
    // the console finds out here and puts the sign-in screen back rather than
    // rendering empty tables that look like a candidate has no data.
    announceSessionLost();
  }
  if (!res.ok) {
    let message = `Request failed (${res.status}).`;
    let structured: unknown;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") {
        message = body.detail;
      } else if (body?.detail && typeof body.detail === "object") {
        structured = body.detail;
        const named = (body.detail as { message?: string }).message;
        if (typeof named === "string") message = named;
      }
    } catch {
      /* keep the fallback */
    }
    throw new ApiError(message, res.status, structured);
  }
  return res.json() as Promise<T>;
}

// --------------------------------------------------------------------------- //
//  Types
// --------------------------------------------------------------------------- //
export interface PoolItem {
  id: string;
  type: string;
  competency: string;
  competency_label: string;
  difficulty: "easy" | "medium" | "hard";
  prompt: string;
  probe_eligible: boolean;
  probe_bank: string[];
  clarify: string;
  looking_for: string[];
  time_estimate_sec: number;
}

export interface PoolPayload {
  role: string;
  role_title: string;
  competencies: { id: string; label: string; weight: number; min_items: number; items: number }[];
  items: PoolItem[];
}

export interface Skill {
  name: string;
  competency_id: string;
  priority: "high" | "medium" | "low";
  proficiency_target: number;
  evaluated: boolean;
  tasks: string[];
  /** Which authored question bank answers this skill. "" = no questions exist. */
  pool_competency: string;
  /**
   * The skill master domain this skill is filed under. The NAME stays free
   * text in the job description's own words; the DOMAIN is catalogued, so the
   * same competency can be counted across interviews instead of each one
   * being an island. "" = nothing matched well enough to propose one, and the
   * recruiter is asked to choose.
   */
  domain: string;
  /** Resolved label for `domain`, so a row can render before the catalogue loads. */
  domain_label: string;
}

export interface SkillDomain {
  id: string;
  label: string;
  category: "technical" | "functional" | "behavioural";
  description: string;
}

export interface SkillMaster {
  version: string;
  categories: string[];
  domains: SkillDomain[];
}

export interface Task {
  description: string;
  required_skills: string[];
  outcome: string;
}

export interface CompanyInfo {
  name: string;
  about: string;
  role_context: string;
}

export interface PreviewSkill {
  competency_id: string;
  name: string;
  priority: string;
  proficiency_target: number;
  pool_competency: string;
  pool_competency_label: string;
  questions_planned: number;
}

export interface PreviewItem {
  id: string;
  position: number;
  competency: string;
  competency_label: string;
  /** Which evaluated skills this question is standing in for. */
  skills: string[];
  difficulty: "easy" | "medium" | "hard";
  prompt: string;
  probe_eligible: boolean;
}

export interface Interview {
  /** Added by the AI Interviews overview — see services/api/recruiter.py. */
  language_label: string;
  interview_type: InterviewType;
  difficulty: Difficulty;
  recommended_duration_min: number;
  skill_count: number;
  high_priority_count: number;
  task_count: number;
  /** Always 0 until the Question Generator phase. */
  question_count: number;
  designed: boolean;

  id: string;
  title: string;
  role: string;
  status: "draft" | "published";

  role_title: string;
  company: CompanyInfo;
  jd_text: string;
  experience_from: number;
  experience_to: number;

  outcomes: string[];
  tasks: Task[];
  skills: Skill[];
  extracted: boolean;
  extracted_at: number | null;

  skills_evaluated: number;
  question_budget: number;
  max_probes_per_item: number;
  allow_generated_probes: boolean;
  language: string;

  created_at: number;
  updated_at: number;
  pool_role_title: string;
  proficiency_labels: string[];
  covered_count: number;
  uncovered_count: number;
  candidates: { total: number; pending: number; in_progress: number; complete: number };
  preview?: {
    items: PreviewItem[];
    estimated_minutes: number;
    skills: PreviewSkill[];
    warnings: string[];
  };
}

export interface ExtractInput {
  jd_text: string;
  role_title: string;
  company_name: string;
  company_about: string;
  role_context: string;
  experience_from: number;
  experience_to: number;
  skills_evaluated: number;
}

export interface Candidate {
  token: string;
  candidate_name: string;
  candidate_id: string;
  role: string;
  interview_id: string;
  created_at: number;
  expires_at: number | null;
  session_id: string | null;
  status: "pending" | "in_progress" | "complete" | "expired";
  link: string;
  answered: number;
  asked: number;
  last_activity: number | null;
  duration_sec: number | null;
}

export interface ReviewItem {
  item_id: string;
  competency: string;
  competency_label: string;
  skills: string[];
  difficulty: string;
  prompt: string;
  exchange: { role: "question" | "answer" | "probe"; text: string }[];
  probes_asked: string[];
  reasks: number;
  clarifies: number;
  evidenced: string[];
  not_evidenced: string[];
  looking_for: string[];
  answered: boolean;
  closed: boolean;
  seconds: number | null;
}

export interface SessionReview {
  session_id: string;
  candidate_name: string;
  interview_id: string;
  interview_title: string | null;
  role_title: string;
  phase: string;
  channel: string;
  accommodations: Record<string, unknown>;
  consent_recording: boolean;
  started_at: number;
  completed_at: number | null;
  duration_sec: number;
  coverage: { id: string; label: string; asked: number; target: number }[];
  items: ReviewItem[];
  transcript: { speaker: string; text: string; at: number; kind: string | null }[];
}

export interface TrailEvent {
  at: number;
  event: string;
  [key: string]: unknown;
}

export interface Trail {
  session_id: string;
  events: TrailEvent[];
  summary: Record<string, number>;
}

export interface SkillScore {
  competency_id: string;
  name: string;
  pool_competency: string;
  priority: string;
  target: number;
  target_label: string;
  level: number | null;
  level_label: string;
  met: boolean;
  confidence: number;
  flagged: boolean;
  items: string[];
  note: string;
}

export interface ItemScore {
  item_id: string;
  competency: string;
  prompt: string;
  answered: boolean;
  level: number | null;
  coverage: number;
  evidenced: string[];
  not_evidenced: string[];
  probes: number;
  words: number;
  confidence: number;
  excluded_reason: string;
}

export interface SessionScore {
  session_id: string;
  candidate_name: string;
  scored: boolean;
  composite: number | null;
  percent: number | null;
  band: string;
  band_label: string;
  met_ratio: number;
  confidence: number;
  flagged_skills: string[];
  skills: SkillScore[];
  items: ItemScore[];
  excluded_items: number;
  note: string;
  level_labels: string[];
}

export interface Results {
  interview_id: string;
  title: string;
  funnel: { invited: number; started: number; completed: number };
  completion_rate: number;
  scored: number;
  bands: Record<string, number>;
  band_order: string[];
  band_labels: Record<string, string>;
  average_composite: number | null;
  flagged: number;
  median_minutes: number | null;
  skills: {
    competency_id: string;
    name: string;
    priority: string;
    target: number;
    candidates: number;
    average: number;
    met: number;
    met_rate: number;
  }[];
  candidates: {
    token: string;
    session_id: string;
    name: string;
    composite: number | null;
    percent: number | null;
    band: string;
    band_label: string;
    confidence: number;
    met_ratio: number;
    flagged: boolean;
  }[];
}

export interface Comparison {
  cross_interview: boolean;
  note: string;
  candidates: {
    session_id: string;
    name: string;
    composite: number | null;
    percent: number | null;
    band: string;
    band_label: string;
    confidence: number;
    met_ratio: number;
  }[];
  skills: {
    competency_id: string;
    name: string;
    priority: string;
    target: number;
    levels: { session_id: string; level: number | null; met: boolean; flagged: boolean; assessed: boolean }[];
  }[];
}

export interface Fairness {
  sessions_audited: number;
  exclusions: { factor: string; mechanism: string }[];
  generated_probes: number;
  blocked_probes: number;
  block_rate: number;
  fallback_probes: number;
  blocked: { candidate: string; session_id: string; probe: string; gate: string; reason: string }[];
  unanswered_items: number;
  /** How often Tara talked a candidate through a microphone problem. */
  device_help_offered: number;
  questions_repeated: number;
  accommodations: Record<string, number>;
  low_confidence_scores: number;
  confidence_threshold: number;
}

export interface TestRun {
  persona: string;
  turns: number;
  questions_asked: number;
  follow_ups: number;
  transcript: { speaker: string; kind: string | null; text: string }[];
  score: SessionScore;
}

export interface Overview {
  interviews: number;
  published: number;
  candidates: number;
  in_progress: number;
  complete: number;
  not_started: number;
  median_minutes: number | null;
  llm: boolean;
}

// --------------------------------------------------------------------------- //
//  Calls
// --------------------------------------------------------------------------- //

/**
 * Like `request`, but unpacks the two structured error shapes the design API
 * returns: field-level validation problems, and a generation failure that kept
 * the recruiter's job details. Everything else falls through as an ApiError.
 */
async function designRequest<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError("Can't reach the server. Is the backend running on :8000?", 0);
  }
  if (res.ok) return res.json() as Promise<T>;

  let detail: unknown = null;
  try {
    detail = (await res.json())?.detail;
  } catch {
    /* fall through to the generic message below */
  }

  if (res.status === 422 && detail && typeof detail === "object" && "errors" in detail) {
    throw new FormError((detail as { errors: FieldErrors }).errors);
  }
  if (detail && typeof detail === "object" && "check" in detail) {
    const d = detail as unknown as {
      message: string;
      check: { problems: { area: string; message: string }[] };
    };
    throw new FormError(
      Object.fromEntries(d.check.problems.map((p, i) => [`${p.area}.${i}`, p.message])),
    );
  }
  if (detail && typeof detail === "object" && "problems" in detail) {
    const d = detail as unknown as { message: string; problems: string[] };
    throw new FormError({ pool: [d.message, ...d.problems].join(" ") });
  }
  if (detail && typeof detail === "object" && "retryable" in detail) {
    const d = detail as unknown as { message: string; interview_id?: string };
    throw new GenerationError(d.message, d.interview_id);
  }
  throw new ApiError(
    typeof detail === "string" ? detail : `Request failed (${res.status}).`,
    res.status,
  );
}

// --------------------------------------------------------------------------- //
//  Interview design — the recruiter's creation flow
// --------------------------------------------------------------------------- //
export type Priority = "high" | "medium" | "low";
export type InterviewType = "short" | "medium" | "deep";
export type Difficulty = "easy" | "medium" | "hard";

export interface DesignSkill {
  id: string;
  name: string;
  priority: Priority;
  description: string;
  assessment_scope: string;
  /** Skill master domain id. "" when nothing matched and a human must choose. */
  domain: string;
  domain_label: string;
  /**
   * Whether this skill is actually interviewed.
   *
   * The designer infers every skill the job description implies; only some of
   * them are assessed, because an interview that spends twenty minutes on
   * eighteen skills has measured none of them. An unassessed skill is still
   * part of the record — it is what the JD asked for — it just has no
   * questions written for it and produces no evidence.
   */
  evaluated: boolean;
  /** 0 novice → 4 expert. The level the role requires, not the one observed. */
  proficiency_target: number;
}

export interface DesignTask {
  id: string;
  name: string;
  description: string;
  priority: Priority;
  skills_assessed: { id: string; name: string }[];
}

export interface Draft {
  id: string;
  title: string;
  role_title: string;
  status: string;
  language: string;
  language_label: string;
  experience_from: number;
  experience_to: number;
  /** The hiring stage chosen in step 1. Sets the starting type and difficulty. */
  funnel_stage: string;
  funnel_stage_label: string;
  job: { id: string; description: string; additional_information: string } | null;
  assessment: {
    interview_type: InterviewType;
    difficulty: Difficulty;
    recommended_duration_min: number;
    duration_band: { min: number; max: number };
    /** How fast Tara speaks in this interview. */
    speech_rate: number;
    speech_rate_range: { min: number; max: number };
  };
  rationale: string;
  design_failed: boolean;
  designed: boolean;
  skills: DesignSkill[];
  high_priority_skill_ids: string[];
  /** The subset questions are written for. See `DesignSkill.evaluated`. */
  evaluated_skill_ids: string[];
  tasks: DesignTask[];
  /** Empty here — the questions themselves live on the pool screen. */
  questions: never[];
  questions_generated: boolean;
  question_count: number;
  /** skill id → how many questions were written for it. */
  questions_by_skill: Record<string, number>;
  published_version: number;
  created_at: number;
  updated_at: number;
}

export interface GenerateInput {
  title: string;
  experience_from: number | string;
  experience_to: number | string;
  language: string;
  /** Which hiring stage. Sets the starting interview type and difficulty. */
  funnel_stage: string;
  job_description: string;
  additional_information: string;
  /**
   * A throwaway id the client mints so it can ask what this generation is
   * doing. Not a credential — the progress endpoint authorises on the
   * caller's session, not on this string. Optional: omit it and the server
   * skips reporting entirely.
   */
  progress_token?: string;
}

/**
 * What a running generation is doing, as reported by the server.
 *
 * Every field is something that has already happened — a stage entered, a slot
 * finished. `stage: "unknown"` means this worker has no record of the token,
 * which is "no news" rather than failure: the generation is driven by the POST
 * and does not care whether anyone is watching.
 */
export interface GenerationProgress {
  stage: "unknown" | "preparing" | "designing" | "writing_questions" | "complete" | "failed";
  detail: string;
  /** Questions written / questions planned. Both 0 before the question stage. */
  done: number;
  total: number;
  interview_id: string;
  elapsed_sec: number;
}

export interface FunnelStage {
  id: string;
  label: string;
  interview_type: string;
  difficulty: string;
}

/** Field-level problems from the server, keyed by field name. */
export interface FieldErrors {
  [field: string]: string;
}

/**
 * Raised when the server rejects a form. Carries the per-field messages so the
 * form can put each one next to the input it belongs to, rather than showing a
 * single banner the recruiter has to map back to a field themselves.
 */
export class FormError extends Error {
  constructor(readonly errors: FieldErrors) {
    super(Object.values(errors)[0] ?? "Please check the form.");
  }
}

/** Raised when generation fails but the recruiter's job details were kept. */
export class GenerationError extends Error {
  constructor(
    message: string,
    readonly interviewId?: string,
    readonly retryable = true,
  ) {
    super(message);
  }
}

export interface DraftPatch {
  title?: string;
  /**
   * The only shape field the review screen sets.
   *
   * `difficulty` and `recommended_duration_min` are deliberately absent: the
   * difficulty follows the hiring stage, and the duration follows the type.
   * The server does not accept either, so offering them here would be a
   * control that appears to work and silently does nothing.
   */
  interview_type?: InterviewType;
  /** Speaking pace, 0.75–1.1. Bounded server-side. */
  speech_rate?: number;
  skills?: {
    id: string;
    name?: string;
    priority?: Priority;
    description?: string;
    assessment_scope?: string;
    domain?: string;
    evaluated?: boolean;
    proficiency_target?: number;
  }[];
  tasks?: {
    id: string;
    name?: string;
    description?: string;
    priority?: Priority;
    skills_assessed?: string[];
  }[];
  remove_skills?: string[];
  remove_tasks?: string[];
}


// --------------------------------------------------------------------------- //
//  Question pool
// --------------------------------------------------------------------------- //
export type QuestionType = "behavioral" | "situational" | "technical" | "task_based";

export interface Criterion {
  criterion_id: string;
  label: string;
  description: string;
  importance: Priority;
}

export interface PoolQuestion {
  question_id: string;
  prompt: string;
  question_type: QuestionType;
  difficulty: Difficulty;
  primary_skill: { id: string; name: string };
  secondary_skills: { id: string; name: string }[];
  task: { id: string; name: string } | null;
  expected_signal: string;
  looking_for: string[];
  evaluation_criteria: Criterion[];
  probe_eligible: boolean;
  probe_bank: string[];
  clarify: string;
  estimated_base_answer_sec: number;
  source: "generated" | "manual" | "authored";
  slot_id: string;
}

export interface PoolGroup {
  skill_id: string;
  skill_name: string;
  priority: Priority;
  assessment_scope: string;
  questions: PoolQuestion[];
}

export interface CoverageProblem {
  code: string;
  message: string;
  fatal: boolean;
}

export interface PoolSummary {
  pool_size: number;
  live_item_budget: number;
  target_duration_min: number;
  skills_covered: number;
  skills_total: number;
  tasks_covered: number;
  tasks_total: number;
  difficulty_distribution: Record<string, number>;
  type_distribution: Record<string, number>;
  per_skill: {
    skill_id: string;
    skill_name: string;
    priority: Priority;
    have: number;
    target: number;
    min_items: number;
  }[];
}

export interface RunningOrderEntry {
  position: number;
  question_id: string;
  question_text: string;
  difficulty: Difficulty;
  skill_id: string;
  skill_name: string;
  question_type: QuestionType;
  probe_eligible: boolean;
}

export interface QuestionPool {
  interview_id: string;
  role_title: string;
  generated: boolean;
  summary: PoolSummary;
  coverage: { ok: boolean; problems: CoverageProblem[] };
  blueprint: {
    slots: { id: string; skill_name: string; status: string; error: string; count: number }[];
    pool_size: number;
    live_item_budget: number;
    target_duration_min: number;
  } | null;
  groups: PoolGroup[];
  running_order: RunningOrderEntry[];
  questions_generated_at: number | null;
  report?: {
    generated: number;
    failed_slots: { slot_id: string; skill: string; error: string }[];
    rejected: { slot_id: string; question: string; problems: string[] }[];
  };
}

export interface QuestionPatchInput {
  question_text?: string;
  primary_skill_id?: string;
  secondary_skill_ids?: string[];
  task_id?: string;
  question_type?: QuestionType;
  difficulty?: Difficulty;
  looking_for?: string[];
  evaluation_criteria?: { id?: string; label: string; description: string; importance: Priority }[];
  probe_eligible?: boolean;
  probe_bank?: string[];
  clarify?: string;
}

export interface NewQuestionInput extends QuestionPatchInput {
  question_text: string;
  primary_skill_id: string;
}


// --------------------------------------------------------------------------- //
//  Publication and invitations
// --------------------------------------------------------------------------- //
export interface PublishProblem {
  area: "interview" | "skills" | "tasks" | "questions" | "coverage" | "runtime";
  message: string;
}

export interface PublishSummary {
  title: string;
  language: string;
  language_label: string;
  interview_type: InterviewType;
  difficulty: Difficulty;
  duration_min: number;
  skills: number;
  high_priority_skills: number;
  tasks: number;
  questions: number;
  questions_asked_per_candidate: number;
  difficulty_distribution: Record<string, number>;
}

export interface PublishCheck {
  interview_id: string;
  ready: boolean;
  check: { ok: boolean; problems: PublishProblem[] };
  summary: PublishSummary;
  published_version: number;
  has_unpublished_changes: boolean;
}

export interface PublishResult {
  interview_id: string;
  version: number;
  checksum: string;
  published_at: number;
  /** False when an identical version already existed — a retry, not a second publish. */
  created: boolean;
  summary: PublishSummary;
}

export type InvitationStatus =
  | "created" | "active" | "opened" | "in_progress"
  | "complete" | "expired" | "revoked";

export interface Invitation {
  token: string;
  link: string;
  candidate_name: string;
  recipient: string;
  channel: "link" | "email";
  open_link: boolean;
  status: InvitationStatus;
  interview_version: number;
  created_at: number;
  expires_at: number | null;
  opened_at: number | null;
  completed_at: number | null;
  note: string;
}

export interface InvitationList {
  interview_id: string;
  published_version: number;
  invitations: Invitation[];
  open_link: Invitation | null;
  /** False in this build. Nothing here sends an email. */
  email_delivery_configured: boolean;
  max_emails_per_batch: number;
}

export interface VersionRow {
  version: number;
  status: string;
  checksum: string;
  published_at: number | null;
  published_by: string;
  notes: string;
  questions: number;
  skills: number;
  invitations: number;
  completed: number;
}


// --------------------------------------------------------------------------- //
//  Evaluation — the persisted `deep_evidence_v1` contract
//
//  Mirrored from the API exactly. Nothing in the console derives a score, a
//  percentage, a rating or a recommendation: every one of those is computed by
//  the evaluation engine and read from here, so the number on this screen and
//  the number in the audit trail cannot disagree.
// --------------------------------------------------------------------------- //
export type EvaluationStatus = "pending" | "running" | "completed" | "failed";
export type DepthStage = "direct" | "probed" | "deep_probed";
export type DiscussionStatus = "discussed" | "mentioned" | "not_discussed";
export type EvidenceConfidence = "high" | "medium" | "low" | "insufficient";
export type EvidenceType = "supported" | "partial" | "contradicted" | "missing" | "unclear";

export interface DepthEvaluation {
  /** How far the INTERVIEW investigated. A fact about the conversation. */
  depth_reached: DepthStage;
  /** How far the CANDIDATE's evidence went. A judgement about the person. */
  depth_demonstrated: DepthStage;
  dimensions_demonstrated: string[];
  dimensions_missing: string[];
  evidence_confidence: EvidenceConfidence;
}

export interface SkillAssessment {
  skill_name: string;
  discussion_status: DiscussionStatus;
  score: number;
  remarks: string;
  Accuracy: number;
  Depth: number;
  Clarity: number;
  "Problem-Solving": number;
  Communication: number;
  depth_evaluation: DepthEvaluation;
}

export interface EvaluationInterview {
  interview_id: string;
  version: number;
  title: string;
  role_title: string;
  /** The configured SCOPE — short | medium | deep. Not the probe ladder. */
  interview_depth: string;
  difficulty: string;
  recommended_duration_min: number;
  experience_from: number;
  experience_to: number;
}

export interface EvaluationSession {
  candidate_name: string;
  phase: string;
  channel: string;
  started_at: number | null;
  completed_at: number | null;
  questions_asked: number;
}

export interface Evaluation {
  evaluation_id: string;
  session_id: string;
  interview_id: string;
  interview_version: number;
  evaluation_engine_version: string;
  status: EvaluationStatus;
  attempt: number;
  superseded: boolean;
  snapshot_checksum: string;
  created_at: number;
  updated_at: number;
  completed_at: number | null;
  interview: EvaluationInterview;
  session: EvaluationSession;

  /** Failed only. `model` is worth retrying; `validation` will refuse again. */
  error_kind?: "model" | "validation";
  error?: string;
  /** Failed only. Which stage stopped: extraction, assessment, or a gate. */
  failed_stage?: string;

  /** Completed only. Absent at every other status — never a zeroed scaffold. */
  candidate_details?: {
    name: string;
    job_role: string;
    experience_level: string;
    total_score: number;
    overall_rating: string;
  };
  skill_assessment?: SkillAssessment[];
  strengths_and_improvement_areas?: {
    strengths: string[];
    areas_for_improvement: string[];
  };
  recommendation?: string;
  recommendation_explaination?: string;
  maximum_possible_score?: number;
  percentage?: number;
  evidence?: {
    validated_items: number;
    quarantined_items: number;
    constraints_applied: number;
  };
}

export interface EvidenceItem {
  session_id: string;
  skill_id: string;
  skill_name: string;
  task_id: string;
  question_id: string;
  question_text: string;
  turn_id: string;
  depth_stage: DepthStage;
  depth_dimension: string;
  evidence_type: EvidenceType;
  evidence_strength: "strong" | "moderate" | "weak";
  supports_criterion: string;
  /** Verbatim, validated against the transcript server-side. Never paraphrased. */
  candidate_quote: string;
}

export interface EvidencePayload {
  evaluation_id: string;
  session_id: string;
  interview_version: number;
  evaluation_engine_version: string;
  status: EvaluationStatus;
  evidence: EvidenceItem[];
  /** Refused items: the reason, never the text it was refused for. */
  quarantined: { reason: string; skill_id: string }[];
}


// --------------------------------------------------------------------------- //
//  The assessment result — one payload, the whole report
//
//  Mirrored from `services/evaluation/result.py`. Nothing here is derived in the
//  browser: the total, the maximum, every percentage, the rating, the
//  recommendation and each skill's ceiling all arrive computed. A report that
//  works out its own denominator is a second scoring system, and the two will
//  eventually disagree in front of a candidate.
// --------------------------------------------------------------------------- //
export interface ResultCoverage {
  skills_total: number;
  skills_discussed: number;
  skills_mentioned: number;
  skills_not_discussed: number;
  /** `discussed / total`. Completeness of the interview, never part of the score. */
  coverage_percentage: number;
}

export interface ResultSkill {
  skill_id: string;
  skill_name: string;
  priority: string;
  expected_proficiency: number | null;
  assessment_scope: string;
  discussion_status: DiscussionStatus;
  score: number;
  max_score: number;
  percentage: number;
  /** The most any one criterion could reach at this status: 5, 1, or 0. */
  criterion_ceiling: number;
  /** False for mentioned and not-discussed skills — they are coverage, not score. */
  counts_toward_overall_score: boolean;
  criteria: Record<string, number>;
  remarks: string;
  depth: DepthEvaluation;
  task_ids: string[];
  question_ids: string[];
  evidence_count: number;
}

export interface ResultTurn {
  turn_id: string;
  /** The rung of the probe ladder, never the interview's configured depth. */
  stage: DepthStage;
  prompt: string;
  answer: string;
  excluded: boolean;
  excluded_reason: string;
}

export interface ResultQuestion {
  question_id: string;
  question_text: string;
  skill_id: string;
  skill_name: string;
  task_ids: string[];
  task_id: string;
  difficulty: string;
  question_type: string;
  answered: boolean;
  turns: ResultTurn[];
  probe_count: number;
  depth_reached: DepthStage;
  depth_demonstrated: DepthStage;
  evidence_count: number;
}

export interface ResultEvidence {
  skill_id: string;
  skill_name: string;
  question_id: string;
  question_text: string;
  task_id: string;
  turn_id: string;
  stage: DepthStage;
  dimension: string;
  evidence_type: EvidenceType;
  evidence_strength: "strong" | "moderate" | "weak";
  supports_criterion: string;
  /** Verbatim, validated against the transcript server-side. */
  candidate_quote: string;
}

export interface AssessmentResult {
  result_contract_version: string;
  evaluation: {
    evaluation_id: string;
    engine_version: string;
    status: EvaluationStatus;
    attempt: number;
    superseded: boolean;
    snapshot_checksum: string;
    created_at: number;
    completed_at: number | null;
  };
  interview: {
    interview_id: string;
    version: number;
    title: string;
    role_title: string;
    /** short | medium | deep — the configured scope, not the probe ladder. */
    interview_depth: string;
    difficulty: string;
    recommended_duration_min: number;
    experience_from: number;
    experience_to: number;
    experience_level: string;
  };
  session: {
    session_id: string;
    candidate_name: string;
    phase: string;
    channel: string;
    started_at: number | null;
    completed_at: number | null;
    duration_sec: number | null;
    questions_asked: number;
  };
  overall: {
    total_score: number;
    max_score: number;
    percentage: number;
    overall_rating: string;
    recommendation: string;
    /** Says what the denominator was, so nothing has to infer it. */
    scored_over: string;
    skill_max_score: number;
  };
  coverage: ResultCoverage;
  skills: ResultSkill[];
  questions: ResultQuestion[];
  evidence: ResultEvidence[];
  summary: {
    strengths: string[];
    areas_for_improvement: string[];
    recommendation: string;
    recommendation_explaination: string;
    recommendation_explanation: string;
    /**
     * Whether this recommendation is sitting on one of its own thresholds.
     * Derived by the engine, disclosed rather than smoothed away: a criterion
     * moving one point can change the recommendation while the score barely
     * moves, and a reader is entitled to know when that is the case.
     */
    recommendation_boundary: {
      at_boundary: boolean;
      reasons: string[];
      skills: string[];
      skills_meeting_bar: number;
      skills_discussed: number;
    };
  };
  scales: {
    criteria: string[];
    criterion_max: number;
    skill_max_score: number;
    rating_bands: string[];
    recommendations: string[];
    interview_depth_scale: string[];
    probe_stage_scale: string[];
    evidence_dimensions: string[];
  };
  integrity: {
    validated_evidence: number;
    quarantined_evidence: number;
    constraints_applied: number;
    repairs: number;
  };
}

// --------------------------------------------------------------------------- //
//  Pilot review
// --------------------------------------------------------------------------- //
export type ReviewVerdict = "agree" | "disagree" | "needs_review";

export type ReviewReason =
  | "wrong_evidence"
  | "wrong_skill"
  | "wrong_score"
  | "wrong_recommendation"
  | "insufficient_coverage"
  | "other";

export interface PilotReviewIn {
  reviewer: string;
  verdict: ReviewVerdict;
  reasons: ReviewReason[];
  /** What the reviewer would have recommended. Recorded beside Tara's, never over it. */
  recommendation?: string;
  note?: string;
}

export interface PilotReview extends PilotReviewIn {
  evaluation_id: string;
  session_id: string;
  /** What the assessment said when it was reviewed, frozen with the verdict. */
  ai_recommendation: string;
  ai_total_score: number;
  ai_percentage: number;
  ai_coverage_percentage: number;
  pilot_run_id: string;
  created_at: number;
  updated_at: number;
}

export interface PilotReviewList {
  session_id: string;
  evaluation_id: string;
  reviews: PilotReview[];
  verdicts: ReviewVerdict[];
  reasons: ReviewReason[];
}

export const adminApi = {
  overview: () => request<Overview>("/api/recruiter/overview"),
  pool: () => request<PoolPayload>("/api/recruiter/pool"),

  interviews: (q?: string) =>
    request<{ interviews: Interview[]; total: number }>(
      `/api/recruiter/interviews${q ? `?q=${encodeURIComponent(q)}` : ""}`,
    ),

  languages: () =>
    request<{ languages: { code: string; label: string }[] }>("/api/recruiter/languages"),

  /** The skill master: the controlled vocabulary a skill's domain comes from. */
  skillDomains: () => request<SkillMaster>("/api/recruiter/skill-domains"),

  /** The hiring stages an interview can be created for. */
  funnelStages: () => request<{ stages: FunnelStage[] }>("/api/recruiter/funnel-stages"),

  /** Job details in, a reviewable draft out. Publishes nothing. */
  generate: (body: GenerateInput) =>
    designRequest<Draft>("/api/recruiter/interviews/generate", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  generationProgress: (token: string) =>
    request<GenerationProgress>(
      `/api/recruiter/interviews/generate/progress/${encodeURIComponent(token)}`,
    ),

  draft: (id: string) => request<Draft>(`/api/recruiter/interviews/${id}/draft`),

  editDraft: (id: string, patch: DraftPatch) =>
    designRequest<Draft>(`/api/recruiter/interviews/${id}/draft`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),

  publishCheck: (id: string) =>
    request<PublishCheck>(`/api/recruiter/interviews/${id}/publish/check`),

  publish: (id: string, notes = "") =>
    designRequest<PublishResult>(`/api/recruiter/interviews/${id}/publish`, {
      method: "POST",
      body: JSON.stringify({ notes }),
    }),

  versions: (id: string) =>
    request<{ versions: VersionRow[] }>(`/api/recruiter/interviews/${id}/versions`),

  invitations: (id: string) =>
    request<InvitationList>(
      `/api/recruiter/interviews/${id}/invitations?base=${encodeURIComponent(location.origin)}`,
    ),

  sendInvitations: (id: string, candidates: string[], note = "") =>
    designRequest<{ created: Invitation[]; email_delivery_configured: boolean; message: string }>(
      `/api/recruiter/interviews/${id}/invitations?base=${encodeURIComponent(location.origin)}`,
      { method: "POST", body: JSON.stringify({ candidates, note }) },
    ),

  setOpenLink: (id: string, enabled: boolean) =>
    designRequest<{ open_link: Invitation | null }>(
      `/api/recruiter/interviews/${id}/invitations/open-link?base=${encodeURIComponent(location.origin)}`,
      { method: "POST", body: JSON.stringify({ enabled }) },
    ),

  revokeInvitation: (id: string, token: string) =>
    designRequest<{ token: string; status: string }>(
      `/api/recruiter/interviews/${id}/invitations/${token}`,
      { method: "DELETE" },
    ),

  questions: (id: string) =>
    request<QuestionPool>(`/api/recruiter/interviews/${id}/questions`),

  generateQuestions: (id: string, slot_ids?: string[]) =>
    designRequest<QuestionPool>(`/api/recruiter/interviews/${id}/questions/generate`, {
      method: "POST",
      body: JSON.stringify({ slot_ids: slot_ids ?? null }),
    }),

  editQuestion: (id: string, questionId: string, patch: QuestionPatchInput) =>
    designRequest<QuestionPool>(
      `/api/recruiter/interviews/${id}/questions/${questionId}`,
      { method: "PATCH", body: JSON.stringify(patch) },
    ),

  addQuestion: (id: string, body: NewQuestionInput) =>
    designRequest<QuestionPool>(`/api/recruiter/interviews/${id}/questions`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  removeQuestion: (id: string, questionId: string) =>
    designRequest<QuestionPool>(
      `/api/recruiter/interviews/${id}/questions/${questionId}`,
      { method: "DELETE" },
    ),

  regenerateQuestion: (id: string, questionId: string) =>
    designRequest<QuestionPool>(
      `/api/recruiter/interviews/${id}/questions/${questionId}/regenerate`,
      { method: "POST" },
    ),

  regenerate: (id: string) =>
    designRequest<Draft>(`/api/recruiter/interviews/${id}/regenerate`, { method: "POST" }),
  interview: (id: string) => request<Interview>(`/api/recruiter/interviews/${id}`),
  createInterview: (title: string) =>
    request<Interview>("/api/recruiter/interviews", {
      method: "POST",
      body: JSON.stringify({ title }),
    }),
  extract: (id: string, body: ExtractInput) =>
    request<Interview>(`/api/recruiter/interviews/${id}/extract`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  resetSkills: (id: string) =>
    request<Interview>(`/api/recruiter/interviews/${id}/reset_skills`, { method: "POST" }),
  updateInterview: (id: string, patch: Record<string, unknown>) =>
    request<Interview>(`/api/recruiter/interviews/${id}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),
  deleteInterview: (id: string) =>
    request<{ deleted: string }>(`/api/recruiter/interviews/${id}`, { method: "DELETE" }),

  candidates: (interviewId?: string) =>
    request<{ candidates: Candidate[] }>(
      `/api/recruiter/candidates${interviewId ? `?interview_id=${interviewId}` : ""}`,
    ),
  invite: (candidate_name: string, interview_id: string) =>
    request<Candidate>("/api/recruiter/candidates", {
      method: "POST",
      body: JSON.stringify({ candidate_name, interview_id }),
    }),

  session: (id: string) => request<SessionReview>(`/api/recruiter/sessions/${id}`),

  /** The persisted deep-evidence evaluation. 404 = never requested. */
  evaluation: (id: string) =>
    request<Evaluation>(`/api/recruiter/sessions/${id}/evaluation`),
  evaluationEvidence: (id: string) =>
    request<EvidencePayload>(`/api/recruiter/sessions/${id}/evaluation/evidence`),

  /**
   * The whole assessment result in one request.
   *
   * 404 = never requested. 409 = there is no result yet (the detail carries the
   * status envelope) or the result contradicted itself (the detail carries the
   * violations). 200 = a completed, self-consistent assessment.
   */
  evaluationResult: (id: string) =>
    request<AssessmentResult>(`/api/recruiter/sessions/${id}/evaluation/result`),

  /** Pilot review — what a human thought of one completed assessment. */
  reviews: (id: string) =>
    request<PilotReviewList>(`/api/recruiter/sessions/${id}/review`),

  /**
   * Record a verdict. It is stored NEXT TO the assessment, never inside it:
   * nothing here can change a score, a recommendation or a piece of evidence,
   * and no evaluator code path reads a review back. The pilot is collecting
   * calibration data, not letting one reviewer retune the product.
   */
  saveReview: (id: string, body: PilotReviewIn) =>
    request<PilotReview>(`/api/recruiter/sessions/${id}/review`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  /**
   * Request an evaluation and run it. Idempotent server-side: a completed
   * evaluation comes back unchanged, and a failed one is retried as a new run.
   * There is deliberately no re-run button for a completed evaluation — that
   * would be a human overriding a recorded result, which is its own feature.
   */
  runEvaluation: (id: string) =>
    request<Evaluation>(`/api/recruiter/sessions/${id}/evaluation`, {
      method: "POST",
      body: JSON.stringify({}),
    }),
  trail: (id: string) => request<Trail>(`/api/recruiter/sessions/${id}/trail`),
  score: (id: string) => request<SessionScore>(`/api/recruiter/sessions/${id}/score`),

  results: (interviewId: string) =>
    request<Results>(`/api/recruiter/interviews/${interviewId}/results`),
  compare: (session_ids: string[]) =>
    request<Comparison>("/api/recruiter/compare", {
      method: "POST",
      body: JSON.stringify({ session_ids }),
    }),
  fairness: (interviewId?: string) =>
    request<Fairness>(`/api/recruiter/fairness${interviewId ? `?interview_id=${interviewId}` : ""}`),
  testRun: (interviewId: string, persona: string) =>
    request<TestRun>(`/api/recruiter/interviews/${interviewId}/test_run`, {
      method: "POST",
      body: JSON.stringify({ persona }),
    }),
};
