import type { Invite, Reply, SessionSnapshot } from "./types";

/** Raised with a message already fit to show a candidate. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError("We couldn't reach the interview server. Check your connection.", 0);
  }
  if (!res.ok) {
    // FastAPI puts human-readable copy in `detail`; fall back to something
    // a candidate can act on rather than a status code.
    let detail = "Something went wrong on our side.";
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      /* keep the fallback */
    }
    throw new ApiError(detail, res.status);
  }
  return res.json() as Promise<T>;
}

export const api = {
  invite: (token: string) => request<Invite>(`/api/invite/${encodeURIComponent(token)}`),

  start: (token: string, accommodations: Record<string, unknown>) =>
    request<{ session_id: string; resumed: boolean; reply: Reply }>("/api/session/start", {
      method: "POST",
      body: JSON.stringify({
        token,
        consent_recording: true,
        accommodations,
      }),
    }),

  session: (id: string) => request<SessionSnapshot>(`/api/session/${id}`),

  /** HTTP turn — used when the socket is unavailable, and by the test driver. */
  turn: (id: string, body: { said?: string; action?: "silence" | "repeat" | "end" }) =>
    request<{ reply: Reply }>(`/api/session/${id}/turn`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
};

export function socketUrl(sessionId: string): string {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}/ws/interview/${sessionId}`;
}
