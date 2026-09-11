import type { ReactNode } from "react";
import { cn } from "../lib/cn";
import { Logo } from "@tara/ui/primitives";

/**
 * The candidate's page frame: one centred column, no navigation, no way out
 * except forward. Everything else on this screen is a shared primitive.
 */
export function Shell({
  children,
  right,
  wide = false,
}: {
  children: ReactNode;
  right?: ReactNode;
  wide?: boolean;
}) {
  return (
    <div className="flex min-h-full flex-col bg-canvas">
      <header className="border-b border-gray-200 bg-surface">
        <div
          className={cn(
            "mx-auto flex h-14 items-center justify-between px-6",
            wide ? "max-w-[1200px]" : "max-w-[760px]",
          )}
        >
          <Logo />
          {right}
        </div>
      </header>
      <main className="flex-1">
        <div className={cn("mx-auto px-6 py-10", wide ? "max-w-[1200px]" : "max-w-[760px]")}>
          {children}
        </div>
      </main>
    </div>
  );
}

export { Badge, Button, Callout, Divider, Logo, Metric, Spinner } from "@tara/ui/primitives";
