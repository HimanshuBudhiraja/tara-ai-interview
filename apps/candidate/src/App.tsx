import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "./lib/api";
import type { Invite, Reply } from "./lib/types";
import { Access } from "./screens/Access";
import { Welcome } from "./screens/Welcome";
import { SystemCheck } from "./screens/SystemCheck";
import { Interview } from "./screens/Interview";
import { RetellInterview } from "./screens/RetellInterview";
import { Complete } from "./screens/Complete";
import { Loading, Problem } from "./screens/Problem";

/**
 * The candidate journey, start to finish.
 *
 *   access → welcome → system check → interview → complete
 *
 * Identity comes from the link (`?invite=…`) and never from a form: the
 * candidate does not sign up, does not pick a role, and cannot start an
 * interview they weren't invited to. `access` is not authentication — it
 * names who the link belongs to and asks them to confirm it, which is the one
 * thing a link alone cannot do. See `screens/Access.tsx`.
 */

type Stage =
  | "loading" | "access" | "welcome" | "check" | "interview" | "complete" | "problem";

const voiceSupported =
  typeof window !== "undefined" &&
  Boolean((window as any).SpeechRecognition ?? (window as any).webkitSpeechRecognition) &&
  "speechSynthesis" in window;

export default function App() {
  const [stage, setStage] = useState<Stage>("loading");
  const [invite, setInvite] = useState<Invite | null>(null);
  const [problem, setProblem] = useState<
    { title: string; detail: string; tone?: "error" | "info" } | null
  >(null);
  const [startError, setStartError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [session, setSession] = useState<{ id: string; first?: Reply } | null>(null);
  const [answered, setAnswered] = useState(0);
  // Chosen on the welcome screen, used a screen later when the session starts.
  const accommodations = useRef<Record<string, unknown>>({});
  const preferred = useRef("");

  const token = new URLSearchParams(location.search).get("invite")?.trim() ?? "";

  useEffect(() => {
    if (!token) {
      setProblem({
        // Deliberately does NOT spell out the `?invite=CODE` format. A
        // candidate with a working link never needs to know the anatomy of it,
        // and anyone else who lands here is the only party the hint helps.
        title: "You'll need your personal interview link",
        detail:
          "This page opens with the unique link you were sent. Use it exactly as you " +
          "received it — copying the whole link, rather than typing the address by hand.",
        // Nothing has failed: they arrived without their ticket.
        tone: "info",
      });
      setStage("problem");
      return;
    }
    api
      .invite(token)
      .then((data) => {
        setInvite(data);
        if (data.status === "complete") {
          setProblem({
            title: "This interview is already complete",
            detail:
              "You've finished this interview and it's with the hiring team. There's nothing more to do.",
          });
          setStage("problem");
          return;
        }
        setStage("access");
      })
      .catch((err: unknown) => {
        const message =
          err instanceof ApiError ? err.message : "We couldn't load your interview.";
        setProblem({ title: "We couldn't open this link", detail: message });
        setStage("problem");
      });
  }, [token]);

  const beginSession = useCallback(
    async (accommodations: Record<string, unknown>, preferredName = "") => {
      setStarting(true);
      setStartError(null);
      try {
        const { session_id, reply } = await api.start(token, accommodations, preferredName);
        setSession({ id: session_id, first: reply });
        setStage("interview");
      } catch (err: unknown) {
        setStartError(err instanceof ApiError ? err.message : "Please try again.");
        // Back to the brief, not to the door: that is where the error renders
        // and where the start button is. Returning to `access` would drop the
        // message and make a failed start look like nothing happened.
        setStage("welcome");
      } finally {
        setStarting(false);
      }
    },
    [token],
  );

  const handleComplete = useCallback(() => {
    if (!session) return;
    // Read the final count from the server rather than trusting a local tally.
    api
      .session(session.id)
      .then((snapshot) => setAnswered(snapshot.progress.answered))
      .catch(() => undefined)
      .finally(() => setStage("complete"));
  }, [session]);

  if (stage === "loading") return <Loading />;
  if (stage === "problem" && problem) return <Problem {...problem} />;

  if (stage === "access" && invite) {
    return <Access invite={invite} onContinue={() => setStage("welcome")} />;
  }

  if (stage === "welcome" && invite) {
    return (
      <Welcome
        invite={invite}
        starting={starting}
        error={startError}
        onStart={({ accommodations: opts, preferredName }) => {
          accommodations.current = opts;
          preferred.current = preferredName;
          // A browser that cannot do speech cannot do this interview, and it
          // is told so here rather than being walked through a hardware check
          // it is going to fail. There is no typed channel to divert into: a
          // written interview is a different assessment, not a fallback.
          if (!voiceSupported) {
            setProblem({
              title: "This browser can't run a spoken interview",
              detail:
                "Your interview is a conversation, so it needs a browser that can use your " +
                "microphone and speak back. Chrome, Edge or Safari on a laptop will work — " +
                "reopen your link there. Your link still works and nothing has been used up.",
              tone: "info",
            });
            setStage("problem");
            return;
          }
          setStage("check");
        }}
      />
    );
  }

  if (stage === "check") {
    return (
      <SystemCheck
        onReady={() => void beginSession(accommodations.current, preferred.current)}
      />
    );
  }

  if (stage === "interview" && session) {
    // Two transports, one interview. In "retell" mode the vendor talks to our
    // server directly and the browser only joins the call; in "browser" mode
    // the client owns the turn loop. The orchestrator, the questions and the
    // evaluation are identical either way — this chooses who carries the audio,
    // nothing else.
    return invite?.voice_mode === "retell" ? (
      <RetellInterview sessionId={session.id} onComplete={handleComplete} />
    ) : (
      <Interview
        sessionId={session.id}
        firstReply={session.first}
        speechRate={invite?.speech_rate}
        onComplete={handleComplete}
      />
    );
  }

  if (stage === "complete") {
    return <Complete name={invite?.candidate_name ?? "there"} answered={answered} />;
  }

  return <Loading />;
}
