import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_catalog, get_csfloat
from app.domain.models import SkinCatalogEntry
from app.main import app

REDLINE = SkinCatalogEntry(
    name="AK-47 | Redline",
    rarity_id="rarity_legendary_weapon",
    min_float=0.10,
    max_float=0.70,
    stattrak=True,
    souvenir=True,
    collections=("The Phoenix Collection",),
)


class FakeCatalog:
    def get(self, name):
        return REDLINE if name == REDLINE.name else None


class FakeCsFloat:
    def __init__(self, price):
        self._price = price

    async def price_near_float(self, market_hash_name, target_float):
        return self._price


@pytest.fixture
def client_with_price(request):
    # Deliberately not entered as a context manager: dependency_overrides
    # replace get_catalog/get_csfloat outright, so app.state (and the real
    # catalog/CSFloat network calls the lifespan would trigger) never comes
    # into play. Keeps this test offline and fast regardless of cache state.
    price = getattr(request, "param", 12.34)
    app.dependency_overrides[get_catalog] = lambda: FakeCatalog()
    app.dependency_overrides[get_csfloat] = lambda: FakeCsFloat(price)
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_get_skin_price_returns_price(client_with_price):
    response = client_with_price.get(
        "/api/skins/price",
        params={"skin_name": "AK-47 | Redline", "raw_float": 0.2, "stattrak": False},
    )
    assert response.status_code == 200
    assert response.json() == {"price": 12.34}


def test_get_skin_price_unknown_skin(client_with_price):
    response = client_with_price.get(
        "/api/skins/price",
        params={"skin_name": "Not A Real Skin", "raw_float": 0.2},
    )
    assert response.status_code == 400


def test_get_skin_price_out_of_range_float(client_with_price):
    response = client_with_price.get(
        "/api/skins/price",
        params={"skin_name": "AK-47 | Redline", "raw_float": 0.9},
    )
    assert response.status_code == 400
