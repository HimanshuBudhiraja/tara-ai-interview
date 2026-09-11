import { Check } from "lucide-react";
import type { Progress } from "../lib/types";
import { cn } from "../lib/cn";

/**
 * Where am I, and how much is left.
 *
 * Shows position, never performance. A candidate must not be able to read
 * "how I'm doing" off this rail — a competency marked done means it was
 * covered, not that it went well, and follow-ups deliberately don't move the
 * counter, or a probed candidate would watch their progress stall and read it
 * as failure.
 */
export function ProgressRail({ progress }: { progress: Progress | null }) {
  if (!progress) return null;
  const { asked, total, coverage } = progress;
  const pct = total ? Math.min(100, Math.round(((asked - 1) / total) * 100)) : 0;

  return (
    <aside className="card p-4">
      <div className="mb-4">
        <div className="mb-2 flex items-baseline justify-between">
          <span className="text-sm font-semibold text-gray-900">Progress</span>
          <span className="tabular text-sm text-gray-500">
            {Math.min(asked, total)} of {total}
          </span>
        </div>
        <div className="h-1.5 overflow-hidden rounded-full bg-gray-100">
          <div
            className="brand-gradient h-full rounded-full transition-[width] duration-500 ease-out"
            style={{ width: `${Math.max(pct, 3)}%` }}
          />
        </div>
      </div>

      <p className="label mb-2.5">Areas covered</p>
      <ul className="space-y-2">
        {coverage.map((c) => {
          const done = c.asked >= c.target;
          const started = c.asked > 0;
          return (
            <li key={c.id} className="flex items-center gap-2.5 text-sm">
              <span
                className={cn(
                  "grid h-4 w-4 shrink-0 place-items-center rounded-full border transition-colors",
                  done
                    ? "border-success-600 bg-success-600 text-white"
                    : started
                      ? "border-brand-400 bg-brand-50"
                      : "border-gray-300 bg-surface",
                )}
              >
                {done && <Check className="h-2.5 w-2.5" strokeWidth={3.5} />}
              </span>
              <span className={cn(done || started ? "text-gray-700" : "text-gray-400")}>
                {c.label}
              </span>
            </li>
          );
        })}
      </ul>

      <p className="mt-4 border-t border-gray-100 pt-3 text-xs leading-relaxed text-gray-400">
        Follow-up questions don't count against your total — they're just part of the conversation.
      </p>
    </aside>
  );
}
