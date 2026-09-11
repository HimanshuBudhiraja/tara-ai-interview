import { useEffect, useState } from "react";
import { ArrowUpRight, Activity, CircleAlert } from "lucide-react";
import { adminApi, type Candidate, type Overview as OverviewData } from "../lib/adminApi";
import { navigate } from "../lib/route";
import { Button, Callout } from "@tara/ui/primitives";
import { Empty, SectionHeading, Stat, StatusPill, timeAgo } from "../components/RecruiterShell";

export function Overview() {
  const [data, setData] = useState<OverviewData | null>(null);
  const [recent, setRecent] = useState<Candidate[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    adminApi.overview().then(setData).catch((e) => setError(e.message));
    adminApi
      .candidates()
      .then((d) => setRecent(d.candidates.slice(0, 6)))
      .catch(() => undefined);
  }, []);

  if (error) return <Callout tone="error">{error}</Callout>;
  if (!data) return <LoadingRows />;

  return (
    <div className="space-y-6">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Interviews" value={data.interviews} hint={`${data.published} published`} />
        <Stat label="Candidates" value={data.candidates} hint={`${data.not_started} not started`} />
        <Stat label="In progress" value={data.in_progress} hint="Live right now" accent />
        <Stat
          label="Completed"
          value={data.complete}
          hint={data.median_minutes ? `${data.median_minutes} min median` : "Awaiting the first"}
        />
      </div>

      {!data.llm && (
        <Callout
          tone="warning"
          title="Running on the offline model"
          icon={<CircleAlert className="h-4 w-4" />}
        >
          No API key is set, so TARA won't write follow-ups from what candidates say — only the
          authored ones are used, and job descriptions can't be analysed. Interviews still run end
          to end.
        </Callout>
      )}

      <section>
        <SectionHeading
          title="Recent activity"
          action={
            <Button variant="link" size="sm" onClick={() => navigate({ name: "candidates" })}>
              All candidates
              <ArrowUpRight className="h-3.5 w-3.5" />
            </Button>
          }
        />

        {recent.length === 0 ? (
          <Empty title="Nothing has happened yet" icon={<Activity className="h-4 w-4" />}>
            Create an interview from a job description, publish it, and invite someone. Their
            progress shows up here.
          </Empty>
        ) : (
          <div className="card divide-y divide-gray-100 overflow-hidden">
            {recent.map((row) => (
              <button
                key={row.token}
                disabled={!row.session_id}
                onClick={() => row.session_id && navigate({ name: "session", id: row.session_id })}
                className="flex w-full items-center gap-4 px-4 py-3 text-left transition-colors enabled:hover:bg-gray-25 disabled:cursor-default"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate text-base font-medium text-gray-900">
                    {row.candidate_name}
                  </p>
                  <p className="meta mt-0.5">
                    {row.asked ? `${row.answered} of ${row.asked} answered · ` : ""}
                    {timeAgo(row.last_activity)}
                  </p>
                </div>
                <StatusPill status={row.status} />
                {row.session_id && (
                  <span className="text-xs font-semibold text-brand-600">Review</span>
                )}
              </button>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

/** Skeleton rather than a spinner — the layout doesn't jump when data lands. */
function LoadingRows() {
  return (
    <div className="space-y-6">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="card h-[92px] animate-pulse bg-gray-50" />
        ))}
      </div>
      <div className="card h-52 animate-pulse bg-gray-50" />
    </div>
  );
}
