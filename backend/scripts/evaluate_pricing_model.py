#!/usr/bin/env python3
"""Offline train/test evaluation of the pricing pipeline against real
collected data. This is the piece that turns every "rough confidence
estimate" from the last few days into an actual measured number.

For each tracked skin with enough real data: split its real (float, price)
observations into train/test, fit the same model service.py would pick
(XGBoost or KNN, by train-set size) on the train split only, then measure
error on the held-out test split against three things:

  1. Our model's prediction
  2. A naive baseline (always guess the training set's mean price) -- if we
     don't beat this, the model isn't learning anything about float at all
  3. CSFloat's own reference.predicted_price for those exact same held-out
     points -- the real "do we add signal beyond CSFloat" comparison

Real data only, same as the live service (see app/pricing/dataset.py) --
synthetic_snapshots is never touched here either.

    backend/.venv/bin/python backend/scripts/evaluate_pricing_model.py
"""

from __future__ import annotations

import csv
import os
import random
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

BACKEND_DIR = Path(__file__).resolve().parent.parent
os.chdir(BACKEND_DIR)
sys.path.insert(0, str(BACKEND_DIR))

from app.config import get_settings  # noqa: E402

HISTORY_FILE = BACKEND_DIR / get_settings().eval_history_path
HISTORY_FIELDS = [
    "timestamp", "real_rows_total", "evaluated_skins", "beat_baseline",
    "beat_csfloat", "csfloat_comparable", "avg_our_mae_cents",
    "avg_baseline_mae_cents", "avg_csfloat_mae_cents",
]


def _append_history(row: dict) -> None:
    """One line per run -- this is what lets us actually see whether real
    performance trends up as more data accumulates, instead of comparing
    today's single run against memory of an earlier one."""
    is_new = not HISTORY_FILE.exists()
    with HISTORY_FILE.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=HISTORY_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow(row)

from app.data.pricing_store import PricingStore  # noqa: E402
from app.data.tracked_skins import TRACKED_SKINS  # noqa: E402
from app.pricing.anomaly import filter_outliers  # noqa: E402
from app.pricing.curve_model import MIN_POINTS_FOR_XGBOOST, CurveModel  # noqa: E402
from app.pricing.knn_fallback import MIN_POINTS_FOR_KNN, KnnModel  # noqa: E402

MIN_POINTS_FOR_EVAL = 15  # need enough real points that an 80/20 split is meaningful
TEST_FRACTION = 0.2
SEED = 20260816  # fixed, so re-running reproduces the same split


@dataclass
class SkinEval:
    skin_name: str
    n_total: int
    n_train: int
    n_train_clean: int
    outliers_removed: int
    n_test: int
    model_type: str
    our_mae: float
    our_mape: float
    baseline_mae: float
    csfloat_mae: float | None
    csfloat_n: int


def _mae(preds: np.ndarray, actual: np.ndarray) -> float:
    return float(np.mean(np.abs(preds - actual)))


def _mape(preds: np.ndarray, actual: np.ndarray) -> float:
    return float(np.mean(np.abs((preds - actual) / actual)) * 100)


def evaluate_skin(store: PricingStore, skin_name: str, stattrak: bool = False) -> SkinEval | str:
    """Returns a SkinEval, or a short string explaining why this skin was skipped."""
    triples = store.real_points_with_benchmark(skin_name, stattrak)
    n_total = len(triples)
    if n_total < MIN_POINTS_FOR_EVAL:
        return f"only {n_total} real points (need {MIN_POINTS_FOR_EVAL}+)"

    rng = random.Random(SEED)
    shuffled = triples[:]
    rng.shuffle(shuffled)
    n_test = max(1, round(n_total * TEST_FRACTION))
    test, train = shuffled[:n_test], shuffled[n_test:]

    train_floats = np.array([t[0] for t in train])
    train_prices = np.array([t[1] for t in train], dtype=float)
    test_floats = np.array([t[0] for t in test])
    test_prices = np.array([t[1] for t in test], dtype=float)

    # Outliers get filtered out of the TRAINING split only -- the test split
    # stays exactly as real listings actually are, so error is measured
    # against the real world, not a version we've cleaned up to look better.
    train_floats, train_prices, outliers_removed = filter_outliers(train_floats, train_prices)

    if len(train_floats) >= MIN_POINTS_FOR_XGBOOST:
        model = CurveModel()
        model.fit(train_floats, train_prices)
        model_type = "xgboost"
    elif len(train_floats) >= MIN_POINTS_FOR_KNN:
        model = KnnModel()
        model.fit(train_floats, train_prices)
        model_type = "knn"
    else:
        return f"train split only {len(train_floats)} points after held-out split + outlier removal"

    our_preds = np.array([model.predict(f) for f in test_floats])
    baseline_preds = np.full_like(test_prices, train_prices.mean())

    csfloat_actual, csfloat_pred = [], []
    for _f, price_cents, predicted_cents in test:
        if predicted_cents is not None:
            csfloat_actual.append(price_cents)
            csfloat_pred.append(predicted_cents)

    csfloat_mae = (
        _mae(np.array(csfloat_pred, dtype=float), np.array(csfloat_actual, dtype=float))
        if csfloat_actual
        else None
    )

    return SkinEval(
        skin_name=skin_name,
        n_total=n_total,
        n_train=len(train),
        n_train_clean=len(train_floats),
        outliers_removed=outliers_removed,
        n_test=len(test),
        model_type=model_type,
        our_mae=_mae(our_preds, test_prices),
        our_mape=_mape(our_preds, test_prices),
        baseline_mae=_mae(baseline_preds, test_prices),
        csfloat_mae=csfloat_mae,
        csfloat_n=len(csfloat_actual),
    )


def main() -> None:
    settings = get_settings()
    store = PricingStore(settings.pricing_db_path)

    results: list[SkinEval] = []
    skipped: list[tuple[str, str]] = []

    try:
        for name in TRACKED_SKINS:
            outcome = evaluate_skin(store, name)
            if isinstance(outcome, str):
                skipped.append((name, outcome))
            else:
                results.append(outcome)
        real_rows_total, _ = store.total_counts()
    finally:
        store.close()

    print(f"Evaluated {len(results)}/{len(TRACKED_SKINS)} tracked skins "
          f"(train/test split={1-TEST_FRACTION:.0%}/{TEST_FRACTION:.0%}, seed={SEED})\n")

    if skipped:
        print("Skipped (not enough data yet):")
        for name, reason in skipped:
            print(f"  {name:<32} {reason}")
        print()

    if not results:
        print("Nothing evaluable yet.")
        _append_history({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "real_rows_total": real_rows_total,
            "evaluated_skins": 0,
            "beat_baseline": 0,
            "beat_csfloat": 0,
            "csfloat_comparable": 0,
            "avg_our_mae_cents": "",
            "avg_baseline_mae_cents": "",
            "avg_csfloat_mae_cents": "",
        })
        return

    results.sort(key=lambda r: r.our_mape)

    header = f"{'skin':<30} {'model':<8} {'train':>5} {'-outl':>5} {'test':>4} {'our $MAE':>10} {'our %err':>9} {'baseline $MAE':>14} {'csfloat $MAE':>13} {'beats baseline':>15} {'beats csfloat':>14}"
    print(header)
    print("-" * len(header))

    beat_baseline_count = 0
    beat_csfloat_count = 0
    csfloat_comparable_count = 0
    our_maes, baseline_maes, csfloat_maes = [], [], []

    for r in results:
        beats_baseline = r.our_mae < r.baseline_mae
        beat_baseline_count += beats_baseline
        our_maes.append(r.our_mae)
        baseline_maes.append(r.baseline_mae)

        csfloat_str = f"${r.csfloat_mae/100:.2f} (n={r.csfloat_n})" if r.csfloat_mae is not None else "n/a"
        beats_csfloat_str = "n/a"
        if r.csfloat_mae is not None:
            csfloat_comparable_count += 1
            beats_csfloat = r.our_mae < r.csfloat_mae
            beat_csfloat_count += beats_csfloat
            beats_csfloat_str = "YES" if beats_csfloat else "no"
            csfloat_maes.append(r.csfloat_mae)

        print(
            f"{r.skin_name:<30} {r.model_type:<8} {r.n_train_clean:>5} {r.outliers_removed:>5} {r.n_test:>4} "
            f"${r.our_mae/100:>8.2f} {r.our_mape:>8.1f}% ${r.baseline_mae/100:>12.2f} "
            f"{csfloat_str:>13} {'YES' if beats_baseline else 'no':>15} {beats_csfloat_str:>14}"
        )

    print()
    print("=" * len(header))
    print("SUMMARY")
    print(f"  Beats naive baseline (always guess train mean): {beat_baseline_count}/{len(results)} skins")
    if csfloat_comparable_count:
        print(f"  Beats CSFloat's own predicted_price:             {beat_csfloat_count}/{csfloat_comparable_count} comparable skins")
        print(f"  Average our MAE:      ${np.mean(our_maes)/100:.2f}")
        print(f"  Average baseline MAE: ${np.mean(baseline_maes)/100:.2f}")
        print(f"  Average CSFloat MAE:  ${np.mean(csfloat_maes)/100:.2f}")
    else:
        print("  No skins had CSFloat predicted_price on held-out points to compare against.")

    _append_history({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "real_rows_total": real_rows_total,
        "evaluated_skins": len(results),
        "beat_baseline": beat_baseline_count,
        "beat_csfloat": beat_csfloat_count,
        "csfloat_comparable": csfloat_comparable_count,
        "avg_our_mae_cents": round(float(np.mean(our_maes)), 1),
        "avg_baseline_mae_cents": round(float(np.mean(baseline_maes)), 1),
        "avg_csfloat_mae_cents": round(float(np.mean(csfloat_maes)), 1) if csfloat_maes else "",
    })
    print(f"\n(appended to {HISTORY_FILE.relative_to(BACKEND_DIR)} for trend tracking)")


if __name__ == "__main__":
    main()
