import httpx
import pytest

from app.config import Settings
from app.data.csfloat_client import CsFloatClient, is_high_value_stickered, sticker_value_cents


def test_sticker_value_cents_sums_multiple_stickers():
    item = {"stickers": [{"reference": {"price": 300}}, {"reference": {"price": 500}}]}
    assert sticker_value_cents(item) == 800


def test_sticker_value_cents_zero_for_bare_item():
    assert sticker_value_cents({}) == 0
    assert sticker_value_cents({"stickers": []}) == 0


def test_sticker_value_cents_ignores_missing_reference_data():
    # Real CSFloat payloads sometimes omit reference pricing for obscure
    # stickers -- shouldn't crash, should just count as worthless.
    item = {"stickers": [{"stickerId": 1}, {"reference": {}}]}
    assert sticker_value_cents(item) == 0


def test_is_high_value_stickered_below_threshold():
    item = {"stickers": [{"reference": {"price": 300}}]}
    assert is_high_value_stickered(item) is False


def test_is_high_value_stickered_above_threshold():
    item = {"stickers": [{"reference": {"price": 10020825}}]}
    assert is_high_value_stickered(item) is True


def _client_with_response(json_body, status_code=200) -> CsFloatClient:
    settings = Settings(csfloat_api_key="test-key", csfloat_min_request_interval_seconds=0.0)
    client = CsFloatClient(settings)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=json_body)

    client._client = httpx.AsyncClient(
        base_url=settings.csfloat_api_base,
        transport=httpx.MockTransport(handler),
    )
    return client


@pytest.mark.asyncio
async def test_get_listings_unwraps_paginated_envelope():
    # The real API returns {"data": [...], "cursor": ...}, not a bare array.
    body = {"data": [{"id": "1", "price": 500, "item": {"float_value": 0.2}}], "cursor": "abc"}
    client = _client_with_response(body)
    listings = await client.get_listings(market_hash_name="AK-47 | Redline (Field-Tested)")
    assert listings == body["data"]
    await client.aclose()


@pytest.mark.asyncio
async def test_get_listings_tolerates_unexpected_shape():
    client = _client_with_response({"error": "something else entirely"})
    listings = await client.get_listings(market_hash_name="AK-47 | Redline (Field-Tested)")
    assert listings == []
    await client.aclose()


@pytest.mark.asyncio
async def test_price_near_float_picks_closest_listing():
    body = {
        "data": [
            {"price": 1000, "item": {"float_value": 0.10}},
            {"price": 2000, "item": {"float_value": 0.20}},
        ]
    }
    client = _client_with_response(body)
    price = await client.price_near_float("AK-47 | Redline (Field-Tested)", target_float=0.19)
    assert price == pytest.approx(20.00)
    await client.aclose()


@pytest.mark.asyncio
async def test_price_near_float_prefers_predicted_price_over_raw_ask():
    # A sticker-decorated/mispriced ask ($99) shouldn't win over CSFloat's own
    # float-aware fair-value model on the same, closest-float listing.
    body = {
        "data": [
            {
                "price": 9900,
                "reference": {"predicted_price": 2050, "base_price": 2000},
                "item": {"float_value": 0.19},
            },
        ]
    }
    client = _client_with_response(body)
    price = await client.price_near_float("AK-47 | Redline (Field-Tested)", target_float=0.19)
    assert price == pytest.approx(20.50)
    await client.aclose()


@pytest.mark.asyncio
async def test_price_near_float_falls_back_to_raw_ask_without_predicted_price():
    body = {"data": [{"price": 1500, "item": {"float_value": 0.19}}]}
    client = _client_with_response(body)
    price = await client.price_near_float("AK-47 | Redline (Field-Tested)", target_float=0.19)
    assert price == pytest.approx(15.00)
    await client.aclose()


@pytest.mark.asyncio
async def test_price_near_float_returns_none_with_no_listings():
    client = _client_with_response({"data": []})
    price = await client.price_near_float("AK-47 | Redline (Field-Tested)", target_float=0.19)
    assert price is None
    await client.aclose()


@pytest.mark.asyncio
async def test_price_near_float_ignores_cheap_stickers():
    # A $3 sticker is exactly the "harmless decoration" case the threshold
    # exists to NOT filter out -- most real listings carry something like
    # this, and excluding them all would throw away most of the data.
    body = {
        "data": [
            {"price": 2050, "item": {"float_value": 0.19, "stickers": [{"reference": {"price": 300}}]}},
        ]
    }
    client = _client_with_response(body)
    price = await client.price_near_float("AK-47 | Redline (Field-Tested)", target_float=0.19)
    assert price == pytest.approx(20.50)
    await client.aclose()


@pytest.mark.asyncio
async def test_price_near_float_prefers_bare_listing_over_closer_high_value_stickered_one():
    # The high-value-stickered listing is a near-exact float match; the bare
    # one is further off. Bare should still win -- a decorated item's price
    # has nothing to do with the bare-skin float curve we're trying to price.
    body = {
        "data": [
            {"price": 50000, "item": {"float_value": 0.190, "stickers": [{"reference": {"price": 10020825}}]}},
            {"price": 2000, "item": {"float_value": 0.15}},
        ]
    }
    client = _client_with_response(body)
    price = await client.price_near_float("AK-47 | Redline (Field-Tested)", target_float=0.19)
    assert price == pytest.approx(20.00)
    await client.aclose()


@pytest.mark.asyncio
async def test_price_near_float_falls_back_to_high_value_stickered_if_thats_all_there_is():
    body = {"data": [{"price": 50000, "item": {"float_value": 0.19, "stickers": [{"reference": {"price": 10020825}}]}}]}
    client = _client_with_response(body)
    price = await client.price_near_float("AK-47 | Redline (Field-Tested)", target_float=0.19)
    assert price == pytest.approx(500.00)
    await client.aclose()
