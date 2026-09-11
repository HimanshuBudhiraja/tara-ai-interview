import { useEffect, useState } from "react";
import { Copy, Link2, Plus, UserPlus } from "lucide-react";
import { adminApi, type Candidate, type Interview } from "../lib/adminApi";
import { navigate } from "../lib/route";
import { Button, Callout } from "@tara/ui/primitives";
import { Empty, StatusPill, mmss, timeAgo } from "../components/RecruiterShell";

export function Candidates() {
  const [rows, setRows] = useState<Candidate[] | null>(null);
  const [interviews, setInterviews] = useState<Interview[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [interviewId, setInterviewId] = useState("");
  const [copied, setCopied] = useState<string | null>(null);
  const [inviting, setInviting] = useState(false);

  const load = () =>
    Promise.all([adminApi.candidates(), adminApi.interviews()])
      .then(([c, i]) => {
        setRows(c.candidates);
        setInterviews(i.interviews);
        const published = i.interviews.filter((x) => x.status === "published");
        setInterviewId((prev) => prev || published[0]?.id || "");
      })
      .catch((e) => setError(e.message));

  useEffect(() => {
    void load();
  }, []);

  const published = interviews.filter((i) => i.status === "published");

  const invite = async () => {
    if (!name.trim() || !interviewId) return;
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
    // The absolute URL, because the recruiter is going to paste this into an
    // email — a relative path would be useless there.
    await navigator.clipboard.writeText(`${location.origin}${row.link}`);
    setCopied(row.token);
    window.setTimeout(() => setCopied(null), 1600);
  };

  return (
    <div className="space-y-5">
      {error && <Callout tone="error">{error}</Callout>}

      {/* --- invite --- */}
      <section className="card p-4 sm:p-5">
        <h2 className="flex items-center gap-2 text-md font-semibold text-gray-900">
          <UserPlus className="h-4 w-4 text-gray-400" />
          Invite a candidate
        </h2>
        <p className="mt-0.5 text-sm text-gray-500">
          The link carries their name and the interview. They don't sign up, and they can't open an
          interview they weren't invited to.
        </p>

        {published.length === 0 ? (
          <div className="mt-4">
            <Callout tone="warning" title="No published interview">
              Publish an interview before inviting anyone — a draft can still change underneath a
              candidate who's already started.
            </Callout>
          </div>
        ) : (
          <div className="mt-4 flex flex-wrap items-end gap-3">
            <label className="min-w-[220px] flex-1">
              <span className="mb-1.5 block text-xs font-semibold text-gray-700">Candidate name</span>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && invite()}
                placeholder="Priya Sharma"
                className="field"
              />
            </label>
            <label className="min-w-[220px] flex-1">
              <span className="mb-1.5 block text-xs font-semibold text-gray-700">Interview</span>
              <select
                value={interviewId}
                onChange={(e) => setInterviewId(e.target.value)}
                className="field-select h-9 w-full text-base"
              >
                {published.map((i) => (
                  <option key={i.id} value={i.id}>
                    {i.title}
                  </option>
                ))}
              </select>
            </label>
            <Button onClick={invite} disabled={!name.trim()} loading={inviting}>
              {!inviting && <Plus className="h-4 w-4" />}
              Create link
            </Button>
          </div>
        )}
      </section>

      {/* --- list --- */}
      {rows === null && <div className="card h-48 animate-pulse bg-gray-50" />}
      {rows?.length === 0 && (
        <Empty title="Nobody invited yet" icon={<UserPlus className="h-4 w-4" />}>
          Create a link above and send it to a candidate. You'll see their progress here as they go.
        </Empty>
      )}

      {rows && rows.length > 0 && (
        <div className="card overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[780px] text-left text-sm">
              <thead className="table-head">
                <tr>
                  <Th>Candidate</Th>
                  <Th>Status</Th>
                  <Th>Progress</Th>
                  <Th>Duration</Th>
                  <Th>Last activity</Th>
                  <Th>Link</Th>
                  <Th> </Th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.token} className="table-row">
                    <td className="px-4 py-2.5">
                      <p className="text-base font-medium text-gray-900">{row.candidate_name}</p>
                      <p className="meta mt-0.5">
                        {interviews.find((i) => i.id === row.interview_id)?.title ?? "—"}
                      </p>
                    </td>
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

function Th({ children }: { children: React.ReactNode }) {
  return <th className="px-4 py-2 font-semibold">{children}</th>;
}
