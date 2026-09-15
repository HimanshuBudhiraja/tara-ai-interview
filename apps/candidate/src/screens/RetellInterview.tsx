import { CircleHelp, Square, Wifi } from "lucide-react";
import { useRetellCall } from "../hooks/useRetellCall";
import { TaraOrb } from "../components/TaraOrb";
import { ProgressRail } from "../components/ProgressRail";
import { Button, Callout, Logo, Spinner } from "../components/Shell";
import { cn } from "../lib/cn";

/**
 * The interview when Retell carries the voice.
 *
 * Intentionally the same screen as the browser-speech one, to the pixel: the
 * orb, the status line, the rail, the mic-feedback line. The candidate must
 * not be able to tell which transport they got, because the transport is not
 * something the assessment depends on — and a candidate noticing they are on
 * "the other one" would reasonably wonder whether they were being assessed
 * differently. They are not.
 *
 * What is absent is the same as there: no question text, no follow-up text, no
 * typed answer box. Spoken interviews are spoken.
 *
 * What differs under the surface is only who runs the turn loop. Here Retell
 * does, against our server, so this screen has no "I'm done answering" button:
 * end-of-turn is Retell's judgement (tuned on the agent), not a thing the
 * browser can assert. Offering a button that did nothing would be worse than
 * not offering one.
 */
export function RetellInterview({
  sessionId,
  onComplete,
}: {
  sessionId: string;
  onComplete: () => void;
}) {
  const { phase, level, heard, progress, error, end } = useRetellCall({
    sessionId,
    onEnded: onComplete,
  });

  const status =
    phase === "connecting"
      ? "Connecting you to Tara…"
      : phase === "speaking"
        ? "Tara is speaking"
        : phase === "listening"
          ? "Listening"
          : phase === "ended"
            ? "That's everything"
            : "The call stopped";

  return (
    <div className="flex min-h-full flex-col bg-canvas">
      <header className="border-b border-gray-200 bg-surface">
        <div className="mx-auto flex h-14 max-w-[1200px] items-center justify-between px-6">
          <Logo />
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="sm" onClick={() => undefined}>
              <CircleHelp className="h-4 w-4" />
              Help
            </Button>
            <Button variant="danger" size="sm" onClick={end} disabled={phase === "ended"}>
              <Square className="h-3 w-3" fill="currentColor" strokeWidth={0} />
              End interview
            </Button>
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-[1200px] flex-1 px-6 py-6">
        {error && (
          <div className="mb-5">
            <Callout tone="error" title="Something's not right">
              {error}
            </Callout>
          </div>
        )}
        {phase === "connecting" && (
          <div className="mb-5">
            <Callout tone="info" title="Connecting">
              <span className="inline-flex items-center gap-2">
                <Wifi className="h-4 w-4" />
                Allow the microphone when your browser asks. Tara will say hello as soon
                as you're connected.
              </span>
            </Callout>
          </div>
        )}

        <div className="grid gap-5 lg:grid-cols-[1fr_288px]">
          <section className="card flex min-h-[560px] flex-col p-6 sm:p-7">
            <div className="flex flex-1 flex-col items-center justify-center">
              <TaraOrb
                phase={phase === "speaking" ? "speaking" : "listening"}
                level={level}
              />
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
                {phase === "connecting" && (
                  <Spinner className="h-3.5 w-3.5 text-gray-400" />
                )}
              </div>

              {/* The candidate's own last words — device feedback, proof the
                  microphone is reaching Tara. Not a transcript to read back,
                  and it is replaced rather than accumulated. */}
              <p
                className="mt-6 min-h-[3.5rem] max-w-[52ch] text-center text-lg leading-relaxed text-gray-400"
                aria-live="off"
              >
                {heard}
              </p>
            </div>

            <div className="mt-5 border-t border-gray-200 pt-4">
              <p className="text-center text-sm text-gray-500">
                Speak naturally — Tara waits for you to finish, and you can interrupt her
                if you need to.
              </p>
            </div>
          </section>

          <div className="space-y-4">
            {progress && <ProgressRail progress={progress} />}
            <div className="card p-4">
              <h3 className="text-sm font-semibold text-gray-900">If you get stuck</h3>
              <ul className="mt-2.5 space-y-2 text-sm leading-relaxed text-gray-500">
                <li>Say "can you repeat that" — Tara will say it again.</li>
                <li>Say "what do you mean" and she'll put it another way.</li>
                <li>
                  Haven't hit that exact situation? Say so and describe how you'd approach
                  it.
                </li>
              </ul>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}
