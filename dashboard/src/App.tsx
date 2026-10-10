import { useState } from "react";
import { writePref } from "./hooks";
import FailureExplorer from "./pages/FailureExplorer";
import History from "./pages/History";
import Overview from "./pages/Overview";
import TestLab from "./pages/TestLab";
import { href, useRoute } from "./router";

const PAGES = [
  { id: "overview", label: "Overview" },
  { id: "lab", label: "Test lab" },
  { id: "failures", label: "Failure explorer" },
  { id: "history", label: "Regression history" },
];

function Mark() {
  return (
    <svg viewBox="0 0 32 32" aria-hidden="true">
      <rect width="32" height="32" rx="7" fill="var(--ink)" />
      <rect x="6" y="9" width="4" height="14" rx="1" fill="var(--pass)" />
      <rect x="12" y="9" width="4" height="14" rx="1" fill="var(--pass)" />
      <rect x="18" y="9" width="4" height="14" rx="1" fill="var(--fail)" />
      <rect x="24" y="9" width="2" height="14" rx="1" fill="var(--pass)" />
    </svg>
  );
}

function ThemeToggle() {
  const [theme, setTheme] = useState<string>(document.documentElement.dataset.theme || "system");
  const next = theme === "system" ? "dark" : theme === "dark" ? "light" : "system";
  const apply = () => {
    if (next === "system") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = next;
    writePref("agentbench-theme", next === "system" ? null : next);
    setTheme(next);
  };
  const names: Record<string, string> = { system: "System theme", dark: "Dark theme", light: "Light theme" };
  return (
    <button type="button" onClick={apply} aria-label={`Theme: ${names[theme]}. Switch to ${names[next]}`}>
      {names[theme]}
    </button>
  );
}

export default function App() {
  const route = useRoute();
  const page = PAGES.some((p) => p.id === route.page) ? route.page : "overview";
  return (
    <div className="shell">
      <aside className="rail">
        <a className="wordmark" href={href("overview")} style={{ color: "inherit", textDecoration: "none" }}>
          <Mark />
          AgentBench
        </a>
        <nav aria-label="Primary">
          {PAGES.map((p) => (
            <a key={p.id} href={href(p.id)} aria-current={page === p.id ? "page" : undefined}>
              {p.label}
            </a>
          ))}
        </nav>
        <div className="foot">
          <ThemeToggle />
          <a href="/docs" target="_blank" rel="noreferrer">
            API reference
          </a>
        </div>
      </aside>
      <main>
        {page === "overview" && <Overview params={route.params} />}
        {page === "lab" && <TestLab />}
        {page === "failures" && <FailureExplorer params={route.params} />}
        {page === "history" && <History params={route.params} />}
      </main>
    </div>
  );
}
