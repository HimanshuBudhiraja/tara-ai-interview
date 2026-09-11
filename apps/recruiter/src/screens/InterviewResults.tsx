import { useEffect, useState } from "react";
import { GitCompare, TriangleAlert, Users } from "lucide-react";
import { adminApi, type Results } from "../lib/adminApi";
import { navigate } from "../lib/route";
import { Badge, Button, Callout } from "@tara/ui/primitives";
import { Empty, SectionHeading, Stat, timeAgo } from "../components/RecruiterShell";
import { BandPill, LevelBar } from "../components/score";
import { Tick } from "../sections/bits";
import { cn } from "../lib/cn";

/**
 * Results for one interview.
 *
 * The per-skill table is the part that earns its place. A recruiter looking at
 * one candidate can't tell a weak candidate from a badly-set bar; looking at
 * everyone who took it, a skill where nobody clears the target is almost always
 * a target set too high rather than a market with no talent in it. So the table
 * is sorted by average ascending — the calibration problems surface first.
 */
export function InterviewResults({ interviewId }: { interviewId: string }) {
  const [data, setData] = useState<Results | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [picked, setPicked] = useState<string[]>([]);

  useEffect(() => {
    adminApi.results(interviewId).then(setData).catch((e) => setError(e.message));
  }, [interviewId]);

  if (error) return <Callout tone="error">{error}</Callout>;
  if (!data) return <div className="card h-64 animate-pulse bg-gray-50" />;

  const { funnel } = data;

  if (funnel.completed === 0) {
    return (
      <Empty title="No completed interviews yet" icon={<Users className="h-4 w-4" />}>
        {funnel.invited > 0
          ? `${funnel.invited} invited, ${funnel.started} started. Results appear here as candidates finish.`
          : "Invite a candidate and their results will appear here."}
      </Empty>
    );
  }

  const toggle = (id: string) =>
    setPicked((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));

  return (
    <div className="space-y-5">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          label="Completed"
          value={funnel.completed}
          hint={`of ${funnel.invited} invited · ${Math.round(data.completion_rate * 100)}%`}
        />
        <Stat
          label="Average level"
          value={data.average_composite != null ? data.average_composite.toFixed(2) : "—"}
          hint="out of 4, priority-weighted"
          accent
        />
        <Stat
          label="Median duration"
          value={data.median_minutes != null ? `${data.median_minutes}m` : "—"}
        />
        <Stat
          label="Low confidence"
          value={data.flagged}
          hint={data.flagged ? "read the evidence before relying on these" : "none flagged"}
        />
      </div>

      {/* Band distribution — a stacked bar, not a pie. Proportions at a glance. */}
      <section className="card p-4">
        <SectionHeading title="Distribution" hint="Where this cohort landed." />
        <div className="flex h-2.5 overflow-hidden rounded-full bg-gray-100">
          {data.band_order.map((band) => {
            const n = data.bands[band] ?? 0;
            if (!n) return null;
            return (
              <div
                key={band}
                className={cn("h-full", BAND_FILL[band] ?? "bg-gray-300")}
                style={{ width: `${(n / data.scored) * 100}%` }}
                title={`${data.band_labels[band]}: ${n}`}
              />
            );
          })}
        </div>
        <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1.5">
          {data.band_order.map((band) => (
            <span key={band} className="flex items-center gap-1.5 text-xs">
              <span className={cn("h-2 w-2 rounded-full", BAND_FILL[band] ?? "bg-gray-300")} />
              <span className="text-gray-600">{data.band_labels[band]}</span>
              <span className="tabular font-semibold text-gray-900">{data.bands[band] ?? 0}</span>
            </span>
          ))}
        </div>
      </section>

      {/* Per-skill calibration */}
      <section className="card overflow-hidden">
        <div className="border-b border-gray-200 px-4 py-3">
          <h2 className="text-md font-semibold text-gray-900">How the cohort did, by skill</h2>
          <p className="meta mt-0.5">
            Weakest first. A skill nobody clears usually means the required level is set too high,
            not that the candidates were weak.
          </p>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[620px] text-left text-sm">
            <thead className="table-head">
              <tr>
                <th className="px-4 py-2 font-semibold">Skill</th>
                <th className="px-4 py-2 font-semibold">Priority</th>
                <th className="px-4 py-2 font-semibold">Average vs required</th>
                <th className="px-4 py-2 font-semibold">Cleared</th>
              </tr>
            </thead>
            <tbody>
              {data.skills.map((s) => (
                <tr key={s.competency_id} className="table-row">
                  <td className="px-4 py-2.5 font-medium text-gray-900">{s.name}</td>
                  <td className="px-4 py-2.5">
                    <Badge tone={s.priority === "high" ? "brand" : "neutral"}>{s.priority}</Badge>
                  </td>
                  <td className="px-4 py-2.5">
                    <LevelBar level={s.average} target={s.target} />
                  </td>
                  <td className="tabular px-4 py-2.5">
                    <span
                      className={cn(
                        "font-semibold",
                        s.met_rate === 0
                          ? "text-error-700"
                          : s.met_rate < 0.5
                            ? "text-warning-700"
                            : "text-gray-700",
                      )}
                    >
                      {Math.round(s.met_rate * 100)}%
                    </span>
                    <span className="ml-1 text-gray-400">of {s.candidates}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* Candidates, ranked */}
      <section className="card overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-200 px-4 py-3">
          <div>
            <h2 className="text-md font-semibold text-gray-900">Candidates</h2>
            <p className="meta mt-0.5">
              Ranked by demonstrated level. This informs the decision — it doesn't make it.
            </p>
          </div>
          <Button
            size="sm"
            variant="secondary"
            disabled={picked.length < 2}
            onClick={() => navigate({ name: "compare", ids: picked })}
          >
            <GitCompare className="h-3.5 w-3.5" />
            Compare {picked.length > 1 ? `(${picked.length})` : ""}
          </Button>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[680px] text-left text-sm">
            <thead className="table-head">
              <tr>
                <th className="w-9 px-4 py-2" />
                <th className="px-4 py-2 font-semibold">Candidate</th>
                <th className="px-4 py-2 font-semibold">Level</th>
                <th className="px-4 py-2 font-semibold">Indication</th>
                <th className="px-4 py-2 font-semibold">Targets met</th>
                <th className="px-4 py-2 font-semibold">Confidence</th>
                <th className="px-4 py-2" />
              </tr>
            </thead>
            <tbody>
              {data.candidates.map((c) => (
                <tr key={c.session_id} className="table-row">
                  <td className="px-4 py-2.5">
                    <Tick checked={picked.includes(c.session_id)} onChange={() => toggle(c.session_id)} />
                  </td>
                  <td className="px-4 py-2.5 font-medium text-gray-900">{c.name}</td>
                  <td className="tabular px-4 py-2.5 font-semibold text-gray-900">
                    {c.composite?.toFixed(2) ?? "—"}
                    <span className="ml-1 font-normal text-gray-400">/ 4</span>
                  </td>
                  <td className="px-4 py-2.5">
                    <BandPill band={c.band} label={c.band_label} />
                  </td>
                  <td className="tabular px-4 py-2.5 text-gray-700">
                    {Math.round(c.met_ratio * 100)}%
                  </td>
                  <td className="px-4 py-2.5">
                    {c.flagged ? (
                      <span className="inline-flex items-center gap-1 text-xs font-semibold text-warning-700">
                        <TriangleAlert className="h-3 w-3" />
                        Low
                      </span>
                    ) : (
                      <span className="tabular text-xs text-gray-500">
                        {Math.round(c.confidence * 100)}%
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    <button
                      onClick={() => navigate({ name: "session", id: c.session_id })}
                      className="text-xs font-semibold text-brand-600 transition-colors hover:text-brand-700"
                    >
                      Evidence
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <p className="meta">
        Levels are derived from which authored evidence cues each answer covered. Nothing here reads
        accent, tone, fluency, or answer length — see the Fairness tab for the full list and the
        mechanism behind each exclusion. Last updated {timeAgo(Date.now() / 1000)}.
      </p>
    </div>
  );
}

export const BAND_FILL: Record<string, string> = {
  strong_hire: "bg-success-600",
  hire: "bg-success-500",
  borderline: "bg-warning-500",
  no_hire: "bg-gray-400",
  not_scored: "bg-gray-300",
};
