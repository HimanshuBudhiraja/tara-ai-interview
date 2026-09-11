import { useEffect, useMemo, useState } from "react";
import { ClipboardList, Plus, Search } from "lucide-react";
import { adminApi, type Interview } from "../lib/adminApi";
import { navigate } from "../lib/route";
import { Button, Callout, Spinner } from "@tara/ui/primitives";
import { DifficultyPill, Empty, StatusPill, timeAgo } from "../components/RecruiterShell";
import { cn } from "../lib/cn";

/**
 * The recruiter's landing page: every interview they have, and the one button
 * that starts a new one.
 *
 * Search runs server-side. The list is the recruiter's whole working set and
 * will outgrow a page long before it outgrows a query, so filtering in the
 * browser would only work until it mattered.
 */
export function AIInterviews() {
  const [rows, setRows] = useState<Interview[] | null>(null);
  const [total, setTotal] = useState(0);
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);

  // Debounced so a typed word is one request rather than one per keystroke.
  useEffect(() => {
    let live = true;
    const timer = setTimeout(() => {
      adminApi
        .interviews(query.trim() || undefined)
        .then((data) => {
          if (!live) return;
          setRows(data.interviews);
          setTotal(data.total);
          setError(null);
        })
        .catch((err: Error) => live && setError(err.message));
    }, query ? 220 : 0);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [query]);

  const searching = query.trim().length > 0;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <label className="relative flex-1 min-w-[240px] max-w-md">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search by job title"
            aria-label="Search by job title"
            className="h-9 w-full rounded-md border border-gray-300 bg-surface pl-8 pr-3 text-base text-gray-900 shadow-xs placeholder:text-gray-400 focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-100"
          />
        </label>
        <Button onClick={() => navigate({ name: "interview-new" })}>
          <Plus className="h-4 w-4" />
          Create AI Interview
        </Button>
      </div>

      {error && <Callout tone="error" title="Couldn't load your interviews">{error}</Callout>}

      {rows === null && !error && (
        <div className="card grid place-items-center py-16 text-gray-400">
          <Spinner />
        </div>
      )}

      {rows?.length === 0 && (
        <Empty
          icon={<ClipboardList className="h-4 w-4" />}
          title={searching ? "Nothing matches that" : "No interviews yet"}
          action={
            searching ? (
              <Button variant="secondary" onClick={() => setQuery("")}>
                Clear search
              </Button>
            ) : (
              <Button onClick={() => navigate({ name: "interview-new" })}>
                <Plus className="h-4 w-4" />
                Create AI Interview
              </Button>
            )
          }
        >
          {searching
            ? `No job title matches “${query.trim()}”. There ${total === 1 ? "is" : "are"} ${total} interview${total === 1 ? "" : "s"} in total.`
            : "Paste a job description and Tara works out the skills, the tasks behind them, and how long the interview should run."}
        </Empty>
      )}

      {rows && rows.length > 0 && (
        <div className="card overflow-hidden">
          <table className="w-full text-left">
            <thead>
              <tr className="border-b border-gray-200 bg-gray-25 text-2xs uppercase tracking-wide text-gray-500">
                <th className="px-4 py-2.5 font-semibold">Job title</th>
                <th className="px-4 py-2.5 font-semibold">Language</th>
                <th className="px-4 py-2.5 font-semibold">Assessment</th>
                <th className="px-4 py-2.5 text-right font-semibold">Invited</th>
                <th className="px-4 py-2.5 text-right font-semibold">Completed</th>
                <th className="px-4 py-2.5 font-semibold">Status</th>
                <th className="px-4 py-2.5" />
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <InterviewRow key={row.id} row={row} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function InterviewRow({ row }: { row: Interview }) {
  const open = () =>
    row.designed || row.skill_count
      ? navigate({ name: "recommended", id: row.id })
      : navigate({ name: "interview", id: row.id, tab: "configure" });

  const assessment = useMemo(() => {
    if (!row.skill_count) return null;
    return `${row.interview_type} · ${row.recommended_duration_min} min`;
  }, [row]);

  return (
    <tr
      onClick={open}
      className="cursor-pointer border-b border-gray-100 text-base last:border-0 hover:bg-gray-25"
    >
      <td className="px-4 py-3">
        <div className="font-medium text-gray-900">{row.role_title || row.title}</div>
        <div className="mt-0.5 text-xs text-gray-500">
          {row.skill_count
            ? `${row.skill_count} skills · ${row.high_priority_count} high priority · ${row.task_count} tasks`
            : "Not designed yet"}
          {" · "}
          {timeAgo(row.updated_at)}
        </div>
      </td>
      <td className="px-4 py-3 text-gray-600">{row.language_label}</td>
      <td className="px-4 py-3">
        {assessment ? (
          <span className="flex items-center gap-1.5 capitalize text-gray-600">
            {assessment}
            <DifficultyPill level={row.difficulty} />
          </span>
        ) : (
          <span className="text-gray-400">—</span>
        )}
      </td>
      <td className="tabular px-4 py-3 text-right text-gray-700">{row.candidates.total}</td>
      <td className="tabular px-4 py-3 text-right text-gray-700">{row.candidates.complete}</td>
      <td className="px-4 py-3">
        <StatusPill status={row.status} />
      </td>
      <td className={cn("px-4 py-3 text-right text-sm font-medium text-brand-600")}>
        {row.designed || row.skill_count ? "Review" : "Open"}
      </td>
    </tr>
  );
}
