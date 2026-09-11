import type { ListenHandlers, VoiceChannel } from "./VoiceChannel";

/**
 * Voice using only what the browser ships: SpeechSynthesis out, SpeechRecognition in.
 *
 * Chosen so the prototype runs with no vendor account, no keys, and no tunnel —
 * open the link and talk. It is a real implementation of the same interface a
 * production vendor would implement, not a stub.
 *
 * Two honest limitations of this particular channel, both solved by a real
 * vendor rather than by more code here:
 *
 *  1. Half-duplex. The recognizer hears the speakers, so if we listened while
 *     Tara talked she would transcribe herself. We therefore close the mic for
 *     the duration of her turn, which means no barge-in. A vendor SDK does
 *     acoustic echo cancellation and can run full-duplex.
 *  2. Chrome-family only. Safari's implementation is partial and Firefox has
 *     none, so `supported` is false there and the app falls back to typing.
 */

type SpeechRecognitionLike = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  start(): void;
  stop(): void;
  abort(): void;
  onresult: ((e: any) => void) | null;
  onerror: ((e: any) => void) | null;
  onend: (() => void) | null;
  onstart: (() => void) | null;
};

function recognizerCtor(): (new () => SpeechRecognitionLike) | null {
  const w = window as any;
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

/** How long after the last word before we call the turn finished. */
const END_OF_TURN_MS = 2200;
/** How long we wait for the candidate to say anything at all. */
const NOTHING_SAID_MS = 15000;

export class BrowserVoiceChannel implements VoiceChannel {
  readonly name = "browser-speech";
  readonly supported: boolean;

  private recognition: SpeechRecognitionLike | null = null;
  private handlers: ListenHandlers | null = null;
  private listening = false;
  private finalText = "";
  private partialText = "";
  private endOfTurnTimer: number | null = null;
  private nothingSaidTimer: number | null = null;
  private voice: SpeechSynthesisVoice | null = null;
  private _speaking = false;

  constructor(private readonly lang = "en-US") {
    this.supported = Boolean(recognizerCtor()) && "speechSynthesis" in window;
    if (this.supported) this.pickVoice();
  }

  get speaking(): boolean {
    return this._speaking;
  }

  // ---------------------------------------------------------------- speaking
  private pickVoice() {
    const choose = () => {
      const voices = window.speechSynthesis.getVoices().filter((v) => v.lang.startsWith("en"));
      if (!voices.length) return;
      // Prefer a natural-sounding local voice; these names are the good ones
      // across macOS and Chrome, in descending order of how human they sound.
      const preferred = ["Samantha", "Google UK English Female", "Google US English", "Karen", "Moira"];
      this.voice =
        preferred.map((n) => voices.find((v) => v.name === n)).find(Boolean) ??
        voices.find((v) => v.localService) ??
        voices[0];
    };
    choose();
    // Chrome populates the voice list asynchronously, often after first paint.
    window.speechSynthesis.onvoiceschanged = choose;
  }

  speak(text: string): Promise<void> {
    if (!this.supported || !text.trim()) return Promise.resolve();
    // The mic must be shut before audio starts or the recognizer transcribes Tara.
    this.stopListening(true);
    window.speechSynthesis.cancel();

    return new Promise<void>((resolve) => {
      const u = new SpeechSynthesisUtterance(text);
      u.lang = this.lang;
      if (this.voice) u.voice = this.voice;
      // Slightly under default: interview questions land better a little slower.
      u.rate = 0.98;
      u.pitch = 1.0;

      let settled = false;
      const finish = () => {
        if (settled) return;
        settled = true;
        this._speaking = false;
        resolve();
      };
      u.onend = finish;
      u.onerror = finish;

      this._speaking = true;
      window.speechSynthesis.speak(u);

      // Chrome drops `onend` on long utterances often enough that a stuck
      // interview is a real risk. Estimate the duration from the text and
      // release the turn if the event never arrives.
      const estimateMs = Math.min(120_000, 900 + (text.split(/\s+/).length / 2.6) * 1000);
      window.setTimeout(() => {
        if (!settled && !window.speechSynthesis.speaking) finish();
      }, estimateMs);
    });
  }

  stopSpeaking(): void {
    if (!this.supported) return;
    window.speechSynthesis.cancel();
    this._speaking = false;
  }

  // --------------------------------------------------------------- listening
  listen(handlers: ListenHandlers): void {
    if (!this.supported) {
      handlers.onError("This browser can't do speech recognition. Chrome or Edge will work.");
      return;
    }
    this.handlers = handlers;
    this.finalText = "";
    this.partialText = "";

    const Ctor = recognizerCtor()!;
    const rec = new Ctor();
    rec.lang = this.lang;
    rec.continuous = true;
    rec.interimResults = true;
    rec.maxAlternatives = 1;

    rec.onresult = (event: any) => {
      let interim = "";
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const result = event.results[i];
        const text = result[0]?.transcript ?? "";
        if (result.isFinal) this.finalText += (this.finalText ? " " : "") + text.trim();
        else interim += text;
      }
      this.partialText = interim;
      this.clearNothingSaid();
      handlers.onPartial((this.finalText + " " + interim).trim());
      this.armEndOfTurn();
    };

    rec.onerror = (event: any) => {
      const code = event?.error;
      // "no-speech" and "aborted" are normal parts of a conversation, not faults.
      if (code === "no-speech" || code === "aborted") return;
      if (code === "not-allowed" || code === "service-not-allowed") {
        this.listening = false;
        handlers.onError("Microphone access was blocked. Allow it in your browser, then rejoin.");
        return;
      }
      handlers.onError("The microphone dropped out. Trying again…");
    };

    rec.onend = () => {
      // Chrome ends recognition on its own after a pause. While the turn is
      // still open, restart it — otherwise the candidate's mic silently dies
      // mid-sentence and they never find out.
      if (this.listening) {
        try {
          rec.start();
        } catch {
          /* already starting; the next onend will retry */
        }
      }
    };

    this.recognition = rec;
    this.listening = true;
    try {
      rec.start();
    } catch {
      /* start() throws if called twice; harmless */
    }
    this.armNothingSaid();
  }

  stopListening(silent = false): void {
    this.clearEndOfTurn();
    this.clearNothingSaid();
    this.listening = false;
    const rec = this.recognition;
    this.recognition = null;
    if (rec) {
      rec.onend = null;
      rec.onresult = null;
      rec.onerror = null;
      try {
        rec.abort();
      } catch {
        /* already dead */
      }
    }
    // Deliver whatever they'd said so a manual "send" never loses a sentence.
    if (!silent && this.handlers) {
      const said = (this.finalText + " " + this.partialText).trim();
      if (said) this.handlers.onFinal(said);
    }
    this.finalText = "";
    this.partialText = "";
  }

  dispose(): void {
    this.stopSpeaking();
    this.stopListening(true);
    this.handlers = null;
    if (this.supported) window.speechSynthesis.onvoiceschanged = null;
  }

  // ------------------------------------------------------------------ timers
  private armEndOfTurn() {
    this.clearEndOfTurn();
    this.endOfTurnTimer = window.setTimeout(() => {
      const said = (this.finalText + " " + this.partialText).trim();
      if (!said) return;
      const handlers = this.handlers;
      this.stopListening(true);
      handlers?.onFinal(said);
    }, END_OF_TURN_MS);
  }

  private clearEndOfTurn() {
    if (this.endOfTurnTimer !== null) {
      window.clearTimeout(this.endOfTurnTimer);
      this.endOfTurnTimer = null;
    }
  }

  private armNothingSaid() {
    this.clearNothingSaid();
    this.nothingSaidTimer = window.setTimeout(() => {
      if (!this.listening) return;
      // Report the silence but keep the mic open — the server decides whether
      // to nudge, repeat, or move on. The client never advances the interview.
      this.handlers?.onSilence();
      this.armNothingSaid();
    }, NOTHING_SAID_MS);
  }

  private clearNothingSaid() {
    if (this.nothingSaidTimer !== null) {
      window.clearTimeout(this.nothingSaidTimer);
      this.nothingSaidTimer = null;
    }
  }
}
