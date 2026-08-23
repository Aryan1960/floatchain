import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from app.data.pricing_store import PricingStore
from app.pricing import deal_radar
from app.pricing.deal_radar import scan_for_deals, to_result_dict


def _decaying_curve(float_value: float, base: float = 10000.0, k: float = 2.5) -> float:
    return base * math.exp(-k * float_value)


def _insert_clean_curve(store: PricingStore, skin_name: str, n: int, seen_at: str, seed: int = 0) -> None:
    # Narrow float band on purpose -- AnomalyDetector works on raw (float,
    # price) points, not curve residuals, so a wide band's naturally-low
    # prices at other floats would confound "is this cheap for ITS float"
    # with "is this cheap overall". Keeping floats tight makes the
    # deliberate outlier below unambiguous in both dimensions.
    rng = np.random.default_rng(seed)
    floats = rng.uniform(0.45, 0.55, size=n)
    for i, f in enumerate(floats):
        price = int(_decaying_curve(float(f)))
        store.upsert_real_snapshot(
            listing_id=f"{skin_name}-clean-{i}",
            market_hash_name=f"{skin_name} (Field-Tested)",
            skin_name=skin_name,
            stattrak=False,
            float_value=float(f),
            price_cents=price,
            predicted_price_cents=price,
            seen_at=seen_at,
        )


def _insert_one(store: PricingStore, skin_name: str, listing_id: str, float_value: float, price_cents: int, seen_at: str) -> None:
    store.upsert_real_snapshot(
        listing_id=listing_id,
        market_hash_name=f"{skin_name} (Field-Tested)",
        skin_name=skin_name,
        stattrak=False,
        float_value=float_value,
        price_cents=price_cents,
        predicted_price_cents=price_cents,
        seen_at=seen_at,
    )


NOW = datetime.now(timezone.utc)
RECENT = NOW.isoformat()
STALE = (NOW - timedelta(hours=5)).isoformat()


@pytest.fixture
def store(tmp_path):
    s = PricingStore(tmp_path / "pricing.db")
    yield s
    s.close()


class FakeCsFloat:
    def __init__(self, remaining, price=None):
        self.last_rate_limit_remaining = remaining
        self._price = price
        self.calls = 0

    async def price_near_float(self, market_hash_name, target_float):
        self.calls += 1
        return self._price


@pytest.mark.asyncio
async def test_scan_for_deals_flags_real_discount_ignores_expensive_side(store):
    _insert_clean_curve(store, "Test Skin", 30, RECENT)
    # A real discount: priced way below the fitted curve at this float.
    _insert_one(store, "Test Skin", "cheap-1", 0.5, price_cents=50, seen_at=RECENT)
    # An anomaly on the expensive side -- flagged by Isolation Forest, but not a deal.
    _insert_one(store, "Test Skin", "pricey-1", 0.5, price_cents=999_999, seen_at=RECENT)

    results = await scan_for_deals(store, None, ("Test Skin",))

    skin_names_and_prices = [(c.skin_name, c.price_cents) for c in results]
    assert ("Test Skin", 50) in skin_names_and_prices
    assert ("Test Skin", 999_999) not in skin_names_and_prices


@pytest.mark.asyncio
async def test_scan_for_deals_filters_stale_listings(store):
    _insert_clean_curve(store, "Test Skin", 30, RECENT)
    _insert_one(store, "Test Skin", "stale-cheap", 0.5, price_cents=50, seen_at=STALE)

    results = await scan_for_deals(store, None, ("Test Skin",))

    assert all(c.price_cents != 50 for c in results)


@pytest.mark.asyncio
async def test_scan_for_deals_caps_verification_at_top_n(store, monkeypatch):
    monkeypatch.setattr(deal_radar, "TOP_N_CANDIDATES", 2)
    for i, skin_name in enumerate(["Skin A", "Skin B", "Skin C"]):
        _insert_clean_curve(store, skin_name, 30, RECENT, seed=i)
        _insert_one(store, skin_name, f"{skin_name}-cheap", 0.5, price_cents=50, seen_at=RECENT)

    csfloat = FakeCsFloat(remaining=100, price=1.0)
    results = await scan_for_deals(store, csfloat, ("Skin A", "Skin B", "Skin C"))

    # >=3: the three deliberate cheap deals must all be found; a little
    # extra false-positive noise from the Isolation Forest's own default
    # contamination rate on random data is expected and not what this test
    # is checking -- the point here is the verification cap, not the count.
    assert len(results) >= 3
    assert {"Skin A", "Skin B", "Skin C"} <= {c.skin_name for c in results if c.price_cents == 50}
    assert csfloat.calls == 2
    assert sum(c.verified_live for c in results) <= 2


@pytest.mark.asyncio
async def test_scan_for_deals_skips_verification_when_csfloat_is_none(store):
    _insert_clean_curve(store, "Test Skin", 30, RECENT)
    _insert_one(store, "Test Skin", "cheap-1", 0.5, price_cents=50, seen_at=RECENT)

    results = await scan_for_deals(store, None, ("Test Skin",))

    assert all(not c.verified_live for c in results)


@pytest.mark.asyncio
async def test_scan_for_deals_skips_verification_when_budget_thin(store):
    _insert_clean_curve(store, "Test Skin", 30, RECENT)
    _insert_one(store, "Test Skin", "cheap-1", 0.5, price_cents=50, seen_at=RECENT)

    csfloat = FakeCsFloat(remaining=5, price=1.0)  # below VERIFY_SAFETY_MARGIN
    results = await scan_for_deals(store, csfloat, ("Test Skin",))

    assert csfloat.calls == 0
    assert all(not c.verified_live for c in results)


@pytest.mark.asyncio
async def test_scan_for_deals_treats_unknown_remaining_as_unsafe(store):
    _insert_clean_curve(store, "Test Skin", 30, RECENT)
    _insert_one(store, "Test Skin", "cheap-1", 0.5, price_cents=50, seen_at=RECENT)

    csfloat = FakeCsFloat(remaining=None, price=1.0)  # no real call made yet
    results = await scan_for_deals(store, csfloat, ("Test Skin",))

    assert csfloat.calls == 0
    assert all(not c.verified_live for c in results)


def test_real_points_with_metadata_returns_identity_fields(store):
    _insert_one(store, "Test Skin", "listing-xyz", 0.3, price_cents=1000, seen_at=RECENT)

    rows = store.real_points_with_metadata("Test Skin", stattrak=False)

    assert rows == [(0.3, 1000, RECENT, "listing-xyz")]


def test_to_result_dict_shape():
    from app.pricing.deal_radar import DealCandidate

    candidate = DealCandidate(
        skin_name="Test Skin", float_value=0.5, price_cents=50, model_price_cents=100,
        discount_pct=0.5, last_seen_at=RECENT, verified_live=True, sample_count=30,
    )
    result = to_result_dict([candidate], generated_at=RECENT)

    assert result["generated_at"] == RECENT
    assert result["candidates"] == [{
        "skin_name": "Test Skin", "float_value": 0.5, "price": 0.5, "model_price": 1.0,
        "discount_pct": 50.0, "last_seen_at": RECENT, "verified_live": True, "sample_count": 30,
    }]
