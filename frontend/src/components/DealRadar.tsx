import { Fragment, useEffect, useState } from "react";
import { getCurveData, getDeals } from "../api/client";
import type { CurveData, DealCandidate, DealsResponse } from "../types";
import { CurveChart } from "./CurveChart";

function money(value: number): string {
  return `$${value.toFixed(2)}`;
}

function timeAgo(iso: string): string {
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  return `${hours}h ago`;
}

function rowKey(c: DealCandidate): string {
  return `${c.skin_name}-${c.float_value}`;
}

export function DealRadar() {
  const [data, setData] = useState<DealsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Which candidate row's graph is open, if any -- one at a time, same
  // click-to-open-the-real-curve idea as the pricing tab's skin picker,
  // just triggered by a table row instead of a <select>.
  const [expandedKey, setExpandedKey] = useState<string | null>(null);
  const [curve, setCurve] = useState<CurveData | null>(null);
  const [curveLoading, setCurveLoading] = useState(false);
  const [curveError, setCurveError] = useState<string | null>(null);

  useEffect(() => {
    getDeals()
      .then(setData)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load deals"))
      .finally(() => setLoading(false));
  }, []);

  const candidates: DealCandidate[] = data?.candidates ?? [];

  function toggleRow(c: DealCandidate) {
    const key = rowKey(c);
    if (expandedKey === key) {
      setExpandedKey(null);
      setCurve(null);
      setCurveError(null);
      return;
    }
    setExpandedKey(key);
    setCurve(null);
    setCurveError(null);
    setCurveLoading(true);
    // The deal radar only ever scans non-StatTrak listings (see
    // app/pricing/deal_radar.py's scan_for_deals) -- stattrak is always
    // false here, not a guess.
    getCurveData(c.skin_name, false)
      .then(setCurve)
      .catch((err) => setCurveError(err instanceof Error ? err.message : "Failed to load curve"))
      .finally(() => setCurveLoading(false));
  }

  return (
    <div className="deal-radar">
      <p className="subtitle">
        Listings flagged as mispriced relative to our model's fitted curve, across the tracked skins — real
        potential deals, not a live scan of the whole market. Refreshes automatically in step with the price
        collector's own schedule (see the "Last scanned" time below); the top few candidates each get a live
        check to confirm something similar is still findable, everything else is unverified.
      </p>

      {loading && <p className="subtitle">Loading…</p>}
      {error && <div className="error-banner">{error}</div>}

      {data && (
        <p className="subtitle deal-radar__generated-at">
          {data.generated_at ? `Last scanned ${timeAgo(data.generated_at)}` : "No scan has run yet"}
        </p>
      )}

      {data && candidates.length === 0 && !loading && (
        <p className="subtitle">Nothing flagged right now — check back after the next collector sweep.</p>
      )}

      {candidates.length > 0 && (
        <table className="outcomes-table deal-radar-table">
          <thead>
            <tr>
              <th>Skin</th>
              <th>Float</th>
              <th>Price</th>
              <th>Model price</th>
              <th>Discount</th>
              <th>Confidence</th>
              <th>Status</th>
              <th>Last seen</th>
            </tr>
          </thead>
          <tbody>
            {candidates.map((c) => {
              const key = rowKey(c);
              const isOpen = expandedKey === key;
              return (
                <Fragment key={key}>
                  <tr
                    className="deal-radar-row"
                    onClick={() => toggleRow(c)}
                    aria-expanded={isOpen}
                  >
                    <td>{c.skin_name}</td>
                    <td>{c.float_value.toFixed(4)}</td>
                    <td>{money(c.price)}</td>
                    <td>{money(c.model_price)}</td>
                    <td className="deal-radar__discount">-{c.discount_pct.toFixed(0)}%</td>
                    <td>{c.sample_count} listings</td>
                    <td>
                      <span className={c.verified_live ? "wear-badge wear-badge--good" : "wear-badge"}>
                        {c.verified_live ? "verified live" : "unverified"}
                      </span>
                    </td>
                    <td>{timeAgo(c.last_seen_at)}</td>
                  </tr>
                  {isOpen && (
                    <tr className="deal-radar-row-expanded">
                      <td colSpan={8}>
                        <div className="deal-radar-graph">
                          <p className="subtitle">
                            Where this listing (${c.price.toFixed(2)} at float {c.float_value.toFixed(4)}) sits
                            against {c.skin_name}&rsquo;s fitted price curve — the discount shown above is exactly
                            the gap between the marked point and the curve.
                          </p>
                          {curveLoading && <p className="subtitle">Loading curve…</p>}
                          {curveError && <div className="error-banner">{curveError}</div>}
                          {curve && (
                            <CurveChart
                              data={curve}
                              selectedFloat={c.float_value}
                              highlightPoint={{ float: c.float_value, price: c.price }}
                            />
                          )}
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
