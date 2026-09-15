import { useEffect } from "react";
import { CircleHelp, Repeat2, Square, Wifi } from "lucide-react";
import * as Dialog from "@radix-ui/react-dialog";
import { useInterview } from "../hooks/useInterview";
import { useMicLevel } from "../hooks/useMicLevel";
import { TaraOrb } from "../components/TaraOrb";
import { ProgressRail } from "../components/ProgressRail";
import { DemoPrompter } from "../components/DemoPrompter";
import { Button, Callout, Logo, Spinner } from "../components/Shell";
import type { Reply } from "../lib/types";
import { cn } from "../lib/cn";

/**
 * The interview itself — a spoken conversation.
 *
 * Layout is deliberately calm and static: the orb and status line answer
 * "whose turn is it", the rail answers "how much is left". Nothing else moves,
 * nothing counts down, and nothing on this screen ever hints at how the
 * candidate is doing.
 *
 * Three things are deliberately NOT here, and their absence is the design:
 *
 * **No question text.** The question is heard, not read. On screen it becomes
 * a reading comprehension test with a different difficulty curve, and it lets
 * a candidate re-read and compose rather than answer — which is a written
 * assessment wearing a voice interview's clothes. "Repeat the question" is the
 * affordance for missing something, and it is the same affordance a person
 * would offer.
 *
 * **No follow-up text.** Same reason, and more so: a probe lands differently
 * when you can see it coming in writing.
 *
 * **No typed answer box.** A text box on screen invites typing, and a typed
 * interview is a materially different assessment from a spoken one. Every
 * word is still recorded — `turns` feeds the session snapshot and the
 * recruiter's report — so the record is unchanged. What changed is that the
 * candidate is having a conversation rather than reading one.
 */
export function Interview({
  sessionId,
  firstReply,
  speechRate,
  onComplete,
}: {
  sessionId: string;
  firstReply?: Reply;
  /** From the published interview — the recruiter's pace for this role. */
  speechRate?: number;
  onComplete: () => void;
}) {
  const {
    phase,
    status,
    currentItem,
    currentKind,
    progress,
    partial,
    error,
    ended,
    usingVoice,
    submitText,
    doneSpeaking,
    askRepeat,
    endInterview,
  } = useInterview({ sessionId, firstReply, speechRate, onEnded: onComplete });

  // A second, independent read of the mic purely so the orb can react to the
  // candidate's voice. The recognizer owns the words; this owns the animation.
  const { level, request, stop } = useMicLevel();
  useEffect(() => {
    if (usingVoice) void request();
    return stop;
  }, [request, stop, usingVoice]);

  const busy = phase === "thinking" || phase === "speaking";
  // Presenter aid, never on for a real candidate — it shows suggested answers.
  const demoMode = new URLSearchParams(location.search).get("demo") === "1";

  return (
    <div className="flex min-h-full flex-col bg-canvas">
      <header className="border-b border-gray-200 bg-surface">
        <div className="mx-auto flex h-14 max-w-[1200px] items-center justify-between px-6">
          <Logo />
          <div className="flex items-center gap-2">
            <HelpDialog />
            <Button variant="danger" size="sm" onClick={endInterview} disabled={ended}>
              <Square className="h-3 w-3" fill="currentColor" strokeWidth={0} />
              End interview
            </Button>
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-[1200px] flex-1 px-6 py-6">
        {phase === "reconnecting" && (
          <div className="mb-5">
            <Callout tone="warning" title="Reconnecting">
              <span className="inline-flex items-center gap-2">
                <Wifi className="h-4 w-4" /> Your connection dropped. Everything you've said is
                saved — we'll pick up from the same question.
              </span>
            </Callout>
          </div>
        )}
        {error && (
          <div className="mb-5">
            <Callout tone="error" title="Something's not right">
              {error}
            </Callout>
          </div>
        )}

        <div className="grid gap-5 lg:grid-cols-[1fr_288px]">
          {/* ---------------- main column ---------------- */}
          <section className="card flex min-h-[560px] flex-col p-6 sm:p-7">
            <div className="flex flex-1 flex-col items-center justify-center">
              <TaraOrb phase={phase} level={level} />
              <div className="mt-4 flex h-6 items-center gap-2">
                <span
                  className={cn(
                    "h-1.5 w-1.5 rounded-full transition-colors",
                    phase === "listening"
                      ? "bg-success-500"
                      : phase === "speaking"
                        ? "bg-brand-500"
                        : "bg-gray-300",
                  )}
                />
                <span
                  className="text-sm font-medium text-gray-600"
                  role="status"
                  aria-live="polite"
                >
                  {status}
                </span>
                {phase === "thinking" && <Spinner className="h-3.5 w-3.5 text-gray-400" />}
              </div>

              {/*
                What the microphone is hearing, right now.
                
                This is the candidate's OWN words, and it is device feedback
                rather than a transcript — the one thing a voice interface has
                to prove is that it can hear you, and a silent orb cannot tell
                the difference between "still listening" and "your microphone
                died". It clears the moment the turn is sent, so it never
                accumulates into something to read back.
              */}
              <p
                className="mt-6 min-h-[3.5rem] max-w-[52ch] text-center text-lg leading-relaxed text-gray-400 transition-opacity"
                aria-live="off"
              >
                {partial}
              </p>
            </div>

            {/* Bottom bar: the two things a candidate can ask for mid-answer. */}
            <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-gray-200 pt-4">
              <button
                onClick={askRepeat}
                disabled={busy || ended}
                className="inline-flex items-center gap-1.5 text-sm font-medium text-gray-500 transition-colors hover:text-gray-900 disabled:opacity-40"
              >
                <Repeat2 className="h-3.5 w-3.5" />
                Repeat the question
              </button>
              <div className="flex items-center gap-4">
                <p className="text-sm text-gray-500">
                  {phase === "listening"
                    ? "Speak naturally. I'll wait for a pause before I answer."
                    : " "}
                </p>
                <Button variant="secondary" onClick={doneSpeaking} disabled={phase !== "listening"}>
                  I'm done answering
                </Button>
              </div>
            </div>
          </section>

          {/* ---------------- rail ---------------- */}
          <div className="space-y-4">
            <ProgressRail progress={progress} />
            <div className="card p-4">
              <h3 className="text-sm font-semibold text-gray-900">If you get stuck</h3>
              <ul className="mt-2.5 space-y-2 text-sm leading-relaxed text-gray-500">
                <li>Say "can you repeat that" — Tara will say it again.</li>
                <li>Say "what do you mean" and she'll put it another way.</li>
                <li>
                  Haven't hit that exact situation? Say so and describe how you'd approach it.
                </li>
              </ul>
            </div>
          </div>
        </div>
      </main>

      {demoMode && (
        <DemoPrompter
          itemId={currentItem}
          kind={currentKind}
          phase={phase}
          usingVoice={usingVoice}
          onSend={submitText}
        />
      )}
    </div>
  );
}

function HelpDialog() {
  return (
    <Dialog.Root>
      <Dialog.Trigger asChild>
        <Button variant="ghost" size="sm">
          <CircleHelp className="h-4 w-4" />
          Help
        </Button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-gray-950/40 animate-fade-in" />
        <Dialog.Content className="fixed left-1/2 top-1/2 w-[min(92vw,480px)] -translate-x-1/2 -translate-y-1/2 rounded-lg border border-gray-200 bg-surface p-5 shadow-xl animate-slide-up">
          <Dialog.Title className="text-xl font-semibold tracking-tight text-gray-900">
            Having trouble?
          </Dialog.Title>
          <Dialog.Description className="mt-1 text-sm text-gray-500">
            None of this counts against you.
          </Dialog.Description>
          <ul className="mt-4 space-y-3 text-sm leading-relaxed text-gray-600">
            <li>
              <strong className="font-semibold text-gray-900">Tara can't hear me.</strong> Check
              the microphone icon in your browser's address bar is allowed. The grey text under
              the orb shows what she's picking up — if it stays empty while you talk, the
              microphone isn't reaching her.
            </li>
            <li>
              <strong className="font-semibold text-gray-900">I missed the question.</strong> Say
              "can you repeat that", or use "Repeat the question" below. Asking twice costs you
              nothing.
            </li>
            <li>
              <strong className="font-semibold text-gray-900">She cut me off.</strong> Keep going —
              just carry on speaking and she'll pick it up. Nothing is lost.
            </li>
            <li>
              <strong className="font-semibold text-gray-900">My connection dropped.</strong> Reopen
              your interview link. You'll resume at the same question.
            </li>
          </ul>
          <div className="mt-5">
            <Dialog.Close asChild>
              <Button>Back to the interview</Button>
            </Dialog.Close>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
