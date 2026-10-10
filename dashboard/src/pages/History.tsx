import { useEffect, useState } from "react";
import { api, type GateResponse, type SuiteBlock } from "../api";
import { LineChart } from "../components/charts";
import { fmtMetric, ms, pct, sortSuites, suiteTitle, usd, when } from "../format";
import { useAsync } from "../hooks";
import { href, replaceParams } from "../router";

const EXTRA: Record<string, { label: string; get: (b: SuiteBlock) => number | null; format: (v: number) => string; max?: number }> = {
  core: { label: "Hallucination on unanswerable", get: (b) => b.eval_metrics?.hallucination_rate ?? null, format: (v) => pct(v, 0), max: 1 },
  injection: { label: "Attack success rate", get: (b) => b.attack_success_rate ?? null, format: (v) => pct(v, 0), max: 1 },
  tool_use: { label: "Duplicate write rate", get: (b) => b.duplicate_write_rate ?? null, format: (v) => pct(v, 0), max: 1 },
  isolation: { label: "Leakage rate", get: (b) => b.leakage_rate ?? null, format: (v) => pct(v, 0), max: 1 },
  load: { label: "Achieved throughput (rps)", get: (b) => b.achieved_rps ?? null, format: (v) => v.toFixed(1) },
};

function GateView({ gate }: { gate: GateResponse }) {
  const names = { pass: "Pass", warn: "Investigate", block: "Blocked" } as const;
  const flagged = gate.checks.filter((c) => c.status !== "skipped");
  return (
    <div>
      <div className="verdict">
        <b className={`v-${gate.verdict}`}>{names[gate.verdict]}</b>
        <span className="note">
          {gate.baseline_run.label || gate.baseline_run.id} compared with {gate.candidate_run.label || gate.candidate_run.id}.
          {gate.verdict === "block" ? " This change would fail CI." : gate.verdict === "warn" ? " CI passes, but these changes deserve a look." : " No thresholds were breached."}
        </span>
      </div>
      {gate.comparison.warnings.map((w) => (
        <p key={w} className="note">{w}</p>
      ))}
      <div className="scroll">
        <table>
          <thead>
            <tr><th>Result</th><th>Check</th><th>Target</th><th className="num">Baseline</th><th className="num">Candidate</th><th>Detail</th></tr>
          </thead>
          <tbody>
            {flagged
              .sort((a, b) => ["block", "warn", "pass"].indexOf(a.status) - ["block", "warn", "pass"].indexOf(b.status))
              .map((c, i) => (
                <tr key={i}>
                  <td><span className={`tag ${c.status}`}>{c.status}</span></td>
                  <td className="mono">{c.suite}.{c.metric}</td>
                  <td className="mono">{c.pair}</td>
                  <td className="num">{fmtMetric(c.metric, c.baseline)}</td>
                  <td className="num">{fmtMetric(c.metric, c.candidate)}</td>
                  <td>{c.detail}</td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>
      {gate.comparison.suites.filter((s) => s.paired_cases).map((s) => (
        <div key={`${s.suite}-${s.candidate_target}`}>
          <h3>
            {suiteTitle(s.suite)}: <span className="mono">{s.baseline_target === s.candidate_target ? s.candidate_target : `${s.baseline_target} to ${s.candidate_target}`}</span>
          </h3>
          <p className="note">
            {s.paired_cases} paired cases. Pass rate changed {s.delta !== null ? `${(s.delta * 100).toFixed(1)}pp` : "–"}
            {s.delta_ci95 ? ` (95% CI ${(s.delta_ci95[0] * 100).toFixed(1)} to ${(s.delta_ci95[1] * 100).toFixed(1)}pp)` : ""}, McNemar p ={" "}
            {s.mcnemar_p?.toPrecision(3)}. {s.newly_failing.length} newly failing, {s.newly_passing.length} newly passing.
          </p>
          {s.newly_failing.length > 0 && (
            <p style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {s.newly_failing.map((c) => (
                <a key={c} className="tag failed mono" href={href("failures", { run: gate.candidate_run.id, suite: s.suite, target: s.candidate_target, q: c })}>
                  {c}
                </a>
              ))}
            </p>
          )}
        </div>
      ))}
    </div>
  );
}

export default function History({ params }: { params: URLSearchParams }) {
  const series = useAsync(() => api.series(), []);
  const runs = useAsync(() => api.runs(200), []);
  const suites = sortSuites(Object.keys(series.data ?? {}));
  const suite = params.get("suite") || (suites.includes("core") ? "core" : suites[0]) || "";
  const targets = series.data?.[suite] ?? [];
  const target = params.get("target") && targets.includes(params.get("target")!) ? params.get("target")! : targets[0] ?? "";
  const history = useAsync(() => (suite && target ? api.history(suite, target) : Promise.resolve([])), [suite, target]);

  const finished = (runs.data ?? []).filter((r) => r.status !== "running");
  const [baseline, setBaseline] = useState(params.get("baseline") || "");
  const [candidate, setCandidate] = useState(params.get("candidate") || "");
  const [map, setMap] = useState(params.get("map") || "");
  const [gate, setGate] = useState<GateResponse | null>(null);
  const [gateError, setGateError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!runs.data || baseline || candidate) return;
    const labelled = (l: string) => finished.find((r) => r.label === l);
    const base = labelled("baseline");
    const cand = labelled("candidate");
    if (base && cand) {
      setBaseline(base.id);
      setCandidate(cand.id);
      if (!map) setMap("mock-strong=mock-strong-v2, agent-secure=agent-shared-cache");
      return;
    }
    const gated = finished.filter((r) => !r.suites.every((s) => s === "load"));
    if (gated[0]) setCandidate(gated[0].id);
    if (gated[1]) setBaseline(gated[1].id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runs.data]);

  const runCompare = async () => {
    setBusy(true);
    setGateError(null);
    try {
      const mapParam = map
        .split(",")
        .map((p) => p.trim())
        .filter((p) => p.includes("="))
        .map((p) => p.replace("=", ":"))
        .join(",");
      setGate(await api.compare(baseline, candidate, mapParam || undefined));
    } catch (e) {
      setGateError((e as Error).message);
      setGate(null);
    } finally {
      setBusy(false);
    }
  };

  const points = (history.data ?? []).map((h) => ({ x: when(h.created_at), sub: h.label, block: h.metrics }));
  const extra = EXTRA[suite];
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Regression history</h1>
          <p>Track quality, latency and cost for one target across runs, and gate a candidate run against a baseline the same way CI does.</p>
        </div>
      </div>
      <section className="panel" aria-labelledby="trend-h">
        <div className="row">
          <h2 id="trend-h">Trends</h2>
          <span className="spacer" />
          <label className="field">
            Suite
            <select value={suite} onChange={(e) => replaceParams("history", { suite: e.target.value })}>
              {suites.map((s) => <option key={s} value={s}>{suiteTitle(s)}</option>)}
            </select>
          </label>
          <label className="field">
            Target
            <select value={target} onChange={(e) => replaceParams("history", { suite, target: e.target.value })}>
              {targets.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </label>
        </div>
        {history.data && history.data.length === 0 && <div className="empty">No runs of {suiteTitle(suite)} on {target} yet.</div>}
        {history.data && history.data.length > 0 && (
          <>
            <div className="charts-2" style={{ marginTop: 8 }}>
              <div>
                <h3>Pass rate</h3>
                <LineChart title="Pass rate" points={points.map((p) => ({ ...p, y: p.block.accuracy }))} format={(v) => pct(v, 0)} yMax={1} />
              </div>
              <div>
                <h3>p95 latency</h3>
                <LineChart title="p95" points={points.map((p) => ({ ...p, y: p.block.latency_ms?.p95 ?? null }))} format={(v) => ms(v)} />
              </div>
              <div>
                <h3>Cost per success</h3>
                <LineChart title="Cost per success" points={points.map((p) => ({ ...p, y: p.block.cost_per_success_usd }))} format={(v) => usd(v)} />
              </div>
              {extra && (
                <div>
                  <h3>{extra.label}</h3>
                  <LineChart title={extra.label} points={points.map((p) => ({ ...p, y: extra.get(p.block) }))} format={extra.format} yMax={extra.max} />
                </div>
              )}
            </div>
            <h3>Runs</h3>
            <div className="scroll">
              <table>
                <thead>
                  <tr><th>Run</th><th>Started</th><th>Commit</th><th className="num">Pass rate</th><th className="num">p95</th><th className="num">Cost per success</th>{extra && <th className="num">{extra.label}</th>}</tr>
                </thead>
                <tbody>
                  {[...(history.data ?? [])].reverse().map((h) => (
                    <tr key={h.run_id}>
                      <td><a href={href("overview", { run: h.run_id })}>{h.label || h.run_id}</a></td>
                      <td>{when(h.created_at)}</td>
                      <td className="mono">{(h.git_sha ?? "n/a").slice(0, 8)}</td>
                      <td className="num">{pct(h.metrics.accuracy)}</td>
                      <td className="num">{ms(h.metrics.latency_ms?.p95)}</td>
                      <td className="num">{usd(h.metrics.cost_per_success_usd)}</td>
                      {extra && <td className="num">{(() => { const v = extra.get(h.metrics); return v === null ? "–" : extra.format(v); })()}</td>}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>

      <section className="panel" aria-labelledby="gate-h">
        <h2 id="gate-h">Compare two runs</h2>
        <p className="lede">
          Applies the gate rules from <code>configs/gates.yaml</code>. When the runs used different targets, map them, for example{" "}
          <code>mock-strong=mock-strong-v2</code>.
        </p>
        <div className="row" style={{ alignItems: "flex-end" }}>
          <label className="field">
            Baseline
            <select value={baseline} onChange={(e) => setBaseline(e.target.value)} style={{ minWidth: 260 }}>
              <option value="">Choose a run</option>
              {finished.map((r) => <option key={r.id} value={r.id}>{r.label || "unlabelled"} ({when(r.created_at)})</option>)}
            </select>
          </label>
          <label className="field">
            Candidate
            <select value={candidate} onChange={(e) => setCandidate(e.target.value)} style={{ minWidth: 260 }}>
              <option value="">Choose a run</option>
              {finished.map((r) => <option key={r.id} value={r.id}>{r.label || "unlabelled"} ({when(r.created_at)})</option>)}
            </select>
          </label>
          <label className="field" style={{ flex: 1, minWidth: 220 }}>
            Target map (optional)
            <input type="text" value={map} placeholder="baseline-target=candidate-target, …" onChange={(e) => setMap(e.target.value)} />
          </label>
          <button type="button" className="btn primary" disabled={!baseline || !candidate || busy} onClick={runCompare}>
            {busy ? "Comparing…" : "Compare runs"}
          </button>
        </div>
        {gateError && <div className="error-box" style={{ marginTop: 12 }}>{gateError}</div>}
        {gate && <div style={{ marginTop: 16 }}><GateView gate={gate} /></div>}
      </section>
    </>
  );
}
