"""Tiny SVG chart helpers for the self-contained HTML report (no JS libraries, works offline).

Marks follow the report's chart spec: bars <= 24px with 4px rounded data-ends, 2px lines,
hairline solid gridlines, text in ink tokens (never the series color), native <title> tooltips.
"""

from __future__ import annotations

from html import escape


def _nice_max(v: float) -> float:
    if v <= 0:
        return 1.0
    import math

    exp = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 2.5, 5, 10):
        if v <= m * exp:
            return m * exp
    return 10 * exp


def bar_ci_chart(
    rows: list[tuple[str, float | None, float | None, float | None]], width: int = 560, title: str = ""
) -> str:
    """Horizontal pass-rate bars (0-100%) with 95% CI whiskers. One series -> one colour."""
    label_w, right_pad, bar_h, gap = 150, 56, 18, 14
    plot_w = width - label_w - right_pad
    height = 28 + len(rows) * (bar_h + gap)
    out = [f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">']
    for t in (0, 25, 50, 75, 100):
        x = label_w + plot_w * t / 100
        out.append(f'<line x1="{x:.1f}" y1="6" x2="{x:.1f}" y2="{height - 18}" class="grid"/>')
        out.append(f'<text x="{x:.1f}" y="{height - 4}" class="tick" text-anchor="middle">{t}%</text>')
    for i, (label, v, lo, hi) in enumerate(rows):
        y = 8 + i * (bar_h + gap)
        out.append(
            f'<text x="{label_w - 10}" y="{y + bar_h / 2 + 4}" class="label" text-anchor="end">{escape(label)}</text>'
        )
        if v is None:
            continue
        w = max(2.0, plot_w * v)
        r = min(4, w / 2)
        out.append(
            f'<path class="bar" d="M{label_w},{y} h{w - r:.1f} a{r},{r} 0 0 1 {r},{r} v{bar_h - 2 * r} '
            f'a{r},{r} 0 0 1 -{r},{r} h-{w - r:.1f} z"><title>{escape(label)}: {v * 100:.1f}%'
            + (f" (95% CI {lo * 100:.1f}–{hi * 100:.1f}%)" if lo is not None and hi is not None else "")
            + "</title></path>"
        )
        if lo is not None and hi is not None:
            x1, x2, cy = label_w + plot_w * lo, label_w + plot_w * hi, y + bar_h / 2
            out.append(f'<line x1="{x1:.1f}" y1="{cy}" x2="{x2:.1f}" y2="{cy}" class="whisker"/>')
            out.append(f'<line x1="{x1:.1f}" y1="{cy - 5}" x2="{x1:.1f}" y2="{cy + 5}" class="whisker"/>')
            out.append(f'<line x1="{x2:.1f}" y1="{cy - 5}" x2="{x2:.1f}" y2="{cy + 5}" class="whisker"/>')
        out.append(
            f'<text x="{label_w + plot_w + 8}" y="{y + bar_h / 2 + 4}" class="value">{v * 100:.1f}%</text>'
        )
    out.append("</svg>")
    return "".join(out)


def line_chart(
    points: list[tuple[float, float | None]],
    width: int = 560,
    height: int = 170,
    title: str = "",
    y_unit: str = "",
    x_unit: str = "s",
) -> str:
    pts = [(x, y) for x, y in points if y is not None]
    left, right, top, bottom = 48, 16, 10, 26
    pw, ph = width - left - right, height - top - bottom
    out = [f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">']
    if not pts:
        out.append(
            f'<text x="{width / 2}" y="{height / 2}" class="tick" text-anchor="middle">No data</text></svg>'
        )
        return "".join(out)
    xmax = max(x for x, _ in pts) or 1
    ymax = _nice_max(max(y for _, y in pts))
    for i in range(5):
        yv = ymax * i / 4
        yy = top + ph - ph * i / 4
        out.append(f'<line x1="{left}" y1="{yy:.1f}" x2="{left + pw}" y2="{yy:.1f}" class="grid"/>')
        lab = f"{yv:.0f}" if ymax >= 10 else f"{yv:.1f}"
        out.append(
            f'<text x="{left - 6}" y="{yy + 4:.1f}" class="tick" text-anchor="end">{lab}{escape(y_unit)}</text>'
        )
    for i in range(6):
        xv = xmax * i / 5
        out.append(
            f'<text x="{left + pw * i / 5:.1f}" y="{height - 6}" class="tick" text-anchor="middle">'
            f"{xv:.0f}{x_unit}</text>"
        )

    def sx(x: float) -> float:
        return left + pw * x / xmax

    def sy(y: float) -> float:
        return top + ph - ph * min(y, ymax) / ymax

    d = " ".join(f"{'M' if i == 0 else 'L'}{sx(x):.1f},{sy(y):.1f}" for i, (x, y) in enumerate(pts))
    out.append(f'<path d="{d}" class="line"/>')
    for x, y in pts:
        out.append(
            f'<circle cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="6" class="hit"><title>{x:.0f}{x_unit}: '
            f"{y:.1f}{escape(y_unit)}</title></circle>"
        )
    lx, ly = pts[-1]
    out.append(f'<circle cx="{sx(lx):.1f}" cy="{sy(ly):.1f}" r="4" class="dot"/>')
    out.append("</svg>")
    return "".join(out)


def trace_waterfall(spans: list[dict], width: int = 620) -> str:
    if not spans:
        return ""
    t0 = min(s["start_ms"] for s in spans)
    t1 = max(s["end_ms"] for s in spans)
    total = max(t1 - t0, 0.001)
    depth: dict[str, int] = {}
    for s in sorted(spans, key=lambda s: s["start_ms"]):
        depth[s["span_id"]] = depth.get(s.get("parent_span_id") or "", -1) + 1
    label_w, row_h = 210, 20
    plot_w = width - label_w - 70
    height = len(spans) * row_h + 6
    out = [
        f'<svg class="chart trace" viewBox="0 0 {width} {height}" role="img" aria-label="Trace waterfall">'
    ]
    for i, s in enumerate(sorted(spans, key=lambda s: s["start_ms"])):
        y = 3 + i * row_h
        indent = depth.get(s["span_id"], 0) * 10
        name = s["name"] if len(s["name"]) < 30 else s["name"][:28] + "…"
        out.append(f'<text x="{4 + indent}" y="{y + 13}" class="label small">{escape(name)}</text>')
        x = label_w + plot_w * (s["start_ms"] - t0) / total
        w = max(2.0, plot_w * s["duration_ms"] / total)
        cls = "span err" if s.get("status") == "ERROR" else "span"
        attrs = ", ".join(f"{k}={v}" for k, v in list(s.get("attributes", {}).items())[:8])
        out.append(
            f'<rect x="{x:.1f}" y="{y + 3}" width="{w:.1f}" height="12" rx="2" class="{cls}">'
            f"<title>{escape(s['name'])} {s['duration_ms']:.1f}ms {escape(s.get('status_message', ''))}\n"
            f"{escape(attrs)}</title></rect>"
        )
        out.append(
            f'<text x="{min(x + w + 4, width - 60):.1f}" y="{y + 13}" class="tick">{s["duration_ms"]:.0f}ms</text>'
        )
    out.append("</svg>")
    return "".join(out)
