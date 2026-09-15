/**
 * Joining a Retell web call, and nothing more.
 *
 * This is NOT a `VoiceChannel`, and the difference is the whole reason this
 * file exists rather than a fourth implementation of that interface.
 * `VoiceChannel` models a speech engine the client drives — you call
 * `speak(text)` and `listen()`, and the client owns the turn loop. Retell is
 * the other way round: the browser hands it a microphone, and Retell runs the
 * conversation by calling OUR SERVER over a websocket and speaking whatever
 * the orchestrator returns. The client never sees a question and never sends
 * an answer.
 *
 * So what is left for the browser is genuinely small: join, surface enough
 * state to render an orb and a status line, and leave.
 *
 * **On `RetellWebClient` being deprecated.** The v3 SDK prefers `RetellClient`
 * with `createWebCall()`, which mints the call from the browser — and minting
 * requires the Retell API key. Putting a provider key in a candidate's browser
 * would hand every candidate the ability to place calls on the account, and
 * this codebase refuses that everywhere else. The deprecated class is the only
 * entry point that takes a bare `accessToken` the server already obtained, so
 * it is the correct one here. If it is removed in v4, the replacement must
 * still be a token-only join; it must not become a key in the bundle.
 *
 * **On the dynamic import.** The SDK is around 590 KB of JavaScript (LiveKit
 * underneath), and importing it at module scope nearly tripled the candidate
 * bundle. Most deployments do not configure Retell at all, and the ones that
 * do still serve the access, welcome, consent and setup screens before any
 * call exists — so a static import makes a candidate on a poor connection wait
 * on a vendor they may never reach. Loading it inside `join` means the cost is
 * paid once, at the moment of joining, by the only people it is for.
 */

export type CallPhase = "connecting" | "speaking" | "listening" | "ended" | "failed";

export interface CallHandlers {
  onPhase(phase: CallPhase): void;
  /** Live level 0..1, for the orb. Retell hands us the raw samples. */
  onLevel(level: number): void;
  /** The candidate's most recent words, for the "we can hear you" line. */
  onHeard(text: string): void;
  onEnded(): void;
  onError(message: string): void;
}

interface Utterance {
  role?: string;
  content?: string;
}

/**
 * Exactly what we use of the SDK.
 *
 * Declared here so this module needs no static import of the package. The
 * event names below are checked against the SDK's own `emit` calls, not
 * guessed — `RetellWebClient` extends an untyped `EventEmitter`, so a wrong
 * name would compile perfectly and simply never fire.
 */
interface WebClient {
  on(event: string, handler: (arg: never) => void): unknown;
  startCall(config: {
    accessToken: string;
    emitRawAudioSamples?: boolean;
  }): Promise<void>;
  stopCall(): void;
}

export class RetellCall {
  private client: WebClient | null = null;
  private ended = false;

  get active(): boolean {
    return this.client !== null && !this.ended;
  }

  async join(accessToken: string, handlers: CallHandlers): Promise<void> {
    handlers.onPhase("connecting");

    const { RetellWebClient } = await import("retell-client-js-sdk");
    const client = new RetellWebClient() as unknown as WebClient;
    this.client = client;
    this.ended = false;

    const on = (event: string, handler: (arg: never) => void) =>
      client.on(event, handler);

    on("call_started", () => handlers.onPhase("listening"));
    on("agent_start_talking", () => handlers.onPhase("speaking"));
    // Back to listening the moment Tara stops. The microphone is Retell's to
    // manage — this only moves the picture on screen.
    on("agent_stop_talking", () => handlers.onPhase("listening"));

    on("update", ((update: { transcript?: Utterance[] }) => {
      const rows = update?.transcript ?? [];
      // The last thing the CANDIDATE said. Device feedback — proof the
      // microphone is reaching Retell — not a transcript to read back.
      for (let i = rows.length - 1; i >= 0; i -= 1) {
        if (rows[i]?.role === "user") {
          handlers.onHeard((rows[i].content ?? "").trim());
          return;
        }
      }
    }) as (arg: never) => void);

    on("audio", ((samples: Float32Array) => {
      // RMS rather than peak: peak makes the orb twitch on consonants, where
      // RMS tracks how loudly someone is actually speaking.
      let sum = 0;
      for (let i = 0; i < samples.length; i += 1) sum += samples[i] * samples[i];
      handlers.onLevel(Math.min(1, Math.sqrt(sum / (samples.length || 1)) * 4));
    }) as (arg: never) => void);

    on("call_ended", () => {
      this.ended = true;
      handlers.onPhase("ended");
      handlers.onEnded();
    });

    on("error", ((err: unknown) => {
      this.ended = true;
      handlers.onPhase("failed");
      handlers.onError(
        err instanceof Error ? err.message : "The call dropped unexpectedly.",
      );
    }) as (arg: never) => void);

    await client.startCall({
      accessToken,
      // Raw samples so the orb reacts to the candidate's own voice. Without
      // this the orb is inert while they speak, which reads as "it can't hear
      // me" — the single most common thing a voice interface gets wrong.
      emitRawAudioSamples: true,
    });
  }

  /** End the call. Safe to call twice, and on a call that never started. */
  leave(): void {
    this.ended = true;
    try {
      this.client?.stopCall();
    } catch {
      // Already gone. Leaving is best-effort by nature: the server closes the
      // session from its own side when the socket drops.
    }
    this.client = null;
  }
}
