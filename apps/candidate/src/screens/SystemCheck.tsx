import { useEffect, useState } from "react";
import { Mic, MicOff, Volume2 } from "lucide-react";
import { useMicLevel } from "../hooks/useMicLevel";
import { Badge, Button, Callout, Shell } from "../components/Shell";
import { cn } from "../lib/cn";

/**
 * Prove the hardware works before the interview depends on it.
 *
 * Discovering a dead microphone in question three is the single worst thing
 * that can happen to a voice interview — the candidate spends the rest of it
 * rattled, and the transcript is unusable. So: ask for the mic, show the level
 * moving, play a line back through the speakers, and only then let them in.
 *
 * There is no way past this screen, and that is a change from when the
 * interview had a typed fallback: a spoken interview with a dead microphone is
 * not a degraded interview, it is no interview. So the check is a gate, and the
 * remedies below are real ones (grant the permission, pick a different device,
 * reopen in another browser) rather than an offer to assess something else.
 *
 * Reached only when the browser supports speech at all — `App` turns that away
 * at the welcome screen, where it can explain itself without a half-run check.
 */
export function SystemCheck({ onReady }: { onReady: () => void }) {
  const { status, level, heardSomething, request, stop } = useMicLevel();
  const [speakerTested, setSpeakerTested] = useState(false);

  useEffect(() => {
    void request();
    return stop;
  }, [request, stop]);

  const testSpeaker = () => {
    const u = new SpeechSynthesisUtterance(
      "Hello — this is Tara. If you can hear me clearly, you're all set.",
    );
    u.rate = 0.98;
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(u);
    setSpeakerTested(true);
  };

  const canStart = status === "ready" && heardSomething && speakerTested;

  return (
    <Shell>
      <div className="animate-slide-up">
        <h1 className="text-3xl font-semibold tracking-tight text-gray-900">
          Let's check your setup
        </h1>
        <p className="mt-1.5 text-lg text-gray-600">
          Two quick checks so nothing gets in the way once we start.
        </p>

        {/* --- Microphone --- */}
        <section className="card mt-6 p-5">
          <div className="flex items-start justify-between gap-4">
            <div className="flex items-start gap-3">
              <span
                className={cn(
                  "mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-md border",
                  heardSomething
                    ? "border-success-200 bg-success-50 text-success-600"
                    : "border-gray-200 bg-gray-50 text-gray-500",
                )}
              >
                {status === "denied" ? <MicOff className="h-4 w-4" /> : <Mic className="h-4 w-4" />}
              </span>
              <div>
                <h2 className="text-md font-semibold text-gray-900">Microphone</h2>
                <p className="mt-0.5 text-sm text-gray-500">
                  {status === "requesting" && "Waiting for permission…"}
                  {status === "ready" &&
                    (heardSomething
                      ? "We can hear you clearly."
                      : "Say something — anything — so we can see the level move.")}
                  {status === "denied" && "Access was blocked."}
                  {status === "unavailable" && "No microphone was found on this device."}
                  {status === "idle" && "Getting ready…"}
                </p>
              </div>
            </div>
            {heardSomething && (
              <Badge tone="success" dot>
                Working
              </Badge>
            )}
          </div>

          {/* A bar rather than a number: the point is to see it move. */}
          <div className="mt-5 flex h-8 items-center gap-[3px]" aria-hidden>
            {Array.from({ length: 48 }).map((_, i) => {
              const threshold = i / 48;
              const lit = level > threshold;
              return (
                <span
                  key={i}
                  className={cn(
                    "flex-1 rounded-full transition-all duration-75",
                    lit ? "bg-brand-500" : "bg-gray-200",
                  )}
                  style={{ height: lit ? `${12 + threshold * 18}px` : "4px" }}
                />
              );
            })}
          </div>

          {status === "denied" && (
            <div className="mt-4">
              <Callout tone="error" title="Microphone blocked">
                Click the padlock or camera icon in your browser's address bar, allow the
                microphone, then reload this page. If you'd rather not, you can type your answers
                instead.
              </Callout>
              <Button variant="secondary" className="mt-3" onClick={() => void request()}>
                Try again
              </Button>
            </div>
          )}
        </section>

        {/* --- Speakers --- */}
        <section className="card mt-3 p-5">
          <div className="flex items-start justify-between gap-4">
            <div className="flex items-start gap-3">
              <span
                className={cn(
                  "mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-md border",
                  speakerTested
                    ? "border-success-200 bg-success-50 text-success-600"
                    : "border-gray-200 bg-gray-50 text-gray-500",
                )}
              >
                <Volume2 className="h-4 w-4" />
              </span>
              <div>
                <h2 className="text-md font-semibold text-gray-900">Speakers</h2>
                <p className="mt-0.5 text-sm text-gray-500">
                  {speakerTested
                    ? "If you heard Tara, you're set. Play it again if you'd like."
                    : "Play a test line and make sure you can hear it."}
                </p>
              </div>
            </div>
            <Button variant="secondary" onClick={testSpeaker} className="shrink-0">
              {speakerTested ? "Play again" : "Play test"}
            </Button>
          </div>
        </section>

        <div className="mt-6">
          <Button size="lg" onClick={onReady} disabled={!canStart}>
            Start the interview
          </Button>
        </div>
        {!canStart && status !== "denied" && (
          <p className="mt-3 text-sm text-gray-400">
            {!heardSomething
              ? "Say a few words so we know the microphone is live."
              : "Play the speaker test to finish the check."}
          </p>
        )}
      </div>
    </Shell>
  );
}
