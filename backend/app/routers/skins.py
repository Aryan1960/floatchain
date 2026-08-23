from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.data.csfloat_client import CsFloatClient
from app.data.csgo_catalog import CsgoCatalog
from app.data.pricing_store import LOCAL_PRICE_MAX_FLOAT_DISTANCE, PricingStore
from app.dependencies import get_catalog, get_csfloat, get_pricing_store
from app.domain.naming import market_hash_name
from app.domain.wear import wear_name

router = APIRouter(prefix="/api/skins", tags=["skins"])


@router.get("/search")
async def search_skins(
    q: str,
    limit: int = 20,
    catalog: CsgoCatalog = Depends(get_catalog),
):
    return catalog.search(q, limit=limit)


@router.get("/price")
async def get_skin_price(
    skin_name: str,
    raw_float: float,
    stattrak: bool = False,
    catalog: CsgoCatalog = Depends(get_catalog),
    csfloat: CsFloatClient = Depends(get_csfloat),
    store: PricingStore = Depends(get_pricing_store),
):
    """Best-effort market price for a specific skin at a specific float,
    used by the frontend to suggest a "price paid" value the user can accept
    or override.

    Checks our own already-collected data first. This endpoint is called
    from the Calculator UI -- often repeatedly, as someone types/adjusts a
    float -- completely independently of the background collector's own
    scheduled sweeps. Two uncoordinated sources hitting the same CSFloat API
    key is a real contributor to rate-limit incidents (confirmed in
    practice, not just theoretical); serving from local data whenever we
    have something close enough avoids adding to that traffic at all for
    any skin we're already tracking.
    """
    skin = catalog.get(skin_name)
    if skin is None:
        raise HTTPException(400, f"Unknown skin: {skin_name!r}")
    if not (skin.min_float <= raw_float <= skin.max_float):
        raise HTTPException(
            400, f"raw_float {raw_float} is outside {skin_name!r}'s float range"
        )

    nearby = store.nearest_real_snapshot(skin_name, stattrak, raw_float)
    if nearby is not None and abs(nearby["float_value"] - raw_float) <= LOCAL_PRICE_MAX_FLOAT_DISTANCE:
        return {"price": nearby["price_cents"] / 100, "source": "local"}

    wear = wear_name(raw_float)
    hash_name = market_hash_name(skin.name, wear, stattrak)
    price = await csfloat.price_near_float(hash_name, raw_float)
    return {"price": price, "source": "live"}
