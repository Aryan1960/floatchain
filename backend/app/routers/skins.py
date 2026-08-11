from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.data.csfloat_client import CsFloatClient
from app.data.csgo_catalog import CsgoCatalog
from app.dependencies import get_catalog, get_csfloat
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
):
    """Best-effort live market price for a specific skin at a specific float,
    used by the frontend to suggest a "price paid" value the user can accept
    or override."""
    skin = catalog.get(skin_name)
    if skin is None:
        raise HTTPException(400, f"Unknown skin: {skin_name!r}")
    if not (skin.min_float <= raw_float <= skin.max_float):
        raise HTTPException(
            400, f"raw_float {raw_float} is outside {skin_name!r}'s float range"
        )

    wear = wear_name(raw_float)
    hash_name = market_hash_name(skin.name, wear, stattrak)
    price = await csfloat.price_near_float(hash_name, raw_float)
    return {"price": price}
