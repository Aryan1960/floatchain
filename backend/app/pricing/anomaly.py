"""Isolation Forest mispricing flag for a skin's currently-stored real
listings. Unsupervised -- no labels needed. Intuition: an outlier gets
isolated by fewer random (feature, split-value) cuts than a normal point,
because it's already off on its own relative to the (float, price) cluster.
Averaging that "cuts to isolate" count across many random trees gives an
anomaly score; useful for flagging cheap inputs (fuel for a profitable
contract) or overpriced outputs (skip) per the roadmap.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.ensemble import IsolationForest

MIN_POINTS_FOR_ANOMALY_DETECTION = 10


@dataclass(frozen=True)
class AnomalyResult:
    float_value: float
    price_cents: int
    is_anomaly: bool
    score: float  # lower (more negative) = more anomalous


class AnomalyDetector:
    def __init__(self, contamination: float = 0.1) -> None:
        self._model = IsolationForest(contamination=contamination, random_state=0)
        self._fitted = False

    def fit(self, floats: np.ndarray, prices_cents: np.ndarray) -> None:
        X = np.column_stack([floats, prices_cents])
        self._model.fit(X)
        self._fitted = True

    def evaluate(self, floats: np.ndarray, prices_cents: np.ndarray) -> list[AnomalyResult]:
        if not self._fitted:
            raise RuntimeError("AnomalyDetector.fit() must be called before evaluate()")
        X = np.column_stack([floats, prices_cents])
        predictions = self._model.predict(X)  # -1 = anomaly, 1 = normal
        scores = self._model.score_samples(X)
        return [
            AnomalyResult(
                float_value=float(f),
                price_cents=int(p),
                is_anomaly=bool(pred == -1),
                score=float(s),
            )
            for f, p, pred, s in zip(floats, prices_cents, predictions, scores)
        ]


def filter_outliers(
    floats: np.ndarray, prices_cents: np.ndarray, contamination: float = 0.1
) -> tuple[np.ndarray, np.ndarray, int]:
    """Drops points the Isolation Forest flags as anomalous before a price
    curve gets fit to them, so one wild listing (a stickered item, a
    fat-fingered ask) doesn't distort the learned curve. Returns
    (kept_floats, kept_prices_cents, removed_count). No-ops below
    MIN_POINTS_FOR_ANOMALY_DETECTION -- too few points to tell a real
    outlier from ordinary variance, so nothing is dropped rather than
    guessing."""
    if len(floats) < MIN_POINTS_FOR_ANOMALY_DETECTION:
        return floats, prices_cents, 0

    detector = AnomalyDetector(contamination=contamination)
    detector.fit(floats, prices_cents)
    results = detector.evaluate(floats, prices_cents)
    keep_mask = np.array([not r.is_anomaly for r in results])
    return floats[keep_mask], prices_cents[keep_mask], int((~keep_mask).sum())
