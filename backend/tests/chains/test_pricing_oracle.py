from dataclasses import dataclass

import pytest

from app.chains import pricing_oracle
from app.chains.pricing_oracle import price_output
from app.domain.models import SkinCatalogEntry

REDLINE = SkinCatalogEntry(
    name="AK-47 | Redline",
    rarity_id="rarity_legendary_weapon",
    min_float=0.10,
    max_float=0.70,
    stattrak=True,
    souvenir=True,
    collections=("The Phoenix Collection",),
)


@dataclass
class FakePrediction:
    model_type: str
    model_price_cents: float | None


class FakeStore:
    def __init__(self, nearby=None):
        self._nearby = nearby

    def nearest_real_snapshot(self, skin_name, stattrak, target_float):
        return self._nearby


class FakeCsFloat:
    def __init__(self, price):
        self._price = price

    async def price_near_float(self, market_hash_name, target_float):
        return self._price


@pytest.mark.asyncio
async def test_price_output_prefers_model_hit(monkeypatch):
    monkeypatch.setattr(
        pricing_oracle, "predict_price", lambda *a, **k: FakePrediction("xgboost", 5000.0)
    )
    price, source = await price_output(
        REDLINE, 0.2, False, FakeStore(nearby={"float_value": 0.2, "price_cents": 999999}), FakeCsFloat(999.0)
    )
    assert (price, source) == (50.0, "model")


@pytest.mark.asyncio
async def test_price_output_falls_back_to_local_when_model_insufficient(monkeypatch):
    monkeypatch.setattr(
        pricing_oracle, "predict_price", lambda *a, **k: FakePrediction("insufficient_data", None)
    )
    price, source = await price_output(
        REDLINE, 0.2, False, FakeStore(nearby={"float_value": 0.201, "price_cents": 2500}), FakeCsFloat(999.0)
    )
    assert (price, source) == (25.0, "local")


@pytest.mark.asyncio
async def test_price_output_falls_back_to_live_when_local_too_far(monkeypatch):
    monkeypatch.setattr(
        pricing_oracle, "predict_price", lambda *a, **k: FakePrediction("insufficient_data", None)
    )
    price, source = await price_output(
        REDLINE, 0.2, False, FakeStore(nearby={"float_value": 0.5, "price_cents": 2500}), FakeCsFloat(12.34)
    )
    assert (price, source) == (12.34, "live")


@pytest.mark.asyncio
async def test_price_output_returns_none_when_nothing_found(monkeypatch):
    monkeypatch.setattr(
        pricing_oracle, "predict_price", lambda *a, **k: FakePrediction("insufficient_data", None)
    )
    price, source = await price_output(REDLINE, 0.2, False, FakeStore(nearby=None), FakeCsFloat(None))
    assert (price, source) == (None, "none")
