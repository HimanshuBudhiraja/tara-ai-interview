import { useState } from "react";
import { ApiError } from "../lib/api";
import { auth, type Session } from "../lib/auth";
import { Logo } from "@tara/ui/primitives";

/**
 * The door.
 *
 * One deliberate omission: nothing here tells the visitor whether an address is
 * known. A wrong password and an address that has never existed produce the
 * identical message, because the difference is the first thing an attacker
 * would like to learn. The server answers them identically too — this screen
 * just doesn't undo that.
 */
export function SignIn({ onSignedIn }: { onSignedIn: (session: Session) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      onSignedIn(await auth.login(email.trim(), password));
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 429
          ? "Too many attempts. Wait a minute and try again."
          : err instanceof ApiError && err.status === 0
            ? err.message
            : "That email and password don't match an account.",
      );
      setPassword("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-full items-center justify-center bg-canvas px-6 py-16">
      <div className="w-full max-w-[380px]">
        <div className="mb-7">
          <Logo subtitle="Talent Acquisition" />
        </div>

        <div className="rounded-lg border border-gray-200 bg-surface p-6 shadow-xs">
          <h1 className="text-lg font-semibold tracking-tight text-gray-900">
            Sign in to the console
          </h1>
          <p className="mt-1 text-sm text-gray-500">
            Interviews, candidates and evaluation reports for your organization.
          </p>

          <form className="mt-5 space-y-4" onSubmit={submit}>
            <label className="block">
              <span className="text-sm font-medium text-gray-700">Work email</span>
              <input
                type="email"
                name="email"
                autoComplete="username"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-base text-gray-900 outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
              />
            </label>

            <label className="block">
              <span className="text-sm font-medium text-gray-700">Password</span>
              <input
                type="password"
                name="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-base text-gray-900 outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
              />
            </label>

            {error && (
              <p role="alert" className="rounded-md border border-error-200 bg-error-25 px-3 py-2 text-sm text-error-700">
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={busy || !email.trim() || !password}
              className="w-full rounded-md bg-brand-600 px-3 py-2 text-base font-semibold text-white transition-colors hover:bg-brand-700 disabled:cursor-not-allowed disabled:bg-gray-200 disabled:text-gray-400"
            >
              {busy ? "Signing in…" : "Sign in"}
            </button>
          </form>
        </div>

        <p className="mt-4 text-center text-xs leading-relaxed text-gray-500">
          Accounts are created by an administrator with{" "}
          <code className="rounded-xs bg-gray-100 px-1 py-px">python -m tools.make_user</code>.
        </p>
      </div>
    </div>
  );
}
