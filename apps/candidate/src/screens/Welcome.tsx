import { useState } from "react";
import { Clock, MessageSquare, Mic, Minus, ShieldCheck } from "lucide-react";
import * as Checkbox from "@radix-ui/react-checkbox";
import { Check } from "lucide-react";
import type { Invite } from "../lib/types";
import { Button, Callout, Shell } from "../components/Shell";

/**
 * First screen. Its job is to lower the candidate's heart rate.
 *
 * Everything here is a promise the interview then keeps: how long, how many
 * questions, that follow-ups are normal, that they can type instead, and
 * exactly what is recorded. Consent is an explicit action, never a
 * pre-ticked box — the interview cannot start without it.
 */
export function Welcome({
  invite,
  onStart,
  starting,
  error,
}: {
  invite: Invite;
  onStart: (opts: { accommodations: Record<string, unknown> }) => void;
  starting: boolean;
  error: string | null;
}) {
  const [consent, setConsent] = useState(false);
  const [extraTime, setExtraTime] = useState(false);
  const firstName = invite.candidate_name.split(" ")[0];

  return (
    <Shell>
      <div className="animate-slide-up">
        <p className="label mb-2 text-brand-600">{invite.role_title}</p>
        <h1 className="text-3xl font-semibold leading-tight tracking-tight text-gray-900">
          {invite.resumable ? `Welcome back, ${firstName}` : `Hello ${firstName} — let's begin`}
        </h1>

        {/* Spoken by Tara rather than written at the candidate. The interview
            is a conversation with her, and the first thing they read should
            sound like the thing they are about to do. */}
        <div className="mt-4 flex gap-3 rounded-lg border border-brand-200 bg-brand-25 p-4">
          <span className="brand-gradient grid h-8 w-8 shrink-0 place-items-center rounded-full text-sm font-semibold text-white">
            T
          </span>
          <p className="max-w-[58ch] text-base leading-relaxed text-gray-700">
            {invite.resumable
              ? `Good to see you again. You've answered ${invite.answered} of ${invite.question_count} questions and nothing was lost — we'll pick up exactly where we stopped.`
              : `I'm Tara, and I'll be running your interview today. It's a conversation, not a test — there are no trick questions, and everything below is what to expect before we start.`}
          </p>
        </div>

        <div className="mt-4 grid gap-3 sm:grid-cols-3">
          <Fact icon={<Clock className="h-4 w-4" />} label="About">
            {invite.estimated_minutes} minutes
          </Fact>
          <Fact icon={<MessageSquare className="h-4 w-4" />} label="Questions">
            {invite.question_count}, plus follow-ups
          </Fact>
          <Fact icon={<Mic className="h-4 w-4" />} label="You'll need">
            A microphone
          </Fact>
        </div>

        <section className="card mt-4 p-5">
          <h2 className="text-md font-semibold text-gray-900">What to expect</h2>
          <ul className="mt-3 space-y-2.5 text-base leading-relaxed text-gray-600">
            <li className="flex gap-2.5">
              <Dot /> Tara asks about real support situations. Answer out loud, in your own words —
              there's no time limit on any answer.
            </li>
            <li className="flex gap-2.5">
              <Dot /> She'll often ask a follow-up. That's normal and it isn't a sign you got
              anything wrong — it's how she hears your reasoning.
            </li>
            <li className="flex gap-2.5">
              <Dot /> If you'd like a question repeated, just say so. If you haven't hit that exact
              situation, say that too and describe how you'd approach it.
            </li>
            <li className="flex gap-2.5">
              <Dot /> If your connection drops, reopen this link. Nothing you've said is lost.
            </li>
          </ul>

          <h3 className="label mt-5">We'll be covering</h3>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {invite.competencies.map((c) => (
              <span
                key={c}
                className="rounded-sm border border-gray-200 bg-gray-50 px-2 py-0.5 text-xs font-medium text-gray-600"
              >
                {c}
              </span>
            ))}
          </div>
        </section>

        <section className="card mt-4 p-5">
          <h2 className="text-md font-semibold text-gray-900">Adjustments</h2>
          <p className="mt-1 text-sm text-gray-500">
            Available to anyone who wants them, no explanation needed.
          </p>
          <div className="mt-4 space-y-3">
            {/* "I'd rather type my answers" used to sit here. It is gone
                because it was not an accommodation, it was a second
                assessment: a written answer and a spoken one are not the same
                evidence, and offering the choice quietly meant two candidates
                could be compared on different things. What remains is a real
                accommodation — the pace changes, what is measured does not. */}
            <Toggle checked={extraTime} onChange={setExtraTime} label="Give me extra time to think between questions" />
          </div>
        </section>

        <section className="card mt-4 p-5">
          <h2 className="text-md font-semibold text-gray-900">How you're assessed</h2>
          <p className="mt-1 text-sm text-gray-500">
            A person makes the hiring decision — Tara does not.
          </p>
          <div className="mt-4 grid gap-5 sm:grid-cols-2">
            <div>
              <h3 className="label text-gray-500">What counts</h3>
              <ul className="mt-2 space-y-1.5">
                {invite.assessed_on.map((c) => (
                  <li key={c} className="flex items-center gap-2 text-base text-gray-700">
                    <Check className="h-4 w-4 shrink-0 text-success-600" strokeWidth={2.5} />
                    {c}
                  </li>
                ))}
              </ul>
            </div>
            <div>
              {/* Not marketing. Each of these is something the assessment is
                  structurally incapable of reading — the audio never reaches
                  the scorer, only the words in it do. */}
              <h3 className="label text-gray-500">What never counts</h3>
              <ul className="mt-2 space-y-1.5">
                {invite.not_assessed.map((c) => (
                  <li key={c} className="flex items-center gap-2 text-base text-gray-500">
                    <Minus className="h-4 w-4 shrink-0 text-gray-300" strokeWidth={2.5} />
                    {c}
                  </li>
                ))}
              </ul>
            </div>
          </div>

          <div className="mt-5 flex items-start gap-3 border-t border-gray-100 pt-4">
            <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-gray-400" />
            <p className="text-sm leading-relaxed text-gray-600">
              Your answers are transcribed and reviewed as part of your application, and shared
              with the hiring team for this role.
            </p>
          </div>
          <label className="mt-4 flex cursor-pointer items-start gap-3">
            <Checkbox.Root
              checked={consent}
              onCheckedChange={(v) => setConsent(v === true)}
              className="mt-0.5 grid h-[18px] w-[18px] shrink-0 place-items-center rounded-xs border border-gray-300 bg-surface shadow-xs transition-colors data-[state=checked]:border-brand-500 data-[state=checked]:bg-brand-500"
            >
              <Checkbox.Indicator>
                <Check className="h-3.5 w-3.5 text-white" strokeWidth={3} />
              </Checkbox.Indicator>
            </Checkbox.Root>
            <span className="text-base leading-relaxed text-gray-700">
              I understand my answers will be recorded and reviewed, and I'm ready to begin.
            </span>
          </label>
        </section>

        {error && (
          <div className="mt-4">
            <Callout tone="error" title="We couldn't start the interview">
              {error}
            </Callout>
          </div>
        )}

        <div className="mt-6 flex items-center gap-3">
          <Button
            size="lg"
            loading={starting}
            disabled={!consent}
            onClick={() =>
              onStart({ accommodations: { extra_time: extraTime } })
            }
          >
            {invite.resumable ? "Resume interview" : "Check microphone & start"}
          </Button>
          {!consent && <span className="text-sm text-gray-400">Tick the box above to continue</span>}
        </div>
      </div>
    </Shell>
  );
}

function Fact({
  icon,
  label,
  children,
}: {
  icon: React.ReactNode;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="card p-3.5">
      <div className="flex items-center gap-1.5 text-gray-400">
        {icon}
        <span className="label">{label}</span>
      </div>
      <p className="mt-1.5 text-base font-medium text-gray-900">{children}</p>
    </div>
  );
}

function Dot() {
  return <span className="mt-[9px] h-1 w-1 shrink-0 rounded-full bg-brand-400" />;
}

function Toggle({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
}) {
  return (
    <label className="flex cursor-pointer items-center gap-3">
      <Checkbox.Root
        checked={checked}
        onCheckedChange={(v) => onChange(v === true)}
        className="grid h-[18px] w-[18px] shrink-0 place-items-center rounded-xs border border-gray-300 bg-surface shadow-xs transition-colors data-[state=checked]:border-brand-500 data-[state=checked]:bg-brand-500"
      >
        <Checkbox.Indicator>
          <Check className="h-3.5 w-3.5 text-white" strokeWidth={3} />
        </Checkbox.Indicator>
      </Checkbox.Root>
      <span className="text-base text-gray-700">{label}</span>
    </label>
  );
}
