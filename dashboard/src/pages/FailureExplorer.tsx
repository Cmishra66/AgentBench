import { useEffect, useMemo, useState, type ReactNode } from "react";
import { api, type EvalOutcome, type ResultDetail } from "../api";
import { TraceWaterfall } from "../components/charts";
import { ms, sortSuites, suiteTitle, usd } from "../format";
import { useAsync } from "../hooks";
import { replaceParams } from "../router";
import { RunPicker } from "./Overview";

const PAGE = 50;

function json(v: unknown): string {
  return JSON.stringify(v, null, 2);
}

function CopyCommand({ command }: { command: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="copy">
      <pre>{command}</pre>
      <button
        type="button"
        className="btn"
        onClick={() => {
          navigator.clipboard?.writeText(command).then(
            () => {
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            },
            () => setCopied(false),
          );
        }}
      >
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}

function Check({ e, open }: { e: EvalOutcome; open: boolean }) {
  const state = e.skipped ? "skip" : e.passed ? "ok" : "bad";
  const icon = state === "skip" ? "–" : state === "ok" ? "✓" : "✕";
  const hasDetails = e.details && Object.keys(e.details).length > 0;
  return (
    <div className={`check ${state}`}>
      <span className="ic" aria-hidden="true">{icon}</span>
      <div>
        <span>
          {e.name.replace(/_/g, " ")}
          {!e.required && <span className="note"> (informational)</span>}
          {e.skipped && <span className="note"> (skipped)</span>}
          <span className="sr-only">{state === "ok" ? " passed" : state === "bad" ? " failed" : ""}</span>
        </span>
        {hasDetails && (
          <details open={open && state === "bad"}>
            <summary>Details</summary>
            <pre>{json(e.details)}</pre>
          </details>
        )}
      </div>
    </div>
  );
}

function highlight(text: string, needles: string[]): ReactNode {
  const clean = needles.filter(Boolean);
  if (!clean.length) return text;
  const escaped = clean.map((n) => n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const parts = text.split(new RegExp(`(${escaped.join("|")})`, "g"));
  return parts.map((p, i) => (clean.includes(p) ? <mark key={i} className="secret">{p}</mark> : p));
}

type Turn = { turn: number; actor: string; session: string; message: string; reply: string; tool_calls: { name: string; ok: boolean }[] };
type Violation = { turn: number; requester: string; owner: string; secret: string; value: string; boundary: string; severity: string };

function Transcript({ r }: { r: ResultDetail }) {
  const turns: Turn[] = r.input.transcript ?? [];
  const violations: Violation[] = r.evals[0]?.details?.violations ?? [];
  return (
    <>
      <p className="lede">{r.input.description}</p>
      {violations.length > 0 && (
        <div className="error-box" style={{ marginBottom: 12 }}>
          {violations.length} unauthorized disclosure{violations.length > 1 ? "s" : ""}:{" "}
          {violations.map((v, i) => (
            <span key={i}>
              {i > 0 && "; "}
              {v.requester} received {v.owner}'s {v.secret.replace("_", " ")} ({v.severity}, {v.boundary.replace("_", " ")}, turn {v.turn + 1})
            </span>
          ))}
        </div>
      )}
      <div className="transcript">
        {turns.map((t, i) => {
          const leaked = violations.filter((v) => v.turn === t.turn && v.requester === t.actor);
          return (
            <div className="turn" key={i}>
              <div className="who">
                Turn {t.turn + 1}: {t.actor}, session {t.session}
              </div>
              <div className="bubble">{t.message}</div>
              <div className="who">Agent{t.tool_calls?.length ? ` (tools: ${t.tool_calls.map((c) => c.name).join(", ")})` : ""}</div>
              <div className={`bubble agent${leaked.length ? " leak" : ""}`}>{highlight(t.reply, leaked.map((v) => v.value))}</div>
            </div>
          );
        })}
      </div>
    </>
  );
}

function ToolDetail({ r }: { r: ResultDetail }) {
  const calls: { name: string; arguments: Record<string, unknown>; ok: boolean; error: string | null }[] = r.metrics.tool_calls_detail ?? [];
  const effects: { tool: string; arguments: Record<string, unknown>; idempotency_key: string | null }[] | null = r.metrics.effects ?? null;
  const diagnostics = r.evals.find((e) => e.type === "diagnostics");
  return (
    <>
      <div className="two">
        <div>
          <h3>Request</h3>
          <pre>{`${r.input.user} (${r.input.role}): ${r.input.prompt}`}</pre>
          {r.input.faults?.length ? (
            <p className="note">Injected faults: {r.input.faults.map((f: { tool: string; type: string; on_call: number }) => `${f.type} on ${f.tool} call ${f.on_call}`).join(", ")}</p>
          ) : null}
        </div>
        <div>
          <h3>Expected</h3>
          <pre>{json(r.expected)}</pre>
        </div>
      </div>
      <h3>Tool calls</h3>
      {calls.length ? (
        <div className="scroll">
          <table>
            <thead><tr><th>#</th><th>Tool</th><th>Arguments</th><th>Result</th></tr></thead>
            <tbody>
              {calls.map((c, i) => (
                <tr key={i}>
                  <td>{i + 1}</td>
                  <td className="mono">{c.name}</td>
                  <td className="mono">{JSON.stringify(c.arguments)}</td>
                  <td>{c.ok ? <span className="tag pass">ok</span> : <span className="tag failed">{c.error ?? "error"}</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="note">No tools were called.</p>
      )}
      {effects && (
        <>
          <h3>Committed side effects</h3>
          {effects.length ? (
            <pre>{effects.map((e) => `${e.tool} ${JSON.stringify(e.arguments)}${e.idempotency_key ? ` key=${e.idempotency_key}` : " (no idempotency key)"}`).join("\n")}</pre>
          ) : (
            <p className="note">None.</p>
          )}
        </>
      )}
      <h3>Final answer</h3>
      <pre>{r.output || "(empty)"}</pre>
      {diagnostics && (
        <details>
          <summary>Diagnostics</summary>
          <pre>{json(diagnostics.details)}</pre>
        </details>
      )}
    </>
  );
}

function Detail({ id, runId }: { id: number; runId: string }) {
  const { data: r, error, loading } = useAsync(() => api.result(id), [id]);
  if (error) return <div className="error-box">Could not load result {id}: {error}</div>;
  if (loading || !r) return <p className="note">Loading result…</p>;
  const evals = [...r.evals]
    .filter((e) => e.type !== "diagnostics")
    .sort((a, b) => Number(a.passed && !a.skipped) - Number(b.passed && !b.skipped));
  const repro = `agentbench repro ${runId} ${r.case_id} --target ${r.target_id} --suite ${r.suite}${r.trial ? ` --trial ${r.trial}` : ""}`;
  return (
    <article aria-label={`Result ${r.case_id}`}>
      <div className="row">
        <h2 className="mono" style={{ fontSize: 18 }}>{r.case_id}</h2>
        <span className={`tag ${r.status}`}>{r.status}</span>
      </div>
      <p className="note" style={{ marginTop: 2 }}>
        {suiteTitle(r.suite)}, {r.category.replace(/_/g, " ")}, target <span className="mono">{r.target_id}</span>
        {r.trial ? `, repeat ${r.trial + 1}` : ""}
      </p>
      {r.error && <pre className="error-box">{r.error}</pre>}
      <h3>Checks</h3>
      <div className="checks">{evals.map((e) => <Check key={e.name} e={e} open={r.suite !== "isolation"} />)}</div>
      {r.suite === "isolation" ? (
        <Transcript r={r} />
      ) : r.suite === "tool_use" ? (
        <ToolDetail r={r} />
      ) : (
        <>
          <div className="two">
            <div>
              <h3>Input</h3>
              {r.input.system && <pre>{`[system]\n${r.input.system}`}</pre>}
              <pre>{r.input.prompt ?? json(r.input)}</pre>
            </div>
            <div>
              <h3>Expected</h3>
              <pre>{typeof r.expected === "string" ? r.expected : json(r.expected)}</pre>
              <h3>Actual output</h3>
              <pre>{r.output || "(empty)"}</pre>
            </div>
          </div>
        </>
      )}
      <h3>Trace</h3>
      <TraceWaterfall spans={r.trace ?? []} />
      <p className="note">
        {ms(r.latency_ms)} end to end, {r.attempts} attempt{r.attempts === 1 ? "" : "s"} ({r.retries} retried), {r.input_tokens.toLocaleString()} input and{" "}
        {r.output_tokens.toLocaleString()} output tokens, {usd(r.cost_usd)}
        {r.models?.length ? `, model ${r.models.join(", ")}` : ""}.
      </p>
      <h3>Reproduce</h3>
      <CopyCommand command={repro} />
    </article>
  );
}

export default function FailureExplorer({ params }: { params: URLSearchParams }) {
  const runs = useAsync(() => api.runs(100), []);
  const runId = params.get("run") || runs.data?.find((r) => r.status !== "running")?.id || null;
  const suite = params.get("suite") || "";
  const target = params.get("target") || "";
  const status = params.get("status") || "failing";
  const q = params.get("q") || "";
  const resultId = params.get("result") ? Number(params.get("result")) : null;
  const [offset, setOffset] = useState(0);
  const [search, setSearch] = useState(q);
  const run = useAsync(() => (runId ? api.run(runId) : Promise.resolve(null)), [runId]);
  const list = useAsync(
    () => (runId ? api.results(runId, { status: status === "all" ? undefined : status, suite, target, q, limit: PAGE, offset }) : Promise.resolve({ items: [], total: 0 })),
    [runId, suite, target, status, q, offset],
  );
  const set = (patch: Record<string, string | number | null>) =>
    replaceParams("failures", { run: runId, suite, target, status, q, result: resultId, ...patch });

  useEffect(() => setOffset(0), [runId, suite, target, status, q]);
  useEffect(() => {
    const id = setTimeout(() => {
      if (search !== q) set({ q: search, result: null });
    }, 300);
    return () => clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  const suites = useMemo(() => {
    const s = new Set<string>();
    Object.values(run.data?.summary.targets ?? {}).forEach((b) => Object.keys(b).forEach((k) => k !== "load" && s.add(k)));
    return sortSuites([...s]);
  }, [run.data]);
  const targets = useMemo(
    () => Object.entries(run.data?.summary.targets ?? {}).filter(([, b]) => !suite || suite in b).map(([t]) => t),
    [run.data, suite],
  );
  const items = list.data?.items ?? [];
  const selected = resultId ?? items[0]?.id ?? null;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Failure explorer</h1>
          <p>Inspect the prompt, the expected result, what came back, which checks failed, the trace, and the command that reproduces it.</p>
        </div>
      </div>
      <div className="panel" style={{ paddingBottom: 14 }}>
        <div className="row" role="search">
          <RunPicker value={runId} onChange={(id) => set({ run: id, suite: "", target: "", result: null })} />
          <label className="field">
            Suite
            <select value={suite} onChange={(e) => set({ suite: e.target.value, target: "", result: null })}>
              <option value="">All suites</option>
              {suites.map((s) => <option key={s} value={s}>{suiteTitle(s)}</option>)}
            </select>
          </label>
          <label className="field">
            Target
            <select value={target} onChange={(e) => set({ target: e.target.value, result: null })}>
              <option value="">All targets</option>
              {targets.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </label>
          <label className="field">
            Outcome
            <select value={status} onChange={(e) => set({ status: e.target.value, result: null })}>
              <option value="failing">Failed or errored</option>
              <option value="failed">Failed</option>
              <option value="error">Errored</option>
              <option value="passed">Passed</option>
              <option value="all">All</option>
            </select>
          </label>
          <label className="field" style={{ flex: 1, minWidth: 200 }}>
            Search
            <input type="search" value={search} placeholder="Case id, output or error text" onChange={(e) => setSearch(e.target.value)} />
          </label>
        </div>
      </div>
      <div className="explorer">
        <div className="panel" style={{ padding: 8, margin: 0 }}>
          <div className="row" style={{ padding: "4px 8px 8px" }}>
            <span className="note">{list.data ? `${list.data.total} result${list.data.total === 1 ? "" : "s"}` : "Loading…"}</span>
            <span className="spacer" />
            {list.data && list.data.total > PAGE && (
              <>
                <button type="button" className="btn" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</button>
                <button type="button" className="btn" disabled={offset + PAGE >= list.data.total} onClick={() => setOffset(offset + PAGE)}>Next</button>
              </>
            )}
          </div>
          <div className="result-list" role="listbox" aria-label="Results">
            {items.length === 0 && !list.loading && <div className="empty">Nothing matches these filters.</div>}
            {items.map((it) => (
              <button
                type="button"
                key={it.id}
                role="option"
                aria-selected={selected === it.id}
                aria-current={selected === it.id ? "true" : undefined}
                className="result-item"
                onClick={() => set({ result: it.id })}
              >
                <span className="top">
                  <span className="case">{it.case_id}</span>
                  <span className={`tag ${it.status}`}>{it.status}</span>
                </span>
                <span className="sub">
                  <span className="mono">{it.target_id}</span> {suiteTitle(it.suite)}
                </span>
                <span className="sub">{it.error ? it.error.split("\n")[0] : it.output_preview}</span>
              </button>
            ))}
          </div>
        </div>
        <div className="panel" style={{ margin: 0, minHeight: 300 }}>
          {selected && runId ? <Detail id={selected} runId={runId} /> : <div className="empty">Select a result to inspect it.</div>}
        </div>
      </div>
    </>
  );
}
