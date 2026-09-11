import { Clock, ShieldCheck, Sparkles, UserRound } from "lucide-react";
import type { Invite } from "../lib/types";
import { Button, Shell } from "../components/Shell";

/**
 * The door. One question: is this you?
 *
 * The invitation link is the credential — 43 random bytes, and the only thing
 * that proves anyone may sit this interview. So this screen does NOT ask for a
 * password, an access code, or an email to match against; it has nothing to
 * check them with, and a form that looks like authentication while
 * authenticating nothing is worse than no form at all.
 *
 * What it does is worth doing on its own. A link travels: forwarded by a
 * recruiter to the wrong person, pasted in a group chat, opened on a shared
 * machine. Today that just works, silently, under someone else's name and into
 * someone else's hiring record. Naming the candidate before anything starts
 * turns a silent mistake into an obvious one — and gives the person a moment to
 * stop.
 */
export function Access({ invite, onContinue }: { invite: Invite; onContinue: () => void }) {
  return (
    <Shell>
      <div className="animate-slide-up flex min-h-[70vh] flex-col justify-center py-8">
        <div className="mx-auto w-full max-w-[520px]">
          <p className="label text-brand-600">{invite.role_title}</p>
          <h1 className="mt-2 text-3xl font-semibold leading-tight tracking-tight text-gray-900">
            {invite.resumable ? "Ready to pick up where you left off?" : "Your interview is ready"}
          </h1>

          <div className="card mt-6 p-5">
            <div className="flex items-start gap-3">
              <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-brand-50 text-brand-700">
                <UserRound className="h-5 w-5" />
              </span>
              <div className="min-w-0">
                <p className="label">You're joining as</p>
                <p className="mt-0.5 truncate text-lg font-semibold text-gray-900">
                  {invite.candidate_name}
                </p>
                {invite.resumable && (
                  <p className="mt-1 text-sm text-gray-500">
                    {invite.answered} of {invite.question_count} questions answered so far.
                  </p>
                )}
              </div>
            </div>

            <div className="mt-5 flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-gray-100 pt-4">
              <Badge icon={<Clock className="h-3.5 w-3.5" />}>
                About {invite.estimated_minutes} minutes
              </Badge>
              <Badge icon={<ShieldCheck className="h-3.5 w-3.5" />}>
                Reviewed by a person
              </Badge>
              <Badge icon={<Sparkles className="h-3.5 w-3.5" />}>
                AI-led conversation
              </Badge>
            </div>

            <div className="mt-5">
              <Button size="lg" onClick={onContinue} className="w-full sm:w-auto">
                {invite.resumable ? "Continue my interview" : "That's me — continue"}
              </Button>
            </div>
          </div>

          {/* No "switch account" link, because there is no account to switch
              to. The only honest remedy is the person who sent the link. */}
          <p className="mt-4 text-center text-sm leading-relaxed text-gray-500">
            Not {invite.candidate_name.split(" ")[0]}? Don't continue — this link belongs to
            someone else's application. Ask whoever sent it to issue your own.
          </p>
        </div>
      </div>
    </Shell>
  );
}

function Badge({ icon, children }: { icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-sm text-gray-600">
      <span className="text-gray-400">{icon}</span>
      {children}
    </span>
  );
}
