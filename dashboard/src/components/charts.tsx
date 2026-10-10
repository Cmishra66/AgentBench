import { useMemo, useRef, useState, type MouseEvent } from "react";
import type { Span } from "../api";

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const exp = 10 ** Math.floor(Math.log10(v));
  for (const m of [1, 2, 2.5, 5, 10]) if (v <= m * exp) return m * exp;
  return 10 * exp;
}

/* ------------------------------------------------------------------------------------------------
 * Horizontal pass-rate bars with 95% CI whiskers. One series, one colour; text in ink tokens.
 * ---------------------------------------------------------------------------------------------- */
export type BarRow = { label: string; value: number | null; lo?: number | null; hi?: number | null };

export function BarCI({ rows, title }: { rows: BarRow[]; title: string }) {
  const [hover, setHover] = useState<{ i: number; x: number; y: number } | null>(null);
  const width = 640;
  const labelW = 170;
  const rightPad = 60;
  const barH = 18;
  const gap = 14;
  const plotW = width - labelW - rightPad;
  const height = 30 + rows.length * (barH + gap);
  return (
    <div className="chart-box" onMouseLeave={() => setHover(null)}>
      <svg className="chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={title}>
        {[0, 25, 50, 75, 100].map((t) => {
          const x = labelW + (plotW * t) / 100;
          return (
            <g key={t}>
              <line x1={x} y1={6} x2={x} y2={height - 20} className="grid" />
              <text x={x} y={height - 5} className="tick" textAnchor="middle">
                {t}%
              </text>
            </g>
          );
        })}
        {rows.map((r, i) => {
          const y = 8 + i * (barH + gap);
          const v = r.value ?? 0;
          const w = Math.max(2, plotW * v);
          const rad = Math.min(4, w / 2);
          const cy = y + barH / 2;
          return (
            <g
              key={r.label}
              onMouseMove={(e: MouseEvent<SVGGElement>) => {
                const box = (e.currentTarget.ownerSVGElement as SVGSVGElement).getBoundingClientRect();
                setHover({ i, x: e.clientX - box.left, y: e.clientY - box.top });
              }}
            >
              <rect x={0} y={y - gap / 2} width={width} height={barH + gap} fill="transparent" />
              <text x={labelW - 10} y={cy + 4} className="label" textAnchor="end">
                {r.label.length > 22 ? `${r.label.slice(0, 21)}…` : r.label}
              </text>
              {r.value !== null && (
                <>
                  <path
                    className="bar"
                    d={`M${labelW},${y} h${w - rad} a${rad},${rad} 0 0 1 ${rad},${rad} v${barH - 2 * rad} a${rad},${rad} 0 0 1 -${rad},${rad} h-${w - rad} z`}
                  />
                  {r.lo != null && r.hi != null && (
                    <g className="whisker">
                      <line x1={labelW + plotW * r.lo} x2={labelW + plotW * r.hi} y1={cy} y2={cy} className="whisker" />
                      <line x1={labelW + plotW * r.lo} x2={labelW + plotW * r.lo} y1={cy - 5} y2={cy + 5} className="whisker" />
                      <line x1={labelW + plotW * r.hi} x2={labelW + plotW * r.hi} y1={cy - 5} y2={cy + 5} className="whisker" />
                    </g>
                  )}
                  <text x={labelW + plotW + 8} y={cy + 4} className="value">
                    {(v * 100).toFixed(1)}%
                  </text>
                </>
              )}
            </g>
          );
        })}
      </svg>
      {hover && rows[hover.i] && (
        <div className="tooltip" style={{ left: Math.min(hover.x + 12, 460), top: hover.y + 12 }}>
          <span className="mono">{rows[hover.i].label}</span>
          <br />
          Pass rate <b>{rows[hover.i].value === null ? "–" : `${((rows[hover.i].value ?? 0) * 100).toFixed(1)}%`}</b>
          {rows[hover.i].lo != null && rows[hover.i].hi != null && (
            <>
              <br />
              95% CI {((rows[hover.i].lo ?? 0) * 100).toFixed(1)}–{((rows[hover.i].hi ?? 0) * 100).toFixed(1)}%
            </>
          )}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------------------------------------
 * Single-series line chart over ordered points (runs, or seconds) with a crosshair tooltip.
 * ---------------------------------------------------------------------------------------------- */
export type LinePoint = { x: string; y: number | null; sub?: string };

export function LineChart({
  points,
  title,
  format,
  height = 190,
  yMax,
}: {
  points: LinePoint[];
  title: string;
  format: (v: number) => string;
  height?: number;
  yMax?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  const width = 420;
  const left = 54;
  const right = 14;
  const top = 10;
  const bottom = 24;
  const pw = width - left - right;
  const ph = height - top - bottom;
  const valid = points.filter((p) => p.y !== null) as { x: string; y: number; sub?: string }[];
  const max = yMax ?? niceMax(Math.max(0, ...valid.map((p) => p.y)));
  const sx = (i: number) => left + (points.length <= 1 ? pw / 2 : (pw * i) / (points.length - 1));
  const sy = (v: number) => top + ph - (ph * Math.min(v, max)) / (max || 1);
  const path = useMemo(() => {
    let d = "";
    let started = false;
    points.forEach((p, i) => {
      if (p.y === null) {
        started = false;
        return;
      }
      d += `${started ? "L" : "M"}${sx(i).toFixed(1)},${sy(p.y).toFixed(1)} `;
      started = true;
    });
    return d;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [points, max]);
  if (!valid.length) {
    return <div className="empty note">{title}: no data yet.</div>;
  }
  const lastIdx = points.map((p) => p.y !== null).lastIndexOf(true);
  const onMove = (e: MouseEvent<SVGSVGElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const x = ((e.clientX - box.left) / box.width) * width;
    const idx = points.length <= 1 ? 0 : Math.round(((x - left) / pw) * (points.length - 1));
    setHover(Math.max(0, Math.min(points.length - 1, idx)));
  };
  const hp = hover !== null ? points[hover] : null;
  return (
    <div className="chart-box" ref={ref}>
      <svg
        className="chart"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={title}
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
      >
        {[0, 1, 2, 3, 4].map((i) => {
          const v = (max * i) / 4;
          const y = top + ph - (ph * i) / 4;
          return (
            <g key={i}>
              <line x1={left} x2={left + pw} y1={y} y2={y} className="grid" />
              <text x={left - 6} y={y + 4} className="tick" textAnchor="end">
                {format(v)}
              </text>
            </g>
          );
        })}
        <path d={path} className="line" />
        {hp && hp.y !== null && hover !== null && (
          <>
            <line x1={sx(hover)} x2={sx(hover)} y1={top} y2={top + ph} className="crosshair" />
            <circle cx={sx(hover)} cy={sy(hp.y)} r={4.5} className="dot" />
          </>
        )}
        {lastIdx >= 0 && hover === null && <circle cx={sx(lastIdx)} cy={sy(points[lastIdx].y as number)} r={4} className="dot" />}
        <text x={left} y={height - 6} className="tick">
          {points[0]?.x}
        </text>
        <text x={left + pw} y={height - 6} className="tick" textAnchor="end">
          {points[points.length - 1]?.x}
        </text>
      </svg>
      {hp && (
        <div
          className="tooltip"
          style={{ left: `${Math.min(((sx(hover ?? 0) / width) * 100), 70)}%`, top: 8 }}
          role="status"
        >
          {hp.x}
          {hp.sub ? <span className="note"> {hp.sub}</span> : null}
          <br />
          {title} <b>{hp.y === null ? "–" : format(hp.y)}</b>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------------------------------------
 * Trace waterfall: one row per span, offset from the start of the case.
 * ---------------------------------------------------------------------------------------------- */
export function TraceWaterfall({ spans }: { spans: Span[] }) {
  const [hover, setHover] = useState<number | null>(null);
  if (!spans.length) return <p className="note">No trace recorded.</p>;
  const sorted = [...spans].sort((a, b) => a.start_ms - b.start_ms);
  const t0 = Math.min(...sorted.map((s) => s.start_ms));
  const t1 = Math.max(...sorted.map((s) => s.end_ms));
  const total = Math.max(t1 - t0, 0.001);
  const depth: Record<string, number> = {};
  for (const s of sorted) depth[s.span_id] = (s.parent_span_id ? (depth[s.parent_span_id] ?? -1) : -1) + 1;
  const width = 760;
  const labelW = 230;
  const plotW = width - labelW - 70;
  const rowH = 22;
  const height = sorted.length * rowH + 6;
  return (
    <div className="chart-box" onMouseLeave={() => setHover(null)}>
      <svg className="chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Trace waterfall">
        {sorted.map((s, i) => {
          const y = 3 + i * rowH;
          const x = labelW + (plotW * (s.start_ms - t0)) / total;
          const w = Math.max(2, (plotW * s.duration_ms) / total);
          const name = s.name.length > 30 ? `${s.name.slice(0, 29)}…` : s.name;
          return (
            <g key={s.span_id} onMouseEnter={() => setHover(i)}>
              <rect x={0} y={y} width={width} height={rowH} fill="transparent" />
              <text x={4 + depth[s.span_id] * 12} y={y + 14} className="label" style={{ fontSize: 11 }}>
                {name}
              </text>
              <rect x={x} y={y + 4} width={w} height={12} rx={2} className={s.status === "ERROR" ? "span err" : "span"} />
              <text x={Math.min(x + w + 5, width - 64)} y={y + 14} className="tick">
                {s.duration_ms.toFixed(0)}ms
              </text>
            </g>
          );
        })}
      </svg>
      {hover !== null && sorted[hover] && (
        <div className="tooltip" style={{ left: 12, top: 3 + (hover + 1) * rowH }}>
          <b>{sorted[hover].name}</b> {sorted[hover].duration_ms.toFixed(1)}ms
          {sorted[hover].status === "ERROR" && <span className="trend-worse"> {sorted[hover].status_message}</span>}
          {Object.entries(sorted[hover].attributes)
            .slice(0, 8)
            .map(([k, v]) => (
              <div key={k} className="mono" style={{ fontSize: 11.5 }}>
                {k} = {String(v)}
              </div>
            ))}
        </div>
      )}
    </div>
  );
}
