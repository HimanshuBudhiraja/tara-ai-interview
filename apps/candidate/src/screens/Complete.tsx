import { Check } from "lucide-react";
import { Shell } from "../components/Shell";

/**
 * The last thing they see.
 *
 * No score, no percentage, no "you did well". The candidate app is not
 * permitted to show an assessment and a person hasn't made the decision yet —
 * anything that reads as a result here would be a guess dressed as a verdict.
 * What it can honestly give is closure: it's finished, it was received, and
 * here is what happens next.
 */
export function Complete({ name, answered }: { name: string; answered: number }) {
  const firstName = name.split(" ")[0];
  return (
    <Shell>
      <div className="animate-slide-up py-8 text-center">
        <div className="mx-auto grid h-11 w-11 place-items-center rounded-full border border-success-200 bg-success-50">
          <Check className="h-5 w-5 text-success-600" strokeWidth={2} />
        </div>
        <h1 className="mt-4 text-3xl font-semibold tracking-tight text-gray-900">
          That's everything, {firstName}
        </h1>
        <p className="mx-auto mt-2 max-w-[50ch] text-lg leading-relaxed text-gray-600">
          Thank you for your time. Your interview has been submitted and nothing further is needed
          from you.
        </p>

        <div className="card mx-auto mt-7 max-w-[460px] p-5 text-left">
          <h2 className="text-md font-semibold text-gray-900">What happens next</h2>
          <ol className="mt-3 space-y-3 text-sm leading-relaxed text-gray-600">
            <Step n={1}>
              A member of the hiring team reviews your interview alongside your application.
            </Step>
            <Step n={2}>
              They make the decision — a person, not Tara. Tara's job was to run the conversation.
            </Step>
            <Step n={3}>
              You'll hear from the team either way. If you don't hear anything within a week,
              follow up with whoever invited you.
            </Step>
          </ol>
        </div>

        {answered > 0 && (
          <p className="mt-6 text-sm text-gray-400">
            {answered} {answered === 1 ? "question" : "questions"} answered. You can close this tab.
          </p>
        )}
      </div>
    </Shell>
  );
}

function Step({ n, children }: { n: number; children: React.ReactNode }) {
  return (
    <li className="flex gap-3">
      <span className="tabular grid h-5 w-5 shrink-0 place-items-center rounded-full bg-gray-100 text-2xs font-bold text-gray-500">
        {n}
      </span>
      <span>{children}</span>
    </li>
  );
}
