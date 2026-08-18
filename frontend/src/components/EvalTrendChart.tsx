import type { EvalHistoryRow } from "../types";

const WIDTH = 640;
const HEIGHT = 160;
const PAD_LEFT = 34;
const PAD_RIGHT = 12;
const PAD_TOP = 12;
const PAD_BOTTOM = 24;

/** Single-series line chart of the win-rate against CSFloat over time, one
 * point per scheduled evaluation run. No legend needed for a single series
 * (the title already names it); direct-labels the first and last point
 * rather than every point, since a dense run of dots would just be noise. */
export function EvalTrendChart({ history }: { history: EvalHistoryRow[] }) {
  if (history.length < 2) {
    return <p className="subtitle">Need at least two evaluation runs to show a trend.</p>;
  }

  const rates = history.map((row) => (row.evaluated_skins > 0 ? row.beat_csfloat / row.evaluated_skins : 0));
  const maxRate = Math.max(0.5, ...rates); // never let the axis read as "topped out" below 50%
  const plotW = WIDTH - PAD_LEFT - PAD_RIGHT;
  const plotH = HEIGHT - PAD_TOP - PAD_BOTTOM;

  const points = rates.map((rate, i) => {
    const x = PAD_LEFT + (i / (rates.length - 1)) * plotW;
    const y = PAD_TOP + (1 - rate / maxRate) * plotH;
    return { x, y, rate, timestamp: history[i].timestamp };
  });

  const path = points.map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
  const fmtTime = (iso: string) =>
    new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });

  return (
    <svg
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      role="img"
      aria-label={`Win rate against CSFloat over ${history.length} evaluation runs, currently ${(rates[rates.length - 1] * 100).toFixed(0)}%`}
      className="eval-trend-chart"
    >
      {[0, 0.25, 0.5, 0.75, 1].map((frac) => {
        const y = PAD_TOP + (1 - frac) * plotH;
        return (
          <g key={frac}>
            <line x1={PAD_LEFT} y1={y} x2={WIDTH - PAD_RIGHT} y2={y} className="chart-gridline" />
            <text x={PAD_LEFT - 6} y={y + 3} className="chart-axis-label" textAnchor="end">
              {(frac * maxRate * 100).toFixed(0)}%
            </text>
          </g>
        );
      })}

      <path d={path} className="chart-line" fill="none" />

      {points.map((p, i) => (
        <circle key={i} cx={p.x} cy={p.y} r={i === points.length - 1 ? 4 : 2.5} className="chart-dot" />
      ))}

      <text x={points[0].x} y={HEIGHT - 6} className="chart-axis-label" textAnchor="start">
        {fmtTime(points[0].timestamp)}
      </text>
      <text x={points[points.length - 1].x} y={HEIGHT - 6} className="chart-axis-label" textAnchor="end">
        {fmtTime(points[points.length - 1].timestamp)}
      </text>
      <text
        x={points[points.length - 1].x}
        y={points[points.length - 1].y - 10}
        className="chart-value-label"
        textAnchor="end"
      >
        {(rates[rates.length - 1] * 100).toFixed(0)}%
      </text>
    </svg>
  );
}
