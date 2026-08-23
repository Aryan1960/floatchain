import pytest
from fastapi.testclient import TestClient

from app.chains import search as search_module
from app.dependencies import get_catalog, get_csfloat, get_pricing_store
from app.domain.models import SkinCatalogEntry
from app.main import app

INPUT = SkinCatalogEntry(
    name="Classified Input",
    rarity_id="rarity_legendary_weapon",
    min_float=0.0,
    max_float=1.0,
    stattrak=False,
    souvenir=False,
    collections=("Coll",),
)
OUTPUT = SkinCatalogEntry(
    name="Covert Output",
    rarity_id="rarity_ancient_weapon",
    min_float=0.0,
    max_float=1.0,
    stattrak=False,
    souvenir=False,
    collections=("Coll",),
)


class FakeCatalog:
    def get(self, name):
        return {INPUT.name: INPUT, OUTPUT.name: OUTPUT}.get(name)

    async def load(self):
        return [INPUT, OUTPUT]


@pytest.fixture
def client(monkeypatch):
    async def fake_price_output(skin, output_flt, stattrak, store, csfloat):
        return 10.0, "model"

    monkeypatch.setattr(search_module, "price_output", fake_price_output)
    app.dependency_overrides[get_catalog] = lambda: FakeCatalog()
    app.dependency_overrides[get_csfloat] = lambda: object()
    app.dependency_overrides[get_pricing_store] = lambda: object()
    yield TestClient(app)
    app.dependency_overrides.clear()


def _valid_inputs(n=10):
    return [
        {"skin_name": INPUT.name, "raw_float": 0.2, "price": 5.0, "stattrak": False} for _ in range(n)
    ]


def test_evaluate_chain_unknown_skin(client):
    body = {"inputs": [{"skin_name": "Nope", "raw_float": 0.2, "price": 5.0, "stattrak": False}] * 10}
    response = client.post("/api/chains/evaluate", json=body)
    assert response.status_code == 400


def test_evaluate_chain_mixed_rarity(client):
    inputs = _valid_inputs(9) + [
        {"skin_name": "Covert Output", "raw_float": 0.2, "price": 5.0, "stattrak": False}
    ]
    response = client.post("/api/chains/evaluate", json={"inputs": inputs})
    assert response.status_code == 400


def test_evaluate_chain_wrong_input_count(client):
    response = client.post("/api/chains/evaluate", json={"inputs": _valid_inputs(9)})
    assert response.status_code == 400


def test_evaluate_chain_root_level_catalog_gap_returns_400(client):
    # Covert-tier inputs submitted directly -- next tier is Gold, and the
    # fake catalog has no Gold-tier skin in "Coll". outcome_distribution
    # raises ValueError at the ROOT (not inside a recursive call), which
    # only the router's own try/except around search_chain can catch.
    inputs = [
        {"skin_name": OUTPUT.name, "raw_float": 0.2, "price": 5.0, "stattrak": False} for _ in range(5)
    ]
    response = client.post("/api/chains/evaluate", json={"inputs": inputs})
    assert response.status_code == 400


def test_evaluate_chain_happy_path_round_trips_json(client):
    response = client.post("/api/chains/evaluate", json={"inputs": _valid_inputs(10)})
    assert response.status_code == 200
    body = response.json()
    assert body["single_contract_ev"] == pytest.approx(10.0)
    assert body["root_branches"][0]["skin_name"] == "Covert Output"
    assert body["root_branches"][0]["children"] == []
