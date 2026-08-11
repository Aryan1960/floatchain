from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app.data.csfloat_client import CsFloatClient
from app.data.csgo_catalog import CsgoCatalog
from app.dependencies import get_catalog, get_csfloat
from app.domain import ev as ev_module
from app.domain import floatmath
from app.domain import montecarlo
from app.domain import probability
from app.domain.models import ContractResult, OutcomeProbability
from app.domain.naming import market_hash_name
from app.domain.rarity import inputs_required
from app.domain.wear import wear_name
from app.schemas import ContractRequest

router = APIRouter(prefix="/api/contracts", tags=["contracts"])


@router.post("/evaluate", response_model=None)
async def evaluate_contract(
    request: ContractRequest,
    catalog: CsgoCatalog = Depends(get_catalog),
    csfloat: CsFloatClient = Depends(get_csfloat),
) -> ContractResult:
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

    try:
        distribution = probability.outcome_distribution(
            [skin for skin, _ in resolved], await catalog.load(), stattrak
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    async def price_outcome(skin, collection, prob) -> OutcomeProbability:
        output_flt = floatmath.output_float(avg_adjusted, skin.min_float, skin.max_float)
        wear = wear_name(output_flt)
        hash_name = market_hash_name(skin.name, wear, stattrak)
        price = await csfloat.price_near_float(hash_name, output_flt)
        return OutcomeProbability(
            skin_name=skin.name,
            collection=collection,
            probability=prob,
            output_float=output_flt,
            wear=wear,
            price=price,
        )

    outcomes = await asyncio.gather(
        *(price_outcome(skin, collection, prob) for skin, collection, prob in distribution)
    )

    priced = [(o.probability, o.price) for o in outcomes]
    ev = ev_module.expected_value(priced)
    profitability = ev_module.profitability_pct(ev, total_cost)
    mc_summary = montecarlo.run_monte_carlo(priced, total_cost, runs=request.monte_carlo_runs)

    return ContractResult(
        avg_adjusted_float=avg_adjusted,
        total_input_cost=total_cost,
        outcomes=tuple(outcomes),
        expected_value=ev,
        profitability_pct=profitability,
        monte_carlo=mc_summary,
    )
