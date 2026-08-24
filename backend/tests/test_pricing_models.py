import math

import numpy as np
import pytest

from app.data.pricing_store import PricingStore
from app.pricing.anomaly import MIN_POINTS_FOR_ANOMALY_DETECTION, AnomalyDetector, filter_outliers
from app.pricing.curve_model import CurveModel
from app.pricing.knn_fallback import KnnModel
from app.pricing.service import curve_data, detect_anomalies, predict_price


def _decaying_curve(float_value: float, base: float = 10000.0, k: float = 2.5) -> float:
    return base * math.exp(-k * float_value)


def _synthetic_floats_prices(n: int, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    floats = rng.uniform(0.0, 1.0, size=n)
    prices = np.array([_decaying_curve(f) for f in floats])
    return floats, prices


# ---- CurveModel (XGBoost) --------------------------------------------------


def test_curve_model_learns_decreasing_price_with_float():
    floats, prices = _synthetic_floats_prices(200)
    model = CurveModel()
    model.fit(floats, prices)

    low = model.predict(0.02)
    high = model.predict(0.9)
    assert low > high  # matches the decaying curve's shape


def test_curve_model_save_and_load_round_trip(tmp_path):
    floats, prices = _synthetic_floats_prices(100)
    model = CurveModel()
    model.fit(floats, prices)
    prediction_before = model.predict(0.3)

    path = tmp_path / "model.joblib"
    model.save(str(path))

    loaded = CurveModel.load(str(path))
    assert loaded.predict(0.3) == pytest.approx(prediction_before)


def test_curve_model_predict_before_fit_raises():
    model = CurveModel()
    with pytest.raises(RuntimeError):
        model.predict(0.5)


def test_curve_model_first_tree_none_before_fit():
    model = CurveModel()
    assert model.get_first_tree() is None


def _assert_valid_tree_node(node: dict) -> None:
    assert node["type"] in ("split", "leaf")
    if node["type"] == "leaf":
        assert isinstance(node["value"], float)
    else:
        assert isinstance(node["threshold"], float)
        _assert_valid_tree_node(node["left"])
        _assert_valid_tree_node(node["right"])


def test_curve_model_first_tree_shape_after_fit():
    floats, prices = _synthetic_floats_prices(200)
    model = CurveModel()
    model.fit(floats, prices)

    tree = model.get_first_tree()
    assert tree is not None
    _assert_valid_tree_node(tree)
    # max_depth=3 means the root always splits -- a single-node "leaf only"
    # tree would mean the model learned nothing at all from this data.
    assert tree["type"] == "split"


# ---- KnnModel ---------------------------------------------------------------


def test_knn_model_predicts_near_known_point():
    floats = np.array([0.05, 0.10, 0.50, 0.90])
    prices = np.array([9000.0, 8500.0, 3000.0, 1500.0])
    model = KnnModel(k=2)
    model.fit(floats, prices)

    prediction = model.predict(0.06)
    # should land between the two nearest neighbors' prices (0.05, 0.10)
    assert 8500.0 <= prediction <= 9000.0


def test_knn_model_fit_requires_at_least_one_point():
    model = KnnModel()
    with pytest.raises(ValueError):
        model.fit(np.array([]), np.array([]))


def test_knn_model_k_caps_at_available_points():
    floats = np.array([0.1, 0.2])
    prices = np.array([100.0, 200.0])
    model = KnnModel(k=5)  # more than available points
    model.fit(floats, prices)
    assert model.predict(0.15) == pytest.approx(150.0, rel=0.2)


# ---- AnomalyDetector ----------------------------------------------------------


def test_anomaly_detector_flags_injected_outlier():
    rng = np.random.default_rng(1)
    floats = rng.uniform(0.1, 0.5, size=40)
    prices = np.array([_decaying_curve(f) for f in floats])

    # Inject one wildly overpriced listing at a float where price should be low.
    floats = np.append(floats, 0.45)
    prices = np.append(prices, 50000.0)

    detector = AnomalyDetector(contamination=0.1)
    detector.fit(floats, prices)
    results = detector.evaluate(floats, prices)

    outlier_result = results[-1]
    assert outlier_result.is_anomaly is True
    # the outlier should score more anomalous (lower) than a typical normal point
    normal_scores = [r.score for r in results[:-1]]
    assert outlier_result.score < max(normal_scores)


# ---- filter_outliers ---------------------------------------------------------


def test_filter_outliers_removes_injected_outlier():
    rng = np.random.default_rng(2)
    floats = rng.uniform(0.1, 0.5, size=40)
    prices = np.array([_decaying_curve(f) for f in floats])

    floats = np.append(floats, 0.3)
    prices = np.append(prices, 100000.0)  # wildly overpriced relative to its float

    kept_floats, kept_prices, removed = filter_outliers(floats, prices)

    assert removed >= 1
    assert 100000.0 not in kept_prices
    assert len(kept_floats) == len(floats) - removed


def test_filter_outliers_noops_below_threshold():
    floats = np.array([0.1, 0.2, 0.3])
    prices = np.array([100.0, 90.0, 80.0])

    kept_floats, kept_prices, removed = filter_outliers(floats, prices)

    assert removed == 0
    assert len(kept_floats) == 3
    np.testing.assert_array_equal(kept_floats, floats)
    np.testing.assert_array_equal(kept_prices, prices)


# ---- service.predict_price / detect_anomalies (threshold selection) --------


def _insert_real_points(store: PricingStore, skin_name: str, n: int, seed: int = 0) -> None:
    floats, prices = _synthetic_floats_prices(n, seed=seed)
    for i, (f, p) in enumerate(zip(floats, prices)):
        store.upsert_real_snapshot(
            listing_id=f"{skin_name}-{seed}-{i}",
            market_hash_name=f"{skin_name} (Field-Tested)",
            skin_name=skin_name,
            stattrak=False,
            float_value=float(f),
            price_cents=int(p),
            predicted_price_cents=int(p * 0.95),
            seen_at="2026-08-12T00:00:00+00:00",
        )


@pytest.fixture
def store(tmp_path):
    s = PricingStore(tmp_path / "pricing.db")
    yield s
    s.close()


def test_predict_price_insufficient_data_below_knn_threshold(store):
    _insert_real_points(store, "Test Skin", n=3)
    result = predict_price(store, "Test Skin", stattrak=False, float_value=0.3)
    assert result.model_type == "insufficient_data"
    assert result.model_price_cents is None
    assert result.sample_count == 3


def test_predict_price_uses_knn_between_thresholds(store):
    _insert_real_points(store, "Test Skin", n=20)
    result = predict_price(store, "Test Skin", stattrak=False, float_value=0.3)
    assert result.model_type == "knn"
    assert result.model_price_cents is not None
    assert result.sample_count == 20


def test_predict_price_uses_xgboost_above_threshold(store):
    _insert_real_points(store, "Test Skin", n=60)
    result = predict_price(store, "Test Skin", stattrak=False, float_value=0.3)
    assert result.model_type == "xgboost"
    assert result.model_price_cents is not None
    assert result.sample_count == 60


def test_predict_price_reports_outliers_removed(store):
    _insert_real_points(store, "Test Skin", n=60)
    # One wildly-mispriced listing on top of the clean synthetic curve.
    store.upsert_real_snapshot(
        listing_id="outlier-1",
        market_hash_name="Test Skin (Field-Tested)",
        skin_name="Test Skin",
        stattrak=False,
        float_value=0.3,
        price_cents=999_999,
        predicted_price_cents=5000,
        seen_at="2026-08-12T00:00:00+00:00",
    )

    result = predict_price(store, "Test Skin", stattrak=False, float_value=0.3)

    assert result.sample_count == 61  # raw count, includes the outlier
    assert result.outliers_removed >= 1
    assert result.model_type == "xgboost"


def test_predict_price_surfaces_csfloat_benchmark(store):
    _insert_real_points(store, "Test Skin", n=20)
    result = predict_price(store, "Test Skin", stattrak=False, float_value=0.3)
    assert result.csfloat_predicted_price_cents is not None
    assert result.csfloat_reference_float_distance is not None
    assert result.csfloat_reference_float_distance >= 0


def test_predict_price_nearest_real_float_distance_is_exact(store):
    # Two points far apart, nothing near the query float -- the reported
    # distance should be exactly the gap to the closer one, not an average
    # or a guess, and independent of outlier filtering / model choice.
    for i, f in enumerate([0.1, 0.9]):
        store.upsert_real_snapshot(
            listing_id=f"gap-{i}",
            market_hash_name="Gap Skin (Field-Tested)",
            skin_name="Gap Skin",
            stattrak=False,
            float_value=f,
            price_cents=5000,
            predicted_price_cents=4800,
            seen_at="2026-08-12T00:00:00+00:00",
        )
    result = predict_price(store, "Gap Skin", stattrak=False, float_value=0.35)
    assert result.nearest_real_float_distance == pytest.approx(0.25)  # closer to 0.1 than 0.9


def test_predict_price_nearest_real_float_distance_none_with_no_data(store):
    result = predict_price(store, "Empty Skin", stattrak=False, float_value=0.3)
    assert result.sample_count == 0
    assert result.nearest_real_float_distance is None


def test_predict_price_never_reads_synthetic_table(store):
    # Only synthetic data exists for this skin -- real table is empty.
    store.insert_synthetic(
        skin_name="Synthetic Only Skin",
        stattrak=False,
        float_value=0.3,
        price_cents=5000,
        generated_at="2026-08-12T00:00:00+00:00",
    )
    result = predict_price(store, "Synthetic Only Skin", stattrak=False, float_value=0.3)
    assert result.model_type == "insufficient_data"
    assert result.sample_count == 0


def test_detect_anomalies_returns_empty_below_threshold(store):
    _insert_real_points(store, "Test Skin", n=MIN_POINTS_FOR_ANOMALY_DETECTION - 1)
    results = detect_anomalies(store, "Test Skin", stattrak=False)
    assert results == []


def test_detect_anomalies_runs_above_threshold(store):
    _insert_real_points(store, "Test Skin", n=30)
    results = detect_anomalies(store, "Test Skin", stattrak=False)
    assert len(results) == 30
    # default contamination=0.1 over 30 points should flag a handful, not none/all
    flagged = sum(1 for r in results if r.is_anomaly)
    assert 1 <= flagged <= 8


# ---- service.curve_data (first_tree) ----------------------------------------


def test_curve_data_first_tree_present_for_xgboost(store):
    _insert_real_points(store, "Test Skin", n=60)
    result = curve_data(store, "Test Skin", stattrak=False)
    assert result.model_type == "xgboost"
    assert result.first_tree is not None
    assert result.first_tree["type"] in ("split", "leaf")


def test_curve_data_first_tree_none_for_knn(store):
    _insert_real_points(store, "Test Skin", n=20)
    result = curve_data(store, "Test Skin", stattrak=False)
    assert result.model_type == "knn"
    assert result.first_tree is None


def test_curve_data_first_tree_none_for_insufficient_data(store):
    _insert_real_points(store, "Test Skin", n=3)
    result = curve_data(store, "Test Skin", stattrak=False)
    assert result.model_type == "insufficient_data"
    assert result.first_tree is None
