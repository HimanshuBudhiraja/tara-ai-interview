import { useCallback, useEffect, useState } from "react";

/**
 * A router in a hundred lines, because the console has a dozen routes.
 *
 * Pulling in react-router for `/recruiter/interviews/:id` would be more
 * machinery than the thing it routes. If this grows past this list, swap it —
 * the surface (`useRoute`, `navigate`, `href`) is the shape a real router
 * exposes, so the change stays inside this file.
 *
 * The route tree is the FULL product's tree, not just what is built today.
 * Routes whose screens land in a later phase resolve to an honest placeholder
 * that says what will be there, rather than a 404 or a fake screen — see
 * `Placeholder.tsx`. Establishing the tree now is what stops the next four
 * phases each inventing their own URL shape.
 */

export const BASE = "/recruiter";

/** An interview is a container: its own tabs. */
export type InterviewTab =
  | "configure"
  | "review"
  | "candidates"
  | "results"
  | "fairness"
  | "versions";

export type Route =
  | { name: "dashboard" }
  | { name: "interviews" }
  | { name: "interview-new" }
  | { name: "recommended"; id: string }
  | { name: "question-pool"; id: string }
  | { name: "publish"; id: string }
  | { name: "interview"; id: string; tab: InterviewTab }
  | { name: "candidates" }
  | { name: "compare"; ids: string[] }
  | { name: "session"; id: string }
  | { name: "questions" }
  | { name: "results" }
  | { name: "reports" }
  | { name: "report"; id: string };

const TABS: InterviewTab[] = [
  "configure",
  "review",
  "candidates",
  "results",
  "fairness",
  "versions",
];

export function parse(pathname: string): Route {
  const parts = pathname
    .replace(/^\/recruiter\/?/, "")
    // `/admin` was this console's address before the two apps split. The server
    // 308s it here, but a bookmarked deep link can still arrive spelled that
    // way for as long as a browser remembers it.
    .replace(/^\/admin\/?/, "")
    .split("/")
    .filter(Boolean);

  if (parts.length === 0) return { name: "dashboard" };

  if (parts[0] === "interviews") {
    if (parts[1] === "new") return { name: "interview-new" };
    if (parts[1] && parts[2] === "recommended") return { name: "recommended", id: parts[1] };
    if (parts[1] && parts[2] === "questions") return { name: "question-pool", id: parts[1] };
    if (parts[1] && parts[2] === "publish") return { name: "publish", id: parts[1] };
    if (parts[1]) {
      const tab = (TABS as string[]).includes(parts[2] ?? "")
        ? (parts[2] as InterviewTab)
        : "configure";
      return { name: "interview", id: parts[1], tab };
    }
    return { name: "interviews" };
  }
  if (parts[0] === "candidates") return { name: "candidates" };
  if (parts[0] === "questions") return { name: "questions" };
  if (parts[0] === "results") return { name: "results" };
  if (parts[0] === "reports") {
    return parts[1] ? { name: "report", id: parts[1] } : { name: "reports" };
  }
  if (parts[0] === "compare") {
    const ids = new URLSearchParams(location.search).get("ids");
    return { name: "compare", ids: ids ? ids.split(",").filter(Boolean) : [] };
  }
  if (parts[0] === "sessions" && parts[1]) return { name: "session", id: parts[1] };
  return { name: "dashboard" };
}

export function href(route: Route): string {
  switch (route.name) {
    case "dashboard":
      return BASE;
    case "interviews":
      return `${BASE}/interviews`;
    case "interview-new":
      return `${BASE}/interviews/new`;
    case "recommended":
      return `${BASE}/interviews/${route.id}/recommended`;
    case "question-pool":
      return `${BASE}/interviews/${route.id}/questions`;
    case "publish":
      return `${BASE}/interviews/${route.id}/publish`;
    case "interview":
      return `${BASE}/interviews/${route.id}/${route.tab}`;
    case "candidates":
      return `${BASE}/candidates`;
    case "compare":
      return `${BASE}/compare?ids=${route.ids.join(",")}`;
    case "questions":
      return `${BASE}/questions`;
    case "results":
      return `${BASE}/results`;
    case "reports":
      return `${BASE}/reports`;
    case "report":
      return `${BASE}/reports/${route.id}`;
    case "session":
      return `${BASE}/sessions/${route.id}`;
  }
}

export function navigate(route: Route) {
  history.pushState({}, "", href(route));
  window.dispatchEvent(new PopStateEvent("popstate"));
}

export function useRoute(): [Route, (r: Route) => void] {
  const [route, setRoute] = useState<Route>(() => parse(location.pathname));

  useEffect(() => {
    const onPop = () => setRoute(parse(location.pathname));
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  return [route, useCallback((r: Route) => navigate(r), [])];
}
