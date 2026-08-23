import pytest

from app.chains import search
from app.chains.search import search_chain
from app.domain.models import SkinCatalogEntry

# rarity_common_weapon -> uncommon -> rare -> mythical -> legendary -> ancient_weapon (Covert) -> ancient (Gold)


def _skin(name, rarity_id, collection, min_float=0.0, max_float=1.0):
    return SkinCatalogEntry(
        name=name,
        rarity_id=rarity_id,
        min_float=min_float,
        max_float=max_float,
        stattrak=False,
        souvenir=False,
        collections=(collection,),
    )


def _priced(prices: dict[str, float]):
    """A fake price_output keyed purely on skin name -- these tests are
    about the search's beam/backward-induction logic, not the pricing
    cascade (covered separately in test_pricing_oracle.py)."""

    async def fake_price_output(skin, output_flt, stattrak, store, csfloat):
        if skin.name not in prices:
            return None, "none"
        return prices[skin.name], "model"

    return fake_price_output


@pytest.mark.asyncio
async def test_chain_ev_equals_single_when_nothing_explored(monkeypatch):
    # Covert-tier inputs -> Gold-tier output has no further tier to trade
    # into, so every branch is a forced "top_of_tier" leaf.
    inputs = [_skin(f"In{i}", "rarity_ancient_weapon", "Coll") for i in range(5)]
    catalog = [_skin("GoldOut", "rarity_ancient", "Coll")]
    monkeypatch.setattr(search, "price_output", _priced({"GoldOut": 100.0}))

    result = await search_chain(inputs, 0.5, 250.0, False, catalog, None, None, beam_width=3, max_depth=2)

    assert result.chain_ev == pytest.approx(result.single_contract_ev)
    assert result.root_branches[0].explored is False
    assert result.root_branches[0].leaf_reason == "top_of_tier"


@pytest.mark.asyncio
async def test_backward_induction_needs_full_depth_to_find_value(monkeypatch):
    inputs = [_skin(f"In{i}", "rarity_common_weapon", "Coll") for i in range(10)]
    catalog = [
        _skin("O1", "rarity_uncommon_weapon", "Coll"),
        _skin("O2", "rarity_rare_weapon", "Coll"),
        _skin("O3", "rarity_mythical_weapon", "Coll"),
        _skin("O4", "rarity_legendary_weapon", "Coll"),
    ]
    prices = {"O1": 10.0, "O2": 12.0, "O3": 14.0, "O4": 200_000.0}
    monkeypatch.setattr(search, "price_output", _priced(prices))

    shallow = await search_chain(inputs, 0.5, 50.0, False, catalog, None, None, beam_width=3, max_depth=2)
    deep = await search_chain(inputs, 0.5, 50.0, False, catalog, None, None, beam_width=3, max_depth=3)

    assert shallow.chain_ev == pytest.approx(shallow.single_contract_ev)
    assert deep.chain_ev > deep.single_contract_ev
    assert deep.chain_beats_single is True


@pytest.mark.asyncio
async def test_chosen_action_is_sell_when_chaining_is_a_real_bad_decision(monkeypatch):
    inputs = [_skin(f"In{i}", "rarity_common_weapon", "Coll") for i in range(10)]
    catalog = [
        _skin("O1", "rarity_uncommon_weapon", "Coll"),
        _skin("O2", "rarity_rare_weapon", "Coll"),
    ]
    # chaining O1 costs 10x its own price but only nets a cheaper O2 back.
    monkeypatch.setattr(search, "price_output", _priced({"O1": 10.0, "O2": 1.0}))

    result = await search_chain(inputs, 0.5, 50.0, False, catalog, None, None, beam_width=3, max_depth=2)

    branch = result.root_branches[0]
    assert branch.explored is True
    assert branch.leaf_reason is None
    assert branch.chosen_action == "sell"


@pytest.mark.asyncio
async def test_beam_prunes_by_probability_times_price_not_probability_alone(monkeypatch):
    # Weighted so "Common" (90% of inputs) heavily outweighs "Rare" (10%),
    # but Rare's single output is priced high enough that prob*price still
    # beats Common's flood of cheap outputs -- must survive beam_width=1.
    inputs = (
        [_skin(f"CommonIn{i}", "rarity_ancient_weapon", "Common") for i in range(9)]
        + [_skin("RareIn", "rarity_ancient_weapon", "Rare")]
    )
    catalog = (
        [_skin(f"CommonOut{i}", "rarity_ancient", "Common") for i in range(5)]
        + [_skin("RareOut", "rarity_ancient", "Rare")]
    )
    prices = {**{f"CommonOut{i}": 1.0 for i in range(5)}, "RareOut": 10_000.0}
    monkeypatch.setattr(search, "price_output", _priced(prices))

    result = await search_chain(inputs, 0.5, 500.0, False, catalog, None, None, beam_width=1, max_depth=1)

    rare_branch = next(b for b in result.root_branches if b.skin_name == "RareOut")
    assert rare_branch.leaf_reason != "beam_pruned"
    pruned = [b for b in result.root_branches if b.skin_name != "RareOut"]
    assert all(b.leaf_reason == "beam_pruned" for b in pruned)

    total_probability = sum(b.probability for b in result.root_branches)
    assert total_probability == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_covert_to_gold_catalog_gap_is_a_leaf_not_a_crash(monkeypatch):
    # Root tier is Classified so the root's own outcome_distribution
    # succeeds fine; the Covert output it lands on has no matching Gold-tier
    # skin in the catalog, so chaining into it must leaf, not raise.
    inputs = [_skin(f"In{i}", "rarity_legendary_weapon", "Coll") for i in range(10)]
    catalog = [_skin("CovertOut", "rarity_ancient_weapon", "Coll")]
    monkeypatch.setattr(search, "price_output", _priced({"CovertOut": 50.0}))

    result = await search_chain(inputs, 0.5, 100.0, False, catalog, None, None, beam_width=3, max_depth=2)

    branch = result.root_branches[0]
    assert branch.explored is False
    assert branch.leaf_reason == "catalog_gap"
    assert result.chain_ev == pytest.approx(result.single_contract_ev)


@pytest.mark.asyncio
async def test_chain_ev_is_monotonic_in_max_depth(monkeypatch):
    # A leaf's node_value is always `price`; an explored node's is
    # max(price, chain_value) -- so exploring deeper can only raise or hold
    # a branch's value, never lower it. Reuses the 4-tier fixture where the
    # real payoff only appears at max_depth=3.
    inputs = [_skin(f"In{i}", "rarity_common_weapon", "Coll") for i in range(10)]
    catalog = [
        _skin("O1", "rarity_uncommon_weapon", "Coll"),
        _skin("O2", "rarity_rare_weapon", "Coll"),
        _skin("O3", "rarity_mythical_weapon", "Coll"),
        _skin("O4", "rarity_legendary_weapon", "Coll"),
    ]
    prices = {"O1": 10.0, "O2": 12.0, "O3": 14.0, "O4": 200_000.0}
    monkeypatch.setattr(search, "price_output", _priced(prices))

    depth1 = await search_chain(inputs, 0.5, 50.0, False, catalog, None, None, beam_width=3, max_depth=1)
    depth2 = await search_chain(inputs, 0.5, 50.0, False, catalog, None, None, beam_width=3, max_depth=2)
    depth3 = await search_chain(inputs, 0.5, 50.0, False, catalog, None, None, beam_width=3, max_depth=3)

    assert depth1.chain_ev <= depth2.chain_ev <= depth3.chain_ev
    assert depth1.chain_ev < depth3.chain_ev


@pytest.mark.asyncio
async def test_chain_ev_is_monotonic_in_beam_width(monkeypatch):
    # 4 equally-likely outputs in one collection, all reaching the same
    # profitable next-tier output if explored -- a narrower beam prunes the
    # lower-ranked ones to a flat sell price, so widening the beam can only
    # raise chain_ev, never lower it.
    inputs = [_skin(f"In{i}", "rarity_common_weapon", "Coll") for i in range(10)]
    catalog = [
        _skin("A", "rarity_uncommon_weapon", "Coll"),
        _skin("B", "rarity_uncommon_weapon", "Coll"),
        _skin("C", "rarity_uncommon_weapon", "Coll"),
        _skin("D", "rarity_uncommon_weapon", "Coll"),
        _skin("E", "rarity_rare_weapon", "Coll"),
    ]
    prices = {"A": 100.0, "B": 90.0, "C": 80.0, "D": 1.0, "E": 100_000.0}
    monkeypatch.setattr(search, "price_output", _priced(prices))

    narrow = await search_chain(inputs, 0.5, 50.0, False, catalog, None, None, beam_width=1, max_depth=1)
    wide = await search_chain(inputs, 0.5, 50.0, False, catalog, None, None, beam_width=3, max_depth=1)

    assert narrow.chain_ev <= wide.chain_ev
    assert narrow.chain_ev < wide.chain_ev


@pytest.mark.asyncio
async def test_materiality_threshold_gates_the_boolean_not_the_raw_numbers(monkeypatch):
    inputs = [_skin(f"In{i}", "rarity_common_weapon", "Coll") for i in range(10)]
    catalog = [
        _skin("O1", "rarity_uncommon_weapon", "Coll"),
        _skin("O2", "rarity_rare_weapon", "Coll"),
    ]
    monkeypatch.setattr(search, "price_output", _priced({"O1": 10.0, "O2": 200.0}))

    lenient = await search_chain(
        inputs, 0.5, 50.0, False, catalog, None, None, beam_width=3, max_depth=2, materiality_threshold_pct=5.0
    )
    strict = await search_chain(
        inputs, 0.5, 50.0, False, catalog, None, None, beam_width=3, max_depth=2, materiality_threshold_pct=1000.0
    )

    assert lenient.chain_ev == pytest.approx(strict.chain_ev)
    assert lenient.chain_ev > lenient.single_contract_ev
    assert lenient.chain_beats_single is True
    assert strict.chain_beats_single is False
