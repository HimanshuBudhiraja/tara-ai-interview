import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { socketUrl } from "../lib/api";
import type { CallPhase, Progress, Reply, TranscriptTurn } from "../lib/types";
import { BrowserVoiceChannel } from "../voice/browserVoice";
import type { VoiceChannel } from "../voice/VoiceChannel";

/**
 * The client half of one turn.
 *
 * The loop is deliberately dumb: Tara speaks, the mic opens, the candidate's
 * words go to the server, the server says what happens next. Nothing here
 * decides to move on, to follow up, or to end — those are the orchestrator's,
 * and duplicating any of them on the client would let the two disagree.
 *
 * Everything it does own is presentation and device state: whether the orb is
 * pulsing, what the microphone is currently hearing, and reconnecting a
 * dropped socket.
 */

interface Options {
  sessionId: string;
  firstReply?: Reply;
  onEnded?: () => void;
  /** Speaking pace from the published interview. See RuntimeLimits.speech_rate. */
  speechRate?: number;
}

const RECONNECT_DELAYS_MS = [500, 1000, 2000, 4000, 8000];

export function useInterview({ sessionId, firstReply, onEnded, speechRate = 0.9 }: Options) {
  const [phase, setPhase] = useState<CallPhase>("connecting");
  const [turns, setTurns] = useState<TranscriptTurn[]>([]);
  const [progress, setProgress] = useState<Progress | null>(firstReply?.progress ?? null);
  // What Tara is asking right now — the demo prompter needs both to pick a line.
  const [currentItem, setCurrentItem] = useState<string | null>(null);
  const [currentKind, setCurrentKind] = useState<Reply["kind"] | null>(null);
  const [partial, setPartial] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [ended, setEnded] = useState(false);

  const ws = useRef<WebSocket | null>(null);
  const voice = useRef<VoiceChannel | null>(null);
  const attempt = useRef(0);
  const closedByUs = useRef(false);
  const pendingFirst = useRef<Reply | undefined>(firstReply);
  // Guards double-submitting one turn when the end-of-turn timer and a manual
  // send race each other.
  const turnOpen = useRef(false);

  if (voice.current === null) {
    voice.current = new BrowserVoiceChannel("en-US", speechRate);
  }
  const voiceSupported = voice.current?.supported ?? false;
  const usingVoice = voiceSupported;

  const send = useCallback((payload: Record<string, unknown>) => {
    const socket = ws.current;
    if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(payload));
  }, []);

  /**
   * A fresh id for one turn.
   *
   * The server applies a turn at most once per id, so a re-send after a socket
   * flap is the same turn rather than a second answer. Minted here because only
   * the client knows whether two deliveries are one turn or two.
   */
  const turnId = useCallback(
    () =>
      typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID()
        : `t_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`,
    [],
  );

  const appendTurn = useCallback((turn: TranscriptTurn) => {
    setTurns((prev) => [...prev, turn]);
  }, []);

  // ------------------------------------------------------------ listening
  const openMic = useCallback(() => {
    if (!usingVoice || !voice.current) {
      setPhase("listening");
      return;
    }
    turnOpen.current = true;
    setPhase("listening");
    setPartial("");
    voice.current.listen({
      onPartial: (text) => setPartial(text),
      onFinal: (text) => {
        if (!turnOpen.current) return;
        turnOpen.current = false;
        setPartial("");
        appendTurn({ speaker: "candidate", text, at: Date.now() / 1000, kind: null });
        setPhase("thinking");
        send({ type: "answer", text, turn_id: turnId() });
      },
      onSilence: () => {
        if (!turnOpen.current) return;
        // Report it; the server owns what silence means.
        send({ type: "silence", turn_id: turnId() });
      },
      onError: (message) => {
        // Surfaced as what it is. There is no typed channel to fall into: this
        // is a spoken interview, and quietly turning it into a written one
        // would change what is being assessed without telling anyone.
        setError(message);
      },
    });
  }, [appendTurn, send, turnId, usingVoice]);

  // ------------------------------------------------------------- speaking
  const deliver = useCallback(
    async (reply: Reply) => {
      // Still recorded, and no longer displayed: questions and follow-ups are
      // heard, not read. `turns` is what the session snapshot and the recruiter
      // report are built from, so the record is unchanged — what changed is
      // that the candidate is answering a question rather than reading one.
      appendTurn({ speaker: "tara", text: reply.text, at: Date.now() / 1000, kind: reply.kind });
      setCurrentItem(reply.item_id);
      setCurrentKind(reply.kind);
      if (reply.progress?.total) setProgress(reply.progress);

      if (usingVoice) {
        setPhase("speaking");
        await voice.current!.speak(reply.text);
      }

      if (reply.ends) {
        setEnded(true);
        setPhase("ended");
        voice.current?.dispose();
        closedByUs.current = true;
        ws.current?.close();
        onEnded?.();
        return;
      }
      openMic();
    },
    [appendTurn, onEnded, openMic, usingVoice],
  );

  // -------------------------------------------------------------- socket
  useEffect(() => {
    let disposed = false;
    let retryTimer: number | null = null;

    const connect = () => {
      if (disposed) return;
      const socket = new WebSocket(socketUrl(sessionId));
      ws.current = socket;

      socket.onopen = () => {
        attempt.current = 0;
        setError(null);
        // The greeting came back from the REST start call, so play it now that
        // there's a socket to answer into.
        const first = pendingFirst.current;
        if (first) {
          pendingFirst.current = undefined;
          void deliver(first);
        } else {
          setPhase("listening");
        }
      };

      socket.onmessage = (event) => {
        let msg: any;
        try {
          msg = JSON.parse(event.data);
        } catch {
          return;
        }
        if (msg.type === "hello") {
          if (msg.progress) setProgress(msg.progress);
          if (Array.isArray(msg.transcript) && msg.transcript.length && turns.length === 0) {
            setTurns(msg.transcript);
          }
          return;
        }
        if (msg.type === "thinking") {
          setPhase("thinking");
          return;
        }
        if (msg.type === "say") {
          void deliver(msg.reply as Reply);
          return;
        }
        if (msg.type === "error") {
          setError(msg.message ?? "Something went wrong.");
          setPhase("failed");
        }
      };

      socket.onclose = () => {
        if (disposed || closedByUs.current) return;
        // A dropped socket is not a dropped interview: state is on the server
        // after every turn, so reconnecting resumes rather than restarts.
        setPhase("reconnecting");
        voice.current?.stopListening();
        const delay = RECONNECT_DELAYS_MS[Math.min(attempt.current, RECONNECT_DELAYS_MS.length - 1)];
        attempt.current += 1;
        if (attempt.current > 8) {
          setError("We lost the connection. Reopen your interview link to pick up where you left off.");
          setPhase("failed");
          return;
        }
        retryTimer = window.setTimeout(connect, delay);
      };
    };

    connect();
    return () => {
      disposed = true;
      if (retryTimer !== null) window.clearTimeout(retryTimer);
      voice.current?.dispose();
      ws.current?.close();
    };
    // deliver is stable enough for the socket's lifetime; re-running this effect
    // would tear down a live call.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  // ------------------------------------------------------------- actions
  /**
   * Send a turn as text.
   *
   * NOT a candidate affordance — nothing in the interview UI calls this. It
   * exists for `DemoPrompter`, which only mounts behind `?demo=1`, as the
   * escape hatch for a dead microphone in front of an audience. It routes
   * through the identical turn message, so what the server sees is a turn
   * either way.
   */
  const submitText = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || phase === "thinking" || ended) return;
      turnOpen.current = false;
      voice.current?.stopListening(true);
      setPartial("");
      appendTurn({ speaker: "candidate", text: trimmed, at: Date.now() / 1000, kind: null });
      setPhase("thinking");
      send({ type: "answer", text: trimmed, turn_id: turnId() });
    },
    [appendTurn, ended, phase, send, turnId],
  );

  /** Candidate says they're finished talking before the pause timer fires. */
  const doneSpeaking = useCallback(() => {
    if (!usingVoice || !turnOpen.current) return;
    voice.current?.stopListening();
  }, [usingVoice]);

  const askRepeat = useCallback(() => {
    if (ended) return;
    turnOpen.current = false;
    voice.current?.stopListening(true);
    setPhase("thinking");
    send({ type: "repeat", turn_id: turnId() });
  }, [ended, send, turnId]);

  const endInterview = useCallback(() => {
    if (ended) return;
    turnOpen.current = false;
    voice.current?.stopSpeaking();
    voice.current?.stopListening(true);
    setPhase("thinking");
    send({ type: "end", turn_id: turnId() });
  }, [ended, send, turnId]);

  const status = useMemo(() => describe(phase, usingVoice), [phase, usingVoice]);

  useEffect(() => {
    const socket = ws.current;
    const ping = window.setInterval(() => {
      if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "ping" }));
    }, 25000);
    return () => window.clearInterval(ping);
  }, []);

  return {
    phase,
    status,
    currentItem,
    currentKind,
    progress,
    partial,
    error,
    ended,
    voiceSupported,
    usingVoice,
    submitText,
    doneSpeaking,
    askRepeat,
    endInterview,
  };
}

function describe(phase: CallPhase, usingVoice: boolean): string {
  switch (phase) {
    case "connecting":
      return "Connecting…";
    case "speaking":
      return "Tara is speaking";
    case "listening":
      return usingVoice ? "Listening — go ahead" : "Your turn — type your answer";
    case "thinking":
      return "Tara is thinking";
    case "reconnecting":
      return "Reconnecting — nothing is lost";
    case "ended":
      return "Interview complete";
    case "failed":
      return "Connection problem";
  }
}
