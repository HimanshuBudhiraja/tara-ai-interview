import { useCallback, useEffect, useState } from "react";
import { adminApi, type Interview } from "./lib/adminApi";
import { auth, SESSION_LOST, type Session } from "./lib/auth";
import { SignIn } from "./screens/SignIn";
import { useRoute } from "./lib/route";
import { RecruiterShell } from "./components/RecruiterShell";
import { AIInterviews } from "./screens/AIInterviews";
import { CreateInterview } from "./screens/CreateInterview";
import { RecommendedInterview } from "./screens/RecommendedInterview";
import { QuestionPoolScreen } from "./screens/QuestionPoolScreen";
import { PublishInterview } from "./screens/PublishInterview";
import { Interviews } from "./screens/Interviews";
import { InterviewWorkspace } from "./screens/InterviewWorkspace";
import { Candidates } from "./screens/Candidates";
import { Compare } from "./screens/Compare";
import { SessionReview } from "./screens/SessionReview";
import { QuestionPool } from "./screens/QuestionPool";
import { Placeholder } from "./screens/Placeholder";

/**
 * The recruiter console.
 *
 * Its own app and its own bundle now, talking to `/api/recruiter`. Nothing here
 * is ever shipped to a candidate's browser, which is the point: the question
 * pool, the evidence, and every transcript live behind this build, not behind an
 * `if` in a shared one.
 *
 * Sign-in gates the console, and it is worth being precise about what that
 * means: the gate decides which SCREEN renders, not what a recruiter may read.
 * Every `/api/recruiter` route authorises itself against the session cookie, so
 * skipping this component — by editing the bundle, or by curling the API —
 * reaches a 401, not a transcript.
 */
export function RecruiterApp() {
  const [route, go] = useRoute();
  const [title, setTitle] = useState<string | null>(null);
  //: `undefined` while the first `/api/auth/me` is in flight, so the console
  //: does not flash a sign-in form at somebody who is already signed in.
  const [session, setSession] = useState<Session | null | undefined>(undefined);

  const refresh = useCallback(() => {
    auth
      .me()
      .then(setSession)
      .catch(() => setSession(null));
  }, []);

  useEffect(refresh, [refresh]);

  // Any request answering 401 means the session ended mid-visit. Re-checking
  // rather than assuming: the one authority on whether there is a session is
  // the server, and a race between two requests must not sign someone out.
  useEffect(() => {
    const onLost = () => setSession((current) => (current === undefined ? current : null));
    window.addEventListener(SESSION_LOST, onLost);
    return () => window.removeEventListener(SESSION_LOST, onLost);
  }, []);

  // The workspace's page title is the interview's own name, which only the
  // workspace knows. Fetch it here so the header doesn't flash a placeholder.
  useEffect(() => {
    if (route.name !== "interview" || !session) {
      setTitle(null);
      return;
    }
    let live = true;
    adminApi
      .interview(route.id)
      .then((iv: Interview) => live && setTitle(iv.title))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [route, session]);

  const meta = header(route.name, title);

  if (session === undefined) {
    return (
      <div className="flex min-h-full items-center justify-center bg-canvas">
        <p className="text-sm text-gray-500">Loading…</p>
      </div>
    );
  }
  if (session === null) {
    return <SignIn onSignedIn={setSession} />;
  }

  return (
    <RecruiterShell
      route={route}
      go={go}
      title={meta.title}
      subtitle={meta.subtitle}
      session={session}
      onSignOut={async () => {
        await auth.logout();
        setSession(null);
      }}
    >
      {route.name === "dashboard" && <AIInterviews />}
      {route.name === "interviews" && <Interviews />}
      {route.name === "interview-new" && <CreateInterview />}
      {route.name === "recommended" && <RecommendedInterview id={route.id} />}
      {route.name === "question-pool" && <QuestionPoolScreen id={route.id} />}
      {route.name === "publish" && <PublishInterview id={route.id} />}
      {route.name === "interview" && <InterviewWorkspace id={route.id} tab={route.tab} />}
      {route.name === "candidates" && <Candidates />}
      {route.name === "compare" && <Compare ids={route.ids} />}
      {route.name === "questions" && <QuestionPool />}
      {route.name === "session" && <SessionReview id={route.id} />}
      {/* `/reports/{session_id}` is the evaluation report on its own — the same
          component the review screen opens on, deep-linkable for sharing. */}
      {route.name === "report" && <SessionReview id={route.id} initialTab="evaluation" />}

      {route.name === "results" && (
        <Placeholder
          title="Results across interviews"
          what={
            "Every completed interview in one place, filterable by role and interview, with the " +
            "skills that consistently come up short across a whole candidate pool."
          }
          backend={
            "Per-interview results, candidate comparison and the fairness audit are built and " +
            "reachable from inside an interview's own workspace. This route is the roll-up across " +
            "all of them."
          }
          phase="Phase 3 — Scoring and results."
        />
      )}

      {/* The reports destination IS the candidate list: every row is a
          candidate and a report, and "Review" opens the evaluation. It used to
          be a placeholder promising a roll-up while the working list sat behind
          a separate nav entry called Candidates — two doors to one room, one of
          which was locked. */}
      {route.name === "reports" && <Candidates />}

    </RecruiterShell>
  );
}

function header(name: string, title: string | null): { title: string; subtitle?: string } {
  switch (name) {
    case "dashboard":
      return {
        title: "AI Interviews",
        subtitle: "Every interview you've designed, and the one button that starts another",
      };
    case "interviews":
      return {
        title: "Interviews",
        subtitle: "Configured from a job description — skills, depth, and the questions behind them",
      };
    case "interview-new":
      return {
        title: "Create AI Interview",
        subtitle: "Tell Tara about the role — it works out what to assess",
      };
    case "publish":
      return {
        title: "Publish & invite",
        subtitle: "Freeze this assessment, then send it to candidates",
      };
    case "question-pool":
      return {
        title: "Question Pool",
        subtitle: "Every question Tara wrote, what it assesses, and how it will be judged",
      };
    case "recommended":
      return {
        title: "Recommended Interview",
        subtitle: "Tara's proposal. Review it, adjust anything, and it saves as you go.",
      };
    case "interview":
      return { title: title ?? "Interview", subtitle: undefined };
    case "compare":
      return {
        title: "Compare candidates",
        subtitle: "Side by side on the skills the role depends on",
      };
    case "candidates":
      return { title: "Candidates", subtitle: "Invite links, progress, and completed interviews" };
    case "reports":
      return {
        title: "AI Interview Reports",
        subtitle: "Every candidate, their progress, and the evaluation behind each completed interview",
      };
    case "questions":
      return {
        title: "Question bank",
        subtitle: "Every authored question for this role — read-only",
      };
    case "results":
      return { title: "Results", subtitle: "Across every interview" };
    case "report":
      return {
        title: "Evaluation report",
        subtitle: "Evidence, depth and the five criteria — as the evaluation engine recorded them",
      };
    case "session":
      return {
        title: "Interview review",
        subtitle: "The evaluation, the evidence behind it, and every decision that produced it",
      };
    default:
      return { title: "Console" };
  }
}
