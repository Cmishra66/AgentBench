import { useEffect, useMemo, useState } from "react";
import { api, type Job, type LaunchBody } from "../api";
import { suiteTitle, usd, when } from "../format";
import { useAsync } from "../hooks";
import { href } from "../router";
import { DEFAULT_LOAD, LoadControls, type LoadSettings } from "../components/LoadControls";

function JobStatus({ job }: { job: Job }) {
  const pctDone = job.total ? Math.round((job.done / job.total) * 100) : 0;
  return (
    <div style={{ display: "grid", gap: 6 }}>
      <div className="row">
        <b>{job.label}</b>
        <span className={`tag ${job.status === "completed" ? "pass" : job.status === "failed" ? "failed" : "running"}`}>{job.status}</span>
        <span className="spacer" />
        {job.run_id && (
          <a className="btn" href={href("overview", { run: job.run_id })}>
            View run
          </a>
        )}
      </div>
      {job.status === "running" && (
        <>
          <div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pctDone}>
            <div style={{ width: `${pctDone}%` }} />
          </div>
          <span className="note">{job.total ? `${job.done} of ${job.total} ${job.suites?.length === 1 && job.suites[0] === "load" ? "targets finished; stages and in-flight calls must finish" : "work items"}` : "Starting…"}</span>
        </>
      )}
      {job.targets && <p className="note">Targets: {job.targets.map((t) => `${t.id} (${t.source === "simulation" ? "simulated" : "API"}${t.model ? `: ${t.model}` : ""})`).join("; ")}</p>}
      {job.error && <div className="error-box">{job.error}</div>}
    </div>
  );
}

export default function TestLab() {
  const config = useAsync(() => api.config(), []);
  const [suites, setSuites] = useState<string[]>(["core"]);
  const [targets, setTargets] = useState<string[]>([]);
  const [label, setLabel] = useState("");
  const [repeats, setRepeats] = useState(1);
  const [concurrency, setConcurrency] = useState(16);
  const [limit, setLimit] = useState<string>("");
  const [maxUsd, setMaxUsd] = useState<string>("");
  const [loadOptions, setLoadOptions] = useState<LoadSettings>(DEFAULT_LOAD);
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [estimate, setEstimate] = useState<Awaited<ReturnType<typeof api.estimate>> | null>(null);
  const jobs = useAsync(() => api.jobs(), [job?.status]);

  const cfg = config.data;
  const accepted = useMemo(() => {
    const kinds = new Set<string>();
    cfg?.suites.filter((s) => suites.includes(s.name)).forEach((s) => s.accepts.forEach((k) => kinds.add(k)));
    return kinds;
  }, [cfg, suites]);

  useEffect(() => {
    if (!cfg || targets.length) return;
    const defaults = new Set<string>();
    cfg.suites.filter((s) => suites.includes(s.name)).forEach((s) => s.default_targets.forEach((t) => defaults.add(t)));
    setTargets(cfg.targets.filter((t) => t.available && defaults.has(t.id)).map((t) => t.id));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cfg]);

  useEffect(() => {
    if (!job || job.status !== "running") return;
    const id = setInterval(() => {
      api.job(job.id).then(setJob).catch(() => undefined);
    }, 1000);
    return () => clearInterval(id);
  }, [job]);

  const body = (): LaunchBody => ({
    suites,
    targets: targets.filter((t) => {
      const info = cfg?.targets.find((x) => x.id === t);
      return info && accepted.has(info.kind);
    }),
    label: label.trim() || `lab: ${suites.join("+")}`,
    repeats,
    concurrency,
    limit: limit ? Number(limit) : null,
    max_usd: maxUsd ? Number(maxUsd) : null,
    options: suites.includes("load") ? { load: { ...loadOptions } } : {},
  });

  const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);
  const chosenTargets = body().targets;
  const ready = suites.length > 0 && chosenTargets.length > 0;

  const launch = async () => {
    setError(null);
    try {
      setJob(await api.launch(body()));
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const runEstimate = async () => {
    setError(null);
    try {
      setEstimate(await api.estimate(body()));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  if (config.error) return <div className="error-box">Could not load the configuration: {config.error}</div>;
  if (!cfg) return <p className="note">Loading configuration…</p>;
  const models = cfg.targets.filter((t) => t.kind === "model");
  const agents = cfg.targets.filter((t) => t.kind === "agent");
  const apiModels = models.filter((t) => t.available && t.source === "api");
  const uniqueApiModels = apiModels.filter((t, i) => apiModels.findIndex((other) => other.provider === t.provider && other.model === t.model) === i);
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Test lab</h1>
          <p>Choose suites and targets, set how hard to push, and start a run. Results appear in the overview and failure explorer as soon as it finishes.</p>
        </div>
      </div>

      <section className="panel" aria-labelledby="suites-h">
        <h2 id="suites-h">Suites</h2>
        <div className="choices" style={{ marginTop: 10 }}>
          {cfg.suites.map((s) => {
            const on = suites.includes(s.name);
            return (
              <label key={s.name} className={`choice${on ? " on" : ""}`}>
                <input type="checkbox" checked={on} onChange={() => setSuites(toggle(suites, s.name))} />
                <span>
                  <b>{suiteTitle(s.name)}</b>
                  <span>{s.description}. Runs on {s.accepts.join(" and ")} targets.</span>
                </span>
              </label>
            );
          })}
        </div>
      </section>

      {suites.includes("load") && <LoadControls config={cfg} value={loadOptions} onChange={setLoadOptions} />}

      <section className="panel" aria-labelledby="selected-targets-h">
        <h2 id="selected-targets-h">Targets for this run</h2>
        <p>{chosenTargets.length ? chosenTargets.map((id) => {
          const t = cfg.targets.find((item) => item.id === id)!;
          return `${id} (${t.source === "simulation" ? "simulated" : "API"}${t.model ? `: ${t.model}` : ""})`;
        }).join("; ") : "Select a model or agent below."}</p>
        {suites.includes("load") && <>
          <div className="row">
          <button className="btn primary" type="button" disabled={uniqueApiModels.length < 2}
            onClick={() => setTargets(uniqueApiModels.slice(0, 2).map((t) => t.id))}>Compare API models</button>
          <button className="btn" type="button" disabled={!models.some((t) => t.available && t.source === "api") || !models.some((t) => t.available && t.source === "simulation")}
            onClick={() => {
              const real = models.find((t) => t.id === "openrouter" && t.available) ?? models.find((t) => t.available && t.source === "api");
              const mock = models.find((t) => t.id === "mock-strong" && t.available) ?? models.find((t) => t.available && t.source === "simulation");
              if (real && mock) setTargets([real.id, mock.id]);
            }}>Compare API and mock</button>
          </div>
          <p className="note" style={{ marginTop: 8 }}>Compare API models selects the first two available distinct models. You can change the targets below.</p>
          <p className="note" style={{ marginTop: 8 }}>Mock timings and quality are simulated. Compare them to exercise the platform; use two real API targets for model performance claims.</p>
        </>}
      </section>

      {[{ title: "Models", list: models, kind: "model" }, { title: "Agents", list: agents, kind: "agent" }].map((group) => (
        <section className="panel" key={group.kind} aria-labelledby={`t-${group.kind}`}>
          <h2 id={`t-${group.kind}`}>{group.title}</h2>
          {!accepted.has(group.kind) && <p className="note">None of the selected suites run on {group.kind} targets.</p>}
          <div className="choices" style={{ marginTop: 10 }}>
            {group.list.map((t) => {
              const usable = t.available && accepted.has(t.kind);
              const on = targets.includes(t.id) && usable;
              return (
                <label key={t.id} className={`choice${on ? " on" : ""}${usable ? "" : " off"}`}>
                  <input type="checkbox" disabled={!usable} checked={on} onChange={() => setTargets(toggle(targets, t.id))} />
                  <span>
                    <b className="mono" style={{ fontSize: 13.5 }}>{t.id}</b>
                    <span>{t.source === "simulation" ? "Simulated" : "API"}{t.max_concurrency ? ` · configured call limit ${t.max_concurrency}` : ""}</span>
                    <span>
                      {t.agent ? `${t.agent.preset} preset on ${t.agent.model_target}` : `${t.provider}${t.model ? `, ${t.model}` : ""}`}
                      {t.description ? `. ${t.description}` : ""}
                      {!t.available && ` (${t.status})`}
                    </span>
                  </span>
                </label>
              );
            })}
          </div>
        </section>
      ))}

      <section className="panel" aria-labelledby="opts-h">
        <h2 id="opts-h">Run settings</h2>
        <div className="form-grid" style={{ marginTop: 10 }}>
          <label className="field">
            Label
            <input type="text" value={label} placeholder={`lab: ${suites.join("+")}`} onChange={(e) => setLabel(e.target.value)} />
          </label>
          <label className="field">
            Repeats per case
            <input type="number" min={1} max={10} value={repeats} onChange={(e) => setRepeats(Math.max(1, Number(e.target.value)))} />
          </label>
          <label className="field">
            Concurrent cases
            <input type="number" min={1} max={256} value={concurrency} onChange={(e) => setConcurrency(Math.max(1, Number(e.target.value)))} />
          </label>
          <label className="field">
            Cases per suite (optional)
            <input type="number" min={1} value={limit} placeholder="All" onChange={(e) => setLimit(e.target.value)} />
          </label>
          <label className="field">
            Spend limit in USD (optional)
            <input type="number" min={0} step="0.5" value={maxUsd} placeholder={usd(cfg.run?.budget?.max_usd)} onChange={(e) => setMaxUsd(e.target.value)} />
          </label>
        </div>
        <div className="row" style={{ marginTop: 16 }}>
          <button type="button" className="btn primary" disabled={!ready || job?.status === "running"} onClick={launch}>
            Start run
          </button>
          <button type="button" className="btn" disabled={!ready} onClick={runEstimate}>
            Estimate cost
          </button>
          <span className="note">
            {ready ? `${suites.length} suite${suites.length > 1 ? "s" : ""} on ${chosenTargets.length} target${chosenTargets.length > 1 ? "s" : ""}.` : "Pick at least one suite and one target that suite accepts."}
          </span>
        </div>
        {error && <div className="error-box" style={{ marginTop: 12 }}>{error}</div>}
        {estimate && (
          <div className="scroll" style={{ marginTop: 12 }}>
            <table>
              <thead><tr><th>Suite</th><th>Target</th><th className="num">Items</th><th className="num">Requests</th><th className="num">Tokens</th><th className="num">Estimated cost</th></tr></thead>
              <tbody>
                {estimate.rows.map((r, i) => (
                  <tr key={i}>
                    <td>{suiteTitle(r.suite)}</td>
                    <td className="mono">{r.target ?? "–"}</td>
                    <td className="num">{r.items}</td>
                    <td className="num">{r.requests}</td>
                    <td className="num">{r.tokens?.toLocaleString() ?? "–"}</td>
                    <td className="num">{usd(r.usd)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="note">{estimate.total_usd == null ? "Load-test cost depends on completed requests and token usage." : `Roughly ${usd(estimate.total_usd)}.`} Spend limit: {usd(estimate.budget_usd)}. Mock targets use illustrative prices.</p>
          </div>
        )}
        {job && <div style={{ marginTop: 16 }}><JobStatus job={job} /></div>}
      </section>

      {jobs.data && jobs.data.length > 0 && (
        <section className="panel" aria-labelledby="jobs-h">
          <h2 id="jobs-h">Runs started from this dashboard</h2>
          <div className="scroll">
            <table>
              <thead><tr><th>Label</th><th>Started</th><th>Status</th><th>Progress</th><th /></tr></thead>
              <tbody>
                {jobs.data.map((j) => (
                  <tr key={j.id}>
                    <td>{j.label}</td>
                    <td>{when(new Date(j.started * 1000).toISOString())}</td>
                    <td><span className={`tag ${j.status === "completed" ? "pass" : j.status === "failed" ? "failed" : "running"}`}>{j.status}</span></td>
                    <td className="num">{j.total ? `${j.done}/${j.total}` : "–"}</td>
                    <td>{j.run_id && <a href={href("overview", { run: j.run_id })}>View run</a>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </>
  );
}
