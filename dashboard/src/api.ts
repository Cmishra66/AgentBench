// Typed client for the AgentBench REST API (served by `agentbench serve`).

export type Totals = {
  results: number;
  executed: number;
  passed: number;
  errors: number;
  skipped: number;
  cost_usd: number;
  requests: number;
  duration_s?: number;
};

export type LatencyBlock = {
  p50: number | null;
  p95: number | null;
  p99: number | null;
  mean: number | null;
  max: number | null;
  n: number;
};

// Suite summary blocks are open-ended: every suite adds its own metrics.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type SuiteBlock = Record<string, any> & {
  cases: number;
  executed: number;
  passed: number;
  errors: number;
  accuracy: number | null;
  accuracy_ci95: [number | null, number | null];
  latency_ms: LatencyBlock;
  ttft_ms: LatencyBlock;
  error_rate: number | null;
  cost_usd: number | null;
  cost_per_success_usd: number | null;
};

export type RunListItem = {
  id: string;
  label: string;
  status: string;
  created_at: string;
  finished_at: string | null;
  suites: string[];
  git_sha: string | null;
  totals: Totals;
  targets: string[];
};

export type Comparison = {
  a: string;
  b: string;
  paired_cases: number;
  a_accuracy: number | null;
  b_accuracy: number | null;
  a_only_passed: number;
  b_only_passed: number;
  mcnemar_p: number;
};

export type RunDetail = Omit<RunListItem, "totals" | "targets"> & {
  summary: {
    targets: Record<string, Record<string, SuiteBlock>>;
    comparisons: Record<string, Comparison[]>;
    totals: Totals;
  };
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  manifest: Record<string, any>;
};

export type ResultRow = {
  id: number;
  run_id: string;
  suite: string;
  case_id: string;
  category: string;
  target_id: string;
  trial: number;
  status: "passed" | "failed" | "error" | "skipped";
  passed: boolean;
  score: number;
  latency_ms: number | null;
  ttft_ms: number | null;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  attempts: number;
  retries: number;
  cached: boolean;
  error_kind: string | null;
  error: string | null;
  output_preview?: string;
};

export type EvalOutcome = {
  name: string;
  type: string;
  passed: boolean | null;
  score: number;
  required: boolean;
  skipped: boolean;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  details: Record<string, any>;
  metrics: Record<string, number>;
};

export type Span = {
  trace_id: string;
  span_id: string;
  parent_span_id: string | null;
  name: string;
  kind: string;
  start_ms: number;
  end_ms: number;
  duration_ms: number;
  attributes: Record<string, unknown>;
  status: string;
  status_message: string;
};

export type ResultDetail = ResultRow & {
  output: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  input: Record<string, any>;
  expected: unknown;
  evals: EvalOutcome[];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  metrics: Record<string, any>;
  trace: Span[];
  tags: string[];
  models: string[];
  model_latency_ms: number | null;
};

export type Matrix = {
  cases: { id: string; category: string }[];
  cells: Record<string, { status: string; result_id: number; trials: number; passed: number }>;
};

export type TargetInfo = {
  id: string;
  kind: "model" | "agent";
  provider: string;
  source?: "simulation" | "api";
  max_concurrency?: number | null;
  rate_limit_rps?: number | null;
  model: string;
  description: string;
  available: boolean;
  status: string;
  agent: { preset: string; model_target: string } | null;
};

export type SuiteInfo = {
  name: string;
  description: string;
  default_targets: string[];
  categories: string[];
  accepts: string[];
};

export type AppConfig = {
  project: string;
  targets: TargetInfo[];
  suites: SuiteInfo[];
  datasets: { file: string; cases: number; categories: Record<string, number>; sha256: string }[];
  load_profiles?: Record<string, { users: number; duration_s: number }[]>;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  run: Record<string, any>;
};

export type GateCheck = {
  rule: string;
  metric: string;
  suite: string;
  pair: string;
  baseline: number | null;
  candidate: number | null;
  status: "pass" | "warn" | "block" | "skipped";
  detail: string;
  severity: string;
};

export type SuiteComparison = {
  suite: string;
  baseline_target: string;
  candidate_target: string;
  paired_cases: number;
  newly_failing: string[];
  newly_passing: string[];
  still_failing: number;
  mcnemar_p: number | null;
  delta: number | null;
  delta_ci95: [number, number] | null;
  metrics: { metric: string; label: string; direction: string; baseline: number | null; candidate: number | null; delta?: number; trend?: string }[];
};

export type GateResponse = {
  verdict: "pass" | "warn" | "block";
  fail_on: string;
  exit_code: number;
  checks: GateCheck[];
  comparison: { baseline: string; candidate: string; suites: SuiteComparison[]; warnings: string[] };
  baseline_run: { id: string; label: string; created_at: string };
  candidate_run: { id: string; label: string; created_at: string };
};

export type HistoryPoint = { run_id: string; created_at: string; label: string; git_sha: string | null; metrics: SuiteBlock };

export type Job = {
  id: string;
  status: "running" | "completed" | "failed";
  done: number;
  total: number | null;
  run_id: string | null;
  error: string | null;
  started: number;
  label: string;
  suites?: string[];
  targets?: Pick<TargetInfo, "id" | "model" | "provider" | "source" | "max_concurrency">[];
};

export type LaunchBody = {
  suites: string[];
  targets: string[];
  label: string;
  repeats: number;
  concurrency: number;
  limit?: number | null;
  max_usd?: number | null;
  options?: Record<string, Record<string, unknown>>;
};

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function qs(params?: Record<string, string | number | undefined | null>): string {
  if (!params) return "";
  const s = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") s.set(k, String(v));
  }
  const out = s.toString();
  return out ? `?${out}` : "";
}

async function handle<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    let message = `${resp.status} ${resp.statusText}`;
    try {
      const body = await resp.json();
      if (body?.detail) message = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* keep status text */
    }
    throw new ApiError(resp.status, message);
  }
  return resp.json() as Promise<T>;
}

export function get<T>(path: string, params?: Record<string, string | number | undefined | null>): Promise<T> {
  return fetch(`/api${path}${qs(params)}`).then((r) => handle<T>(r));
}

export function post<T>(path: string, body: unknown): Promise<T> {
  return fetch(`/api${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  }).then((r) => handle<T>(r));
}

export const api = {
  config: () => get<AppConfig>("/config"),
  runs: (limit = 100) => get<RunListItem[]>("/runs", { limit }),
  run: (ref: string) => get<RunDetail>(`/runs/${encodeURIComponent(ref)}`),
  matrix: (ref: string, suite: string) => get<Matrix>(`/runs/${encodeURIComponent(ref)}/matrix`, { suite }),
  results: (ref: string, params: Record<string, string | number | undefined | null>) =>
    get<{ items: ResultRow[]; total: number }>(`/runs/${encodeURIComponent(ref)}/results`, params),
  result: (id: number) => get<ResultDetail>(`/results/${id}`),
  compare: (baseline: string, candidate: string, map?: string) => get<GateResponse>("/compare", { baseline, candidate, map }),
  history: (suite: string, target: string) => get<HistoryPoint[]>("/history", { suite, target, limit: 60 }),
  series: () => get<Record<string, string[]>>("/series"),
  launch: (body: LaunchBody) => post<Job>("/runs", body),
  estimate: (body: LaunchBody) =>
    post<{ rows: { suite: string; target?: string; items: number; requests: number | string; tokens: number | null; usd: number | null }[]; total_usd: number | null; budget_usd: number | null }>(
      "/estimate",
      body,
    ),
  job: (id: string) => get<Job>(`/jobs/${id}`),
  jobs: () => get<Job[]>("/jobs"),
};
