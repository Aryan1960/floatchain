import pytest

from app.domain.rarity import inputs_required, next_tier_id, tier_index


def test_tier_index_order():
    assert tier_index("rarity_common_weapon") < tier_index("rarity_uncommon_weapon")
    assert tier_index("rarity_legendary_weapon") < tier_index("rarity_ancient_weapon")


def test_tier_index_rejects_unknown():
    with pytest.raises(ValueError):
        tier_index("rarity_contraband_weapon")


def test_next_tier_covert_to_gold():
    assert next_tier_id("rarity_ancient_weapon") == "rarity_ancient"


def test_next_tier_has_no_tier_above_gold():
    with pytest.raises(ValueError):
        next_tier_id("rarity_ancient")


def test_inputs_required_is_five_only_for_covert_to_gold():
    assert inputs_required("rarity_ancient_weapon") == 5
    assert inputs_required("rarity_legendary_weapon") == 10
    assert inputs_required("rarity_common_weapon") == 10
