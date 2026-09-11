import type { ReactNode } from "react";
import { Check } from "lucide-react";
import { cn } from "../lib/cn";

/**
 * The three steps, and the block that holds a group of fields.
 *
 * Shared rather than copied because the two screens either side of the
 * designer — job details, then the review — are one flow, and a rail that
 * drifts between them stops being a rail and becomes decoration.
 */
export const STEPS = [
  { n: 1, label: "Define job role", hint: "What the role is" },
  { n: 2, label: "Interview configuration", hint: "Review what Tara proposes" },
  { n: 3, label: "Invite candidates", hint: "Send the link" },
] as const;

/**
 * Where you are in the three steps.
 *
 * A rail rather than a breadcrumb: the useful question on a form is how much
 * is left, not how you arrived.
 */
export function Steps({ current }: { current: number }) {
  return (
    <ol className="flex flex-wrap gap-2">
      {STEPS.map((step) => {
        const done = step.n < current;
        const active = step.n === current;
        return (
          <li
            key={step.n}
            aria-current={active ? "step" : undefined}
            className={cn(
              "flex min-w-[168px] flex-1 items-center gap-2.5 rounded-md border px-3 py-2",
              active ? "border-brand-200 bg-brand-25" : "border-gray-200 bg-surface",
            )}
          >
            <span
              className={cn(
                "grid h-6 w-6 shrink-0 place-items-center rounded-full text-xs font-semibold",
                active && "bg-brand-600 text-white",
                done && "bg-success-100 text-success-700",
                !active && !done && "bg-gray-100 text-gray-400",
              )}
            >
              {done ? <Check className="h-3.5 w-3.5" /> : step.n}
            </span>
            <span className="min-w-0">
              <span
                className={cn(
                  "block truncate text-sm font-medium",
                  active ? "text-gray-900" : "text-gray-500",
                )}
              >
                {step.label}
              </span>
              <span className="block truncate text-2xs text-gray-400">{step.hint}</span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}

/**
 * One titled group. Several short blocks read faster than one long card, and
 * the hint carries the thing a recruiter would otherwise have to be told.
 */
export function Block({
  title,
  hint,
  action,
  children,
}: {
  title: string;
  hint?: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="card space-y-4 p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-md font-semibold text-gray-900">{title}</h3>
          {hint && (
            <p className="mt-0.5 max-w-[68ch] text-sm leading-relaxed text-gray-500">{hint}</p>
          )}
        </div>
        {action && <div className="shrink-0">{action}</div>}
      </div>
      {children}
    </section>
  );
}

/** One fact in a header's meta row: an icon, a label, a value. */
export function Meta({ icon, children }: { icon: ReactNode; children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-sm text-gray-600">
      <span className="text-gray-400">{icon}</span>
      {children}
    </span>
  );
}
