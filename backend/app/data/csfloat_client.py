"""Thin client for the CSFloat API (https://docs.csfloat.com), used to price
trade-up outputs against live listings instead of static reference prices.

Auth: `Authorization: <API-KEY>` header (no "Bearer" prefix).
Prices come back in cents; this module converts to dollars at the edge.

Rate limiting: CSFloat doesn't document a blanket limit for /listings (hot
endpoints like newest-listings are ~1 call/60s), but we self-impose a
minimum interval between requests plus a short-TTL in-memory cache so
repeated lookups for the same skin during one contract evaluation don't
hammer the API.
"""

from __future__ import annotations

import asyncio
import time

import httpx

from app.config import Settings


class CsFloatClient:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.csfloat_api_base,
            headers={"Authorization": settings.csfloat_api_key},
            timeout=15.0,
        )
        self._lock = asyncio.Lock()
        self._last_request_at: float = 0.0
        self._cache: dict[str, tuple[float, list[dict]]] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_listings(
        self,
        *,
        market_hash_name: str | None = None,
        paint_index: str | None = None,
        min_float: float | None = None,
        max_float: float | None = None,
        category: int | None = None,
        limit: int = 50,
        sort_by: str = "lowest_price",
    ) -> list[dict]:
        """market_hash_name pins the query to one specific wear tier (that's
        baked into the name, Steam-style). paint_index is wear-independent,
        so combining it with min_float/max_float spans a skin's whole float
        range in a single call -- the more efficient option when sampling
        broadly rather than pricing one exact wear."""
        if market_hash_name is None and paint_index is None:
            raise ValueError("must provide market_hash_name or paint_index")

        params: dict[str, str | int | float] = {
            "limit": min(limit, 50),
            "sort_by": sort_by,
        }
        if market_hash_name is not None:
            params["market_hash_name"] = market_hash_name
        if paint_index is not None:
            params["paint_index"] = paint_index
        if min_float is not None:
            params["min_float"] = min_float
        if max_float is not None:
            params["max_float"] = max_float
        if category is not None:
            params["category"] = category

        cache_key = "|".join(f"{k}={v}" for k, v in sorted(params.items()))
        cached = self._cache.get(cache_key)
        if cached and (time.time() - cached[0]) < self._settings.csfloat_cache_ttl_seconds:
            return cached[1]

        await self._respect_rate_limit()
        response = await self._client.get("/listings", params=params)
        response.raise_for_status()
        body = response.json()
        # Live responses are paginated envelopes ({"data": [...], "cursor": ...}),
        # not the bare array docs.csfloat.com's example shows.
        listings = body.get("data", []) if isinstance(body, dict) else body
        if not isinstance(listings, list):
            listings = []

        self._cache[cache_key] = (time.time(), listings)
        return listings

    async def price_near_float(
        self, market_hash_name: str, target_float: float
    ) -> float | None:
        """Best-effort market price (USD) for a skin near a target float.

        Picks the listing whose float is closest to the target, then prefers
        that listing's `reference.predicted_price` — CSFloat's own float-aware
        fair-value model — over its raw `price`. The raw ask price is one
        seller's number for their specific (often sticker-decorated) item and
        is frequently a wild outlier; predicted_price tracks the bare-skin
        float curve instead, which is also the right thing for us since a
        trade-up output is always a fresh, sticker-free item. Falls back to
        the raw price if predicted_price is ever absent, and to None if there
        are no active listings for this exact market_hash_name at all.
        """
        try:
            listings = await self.get_listings(market_hash_name=market_hash_name)
        except httpx.HTTPStatusError:
            return None

        if not listings:
            return None

        def float_distance(listing: dict) -> float:
            item_float = listing.get("item", {}).get("float_value")
            if item_float is None:
                return float("inf")
            return abs(item_float - target_float)

        best = min(listings, key=float_distance)
        reference = best.get("reference") or {}
        price_cents = reference.get("predicted_price")
        if price_cents is None:
            price_cents = best.get("price")
        if price_cents is None:
            return None
        return price_cents / 100

    async def _respect_rate_limit(self) -> None:
        async with self._lock:
            elapsed = time.time() - self._last_request_at
            wait = self._settings.csfloat_min_request_interval_seconds - elapsed
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request_at = time.time()
