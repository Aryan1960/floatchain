import pytest

from app.domain.models import SkinCatalogEntry
from app.domain.probability import (
    collection_weights,
    eligible_outputs,
    outcome_distribution,
)

INPUT_RARITY = "rarity_legendary_weapon"  # Classified
OUTPUT_RARITY = "rarity_ancient_weapon"  # Covert


def _skin(name, rarity_id, collection, stattrak=False, souvenir=False):
    return SkinCatalogEntry(
        name=name,
        rarity_id=rarity_id,
        min_float=0.0,
        max_float=1.0,
        stattrak=stattrak,
        souvenir=souvenir,
        collections=(collection,),
    )


def _guide_example_setup():
    """docus/cs2_tradeup_contracts_2026_guide.md section 5 worked example:
    1 Norse input (10%, 3 possible outputs -> 3.33% each)
    4 Fever inputs (40%, 5 possible outputs -> 8.00% each)
    5 Phoenix inputs (50%, 4 possible outputs -> 12.50% each)
    """
    inputs = (
        [_skin("Norse Input", INPUT_RARITY, "Norse")]
        + [_skin(f"Fever Input {i}", INPUT_RARITY, "Fever") for i in range(4)]
        + [_skin(f"Phoenix Input {i}", INPUT_RARITY, "Phoenix") for i in range(5)]
    )
    catalog = (
        [_skin(f"Norse Output {i}", OUTPUT_RARITY, "Norse") for i in range(3)]
        + [_skin(f"Fever Output {i}", OUTPUT_RARITY, "Fever") for i in range(5)]
        + [_skin(f"Phoenix Output {i}", OUTPUT_RARITY, "Phoenix") for i in range(4)]
    )
    return inputs, catalog


def test_collection_weights_match_input_share():
    inputs, _ = _guide_example_setup()
    weights = collection_weights(inputs)
    assert weights["Norse"] == pytest.approx(0.1)
    assert weights["Fever"] == pytest.approx(0.4)
    assert weights["Phoenix"] == pytest.approx(0.5)


def test_eligible_outputs_filters_by_rarity_and_collection():
    _, catalog = _guide_example_setup()
    outputs = eligible_outputs("Phoenix", OUTPUT_RARITY, stattrak=False, catalog=catalog)
    assert len(outputs) == 4
    assert all(s.collections == ("Phoenix",) for s in outputs)


def test_outcome_distribution_matches_guide_worked_example():
    inputs, catalog = _guide_example_setup()
    distribution = outcome_distribution(inputs, catalog, stattrak=False)

    probs_by_collection = {}
    for skin, collection, prob in distribution:
        probs_by_collection.setdefault(collection, []).append(prob)

    assert all(p == pytest.approx(0.1 / 3) for p in probs_by_collection["Norse"])
    assert all(p == pytest.approx(0.4 / 5) for p in probs_by_collection["Fever"])
    assert all(p == pytest.approx(0.5 / 4) for p in probs_by_collection["Phoenix"])

    total_probability = sum(prob for _, _, prob in distribution)
    assert total_probability == pytest.approx(1.0)


def test_outcome_distribution_rejects_mixed_rarity_inputs():
    inputs, catalog = _guide_example_setup()
    mixed = inputs[:-1] + [_skin("Wrong Tier", "rarity_mythical_weapon", "Phoenix")]
    with pytest.raises(ValueError):
        outcome_distribution(mixed, catalog, stattrak=False)


def test_eligible_outputs_for_normal_contract_ignores_stattrak_capability():
    # stattrak=True on a catalog row is a capability flag ("this pattern can
    # also exist as StatTrak"), not a per-item variant marker. A normal
    # (non-StatTrak) contract must still be able to land on such a pattern.
    catalog = [
        _skin("Capable Of StatTrak", OUTPUT_RARITY, "Phoenix", stattrak=True),
        _skin("Never StatTrak", OUTPUT_RARITY, "Phoenix", stattrak=False),
    ]
    outputs = eligible_outputs("Phoenix", OUTPUT_RARITY, stattrak=False, catalog=catalog)
    assert {s.name for s in outputs} == {"Capable Of StatTrak", "Never StatTrak"}


def test_eligible_outputs_for_stattrak_contract_requires_capability():
    catalog = [
        _skin("Capable Of StatTrak", OUTPUT_RARITY, "Phoenix", stattrak=True),
        _skin("Never StatTrak", OUTPUT_RARITY, "Phoenix", stattrak=False),
    ]
    outputs = eligible_outputs("Phoenix", OUTPUT_RARITY, stattrak=True, catalog=catalog)
    assert {s.name for s in outputs} == {"Capable Of StatTrak"}
