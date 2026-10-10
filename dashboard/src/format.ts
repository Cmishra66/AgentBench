export const pct = (v: number | null | undefined, digits = 1): string =>
  v === null || v === undefined ? "–" : `${(v * 100).toFixed(digits)}%`;

export const ms = (v: number | null | undefined): string => {
  if (v === null || v === undefined) return "–";
  return v >= 1000 ? `${(v / 1000).toFixed(2)}s` : `${Math.round(v)}ms`;
};

export const usd = (v: number | null | undefined): string => {
  if (v === null || v === undefined) return "–";
  return v < 1 ? `$${v.toFixed(4)}` : `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
};

export const num = (v: number | null | undefined): string =>
  v === null || v === undefined ? "–" : v.toLocaleString();

export const when = (iso: string): string => {
  if (!iso) return "–";
  const d = new Date(iso);
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
};

export const fmtMetric = (metric: string, v: number | null | undefined): string => {
  if (v === null || v === undefined) return "–";
  if (metric.includes("latency") || metric.endsWith("_ms") || /\.p\d\d$/.test(metric)) return ms(v);
  if (metric.includes("usd") || metric.includes("cost")) return usd(v);
  if (v >= 0 && v <= 1 && /(rate|accuracy|f1|valid)/.test(metric)) return pct(v);
  return Number.isInteger(v) ? String(v) : v.toFixed(3);
};

export const titleCase = (s: string): string => s.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());

export const SUITE_TITLES: Record<string, string> = {
  core: "Accuracy",
  injection: "Prompt injection",
  tool_use: "Tool use",
  isolation: "Context isolation",
  load: "Load and performance",
};

export const suiteTitle = (s: string): string => SUITE_TITLES[s] ?? titleCase(s);

export const SUITE_ORDER = ["core", "injection", "tool_use", "isolation", "load"];

export const sortSuites = (names: string[]): string[] =>
  [...names].sort((a, b) => {
    const ia = SUITE_ORDER.indexOf(a);
    const ib = SUITE_ORDER.indexOf(b);
    return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
  });
