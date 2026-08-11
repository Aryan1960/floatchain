"""Collection weighting and output-probability distribution for a trade-up.

Rules (docus/cs2_tradeup_contracts_2026_guide.md section 5):
- Each input contributes an equal share of the collection-selection
  probability: 1/10 normally, 1/5 for a Covert -> Gold (5-input) contract.
  If a skin legitimately belongs to more than one collection its share is
  split evenly across those collections.
- Once a collection is selected, every eligible output skin within it
  (same collection, exactly one rarity tier above the inputs, matching
  StatTrak-ness) is equally likely.

IMPORTANT data quirk: in the ByMykel/CSGO-API catalog, a skin's `stattrak`
and `souvenir` fields are pattern-level *capability* flags ("can this
pattern exist as StatTrak/Souvenir"), not a per-item variant indicator —
there is one catalog row per pattern, not one per variant. So:
- For a StatTrak contract, an output must have stattrak capability (True).
- For a normal contract, capability is irrelevant — a pattern's normal
  variant always exists regardless of whether it also supports StatTrak.
- Souvenir items can't be entered as inputs in the first place (this tool's
  contract schema has no souvenir field), so no souvenir filtering is done
  here; the `souvenir` flag is not a reliable per-item signal anyway.
"""

from __future__ import annotations

from collections import defaultdict

from app.domain.models import SkinCatalogEntry
from app.domain.rarity import next_tier_id


def collection_weights(input_skins: list[SkinCatalogEntry]) -> dict[str, float]:
    if not input_skins:
        raise ValueError("need at least one input skin")
    share_per_input = 1.0 / len(input_skins)
    weights: dict[str, float] = defaultdict(float)
    for skin in input_skins:
        if not skin.collections:
            raise ValueError(f"{skin.name!r} has no collection data; cannot weight it")
        split = share_per_input / len(skin.collections)
        for collection in skin.collections:
            weights[collection] += split
    return dict(weights)


def eligible_outputs(
    collection: str,
    output_rarity_id: str,
    stattrak: bool,
    catalog: list[SkinCatalogEntry],
) -> list[SkinCatalogEntry]:
    return [
        skin
        for skin in catalog
        if skin.rarity_id == output_rarity_id
        and (not stattrak or skin.stattrak)
        and collection in skin.collections
    ]


def outcome_distribution(
    input_skins: list[SkinCatalogEntry],
    catalog: list[SkinCatalogEntry],
    stattrak: bool,
) -> list[tuple[SkinCatalogEntry, str, float]]:
    """Returns [(output_skin, collection, probability), ...] summing to 1.0."""
    if len({skin.rarity_id for skin in input_skins}) != 1:
        raise ValueError("all inputs must share the same rarity tier")

    output_rarity_id = next_tier_id(input_skins[0].rarity_id)
    weights = collection_weights(input_skins)

    distribution: list[tuple[SkinCatalogEntry, str, float]] = []
    for collection, weight in weights.items():
        outputs = eligible_outputs(collection, output_rarity_id, stattrak, catalog)
        if not outputs:
            raise ValueError(
                f"no eligible {output_rarity_id} outputs found in collection "
                f"{collection!r}; catalog data may be incomplete"
            )
        per_output_probability = weight / len(outputs)
        for skin in outputs:
            distribution.append((skin, collection, per_output_probability))

    return distribution
