import { ApiError } from "./api";

/**
 * Who is signed in, from the server's answer — never from local state.
 *
 * The console does not decide what a recruiter may see; the backend does, and
 * every request is authorised on its own. What this module is for is showing the
 * right screen: a sign-in form when there is no session, the console when there
 * is, and the sign-in form again the moment the server says the session is gone.
 *
 * So nothing here is a security control, and it deliberately stores nothing.
 * There is no token in `localStorage` to steal, because the session lives in an
 * HttpOnly cookie that this code cannot read. Hiding a route is not security
 * (the tests in `tests/test_security.py` are), and treating it as security is
 * how a console ends up with a bypass in a query string.
 */
export interface Principal {
  user_id: string;
  email: string;
  role: "admin" | "recruiter" | "viewer";
  organization_id: string;
  capabilities: string[];
}

export interface Session {
  user: Principal;
  organization: { organization_id: string; name: string };
}

/** Fired when any request comes back 401, so the app can show sign-in again. */
export const SESSION_LOST = "tara:session-lost";

export function announceSessionLost(): void {
  window.dispatchEvent(new Event(SESSION_LOST));
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      ...init,
      // Explicit rather than relying on the default: the cookie is the session,
      // and a build served from another origin would silently stop sending it.
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError("Can't reach the server. Is the backend running on :8000?", 0);
  }
  if (!res.ok) {
    let message = `Request failed (${res.status}).`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") message = body.detail;
    } catch {
      /* keep the fallback */
    }
    throw new ApiError(message, res.status);
  }
  return res.json() as Promise<T>;
}

export const auth = {
  /** The current session, or null when there is none. Only 401 means "none". */
  async me(): Promise<Session | null> {
    try {
      return await call<Session>("/api/auth/me");
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) return null;
      throw err;
    }
  },

  login(email: string, password: string): Promise<Session> {
    return call<Session>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
  },

  /** Always resolves. Signing out must not depend on being signed in. */
  async logout(): Promise<void> {
    try {
      await call<{ signed_out: boolean }>("/api/auth/logout", { method: "POST" });
    } catch {
      /* the cookie is cleared server-side either way */
    }
  },
};
