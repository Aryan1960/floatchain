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

# Confirmed live (see the build log): 70-90% of real listings for popular
# skins carry *some* sticker, usually a cheap/common one worth a few dollars
# combined -- filtering out every stickered listing was throwing away the
# large majority of otherwise-normal data for exactly the skins we most want
# volume on. Only a listing whose stickers are collectively worth enough to
# plausibly move its ask price matters here (a real case: 4x Katowice 2014
# stickers worth $100,208 *each*, on an ~$80 skin) -- realistically nobody's
# going to notice or care about a $5 sticker's effect on a trade-up price,
# so the threshold is set well above "common decoration," not at zero.
STICKER_VALUE_THRESHOLD_CENTS = 5000  # $50 combined


def sticker_value_cents(item: dict) -> int:
    """Sum of each attached sticker's own CSFloat reference price -- 0 for a
    bare item or one with no sticker pricing data at all."""
    stickers = item.get("stickers") or []
    return sum((s.get("reference") or {}).get("price") or 0 for s in stickers)


def is_high_value_stickered(item: dict, threshold_cents: int = STICKER_VALUE_THRESHOLD_CENTS) -> bool:
    return sticker_value_cents(item) > threshold_cents


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
        # CSFloat sends standard X-RateLimit-* headers on every /listings
        # response (confirmed live: limit=200, a remaining count, and a reset
        # Unix timestamp -- observed as a fixed window rather than per-call
        # throttling). Tracking the latest values lets callers throttle
        # themselves proactively before actually exhausting the budget,
        # instead of only reacting after a 429 already happened.
        self.last_rate_limit_remaining: int | None = None
        self.last_rate_limit_reset: int | None = None

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
        self._record_rate_limit_headers(response)
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

        Picks the listing whose float is closest to the target (preferring a
        listing without a high-value sticker set, if one's available -- see
        below), then prefers that listing's `reference.predicted_price` —
        CSFloat's own float-aware fair-value model — over its raw `price`.
        The raw ask price is one seller's number for their specific item and
        is frequently a wild outlier; predicted_price tracks the bare-skin
        float curve instead, which is also the right thing for us since a
        trade-up output is always a fresh, sticker-free item. Falls back to
        the raw price if predicted_price is ever absent, and to None if there
        are no active listings for this exact market_hash_name at all.

        The sticker check is a defense-in-depth measure, not the primary
        fix: confirmed against real stored data that predicted_price is
        usually (not always) already close to bare-skin fair value even for
        heavily-stickered listings, so this mostly protects the sizeable
        minority of cases where it isn't, and the cases where the closest
        listing's raw price is used as a fallback (no predicted_price at
        all) -- there, a stickered price would go straight through
        unfiltered otherwise. Only high-value sticker sets are excluded
        (see STICKER_VALUE_THRESHOLD_CENTS) -- most real listings carry at
        least one cheap, harmless sticker, and excluding all of them would
        throw away the majority of otherwise-normal listings.
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

        bare_listings = [l for l in listings if not is_high_value_stickered(l.get("item") or {})]
        candidates = bare_listings or listings  # fall back to stickered if that's all there is

        best = min(candidates, key=float_distance)
        reference = best.get("reference") or {}
        price_cents = reference.get("predicted_price")
        if price_cents is None:
            price_cents = best.get("price")
        if price_cents is None:
            return None
        return price_cents / 100

    def _record_rate_limit_headers(self, response: httpx.Response) -> None:
        remaining = response.headers.get("X-RateLimit-Remaining")
        reset = response.headers.get("X-RateLimit-Reset")
        if remaining is not None and remaining.isdigit():
            self.last_rate_limit_remaining = int(remaining)
        if reset is not None and reset.isdigit():
            self.last_rate_limit_reset = int(reset)

    async def _respect_rate_limit(self) -> None:
        async with self._lock:
            elapsed = time.time() - self._last_request_at
            wait = self._settings.csfloat_min_request_interval_seconds - elapsed
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request_at = time.time()
