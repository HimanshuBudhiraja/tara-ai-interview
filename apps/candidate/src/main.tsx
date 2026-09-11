import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./index.css";

/**
 * The candidate experience, on its own.
 *
 * It used to share a bundle with the recruiter console and split on the path.
 * They are separate apps now, which means the console's screens, its API client
 * and its route tree are not shipped to a candidate's browser at all — the
 * boundary is a build artefact rather than an `if` statement.
 */
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
