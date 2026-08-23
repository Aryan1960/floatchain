#!/usr/bin/env python3
"""Phase 3's real exit criterion, per the project's own roadmap: not "the
beam search runs on one contract" but "does chaining actually beat the best
single-shot contract, net of fees, across many real contracts." Nobody had
run that sweep yet -- this script is that sweep.

Three attempts got here:

v1 rooted each sample at the tracked skin's own tier, which for 16/24
tracked skins (already Covert-tier) meant the very first move was into
Gold -- the one catalog-wide gap this project already knows about (gold
items are catalogued by `crates`, not `collections`; see csgo_catalog.py's
docstring). Only 6/24 samples ran.

v2 tried rooting lower in each collection's own tier ladder using
`catalog_index[collection][tier][0]` -- any real catalog skin at that
(collection, tier) -- as the representative input. This raised 14/24 to
run, but a live analysis of the real catalog also corrected a wrong
assumption from v1's own writeup: 88/91 collections with a Rare-tier skin
also have Mythical, 78/91 also have Legendary, 65/78 also have Covert --
tier-to-tier chainability is *healthy* (83-97%) across the catalog. The
actual failure mode wasn't missing tiers, it was liquidity: an arbitrary
skin at a lower tier in a tracked skin's collection is almost never itself
one of Phase 2's curated, liquid TRACKED_SKINS -- most of the wider catalog
has zero CSFloat listings at all for a given wear, not just a wide float
gap. Confirmed directly: mixing in an arbitrary "safe" sibling collection
(structurally chainable, per the tier map) still failed on real skins like
"FAMAS | Sergeant" and "AUG | Torque" with zero live listings.

v3 (this version) only ever roots at, or mixes in, a skin that is ITSELF
one of TRACKED_SKINS -- i.e. only uses tiers/collections Phase 2's own
data-collection has already curated for liquidity, never an arbitrary
catalog substitute. Concretely: for a tracked skin, find the LOWEST-tier
tracked skin sharing its collection and root there instead of at the
skin's own tier (several tracked skins share a collection at different
tiers, e.g. Five-SeveN | Monkey Business (Classified) and MAC-10 | Neon
Rider (Covert) both being in "The Chroma 2 Collection" -- rooting at Monkey
Business's tier lets the search reach Neon Rider's tier for real, using
only curated skins on both ends). A tracked skin whose collection has no
lower tracked anchor (it's the only tracked representative, and it's
already Covert) is honestly skipped -- there's nothing this project's data
can test for it today.

Mixing (the guide's documented "Collection Cross-Pollination") is layered
on top for realism, restricted to sibling collections that are ALSO
TRACKED_SKINS collections for the same liquidity reason, with a further
safety net: `probability.outcome_distribution` raises for the WHOLE
contract if any contributing collection lacks an eligible output at the
next tier, so a sibling is only used if it clears that hop AND its
representative skin can actually be priced -- if not, the sample falls
back to pure single-collection input rather than failing outright.

IMPORTANT known limit, discovered by running v3: even with a correctly-
liquid root and sibling, every sample here still reports exactly 0.0%
delta between chain_ev and single_contract_ev. That's not a v3 bug --
verified directly by comparing MAX_DEPTH=1 against MAX_DEPTH=2 (identical
results either way). It's because every one of TRACKED_SKINS' collections
tops out at Covert, so the *only* tier a Covert output could ever chain
into is Gold -- which is always blocked by the same crates-not-collections
gap. single_contract_ev here already means "run the one real contract this
project's data supports (e.g. Classified->Covert) and sell"; chain_ev can
only ever equal that, because there is no second real contract this
catalog can resolve for a liquid weapon skin. Genuinely testing 2+ hops
would need either the Gold/crates mapping fixed, or liquidity data for
lower (Uncommon/Rare) tiers that TRACKED_SKINS was never curated to cover.

    backend/.venv/bin/python backend/scripts/chain_backtest.py
"""

from __future__ import annotations

import asyncio
import hashlib
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
from app.data.tracked_skins import TRACKED_SKINS  # noqa: E402
from app.domain import floatmath  # noqa: E402
from app.domain.models import SkinCatalogEntry  # noqa: E402
from app.domain.rarity import TIER_ORDER, inputs_required  # noqa: E402

WEAPON_TIERS = TIER_ORDER[:6]  # common..ancient_weapon (Covert) -- excludes Gold
COVERT_IDX = WEAPON_TIERS.index("rarity_ancient_weapon")

# The feasibility report's own empirical claim: "quoted gross EV figures are
# commonly overstated by roughly 17.6% relative to what you actually net
# after Steam fees" -- a better-sourced number than a flat 15% guess (which
# only captured the Steam-side cut, not the third-party cashout cut
# CLAUDE.md also documents: Steam proceeds are wallet-locked, so a real
# cash-out needs a third-party market's own fee on top). A flat constant
# either way, not a real fee model -- good enough to see whether chaining
# survives a fee haircut at all, not a precise P&L. Not applied anywhere in
# the actual API (Phase 1's /api/contracts/evaluate or Phase 3's
# /api/chains/evaluate both remain gross-of-fees); local to this script.
COMBINED_FEE_RATE = 0.176

# Minority share of inputs drawn from a safe sibling collection when one is
# found (see _find_safe_sibling) -- majority stays with the tracked skin's
# own collection so its real accumulated pricing data still dominates.
SIBLING_INPUT_SHARE = 0.3

BEAM_WIDTH = 3
MAX_DEPTH = 2
MATERIALITY_THRESHOLD_PCT = 5.0

RESULTS_PATH = BACKEND_DIR / ".cache" / "chain_backtest_results.json"


@dataclass
class SampleResult:
    skin_name: str
    root_collection: str
    root_tier: str
    mixed_with: str | None
    total_cost: float
    single_contract_ev: float
    chain_ev: float
    delta_pct_gross: float
    chain_beats_single_gross: bool
    single_contract_ev_net: float
    chain_ev_net: float
    delta_pct_net: float
    chain_beats_single_net: bool
    price_sources: dict[str, int]


def _build_collection_tier_map(catalog: list[SkinCatalogEntry]) -> dict[str, set[str]]:
    """collection name -> set of weapon rarity_ids present in it. No
    souvenir filtering: per CLAUDE.md, `souvenir` is a pattern-level
    capability flag ("can this pattern exist as souvenir"), not a per-item
    marker -- filtering on it would wrongly exclude most weapon skins."""
    coll_tiers: dict[str, set[str]] = {}
    for skin in catalog:
        if skin.rarity_id not in WEAPON_TIERS:
            continue
        for collection in skin.collections:
            coll_tiers.setdefault(collection, set()).add(skin.rarity_id)
    return coll_tiers


def _build_catalog_index(catalog: list[SkinCatalogEntry]) -> dict[str, dict[str, list[SkinCatalogEntry]]]:
    """(collection, rarity_id) -> sorted list of real catalog skins there --
    fallback only, when a sibling collection is chainable but has no tracked
    skin at the exact tier needed (see _find_safe_sibling)."""
    index: dict[str, dict[str, list[SkinCatalogEntry]]] = {}
    for skin in catalog:
        if skin.rarity_id not in WEAPON_TIERS:
            continue
        for collection in skin.collections:
            index.setdefault(collection, {}).setdefault(skin.rarity_id, []).append(skin)
    for by_tier in index.values():
        for skins in by_tier.values():
            skins.sort(key=lambda s: s.name)
    return index


def _build_tracked_index(catalog: CsgoCatalog) -> dict[str, dict[str, SkinCatalogEntry]]:
    """collection -> {rarity_id: tracked SkinCatalogEntry}, from
    TRACKED_SKINS only -- the only skins this project has real accumulated
    liquidity for. A skin's first listed collection is treated as its home
    collection, matching how it's used everywhere else in this script."""
    index: dict[str, dict[str, SkinCatalogEntry]] = {}
    for name in TRACKED_SKINS:
        skin = catalog.get(name)
        if skin is None or skin.rarity_id not in WEAPON_TIERS:
            continue
        index.setdefault(skin.collections[0], {})[skin.rarity_id] = skin
    return index


def _choose_root(
    skin: SkinCatalogEntry, primary_collection: str, tracked_index: dict[str, dict[str, SkinCatalogEntry]]
) -> tuple[SkinCatalogEntry, str] | None:
    """Root at the lowest-tier TRACKED skin sharing this collection -- never
    an arbitrary catalog substitute, so the root input is always something
    Phase 2 has real liquidity data for. Returns None if the lowest tracked
    anchor for this collection is already Covert (nothing lower exists to
    root at, and Covert can only try to move into the blocked Gold tier)."""
    by_tier = tracked_index.get(primary_collection, {})
    lowest_tier = min(by_tier, key=lambda t: WEAPON_TIERS.index(t))
    if lowest_tier == "rarity_ancient_weapon":
        return None
    return by_tier[lowest_tier], lowest_tier


def _find_safe_sibling(
    primary_collection: str,
    root_tier: str,
    tracked_index: dict[str, dict[str, SkinCatalogEntry]],
    coll_tiers: dict[str, set[str]],
    catalog_index: dict[str, dict[str, list[SkinCatalogEntry]]],
) -> tuple[str, SkinCatalogEntry] | None:
    """A sibling is only safe to mix in if it clears the SAME immediate
    next hop the primary collection needs -- outcome_distribution raises
    for the whole contract if any contributing collection lacks an
    eligible output at the next tier, so this is a hard requirement, not a
    nice-to-have. Deeper hops are allowed to differ; those fail gracefully
    via the existing catalog_gap leaf handling instead.

    Restricted to other TRACKED_SKINS collections only, for the same
    liquidity reason _choose_root is -- an arbitrary "structurally
    chainable" catalog collection is still usually untradeable in practice.
    Picked deterministically-but-spread-out via a hash of the primary
    collection's name, so many different real siblings get exercised
    across samples instead of one collection dominating every mix."""
    root_idx = WEAPON_TIERS.index(root_tier)
    if root_idx + 1 > COVERT_IDX:
        return None
    next_tier = WEAPON_TIERS[root_idx + 1]
    candidates = sorted(
        collection
        for collection in tracked_index
        if collection != primary_collection
        and root_tier in coll_tiers.get(collection, set())
        and next_tier in coll_tiers.get(collection, set())
    )
    if not candidates:
        return None
    # hashlib, not the builtin hash() -- str hashing is randomized per
    # process (PYTHONHASHSEED) unless pinned, which would make this pick
    # (and the whole backtest's reported results) non-reproducible run to run.
    digest = hashlib.md5(primary_collection.encode()).hexdigest()
    chosen = candidates[int(digest, 16) % len(candidates)]
    sibling_skin = tracked_index[chosen].get(root_tier) or catalog_index[chosen][root_tier][0]
    return chosen, sibling_skin


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


async def run_sample(
    skin_name: str,
    catalog: CsgoCatalog,
    full_catalog: list[SkinCatalogEntry],
    coll_tiers: dict[str, set[str]],
    catalog_index: dict[str, dict[str, list[SkinCatalogEntry]]],
    tracked_index: dict[str, dict[str, SkinCatalogEntry]],
    store: PricingStore,
    csfloat: CsFloatClient,
) -> SampleResult | str:
    """Returns a SampleResult, or a short string explaining why this skin
    was skipped."""
    skin = catalog.get(skin_name)
    if skin is None:
        return "not found in catalog"

    primary_collection = skin.collections[0]
    root = _choose_root(skin, primary_collection, tracked_index)
    if root is None:
        return "already Covert-tier with no lower tracked anchor in its collection -- blocked by the Gold/crates gap"
    root_skin, root_tier = root
    required = inputs_required(root_tier)

    sibling = _find_safe_sibling(primary_collection, root_tier, tracked_index, coll_tiers, catalog_index)
    sibling_collection, sibling_skin = sibling if sibling else (None, None)

    # Most of the real catalog outside the curated TRACKED_SKINS list is
    # simply illiquid -- zero CSFloat listings at all, not just a wide
    # float gap. A sibling can pass every structural check here and still
    # turn out unpriceable if it fell back to catalog_index rather than a
    # tracked skin, so try it and fall back to pure single-collection input
    # rather than failing the whole sample over a filler nobody trades.
    sibling_priced: list[tuple[SkinCatalogEntry, float, float]] = []
    if sibling_skin is not None:
        sibling_count = max(1, round(required * SIBLING_INPUT_SHARE))
        for f in _spread_floats(sibling_skin, sibling_count):
            price, _source = await price_output(sibling_skin, f, False, store, csfloat)
            if price is None:
                sibling_collection, sibling_skin = None, None
                sibling_priced = []
                break
            sibling_priced.append((sibling_skin, f, price))

    primary_count = required - len(sibling_priced)
    primary_priced: list[tuple[SkinCatalogEntry, float, float]] = []
    for f in _spread_floats(root_skin, primary_count):
        price, _source = await price_output(root_skin, f, False, store, csfloat)
        if price is None:
            return f"input unpriceable ({root_skin.name} at float {f:.3f})"
        primary_priced.append((root_skin, f, price))

    priced_inputs = primary_priced + sibling_priced
    input_skins = [s for s, _, _ in priced_inputs]

    adjusted = [floatmath.adjusted_float(f, s.min_float, s.max_float) for s, f, _ in priced_inputs]
    avg_adjusted = floatmath.average_adjusted_float(adjusted)
    total_cost = sum(p for _, _, p in priced_inputs)

    try:
        result = await search_chain(
            input_skins=input_skins,
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
    except ValueError as exc:
        return f"root catalog gap: {exc}"

    single_net = result.single_contract_ev * (1 - COMBINED_FEE_RATE)
    chain_net = result.chain_ev * (1 - COMBINED_FEE_RATE)
    delta_net = chain_net - single_net
    delta_pct_net = (delta_net / total_cost * 100) if total_cost > 0 else 0.0

    return SampleResult(
        skin_name=skin_name,
        root_collection=primary_collection,
        root_tier=root_tier,
        mixed_with=sibling_collection,
        total_cost=total_cost,
        single_contract_ev=result.single_contract_ev,
        chain_ev=result.chain_ev,
        delta_pct_gross=result.chain_ev_delta_pct,
        chain_beats_single_gross=result.chain_beats_single,
        single_contract_ev_net=single_net,
        chain_ev_net=chain_net,
        delta_pct_net=delta_pct_net,
        chain_beats_single_net=delta_pct_net >= MATERIALITY_THRESHOLD_PCT,
        price_sources=_price_source_counts(result),
    )


async def main() -> None:
    settings = get_settings()
    catalog = CsgoCatalog(settings)
    full_catalog = await catalog.load()
    coll_tiers = _build_collection_tier_map(full_catalog)
    catalog_index = _build_catalog_index(full_catalog)
    tracked_index = _build_tracked_index(catalog)
    store = PricingStore(settings.pricing_db_path)
    csfloat = CsFloatClient(settings)

    results: list[SampleResult] = []
    skipped: list[tuple[str, str]] = []

    try:
        for name in TRACKED_SKINS:
            outcome = await run_sample(
                name, catalog, full_catalog, coll_tiers, catalog_index, tracked_index, store, csfloat
            )
            if isinstance(outcome, str):
                skipped.append((name, outcome))
            else:
                results.append(outcome)
    finally:
        store.close()
        await csfloat.aclose()

    print(f"Chain-search backtest over {len(TRACKED_SKINS)} tracked skins "
          f"(beam_width={BEAM_WIDTH}, max_depth={MAX_DEPTH}, "
          f"fee haircut={COMBINED_FEE_RATE:.1%} for the net columns only)\n")

    if skipped:
        print("Skipped:")
        for name, reason in skipped:
            print(f"  {name:<32} {reason}")
        print()

    if not results:
        print("Nothing evaluable -- every tracked skin was skipped.")
        return

    header = (
        f"{'skin':<28} {'root tier':<24} {'mixed w/':<24} {'cost':>8} {'single EV':>10} {'chain EV':>10} "
        f"{'gross d%':>9} {'net d%':>8} {'beats(g/n)':>11} {'price sources':>20}"
    )
    print(header)
    print("-" * len(header))

    for r in sorted(results, key=lambda r: r.delta_pct_net, reverse=True):
        sources = ",".join(f"{k}:{v}" for k, v in sorted(r.price_sources.items()))
        beats = f"{'Y' if r.chain_beats_single_gross else 'n'}/{'Y' if r.chain_beats_single_net else 'n'}"
        print(
            f"{r.skin_name:<28} {r.root_tier:<24} {(r.mixed_with or '-'):<24} ${r.total_cost:>7.2f} "
            f"${r.single_contract_ev:>9.2f} ${r.chain_ev:>9.2f} {r.delta_pct_gross:>8.1f}% "
            f"{r.delta_pct_net:>7.1f}% {beats:>11} {sources:>20}"
        )

    n = len(results)
    beats_gross = sum(r.chain_beats_single_gross for r in results)
    beats_net = sum(r.chain_beats_single_net for r in results)
    avg_delta_gross = sum(r.delta_pct_gross for r in results) / n
    avg_delta_net = sum(r.delta_pct_net for r in results) / n
    mixed_count = sum(1 for r in results if r.mixed_with)
    ranked = sorted(results, key=lambda r: r.delta_pct_net, reverse=True)

    print()
    print("=" * len(header))
    print("SUMMARY")
    print(f"  Samples run:                    {n}/{len(TRACKED_SKINS)} tracked skins ({len(skipped)} skipped)")
    print(f"  Samples with a mixed-in sibling: {mixed_count}/{n}")
    print(f"  Chain beats single (gross):     {beats_gross}/{n} ({beats_gross/n:.0%})")
    print(f"  Chain beats single (net of ~{COMBINED_FEE_RATE:.1%} fee): {beats_net}/{n} ({beats_net/n:.0%})")
    print(f"  Average delta_pct (gross):      {avg_delta_gross:+.1f}%")
    print(f"  Average delta_pct (net):        {avg_delta_net:+.1f}%")
    print("  Top 3 winners (net delta_pct):")
    for r in ranked[:3]:
        print(f"    {r.skin_name:<30} {r.delta_pct_net:+.1f}%")
    print("  Top 3 losers (net delta_pct):")
    for r in ranked[-3:]:
        print(f"    {r.skin_name:<30} {r.delta_pct_net:+.1f}%")

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "beam_width": BEAM_WIDTH,
        "max_depth": MAX_DEPTH,
        "fee_haircut_applied_to_net_columns": COMBINED_FEE_RATE,
        "samples_run": n,
        "samples_skipped": len(skipped),
        "samples_with_mixed_sibling": mixed_count,
        "skipped": [{"skin_name": name, "reason": reason} for name, reason in skipped],
        "chain_beats_single_gross_count": beats_gross,
        "chain_beats_single_net_count": beats_net,
        "avg_delta_pct_gross": avg_delta_gross,
        "avg_delta_pct_net": avg_delta_net,
        "samples": [
            {
                "skin_name": r.skin_name,
                "root_collection": r.root_collection,
                "root_tier": r.root_tier,
                "mixed_with": r.mixed_with,
                "total_cost": r.total_cost,
                "single_contract_ev": r.single_contract_ev,
                "chain_ev": r.chain_ev,
                "delta_pct_gross": r.delta_pct_gross,
                "chain_beats_single_gross": r.chain_beats_single_gross,
                "single_contract_ev_net": r.single_contract_ev_net,
                "chain_ev_net": r.chain_ev_net,
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
