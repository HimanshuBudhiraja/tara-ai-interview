/**
 * The mouth and ears — and nothing else.
 *
 * TARA's architecture keeps the voice vendor strictly at the I/O surface: it
 * turns text into sound and sound into text. It never decides what to ask, when
 * to follow up, or when the interview ends. Every one of those decisions is made
 * by the orchestrator on the server and arrives here as plain text.
 *
 * That boundary is what this interface is for. Swapping the browser's built-in
 * speech engine for a vendor (Retell, Deepgram, ElevenLabs, a telephony bridge)
 * means writing one more implementation of `VoiceChannel`. No screen, no hook,
 * and no server route changes.
 */

export interface ListenHandlers {
  /** Partial text as the candidate speaks — for live captions only. */
  onPartial(text: string): void;
  /** The candidate finished a turn: final text, ready to send. */
  onFinal(text: string): void;
  /** Nothing was said for the whole listening window. */
  onSilence(): void;
  /** The channel broke (mic revoked, engine crash, network). */
  onError(message: string): void;
}

export interface VoiceChannel {
  /** For diagnostics and the "having trouble?" panel. */
  readonly name: string;
  /** False when this browser/device can't run this channel at all. */
  readonly supported: boolean;
  /** True while audio is actually playing. */
  readonly speaking: boolean;

  /** Speak the text. Resolves when the last word has played (or was cut off). */
  speak(text: string): Promise<void>;
  /** Stop speaking immediately — used for barge-in and for ending the call. */
  stopSpeaking(): void;

  /** Open the mic and begin a candidate turn. */
  listen(handlers: ListenHandlers): void;
  /**
   * Close the mic. Anything already said is delivered via `onFinal` first, so a
   * manual "I'm done" never drops the last sentence. Pass `silent` to discard it
   * instead — used when the mic is being closed to make way for Tara's turn.
   */
  stopListening(silent?: boolean): void;

  /** Release every device handle. Called on unmount and on end-of-interview. */
  dispose(): void;
}
