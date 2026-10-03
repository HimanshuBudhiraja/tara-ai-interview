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
 * **On Retell's v3 client.** `RetellWebClient` and POST /v2/create-web-call are
 * retired on 2026-10-18. The v3 `RetellClient` normally mints the call from the
 * browser with a key, which would hand every candidate the ability to place
 * calls on the account. So its address is set to THIS server instead: the
 * server already minted the call with the secret key (/voice), its answer is
 * handed to the client in place of the client's own "create", and the only
 * other things the client asks for, stopping the call and the live
 * transcript, go to this session's relay. No key is ever in the bundle.
 *
 * **On the dynamic import.** The SDK is around 590 KB of JavaScript (LiveKit
 * underneath), and importing it at module scope nearly tripled the candidate
 * bundle. Most deployments do not configure Retell at all, and the ones that
 * do still serve the access, welcome, consent and setup screens before any
 * call exists — so a static import makes a candidate on a poor connection wait
 * on a vendor they may never reach. Loading it inside `join` means the cost is
 * paid once, at the moment of joining, by the only people it is for.
 */

import type { VoiceCall } from "../lib/api";

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

/** Exactly what we use of the v3 SDK, declared so there's no static import of the package. */
interface WebCallSession {
  ready: Promise<void>;
  mute(): void;
  unmute(): void;
  end(): Promise<void>;
}
interface V3Client {
  createWebCall(options: Record<string, unknown>): WebCallSession;
}
interface V3Module {
  RetellClient: new (config: { key: string; baseURL: string; fetch: typeof fetch }) => V3Client;
}

export class RetellCall {
  private session: WebCallSession | null = null;
  private ended = false;

  get active(): boolean {
    return this.session !== null && !this.ended;
  }

  /**
   * Join a call the server already created.
   * `relayBase` is this session's relay: `/api/session/{id}/voice/retell`.
   */
  async join(created: VoiceCall, relayBase: string, handlers: CallHandlers): Promise<void> {
    handlers.onPhase("connecting");
    this.ended = false;

    const { RetellClient } = (await import("retell-client-js-sdk")) as unknown as V3Module;
    // The client's own "create call" is answered with what the server already
    // created; anything else goes to the relay with the session cookie only.
    const ourFetch: typeof fetch = (input, init) => {
      const url = String(input);
      if (url.endsWith("/v3/create-web-call")) {
        return Promise.resolve(new Response(JSON.stringify(created), {
          status: 200, headers: { "Content-Type": "application/json" },
        }));
      }
      const headers = { ...((init?.headers as Record<string, string>) ?? {}) };
      delete headers.Authorization;
      return fetch(url, { ...init, headers, credentials: "same-origin" });
    };
    const client = new RetellClient({
      key: "session",
      baseURL: new URL(relayBase, window.location.origin).toString(),
      fetch: ourFetch,
    });

    const fail = (err: unknown) => {
      if (this.ended) return;
      this.ended = true;
      handlers.onPhase("failed");
      handlers.onError(err instanceof Error ? err.message : "The call dropped unexpectedly.");
    };

    const session = client.createWebCall({
      agent_id: "session",
      transcript: true,
      // Raw samples so the orb reacts to the candidate's own voice. Without
      // this the orb is inert while they speak, which reads as "it can't hear
      // me" — the single most common thing a voice interface gets wrong.
      audio: { emitRawAudioSamples: true },
      hooks: {
        onStatus: (status: string) => { if (status === "live") handlers.onPhase("listening"); },
        onAgentStartTalking: () => handlers.onPhase("speaking"),
        // Back to listening the moment Tara stops. The microphone is Retell's
        // to manage — this only moves the picture on screen.
        onAgentStopTalking: () => handlers.onPhase("listening"),
        onTranscript: (rows: Utterance[]) => {
          // The last thing the CANDIDATE said. Device feedback — proof the
          // microphone is reaching Retell — not a transcript to read back.
          for (let i = (rows?.length ?? 0) - 1; i >= 0; i -= 1) {
            if (rows[i]?.role === "user") {
              handlers.onHeard((rows[i].content ?? "").trim());
              return;
            }
          }
        },
        onAudio: (samples: Float32Array) => {
          // RMS rather than peak: peak makes the orb twitch on consonants,
          // where RMS tracks how loudly someone is actually speaking.
          let sum = 0;
          for (let i = 0; i < samples.length; i += 1) sum += samples[i] * samples[i];
          handlers.onLevel(Math.min(1, Math.sqrt(sum / (samples.length || 1)) * 4));
        },
        onEnd: () => {
          if (this.ended) return;
          this.ended = true;
          handlers.onPhase("ended");
          handlers.onEnded();
        },
        onError: fail,
      },
    });
    this.session = session;
    await session.ready;
  }

  /** End the call. Safe to call twice, and on a call that never started. */
  leave(): void {
    this.ended = true;
    try {
      void this.session?.end();
    } catch {
      // Already gone. Leaving is best-effort by nature: the server closes the
      // session from its own side when the socket drops.
    }
    this.session = null;
  }
}
