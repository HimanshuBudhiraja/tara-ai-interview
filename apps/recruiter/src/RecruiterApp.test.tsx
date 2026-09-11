import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RecruiterApp } from "./RecruiterApp";
import { SESSION_LOST } from "./lib/auth";

/**
 * The console's door.
 *
 * What these tests are and are not: they check that the console shows the right
 * screen for the session the SERVER reports, and that it never decides on its
 * own that somebody is signed in. They are not a security control — hiding a
 * screen protects nothing, and `tests/test_security.py` is where the actual
 * authorization is proved. Both halves are needed: the backend refuses, and the
 * console must not present a shell full of empty tables when it does.
 */

const SESSION = {
  user: {
    user_id: "usr_abc123",
    email: "recruiter@acme.test",
    role: "recruiter" as const,
    organization_id: "org_acme",
    capabilities: ["read", "write", "publish", "invite"],
  },
  organization: { organization_id: "org_acme", name: "Acme Hiring" },
};

/** Answers `/api/auth/*` and gives everything else an empty-but-valid shape. */
function stubServer(opts: { signedIn?: boolean; loginFails?: number } = {}) {
  const state = { signedIn: opts.signedIn ?? false, loginFails: opts.loginFails ?? 0 };
  const calls: string[] = [];
  const json = (body: unknown, status = 200) =>
    Promise.resolve(new Response(JSON.stringify(body), {
      status, headers: { "Content-Type": "application/json" },
    }));

  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push(`${init?.method ?? "GET"} ${url}`);
    if (url === "/api/auth/me") {
      return state.signedIn ? json(SESSION) : json({ detail: "Sign in." }, 401);
    }
    if (url === "/api/auth/login") {
      if (state.loginFails > 0) {
        state.loginFails -= 1;
        return json({ detail: "Email or password is incorrect." }, 401);
      }
      state.signedIn = true;
      return json(SESSION);
    }
    if (url === "/api/auth/logout") {
      state.signedIn = false;
      return json({ signed_out: true });
    }
    if (!state.signedIn) return json({ detail: "Sign in." }, 401);
    return json({ interviews: [], total: 0, candidates: [], rows: [] });
  }));
  return { state, calls };
}

beforeEach(() => {
  window.history.pushState({}, "", "/recruiter");
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("the console's session gate", () => {
  it("asks the server who is signed in before rendering anything", async () => {
    const server = stubServer({ signedIn: false });
    render(<RecruiterApp />);
    await waitFor(() => expect(screen.getByRole("heading", {
      name: /sign in to the console/i,
    })).toBeInTheDocument());
    expect(server.calls).toContain("GET /api/auth/me");
  });

  it("shows the sign-in form, not an empty console, when there is no session", async () => {
    stubServer({ signedIn: false });
    render(<RecruiterApp />);
    await waitFor(() => expect(screen.getByLabelText(/work email/i)).toBeInTheDocument());
    // None of the console's navigation is rendered behind it.
    expect(screen.queryByRole("link", { name: /candidates/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /question bank/i })).not.toBeInTheDocument();
  });

  it("renders the console, the signed-in address and the organization once signed in", async () => {
    stubServer({ signedIn: true });
    render(<RecruiterApp />);
    await waitFor(() => expect(screen.getByText("recruiter@acme.test")).toBeInTheDocument());
    expect(screen.getByText(/Acme Hiring/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/work email/i)).not.toBeInTheDocument();
  });

  it("signs in from the form and does not put the password anywhere but the request", async () => {
    const server = stubServer({ signedIn: false });
    const stored = vi.spyOn(Storage.prototype, "setItem");
    render(<RecruiterApp />);
    await waitFor(() => screen.getByLabelText(/work email/i));

    await userEvent.type(screen.getByLabelText(/work email/i), "recruiter@acme.test");
    await userEvent.type(screen.getByLabelText(/^password$/i), "test-password-1234");
    await userEvent.click(screen.getByRole("button", { name: /^sign in$/i }));

    await waitFor(() => expect(screen.getByText("recruiter@acme.test")).toBeInTheDocument());
    expect(server.calls).toContain("POST /api/auth/login");
    // Nothing cached it. The session is an HttpOnly cookie; there is no token
    // in browser storage for a later XSS to read, and the credential is not
    // left in the DOM after the form is replaced.
    expect(stored).not.toHaveBeenCalled();
    expect(document.cookie).not.toContain("test-password-1234");
    expect(document.body.innerHTML).not.toContain("test-password-1234");
    expect(document.body.innerHTML).not.toContain("usr_abc123");
  });

  it("says the same thing for a wrong password as for an unknown address", async () => {
    stubServer({ signedIn: false, loginFails: 1 });
    render(<RecruiterApp />);
    await waitFor(() => screen.getByLabelText(/work email/i));

    await userEvent.type(screen.getByLabelText(/work email/i), "nobody@nowhere.test");
    await userEvent.type(screen.getByLabelText(/^password$/i), "wrong-password-x");
    await userEvent.click(screen.getByRole("button", { name: /^sign in$/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/don't match an account/i);
    // Nothing in the message distinguishes "no such user" from "wrong password".
    expect(alert.textContent).not.toMatch(/unknown|no such|not found|disabled/i);
    // And the field is cleared rather than left holding the attempt.
    expect(screen.getByLabelText(/^password$/i)).toHaveValue("");
  });

  it("returns to sign-in when the server says the session has ended", async () => {
    stubServer({ signedIn: true });
    render(<RecruiterApp />);
    await waitFor(() => screen.getByText("recruiter@acme.test"));

    // What a 401 from any request does — expiry, revocation, a disabled account.
    window.dispatchEvent(new Event(SESSION_LOST));

    await waitFor(() => expect(screen.getByRole("heading", {
      name: /sign in to the console/i,
    })).toBeInTheDocument());
    expect(screen.queryByText("recruiter@acme.test")).not.toBeInTheDocument();
  });

  it("signs out through the server, not by forgetting locally", async () => {
    const server = stubServer({ signedIn: true });
    render(<RecruiterApp />);
    await waitFor(() => screen.getByText("recruiter@acme.test"));

    await userEvent.click(screen.getByRole("button", { name: /sign out/i }));

    await waitFor(() => expect(screen.getByLabelText(/work email/i)).toBeInTheDocument());
    expect(server.calls).toContain("POST /api/auth/logout");
    expect(server.state.signedIn).toBe(false);
  });

  it("sends the session cookie with every request it makes", async () => {
    stubServer({ signedIn: true });
    render(<RecruiterApp />);
    await waitFor(() => screen.getByText("recruiter@acme.test"));

    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    expect(fetchMock.mock.calls.length).toBeGreaterThan(0);
    for (const [, init] of fetchMock.mock.calls) {
      expect((init as RequestInit | undefined)?.credentials).toBe("same-origin");
    }
  });
});
