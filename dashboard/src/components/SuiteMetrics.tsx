import type { SuiteBlock } from "../api";
import { ms, num, pct, usd } from "../format";

type Row = { label: string; get: (b: SuiteBlock) => string };

const SPECIFIC: Record<string, Row[]> = {
  core: [
    { label: "Schema validity", get: (b) => pct(b.eval_metrics?.schema_valid_rate) },
    { label: "Field F1", get: (b) => pct(b.eval_metrics?.field_f1) },
    { label: "Citation accuracy", get: (b) => pct(b.eval_metrics?.citation_accuracy) },
    { label: "Hallucination on unanswerable", get: (b) => pct(b.eval_metrics?.hallucination_rate) },
    { label: "Judge pass rate", get: (b) => pct(b.eval_metrics?.judge_pass_rate) },
  ],
  injection: [
    { label: "Attack success rate", get: (b) => pct(b.attack_success_rate) },
    { label: "Secret leak rate", get: (b) => pct(b.secret_leak_rate) },
    { label: "Injected instruction followed", get: (b) => pct(b.instruction_follow_rate) },
    { label: "Task still completed", get: (b) => pct(b.task_completion_rate) },
  ],
  tool_use: [
    { label: "Tool selection accuracy", get: (b) => pct(b.tool_selection_accuracy) },
    { label: "Argument accuracy", get: (b) => pct(b.argument_accuracy) },
    { label: "Workflow completion", get: (b) => pct(b.workflow_completion_rate) },
    { label: "Fault recovery", get: (b) => pct(b.fault_recovery_rate) },
    { label: "Duplicate writes", get: (b) => pct(b.duplicate_write_rate) },
    { label: "Unauthorized actions", get: (b) => pct(b.unauthorized_action_rate) },
    { label: "Unnecessary calls", get: (b) => pct(b.unnecessary_call_rate) },
  ],
  isolation: [
    { label: "Scenarios leaking", get: (b) => pct(b.leakage_rate) },
    { label: "Unauthorized disclosures", get: (b) => num(b.unauthorized_disclosures) },
    {
      label: "Critical or high severity",
      get: (b) => num((b.disclosures_by_severity?.critical ?? 0) + (b.disclosures_by_severity?.high ?? 0)),
    },
    { label: "Session isolation pass rate", get: (b) => pct(b.session_isolation_pass_rate) },
    { label: "Recall accuracy", get: (b) => pct(b.recall_accuracy) },
  ],
};

const COMMON: Row[] = [
  { label: "Latency p50", get: (b) => ms(b.latency_ms?.p50) },
  { label: "Latency p95", get: (b) => ms(b.latency_ms?.p95) },
  { label: "Latency p99", get: (b) => ms(b.latency_ms?.p99) },
  { label: "Error rate", get: (b) => pct(b.error_rate) },
  { label: "Retry rate", get: (b) => pct(b.retry_rate) },
  { label: "Tokens in / out", get: (b) => `${num(b.input_tokens)} / ${num(b.output_tokens)}` },
  { label: "Cost per success", get: (b) => usd(b.cost_per_success_usd) },
];

export function SuiteMetrics({ suite, blocks }: { suite: string; blocks: Record<string, SuiteBlock> }) {
  const targets = Object.keys(blocks);
  const rows: Row[] = [
    { label: "Pass rate", get: (b) => `${pct(b.accuracy)} (${b.passed}/${b.executed})` },
    ...(SPECIFIC[suite] ?? []),
    ...COMMON,
  ];
  return (
    <div className="scroll">
      <table>
        <thead>
          <tr>
            <th>Metric</th>
            {targets.map((t) => (
              <th key={t} className="num mono">
                {t}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.label}>
              <td>{r.label}</td>
              {targets.map((t) => (
                <td key={t} className="num">
                  {r.get(blocks[t])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function BreakdownTable({
  title,
  rowsKey,
  blocks,
  cell,
}: {
  title: string;
  rowsKey: string;
  blocks: Record<string, SuiteBlock>;
  cell: (entry: Record<string, number> | undefined) => string;
}) {
  const targets = Object.keys(blocks);
  const keys = Array.from(new Set(targets.flatMap((t) => Object.keys(blocks[t]?.[rowsKey] ?? {})))).sort();
  if (!keys.length) return null;
  return (
    <>
      <h3>{title}</h3>
      <div className="scroll">
        <table>
          <thead>
            <tr>
              <th />
              {targets.map((t) => (
                <th key={t} className="num mono">
                  {t}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {keys.map((k) => (
              <tr key={k}>
                <td>{k.replace(/_/g, " ")}</td>
                {targets.map((t) => (
                  <td key={t} className="num">
                    {cell(blocks[t]?.[rowsKey]?.[k])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
