"""Rarity tier ordering for weapon skins and golds (knives/gloves).

Tier ids match the `rarity.id` field returned by the ByMykel/CSGO-API dataset.
Contraband (M4A4 | Howl) is intentionally excluded: it is non-tradeable and
cannot be used in trade-ups.
"""

from __future__ import annotations

TIER_ORDER: list[str] = [
    "rarity_common_weapon",  # Consumer Grade
    "rarity_uncommon_weapon",  # Industrial Grade
    "rarity_rare_weapon",  # Mil-Spec Grade
    "rarity_mythical_weapon",  # Restricted
    "rarity_legendary_weapon",  # Classified
    "rarity_ancient_weapon",  # Covert
    "rarity_ancient",  # Extraordinary (Gold: knives/gloves)
]

TIER_INDEX: dict[str, int] = {rarity_id: i for i, rarity_id in enumerate(TIER_ORDER)}

GOLD_TIER_ID = "rarity_ancient"


def tier_index(rarity_id: str) -> int:
    if rarity_id not in TIER_INDEX:
        raise ValueError(f"Unknown or non-tradeable rarity id: {rarity_id!r}")
    return TIER_INDEX[rarity_id]


def next_tier_id(rarity_id: str) -> str:
    idx = tier_index(rarity_id)
    if idx + 1 >= len(TIER_ORDER):
        raise ValueError(f"{rarity_id!r} has no higher tier to trade up into")
    return TIER_ORDER[idx + 1]


def inputs_required(rarity_id: str) -> int:
    """10 inputs normally; 5 for a Covert -> Gold (knife/glove) contract."""
    return 5 if next_tier_id(rarity_id) == GOLD_TIER_ID else 10
