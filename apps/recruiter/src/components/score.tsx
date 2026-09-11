import { cn } from "../lib/cn";
import { Badge } from "@tara/ui/primitives";

/**
 * The visual language for a level.
 *
 * Everything a level appears in — results table, comparison grid, candidate
 * report — uses these two components, so "3.2 against a target of 3" looks the
 * same wherever a recruiter meets it. A score that renders differently on two
 * screens is a score people stop trusting.
 */

const BAND_TONE: Record<string, "success" | "warning" | "neutral" | "error"> = {
  strong_hire: "success",
  hire: "success",
  borderline: "warning",
  no_hire: "neutral",
  not_scored: "neutral",
};

export function BandPill({ band, label }: { band: string; label: string }) {
  return (
    <Badge tone={BAND_TONE[band] ?? "neutral"} dot>
      {label}
    </Badge>
  );
}

/**
 * A level on the 0-4 scale with the required level marked on it.
 *
 * The target tick is the whole point: a bare number can't tell you whether 3 is
 * good, and "3 out of 4" is meaningless if the role only ever needed 2.
 */
export function LevelBar({
  level,
  target,
  compact = false,
}: {
  level: number | null;
  target: number;
  compact?: boolean;
}) {
  if (level == null) {
    return <span className="text-xs text-gray-400">Not assessed</span>;
  }
  const met = level >= target;
  const pct = Math.max(0, Math.min(100, (level / 4) * 100));
  const targetPct = Math.max(0, Math.min(100, (target / 4) * 100));

  return (
    <div className={cn("flex items-center gap-2.5", compact ? "min-w-[104px]" : "min-w-[150px]")}>
      <div className="relative h-1.5 flex-1 overflow-visible rounded-full bg-gray-100">
        <div
          className={cn(
            "h-full rounded-full transition-[width] duration-300",
            met ? "bg-success-500" : "bg-warning-500",
          )}
          style={{ width: `${pct}%` }}
        />
        {/* The bar the role asked for. */}
        <span
          className="absolute -top-1 h-3.5 w-px bg-gray-500"
          style={{ left: `${targetPct}%` }}
          title={`Required: ${target}`}
        />
      </div>
      <span className={cn("tabular text-xs font-semibold", met ? "text-gray-900" : "text-warning-700")}>
        {level.toFixed(1)}
      </span>
      {!compact && <span className="tabular text-xs text-gray-400">/ {target}</span>}
    </div>
  );
}

/** A single level as a compact chip — for dense grids like the comparison. */
export function LevelChip({
  level,
  target,
  flagged,
  assessed = true,
}: {
  level: number | null;
  target: number;
  flagged?: boolean;
  assessed?: boolean;
}) {
  if (!assessed || level == null) {
    return <span className="text-xs text-gray-300">—</span>;
  }
  const met = level >= target;
  return (
    <span
      className={cn(
        "tabular inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5 text-xs font-semibold",
        met
          ? "border-success-200 bg-success-50 text-success-700"
          : "border-warning-200 bg-warning-50 text-warning-700",
      )}
      title={met ? `Meets the required level of ${target}` : `Below the required level of ${target}`}
    >
      {level.toFixed(1)}
      {flagged && <span className="text-warning-600" title="Low confidence">*</span>}
    </span>
  );
}
