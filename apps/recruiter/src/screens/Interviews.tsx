import { useEffect, useState } from "react";
import { ChevronRight, ClipboardList, Plus, TriangleAlert } from "lucide-react";
import { adminApi, type Interview } from "../lib/adminApi";
import { navigate } from "../lib/route";
import { Button, Callout } from "@tara/ui/primitives";
import { Empty, StatusPill, timeAgo } from "../components/RecruiterShell";

export function Interviews() {
  const [rows, setRows] = useState<Interview[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    adminApi
      .interviews()
      .then((d) => setRows(d.interviews))
      .catch((e) => setError(e.message));
  }, []);

  const create = async () => {
    setCreating(true);
    try {
      const iv = await adminApi.createInterview("Untitled interview");
      navigate({ name: "interview", id: iv.id, tab: "configure" });
    } catch (e: any) {
      setError(e.message);
      setCreating(false);
    }
  };

  return (
    <div className="space-y-4">
      {error && <Callout tone="error">{error}</Callout>}

      <div className="flex justify-end">
        <Button onClick={create} loading={creating}>
          {!creating && <Plus className="h-4 w-4" />}
          New interview
        </Button>
      </div>

      {rows === null && <div className="card h-40 animate-pulse bg-gray-50" />}

      {rows?.length === 0 && (
        <Empty
          title="No interviews yet"
          icon={<ClipboardList className="h-4 w-4" />}
          action={
            <Button onClick={create} loading={creating}>
              {!creating && <Plus className="h-4 w-4" />}
              New interview
            </Button>
          }
        >
          Start from a job description. TARA works out what the role achieves, the tasks behind it,
          and the skills worth interviewing — you adjust it, publish, and send candidates a link.
        </Empty>
      )}

      {rows && rows.length > 0 && (
        <div className="card divide-y divide-gray-100 overflow-hidden">
          {rows.map((iv) => (
            <button
              key={iv.id}
              onClick={() => navigate({ name: "interview", id: iv.id, tab: "configure" })}
              className="group flex w-full items-center gap-5 px-4 py-3.5 text-left transition-colors hover:bg-gray-25"
            >
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="truncate text-md font-semibold text-gray-900">{iv.title}</h3>
                  <StatusPill status={iv.status} />
                </div>
                <p className="meta mt-1 truncate">
                  {iv.role_title || iv.pool_role_title}
                  {iv.experience_to > 0 && ` · ${iv.experience_from}–${iv.experience_to} yrs`}
                  {" · "}
                  {iv.extracted
                    ? `${iv.covered_count} skill${iv.covered_count === 1 ? "" : "s"} assessed`
                    : "no job description yet"}
                  {` · ${iv.question_budget} questions · updated ${timeAgo(iv.updated_at)}`}
                </p>
                {iv.uncovered_count > 0 && (
                  <p className="mt-1 inline-flex items-center gap-1 text-xs text-warning-700">
                    <TriangleAlert className="h-3 w-3" />
                    {iv.uncovered_count} evaluated skill{iv.uncovered_count === 1 ? "" : "s"} with
                    no questions behind {iv.uncovered_count === 1 ? "it" : "them"}
                  </p>
                )}
              </div>

              <div className="hidden shrink-0 items-center divide-x divide-gray-200 sm:flex">
                <Count value={iv.candidates.total} label="Invited" />
                <Count value={iv.candidates.in_progress} label="Active" />
                <Count value={iv.candidates.complete} label="Done" />
              </div>

              <ChevronRight className="h-4 w-4 shrink-0 text-gray-300 transition-colors group-hover:text-gray-500" />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function Count({ value, label }: { value: number; label: string }) {
  return (
    <div className="px-4 text-center first:pl-0 last:pr-0">
      <p className="tabular text-base font-semibold text-gray-900">{value}</p>
      <p className="label mt-0.5">{label}</p>
    </div>
  );
}
