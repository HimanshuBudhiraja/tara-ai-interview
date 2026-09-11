import { useEffect, useState } from "react";
import { ArrowLeft, Info } from "lucide-react";
import { adminApi, type Comparison } from "../lib/adminApi";
import { navigate } from "../lib/route";
import { Badge, Callout } from "@tara/ui/primitives";
import { Empty } from "../components/RecruiterShell";
import { BandPill, LevelChip } from "../components/score";
import { cn } from "../lib/cn";

/**
 * Candidates side by side.
 *
 * Rows are ordered by the recruiter's own priority bands, not by score spread,
 * so the skills the role actually depends on sit at the top where the decision
 * gets made. Sorting by "biggest difference" would put the most dramatic row
 * first, which is a different and worse question than "which of these matters".
 */
export function Compare({ ids }: { ids: string[] }) {
  const [data, setData] = useState<Comparison | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (ids.length < 2) return;
    adminApi.compare(ids).then(setData).catch((e) => setError(e.message));
  }, [ids.join(",")]);

  if (ids.length < 2) {
    return (
      <Empty title="Pick at least two candidates">
        Go to an interview's Results tab, tick two or more candidates, and choose Compare.
      </Empty>
    );
  }
  if (error) return <Callout tone="error">{error}</Callout>;
  if (!data) return <div className="card h-72 animate-pulse bg-gray-50" />;

  return (
    <div className="space-y-4">
      <button
        onClick={() => history.back()}
        className="inline-flex items-center gap-1.5 text-sm font-medium text-gray-500 transition-colors hover:text-gray-900"
      >
        <ArrowLeft className="h-3.5 w-3.5" />
        Back
      </button>

      {data.cross_interview && (
        <Callout tone="warning" title="Different interviews" icon={<Info className="h-4 w-4" />}>
          {data.note}
        </Callout>
      )}

      <div className="card overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-sm">
            <thead>
              <tr className="border-b border-gray-200 bg-gray-25">
                <th className="w-[220px] px-4 py-3 align-bottom">
                  <span className="label">Skill</span>
                </th>
                {data.candidates.map((c) => (
                  <th key={c.session_id} className="px-4 py-3 align-bottom">
                    <button
                      onClick={() => navigate({ name: "session", id: c.session_id })}
                      className="text-left"
                    >
                      <span className="block text-base font-semibold text-gray-900 hover:text-brand-700">
                        {c.name}
                      </span>
                    </button>
                    <span className="tabular mt-1 block text-xs text-gray-500">
                      {c.composite?.toFixed(2) ?? "—"} / 4 · {Math.round(c.met_ratio * 100)}% met
                    </span>
                    <span className="mt-1.5 block">
                      <BandPill band={c.band} label={c.band_label} />
                    </span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.skills.map((s) => (
                <tr key={s.competency_id} className="table-row">
                  <td className="px-4 py-2.5">
                    <span className="block text-base font-medium text-gray-900">{s.name}</span>
                    <span className="mt-0.5 flex items-center gap-1.5">
                      <Badge tone={s.priority === "high" ? "brand" : "neutral"}>{s.priority}</Badge>
                      <span className="meta">needs {s.target}</span>
                    </span>
                  </td>
                  {s.levels.map((l) => {
                    const best =
                      Math.max(...s.levels.map((x) => x.level ?? -1)) === (l.level ?? -1) &&
                      (l.level ?? -1) >= 0 &&
                      s.levels.filter((x) => x.level === l.level).length < s.levels.length;
                    return (
                      <td
                        key={l.session_id}
                        className={cn("px-4 py-2.5", best && "bg-brand-25")}
                      >
                        <LevelChip
                          level={l.level}
                          target={s.target}
                          flagged={l.flagged}
                          assessed={l.assessed}
                        />
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <p className="meta">
        Shaded cells are the strongest on that skill. An asterisk marks a level scored with low
        confidence — read the evidence before leaning on it. A dash means the skill wasn't assessed
        for that candidate.
      </p>
    </div>
  );
}
