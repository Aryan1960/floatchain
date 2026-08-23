"""Phase 3: a classical, non-learned beam search answering "sell this
trade-up output now, or feed it into another trade-up?" -- 2-3 steps deep.

Not ML. A hypothetical next-level contract's inputs are modeled as
`inputs_required(tier)` identical copies of one branch's output skin, at
that branch's output float -- consistent with Phase 1/2's existing
idealization of frictionless instant transacting at a stated price, not a
new corner cut for this layer specifically.

Every branch's "chain" value is the child contract's own *optimal* EV
(itself decided sell-vs-chain, recursively), not the child's flat
sum-of-sell-prices -- otherwise a chain whose real edge sits two hops deep
would be silently undervalued regardless of how deep max_depth allows.
"""

from __future__ import annotations

import asyncio

from app.chains.models import ChainBranch, ChainSearchResult
from app.chains.pricing_oracle import price_output
from app.data.csfloat_client import CsFloatClient
from app.data.pricing_store import PricingStore
from app.domain import ev as ev_module
from app.domain import floatmath
from app.domain import probability
from app.domain.models import SkinCatalogEntry
from app.domain.rarity import inputs_required, next_tier_id
from app.domain.wear import wear_name


async def _evaluate_level(
    input_skins: list[SkinCatalogEntry],
    avg_adjusted_float: float,
    total_cost: float,
    stattrak: bool,
    catalog: list[SkinCatalogEntry],
    store: PricingStore,
    csfloat: CsFloatClient,
    beam_width: int,
    depth_remaining: int,
) -> tuple[float, float, tuple[ChainBranch, ...]]:
    """Returns (sell_ev, chain_ev, branches) for one contract level. Raises
    ValueError only if outcome_distribution itself fails (e.g. a Covert
    input with no catalogued Gold-tier match in a given collection --
    catalog gap, or the classic "inputs don't share one rarity tier"). The
    caller is responsible for deciding whether that's a 400 (root call) or a
    forced leaf (recursive call, see the catalog_gap handling below)."""
    distribution = probability.outcome_distribution(input_skins, catalog, stattrak)

    async def _price_one(skin, collection, prob):
        output_flt = floatmath.output_float(avg_adjusted_float, skin.min_float, skin.max_float)
        wear = wear_name(output_flt)
        price, source = await price_output(skin, output_flt, stattrak, store, csfloat)
        return skin, collection, prob, output_flt, wear, price, source

    priced = await asyncio.gather(
        *(_price_one(skin, collection, prob) for skin, collection, prob in distribution)
    )

    sell_ev = ev_module.expected_value([(t[2], t[5]) for t in priced])

    # Rank by probability-weighted dollar value, not probability alone -- a
    # rare-but-high-value outcome is exactly where chaining upside
    # concentrates, and pruning it on probability alone would bias the
    # search toward "chaining doesn't help" for reasons unrelated to
    # chaining. Every outcome is still priced above regardless of beam
    # width; only recursion is gated by it. `sorted()` reorders references
    # without copying, so identity comparison against the original tuples
    # in `priced` is safe and exact.
    ranked = sorted(priced, key=lambda t: t[2] * (t[5] or 0), reverse=True)
    candidate_ids = {id(t) for t in ranked[:beam_width]}

    branches: list[ChainBranch] = []
    for t in priced:
        skin, collection, prob, output_flt, wear, price, source = t
        branches.append(
            await _resolve_branch(
                skin, collection, prob, output_flt, wear, price, source,
                id(t) in candidate_ids, stattrak, catalog, store, csfloat, beam_width, depth_remaining,
                avg_adjusted_float,
            )
        )

    chain_ev = sum(b.probability * b.node_value for b in branches)
    return sell_ev, chain_ev, tuple(branches)


async def _resolve_branch(
    skin: SkinCatalogEntry,
    collection: str,
    prob: float,
    output_flt: float,
    wear: str,
    price: float | None,
    source: str,
    is_candidate: bool,
    stattrak: bool,
    catalog: list[SkinCatalogEntry],
    store: PricingStore,
    csfloat: CsFloatClient,
    beam_width: int,
    depth_remaining: int,
    parent_avg_adjusted: float,
) -> ChainBranch:
    def leaf(reason: str) -> ChainBranch:
        return ChainBranch(
            skin_name=skin.name,
            collection=collection,
            probability=prob,
            output_float=output_flt,
            wear=wear,
            sell_price=price,
            price_source=source,
            child_contract_cost=None,
            child_contract_sell_ev=None,
            child_contract_chain_ev=None,
            node_value=price or 0.0,
            chosen_action="sell",
            explored=False,
            leaf_reason=reason,
            children=(),
        )

    if not is_candidate:
        return leaf("beam_pruned")
    if price is None:
        return leaf("unpriceable")
    if depth_remaining <= 0:
        return leaf("max_depth_reached")
    try:
        next_tier_id(skin.rarity_id)
    except ValueError:
        return leaf("top_of_tier")

    required = inputs_required(skin.rarity_id)
    # output_flt was computed as output_float(parent_avg_adjusted, skin.min_float,
    # skin.max_float) one line up the call stack -- adjusted_float() on it against
    # the same skin's range is the algebraic inverse, so it always just returns
    # parent_avg_adjusted back. Use it directly instead of round-tripping through
    # floating point for no reason.
    child_avg_adjusted = parent_avg_adjusted
    child_total_cost = required * price

    try:
        child_sell_ev, child_chain_ev, child_branches = await _evaluate_level(
            [skin] * required,
            child_avg_adjusted,
            child_total_cost,
            stattrak,
            catalog,
            store,
            csfloat,
            beam_width,
            depth_remaining - 1,
        )
    except ValueError:
        return leaf("catalog_gap")

    # child_chain_ev is the whole child contract's expected payout (funded
    # by `required` units), not one unit's share of it -- dividing back down
    # is what makes this comparable to `price` (one unit, sold). Using
    # child_chain_ev raw here would overstate chaining's value by up to
    # `required`x and make the search's verdict meaningless.
    chain_value_per_unit = child_chain_ev / required
    node_value = max(price, chain_value_per_unit)
    chosen_action = "chain" if child_chain_ev > child_total_cost else "sell"

    return ChainBranch(
        skin_name=skin.name,
        collection=collection,
        probability=prob,
        output_float=output_flt,
        wear=wear,
        sell_price=price,
        price_source=source,
        child_contract_cost=child_total_cost,
        child_contract_sell_ev=child_sell_ev,
        child_contract_chain_ev=child_chain_ev,
        node_value=node_value,
        chosen_action=chosen_action,
        explored=True,
        leaf_reason=None,
        children=child_branches,
    )


async def search_chain(
    input_skins: list[SkinCatalogEntry],
    avg_adjusted_float: float,
    total_cost: float,
    stattrak: bool,
    catalog: list[SkinCatalogEntry],
    store: PricingStore,
    csfloat: CsFloatClient,
    beam_width: int = 3,
    max_depth: int = 2,
    materiality_threshold_pct: float = 5.0,
) -> ChainSearchResult:
    sell_ev, chain_ev, branches = await _evaluate_level(
        input_skins, avg_adjusted_float, total_cost, stattrak,
        catalog, store, csfloat, beam_width, max_depth,
    )

    delta = chain_ev - sell_ev
    delta_pct = (delta / total_cost * 100) if total_cost > 0 else 0.0

    return ChainSearchResult(
        root_total_cost=total_cost,
        single_contract_ev=sell_ev,
        chain_ev=chain_ev,
        chain_ev_delta=delta,
        chain_ev_delta_pct=delta_pct,
        chain_beats_single=delta_pct >= materiality_threshold_pct,
        materiality_threshold_pct=materiality_threshold_pct,
        beam_width=beam_width,
        max_depth=max_depth,
        root_branches=branches,
    )
