import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RecruiterApp } from "./RecruiterApp";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <RecruiterApp />
  </StrictMode>,
);
