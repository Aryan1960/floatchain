"""Prices a hypothetical trade-up output for the chain search.

A single-contract evaluation (routers/contracts.py) prices each outcome with
exactly one live CSFloat call. Chain search prices far more (skin, float)
pairs per request -- every outcome at every explored depth -- so hitting
live CSFloat for all of them would reintroduce the exact rate-limit
fragility the collector was hardened against. Cascade instead, cheapest and
fastest first:

  1. Phase 2's ML model (pure local SQLite + XGBoost/KNN, no network call,
     safe to call in a tight loop).
  2. A stored real listing close enough in float to trust directly.
  3. Live CSFloat, only as a last resort.
"""

from __future__ import annotations

from app.data.csfloat_client import CsFloatClient
from app.data.pricing_store import LOCAL_PRICE_MAX_FLOAT_DISTANCE, PricingStore
from app.domain.models import SkinCatalogEntry
from app.domain.naming import market_hash_name
from app.domain.wear import wear_name
from app.pricing.service import predict_price


async def price_output(
    skin: SkinCatalogEntry,
    float_value: float,
    stattrak: bool,
    store: PricingStore,
    csfloat: CsFloatClient,
) -> tuple[float | None, str]:
    """Returns (price_in_dollars, source) -- source is "model" | "local" |
    "live" | "none". predict_price() already reports "insufficient_data"
    internally for any skin without enough real rows, so there's no need to
    gate this on the tracked-skin list separately -- that would just
    duplicate what the pricing layer already handles."""
    prediction = predict_price(store, skin.name, stattrak, float_value)
    if prediction.model_type != "insufficient_data" and prediction.model_price_cents is not None:
        return prediction.model_price_cents / 100, "model"

    nearby = store.nearest_real_snapshot(skin.name, stattrak, float_value)
    if nearby is not None and abs(nearby["float_value"] - float_value) <= LOCAL_PRICE_MAX_FLOAT_DISTANCE:
        return nearby["price_cents"] / 100, "local"

    wear = wear_name(float_value)
    hash_name = market_hash_name(skin.name, wear, stattrak)
    price = await csfloat.price_near_float(hash_name, float_value)
    if price is not None:
        return price, "live"
    return None, "none"
