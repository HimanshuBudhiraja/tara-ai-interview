import type { ReactNode } from "react";
import { Check } from "lucide-react";
import * as Checkbox from "@radix-ui/react-checkbox";
import { cn } from "../lib/cn";

export function Section({
  title,
  hint,
  action,
  children,
  id,
}: {
  title: string;
  hint?: string;
  action?: ReactNode;
  children: ReactNode;
  id?: string;
}) {
  return (
    <section id={id} className="card p-4 sm:p-5">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-md font-semibold text-gray-900">{title}</h2>
          {hint && (
            <p className="mt-0.5 max-w-[76ch] text-sm leading-relaxed text-gray-500">{hint}</p>
          )}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs font-semibold text-gray-700">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs leading-relaxed text-gray-400">{hint}</span>}
    </label>
  );
}

export function Tick({
  checked,
  onChange,
  disabled,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <Checkbox.Root
      checked={checked}
      disabled={disabled}
      onCheckedChange={(v) => onChange(v === true)}
      className={cn(
        "grid h-[18px] w-[18px] shrink-0 place-items-center rounded-[5px] border border-gray-300 bg-surface",
        "data-[state=checked]:border-brand-500 data-[state=checked]:bg-brand-500",
        disabled && "opacity-40",
      )}
    >
      <Checkbox.Indicator>
        <Check className="h-3 w-3 text-white" strokeWidth={3.5} />
      </Checkbox.Indicator>
    </Checkbox.Root>
  );
}

const PRIORITY_STYLE: Record<string, string> = {
  high: "bg-brand-50 text-brand-700 shadow-[inset_0_0_0_1px_theme(colors.brand.200)]",
  medium: "bg-gray-100 text-gray-700 shadow-[inset_0_0_0_1px_theme(colors.gray.300)]",
  low: "bg-gray-50 text-gray-500 shadow-[inset_0_0_0_1px_theme(colors.gray.200)]",
};

/** Priority as a three-way segmented control — a band, never a percentage. */
export function PriorityPicker({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: "high" | "medium" | "low") => void;
}) {
  return (
    <div
      role="radiogroup"
      className="inline-flex h-8 items-stretch overflow-hidden rounded-md border border-gray-300 bg-surface shadow-xs"
    >
      {(["high", "medium", "low"] as const).map((p, i) => (
        <button
          key={p}
          role="radio"
          aria-checked={value === p}
          onClick={() => onChange(p)}
          className={cn(
            "relative px-2.5 text-xs font-semibold capitalize transition-colors",
            i > 0 && "border-l border-gray-200",
            value === p ? PRIORITY_STYLE[p] : "text-gray-500 hover:bg-gray-50 hover:text-gray-700",
          )}
        >
          {p}
        </button>
      ))}
    </div>
  );
}

export function Slider({
  label,
  value,
  min,
  max,
  onChange,
  note,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  onChange: (v: number) => void;
  note?: string;
}) {
  return (
    <div>
      <div className="mb-2 flex items-baseline justify-between">
        <span className="text-xs font-semibold text-gray-700">{label}</span>
        <span className="tabular text-base font-semibold text-gray-900">{value}</span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="h-1 w-full cursor-pointer appearance-none rounded-full bg-gray-200 accent-brand-500"
      />
      {note && <p className="mt-1.5 text-xs leading-relaxed text-gray-500">{note}</p>}
    </div>
  );
}
