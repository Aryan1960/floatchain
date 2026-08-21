import { useMemo, useRef, useState } from "react";
import { WEAR_THRESHOLDS } from "../lib/wear";
import type { CurveData } from "../types";

const WIDTH = 640;
const HEIGHT = 320;
const PAD_LEFT = 56;
const PAD_RIGHT = 16;
const PAD_TOP = 16;
const PAD_BOTTOM = 40;

interface HoverInfo {
  x: number;
  y: number;
  lines: string[];
}

interface Props {
  data: CurveData;
  onSelectFloat?: (floatValue: number) => void;
  /** The float currently entered in the predictor form below the chart, if
   * any -- drawn as a persistent marker so you can see where that exact
   * value sits on the curve, distinct from the crosshair that follows the
   * mouse while just exploring. */
  selectedFloat?: number | null;
  /** The skin's actual legal float range (its float-cap min/max), not just
   * the extent of the real listings we happen to have collected -- the axis
   * spans this so the chart shows the skin's whole range, with the part
   * beyond what real data covers rendered as an honest dashed extrapolation
   * (the model really does flatline out there; see curve_model.py). Falls
   * back to the data's own extent if unavailable. */
  skinMinFloat?: number | null;
  skinMaxFloat?: number | null;
}

/** Real listings (float vs. price) for one skin, our model's fitted curve
 * drawn through them as an honest step function (the model really is
 * piecewise-constant -- a diagonal line between samples would misrepresent
 * it), and CSFloat's own predicted_price at each listing. Two real series
 * (our model, CSFloat) get a legend per the project's chart convention;
 * outliers get de-emphasized (muted, not a third color) since they're
 * excluded from the story, not a competing one.
 *
 * Perf note: the mouse crosshair used to be React state, which re-rendered
 * (and re-built a 150-point path string + re-filtered ~350 points) on every
 * single pixel of mouse movement -- the actual cause of the lag reported
 * here, not a cosmetic issue. It's now moved entirely off React: the
 * crosshair's dot/label are mutated directly via refs inside a
 * requestAnimationFrame loop, so moving the mouse never triggers a
 * component re-render at all. Everything mouse-independent (the step path,
 * point lists, wear bands) is memoized so toggling a checkbox or typing a
 * float doesn't rebuild them either. */
export function CurveChart({ data, onSelectFloat, selectedFloat, skinMinFloat, skinMaxFloat }: Props) {
  const svgRef = useRef<SVGSVGElement>(null);
  const crosshairGroupRef = useRef<SVGGElement>(null);
  const crosshairDotRef = useRef<SVGCircleElement>(null);
  const crosshairLabelRef = useRef<SVGTextElement>(null);
  const hoveringDotRef = useRef(false);
  const lastHoverKeyRef = useRef<string | null>(null);
  const rafRef = useRef<number | null>(null);

  const [hover, setHover] = useState<HoverInfo | null>(null);
  const [showRealListings, setShowRealListings] = useState(true);
  const [showOutliers, setShowOutliers] = useState(true);
  const [showCsfloat, setShowCsfloat] = useState(true);

  // dataMin/MaxFloat: the actual extent of real listings we have -- where
  // the curve is genuinely data-backed. minFloat/maxFloat: the axis bounds,
  // which use the skin's real legal float range when known so the chart
  // shows the whole range rather than just whatever we happen to have
  // collected so far.
  const { dataMinFloat, dataMaxFloat, minFloat, maxFloat, floatSpan } = useMemo(() => {
    const floats = data.points.map((p) => p.float_value);
    const dLo = floats.length ? Math.min(...floats) : 0;
    const dHi = floats.length ? Math.max(...floats) : 1;
    const lo = skinMinFloat != null ? Math.min(skinMinFloat, dLo) : dLo;
    const hi = skinMaxFloat != null ? Math.max(skinMaxFloat, dHi) : dHi;
    return { dataMinFloat: dLo, dataMaxFloat: dHi, minFloat: lo, maxFloat: hi, floatSpan: Math.max(hi - lo, 1e-9) };
  }, [data, skinMinFloat, skinMaxFloat]);

  // Scale the Y-axis to the CLEAN price range (outliers + curve + CSFloat
  // estimates), not the raw max -- a single mispriced listing shouldn't
  // squash every normal listing into the bottom of the chart. Outlier dots
  // that exceed this range just get clipped by the SVG viewBox, which is
  // fine since they're de-emphasized rather than part of the story anyway.
  const maxPrice = useMemo(() => {
    const cleanPrices = [
      ...data.points.filter((p) => !p.is_outlier).map((p) => p.price),
      ...data.points.map((p) => p.csfloat_predicted_price).filter((p): p is number => p !== null),
      ...data.curve.map((c) => c.price),
    ];
    return (cleanPrices.length ? Math.max(...cleanPrices) : 1) * 1.08;
  }, [data]);

  const plotW = WIDTH - PAD_LEFT - PAD_RIGHT;
  const plotH = HEIGHT - PAD_TOP - PAD_BOTTOM;
  const x = (f: number) => PAD_LEFT + ((f - minFloat) / floatSpan) * plotW;
  const y = (price: number) => PAD_TOP + (1 - Math.min(price, maxPrice) / maxPrice) * plotH;

  // Step-after path: the model really does output one constant price per
  // leaf, so hold flat until the next sampled float, then jump -- a
  // diagonal line between samples would visually claim a smoothness the
  // model doesn't have. Split into three pieces: the real, data-backed
  // curve in the middle, plus a dashed flat extrapolation on either side
  // out to the skin's true float bounds -- confirmed live (see the
  // extrapolation test in conversation) that the model really does just
  // hold the boundary leaf's value out there, so this draws exactly what
  // predict() would actually say, honestly styled as unmeasured rather
  // than left as blank space that reads like a rendering gap.
  const { stepPath, leadExtrapolationPath, trailExtrapolationPath } = useMemo(() => {
    if (data.curve.length === 0) return { stepPath: "", leadExtrapolationPath: "", trailExtrapolationPath: "" };

    const main = data.curve
      .map((c, i) => {
        const cx = x(c.float_value).toFixed(1);
        const cy = y(c.price).toFixed(1);
        if (i === 0) return `M${cx},${cy}`;
        const prevY = y(data.curve[i - 1].price).toFixed(1);
        return `L${cx},${prevY} L${cx},${cy}`;
      })
      .join(" ");

    const first = data.curve[0];
    const last = data.curve[data.curve.length - 1];
    const lead =
      minFloat < first.float_value
        ? `M${x(minFloat).toFixed(1)},${y(first.price).toFixed(1)} L${x(first.float_value).toFixed(1)},${y(first.price).toFixed(1)}`
        : "";
    const trail =
      maxFloat > last.float_value
        ? `M${x(last.float_value).toFixed(1)},${y(last.price).toFixed(1)} L${x(maxFloat).toFixed(1)},${y(last.price).toFixed(1)}`
        : "";

    return { stepPath: main, leadExtrapolationPath: lead, trailExtrapolationPath: trail };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, minFloat, maxFloat, maxPrice]);

  const csfloatPoints = useMemo(
    () => (showCsfloat ? data.points.filter((p) => p.csfloat_predicted_price !== null) : []),
    [data, showCsfloat]
  );
  const visiblePoints = useMemo(
    () => (showRealListings ? data.points.filter((p) => showOutliers || !p.is_outlier) : []),
    [data, showRealListings, showOutliers]
  );

  const wearBands = useMemo(
    () =>
      WEAR_THRESHOLDS.map(([upper], i) => {
        const lower = i === 0 ? 0 : WEAR_THRESHOLDS[i - 1][0];
        const bandStart = Math.max(lower, minFloat);
        const bandEnd = Math.min(upper, maxFloat);
        return bandEnd > bandStart ? { name: WEAR_THRESHOLDS[i][1], start: bandStart, end: bandEnd } : null;
      }).filter((b): b is { name: string; start: number; end: number } => b !== null),
    [minFloat, maxFloat]
  );

  const yTicks = [0, 0.25, 0.5, 0.75, 1];

  if (data.model_type === "insufficient_data" || data.points.length === 0) {
    return <p className="subtitle">Not enough real data collected for this skin yet to plot a curve.</p>;
  }

  function svgPoint(clientX: number, clientY: number): { x: number; y: number } | null {
    const svg = svgRef.current;
    if (!svg) return null;
    const pt = svg.createSVGPoint();
    pt.x = clientX;
    pt.y = clientY;
    const ctm = svg.getScreenCTM();
    if (!ctm) return null;
    const local = pt.matrixTransform(ctm.inverse());
    return { x: local.x, y: local.y };
  }

  // The step curve's price at any float -- last sample at or before it,
  // matching the real piecewise-constant model rather than interpolating.
  function curvePriceAt(floatValue: number): number | null {
    if (data.curve.length === 0) return null;
    let result = data.curve[0].price;
    for (const c of data.curve) {
      if (c.float_value > floatValue) break;
      result = c.price;
    }
    return result;
  }

  function handlePlotClick(e: React.MouseEvent<SVGSVGElement>) {
    if (!onSelectFloat) return;
    const local = svgPoint(e.clientX, e.clientY);
    if (!local || local.x < PAD_LEFT || local.x > WIDTH - PAD_RIGHT) return;
    const frac = (local.x - PAD_LEFT) / plotW;
    onSelectFloat(Math.min(maxFloat, Math.max(minFloat, minFloat + frac * floatSpan)));
  }

  function hideCrosshair() {
    if (crosshairGroupRef.current) crosshairGroupRef.current.style.display = "none";
  }

  // Nearest real/CSFloat point to the cursor wins, by actual screen
  // distance -- not whichever tiny overlapping circle happens to catch the
  // DOM event. This is what stops dense clusters from flickering between
  // several listings as the mouse crosses a handful of 2px dots in a few
  // pixels of movement: there's one decision per frame, not a race between
  // several elements' own enter/leave events.
  const HOVER_RADIUS = 8;

  function findNearestPoint(localX: number, localY: number): { key: string; cx: number; cy: number; lines: string[] } | null {
    let best: { key: string; cx: number; cy: number; lines: string[] } | null = null;
    let bestDist = HOVER_RADIUS;

    for (const p of visiblePoints) {
      const cx = x(p.float_value);
      const cy = y(p.price);
      const d = Math.hypot(cx - localX, cy - localY);
      if (d < bestDist) {
        bestDist = d;
        best = {
          key: `pt-${p.float_value}-${p.price}`,
          cx,
          cy,
          lines: [`float ${p.float_value.toFixed(4)}`, `$${p.price.toFixed(2)}${p.is_outlier ? " — excluded outlier" : ""}`],
        };
      }
    }
    for (const p of csfloatPoints) {
      const price = p.csfloat_predicted_price as number;
      const cx = x(p.float_value);
      const cy = y(price);
      const d = Math.hypot(cx - localX, cy - localY);
      if (d < bestDist) {
        bestDist = d;
        best = { key: `cf-${p.float_value}`, cx, cy, lines: [`float ${p.float_value.toFixed(4)}`, `CSFloat: $${price.toFixed(2)}`] };
      }
    }
    return best;
  }

  function updatePointer(clientX: number, clientY: number) {
    const local = svgPoint(clientX, clientY);
    if (!local || local.x < PAD_LEFT || local.x > WIDTH - PAD_RIGHT) {
      hideCrosshair();
      if (hoveringDotRef.current) {
        hoveringDotRef.current = false;
        lastHoverKeyRef.current = null;
        setHover(null);
      }
      return;
    }

    const nearest = findNearestPoint(local.x, local.y);
    if (nearest) {
      hideCrosshair();
      // Only touch React state on an actual change of which point is
      // nearest -- steady hover over one dot re-finds the same point every
      // frame but never re-calls setState for it. Tracked via a ref, not
      // the hover state value itself, so this check never reads a stale
      // closure across the requestAnimationFrame boundary.
      if (lastHoverKeyRef.current !== nearest.key) {
        lastHoverKeyRef.current = nearest.key;
        hoveringDotRef.current = true;
        setHover({ x: nearest.cx, y: nearest.cy, lines: nearest.lines });
      }
      return;
    }

    if (hoveringDotRef.current) {
      hoveringDotRef.current = false;
      lastHoverKeyRef.current = null;
      setHover(null);
    }

    const frac = (local.x - PAD_LEFT) / plotW;
    const floatValue = minFloat + frac * floatSpan;
    const price = curvePriceAt(floatValue);
    if (price === null) {
      hideCrosshair();
      return;
    }
    const cx = x(floatValue);
    const cy = y(price);
    const group = crosshairGroupRef.current;
    if (group) group.style.display = "";
    crosshairDotRef.current?.setAttribute("cx", String(cx));
    crosshairDotRef.current?.setAttribute("cy", String(cy));
    if (crosshairLabelRef.current) {
      crosshairLabelRef.current.setAttribute("x", String(Math.min(Math.max(cx, PAD_LEFT + 55), WIDTH - PAD_RIGHT - 55)));
      crosshairLabelRef.current.setAttribute("y", String(Math.max(cy - 10, PAD_TOP + 10)));
      crosshairLabelRef.current.textContent = `${floatValue.toFixed(4)} → $${price.toFixed(2)}`;
    }
  }

  // Coalesced to one update per animation frame, decoupled from however
  // fast the browser fires raw mousemove events.
  function handlePlotMouseMove(e: React.MouseEvent<SVGSVGElement>) {
    const clientX = e.clientX;
    const clientY = e.clientY;
    if (rafRef.current !== null) return;
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = null;
      updatePointer(clientX, clientY);
    });
  }

  function handlePlotMouseLeave() {
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    hideCrosshair();
    setHover(null);
    hoveringDotRef.current = false;
    lastHoverKeyRef.current = null;
  }

  const selectedPrice =
    selectedFloat != null && selectedFloat >= minFloat && selectedFloat <= maxFloat
      ? curvePriceAt(selectedFloat)
      : null;

  return (
    <div className="curve-chart-wrap">
      <div className="curve-toggles">
        <label>
          <input
            type="checkbox"
            checked={showRealListings}
            onChange={(e) => setShowRealListings(e.target.checked)}
          />
          Show real listings
        </label>
        <label>
          <input
            type="checkbox"
            checked={showOutliers}
            onChange={(e) => setShowOutliers(e.target.checked)}
            disabled={!showRealListings}
          />
          Show excluded outliers
        </label>
        <label>
          <input type="checkbox" checked={showCsfloat} onChange={(e) => setShowCsfloat(e.target.checked)} />
          Show CSFloat estimates
        </label>
      </div>

      <div className="curve-chart-svg-wrap">
        <svg
          ref={svgRef}
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          role="img"
          aria-label={`Price versus float for ${data.sample_count} real ${data.skin_name} listings, with our ${data.model_type} model's fitted curve and CSFloat's own predicted price overlaid. Click anywhere on the plot to set that float in the predictor below.`}
          className="curve-chart"
          onClick={handlePlotClick}
          onMouseMove={handlePlotMouseMove}
          onMouseLeave={handlePlotMouseLeave}
        >
          {dataMinFloat > minFloat && (
            <rect x={x(minFloat)} y={PAD_TOP} width={Math.max(x(dataMinFloat) - x(minFloat), 0)} height={plotH} className="curve-no-data-band" />
          )}
          {dataMaxFloat < maxFloat && (
            <rect x={x(dataMaxFloat)} y={PAD_TOP} width={Math.max(x(maxFloat) - x(dataMaxFloat), 0)} height={plotH} className="curve-no-data-band" />
          )}

          {wearBands.map((band) => (
            <rect
              key={band.name}
              x={x(band.start)}
              y={PAD_TOP}
              width={Math.max(x(band.end) - x(band.start), 0)}
              height={plotH}
              className="curve-wear-band"
            />
          ))}
          {wearBands.map((band) => {
            const mid = (x(band.start) + x(band.end)) / 2;
            return (
              <text key={`${band.name}-label`} x={mid} y={PAD_TOP + 12} textAnchor="middle" className="curve-wear-label">
                {band.name}
              </text>
            );
          })}

          {yTicks.map((frac) => {
            const py = PAD_TOP + (1 - frac) * plotH;
            return (
              <g key={frac}>
                <line x1={PAD_LEFT} y1={py} x2={WIDTH - PAD_RIGHT} y2={py} className="chart-gridline" />
                <text x={PAD_LEFT - 8} y={py + 3} className="chart-axis-label" textAnchor="end">
                  ${(frac * maxPrice).toFixed(0)}
                </text>
              </g>
            );
          })}

          {showCsfloat &&
            showRealListings &&
            csfloatPoints.map((p, i) => (
              <line
                key={`miss-${i}`}
                x1={x(p.float_value)}
                y1={y(p.price)}
                x2={x(p.float_value)}
                y2={y(p.csfloat_predicted_price as number)}
                className="curve-miss-line"
              />
            ))}

          {leadExtrapolationPath && <path d={leadExtrapolationPath} className="curve-line curve-line--extrapolated" fill="none" />}
          <path d={stepPath} className="curve-line" fill="none" />
          {trailExtrapolationPath && <path d={trailExtrapolationPath} className="curve-line curve-line--extrapolated" fill="none" />}

          {/* No hover handlers on individual dots -- updatePointer() finds
              the nearest one by actual screen distance every frame instead,
              which is what stops dense clusters from flickering between
              overlapping circles' competing enter/leave events. */}
          {visiblePoints.map((p, i) => (
            <circle
              key={`pt-${i}`}
              cx={x(p.float_value)}
              cy={y(p.price)}
              r={p.is_outlier ? 2 : 2.6}
              className={p.is_outlier ? "curve-dot curve-dot--outlier" : "curve-dot curve-dot--ours"}
            />
          ))}

          {csfloatPoints.map((p, i) => (
            <circle
              key={`cf-${i}`}
              cx={x(p.float_value)}
              cy={y(p.csfloat_predicted_price as number)}
              r={2.2}
              className="curve-dot curve-dot--csfloat"
            />
          ))}

          <g ref={crosshairGroupRef} className="curve-crosshair" style={{ display: "none" }}>
            <circle ref={crosshairDotRef} r={3.5} className="curve-crosshair-dot" />
            <text ref={crosshairLabelRef} textAnchor="middle" className="curve-crosshair-label" />
          </g>

          {selectedPrice !== null && selectedFloat != null && (
            <g className="curve-selected-marker">
              <circle cx={x(selectedFloat)} cy={y(selectedPrice)} r={4} className="curve-selected-dot" />
              <text
                x={Math.min(Math.max(x(selectedFloat), PAD_LEFT + 55), WIDTH - PAD_RIGHT - 55)}
                y={Math.min(y(selectedPrice) + 20, PAD_TOP + plotH - 4)}
                textAnchor="middle"
                className="curve-selected-label"
              >
                {selectedFloat.toFixed(4)} → ${selectedPrice.toFixed(2)}
              </text>
            </g>
          )}

          <text x={PAD_LEFT} y={HEIGHT - 22} className="chart-axis-label" textAnchor="start">
            {minFloat.toFixed(3)}
          </text>
          <text x={WIDTH - PAD_RIGHT} y={HEIGHT - 22} className="chart-axis-label" textAnchor="end">
            {maxFloat.toFixed(3)}
          </text>
          <text x={(PAD_LEFT + WIDTH - PAD_RIGHT) / 2} y={HEIGHT - 6} className="curve-axis-title" textAnchor="middle">
            Float value {onSelectFloat ? "— click to try a prediction" : ""}
          </text>
        </svg>

        {hover && (
          <div
            className="curve-tooltip"
            style={{ left: `${(hover.x / WIDTH) * 100}%`, top: `${(hover.y / HEIGHT) * 100}%` }}
          >
            {hover.lines.map((line, i) => (
              <div key={i}>{line}</div>
            ))}
          </div>
        )}
      </div>

      <div className="curve-legend">
        <span className="curve-legend__item">
          <span className="curve-legend__swatch curve-legend__swatch--ours" />
          Real listings + our {data.model_type} curve
        </span>
        <span className="curve-legend__item">
          <span className="curve-legend__swatch curve-legend__swatch--csfloat" />
          CSFloat&rsquo;s predicted price
        </span>
        {data.outliers_removed > 0 && (
          <span className="curve-legend__item curve-legend__item--muted">
            <span className="curve-legend__swatch curve-legend__swatch--outlier" />
            {data.outliers_removed} outlier{data.outliers_removed === 1 ? "" : "s"} excluded from the fit
          </span>
        )}
        {(leadExtrapolationPath || trailExtrapolationPath) && (
          <span className="curve-legend__item curve-legend__item--muted">
            <span className="curve-legend__dash" />
            dashed = beyond real data, model just holds its last known value
          </span>
        )}
        {selectedPrice !== null && (
          <span className="curve-legend__item">
            <span className="curve-legend__swatch curve-legend__swatch--selected" />
            Float selected below
          </span>
        )}
      </div>

      <details className="curve-table-details">
        <summary>View as table</summary>
        <div className="curve-table-scroll">
          <table className="outcomes-table curve-table">
            <thead>
              <tr>
                <th>Float</th>
                <th>Price</th>
                <th>CSFloat estimate</th>
                <th>Excluded outlier</th>
              </tr>
            </thead>
            <tbody>
              {data.points.map((p, i) => (
                <tr key={i}>
                  <td>{p.float_value.toFixed(6)}</td>
                  <td>${p.price.toFixed(2)}</td>
                  <td>{p.csfloat_predicted_price !== null ? `$${p.csfloat_predicted_price.toFixed(2)}` : "—"}</td>
                  <td>{p.is_outlier ? "yes" : "no"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
