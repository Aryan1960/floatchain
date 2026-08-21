from __future__ import annotations

import csv
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.config import Settings, get_settings
from app.data.csgo_catalog import CsgoCatalog
from app.data.pricing_store import PricingStore
from app.dependencies import get_catalog, get_pricing_store
from app.pricing import service

router = APIRouter(prefix="/api/pricing", tags=["pricing"])


def _cents_to_dollars(cents: float | int | None) -> float | None:
    return None if cents is None else cents / 100


@router.get("/predict")
async def predict(
    skin_name: str,
    raw_float: float,
    stattrak: bool = False,
    catalog: CsgoCatalog = Depends(get_catalog),
    store: PricingStore = Depends(get_pricing_store),
):
    """Our own float-vs-price model's prediction for a skin/float, alongside
    CSFloat's own predicted_price (pulled from data we've already collected
    locally, no extra API call) as a benchmark -- the point of this layer
    isn't the prediction in isolation, it's whether it adds signal beyond
    what CSFloat already computes for free."""
    skin = catalog.get(skin_name)
    if skin is None:
        raise HTTPException(400, f"Unknown skin: {skin_name!r}")
    if not (skin.min_float <= raw_float <= skin.max_float):
        raise HTTPException(400, f"raw_float {raw_float} is outside {skin_name!r}'s float range")

    result = service.predict_price(store, skin_name, stattrak, raw_float)
    return {
        "skin_name": result.skin_name,
        "stattrak": result.stattrak,
        "float_value": result.float_value,
        "model_price": _cents_to_dollars(result.model_price_cents),
        "model_type": result.model_type,
        "sample_count": result.sample_count,
        "outliers_removed": result.outliers_removed,
        "csfloat_predicted_price": _cents_to_dollars(result.csfloat_predicted_price_cents),
        "csfloat_reference_float_distance": result.csfloat_reference_float_distance,
    }


@router.get("/curve")
async def curve(
    skin_name: str,
    stattrak: bool = False,
    catalog: CsgoCatalog = Depends(get_catalog),
    store: PricingStore = Depends(get_pricing_store),
):
    """Real listing points plus our model's fitted curve, for a chart --
    the same fit predict_price() would produce, just laid out across the
    whole float range instead of collapsed into one number."""
    skin = catalog.get(skin_name)
    if skin is None:
        raise HTTPException(400, f"Unknown skin: {skin_name!r}")

    result = service.curve_data(store, skin_name, stattrak)
    return {
        "skin_name": result.skin_name,
        "stattrak": result.stattrak,
        "model_type": result.model_type,
        "sample_count": result.sample_count,
        "outliers_removed": result.outliers_removed,
        "points": [
            {
                "float_value": p.float_value,
                "price": _cents_to_dollars(p.price_cents),
                "csfloat_predicted_price": _cents_to_dollars(p.csfloat_predicted_price_cents),
                "is_outlier": p.is_outlier,
            }
            for p in result.points
        ],
        "curve": [
            {"float_value": c.float_value, "price": _cents_to_dollars(c.price_cents)}
            for c in result.curve
        ],
    }


@router.get("/anomalies")
async def anomalies(
    skin_name: str,
    stattrak: bool = False,
    catalog: CsgoCatalog = Depends(get_catalog),
    store: PricingStore = Depends(get_pricing_store),
):
    """Flags currently-stored real listings for this skin that look
    mispriced relative to the rest of its own (float, price) cluster."""
    skin = catalog.get(skin_name)
    if skin is None:
        raise HTTPException(400, f"Unknown skin: {skin_name!r}")

    results = service.detect_anomalies(store, skin_name, stattrak)
    return [
        {
            "float_value": r.float_value,
            "price": _cents_to_dollars(r.price_cents),
            "is_anomaly": r.is_anomaly,
            "score": r.score,
        }
        for r in results
    ]


@router.get("/status")
async def status(
    store: PricingStore = Depends(get_pricing_store),
    settings: Settings = Depends(get_settings),
    catalog: CsgoCatalog = Depends(get_catalog),
):
    """Data-collection progress: how much real vs. synthetic data exists,
    per skin, and whether the collector is currently paused. Includes each
    tracked skin's float range so callers (the frontend's predictor picker)
    can build a skin selector scoped to exactly what has real data, with
    everything FloatInput needs, in one call -- rather than the full
    2000+-skin catalog search, most of which was never collected for."""
    real_total, synthetic_total = store.total_counts()
    paused = Path(settings.collector_pause_flag_path).exists()

    by_skin = []
    for row in store.real_summary_by_skin():
        skin = catalog.get(row["skin_name"])
        by_skin.append({
            "skin_name": row["skin_name"],
            "stattrak": bool(row["stattrak"]),
            "sample_count": row["n"],
            "first_seen_at": row["first_seen_at"],
            "last_seen_at": row["last_seen_at"],
            "min_float": skin.min_float if skin else None,
            "max_float": skin.max_float if skin else None,
        })

    return {
        "collector_paused": paused,
        "real_rows": real_total,
        "synthetic_rows": synthetic_total,
        "by_skin": by_skin,
    }


@router.get("/eval-history")
async def eval_history(settings: Settings = Depends(get_settings)):
    """The trend of scheduled evaluate_pricing_model.py runs (see backend/
    scripts/evaluate_pricing_model.py) -- one row per run, so callers can see
    whether real performance is actually trending, not just its latest
    value. Read-only: never triggers a new evaluation, just reads whatever
    the periodic FloatChainEval task has already written."""
    path = Path(settings.eval_history_path)
    if not path.exists():
        return []

    rows = []
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            rows.append({
                "timestamp": row["timestamp"],
                "real_rows_total": int(row["real_rows_total"]),
                "evaluated_skins": int(row["evaluated_skins"]),
                "beat_baseline": int(row["beat_baseline"]),
                "beat_csfloat": int(row["beat_csfloat"]),
                "csfloat_comparable": int(row["csfloat_comparable"]),
                "avg_our_mae": _cents_to_dollars(float(row["avg_our_mae_cents"])) if row["avg_our_mae_cents"] else None,
                "avg_baseline_mae": _cents_to_dollars(float(row["avg_baseline_mae_cents"])) if row["avg_baseline_mae_cents"] else None,
                "avg_csfloat_mae": _cents_to_dollars(float(row["avg_csfloat_mae_cents"])) if row["avg_csfloat_mae_cents"] else None,
            })
    return rows
