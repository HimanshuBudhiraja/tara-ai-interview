import { useEffect, useState } from "react";
import { ChevronDown, Mic, Send, Sparkles } from "lucide-react";
import { cn } from "../lib/cn";
import type { CallPhase, SpeechKind } from "../lib/types";

/**
 * Presenter aid for the live voice demo. Only mounts when the link carries `?demo=1`.
 *
 * A voice demo rarely fails on the technology. It fails because the person
 * holding the microphone freezes in front of an audience, waffles, and gets
 * probed three times on the opening question — which reads as the product being
 * broken when it's the product working correctly on a bad answer.
 *
 * So this is a teleprompter, not a bypass. The presenter reads the line ALOUD:
 * the microphone, the transcription, the orchestrator, and Tara's voice are all
 * the real thing, and what the audience sees is a genuine voice interview. The
 * only thing that's staged is knowing what to say.
 *
 * The "send without speaking" button is the escape hatch for a dead microphone
 * or a noisy room. It routes through the identical turn endpoint, so the demo
 * degrades to a typed interview rather than collapsing.
 *
 * Never rendered without `?demo=1` — a candidate must never see suggested
 * answers to the questions they're being asked.
 */

interface Prompt {
  read: string;
  note?: string;
}

export function DemoPrompter({
  itemId,
  kind,
  phase,
  onSend,
  usingVoice,
}: {
  itemId: string | null;
  kind: SpeechKind | null;
  phase: CallPhase;
  onSend: (text: string) => void;
  usingVoice: boolean;
}) {
  const [answers, setAnswers] = useState<Record<string, Prompt>>({});
  const [probeReplies, setProbeReplies] = useState<string[]>([]);
  const [probeIndex, setProbeIndex] = useState(0);
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    fetch("/api/demo/prompts")
      .then((r) => r.json())
      .then((d) => {
        setAnswers(d.answers ?? {});
        setProbeReplies(d.probe_replies ?? []);
      })
      .catch(() => undefined);
  }, []);

  // Follow-ups get their own rotating replies — reading the original answer
  // again in response to "can you be more specific?" makes the demo look broken.
  useEffect(() => {
    if (kind === "probe") setProbeIndex((i) => i + 1);
  }, [kind, itemId]);

  const isProbe = kind === "probe";
  const line = isProbe
    ? probeReplies[probeIndex % Math.max(1, probeReplies.length)]
    : itemId
      ? answers[itemId]?.read
      : undefined;
  const note = !isProbe && itemId ? answers[itemId]?.note : undefined;

  const ready = phase === "listening";

  return (
    <div className="fixed bottom-4 left-1/2 z-50 w-[min(92vw,720px)] -translate-x-1/2">
      <div className="rounded-xl border border-gray-800 bg-gray-900 text-white shadow-xl">
        <button
          onClick={() => setCollapsed((v) => !v)}
          className="flex w-full items-center gap-2 px-4 py-2.5 text-left"
        >
          <Sparkles className="h-3.5 w-3.5 text-brand-300" />
          <span className="text-xs font-bold uppercase tracking-wider text-brand-300">
            Demo prompter
          </span>
          <span className="text-xs text-gray-400">
            {isProbe ? "follow-up" : "question"} · {phase}
          </span>
          <ChevronDown
            className={cn(
              "ml-auto h-4 w-4 text-gray-500 transition-transform",
              collapsed && "rotate-180",
            )}
          />
        </button>

        {!collapsed && (
          <div className="border-t border-gray-800 px-4 pb-4 pt-3">
            {line ? (
              <>
                <p className="mb-1 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-gray-500">
                  <Mic className="h-3 w-3" />
                  {isProbe
                    ? "Unscripted — answer naturally, or read this"
                    : usingVoice
                      ? "Read this out loud"
                      : "Send this"}
                </p>
                <p className="text-[17px] font-medium leading-relaxed text-white">{line}</p>
                {isProbe && (
                  // Worth saying out loud during a demo: the follow-up was
                  // written from the answer that was just given, so there is
                  // nothing to script it against. That is the product working.
                  <p className="mt-2 text-xs italic text-brand-300">
                    Tara wrote that follow-up from what was just said — it isn&rsquo;t from a script,
                    so this reply is only a safety net.
                  </p>
                )}
                {note && <p className="mt-2 text-xs italic text-brand-300">{note}</p>}
              </>
            ) : (
              <p className="text-sm text-gray-400">
                No scripted line for this turn — answer however you like, it's a real interview.
              </p>
            )}

            <div className="mt-3 flex items-center gap-3 border-t border-gray-800 pt-3">
              <span
                className={cn(
                  "inline-flex items-center gap-1.5 text-xs font-medium",
                  ready ? "text-success-500" : "text-gray-500",
                )}
              >
                <span
                  className={cn(
                    "h-1.5 w-1.5 rounded-full",
                    ready ? "bg-success-500" : "bg-gray-600",
                  )}
                />
                {ready
                  ? usingVoice
                    ? "Mic is open — go ahead"
                    : "Your turn"
                  : phase === "speaking"
                    ? "Tara is speaking"
                    : phase === "thinking"
                      ? "Tara is thinking"
                      : phase}
              </span>
              {line && (
                <button
                  onClick={() => onSend(line)}
                  disabled={!ready}
                  className="ml-auto inline-flex items-center gap-1.5 rounded-md border border-gray-700 px-2.5 py-1.5 text-xs font-semibold text-gray-300 transition-colors hover:bg-gray-800 disabled:opacity-40"
                  title="Escape hatch if the microphone isn't cooperating — same endpoint, no voice"
                >
                  <Send className="h-3 w-3" />
                  Send without speaking
                </button>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
