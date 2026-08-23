"""Finds real, currently-flagged mispriced listings across the tracked
skins -- "money left on the table," not a rescan of the whole market.

Two-stage, deliberately: `real_snapshots` never marks a listing sold or
delisted (upsert_real_snapshot only ever adds or refreshes a row), so a
flagged anomaly could be a listing from weeks ago that's long gone. But
scanning for anomalies itself is 100% local (SQLite + Isolation Forest, no
network) -- so stage one costs nothing and can run every time the collector
does. Only the handful of best-looking candidates get a live CSFloat call
to confirm something similar is still actually findable, keeping the
expensive part bounded regardless of how many skins get scanned.

Does NOT reuse app.pricing.service.detect_anomalies() directly -- that
function's data source (real_points_for_skin) doesn't carry listing_id or
last_seen_at, which this needs to drop stale rows and know what to
spot-check. Uses the same underlying AnomalyDetector primitive instead,
against app.data.pricing_store.PricingStore.real_points_with_metadata.

Known limitation, not addressed here: the collector doesn't sample a
skin's whole float range uniformly (see scripts/snapshot.py's own comments
about a once-found "dead zone"), so a real deal sitting outside its sampled
bands is invisible to this whole pipeline -- not wrongly rejected, never
seen. Fixing that means changing the collector's own query strategy, a
separate Phase-2-owned follow-up. This module reports `sample_count` per
candidate specifically so a caller can gauge confidence instead of every
flag looking equally sure of itself, and callers should present results as
"best deals in what's been collected so far," not "best deals in the
market."
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np

from app.data.csfloat_client import CsFloatClient
from app.data.pricing_store import PricingStore
from app.domain.naming import market_hash_name
from app.domain.wear import wear_name
from app.pricing.anomaly import MIN_POINTS_FOR_ANOMALY_DETECTION, AnomalyDetector
from app.pricing.service import predict_price

# ~2 collector cycles (see scripts/snapshot.py's ~1-hour schedule,
# stretched from 30 min once duplicate-heavy sweeps showed 30 min was
# burning budget for near-zero new-data yield) -- a cheap, honest proxy
# for "probably still up," not a guarantee.
RECENCY_WINDOW = timedelta(hours=2)

# Isolation Forest flags points far from the cluster in EITHER direction;
# only a real discount below the fitted curve counts as a deal here.
MIN_DISCOUNT_PCT = 0.15

TOP_N_CANDIDATES = 5

# Skip live verification once the sweep that just ran has left this little
# headroom -- same margin already used by the tier-survey backtest script.
VERIFY_SAFETY_MARGIN = 20


@dataclass(frozen=True)
class DealCandidate:
    skin_name: str
    float_value: float
    price_cents: int
    model_price_cents: int
    discount_pct: float
    last_seen_at: str
    verified_live: bool
    sample_count: int


def _scan_skin(store: PricingStore, skin_name: str, stattrak: bool) -> list[DealCandidate]:
    rows = store.real_points_with_metadata(skin_name, stattrak)
    if len(rows) < MIN_POINTS_FOR_ANOMALY_DETECTION:
        return []

    floats = np.array([r[0] for r in rows], dtype=float)
    prices = np.array([r[1] for r in rows], dtype=float)
    detector = AnomalyDetector()
    detector.fit(floats, prices)
    results = detector.evaluate(floats, prices)

    candidates: list[DealCandidate] = []
    for (float_value, price_cents, last_seen_at, _listing_id), result in zip(rows, results):
        if not result.is_anomaly:
            continue
        prediction = predict_price(store, skin_name, stattrak, float_value)
        if prediction.model_price_cents is None:
            continue  # no baseline to judge direction against
        model_price = prediction.model_price_cents
        if price_cents >= model_price:
            continue  # anomaly is on the expensive side, not a deal
        discount_pct = (model_price - price_cents) / model_price
        if discount_pct < MIN_DISCOUNT_PCT:
            continue
        candidates.append(DealCandidate(
            skin_name=skin_name,
            float_value=float_value,
            price_cents=price_cents,
            model_price_cents=int(model_price),
            discount_pct=discount_pct,
            last_seen_at=last_seen_at,
            verified_live=False,
            sample_count=len(rows),
        ))
    return candidates


def _is_recent(last_seen_at: str, now: datetime) -> bool:
    try:
        seen = datetime.fromisoformat(last_seen_at)
    except ValueError:
        return False
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    return now - seen <= RECENCY_WINDOW


async def _verify(csfloat: CsFloatClient, candidate: DealCandidate, stattrak: bool) -> bool:
    wear = wear_name(candidate.float_value)
    hash_name = market_hash_name(candidate.skin_name, wear, stattrak)
    price = await csfloat.price_near_float(hash_name, candidate.float_value)
    if price is None:
        return False
    # "Still findable near this float at a similarly low price" -- there's
    # no exact-listing-id lookup on CsFloatClient (only get_listings/
    # price_near_float), so this can't confirm the SAME listing, only that
    # the opportunity itself still looks real.
    return price * 100 <= candidate.model_price_cents * (1 - MIN_DISCOUNT_PCT / 2)


async def scan_for_deals(
    store: PricingStore,
    csfloat: CsFloatClient | None,
    tracked_skins: tuple[str, ...],
) -> list[DealCandidate]:
    now = datetime.now(timezone.utc)
    all_candidates: list[DealCandidate] = []
    for skin_name in tracked_skins:
        for candidate in _scan_skin(store, skin_name, stattrak=False):
            if _is_recent(candidate.last_seen_at, now):
                all_candidates.append(candidate)

    all_candidates.sort(key=lambda c: c.discount_pct, reverse=True)
    top = all_candidates[:TOP_N_CANDIDATES]
    rest = all_candidates[TOP_N_CANDIDATES:]

    verified_top: list[DealCandidate] = []
    for candidate in top:
        verified = False
        if csfloat is not None:
            remaining = csfloat.last_rate_limit_remaining
            # None means no real call has happened yet on this client --
            # treat as "unknown, don't spend" rather than "assume safe"
            # (the exact mistake that caused a budget incident earlier).
            if remaining is not None and remaining >= VERIFY_SAFETY_MARGIN:
                verified = await _verify(csfloat, candidate, stattrak=False)
        verified_top.append(
            DealCandidate(
                skin_name=candidate.skin_name,
                float_value=candidate.float_value,
                price_cents=candidate.price_cents,
                model_price_cents=candidate.model_price_cents,
                discount_pct=candidate.discount_pct,
                last_seen_at=candidate.last_seen_at,
                verified_live=verified,
                sample_count=candidate.sample_count,
            )
        )

    return verified_top + rest


def to_result_dict(candidates: list[DealCandidate], generated_at: str) -> dict:
    """Matches what GET /api/pricing/deals reads back -- kept here, next to
    DealCandidate itself, rather than duplicated at each write site."""
    return {
        "generated_at": generated_at,
        "candidates": [
            {
                "skin_name": c.skin_name,
                "float_value": c.float_value,
                "price": c.price_cents / 100,
                "model_price": c.model_price_cents / 100,
                "discount_pct": round(c.discount_pct * 100, 1),
                "last_seen_at": c.last_seen_at,
                "verified_live": c.verified_live,
                "sample_count": c.sample_count,
            }
            for c in candidates
        ],
    }
