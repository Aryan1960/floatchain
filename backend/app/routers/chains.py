from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.chains.models import ChainSearchResult
from app.chains.search import search_chain
from app.data.csfloat_client import CsFloatClient
from app.data.csgo_catalog import CsgoCatalog
from app.data.pricing_store import PricingStore
from app.dependencies import get_catalog, get_csfloat, get_pricing_store
from app.domain import floatmath
from app.domain import probability
from app.domain.rarity import inputs_required
from app.schemas import ChainRequest

router = APIRouter(prefix="/api/chains", tags=["chains"])


@router.post("/evaluate", response_model=None)
async def evaluate_chain(
    request: ChainRequest,
    catalog: CsgoCatalog = Depends(get_catalog),
    csfloat: CsFloatClient = Depends(get_csfloat),
    store: PricingStore = Depends(get_pricing_store),
) -> ChainSearchResult:
    """Same resolution logic as /api/contracts/evaluate -- duplicated here
    rather than extracted into a shared helper, since it's ~15 lines of
    already-tested-piece assembly and contracts.py has no dedicated router
    test guarding its orchestration yet to safely refactor against."""
    resolved = []
    for item in request.inputs:
        skin = catalog.get(item.skin_name)
        if skin is None:
            raise HTTPException(400, f"Unknown skin: {item.skin_name!r}")
        resolved.append((skin, item))

    rarity_ids = {skin.rarity_id for skin, _ in resolved}
    if len(rarity_ids) != 1:
        raise HTTPException(400, "All inputs must share the same rarity tier")
    stattrak_flags = {item.stattrak for _, item in resolved}
    if len(stattrak_flags) != 1:
        raise HTTPException(400, "Cannot mix StatTrak and Normal inputs")

    rarity_id = next(iter(rarity_ids))
    stattrak = next(iter(stattrak_flags))
    required = inputs_required(rarity_id)
    if len(resolved) != required:
        raise HTTPException(
            400, f"This rarity tier requires exactly {required} inputs, got {len(resolved)}"
        )

    try:
        adjusted = [
            floatmath.adjusted_float(item.raw_float, skin.min_float, skin.max_float)
            for skin, item in resolved
        ]
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    avg_adjusted = floatmath.average_adjusted_float(adjusted)

    total_cost = sum(item.price for _, item in resolved)

    full_catalog = await catalog.load()

    try:
        return await search_chain(
            input_skins=[skin for skin, _ in resolved],
            avg_adjusted_float=avg_adjusted,
            total_cost=total_cost,
            stattrak=stattrak,
            catalog=full_catalog,
            store=store,
            csfloat=csfloat,
            beam_width=request.beam_width,
            max_depth=request.max_depth,
            materiality_threshold_pct=request.materiality_threshold_pct,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
