import type { ReactNode } from "react";
import { Callout } from "@tara/ui/primitives";

/**
 * A route that exists but whose screen doesn't yet.
 *
 * Deliberately not a mockup and deliberately not a 404. A mockup of a screen
 * that doesn't work gets demoed, believed, and planned around; a 404 makes a
 * reviewer think the route was forgotten. This says exactly what will be here,
 * what already works underneath it, and which phase builds it — so the
 * navigation can be walked end to end today without anyone mistaking the walk
 * for a working product.
 */
export function Placeholder({
  title,
  what,
  backend,
  phase,
  children,
}: {
  title: string;
  what: string;
  backend?: string;
  phase: string;
  children?: ReactNode;
}) {
  return (
    <div className="max-w-2xl space-y-4">
      <Callout tone="info" title={`${title} — not built yet`}>
        {what}
      </Callout>
      {backend && (
        <div className="rounded-lg border border-gray-200 bg-white p-4">
          <div className="text-2xs font-semibold uppercase tracking-wide text-gray-400">
            Already working underneath
          </div>
          <p className="mt-1.5 text-sm text-gray-600">{backend}</p>
        </div>
      )}
      <div className="rounded-lg border border-gray-200 bg-gray-25 p-4">
        <div className="text-2xs font-semibold uppercase tracking-wide text-gray-400">
          Scheduled
        </div>
        <p className="mt-1.5 text-sm text-gray-600">{phase}</p>
      </div>
      {children}
    </div>
  );
}
