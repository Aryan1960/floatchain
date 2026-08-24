import { useEffect, useMemo, useState } from "react";
import { getCurveData, getEvalHistory, getPricePrediction, getPricingStatus } from "../api/client";
import type { CurveData, EvalHistoryRow, PricePrediction, PricingStatus, PricingStatusSkin } from "../types";
import { CurveChart } from "./CurveChart";
import { EvalTrendChart } from "./EvalTrendChart";
import { FloatInput } from "./FloatInput";
import { TreeDiagram } from "./TreeDiagram";

function money(value: number | null): string {
  return value === null ? "—" : `$${value.toFixed(2)}`;
}

// Same threshold app/data/pricing_store.py already uses (LOCAL_PRICE_MAX_FLOAT_DISTANCE)
// for "is stored data close enough to trust" -- reused here rather than inventing a
// second opinion on the same question.
const NEARBY_REAL_DATA_MAX_DISTANCE = 0.02;

export function PricingInsights() {
  const [status, setStatus] = useState<PricingStatus | null>(null);
  const [history, setHistory] = useState<EvalHistoryRow[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [selectedSkin, setSelectedSkin] = useState<PricingStatusSkin | null>(null);
  const [floatValue, setFloatValue] = useState("");
  const [stattrak, setStattrak] = useState(false);
  const [prediction, setPrediction] = useState<PricePrediction | null>(null);
  const [predicting, setPredicting] = useState(false);
  const [predictError, setPredictError] = useState<string | null>(null);

  const [curve, setCurve] = useState<CurveData | null>(null);
  const [curveLoading, setCurveLoading] = useState(false);
  const [curveError, setCurveError] = useState<string | null>(null);

  // Only skins we've actually collected real data for -- picking anything
  // outside this list would always come back "insufficient_data", so there's
  // no point offering the full 2000+-skin catalog here like the calculator does.
  const trackedSkins = useMemo(
    () => (status?.by_skin ?? []).filter((s) => !s.stattrak).sort((a, b) => a.skin_name.localeCompare(b.skin_name)),
    [status]
  );

  useEffect(() => {
    Promise.all([getPricingStatus(), getEvalHistory()])
      .then(([s, h]) => {
        setStatus(s);
        setHistory(h);
      })
      .catch((err) => setLoadError(err instanceof Error ? err.message : "Failed to load pricing data"));
  }, []);

  const latest = history.length > 0 ? history[history.length - 1] : null;

  useEffect(() => {
    if (!selectedSkin) {
      setCurve(null);
      return;
    }
    setCurveLoading(true);
    setCurveError(null);
    getCurveData(selectedSkin.skin_name, stattrak)
      .then(setCurve)
      .catch((err) => setCurveError(err instanceof Error ? err.message : "Failed to load curve"))
      .finally(() => setCurveLoading(false));
  }, [selectedSkin, stattrak]);

  async function handlePredict() {
    if (!selectedSkin || floatValue === "") return;
    setPredicting(true);
    setPredictError(null);
    try {
      const result = await getPricePrediction(selectedSkin.skin_name, Number(floatValue), stattrak);
      setPrediction(result);
    } catch (err) {
      setPredictError(err instanceof Error ? err.message : "Prediction failed");
      setPrediction(null);
    } finally {
      setPredicting(false);
    }
  }

  return (
    <div className="pricing-insights">
      <p className="subtitle">
        A from-scratch price-vs-float model, trained on real listings collected from CSFloat over time and
        benchmarked against CSFloat's own price estimate on the same held-out data. Details in{" "}
        <code>docus/phase2_pricing_pipeline_guide.html</code>.
      </p>

      {loadError && <div className="error-banner">{loadError}</div>}

      {latest && (
        <div className="hero-readout positive">
          <span className="hero-readout__label">Beats CSFloat on</span>
          <span className="hero-readout__value">
            {latest.beat_csfloat}/{latest.evaluated_skins}
          </span>
          <span className="hero-readout__note">tracked skins, measured on real held-out data</span>
        </div>
      )}

      {status && (
        <div className="stat-strip">
          <div className="stat-strip__item">
            <span className="label">Real listings collected</span>
            <span className="value">{status.real_rows.toLocaleString()}</span>
          </div>
          <div className="stat-strip__item">
            <span className="label">Skins tracked</span>
            <span className="value">{status.by_skin.length}</span>
          </div>
          <div className="stat-strip__item">
            <span className="label">Collector</span>
            <span className={`value ${status.collector_paused ? "value--critical" : "value--good"}`}>
              {status.collector_paused ? "Paused" : "Active"}
            </span>
          </div>
        </div>
      )}

      {history.length > 0 && (
        <>
          <h3>Model performance over time</h3>
          <p className="subtitle">
            Each point is a full re-evaluation: fit on 80% of real data, tested against the held-out 20% the
            model never saw, run automatically every 6 hours.
          </p>
          <EvalTrendChart history={history} />

          <table className="outcomes-table eval-history-table">
            <thead>
              <tr>
                <th>Run</th>
                <th>Real rows</th>
                <th>Beats baseline</th>
                <th>Beats CSFloat</th>
                <th>Our avg error</th>
                <th>CSFloat avg error</th>
              </tr>
            </thead>
            <tbody>
              {[...history].reverse().map((row) => (
                <tr key={row.timestamp}>
                  <td>{new Date(row.timestamp).toLocaleString()}</td>
                  <td>{row.real_rows_total.toLocaleString()}</td>
                  <td>
                    {row.beat_baseline}/{row.evaluated_skins}
                  </td>
                  <td>
                    {row.beat_csfloat}/{row.csfloat_comparable}
                  </td>
                  <td>{money(row.avg_our_mae)}</td>
                  <td>{money(row.avg_csfloat_mae)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      <h3>Try a live prediction</h3>
      <p className="subtitle">
        Pick a skin and float to compare our model's price against CSFloat's own estimate. Limited to the{" "}
        {trackedSkins.length} skins we're actually collecting data for — see "Skins tracked" above for the full
        list, or the &sect;4.1 tracked-skin list in the build log for why these specific ones were chosen.
      </p>
      <div className="predictor-form">
        <select
          className="skin-select"
          value={selectedSkin?.skin_name ?? ""}
          onChange={(e) => {
            const skin = trackedSkins.find((s) => s.skin_name === e.target.value) ?? null;
            setSelectedSkin(skin);
            setFloatValue("");
            setPrediction(null);
          }}
        >
          <option value="" disabled>
            Select a tracked skin…
          </option>
          {trackedSkins.map((skin) => (
            <option key={skin.skin_name} value={skin.skin_name}>
              {skin.skin_name} ({skin.sample_count} listings)
            </option>
          ))}
        </select>
        <FloatInput
          value={floatValue}
          min={selectedSkin?.min_float ?? 0}
          max={selectedSkin?.max_float ?? 1}
          disabled={!selectedSkin}
          onChange={setFloatValue}
        />
        <label className="stattrak-toggle">
          <input type="checkbox" checked={stattrak} onChange={(e) => setStattrak(e.target.checked)} />
          StatTrak
        </label>
        <button
          type="button"
          className="predict-button"
          onClick={handlePredict}
          disabled={!selectedSkin || floatValue === "" || predicting}
        >
          {predicting ? "Predicting..." : "Get prediction"}
        </button>
      </div>

      {selectedSkin && (
        <>
          <h3>Price curve</h3>
          <p className="subtitle">
            Every real listing collected for {selectedSkin.skin_name}, plotted by float, with our model's fitted
            curve and CSFloat's own predicted price overlaid — the actual relationship the model learns, not just
            a single prediction.
          </p>
          {curveLoading && <p className="subtitle">Loading curve…</p>}
          {curveError && <div className="error-banner">{curveError}</div>}
          {curve && (
            <CurveChart
              data={curve}
              onSelectFloat={(f) => setFloatValue(f.toFixed(6))}
              selectedFloat={floatValue !== "" && !Number.isNaN(Number(floatValue)) ? Number(floatValue) : null}
              skinMinFloat={selectedSkin.min_float}
              skinMaxFloat={selectedSkin.max_float}
            />
          )}

          {curve && (
            <>
              <h3>Inside the model</h3>
              <p className="subtitle">
                One real tree from {selectedSkin.skin_name}&rsquo;s fitted model &mdash; the actual float thresholds
                it splits on, not a mockup.
              </p>
              <TreeDiagram data={curve} />
            </>
          )}
        </>
      )}

      {predictError && <div className="error-banner">{predictError}</div>}

      {prediction && (
        <div className="prediction-result">
          <div className="prediction-compare">
            <div className="prediction-side">
              <span className="label">Our model ({prediction.model_type})</span>
              <span className="value">{money(prediction.model_price)}</span>
              <span className="prediction-note">
                {prediction.sample_count} real listings
                {prediction.outliers_removed > 0 ? `, ${prediction.outliers_removed} outliers filtered` : ""}
              </span>
            </div>
            <div className="prediction-side">
              <span className="label">CSFloat's estimate</span>
              <span className="value">{money(prediction.csfloat_predicted_price)}</span>
              {prediction.csfloat_reference_float_distance !== null && (
                <span className="prediction-note">
                  nearest reference float distance: {prediction.csfloat_reference_float_distance.toFixed(4)}
                </span>
              )}
            </div>
          </div>
          {prediction.model_type === "insufficient_data" && (
            <p className="prediction-caveat">
              Not enough real data collected for this skin yet ({prediction.sample_count} points) — no model
              prediction to show.
            </p>
          )}
          {prediction.model_type !== "insufficient_data" &&
            prediction.nearest_real_float_distance !== null &&
            prediction.nearest_real_float_distance > NEARBY_REAL_DATA_MAX_DISTANCE && (
              <p className="prediction-caveat">
                The nearest real listing we've actually collected for this skin is {" "}
                {prediction.nearest_real_float_distance.toFixed(4)} float away — this prediction is extrapolating
                across a real gap in the data, not reading off a nearby example. Treat it as a rough guess, not a
                confident number.
              </p>
            )}
        </div>
      )}
    </div>
  );
}
