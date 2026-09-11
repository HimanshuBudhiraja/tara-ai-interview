import type { ReactNode } from "react";
import {
  FileText,
  LayoutGrid,
  LogOut,
  ShieldAlert,
} from "lucide-react";
import { href, type Route } from "../lib/route";
import { cn } from "../lib/cn";
import type { Session } from "../lib/auth";
import { Badge, Logo } from "@tara/ui/primitives";

/** What a role is called in the interface. The capability set behind it lives
 *  on the server; this is only a label. */
const ROLE_LABEL: Record<string, string> = {
  admin: "Administrator",
  recruiter: "Recruiter",
  viewer: "Read-only",
};

/**
 * The console's navigation. Two destinations, because the product has two:
 * you design and publish interviews, and you read what came back from them.
 *
 * The routes that used to be here — the interview list, the candidate list, the
 * question bank — have not been removed. They are reachable from inside those
 * two, which is where you are already standing when you want them: a candidate
 * from an interview, a question pool from its interview, a report from a
 * candidate. A sidebar entry for each was six ways into a product with two
 * jobs, and the deep links still work if anyone has one bookmarked.
 */
const NAV: { route: Route; label: string; icon: ReactNode; soon?: boolean }[] = [
  { route: { name: "dashboard" }, label: "AI Interview", icon: <LayoutGrid className="h-4 w-4" /> },
  { route: { name: "reports" }, label: "AI Interview Reports", icon: <FileText className="h-4 w-4" /> },
];

export function RecruiterShell({
  route,
  go,
  title,
  subtitle,
  actions,
  session,
  onSignOut,
  children,
}: {
  route: Route;
  go: (r: Route) => void;
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  /** Who is signed in. Shown, never trusted — the server re-checks every call. */
  session?: Session;
  onSignOut?: () => void;
  children: ReactNode;
}) {
  return (
    <div className="flex min-h-full bg-canvas">
      <nav className="sticky top-0 hidden h-screen w-[224px] shrink-0 flex-col border-r border-gray-200 bg-surface px-3 py-3.5 lg:flex">
        <div className="px-2 pb-5">
          <Logo subtitle="Talent Acquisition" />
        </div>

        <ul className="space-y-px">
          {NAV.map((entry) => {
            // Which of the two rows owns the screen you are on. Everything in
            // the design half — creating, reviewing, the question pool,
            // publishing, an interview's own workspace — lights the first row;
            // everything about a candidate lights the second.
            const active =
              entry.route.name === route.name ||
              (entry.route.name === "dashboard" &&
                ["interview-new", "recommended", "question-pool", "publish",
                 "interviews", "interview", "questions"].includes(route.name)) ||
              (entry.route.name === "reports" &&
                ["report", "session", "candidates", "compare", "results"].includes(
                  route.name));
            return (
              <li key={entry.label}>
                <a
                  href={href(entry.route)}
                  onClick={(e) => {
                    e.preventDefault();
                    go(entry.route);
                  }}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "group relative flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-base font-medium transition-colors",
                    active
                      ? "bg-gray-100 text-gray-900"
                      : "text-gray-600 hover:bg-gray-50 hover:text-gray-900",
                  )}
                >
                  {/* A 2px brand marker on the active row rather than a filled
                      tint — legible, and it doesn't compete with the content. */}
                  <span
                    className={cn(
                      "absolute inset-y-1.5 left-0 w-0.5 rounded-full transition-colors",
                      active ? "bg-brand-500" : "bg-transparent",
                    )}
                  />
                  <span className={cn(active ? "text-brand-600" : "text-gray-400")}>
                    {entry.icon}
                  </span>
                  {entry.label}
                  {entry.soon && (
                    <span className="ml-auto rounded-xs border border-gray-200 bg-gray-50 px-1 py-px text-2xs font-semibold uppercase tracking-wide text-gray-400">
                      Soon
                    </span>
                  )}
                </a>
              </li>
            );
          })}
        </ul>

        {session ? (
          <div className="mt-auto rounded-md border border-gray-200 bg-gray-25 p-2.5">
            <p className="truncate text-xs font-semibold text-gray-800" title={session.user.email}>
              {session.user.email}
            </p>
            {/* The organization is named because everything on every screen is
                scoped to it — a recruiter with two accounts should never have to
                guess whose candidates they are reading. */}
            <p className="mt-0.5 truncate text-2xs text-gray-500">
              {session.organization.name} · {ROLE_LABEL[session.user.role] ?? session.user.role}
            </p>
            {onSignOut && (
              <button
                type="button"
                onClick={onSignOut}
                className="mt-2 flex items-center gap-1.5 text-xs font-medium text-gray-600 transition-colors hover:text-gray-900"
              >
                <LogOut className="h-3.5 w-3.5" />
                Sign out
              </button>
            )}
          </div>
        ) : (
          <div className="mt-auto rounded-md border border-warning-200 bg-warning-25 p-2.5">
            <p className="flex items-center gap-1.5 text-2xs font-semibold uppercase tracking-wider text-warning-700">
              <ShieldAlert className="h-3.5 w-3.5" />
              Not signed in
            </p>
            <p className="mt-1 text-xs leading-relaxed text-warning-700">
              This console is rendering without a session. Every request it makes will be refused.
            </p>
          </div>
        )}
      </nav>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 border-b border-gray-200 bg-surface/95 px-6 py-4 backdrop-blur-sm lg:px-7">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="min-w-0">
              <h1 className="truncate text-xl font-semibold tracking-tight text-gray-900">
                {title}
              </h1>
              {subtitle && <p className="mt-0.5 truncate text-sm text-gray-500">{subtitle}</p>}
            </div>
            {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
          </div>
        </header>
        <main className="flex-1 px-6 py-5 lg:px-7">{children}</main>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- //
//  Status vocabulary — one mapping, used everywhere
// --------------------------------------------------------------------------- //
const STATUS: Record<string, { tone: "neutral" | "brand" | "success" | "error"; label: string }> = {
  published: { tone: "success", label: "Published" },
  draft: { tone: "neutral", label: "Draft" },
  complete: { tone: "success", label: "Complete" },
  in_progress: { tone: "brand", label: "In progress" },
  pending: { tone: "neutral", label: "Not started" },
  expired: { tone: "error", label: "Expired" },
};

export function StatusPill({ status }: { status: string }) {
  const s = STATUS[status] ?? { tone: "neutral" as const, label: status };
  return (
    <Badge tone={s.tone} dot>
      {s.label}
    </Badge>
  );
}

const DIFFICULTY: Record<string, string> = {
  easy: "text-blue-700 bg-blue-50 border-blue-200",
  medium: "text-warning-700 bg-warning-50 border-warning-200",
  hard: "text-error-700 bg-error-50 border-error-200",
};

export function DifficultyPill({ level }: { level: string }) {
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center rounded-xs border px-1 py-px text-2xs font-semibold capitalize leading-4",
        DIFFICULTY[level] ?? DIFFICULTY.medium,
      )}
    >
      {level}
    </span>
  );
}

/**
 * A KPI tile.
 *
 * Enterprise dashboard, not marketing tile: the caption is small and muted, the
 * number carries the weight, and the accent is a hairline rather than a fill.
 */
export function Stat({
  label,
  value,
  hint,
  accent = false,
}: {
  label: string;
  value: ReactNode;
  hint?: string;
  accent?: boolean;
}) {
  return (
    <div className="card relative overflow-hidden p-4">
      {accent && <span className="brand-gradient absolute inset-x-0 top-0 h-0.5" />}
      <p className="label">{label}</p>
      <p className="tabular mt-1.5 text-stat font-semibold text-gray-900">{value}</p>
      {hint && <p className="mt-1 text-xs text-gray-500">{hint}</p>}
    </div>
  );
}

export function Empty({
  title,
  icon,
  children,
  action,
}: {
  title: string;
  icon?: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="card grid place-items-center px-6 py-12 text-center">
      {icon && (
        <div className="mb-3 grid h-9 w-9 place-items-center rounded-md border border-gray-200 bg-gray-50 text-gray-400">
          {icon}
        </div>
      )}
      <p className="text-md font-semibold text-gray-900">{title}</p>
      {children && <div className="mt-1 max-w-[56ch] text-sm text-gray-500">{children}</div>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

/** A section header inside a page — one step down from the page title. */
export function SectionHeading({
  title,
  hint,
  action,
}: {
  title: string;
  hint?: string;
  action?: ReactNode;
}) {
  return (
    <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
      <div>
        <h2 className="text-md font-semibold text-gray-900">{title}</h2>
        {hint && <p className="mt-0.5 text-sm text-gray-500">{hint}</p>}
      </div>
      {action}
    </div>
  );
}

export function timeAgo(ts: number | null | undefined): string {
  if (!ts) return "—";
  const secs = Math.max(0, Date.now() / 1000 - ts);
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

export function mmss(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}
