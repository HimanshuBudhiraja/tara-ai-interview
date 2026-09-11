import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle, ArrowLeft, Check, Copy, Link2, Lock, Mail, Send, Trash2, X,
} from "lucide-react";
import {
  adminApi, FormError,
  type Invitation, type InvitationList, type PublishCheck, type VersionRow,
} from "../lib/adminApi";
import { navigate } from "../lib/route";
import { Badge, Button, Callout, Spinner } from "@tara/ui/primitives";
import { SectionHeading, timeAgo } from "../components/RecruiterShell";
import { cn } from "../lib/cn";

/**
 * Publish, then invite.
 *
 * Publishing is a one-way door: everything before it is a draft the recruiter
 * can change, everything after it is fixed. So the confirmation shows the real
 * persisted counts — nothing estimated — and when it cannot publish it says
 * exactly which thing to go and fix.
 */
export function PublishInterview({ id }: { id: string }) {
  const [check, setCheck] = useState<PublishCheck | null>(null);
  const [invites, setInvites] = useState<InvitationList | null>(null);
  const [versions, setVersions] = useState<VersionRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [problems, setProblems] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [justPublished, setJustPublished] = useState<number | null>(null);

  const load = useCallback(async () => {
    const [c, v] = await Promise.all([adminApi.publishCheck(id), adminApi.versions(id)]);
    setCheck(c);
    setVersions(v.versions);
    if (c.published_version) setInvites(await adminApi.invitations(id));
  }, [id]);

  useEffect(() => {
    load().catch((e: Error) => setError(e.message));
  }, [load]);

  async function publish() {
    setBusy(true);
    setError(null);
    setProblems([]);
    try {
      const result = await adminApi.publish(id);
      setJustPublished(result.created ? result.version : null);
      await load();
    } catch (err) {
      if (err instanceof FormError) setProblems(Object.values(err.errors));
      else setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (error && !check) return <Callout tone="error" title="Couldn't load this interview">{error}</Callout>;
  if (!check) return <div className="card grid place-items-center py-20"><Spinner /></div>;

  const { summary } = check;
  const published = check.published_version > 0;

  return (
    <div className="max-w-3xl space-y-5">
      <Button variant="link" onClick={() => navigate({ name: "question-pool", id })}>
        <ArrowLeft className="h-4 w-4" />
        Question pool
      </Button>

      {error && <Callout tone="error" title="That didn't work">{error}</Callout>}

      {justPublished && (
        <Callout tone="success" title={`Published as version ${justPublished}`}>
          This version is now fixed. Anyone you invite from here sits exactly this
          interview, whatever you change afterwards.
        </Callout>
      )}

      {/* --- what would be published ---------------------------------- */}
      <section className="card p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="label">{published ? "Published interview" : "Ready to publish"}</p>
            <h2 className="mt-0.5 text-xl font-semibold tracking-tight text-gray-900">
              {summary.title}
            </h2>
          </div>
          {published ? (
            <Badge tone="success" dot>Version {check.published_version}</Badge>
          ) : (
            <Badge tone="neutral" dot>Draft</Badge>
          )}
        </div>

        <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-2.5 border-t border-gray-100 pt-4 sm:grid-cols-3">
          <Fact label="Skills" value={summary.skills}
                hint={`${summary.high_priority_skills} high priority`} />
          <Fact label="Tasks" value={summary.tasks} />
          <Fact label="Questions" value={summary.questions}
                hint={`${summary.questions_asked_per_candidate} asked per candidate`} />
          <Fact label="Duration" value={`${summary.duration_min} min`}
                hint={summary.interview_type} />
          <Fact label="Difficulty" value={summary.difficulty} />
          <Fact label="Language" value={summary.language_label} />
        </dl>

        {check.ready && !problems.length && (
          <p className="mt-3 flex items-center gap-1.5 border-t border-gray-100 pt-3 text-sm text-success-700">
            <Check className="h-4 w-4" />
            All required assessment coverage and safety checks passed.
          </p>
        )}

        {problems.length > 0 && (
          <Callout tone="error" title="Cannot publish" className="mt-3">
            <ul className="ml-4 list-disc space-y-1">
              {problems.map((p, i) => <li key={i}>{p}</li>)}
            </ul>
          </Callout>
        )}

        {!check.ready && !problems.length && (
          <Callout tone="error" title="Cannot publish yet" className="mt-3">
            <ul className="ml-4 list-disc space-y-1">
              {check.check.problems.map((p, i) => (
                <li key={i}><span className="font-medium capitalize">{p.area}</span> — {p.message}</li>
              ))}
            </ul>
          </Callout>
        )}

        {published && check.has_unpublished_changes && (
          <Callout tone="warning" title="You have unpublished changes" className="mt-3">
            Version {check.published_version} is unchanged and anyone already invited still
            sits it. Publishing again creates the next version.
          </Callout>
        )}

        <div className="mt-4 flex items-center gap-3 border-t border-gray-100 pt-4">
          <Button onClick={publish} disabled={busy || !check.ready}>
            {busy ? <Spinner /> : <Lock className="h-4 w-4" />}
            {published ? "Publish new version" : "Publish interview"}
          </Button>
          <p className="text-sm text-gray-500">
            Publishing freezes this assessment. It can't be edited afterwards — edits
            become the next version.
          </p>
        </div>
      </section>

      {versions.length > 0 && (
        <section>
          <SectionHeading title="Versions"
                          hint="Each one is fixed. Candidates sit the version they were invited to." />
          <div className="card overflow-hidden">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-gray-200 bg-gray-25 text-2xs uppercase tracking-wide text-gray-500">
                  <th className="px-4 py-2 font-semibold">Version</th>
                  <th className="px-4 py-2 font-semibold">Published</th>
                  <th className="px-4 py-2 text-right font-semibold">Questions</th>
                  <th className="px-4 py-2 text-right font-semibold">Invited</th>
                  <th className="px-4 py-2 text-right font-semibold">Completed</th>
                </tr>
              </thead>
              <tbody>
                {versions.map((v) => (
                  <tr key={v.version} className="border-b border-gray-100 last:border-0">
                    <td className="px-4 py-2 font-medium text-gray-900">v{v.version}</td>
                    <td className="px-4 py-2 text-gray-600">{timeAgo(v.published_at)}</td>
                    <td className="tabular px-4 py-2 text-right text-gray-700">{v.questions}</td>
                    <td className="tabular px-4 py-2 text-right text-gray-700">{v.invitations}</td>
                    <td className="tabular px-4 py-2 text-right text-gray-700">{v.completed}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {published && invites && (
        <Invitations id={id} data={invites} onChange={load} />
      )}
    </div>
  );
}

function Fact({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  return (
    <div>
      <dt className="label">{label}</dt>
      <dd className="mt-0.5 text-md font-semibold capitalize text-gray-900">{value}</dd>
      {hint && <dd className="text-xs capitalize text-gray-500">{hint}</dd>}
    </div>
  );
}

/* ---------------------------------------------------------------------- */

function Invitations({
  id, data, onChange,
}: { id: string; data: InvitationList; onChange: () => Promise<void> }) {
  const [emails, setEmails] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<Invitation[]>([]);

  async function send() {
    const list = emails.split(/[\n,]/).map((e) => e.trim()).filter(Boolean);
    if (!list.length) return;
    setBusy(true);
    setError(null);
    try {
      const result = await adminApi.sendInvitations(id, list, note);
      setCreated(result.created);
      setEmails("");
      await onChange();
    } catch (err) {
      setError(err instanceof FormError
        ? Object.values(err.errors).join(" ")
        : (err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="space-y-4">
      <SectionHeading title="Invite candidates"
                      hint={`Everyone invited from here sits version ${data.published_version}.`} />

      {error && <Callout tone="error" title="That didn't work">{error}</Callout>}

      {!data.email_delivery_configured && (
        <Callout tone="warning" title="Email delivery isn't set up">
          Tara creates the invitation and gives you the link. Sending it is up to you —
          nothing here emails anyone.
        </Callout>
      )}

      <div className="card space-y-3 p-4">
        <p className="flex items-center gap-1.5 text-md font-semibold text-gray-900">
          <Mail className="h-4 w-4 text-gray-400" />
          Individual invitations
        </p>
        <textarea
          value={emails}
          onChange={(e) => setEmails(e.target.value)}
          rows={3}
          placeholder={"priya@example.com\nalex@example.com"}
          className="w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm shadow-xs focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-100"
        />
        <p className="text-xs text-gray-500">
          One per line, or a name if you don't have an address. Up to{" "}
          {data.max_emails_per_batch} at a time.
        </p>
        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="A note to include with the invitation (optional)"
          className="h-8 w-full rounded-md border border-gray-300 px-2 text-sm shadow-xs focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-100"
        />
        <Button size="sm" onClick={send} disabled={busy}>
          {busy ? <Spinner /> : <Send className="h-4 w-4" />}
          Create invitations
        </Button>

        {created.length > 0 && (
          <div className="rounded-md border border-success-200 bg-success-25 p-3">
            <p className="text-sm font-medium text-success-700">
              {created.length} invitation{created.length === 1 ? "" : "s"} created — copy
              the links and send them.
            </p>
            <div className="mt-2 space-y-1.5">
              {created.map((i) => <LinkRow key={i.token} invitation={i} />)}
            </div>
          </div>
        )}
      </div>

      <OpenLink id={id} current={data.open_link} onChange={onChange} />

      {data.invitations.length > 0 && (
        <div className="card overflow-hidden">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-gray-200 bg-gray-25 text-2xs uppercase tracking-wide text-gray-500">
                <th className="px-4 py-2 font-semibold">Candidate</th>
                <th className="px-4 py-2 font-semibold">Version</th>
                <th className="px-4 py-2 font-semibold">Status</th>
                <th className="px-4 py-2 font-semibold">Created</th>
                <th className="px-4 py-2" />
              </tr>
            </thead>
            <tbody>
              {data.invitations.map((i) => (
                <tr key={i.token} className="border-b border-gray-100 last:border-0">
                  <td className="px-4 py-2">
                    <span className="font-medium text-gray-900">{i.candidate_name}</span>
                    {i.recipient && (
                      <span className="ml-1.5 text-xs text-gray-500">{i.recipient}</span>
                    )}
                  </td>
                  <td className="tabular px-4 py-2 text-gray-600">v{i.interview_version}</td>
                  <td className="px-4 py-2"><InvitationStatusPill status={i.status} /></td>
                  <td className="px-4 py-2 text-gray-500">{timeAgo(i.created_at)}</td>
                  <td className="px-4 py-2 text-right">
                    <div className="flex items-center justify-end gap-1">
                      <CopyButton value={new URL(i.link, location.origin).toString()} />
                      {i.status !== "revoked" && (
                        <button
                          title="Withdraw"
                          onClick={async () => {
                            await adminApi.revokeInvitation(id, i.token);
                            await onChange();
                          }}
                          className="rounded p-1 text-gray-400 hover:bg-error-50 hover:text-error-600"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function OpenLink({
  id, current, onChange,
}: { id: string; current: Invitation | null; onChange: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);

  async function toggle(enabled: boolean) {
    setBusy(true);
    try {
      await adminApi.setOpenLink(id, enabled);
      await onChange();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card space-y-2.5 p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="flex items-center gap-1.5 text-md font-semibold text-gray-900">
            <Link2 className="h-4 w-4 text-gray-400" />
            Open link
          </p>
          <p className="mt-0.5 max-w-[60ch] text-sm text-gray-600">
            One link anyone can use. It stays bound to the version it was created
            against, so two people opening it a week apart sit the same interview.
          </p>
        </div>
        <Button size="sm" variant={current ? "danger" : "secondary"} disabled={busy}
                onClick={() => toggle(!current)}>
          {busy ? <Spinner /> : current ? <X className="h-4 w-4" /> : <Link2 className="h-4 w-4" />}
          {current ? "Turn off" : "Enable"}
        </Button>
      </div>
      {current && <LinkRow invitation={current} />}
    </div>
  );
}

function LinkRow({ invitation }: { invitation: Invitation }) {
  const url = new URL(invitation.link, location.origin).toString();
  return (
    <div className="flex items-center gap-2 rounded-md border border-gray-200 bg-surface px-2 py-1.5">
      <code className="tabular flex-1 truncate text-xs text-gray-700">{url}</code>
      <CopyButton value={url} />
    </div>
  );
}

function CopyButton({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      title="Copy link"
      onClick={() => {
        void navigator.clipboard?.writeText(value);
        setCopied(true);
        setTimeout(() => setCopied(false), 1400);
      }}
      className={cn("rounded p-1 hover:bg-gray-100",
        copied ? "text-success-600" : "text-gray-400 hover:text-gray-700")}
    >
      {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
    </button>
  );
}

const STATUS_TONE: Record<string, "neutral" | "brand" | "success" | "error"> = {
  created: "neutral", active: "neutral", opened: "brand",
  in_progress: "brand", complete: "success", expired: "error", revoked: "error",
};

function InvitationStatusPill({ status }: { status: string }) {
  return (
    <Badge tone={STATUS_TONE[status] ?? "neutral"} dot>
      {status.replace("_", " ")}
    </Badge>
  );
}

/** Proctoring is in the product design and is not implemented. Said, not shown. */
export function ProctoringNotice() {
  return (
    <div className="rounded-lg border border-dashed border-gray-300 bg-gray-25 p-4">
      <p className="flex items-center gap-1.5 text-sm font-semibold text-gray-700">
        <AlertTriangle className="h-4 w-4 text-gray-400" />
        Proctoring isn't available
      </p>
      <p className="mt-1 max-w-[60ch] text-sm text-gray-600">
        Image proctoring and the safe assessment browser are part of the product design
        but are not built. Nothing is enforced on the candidate's machine.
      </p>
    </div>
  );
}
