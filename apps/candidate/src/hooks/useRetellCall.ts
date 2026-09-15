import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { RetellCall, type CallPhase } from "../voice/retellCall";
import type { Progress } from "../lib/types";

/**
 * The interview, when Retell is carrying the voice.
 *
 * Deliberately much smaller than `useInterview`. In browser-speech mode the
 * client owns the turn loop: it holds a websocket to our server, sends answers,
 * receives replies and speaks them. Here none of that is the browser's job —
 * Retell talks to our server directly, so there is no turn loop to own and no
 * answer to send. What is left is join the call, mirror its state on screen,
 * and notice when it is over.
 *
 * Progress is POLLED rather than pushed, and that is a real limitation stated
 * plainly: the authoritative session state lives on the server and advances
 * when Retell asks it a question, so the browser learns about it on a delay of
 * up to one poll. A pushed channel would mean a second websocket from the
 * browser purely to watch a conversation it is not part of. The rail moving a
 * couple of seconds late is a fair price; claiming otherwise would not be.
 */
export function useRetellCall({
  sessionId,
  onEnded,
}: {
  sessionId: string;
  onEnded: () => void;
}) {
  const [phase, setPhase] = useState<CallPhase>("connecting");
  const [level, setLevel] = useState(0);
  const [heard, setHeard] = useState("");
  const [progress, setProgress] = useState<Progress | null>(null);
  const [error, setError] = useState<string | null>(null);

  const call = useRef<RetellCall | null>(null);
  const finished = useRef(false);

  const end = useCallback(() => {
    finished.current = true;
    call.current?.leave();
    setPhase("ended");
    onEnded();
  }, [onEnded]);

  // --- join, once -------------------------------------------------------- //
  useEffect(() => {
    let live = true;
    const instance = new RetellCall();
    call.current = instance;

    (async () => {
      try {
        const { access_token } = await api.startVoiceCall(sessionId);
        if (!live) return;
        if (!access_token) {
          setError("The voice service didn't return a call. Please reopen your link.");
          setPhase("failed");
          return;
        }
        await instance.join(access_token, {
          onPhase: (p) => live && setPhase(p),
          onLevel: (l) => live && setLevel(l),
          onHeard: (t) => live && setHeard(t),
          onEnded: () => {
            if (!live || finished.current) return;
            finished.current = true;
            onEnded();
          },
          onError: (message) => live && setError(message),
        });
      } catch (err) {
        if (!live) return;
        setError((err as Error).message || "Couldn't start the call.");
        setPhase("failed");
      }
    })();

    return () => {
      live = false;
      instance.leave();
    };
  }, [sessionId, onEnded]);

  // --- watch the server's own view of the interview ---------------------- //
  useEffect(() => {
    let live = true;
    const poll = async () => {
      try {
        const snapshot = await api.session(sessionId);
        if (!live) return;
        setProgress(snapshot.progress);
        // The server is the authority on whether the interview is over — not
        // the call ending. A dropped call is resumable; a completed interview
        // is not, and only the orchestrator knows which just happened.
        if (snapshot.phase === "complete" && !finished.current) {
          finished.current = true;
          call.current?.leave();
          onEnded();
        }
      } catch {
        // A failed poll is not a failed interview. The call is unaffected.
      }
    };
    void poll();
    const id = setInterval(poll, 3000);
    return () => {
      live = false;
      clearInterval(id);
    };
  }, [sessionId, onEnded]);

  return { phase, level, heard, progress, error, end };
}
