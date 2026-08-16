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
from app.data.csfloat_client import CsFloatClient  # noqa: E402
from app.data.csgo_catalog import CsgoCatalog  # noqa: E402
from app.data.pricing_store import PricingStore  # noqa: E402
from app.data.tracked_skins import TRACKED_SKINS  # noqa: E402
from app.domain.models import SkinCatalogEntry  # noqa: E402

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
    directed backoff; anything else gets the short transient-error delay."""
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
        retry_after = exc.response.headers.get("Retry-After", "")
        delay = float(retry_after) if retry_after.isdigit() else RATE_LIMIT_RETRY_DELAY_SECONDS
        return min(delay, MAX_RATE_LIMIT_RETRY_DELAY_SECONDS), "rate limited (429)"
    return FETCH_RETRY_DELAY_SECONDS, "fetch failed"


async def _fetch_with_retry(csfloat: CsFloatClient, *, skin_name: str, sort_by: str, **kwargs) -> list[dict] | None:
    """Retries a fetch a couple of times on transient errors (DNS blips,
    momentary connection resets, rate limiting) before giving up on this one
    call. Returns None (not an exception) on final failure, so callers can
    just count it."""
    for attempt in range(1, FETCH_RETRY_ATTEMPTS + 1):
        try:
            return await csfloat.get_listings(sort_by=sort_by, **kwargs)
        except Exception as exc:
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
    return None


async def sweep_skin(
    csfloat: CsFloatClient, store: PricingStore, skin: SkinCatalogEntry
) -> tuple[int, int, int, int]:
    """Returns (new_count, duplicate_count, failed_count, attempted_count) for one skin."""
    if not skin.paint_index:
        log.warning("skipping %r: no paint_index in catalog data", skin.name)
        return 0, 0, 0, 0

    seen_at = datetime.now(timezone.utc).isoformat()
    new_count = 0
    dup_count = 0
    fail_count = 0
    attempted = 0

    # paint_index is wear-independent, so this spans the skin's whole float
    # range in one call. Query both float-sorted directions so rare extremes
    # (near-FN, near-BS) get deliberate coverage rather than only whatever a
    # single default sort happens to surface.
    for sort_by in ("lowest_float", "highest_float"):
        attempted += 1
        listings = await _fetch_with_retry(
            csfloat,
            skin_name=skin.name,
            sort_by=sort_by,
            paint_index=skin.paint_index,
            min_float=skin.min_float,
            max_float=skin.max_float,
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
            reference = listing.get("reference") or {}
            is_new = store.upsert_real_snapshot(
                listing_id=str(listing_id),
                market_hash_name=item.get("market_hash_name", skin.name),
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

    return new_count, dup_count, fail_count, attempted


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
        missing: list[str] = []

        for name in TRACKED_SKINS:
            skin = catalog.get(name)
            if skin is None:
                missing.append(name)
                continue
            new_count, dup_count, fail_count, attempted = await sweep_skin(csfloat, store, skin)
            total_new += new_count
            total_dup += dup_count
            total_fail += fail_count
            total_attempted += attempted

        if missing:
            log.warning("tracked skins not found in catalog: %s", missing)

        real_total, synthetic_total = store.total_counts()
        log.info(
            "sweep done: +%d new, %d duplicate, %d/%d fetches failed, %d real rows total "
            "(%d synthetic, untouched)",
            total_new,
            total_dup,
            total_fail,
            total_attempted,
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
    finally:
        await csfloat.aclose()
        store.close()


if __name__ == "__main__":
    asyncio.run(run())
