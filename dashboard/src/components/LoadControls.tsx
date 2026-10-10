import type { AppConfig } from "../api";

export type LoadSettings = {
  profile: string;
  stages?: { users: number; duration_s: number }[];
  max_requests: number;
  max_inflight: number | null;
  latency_slo_ms: number;
  min_pass_rate: number;
  max_error_rate: number;
  min_samples: number;
  pad_context_tokens: number;
  think_time_s: number;
};

export const DEFAULT_LOAD: LoadSettings = {
  profile: "smoke", max_requests: 1000, max_inflight: 4, latency_slo_ms: 5000,
  min_pass_rate: 0.95, max_error_rate: 0.01, min_samples: 20,
  pad_context_tokens: 0, think_time_s: 0,
};

export function LoadControls({ config, value, onChange }: {
  config: AppConfig;
  value: LoadSettings;
  onChange: (next: LoadSettings) => void;
}) {
  const profiles = config.load_profiles ?? { smoke: [{ users: 2, duration_s: 5 }] };
  const stages = value.stages ?? profiles[value.profile] ?? profiles.smoke;
  const setStage = (index: number, key: "users" | "duration_s", number: number) => {
    onChange({ ...value, stages: stages.map((stage, i) => i === index ? { ...stage, [key]: number } : stage) });
  };
  const labels: Record<string, string> = {
    smoke: "Smoke: quick connection check", steady: "Steady: sustained traffic",
    ramp: "Ramp: increase users in steps", spike: "Spike: surge then recovery",
    stress: "Stress: 2 to 64 users", soak: "Soak: 8 users for 5 minutes",
  };
  const fields: { label: string; key: keyof Omit<LoadSettings, "profile" | "stages">; min: number; max?: number; step?: number; scale?: number }[] = [
    { label: "Max requests per target", key: "max_requests", min: 1, max: 100000 },
    { label: "Max simultaneous calls", key: "max_inflight", min: 1, max: 256 },
    { label: "p95 latency target (seconds)", key: "latency_slo_ms", min: 0.001, step: 0.1, scale: 1000 },
    { label: "Minimum pass rate (%)", key: "min_pass_rate", min: 0, max: 100, step: 1, scale: 0.01 },
    { label: "Maximum API error rate (%)", key: "max_error_rate", min: 0, max: 100, step: 0.1, scale: 0.01 },
    { label: "Minimum samples per stage", key: "min_samples", min: 1 },
    { label: "Extra context tokens", key: "pad_context_tokens", min: 0, max: 100000 },
    { label: "Pause between requests (seconds)", key: "think_time_s", min: 0, max: 60, step: 0.1 },
  ];
  return (
    <section className="panel" aria-labelledby="load-settings-h">
      <h2 id="load-settings-h">Load settings</h2>
      <p className="note">Each selected target runs the same stages separately. Users send another request when the previous one finishes. The call limit controls how many can reach the provider at once; additional users wait in the client queue.</p>
      <label className="field" style={{ marginTop: 12 }}>
        Load profile
        <select value={value.stages ? "custom" : value.profile} onChange={(e) => onChange({ ...value, profile: e.target.value, stages: undefined })}>
          {Object.keys(profiles).map((profile) => <option key={profile} value={profile}>{labels[profile] ?? profile}</option>)}
          {value.stages && <option value="custom">Custom stages</option>}
        </select>
      </label>
      <div className="scroll" style={{ marginTop: 12 }}>
        <table>
          <thead><tr><th>Stage</th><th>Virtual users</th><th>Duration in seconds</th><th /></tr></thead>
          <tbody>{stages.map((stage, i) => (
            <tr key={i}>
              <td>{i + 1}</td>
              <td><input aria-label={`Stage ${i + 1} virtual users`} type="number" min={1} max={256} value={stage.users} onChange={(e) => setStage(i, "users", Number(e.target.value))} /></td>
              <td><input aria-label={`Stage ${i + 1} duration in seconds`} type="number" min={0.1} max={3600} step={0.1} value={stage.duration_s} onChange={(e) => setStage(i, "duration_s", Number(e.target.value))} /></td>
              <td><button className="btn" type="button" aria-label={`Remove stage ${i + 1}`} disabled={stages.length === 1} onClick={() => onChange({ ...value, stages: stages.filter((_, j) => i !== j) })}>Remove</button></td>
            </tr>
          ))}</tbody>
        </table>
      </div>
      <button className="btn" type="button" style={{ marginTop: 10 }} disabled={stages.length >= 20} onClick={() => onChange({ ...value, stages: [...stages, { users: Math.min(256, stages[stages.length - 1].users * 2), duration_s: 15 }] })}>Add stage</button>
      <div className="form-grid" style={{ marginTop: 16 }}>
        {fields.map(({ label, key, min, max, step, scale = 1 }) => (
          <label className="field" key={key}>
            {label}
            <input type="number" min={min} max={max} step={step ?? 1} value={value[key] == null ? "" : Number((value[key]! / scale).toFixed(6))}
              placeholder={key === "max_inflight" ? "Use target defaults" : undefined}
              onChange={(e) => onChange({ ...value, [key]: e.target.value === "" && key === "max_inflight" ? null : Number(e.target.value) * scale })} />
          </label>
        ))}
      </div>
      <p className="note" style={{ marginTop: 12 }}>A stage meets its objectives when p95 latency, pass rate and API errors meet these limits, with enough samples. Requests already in flight finish after each stage's duration. A request or spend cap can end the profile early.</p>
    </section>
  );
}
