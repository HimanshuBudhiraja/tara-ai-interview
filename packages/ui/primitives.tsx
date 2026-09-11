/**
 * The primitives both products are built from.
 *
 * They live here rather than in either app because a Button that differs
 * between the interview screen and the console is a Button that will differ
 * more next month. What does NOT live here is either app's page frame — those
 * are genuinely different: the candidate gets a single centred column with no
 * navigation, the recruiter gets a sidebar and a workspace.
 */
import type { ReactNode } from "react";
import { cn } from "./cn";

export function Logo({ className, subtitle }: { className?: string; subtitle?: string }) {
  return (
    <div className={cn("flex items-center gap-2.5", className)}>
      <div className="brand-gradient grid h-7 w-7 place-items-center rounded-md text-xs font-bold tracking-tight text-white shadow-xs">
        iM
      </div>
      <div className="leading-none">
        <span className="text-base font-semibold tracking-tight text-gray-900">iMocha</span>
        {subtitle && <span className="mt-1 block text-2xs text-gray-400">{subtitle}</span>}
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- //
//  Button
// --------------------------------------------------------------------------- //
type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "link";
type ButtonSize = "sm" | "md" | "lg";

const BUTTON_SIZE: Record<ButtonSize, string> = {
  sm: "h-8 gap-1.5 px-2.5 text-sm",
  md: "h-9 gap-2 px-3.5 text-base",
  lg: "h-10 gap-2 px-4 text-base",
};

const BUTTON_VARIANT: Record<ButtonVariant, string> = {
  // A one-stop gradient on the primary action only — the single place in the
  // product where a filled gradient earns its keep.
  primary:
    "brand-gradient text-white shadow-xs hover:brightness-[1.06] active:brightness-95 " +
    "disabled:brightness-100 disabled:saturate-[.35]",
  secondary:
    "border border-gray-300 bg-surface text-gray-700 shadow-xs hover:bg-gray-50 " +
    "hover:border-gray-400 active:bg-gray-100",
  ghost: "text-gray-600 hover:bg-gray-100 hover:text-gray-900 active:bg-gray-200",
  danger:
    "border border-error-200 bg-surface text-error-700 shadow-xs hover:bg-error-50 " +
    "hover:border-error-300 active:bg-error-100",
  link: "px-0 text-brand-600 hover:text-brand-700 hover:underline underline-offset-2",
};

export function Button({
  children,
  variant = "primary",
  size = "md",
  loading = false,
  className,
  disabled,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
}) {
  return (
    <button
      {...props}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cn(
        "inline-flex select-none items-center justify-center whitespace-nowrap rounded-md font-semibold",
        "transition-[background-color,border-color,color,filter,box-shadow]",
        "disabled:cursor-not-allowed disabled:opacity-55",
        BUTTON_SIZE[size],
        BUTTON_VARIANT[variant],
        className,
      )}
    >
      {loading && <Spinner className="h-3.5 w-3.5" />}
      {children}
    </button>
  );
}

// --------------------------------------------------------------------------- //
//  Badge — status, not decoration
// --------------------------------------------------------------------------- //
type BadgeTone = "neutral" | "brand" | "success" | "warning" | "error" | "info";

const BADGE_TONE: Record<BadgeTone, string> = {
  neutral: "border-gray-200 bg-gray-50 text-gray-600",
  brand: "border-brand-200 bg-brand-50 text-brand-700",
  success: "border-success-200 bg-success-50 text-success-700",
  warning: "border-warning-200 bg-warning-50 text-warning-700",
  error: "border-error-200 bg-error-50 text-error-700",
  info: "border-blue-200 bg-blue-50 text-blue-700",
};

export function Badge({
  children,
  tone = "neutral",
  dot = false,
  className,
}: {
  children: ReactNode;
  tone?: BadgeTone;
  /** A status dot, for states that need to read at a glance in a table. */
  dot?: boolean;
  className?: string;
}) {
  const dotTone: Record<BadgeTone, string> = {
    neutral: "bg-gray-400",
    brand: "bg-brand-500",
    success: "bg-success-500",
    warning: "bg-warning-500",
    error: "bg-error-500",
    info: "bg-blue-500",
  };
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-1.5 rounded-sm border px-1.5 py-0.5",
        "text-2xs font-semibold leading-4",
        BADGE_TONE[tone],
        className,
      )}
    >
      {dot && <span className={cn("h-1.5 w-1.5 rounded-full", dotTone[tone])} />}
      {children}
    </span>
  );
}

// --------------------------------------------------------------------------- //
//  Callout
// --------------------------------------------------------------------------- //
export function Callout({
  tone = "info",
  title,
  icon,
  className,
  children,
}: {
  tone?: "info" | "warning" | "error" | "success";
  title?: string;
  icon?: ReactNode;
  /** So a callout can sit inside a card's spacing without a wrapper div. */
  className?: string;
  children: ReactNode;
}) {
  const tones = {
    info: "border-blue-200 bg-blue-25 text-blue-700",
    warning: "border-warning-200 bg-warning-25 text-warning-700",
    error: "border-error-200 bg-error-25 text-error-700",
    success: "border-success-200 bg-success-25 text-success-700",
  } as const;
  return (
    <div className={cn("rounded-md border px-3.5 py-3 text-sm", tones[tone], className)}>
      <div className="flex gap-2.5">
        {icon && <span className="mt-px shrink-0">{icon}</span>}
        <div className="min-w-0">
          {title && <p className="mb-0.5 font-semibold">{title}</p>}
          <div className="leading-relaxed">{children}</div>
        </div>
      </div>
    </div>
  );
}

export function Spinner({ className }: { className?: string }) {
  return (
    <svg
      className={cn("h-4 w-4 animate-spin", className)}
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden
    >
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="2.5" opacity=".2" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
    </svg>
  );
}

/** A labelled value — the atom every KPI and detail row is built from. */
export function Metric({
  label,
  value,
  hint,
  align = "left",
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  align?: "left" | "center";
}) {
  return (
    <div className={align === "center" ? "text-center" : undefined}>
      <p className="label">{label}</p>
      <p className="tabular mt-1 text-xl font-semibold leading-none text-gray-900">{value}</p>
      {hint && <p className="mt-1.5 text-xs text-gray-500">{hint}</p>}
    </div>
  );
}

/** A horizontal rule that doesn't shout. */
export function Divider({ className }: { className?: string }) {
  return <div className={cn("h-px bg-gray-200", className)} />;
}
