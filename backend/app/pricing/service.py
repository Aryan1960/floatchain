"""Unified entry point: given a skin/float, decide XGBoost vs KNN vs "not
enough data yet" by real sample count, and report CSFloat's own
predicted_price alongside our prediction as a benchmark -- per CLAUDE.md,
the point of this layer is whether our model adds signal beyond what
CSFloat already computes, not "we fit a curve in a vacuum". Only ever reads
real_snapshots (via dataset.load_real_dataset) -- see that module's
docstring for why synthetic data can't reach this path.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.data.pricing_store import PricingStore
from app.pricing.anomaly import (
    MIN_POINTS_FOR_ANOMALY_DETECTION,
    AnomalyDetector,
    AnomalyResult,
    filter_outliers,
)
from app.pricing.curve_model import MIN_POINTS_FOR_XGBOOST, CurveModel
from app.pricing.dataset import load_real_dataset
from app.pricing.knn_fallback import MIN_POINTS_FOR_KNN, KnnModel

CURVE_SAMPLE_COUNT = 150


@dataclass(frozen=True)
class PricePrediction:
    skin_name: str
    stattrak: bool
    float_value: float
    model_price_cents: float | None
    model_type: str  # "xgboost" | "knn" | "insufficient_data"
    sample_count: int  # raw points available, before outlier filtering
    outliers_removed: int
    csfloat_predicted_price_cents: int | None
    csfloat_reference_float_distance: float | None


def predict_price(
    store: PricingStore, skin_name: str, stattrak: bool, float_value: float
) -> PricePrediction:
    dataset = load_real_dataset(store, skin_name, stattrak)
    n = len(dataset)

    floats, prices, outliers_removed = filter_outliers(dataset.floats, dataset.prices_cents)
    n_clean = len(floats)

    if n_clean >= MIN_POINTS_FOR_XGBOOST:
        model = CurveModel()
        model.fit(floats, prices)
        model_price = model.predict(float_value)
        model_type = "xgboost"
    elif n_clean >= MIN_POINTS_FOR_KNN:
        model = KnnModel()
        model.fit(floats, prices)
        model_price = model.predict(float_value)
        model_type = "knn"
    else:
        model_price = None
        model_type = "insufficient_data"

    reference = store.nearest_real_snapshot(skin_name, stattrak, float_value)
    csfloat_predicted_price_cents = None
    csfloat_reference_float_distance = None
    if reference is not None and reference["predicted_price_cents"] is not None:
        csfloat_predicted_price_cents = reference["predicted_price_cents"]
        csfloat_reference_float_distance = abs(reference["float_value"] - float_value)

    return PricePrediction(
        skin_name=skin_name,
        stattrak=stattrak,
        float_value=float_value,
        model_price_cents=model_price,
        model_type=model_type,
        sample_count=n,
        outliers_removed=outliers_removed,
        csfloat_predicted_price_cents=csfloat_predicted_price_cents,
        csfloat_reference_float_distance=csfloat_reference_float_distance,
    )


@dataclass(frozen=True)
class CurvePoint:
    float_value: float
    price_cents: int
    csfloat_predicted_price_cents: int | None
    is_outlier: bool


@dataclass(frozen=True)
class CurveSample:
    float_value: float
    price_cents: float


@dataclass(frozen=True)
class CurveData:
    skin_name: str
    stattrak: bool
    model_type: str  # "xgboost" | "knn" | "insufficient_data"
    sample_count: int
    outliers_removed: int
    points: list[CurvePoint]
    curve: list[CurveSample]  # empty when model_type is "insufficient_data"


def curve_data(store: PricingStore, skin_name: str, stattrak: bool) -> CurveData:
    """Real listing points plus our model's fitted curve across the skin's
    observed float range -- the data behind /predict, laid out for a chart
    instead of collapsed into one number. Reuses the exact same fit path
    predict_price() uses (same outlier filter, same model choice by sample
    size) so the chart never shows a curve the live predictor wouldn't."""
    triples = store.real_points_with_benchmark(skin_name, stattrak)
    n = len(triples)
    if n == 0:
        return CurveData(skin_name, stattrak, "insufficient_data", 0, 0, [], [])

    floats = np.array([t[0] for t in triples], dtype=float)
    prices = np.array([t[1] for t in triples], dtype=float)

    if n >= MIN_POINTS_FOR_ANOMALY_DETECTION:
        detector = AnomalyDetector()
        detector.fit(floats, prices)
        is_outlier = [r.is_anomaly for r in detector.evaluate(floats, prices)]
    else:
        is_outlier = [False] * n

    outlier_mask = np.array(is_outlier)
    clean_floats = floats[~outlier_mask]
    clean_prices = prices[~outlier_mask]
    outliers_removed = int(outlier_mask.sum())

    model = None
    if len(clean_floats) >= MIN_POINTS_FOR_XGBOOST:
        model = CurveModel()
        model.fit(clean_floats, clean_prices)
        model_type = "xgboost"
    elif len(clean_floats) >= MIN_POINTS_FOR_KNN:
        model = KnnModel()
        model.fit(clean_floats, clean_prices)
        model_type = "knn"
    else:
        model_type = "insufficient_data"

    points = [
        CurvePoint(
            float_value=float(f),
            price_cents=int(p),
            csfloat_predicted_price_cents=cp,
            is_outlier=bool(o),
        )
        for (f, p, cp), o in zip(triples, is_outlier)
    ]

    curve: list[CurveSample] = []
    if model is not None:
        lo, hi = float(floats.min()), float(floats.max())
        span = max(hi - lo, 1e-9)
        for i in range(CURVE_SAMPLE_COUNT):
            fv = lo + span * i / (CURVE_SAMPLE_COUNT - 1)
            curve.append(CurveSample(float_value=fv, price_cents=model.predict(fv)))

    return CurveData(
        skin_name=skin_name,
        stattrak=stattrak,
        model_type=model_type,
        sample_count=n,
        outliers_removed=outliers_removed,
        points=points,
        curve=curve,
    )


def detect_anomalies(
    store: PricingStore, skin_name: str, stattrak: bool
) -> list[AnomalyResult]:
    dataset = load_real_dataset(store, skin_name, stattrak)
    if len(dataset) < MIN_POINTS_FOR_ANOMALY_DETECTION:
        return []

    detector = AnomalyDetector()
    detector.fit(dataset.floats, dataset.prices_cents)
    return detector.evaluate(dataset.floats, dataset.prices_cents)
