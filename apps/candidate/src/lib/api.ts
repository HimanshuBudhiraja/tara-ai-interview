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

  start: (
    token: string,
    accommodations: Record<string, unknown>,
    preferredName = "",
  ) =>
    request<{ session_id: string; resumed: boolean; reply: Reply }>("/api/session/start", {
      method: "POST",
      body: JSON.stringify({
        token,
        consent_recording: true,
        accommodations,
        preferred_name: preferredName,
      }),
    }),

  session: (id: string) => request<SessionSnapshot>(`/api/session/${id}`),

  /**
   * Mint a Retell web call for this session.
   *
   * Returns only an access token — never the account's API key. The key stays
   * on the server, which is the whole reason this is a round trip rather than
   * the browser calling Retell directly.
   */
  /** The v3 connection details for a call the server has already created. */
  startVoiceCall: (sessionId: string) =>
    request<VoiceCall>(
      `/api/session/${encodeURIComponent(sessionId)}/voice`,
      { method: "POST" },
    ),

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

/** What the server returns from /voice: Retell v3's answer, passed straight to the browser client. */
export interface VoiceCall {
  call_id: string;
  access_token: string;
  transport?: string;
  url?: string;
  ice_servers?: unknown[];
  expires_at?: number;
}
