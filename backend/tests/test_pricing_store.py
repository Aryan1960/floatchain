import pytest

from app.data.pricing_store import PricingStore


@pytest.fixture
def store(tmp_path):
    s = PricingStore(tmp_path / "pricing.db")
    yield s
    s.close()


def _upsert(store, listing_id="L1", price_cents=1000, seen_at="2026-08-12T00:00:00+00:00", **overrides):
    kwargs = dict(
        listing_id=listing_id,
        market_hash_name="AK-47 | Redline (Field-Tested)",
        skin_name="AK-47 | Redline",
        stattrak=False,
        float_value=0.20,
        price_cents=price_cents,
        predicted_price_cents=1100,
        seen_at=seen_at,
    )
    kwargs.update(overrides)
    return store.upsert_real_snapshot(**kwargs)


def test_upsert_new_listing_returns_true(store):
    assert _upsert(store) is True


def test_upsert_duplicate_listing_returns_false(store):
    _upsert(store, seen_at="2026-08-12T00:00:00+00:00")
    assert _upsert(store, seen_at="2026-08-12T00:15:00+00:00") is False


def test_duplicate_upsert_keeps_first_seen_but_advances_last_seen(store):
    _upsert(store, seen_at="2026-08-12T00:00:00+00:00")
    _upsert(store, seen_at="2026-08-12T00:15:00+00:00", price_cents=1200)

    rows = store.real_summary_by_skin()
    assert len(rows) == 1
    row = rows[0]
    assert row["n"] == 1  # still one distinct listing, not two rows
    assert row["first_seen_at"] == "2026-08-12T00:00:00+00:00"
    assert row["last_seen_at"] == "2026-08-12T00:15:00+00:00"


def test_duplicate_upsert_increments_times_seen_and_updates_price(store):
    _upsert(store, price_cents=1000)
    _upsert(store, price_cents=1200)
    _upsert(store, price_cents=1300)

    points = store.real_points_for_skin("AK-47 | Redline", stattrak=False)
    assert points == [(0.20, 1300)]  # latest price wins, still a single point


def test_different_listing_ids_produce_separate_rows(store):
    _upsert(store, listing_id="L1")
    _upsert(store, listing_id="L2")

    points = store.real_points_for_skin("AK-47 | Redline", stattrak=False)
    assert len(points) == 2


def test_real_points_excludes_null_float(store):
    _upsert(store, listing_id="L1", float_value=0.20)
    _upsert(store, listing_id="L2", float_value=None)

    points = store.real_points_for_skin("AK-47 | Redline", stattrak=False)
    assert points == [(0.20, 1000)]


def test_real_and_synthetic_are_isolated(store):
    _upsert(store, listing_id="L1")
    store.insert_synthetic(
        skin_name="AK-47 | Redline",
        stattrak=False,
        float_value=0.25,
        price_cents=999,
        generated_at="2026-08-12T00:00:00+00:00",
    )

    real_total, synthetic_total = store.total_counts()
    assert real_total == 1
    assert synthetic_total == 1

    # real_points_for_skin must never surface synthetic rows
    points = store.real_points_for_skin("AK-47 | Redline", stattrak=False)
    assert points == [(0.20, 1000)]

    synth_rows = store.synthetic_summary_by_skin()
    assert len(synth_rows) == 1
    assert synth_rows[0]["n"] == 1


def test_clear_synthetic_leaves_real_untouched(store):
    _upsert(store, listing_id="L1")
    store.insert_synthetic(
        skin_name="AK-47 | Redline",
        stattrak=False,
        float_value=0.25,
        price_cents=999,
        generated_at="2026-08-12T00:00:00+00:00",
    )

    store.clear_synthetic()

    real_total, synthetic_total = store.total_counts()
    assert real_total == 1
    assert synthetic_total == 0


def test_summary_groups_by_skin_and_stattrak(store):
    _upsert(store, listing_id="L1", skin_name="AK-47 | Redline", stattrak=False)
    _upsert(store, listing_id="L2", skin_name="AK-47 | Redline", stattrak=False)
    _upsert(store, listing_id="L3", skin_name="AWP | Asiimov", stattrak=False)

    rows = {r["skin_name"]: r["n"] for r in store.real_summary_by_skin()}
    assert rows == {"AK-47 | Redline": 2, "AWP | Asiimov": 1}
