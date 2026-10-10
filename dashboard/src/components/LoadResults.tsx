import type { RunDetail, SuiteBlock } from "../api";
import { ms, num, pct, usd } from "../format";
import { href } from "../router";
import { LineChart } from "./charts";

const verdictLabel = (value?: string) => value === "pass" ? "Met" : value === "fail" ? "Missed" : value === "not_run" ? "Not run" : value === "insufficient_samples" ? "More samples needed" : "–";
const rate = (value?: number | null) => value == null ? "–" : `${value.toFixed(2)} rps`;

export function LoadResults({ run, blocks }: { run: RunDetail; blocks: Record<string, SuiteBlock> }) {
  const targets = Object.keys(blocks);
  const info = (id: string) => run.manifest.targets?.[id] ?? {};
  const source = (id: string) => info(id).provider === "mock" ? "Simulated model" : info(id).provider === "reference_agent" ? "Reference agent" : "API";
  const rows: { label: string; get: (b: SuiteBlock, id: string) => string }[] = [
    { label: "Source", get: (_, id) => source(id) },
    { label: "Reported model", get: (b, id) => (b.models ?? info(id).reported_models ?? []).join(", ") || info(id).model || "–" },
    { label: "Requests completed", get: (b) => num(b.total_requests) },
    { label: "Pass rate", get: (b) => `${pct(b.accuracy)} (${b.passed}/${b.executed})` },
    { label: "Pass rate 95% interval", get: (b) => `${pct(b.accuracy_ci95?.[0])} – ${pct(b.accuracy_ci95?.[1])}` },
    { label: "End-to-end p50", get: (b) => ms(b.latency_ms?.p50) },
    { label: "End-to-end p95", get: (b) => ms(b.latency_ms?.p95) },
    { label: "End-to-end p99", get: (b) => ms(b.latency_ms?.p99) },
    { label: "Provider round-trip p95", get: (b) => ms(b.api_latency_ms?.p95) },
    { label: "Client queue p95", get: (b) => ms(b.queue_wait_ms?.p95) },
    { label: "In-flight call limit", get: (b) => b.effective_max_inflight == null ? "Target default / unlimited" : num(b.effective_max_inflight) },
    { label: "Observed peak calls", get: (b) => num(b.peak_api_concurrency) },
    { label: "Requests per second", get: (b) => rate(b.achieved_rps) },
    { label: "Correct answers per second", get: (b) => rate(b.goodput_rps) },
    { label: "Correct answers within latency target / second", get: (b) => rate(b.slo_goodput_rps) },
    { label: "Correct and within latency target", get: (b) => pct(b.slo_pass_rate) },
    { label: "API errors / timeouts", get: (b) => `${pct(b.error_rate)} / ${pct(b.timeout_rate)}` },
    { label: "Retries", get: (b) => num(b.retries) },
    { label: "Pass-rate drop under load", get: (b) => b.accuracy_drop_under_load_pp == null ? "–" : `${b.accuracy_drop_under_load_pp} percentage points` },
    { label: "Worst p95 / first-stage p95", get: (b) => b.p95_slowdown == null ? "–" : `${b.p95_slowdown}×` },
    { label: "Stage objectives", get: (b) => verdictLabel(b.slo_verdict) },
    { label: "Cost estimate", get: (b) => usd(b.cost_usd) },
    { label: "Stop reason", get: (b) => b.stop_reason?.replaceAll("_", " ") ?? "Duration" },
  ];
  return (
    <>
      <h3>Load comparison</h3>
      <p className="note">Targets run separately with the same configured stages and prompt cycle. Their sample counts can differ. Simulated models demonstrate the test harness; their speed and quality do not describe real AI services.</p>
      <div className="scroll">
        <table aria-label="Load comparison">
          <thead><tr><th>Measure</th>{targets.map((id) => <th className="num mono" key={id}>{id}</th>)}</tr></thead>
          <tbody>{rows.map((row) => <tr key={row.label}><td>{row.label}</td>{targets.map((id) => <td className="num" key={id}>{row.get(blocks[id], id)}</td>)}</tr>)}</tbody>
        </table>
      </div>
      {targets.map((id) => {
        const b = blocks[id];
        return (
          <div key={id} style={{ marginTop: 24 }}>
            <div className="row"><h3 className="mono">{id}</h3><span className="tag plain">{source(id)}</span><span className="spacer" />
              <a className="btn" href={href("failures", { run: run.id, suite: "load", target: id, status: "failing" })}>Inspect failures</a>
            </div>
            {b.slo && <p className="note">Objectives per stage: p95 ≤ {ms(b.slo.latency_ms)}, pass rate ≥ {pct(b.slo.min_pass_rate)}, API errors ≤ {pct(b.slo.max_error_rate)}, at least {b.slo.min_samples} samples.</p>}
            {b.tail_sample_warning && <p className="note">Fewer than 100 samples: tail latency estimates are sensitive to individual requests. This run's p99 does not establish sustained capacity.</p>}
            <div className="scroll">
              <table aria-label={`${id} load stages`}>
                <thead><tr><th>Stage</th><th className="num">Users</th><th className="num">Requests</th><th className="num">Seconds / planned</th><th className="num">Peak calls</th><th className="num">RPS</th><th className="num">Correct RPS</th><th className="num">p95</th><th className="num">p99</th><th className="num">Queue p95</th><th className="num">Errors</th><th className="num">Pass rate</th><th>Objectives</th></tr></thead>
                <tbody>{(b.stages ?? []).map((stage: SuiteBlock) => <tr key={stage.stage}>
                  <td>{stage.stage}</td><td className="num">{stage.users}</td><td className="num">{stage.requests}</td>
                  <td className="num">{stage.duration_s} / {stage.planned_duration_s ?? "–"}</td>
                  <td className="num">{num(stage.peak_api_concurrency)}</td><td className="num">{rate(stage.throughput_rps)}</td><td className="num">{rate(stage.goodput_rps)}</td>
                  <td className="num">{ms(stage.latency_ms?.p95)}</td><td className="num">{ms(stage.latency_ms?.p99)}</td><td className="num">{ms(stage.queue_wait_ms?.p95)}</td>
                  <td className="num">{pct(stage.error_rate)}</td><td className="num">{pct(stage.accuracy)}</td><td>{verdictLabel(stage.slo_verdict)}</td>
                </tr>)}</tbody>
              </table>
            </div>
            <p className="note">{b.total_requests} requests in {b.wall_time_s}s. End-to-end latency includes client queueing, retries and backoff. Provider round-trip measures individual attempts. A stage drains in-flight requests before the next stage starts.</p>
            <div className="charts-3">
              <div><h3>Completed requests per second</h3><LineChart title="Requests" points={(b.timeline ?? []).map((p: SuiteBlock) => ({ x: `${p.t}s`, y: p.requests }))} format={(v) => String(v)} /></div>
              <div><h3>p95 latency per second</h3><LineChart title="p95" points={(b.timeline ?? []).map((p: SuiteBlock) => ({ x: `${p.t}s`, y: p.p95_ms }))} format={ms} /></div>
              <div><h3>Pass rate by stage</h3><LineChart title="Pass rate" points={(b.stages ?? []).map((s: SuiteBlock) => ({ x: `${s.users} users`, y: s.accuracy }))} format={pct} yMax={1} /></div>
            </div>
            {b.attempt_error_counts && <details><summary>API failure breakdown, including recovered retries</summary><pre>{JSON.stringify({ final_errors: b.error_counts, failed_attempts: b.attempt_error_counts }, null, 2)}</pre></details>}
          </div>
        );
      })}
    </>
  );
}
