export type SpeechKind =
  | "greeting"
  | "question"
  | "probe"
  | "ack"
  | "repeat"
  | "clarify"
  | "closing"
  | "hold";

export interface Coverage {
  id: string;
  label: string;
  asked: number;
  target: number;
}

export interface Progress {
  asked: number;
  answered: number;
  total: number;
  coverage: Coverage[];
  phase: string;
}

/** One thing Tara says, plus everything the screen needs to react to it. */
export interface Reply {
  text: string;
  kind: SpeechKind;
  item_id: string | null;
  ends: boolean;
  progress: Progress;
  /** Tara is waiting on the same question — don't advance the progress rail. */
  awaiting_same_answer: boolean;
  /**
   * Tara has said out loud that a device looks broken and named what to try.
   * The UI does not act on it — she has already given the remedy in the
   * audio, and there is no typed channel to reveal. Kept because the flag is
   * on the wire and the recruiter-side audit reads it.
   */
  device_help_offered: boolean;
}

export interface Invite {
  token: string;
  candidate_name: string;
  role: string;
  role_title: string;
  status: string;
  question_count: number;
  estimated_minutes: number;
  competencies: string[];
  /** The five criteria the evaluator actually scores. */
  assessed_on: string[];
  /** What the assessment is structurally incapable of scoring. */
  not_assessed: string[];
  resumable: boolean;
  session_id: string | null;
  answered: number;
  /**
   * Which voice path this deployment can run: "retell" when the vendor is
   * configured server-side, "browser" otherwise. A server fact, so it is
   * served rather than sniffed — and "browser" is a working path, not a
   * degraded one.
   */
  voice_mode: "retell" | "browser";
  /**
   * How fast Tara speaks, from the published definition. A delivery setting,
   * not assessment content — it says nothing about what is asked.
   */
  speech_rate: number;
}

export interface TranscriptTurn {
  speaker: "tara" | "candidate";
  text: string;
  at: number;
  kind: SpeechKind | null;
}

export interface SessionSnapshot {
  session_id: string;
  candidate_name: string;
  role_title: string;
  phase: string;
  channel: "voice" | "text";
  progress: Progress;
  transcript: TranscriptTurn[];
}

/** What the candidate's screen is doing right now. */
export type CallPhase =
  | "connecting"
  | "speaking" // Tara is talking
  | "listening" // mic is open, candidate's turn
  | "thinking" // answer sent, waiting on the orchestrator
  | "reconnecting"
  | "ended"
  | "failed";
