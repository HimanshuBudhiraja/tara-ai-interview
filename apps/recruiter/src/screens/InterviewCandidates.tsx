import { useEffect, useState } from "react";
import { Copy, Link2, Plus, UserPlus } from "lucide-react";
import { adminApi, type Candidate, type Interview } from "../lib/adminApi";
import { navigate } from "../lib/route";
import { Button, Callout } from "@tara/ui/primitives";
import { Empty, StatusPill, mmss, timeAgo } from "../components/RecruiterShell";

/**
 * The candidates for one interview.
 *
 * Same table as the global list, scoped — which is the point of the workspace.
 * A recruiter running three roles wants "who is in the CSR pipeline", not one
 * undifferentiated list of everyone the company has ever invited.
 */
export function InterviewCandidates({ interviewId }: { interviewId: string }) {
  const [rows, setRows] = useState<Candidate[] | null>(null);
  const [iv, setIv] = useState<Interview | null>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [inviting, setInviting] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);

  const load = () =>
    Promise.all([adminApi.candidates(interviewId), adminApi.interview(interviewId)])
      .then(([c, i]) => {
        setRows(c.candidates);
        setIv(i);
      })
      .catch((e) => setError(e.message));

  useEffect(() => {
    void load();
  }, [interviewId]);

  const invite = async () => {
    if (!name.trim()) return;
    setInviting(true);
    try {
      await adminApi.invite(name.trim(), interviewId);
      setName("");
      setError(null);
      await load();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setInviting(false);
    }
  };

  const copy = async (row: Candidate) => {
    await navigator.clipboard.writeText(`${location.origin}${row.link}`);
    setCopied(row.token);
    window.setTimeout(() => setCopied(null), 1600);
  };

  return (
    <div className="space-y-4">
      {error && <Callout tone="error">{error}</Callout>}

      <section className="card p-4">
        <h2 className="flex items-center gap-2 text-md font-semibold text-gray-900">
          <UserPlus className="h-4 w-4 text-gray-400" />
          Invite a candidate
        </h2>
        <p className="meta mt-0.5">
          The link carries their name and this interview. They don't sign up, and they can't open
          an interview they weren't invited to.
        </p>

        {iv && iv.status !== "published" ? (
          <div className="mt-3">
            <Callout tone="warning" title="Publish it first">
              A draft can still change underneath a candidate who's already started. Publish this
              interview on the Configure tab, then invite.
            </Callout>
          </div>
        ) : (
          <div className="mt-3 flex flex-wrap items-end gap-2">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && invite()}
              placeholder="Candidate name"
              className="field min-w-[240px] flex-1"
            />
            <Button onClick={invite} disabled={!name.trim()} loading={inviting}>
              {!inviting && <Plus className="h-4 w-4" />}
              Create link
            </Button>
          </div>
        )}
      </section>

      {rows === null && <div className="card h-40 animate-pulse bg-gray-50" />}
      {rows?.length === 0 && (
        <Empty title="Nobody invited to this interview yet" icon={<UserPlus className="h-4 w-4" />}>
          Create a link above and send it over. Progress shows up here as they go.
        </Empty>
      )}

      {rows && rows.length > 0 && (
        <div className="card overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[680px] text-left text-sm">
              <thead className="table-head">
                <tr>
                  <th className="px-4 py-2 font-semibold">Candidate</th>
                  <th className="px-4 py-2 font-semibold">Status</th>
                  <th className="px-4 py-2 font-semibold">Progress</th>
                  <th className="px-4 py-2 font-semibold">Duration</th>
                  <th className="px-4 py-2 font-semibold">Last activity</th>
                  <th className="px-4 py-2 font-semibold">Link</th>
                  <th className="px-4 py-2" />
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.token} className="table-row">
                    <td className="px-4 py-2.5 font-medium text-gray-900">{row.candidate_name}</td>
                    <td className="px-4 py-2.5">
                      <StatusPill status={row.status} />
                    </td>
                    <td className="tabular px-4 py-2.5 text-gray-700">
                      {row.asked ? `${row.answered}/${row.asked}` : "—"}
                    </td>
                    <td className="tabular px-4 py-2.5 text-gray-700">{mmss(row.duration_sec)}</td>
                    <td className="px-4 py-2.5 text-gray-500">{timeAgo(row.last_activity)}</td>
                    <td className="px-4 py-2.5">
                      <button
                        onClick={() => copy(row)}
                        className="inline-flex h-7 items-center gap-1.5 rounded-sm border border-gray-300 bg-surface px-2 text-xs font-semibold text-gray-600 shadow-xs transition-colors hover:bg-gray-50"
                      >
                        {copied === row.token ? (
                          "Copied"
                        ) : (
                          <>
                            <Copy className="h-3 w-3" />
                            Copy
                          </>
                        )}
                      </button>
                    </td>
                    <td className="px-4 py-2.5 text-right">
                      {row.session_id ? (
                        <button
                          onClick={() => navigate({ name: "session", id: row.session_id! })}
                          className="text-xs font-semibold text-brand-600 transition-colors hover:text-brand-700"
                        >
                          Review
                        </button>
                      ) : (
                        <a
                          href={row.link}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-1 text-xs text-gray-400 transition-colors hover:text-gray-600"
                        >
                          <Link2 className="h-3.5 w-3.5" />
                          Open
                        </a>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
