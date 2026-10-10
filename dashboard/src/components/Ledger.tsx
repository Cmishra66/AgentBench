import { api } from "../api";
import { useAsync } from "../hooks";
import { navigate } from "../router";

const STATUS_TEXT: Record<string, string> = {
  passed: "passed",
  failed: "failed",
  error: "errored",
  flaky: "mixed across repeats",
  skipped: "skipped",
};

/** The case ledger: every case (column) for every target (row), coloured by outcome. */
export function Ledger({ runId, suite, title, targets }: { runId: string; suite: string; title: string; targets: string[] }) {
  const { data, error, loading } = useAsync(() => api.matrix(runId, suite), [runId, suite]);
  if (error) return <p className="note">Could not load {title}: {error}</p>;
  if (loading || !data) return <div className="ledger-suite" aria-busy="true"><h3>{title}</h3><p className="note">Loading…</p></div>;
  const n = data.cases.length;
  const cw = n <= 30 ? 26 : 12;
  const cols = { gridTemplateColumns: `repeat(${n}, minmax(0, ${cw}px))` };
  const groups: { name: string; span: number }[] = [];
  for (const c of data.cases) {
    const last = groups[groups.length - 1];
    if (last && last.name === c.category) last.span += 1;
    else groups.push({ name: c.category, span: 1 });
  }
  return (
    <div className="ledger-suite">
      <h3>{title}</h3>
      <div className="ledger-row" aria-hidden="true">
        <div />
        <div className="cells cats" style={cols}>
          {groups.map((g) => (
            <span key={g.name} style={{ gridColumn: `span ${g.span}` }} title={g.name}>
              {g.name.replace(/^injection_/, "").replace(/_/g, " ")}
            </span>
          ))}
        </div>
      </div>
      {targets.map((t) => {
        const failing = data.cases.filter((c) => {
          const cell = data.cells[`${t}|${c.id}`];
          return cell && cell.status !== "passed";
        }).length;
        return (
          <div className="ledger-row" key={t}>
            <div className="t" title={t}>
              {t}
            </div>
            <div className="cells" style={cols} role="list" aria-label={`${t}: ${n - failing} of ${n} passing`}>
              {data.cases.map((c) => {
                const cell = data.cells[`${t}|${c.id}`];
                const status = cell?.status ?? "none";
                const label = `${c.id} (${c.category.replace(/_/g, " ")}) ${STATUS_TEXT[status] ?? "not run"}`;
                if (!cell || status === "passed" || status === "skipped") {
                  return <span key={c.id} role="listitem" className={`cell ${status}`} title={label} />;
                }
                return (
                  <button
                    type="button"
                    role="listitem"
                    key={c.id}
                    className={`cell ${status}`}
                    title={label}
                    aria-label={label}
                    onClick={() => navigate("failures", { run: runId, suite, target: t, result: cell.result_id })}
                  />
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function LedgerLegend() {
  return (
    <div className="legend" aria-label="Legend">
      <span><i style={{ background: "var(--pass)" }} />Passed</span>
      <span><i style={{ background: "var(--fail)" }} />Failed an assertion</span>
      <span><i style={{ background: "var(--error)" }} />Errored (timeout or provider failure)</span>
      <span><i style={{ background: "var(--flaky)" }} />Mixed across repeats</span>
      <span><i style={{ background: "var(--skip)" }} />Skipped</span>
    </div>
  );
}
