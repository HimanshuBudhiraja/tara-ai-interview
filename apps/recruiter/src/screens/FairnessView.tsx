import { useEffect, useState } from "react";
import { ShieldCheck, ShieldX, TriangleAlert } from "lucide-react";
import { adminApi, type Fairness } from "../lib/adminApi";
import { Callout } from "@tara/ui/primitives";
import { SectionHeading, Stat } from "../components/RecruiterShell";

/**
 * The bias-audit view.
 *
 * Its job is to be answerable, not reassuring. Every exclusion listed here names
 * the mechanism that enforces it, because "we don't score accent" is a claim and
 * "the audio never reaches the assessment engine" is a fact someone can check in
 * the code. A fairness page full of promises is worse than no page at all — it
 * invites trust it hasn't earned.
 *
 * The blocked-probe list is the uncomfortable half, and it stays. It shows the
 * questions the model tried to ask and the guardrails stopped, which is the
 * evidence that the guardrails do something.
 */
export function FairnessView({ interviewId }: { interviewId?: string }) {
  const [data, setData] = useState<Fairness | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    adminApi.fairness(interviewId).then(setData).catch((e) => setError(e.message));
  }, [interviewId]);

  if (error) return <Callout tone="error">{error}</Callout>;
  if (!data) return <div className="card h-64 animate-pulse bg-gray-50" />;

  return (
    <div className="space-y-5">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Sessions audited" value={data.sessions_audited} />
        <Stat
          label="Follow-ups written"
          value={data.generated_probes}
          hint={`${data.fallback_probes} fell back to authored`}
        />
        <Stat
          label="Blocked by guardrails"
          value={data.blocked_probes}
          hint={`${(data.block_rate * 100).toFixed(1)}% of generated`}
          accent={data.blocked_probes > 0}
        />
        <Stat
          label="Low-confidence scores"
          value={data.low_confidence_scores}
          hint={`flagged below ${Math.round(data.confidence_threshold * 100)}%`}
        />
      </div>

      {/* --- exclusions --- */}
      <section className="card overflow-hidden">
        <div className="border-b border-gray-200 px-4 py-3">
          <h2 className="flex items-center gap-2 text-md font-semibold text-gray-900">
            <ShieldCheck className="h-4 w-4 text-success-600" />
            Excluded from assessment by design
          </h2>
          <p className="meta mt-0.5">
            Each row names the mechanism, not the intention. These are checkable in the code rather
            than promises on a page.
          </p>
        </div>
        <ul className="divide-y divide-gray-100">
          {data.exclusions.map((e) => (
            <li key={e.factor} className="grid gap-1 px-4 py-2.5 sm:grid-cols-[200px_1fr] sm:gap-4">
              <span className="text-sm font-semibold text-gray-900">{e.factor}</span>
              <span className="text-sm leading-relaxed text-gray-600">{e.mechanism}</span>
            </li>
          ))}
        </ul>
      </section>

      {/* --- what the guardrails caught --- */}
      <section>
        <SectionHeading
          title="Questions the guardrails stopped"
          hint="Follow-ups the model wrote that were never spoken to a candidate. Each was replaced with an authored question."
        />
        {data.blocked.length === 0 ? (
          <div className="card px-4 py-8 text-center">
            <p className="text-sm text-gray-500">
              Nothing blocked across {data.sessions_audited} session
              {data.sessions_audited === 1 ? "" : "s"}.
            </p>
            <p className="meta mt-1">
              {data.generated_probes > 0
                ? `All ${data.generated_probes} generated follow-ups passed format, legality, and relevance.`
                : "No follow-ups have been generated yet."}
            </p>
          </div>
        ) : (
          <div className="card divide-y divide-gray-100 overflow-hidden">
            {data.blocked.map((b, i) => (
              <div key={i} className="px-4 py-3">
                <div className="mb-1.5 flex flex-wrap items-center gap-2">
                  <span className="inline-flex items-center gap-1 rounded-sm border border-error-200 bg-error-50 px-1.5 py-0.5 text-2xs font-semibold text-error-700">
                    <ShieldX className="h-3 w-3" />
                    {b.gate}
                  </span>
                  <span className="meta">{b.reason}</span>
                  <span className="meta ml-auto">{b.candidate}</span>
                </div>
                <p className="text-sm italic leading-relaxed text-gray-600">"{b.probe}"</p>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* --- accommodations & recovery --- */}
      <div className="grid gap-4 sm:grid-cols-2">
        <section className="card p-4">
          <h3 className="text-sm font-semibold text-gray-900">Adjustments used</h3>
          <p className="meta mt-0.5">
            Offered to every candidate on the welcome screen, with no reason required.
          </p>
          {Object.keys(data.accommodations).length === 0 ? (
            <p className="mt-3 text-sm text-gray-400">None requested yet.</p>
          ) : (
            <ul className="mt-3 space-y-1.5">
              {Object.entries(data.accommodations).map(([key, n]) => (
                <li key={key} className="flex items-center justify-between text-sm">
                  <span className="capitalize text-gray-700">{key.replace(/_/g, " ")}</span>
                  <span className="tabular font-semibold text-gray-900">{n}</span>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="card p-4">
          <h3 className="text-sm font-semibold text-gray-900">Where candidates were helped</h3>
          <p className="meta mt-0.5">
            Recovery, not penalty — none of these count against anyone.
          </p>
          <ul className="mt-3 space-y-1.5 text-sm">
            <Row label="Questions repeated" value={data.questions_repeated} />
            <Row label="Microphone help given" value={data.device_help_offered} />
            <Row
              label="Items left unanswered"
              value={data.unanswered_items}
              hint="excluded from scoring"
            />
          </ul>
        </section>
      </div>

      {data.low_confidence_scores > 0 && (
        <Callout
          tone="warning"
          title="Some scores are flagged"
          icon={<TriangleAlert className="h-4 w-4" />}
        >
          {data.low_confidence_scores} candidate
          {data.low_confidence_scores === 1 ? " has a score" : "s have scores"} below the confidence
          threshold. Short answers or thin question coverage make a level unreliable — read the
          evidence rather than the number.
        </Callout>
      )}
    </div>
  );
}

function Row({ label, value, hint }: { label: string; value: number; hint?: string }) {
  return (
    <li className="flex items-center justify-between">
      <span className="text-gray-700">
        {label}
        {hint && <span className="meta ml-1.5">({hint})</span>}
      </span>
      <span className="tabular font-semibold text-gray-900">{value}</span>
    </li>
  );
}
