import { CircleAlert, Link2 } from "lucide-react";
import { Shell } from "../components/Shell";

/**
 * Dead ends: a bad link, an expired link, a server that won't answer.
 *
 * A candidate who hits this cannot fix it themselves, so the screen's whole job
 * is to say plainly what went wrong and who to contact — never a status code,
 * never "an error occurred".
 *
 * The closing line does NOT say "reply to your invitation email". Tara sends no
 * email: `invites.py` mints a link and the recruiter passes it on themselves,
 * which might be an ATS, a portal, a message or a forwarded note. Telling a
 * candidate to reply to an email they never received sends them looking for
 * something that does not exist.
 */
export function Problem({
  title,
  detail,
  tone = "error",
}: {
  title: string;
  detail: string;
  /**
   * `error` — something went wrong: the link is expired, revoked, malformed,
   * or the server would not answer. A red mark is honest there.
   *
   * `info` — nothing has gone wrong. The commonest case is someone opening the
   * bare address with no link at all, and greeting them with a red error mark
   * tells them the product is broken when they have simply arrived without
   * their ticket.
   */
  tone?: "error" | "info";
}) {
  const isError = tone === "error";
  return (
    <Shell>
      {/* Centred rather than pinned to the top: there is nothing below this, and
          a short message floating above a screen of empty space reads as a page
          that failed to finish loading. */}
      <div className="animate-slide-up flex min-h-[60vh] flex-col justify-center py-10 text-center">
        <div
          className={
            isError
              ? "mx-auto grid h-11 w-11 place-items-center rounded-full border border-error-200 bg-error-50"
              : "mx-auto grid h-11 w-11 place-items-center rounded-full border border-gray-200 bg-gray-50"
          }
        >
          {isError ? (
            <CircleAlert className="h-5 w-5 text-error-600" />
          ) : (
            <Link2 className="h-5 w-5 text-gray-500" />
          )}
        </div>
        <h1 className="mt-4 text-2xl font-semibold tracking-tight text-gray-900">{title}</h1>
        <p className="mx-auto mt-2 max-w-[50ch] text-base leading-relaxed text-gray-600">
          {detail}
        </p>
        <p className="mx-auto mt-6 max-w-[50ch] text-sm leading-relaxed text-gray-400">
          {isError
            ? "The quickest way forward is to go back to whoever invited you and ask them to check the link."
            : "If you can't find your link, ask whoever invited you to send it again."}
        </p>
      </div>
    </Shell>
  );
}

export function Loading() {
  return (
    <div className="grid min-h-full place-items-center bg-canvas">
      <div className="flex items-center gap-3 text-gray-400">
        <svg className="h-5 w-5 animate-spin" viewBox="0 0 24 24" fill="none">
          <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="3" opacity=".25" />
          <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
        </svg>
        <span className="text-sm">Loading your interview…</span>
      </div>
    </div>
  );
}
