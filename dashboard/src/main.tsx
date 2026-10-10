import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { readPref } from "./hooks";
import "./styles.css";

const theme = readPref("agentbench-theme");
if (theme === "light" || theme === "dark") document.documentElement.dataset.theme = theme;

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
