#!/usr/bin/env python3
"""Low/Mid/High tier chain-search survey.

The Phase 3 chain-search backtest (chain_backtest.py) can only test the 24
curated TRACKED_SKINS, and every one of their collections tops out at
Covert -- so the only thing left to chain into is Gold, permanently
blocked by the crates-vs-collections catalog gap. That gave an honest but
limited result: chaining could never be tested past one hop for any
liquid skin (see chain_backtest.py's own docstring for the full story).

A quick, budget-careful live check overturned the assumption that cheap
low-tier skins are illiquid: a modern, popular, full-ladder collection
("The Anubis Collection") returned real live prices at every tier,
Consumer through Covert. So a genuine multi-hop chain CAN be tested for
real, popular collections that don't cap out at Covert -- we just need to
root below Covert using a collection independently verified to have live
listings at every tier used, not an arbitrary catalog substitute (that
substitution is exactly what made chain_backtest.py's v2 attempt fail on
illiquid filler skins).

This script:
  1. Probes CSFloat's live rate-limit budget before doing anything else
     (last_rate_limit_remaining starts as None on a fresh run -- untested
     budget is treated as "unknown, go find out", never "assume safe").
  2. Verifies each candidate collection's liquidity at all 6 weapon tiers
     with one live call per tier (doubles as the probe for the first
     collection). Only collections that fully pass are used.
  3. For each collection that passes, runs three samples -- Low (rooted at
     Uncommon), Mid (rooted at Mythical), High (rooted at Legendary) --
     each at max_depth=1 (one real hop, e.g. Restricted->Classified),
     beam_width=2, pure single-collection inputs (no sibling mixing this
     round -- that machinery is what caused v2's illiquid-filler problem).
  4. Checks the live remaining-budget count before every price lookup and
     aborts the ENTIRE run immediately (not just the current sample) if it
     drops below SAFETY_MARGIN, writing out whatever partial results exist.

Given the shared CSFloat budget is also drawn down by the scheduled Phase 2
collector (scripts/snapshot.py, ~94-96 requests per ~30-min sweep), this is
meant to be run with that collector paused (scripts/pause_collector.sh) --
resume it with scripts/resume_collector.sh once this finishes. A safety
margin trip mid-run (e.g. from an unlucky-timed sweep despite pausing) is
an acceptable, expected outcome, not a bug -- report whatever ran.

    backend/.venv/bin/python backend/scripts/tier_survey_backtest.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
os.chdir(BACKEND_DIR)
sys.path.insert(0, str(BACKEND_DIR))

from app.chains.pricing_oracle import price_output  # noqa: E402
from app.chains.search import search_chain  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.data.csfloat_client import CsFloatClient  # noqa: E402
from app.data.csgo_catalog import CsgoCatalog  # noqa: E402
from app.data.pricing_store import PricingStore  # noqa: E402
from app.domain import floatmath  # noqa: E402
from app.domain.models import SkinCatalogEntry  # noqa: E402
from app.domain.rarity import TIER_ORDER  # noqa: E402

WEAPON_TIERS = TIER_ORDER[:6]  # common..ancient_weapon (Covert) -- excludes Gold

# Same as chain_backtest.py's constant and rationale (feasibility report's
# own empirical figure for real net-of-fee cashout, not applied to the
# actual API, local to this script only).
COMBINED_FEE_RATE = 0.176
MATERIALITY_THRESHOLD_PCT = 5.0

# Deliberately small/cheap for this first pass -- see the plan this script
# was built from: one real hop, narrow recursion, predictable call volume.
BEAM_WIDTH = 2
MAX_DEPTH = 1

# Same circuit-breaker idea as scripts/snapshot.py's _CircuitBreaker,
# replicated locally rather than imported -- small enough, and this script
# has nothing else in common with the collector.
SAFETY_MARGIN = 20

CANDIDATE_COLLECTIONS = ["The Anubis Collection", "The Ancient Collection"]

BANDS: list[tuple[str, str]] = [
    ("Low", "rarity_uncommon_weapon"),
    ("Mid", "rarity_mythical_weapon"),
    ("High", "rarity_legendary_weapon"),
]

RESULTS_PATH = BACKEND_DIR / ".cache" / "tier_survey_results.json"

# Both CANDIDATE_COLLECTIONS were already verified liquid at every weapon
# tier in a prior run this session -- skip re-spending ~12 calls on that
# again for this branch-logging follow-up. Flip back to False if the
# candidate list above ever changes.
SKIP_LIQUIDITY_CHECK = True


class BudgetExhausted(Exception):
    pass


def _check_budget(csfloat: CsFloatClient) -> None:
    """Raises BudgetExhausted if we're at or below the safety margin.
    Deliberately does NOT treat None specially here -- by the time this is
    called anywhere except the very first probe, a real call has already
    happened and populated a real integer."""
    remaining = csfloat.last_rate_limit_remaining
    if remaining is not None and remaining < SAFETY_MARGIN:
        raise BudgetExhausted(f"only {remaining} requests left in CSFloat's rate-limit window")


async def _probe_and_check(csfloat: CsFloatClient, skin: SkinCatalogEntry) -> float | None:
    """One live call, used both to check a specific (skin, tier)'s
    liquidity and, for the very first call of the whole run, to turn
    last_rate_limit_remaining from None into a real number before anything
    else proceeds."""
    from app.domain.naming import market_hash_name
    from app.domain.wear import wear_name

    mid_float = (skin.min_float + skin.max_float) / 2
    wear = wear_name(mid_float)
    hash_name = market_hash_name(skin.name, wear, stattrak=False)
    price = await csfloat.price_near_float(hash_name, mid_float)
    remaining = csfloat.last_rate_limit_remaining
    if remaining is not None and remaining < SAFETY_MARGIN:
        raise BudgetExhausted(f"only {remaining} requests left in CSFloat's rate-limit window")
    return price


async def verify_collection_liquidity(
    collection: str, catalog_by_tier: dict[str, SkinCatalogEntry], csfloat: CsFloatClient
) -> bool:
    print(f"Verifying liquidity for {collection!r}...")
    for tier in WEAPON_TIERS:
        skin = catalog_by_tier.get(tier)
        if skin is None:
            print(f"  {tier:<24} NO SKIN IN THIS COLLECTION AT THIS TIER -- collection unusable")
            return False
        price = await _probe_and_check(csfloat, skin)
        print(f"  {tier:<24} {skin.name:<32} price={price}")
        if price is None:
            print(f"  -> {collection!r} failed liquidity check at {tier}, skipping this collection")
            return False
    print(f"  {collection!r} passed: real live price at every tier\n")
    return True


# Offsets tried, in order, when a target float comes back unpriceable --
# discovered live: a skin verified liquid at one float can still have zero
# listings at a different float within its own range (patchy real coverage,
# not a computation bug). Small enough that this only costs extra calls
# when it actually hits a gap, not on every lookup.
RETRY_OFFSETS = [0.0, 0.02, -0.02, 0.05, -0.05]


async def _price_with_retry(
    skin: SkinCatalogEntry, target_float: float, store: PricingStore, csfloat: CsFloatClient
) -> tuple[float, str, float] | None:
    """Returns (price, source, actual_float_used) or None if every nearby
    float also came back unpriceable."""
    tried: set[float] = set()
    for offset in RETRY_OFFSETS:
        f = min(max(target_float + offset, skin.min_float), skin.max_float)
        if f in tried:
            continue
        tried.add(f)
        price, source = await price_output(skin, f, False, store, csfloat)
        if price is not None:
            return price, source, f
    return None


def _spread_floats(skin: SkinCatalogEntry, n: int) -> list[float]:
    if n == 1:
        return [(skin.min_float + skin.max_float) / 2]
    span = skin.max_float - skin.min_float
    return [skin.min_float + span * (i / (n - 1)) for i in range(n)]


def _price_source_counts(result) -> dict[str, int]:
    counts: dict[str, int] = {}

    def walk(branches):
        for b in branches:
            counts[b.price_source] = counts.get(b.price_source, 0) + 1
            walk(b.children)

    walk(result.root_branches)
    return counts


@dataclass
class SampleResult:
    collection: str
    band: str
    root_tier: str
    total_cost: float
    single_contract_ev: float
    chain_ev: float
    delta_pct_gross: float
    chain_beats_single_gross: bool
    delta_pct_net: float
    chain_beats_single_net: bool
    price_sources: dict[str, int]


async def run_sample(
    collection: str,
    band: str,
    root_skin: SkinCatalogEntry,
    full_catalog: list[SkinCatalogEntry],
    store: PricingStore,
    csfloat: CsFloatClient,
) -> SampleResult:
    required = 10
    priced_inputs: list[tuple[float, float]] = []
    unpriceable_count = 0
    for target_f in _spread_floats(root_skin, required):
        _check_budget(csfloat)
        hit = await _price_with_retry(root_skin, target_f, store, csfloat)
        if hit is None:
            # Patchy real coverage, not a crash -- drop this spread point
            # and keep going; average_adjusted_float below just averages
            # over however many inputs actually priced.
            unpriceable_count += 1
            continue
        price, _source, actual_f = hit
        priced_inputs.append((actual_f, price))

    if not priced_inputs:
        raise RuntimeError(f"root skin {root_skin.name!r} unpriceable at every spread float and every retry offset")
    if unpriceable_count:
        print(f"    ({unpriceable_count}/{required} input floats unpriceable even with retry, dropped)")

    adjusted = [floatmath.adjusted_float(f, root_skin.min_float, root_skin.max_float) for f, _ in priced_inputs]
    avg_adjusted = floatmath.average_adjusted_float(adjusted)
    total_cost = sum(p for _, p in priced_inputs)

    _check_budget(csfloat)
    result = await search_chain(
        input_skins=[root_skin] * len(priced_inputs),
        avg_adjusted_float=avg_adjusted,
        total_cost=total_cost,
        stattrak=False,
        catalog=full_catalog,
        store=store,
        csfloat=csfloat,
        beam_width=BEAM_WIDTH,
        max_depth=MAX_DEPTH,
        materiality_threshold_pct=MATERIALITY_THRESHOLD_PCT,
    )

    print(f"    branches for {collection} / {band}:")
    for b in sorted(result.root_branches, key=lambda b: b.probability * (b.sell_price or 0), reverse=True):
        chain_note = ""
        if b.explored:
            per_unit = b.child_contract_chain_ev / 10 if b.child_contract_chain_ev is not None else None
            chain_note = (
                f" child_cost=${b.child_contract_cost:.2f} child_chain_ev=${b.child_contract_chain_ev:.2f} "
                f"per_unit=${per_unit:.2f}"
            )
        print(f"      {b.skin_name:<32} prob={b.probability:.4f} sell=${(b.sell_price or 0):.2f} "
              f"node_value=${b.node_value:.2f} action={b.chosen_action} explored={b.explored} "
              f"leaf_reason={b.leaf_reason}{chain_note}")

    single_net = result.single_contract_ev * (1 - COMBINED_FEE_RATE)
    chain_net = result.chain_ev * (1 - COMBINED_FEE_RATE)
    delta_net = chain_net - single_net
    delta_pct_net = (delta_net / total_cost * 100) if total_cost > 0 else 0.0

    return SampleResult(
        collection=collection,
        band=band,
        root_tier=root_skin.rarity_id,
        total_cost=total_cost,
        single_contract_ev=result.single_contract_ev,
        chain_ev=result.chain_ev,
        delta_pct_gross=result.chain_ev_delta_pct,
        chain_beats_single_gross=result.chain_beats_single,
        delta_pct_net=delta_pct_net,
        chain_beats_single_net=delta_pct_net >= MATERIALITY_THRESHOLD_PCT,
        price_sources=_price_source_counts(result),
    )


async def main() -> None:
    settings = get_settings()
    catalog = CsgoCatalog(settings)
    full_catalog = await catalog.load()
    store = PricingStore(settings.pricing_db_path)
    csfloat = CsFloatClient(settings)

    results: list[SampleResult] = []
    stopped_early: str | None = None

    try:
        usable_collections: dict[str, dict[str, SkinCatalogEntry]] = {}
        by_collection: dict[str, dict[str, SkinCatalogEntry]] = {}
        for collection in CANDIDATE_COLLECTIONS:
            by_tier: dict[str, SkinCatalogEntry] = {}
            for skin in full_catalog:
                if skin.rarity_id in WEAPON_TIERS and collection in skin.collections:
                    by_tier[skin.rarity_id] = skin
            by_collection[collection] = by_tier

        if SKIP_LIQUIDITY_CHECK:
            # Already verified both collections pass in a prior run this
            # session -- one cheap probe still needed since
            # last_rate_limit_remaining starts as None on a fresh process.
            probe_skin = by_collection[CANDIDATE_COLLECTIONS[0]][WEAPON_TIERS[0]]
            try:
                await _probe_and_check(csfloat, probe_skin)
                usable_collections = dict(by_collection)
            except BudgetExhausted as exc:
                stopped_early = f"during startup probe: {exc}"
        else:
            for collection, by_tier in by_collection.items():
                try:
                    if await verify_collection_liquidity(collection, by_tier, csfloat):
                        usable_collections[collection] = by_tier
                except BudgetExhausted as exc:
                    stopped_early = f"during liquidity check for {collection!r}: {exc}"
                    break

        if stopped_early is None:
            for collection, by_tier in usable_collections.items():
                for band, tier in BANDS:
                    root_skin = by_tier[tier]
                    try:
                        _check_budget(csfloat)
                        sample = await run_sample(collection, band, root_skin, full_catalog, store, csfloat)
                        results.append(sample)
                        sources = ",".join(f"{k}:{v}" for k, v in sorted(sample.price_sources.items()))
                        print(f"{collection:<28} {band:<5} {tier:<24} "
                              f"cost=${sample.total_cost:.2f} single_ev=${sample.single_contract_ev:.2f} "
                              f"chain_ev=${sample.chain_ev:.2f} gross_d%={sample.delta_pct_gross:.1f} "
                              f"net_d%={sample.delta_pct_net:.1f} sources=({sources})")
                    except BudgetExhausted as exc:
                        stopped_early = f"during {collection!r} / {band}: {exc}"
                        break
                if stopped_early:
                    break
    finally:
        store.close()
        await csfloat.aclose()

    print()
    if stopped_early:
        print(f"STOPPED EARLY -- {stopped_early}")
        print(f"Partial results: {len(results)} sample(s) completed before stopping.\n")

    if not results:
        print("No samples completed.")
        return

    header = f"{'collection':<28} {'band':<5} {'root tier':<24} {'cost':>8} {'single EV':>10} {'chain EV':>10} {'gross d%':>9} {'net d%':>8} {'beats(g/n)':>11}"
    print(header)
    print("-" * len(header))
    for r in results:
        beats = f"{'Y' if r.chain_beats_single_gross else 'n'}/{'Y' if r.chain_beats_single_net else 'n'}"
        print(f"{r.collection:<28} {r.band:<5} {r.root_tier:<24} ${r.total_cost:>7.2f} "
              f"${r.single_contract_ev:>9.2f} ${r.chain_ev:>9.2f} {r.delta_pct_gross:>8.1f}% "
              f"{r.delta_pct_net:>7.1f}% {beats:>11}")

    print()
    print("SUMMARY BY BAND")
    for band, _tier in BANDS:
        band_results = [r for r in results if r.band == band]
        if not band_results:
            continue
        avg_gross = sum(r.delta_pct_gross for r in band_results) / len(band_results)
        avg_net = sum(r.delta_pct_net for r in band_results) / len(band_results)
        beats_net = sum(r.chain_beats_single_net for r in band_results)
        print(f"  {band:<5} n={len(band_results)} avg_gross_d%={avg_gross:+.1f} avg_net_d%={avg_net:+.1f} "
              f"beats_single_net={beats_net}/{len(band_results)}")

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "beam_width": BEAM_WIDTH,
        "max_depth": MAX_DEPTH,
        "fee_haircut_applied_to_net_columns": COMBINED_FEE_RATE,
        "stopped_early": stopped_early,
        "samples": [
            {
                "collection": r.collection,
                "band": r.band,
                "root_tier": r.root_tier,
                "total_cost": r.total_cost,
                "single_contract_ev": r.single_contract_ev,
                "chain_ev": r.chain_ev,
                "delta_pct_gross": r.delta_pct_gross,
                "chain_beats_single_gross": r.chain_beats_single_gross,
                "delta_pct_net": r.delta_pct_net,
                "chain_beats_single_net": r.chain_beats_single_net,
                "price_sources": r.price_sources,
            }
            for r in results
        ],
    }, indent=2))
    print(f"\n(written to {RESULTS_PATH.relative_to(BACKEND_DIR)})")


if __name__ == "__main__":
    asyncio.run(main())
