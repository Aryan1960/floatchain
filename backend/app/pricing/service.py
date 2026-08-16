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


def detect_anomalies(
    store: PricingStore, skin_name: str, stattrak: bool
) -> list[AnomalyResult]:
    dataset = load_real_dataset(store, skin_name, stattrak)
    if len(dataset) < MIN_POINTS_FOR_ANOMALY_DETECTION:
        return []

    detector = AnomalyDetector()
    detector.fit(dataset.floats, dataset.prices_cents)
    return detector.evaluate(dataset.floats, dataset.prices_cents)
