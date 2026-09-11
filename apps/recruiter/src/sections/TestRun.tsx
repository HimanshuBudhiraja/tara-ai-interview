import { useState } from "react";
import { ChevronDown, PlayCircle } from "lucide-react";
import { adminApi, type TestRun as TestRunResult } from "../lib/adminApi";
import { Button, Callout } from "@tara/ui/primitives";
import { Section } from "./bits";
import { BandPill } from "../components/score";
import { cn } from "../lib/cn";

/**
 * Try the interview before anyone real does.
 *
 * The preview shows which questions get asked; this shows what actually
 * *happens* — how often TARA probes, how long it runs, and what a score comes
 * out looking like. Those only emerge from running the thing, and the first
 * person to find out shouldn't be a candidate.
 *
 * Two personas, because the interesting property is the gap between them: a
 * configuration where a strong and a weak answer produce similar scores isn't
 * discriminating, and that is visible here in about a minute.
 */
const PERSONAS = [
  { id: "strong", label: "Strong answers", hint: "Substantive and specific — should mostly advance" },
  { id: "thin", label: "Weak answers", hint: "Platitudes — should draw follow-ups on every question" },
] as const;

export function TestRun({ interviewId, disabled }: { interviewId: string; disabled: boolean }) {
  const [persona, setPersona] = useState<string>("strong");
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<TestRunResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showTranscript, setShowTranscript] = useState(false);

  const run = async () => {
    setRunning(true);
    setError(null);
    try {
      setResult(await adminApi.testRun(interviewId, persona));
    } catch (e: any) {
      setError(e.message);
    } finally {
      setRunning(false);
    }
  };

  return (
    <Section
      title="Test run"
      hint="Run the whole interview against a scripted candidate. Nothing is saved and no candidate is created."
    >
      {disabled ? (
        <Callout tone="info">
          Analyse a job description first — there's no interview to run yet.
        </Callout>
      ) : (
        <>
          <div className="flex flex-wrap items-end gap-2">
            <div className="flex overflow-hidden rounded-md border border-gray-300 bg-surface shadow-xs">
              {PERSONAS.map((p, i) => (
                <button
                  key={p.id}
                  onClick={() => setPersona(p.id)}
                  title={p.hint}
                  className={cn(
                    "h-9 px-3 text-sm font-semibold transition-colors",
                    i > 0 && "border-l border-gray-200",
                    persona === p.id
                      ? "bg-gray-100 text-gray-900"
                      : "text-gray-500 hover:bg-gray-50 hover:text-gray-700",
                  )}
                >
                  {p.label}
                </button>
              ))}
            </div>
            <Button variant="secondary" onClick={run} loading={running}>
              {!running && <PlayCircle className="h-4 w-4" />}
              {running ? "Running the interview…" : "Run it"}
            </Button>
            {running && (
              <span className="text-sm text-gray-500">
                About a minute — it runs every turn for real.
              </span>
            )}
          </div>

          {error && (
            <div className="mt-3">
              <Callout tone="error" title="Test run failed">
                {error}
              </Callout>
            </div>
          )}

          {result && (
            <div className="mt-4 space-y-3">
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <Metric label="Questions" value={result.questions_asked} />
                <Metric label="Follow-ups" value={result.follow_ups} />
                <Metric label="Turns" value={result.turns} />
                <Metric
                  label="Level"
                  value={result.score.composite?.toFixed(2) ?? "—"}
                  hint="out of 4"
                />
              </div>

              <div className="flex flex-wrap items-center gap-2 rounded-md border border-gray-200 bg-gray-25 px-3 py-2.5">
                <BandPill band={result.score.band} label={result.score.band_label} />
                <span className="meta">
                  {Math.round(result.score.met_ratio * 100)}% of targets met · confidence{" "}
                  {Math.round(result.score.confidence * 100)}%
                </span>
              </div>

              {result.score.note && <Callout tone="warning">{result.score.note}</Callout>}

              <button
                onClick={() => setShowTranscript((v) => !v)}
                className="inline-flex items-center gap-1.5 text-sm font-medium text-gray-500 transition-colors hover:text-gray-900"
              >
                <ChevronDown
                  className={cn("h-4 w-4 transition-transform", showTranscript && "rotate-180")}
                />
                {showTranscript ? "Hide" : "Show"} the transcript
              </button>

              {showTranscript && (
                <div className="max-h-[420px] space-y-3 overflow-y-auto rounded-md border border-gray-200 p-4">
                  {result.transcript.map((t, i) => (
                    <div key={i}>
                      <p
                        className={cn(
                          "label mb-1",
                          t.speaker === "tara" ? "text-brand-600" : "text-gray-400",
                        )}
                      >
                        {t.speaker === "tara" ? `TARA${t.kind ? ` · ${t.kind}` : ""}` : "Candidate"}
                      </p>
                      <p
                        className={cn(
                          "whitespace-pre-wrap text-sm leading-relaxed",
                          t.speaker === "tara" ? "text-gray-900" : "text-gray-600",
                        )}
                      >
                        {t.text}
                      </p>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </>
      )}
    </Section>
  );
}

function Metric({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  return (
    <div className="rounded-md border border-gray-200 p-3">
      <p className="label">{label}</p>
      <p className="tabular mt-1 text-xl font-semibold leading-none text-gray-900">
        {value}
        {hint && <span className="ml-1 text-xs font-normal text-gray-400">{hint}</span>}
      </p>
    </div>
  );
}
