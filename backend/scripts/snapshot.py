#!/usr/bin/env python3
"""One-shot CSFloat snapshot collector for the Phase 2 pricing-data pipeline.

Meant to be invoked on a schedule (Windows Task Scheduler -> wsl.exe -> this
script, since plain WSL2 crontab stops whenever the WSL VM isn't running).
Each run: checks the pause flag, sweeps app.data.tracked_skins.TRACKED_SKINS
once, upserts new/updated listings into pricing_store's real_snapshots table
(deduped by listing_id -- reseeing the same ask doesn't inflate the
dataset), and logs a one-line summary. Never touches synthetic_snapshots.

Manual run: backend/.venv/bin/python backend/scripts/snapshot.py
Pause/resume: see scripts/pause_collector.sh and scripts/resume_collector.sh
Status: backend/.venv/bin/python backend/scripts/snapshot_status.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

BACKEND_DIR = Path(__file__).resolve().parent.parent
os.chdir(BACKEND_DIR)  # so relative paths (.env, .cache/...) resolve the
# same way regardless of what cwd Task Scheduler / wsl.exe invokes us from
sys.path.insert(0, str(BACKEND_DIR))

from app.config import get_settings  # noqa: E402
from app.data.csfloat_client import CsFloatClient, is_high_value_stickered  # noqa: E402
from app.data.csgo_catalog import CsgoCatalog  # noqa: E402
from app.data.pricing_store import PricingStore  # noqa: E402
from app.data.tracked_skins import TRACKED_SKINS  # noqa: E402
from app.domain.models import SkinCatalogEntry  # noqa: E402
from app.pricing.deal_radar import scan_for_deals, to_result_dict  # noqa: E402

(BACKEND_DIR / ".cache").mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    handlers=[
        logging.FileHandler(BACKEND_DIR / ".cache" / "snapshot.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("snapshot")

# category query param per CSFloat docs: 0=any, 1=normal, 2=stattrak, 3=souvenir.
# We only track normal (non-StatTrak) variants for this first pass.
NORMAL_CATEGORY = 1

# WSL2's network stack can come back up broken after a sleep/wake or reboot
# cycle (DNS resolution failing while the rest of the OS looks fine) -- a run
# in that state used to complete "successfully" while silently fetching
# nothing at all, indistinguishable from a quiet market unless you read the
# raw log. These constants exist to catch that case loudly instead.
DNS_CHECK_HOST = "csfloat.com"
DNS_PREFLIGHT_RETRIES = 3
DNS_PREFLIGHT_DELAY_SECONDS = 5
FETCH_RETRY_ATTEMPTS = 2
FETCH_RETRY_DELAY_SECONDS = 3
# A 429 means the server is explicitly telling us to slow down -- retrying
# that with the same short delay used for a transient network blip is the
# wrong response and can make things worse. Back off much longer instead,
# and respect the server's own Retry-After value when it sends one.
RATE_LIMIT_RETRY_DELAY_SECONDS = 15.0
MAX_RATE_LIMIT_RETRY_DELAY_SECONDS = 30.0
HEALTH_FILE = BACKEND_DIR / ".cache" / "collector_health.json"

# Once CSFloat is actually rate-limiting us (not just one flaky call), keep
# marching through the remaining skins/queries at the same cadence *extends*
# the lockout instead of waiting it out -- confirmed directly: a single test
# run logged 274 failed requests over ~20 minutes doing exactly that. A small
# number of consecutive fully-exhausted 429s (i.e. ones that survived
# _fetch_with_retry's own per-call backoff) trips this breaker and aborts the
# rest of the run outright, leaving it to the next scheduled tick instead.
RATE_LIMIT_CIRCUIT_BREAKER_THRESHOLD = 3

# CSFloat's real budget was measured live via its X-RateLimit-* headers as
# 200 requests per a ~2-hour window (not the 15-minute schedule cadence we'd
# assumed) -- reverse-engineering the exact refill behavior isn't reliable,
# so instead of hardcoding a request budget per sweep, stop proactively once
# the live remaining count reported by CSFloat itself gets low, before we'd
# ever actually draw a 429. The margin needs to comfortably clear one skin's
# worth of queries (4) so a sweep doesn't stop mid-skin right at the wire.
RATE_LIMIT_SAFETY_MARGIN = 12


class _CircuitBreaker:
    def __init__(self, threshold: int = RATE_LIMIT_CIRCUIT_BREAKER_THRESHOLD):
        self.threshold = threshold
        self.consecutive_rate_limits = 0
        self._tripped_reason: str | None = None

    def record_success(self) -> None:
        self.consecutive_rate_limits = 0

    def record_rate_limited(self) -> None:
        self.consecutive_rate_limits += 1
        if self.consecutive_rate_limits >= self.threshold and self._tripped_reason is None:
            self._tripped_reason = f"{self.consecutive_rate_limits} consecutive rate-limited fetches"

    def check_budget(self, csfloat: CsFloatClient) -> None:
        remaining = csfloat.last_rate_limit_remaining
        if remaining is not None and remaining < RATE_LIMIT_SAFETY_MARGIN and self._tripped_reason is None:
            self._tripped_reason = f"only {remaining} requests left in CSFloat's rate-limit window"

    @property
    def tripped(self) -> bool:
        return self._tripped_reason is not None

    @property
    def reason(self) -> str:
        return self._tripped_reason or ""


def _dns_resolves(host: str = DNS_CHECK_HOST, timeout: float = 3.0) -> bool:
    try:
        socket.setdefaulttimeout(timeout)
        socket.gethostbyname(host)
        return True
    except OSError:
        return False


def _wait_for_network() -> bool:
    """Retries DNS resolution a few times with a short delay before giving
    up -- covers the common case where WSL's resolver just needs a moment to
    settle after waking from sleep, without burning a full sweep's worth of
    doomed HTTP calls to discover that."""
    for attempt in range(1, DNS_PREFLIGHT_RETRIES + 1):
        if _dns_resolves():
            return True
        log.warning(
            "DNS preflight failed (attempt %d/%d) -- retrying in %ds",
            attempt, DNS_PREFLIGHT_RETRIES, DNS_PREFLIGHT_DELAY_SECONDS,
        )
        time.sleep(DNS_PREFLIGHT_DELAY_SECONDS)
    return False


def _write_health(*, attempted: int, failed: int, new: int, duplicate: int, note: str = "") -> None:
    HEALTH_FILE.write_text(json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "attempted": attempted,
        "failed": failed,
        "new": new,
        "duplicate": duplicate,
        "note": note,
    }))


def _retry_delay_for(exc: Exception) -> tuple[float, str]:
    """Returns (delay_seconds, label) -- a 429 gets a much longer, server-
    directed backoff; anything else gets the short transient-error delay.

    CSFloat doesn't send Retry-After, but it does send standard
    X-RateLimit-* headers (confirmed live: limit=200, remaining, reset as a
    Unix timestamp) -- that reset time is a real, accurate signal for how
    long the current window has left, so prefer it over the blind guess.
    Still capped at MAX_RATE_LIMIT_RETRY_DELAY_SECONDS: this is only the
    delay for one in-process retry, not a promise to wait out the whole
    window -- if the real reset is farther off than that, better to let this
    attempt fail (feeding the circuit breaker) than block the whole sweep.
    """
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
        headers = exc.response.headers
        reset_at = headers.get("X-RateLimit-Reset", "")
        retry_after = headers.get("Retry-After", "")
        if reset_at.isdigit():
            delay = float(reset_at) - time.time()
        elif retry_after.isdigit():
            delay = float(retry_after)
        else:
            delay = RATE_LIMIT_RETRY_DELAY_SECONDS
        return max(0.0, min(delay, MAX_RATE_LIMIT_RETRY_DELAY_SECONDS)), "rate limited (429)"
    return FETCH_RETRY_DELAY_SECONDS, "fetch failed"


async def _fetch_with_retry(
    csfloat: CsFloatClient, *, skin_name: str, sort_by: str, breaker: _CircuitBreaker, **kwargs
) -> list[dict] | None:
    """Retries a fetch a couple of times on transient errors (DNS blips,
    momentary connection resets, rate limiting) before giving up on this one
    call. Returns None (not an exception) on final failure, so callers can
    just count it. Feeds the shared circuit breaker so a run of consecutive
    429s (as opposed to occasional unrelated failures) can abort the whole
    sweep instead of continuing to add pressure."""
    last_exc: Exception | None = None
    for attempt in range(1, FETCH_RETRY_ATTEMPTS + 1):
        try:
            listings = await csfloat.get_listings(sort_by=sort_by, **kwargs)
            breaker.record_success()
            return listings
        except Exception as exc:
            last_exc = exc
            delay, label = _retry_delay_for(exc)
            if attempt < FETCH_RETRY_ATTEMPTS:
                log.warning(
                    "%s for %r (sort_by=%s, attempt %d/%d): %s -- retrying in %.0fs",
                    label, skin_name, sort_by, attempt, FETCH_RETRY_ATTEMPTS, exc, delay,
                )
                await asyncio.sleep(delay)
            else:
                log.error(
                    "%s for %r (sort_by=%s) after %d attempts: %s",
                    label, skin_name, sort_by, FETCH_RETRY_ATTEMPTS, exc,
                )
    if isinstance(last_exc, httpx.HTTPStatusError) and last_exc.response.status_code == 429:
        breaker.record_rate_limited()
    return None


async def sweep_skin(
    csfloat: CsFloatClient, store: PricingStore, skin: SkinCatalogEntry, breaker: _CircuitBreaker
) -> tuple[int, int, int, int, int, int]:
    """Returns (new_count, duplicate_count, failed_count, attempted_count,
    mismatched_count, stickered_count)."""
    if not skin.paint_index:
        log.warning("skipping %r: no paint_index in catalog data", skin.name)
        return 0, 0, 0, 0, 0, 0

    seen_at = datetime.now(timezone.utc).isoformat()
    new_count = 0
    dup_count = 0
    fail_count = 0
    attempted = 0
    mismatched_count = 0
    stickered_count = 0
    # paint_index isn't always unique to one weapon -- "cross-weapon" finishes
    # like Case Hardened, Blaze, and Fade reuse the same paint_index across
    # AK-47s, knives, pistols, etc. Confirmed directly against the live DB:
    # 82-92% of stored rows for exactly those three skins were actually a
    # different weapon entirely (e.g. a Kukri Knife stored as "AK-47 | Case
    # Hardened"), which explains why those three had by far the worst eval
    # error of any tracked skin -- not a hard-to-model skin, just wrong data.
    expected_weapon_prefix = skin.name.split(" | ", 1)[0] + " |"

    # paint_index is wear-independent, so this spans the skin's whole float
    # range across these queries. Contiguous, non-overlapping bands, each
    # queried with its own explicit min/max bounds, rather than sorting
    # from a shared edge -- an earlier version here used a full-range pair
    # plus a middle-half pair (both sorted lowest_float/highest_float),
    # which still clustered up to 50 results against whichever edge each
    # query sorted from. Confirmed live by bucketing real_snapshots into
    # deciles of each skin's float range: the 10-20% and 30-40% bands came
    # back systematically empty for nearly every tracked skin, because
    # nothing ever queried those bands with bounds aimed directly at them
    # -- the "fix" just moved the same clustering problem to new shared
    # edges (25%/75% instead of 0%/100%). Disjoint quarters with no shared
    # edges means every part of the range gets a query pointed straight at
    # it, so there's no boundary left to cluster against. Same total
    # request count as before; sort direction alternates per band only so
    # a band with >50 listings doesn't always favor the same relative edge.
    N_FLOAT_BANDS = 4
    range_span = skin.max_float - skin.min_float
    band_width = range_span / N_FLOAT_BANDS
    queries = tuple(
        (
            "lowest_float" if i % 2 == 0 else "highest_float",
            skin.min_float + i * band_width,
            skin.min_float + (i + 1) * band_width,
        )
        for i in range(N_FLOAT_BANDS)
    )
    for sort_by, query_min_float, query_max_float in queries:
        breaker.check_budget(csfloat)
        if breaker.tripped:
            log.error("circuit breaker tripped (%s) -- aborting rest of %r's queries", breaker.reason, skin.name)
            break
        attempted += 1
        listings = await _fetch_with_retry(
            csfloat,
            skin_name=skin.name,
            sort_by=sort_by,
            breaker=breaker,
            paint_index=skin.paint_index,
            min_float=query_min_float,
            max_float=query_max_float,
            category=NORMAL_CATEGORY,
            limit=50,
        )
        if listings is None:
            fail_count += 1
            continue

        for listing in listings:
            listing_id = listing.get("id")
            price_cents = listing.get("price")
            if listing_id is None or price_cents is None:
                continue
            item = listing.get("item") or {}
            hash_name = item.get("market_hash_name", skin.name)
            if not hash_name.startswith(expected_weapon_prefix):
                mismatched_count += 1
                continue
            # A sticker's collector value has nothing to do with the base
            # skin's float, but its price rides along on the same listing --
            # confirmed live: a Redline with 4x Katowice 2014 stickers asked
            # $47,500 against a ~$80 bare median. A trade-up output is always
            # a fresh, undecorated item (see naming.py), so a decorated
            # listing was never a fair training example for this skin's
            # float-vs-price curve in the first place. Only high-value
            # sticker sets get skipped though (see csfloat_client.py's
            # STICKER_VALUE_THRESHOLD_CENTS) -- confirmed live that a bare
            # "any sticker at all" check throws out 70-90% of real listings
            # for popular skins, since most carry at least one cheap,
            # harmless one.
            if is_high_value_stickered(item):
                stickered_count += 1
                continue
            reference = listing.get("reference") or {}
            is_new = store.upsert_real_snapshot(
                listing_id=str(listing_id),
                market_hash_name=hash_name,
                skin_name=skin.name,
                stattrak=False,
                float_value=item.get("float_value"),
                price_cents=int(price_cents),
                predicted_price_cents=reference.get("predicted_price"),
                seen_at=seen_at,
            )
            if is_new:
                new_count += 1
            else:
                dup_count += 1

    if mismatched_count:
        log.info("%r: skipped %d listings for a different weapon sharing this paint_index", skin.name, mismatched_count)
    if stickered_count:
        log.info("%r: skipped %d stickered listings", skin.name, stickered_count)

    return new_count, dup_count, fail_count, attempted, mismatched_count, stickered_count


async def run() -> None:
    settings = get_settings()
    pause_flag = Path(settings.collector_pause_flag_path)
    if pause_flag.exists():
        log.info("paused (found %s) -- skipping this run", pause_flag)
        return

    if not settings.csfloat_api_key:
        log.error("CSFLOAT_API_KEY not set (check backend/.env) -- aborting run")
        return

    if not _wait_for_network():
        log.error(
            "DNS still failing after %d retries -- skipping this sweep entirely "
            "rather than burning it on doomed calls (likely WSL network not "
            "settled yet after sleep/wake; should recover by the next tick)",
            DNS_PREFLIGHT_RETRIES,
        )
        _write_health(attempted=0, failed=0, new=0, duplicate=0, note="skipped: DNS preflight failed")
        return

    catalog = CsgoCatalog(settings)
    csfloat = CsFloatClient(settings)
    store = PricingStore(settings.pricing_db_path)

    try:
        await catalog.load()
        total_new = 0
        total_dup = 0
        total_fail = 0
        total_attempted = 0
        total_mismatched = 0
        total_stickered = 0
        missing: list[str] = []
        breaker = _CircuitBreaker()

        for name in TRACKED_SKINS:
            breaker.check_budget(csfloat)
            if breaker.tripped:
                log.error(
                    "circuit breaker tripped (%s) -- stopping sweep early, "
                    "remaining tracked skins skipped this run", breaker.reason,
                )
                break
            skin = catalog.get(name)
            if skin is None:
                missing.append(name)
                continue
            new_count, dup_count, fail_count, attempted, mismatched_count, stickered_count = await sweep_skin(csfloat, store, skin, breaker)
            total_new += new_count
            total_dup += dup_count
            total_fail += fail_count
            total_attempted += attempted
            total_mismatched += mismatched_count
            total_stickered += stickered_count

        if missing:
            log.warning("tracked skins not found in catalog: %s", missing)

        real_total, synthetic_total = store.total_counts()
        log.info(
            "sweep done: +%d new, %d duplicate, %d/%d fetches failed, %d skipped (wrong weapon), "
            "%d skipped (stickered), %d real rows total (%d synthetic, untouched)",
            total_new,
            total_dup,
            total_fail,
            total_attempted,
            total_mismatched,
            total_stickered,
            real_total,
            synthetic_total,
        )

        # A high failure rate is exactly the "ran fine but got nothing"
        # failure mode that used to be invisible without reading the raw
        # log -- flag it loudly and record it so snapshot_status.py can
        # surface it without anyone having to go digging.
        note = ""
        if total_attempted > 0 and total_fail / total_attempted > 0.5:
            note = f"degraded: {total_fail}/{total_attempted} fetches failed this sweep"
            log.error("SWEEP DEGRADED -- %s (see snapshot.log above for the specific cause -- DNS, rate limiting, or connectivity)", note)
        _write_health(attempted=total_attempted, failed=total_fail, new=total_new, duplicate=total_dup, note=note)

        # Re-scan for mispriced listings against the data this sweep just
        # refreshed. Free (local SQLite + Isolation Forest, no network) for
        # every tracked skin; only the top few candidates spend a live call
        # each to confirm something similar is still findable, and only if
        # this sweep hasn't already used up the window -- reuses the same
        # already-open csfloat/store so rate-limit awareness carries over
        # exactly. See app/pricing/deal_radar.py for the full design.
        deals = await scan_for_deals(store, csfloat, TRACKED_SKINS)
        deal_settings = get_settings()
        Path(deal_settings.deal_candidates_path).write_text(
            json.dumps(to_result_dict(deals, generated_at=datetime.now(timezone.utc).isoformat()))
        )
        log.info("deal radar: %d candidate(s) flagged, %d verified live",
                  len(deals), sum(d.verified_live for d in deals))
    finally:
        await csfloat.aclose()
        store.close()


if __name__ == "__main__":
    asyncio.run(run())
