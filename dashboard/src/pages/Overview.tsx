import { api, type RunDetail, type SuiteBlock } from "../api";
import { BarCI, LineChart } from "../components/charts";
import { Ledger, LedgerLegend } from "../components/Ledger";
import { BreakdownTable, SuiteMetrics } from "../components/SuiteMetrics";
import { ms, pct, sortSuites, suiteTitle, usd, when } from "../format";
import { useAsync } from "../hooks";
import { href, navigate } from "../router";

export function RunPicker({ value, onChange, includeRunning = false }: { value: string | null; onChange: (id: string) => void; includeRunning?: boolean }) {
  const { data } = useAsync(() => api.runs(100), []);
  const runs = (data ?? []).filter((r) => includeRunning || r.status !== "running");
  return (
    <label className="field">
      Run
      <select value={value ?? ""} onChange={(e) => onChange(e.target.value)} style={{ minWidth: 300 }}>
        {!value && <option value="">Choose a run</option>}
        {runs.map((r) => (
          <option key={r.id} value={r.id}>
            {r.label || "unlabelled"} ({when(r.created_at)}, {r.totals.passed ?? 0}/{r.totals.executed ?? 0} passed)
          </option>
        ))}
      </select>
    </label>
  );
}

function bySuite(run: RunDetail): Record<string, Record<string, SuiteBlock>> {
  const out: Record<string, Record<string, SuiteBlock>> = {};
  for (const [target, suites] of Object.entries(run.summary.targets ?? {})) {
    for (const [suite, block] of Object.entries(suites)) {
      (out[suite] ??= {})[target] = block;
    }
  }
  return out;
}

function LoadSection({ blocks }: { blocks: Record<string, SuiteBlock> }) {
  return (
    <>
      {Object.entries(blocks).map(([t, b]) => (
        <div key={t}>
          <h3 className="mono">{t}</h3>
          <div className="scroll">
            <table>
              <thead>
                <tr>
                  <th>Stage</th>
                  <th className="num">Virtual users</th>
                  <th className="num">Requests</th>
                  <th className="num">Throughput</th>
                  <th className="num">p50</th>
                  <th className="num">p95</th>
                  <th className="num">p99</th>
                  <th className="num">Errors</th>
                  <th className="num">Pass rate</th>
                </tr>
              </thead>
              <tbody>
                {(b.stages ?? []).map((s: SuiteBlock) => (
                  <tr key={s.stage}>
                    <td>{s.stage}</td>
                    <td className="num">{s.users}</td>
                    <td className="num">{s.requests}</td>
                    <td className="num">{s.throughput_rps} rps</td>
                    <td className="num">{ms(s.latency_ms?.p50)}</td>
                    <td className="num">{ms(s.latency_ms?.p95)}</td>
                    <td className="num">{ms(s.latency_ms?.p99)}</td>
                    <td className="num">{pct(s.error_rate)}</td>
                    <td className="num">{pct(s.accuracy)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="note">
            {b.total_requests} requests in {b.wall_time_s}s, {b.achieved_rps} rps on average and {b.peak_rps} at peak.
            {b.accuracy_drop_under_load_pp != null && ` Pass rate fell ${b.accuracy_drop_under_load_pp}pp from the first stage to the worst stage.`}
          </p>
          <div className="charts-3">
            <div>
              <h3>Completed requests per second</h3>
              <LineChart
                title="Requests"
                points={(b.timeline ?? []).map((p: SuiteBlock) => ({ x: `${p.t}s`, y: p.requests }))}
                format={(v) => (Number.isInteger(v) ? String(v) : v.toFixed(1))}
              />
            </div>
            <div>
              <h3>p95 latency per second</h3>
              <LineChart
                title="p95"
                points={(b.timeline ?? []).map((p: SuiteBlock) => ({ x: `${p.t}s`, y: p.p95_ms }))}
                format={(v) => ms(v)}
              />
            </div>
            <div>
              <h3>Errors per second</h3>
              <LineChart
                title="Errors"
                points={(b.timeline ?? []).map((p: SuiteBlock) => ({ x: `${p.t}s`, y: p.errors }))}
                format={(v) => (Number.isInteger(v) ? String(v) : v.toFixed(1))}
              />
            </div>
          </div>
        </div>
      ))}
    </>
  );
}

export default function Overview({ params }: { params: URLSearchParams }) {
  const runs = useAsync(() => api.runs(100), []);
  const latest = (runs.data ?? []).find((r) => r.status !== "running" && !r.label.startsWith("load"))
    ?? (runs.data ?? []).find((r) => r.status !== "running");
  const runId = params.get("run") || latest?.id || null;
  const run = useAsync(() => (runId ? api.run(runId) : Promise.resolve(null)), [runId]);

  if (runs.data && runs.data.length === 0) {
    return (
      <div className="panel empty">
        <h2>No runs yet</h2>
        <p className="lede" style={{ margin: "8px auto" }}>
          Start one from the test lab, or run <code>agentbench demo</code> in a terminal.
        </p>
        <a className="btn primary" href={href("lab")}>Open the test lab</a>
      </div>
    );
  }
  const r = run.data;
  const suites = r ? bySuite(r) : {};
  const ordered = sortSuites(Object.keys(suites));
  const totals = r?.summary.totals;
  return (
    <>
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 14, alignItems: "flex-end" }}>
        <RunPicker value={runId} onChange={(id) => navigate("overview", { run: id })} />
        {r && (
          <a className="btn" href={`/api/runs/${r.id}/report`} target="_blank" rel="noreferrer">
            Open HTML report
          </a>
        )}
      </div>
      <div className="page-head">
        <div>
          <h1>{r?.label || "Benchmark run"}</h1>
          {r && (
            <dl className="meta">
              <div><dt>Run</dt><dd className="mono">{r.id}</dd></div>
              <div><dt>Status</dt><dd><span className={`tag ${r.status === "completed" ? "pass" : "warn"}`}>{r.status.replace("_", " ")}</span></dd></div>
              <div><dt>Started</dt><dd>{when(r.created_at)}</dd></div>
              <div><dt>Duration</dt><dd>{totals?.duration_s ?? "–"}s</dd></div>
              <div><dt>Results</dt><dd>{totals?.passed} of {totals?.executed} passed</dd></div>
              <div><dt>Errors</dt><dd>{totals?.errors}</dd></div>
              <div><dt>Spend</dt><dd>{usd(totals?.cost_usd)}</dd></div>
              <div><dt>Commit</dt><dd className="mono">{(r.git_sha ?? "n/a").slice(0, 10)}</dd></div>
            </dl>
          )}
        </div>
      </div>

      {run.error && <div className="error-box">Could not load the run: {run.error}</div>}
      {r && (
        <>
          {ordered.some((s) => s !== "load") && (
            <section className="panel" aria-labelledby="ledger-h">
              <h2 id="ledger-h">Every case, every target</h2>
              <p className="lede">Each square is one test case. Select a failing square to see what went wrong.</p>
              {ordered
                .filter((s) => s !== "load")
                .map((s) => (
                  <Ledger key={s} runId={r.id} suite={s} title={suiteTitle(s)} targets={Object.keys(suites[s])} />
                ))}
              <LedgerLegend />
            </section>
          )}

          {ordered.map((s) => (
            <section className="panel" key={s} aria-labelledby={`h-${s}`}>
              <div className="row">
                <h2 id={`h-${s}`}>{suiteTitle(s)}</h2>
                <span className="spacer" />
                {s !== "load" && (
                  <a className="btn" href={href("failures", { run: r.id, suite: s })}>Inspect failures</a>
                )}
              </div>
              <p className="lede">{r.manifest?.suites?.[s]?.description}.</p>
              {s === "load" ? (
                <LoadSection blocks={suites[s]} />
              ) : (
                <>
                  <div style={{ maxWidth: 640 }}>
                    <BarCI
                      title={`${suiteTitle(s)} pass rate by target`}
                      rows={Object.entries(suites[s]).map(([t, b]) => ({
                        label: t,
                        value: b.accuracy,
                        lo: b.accuracy_ci95?.[0],
                        hi: b.accuracy_ci95?.[1],
                      }))}
                    />
                    <p className="note">Pass rate with 95% Wilson confidence interval.</p>
                  </div>
                  <SuiteMetrics suite={s} blocks={suites[s]} />
                  {s === "core" && (
                    <BreakdownTable title="Pass rate by category" rowsKey="by_category" blocks={suites[s]}
                      cell={(e) => (e ? `${pct(e.accuracy)} (${e.passed}/${e.n})` : "–")} />
                  )}
                  {s === "injection" && (
                    <BreakdownTable title="Attack success by technique" rowsKey="by_attack" blocks={suites[s]}
                      cell={(e) => (e ? pct(e.attack_success_rate) : "–")} />
                  )}
                  {s === "isolation" && (
                    <BreakdownTable title="Leaking scenarios by boundary" rowsKey="by_boundary" blocks={suites[s]}
                      cell={(e) => (e ? `${e.leaked} of ${e.scenarios}` : "–")} />
                  )}
                  {r.summary.comparisons?.[s]?.length ? (
                    <>
                      <h3>Paired comparisons</h3>
                      <div className="scroll">
                        <table>
                          <thead>
                            <tr><th>A</th><th>B</th><th className="num">A pass</th><th className="num">B pass</th><th className="num">Only A passed</th><th className="num">Only B passed</th><th className="num">McNemar p</th></tr>
                          </thead>
                          <tbody>
                            {r.summary.comparisons[s].map((c) => (
                              <tr key={`${c.a}-${c.b}`}>
                                <td className="mono">{c.a}</td><td className="mono">{c.b}</td>
                                <td className="num">{pct(c.a_accuracy)}</td><td className="num">{pct(c.b_accuracy)}</td>
                                <td className="num">{c.a_only_passed}</td><td className="num">{c.b_only_passed}</td>
                                <td className="num">{c.mcnemar_p < 0.001 ? "<0.001" : c.mcnemar_p.toPrecision(2)} {c.mcnemar_p < 0.05 && <span className="tag plain">significant</span>}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </>
                  ) : null}
                </>
              )}
            </section>
          ))}
          {r.manifest?.skipped_targets && Object.keys(r.manifest.skipped_targets).length > 0 && (
            <p className="note">
              Skipped targets: {Object.entries(r.manifest.skipped_targets).map(([t, why]) => `${t} (${why})`).join(", ")}.
            </p>
          )}
        </>
      )}
    </>
  );
}
