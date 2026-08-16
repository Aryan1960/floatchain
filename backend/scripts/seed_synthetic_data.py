#!/usr/bin/env python3
"""Seeds the synthetic_snapshots table with a plausible float-to-price curve
per tracked skin, purely so the ML pipeline (Phase 2, built after the
real-data checkpoint) can be exercised before enough real CSFloat data has
accumulated. Makes no network calls. Never touches real_snapshots, and
nothing that trains a "real" model should ever read this table.

Shape: price decays steeply from Factory New toward Battle-Scarred then
flattens (matching the roadmap's note that the real curve isn't linear),
plus lognormal noise to mimic real listing-price variance across sellers.

    backend/.venv/bin/python backend/scripts/seed_synthetic_data.py [--clear] [--points-per-skin N]
"""

from __future__ import annotations

import argparse
import asyncio
import math
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
os.chdir(BACKEND_DIR)
sys.path.insert(0, str(BACKEND_DIR))

from app.config import get_settings  # noqa: E402
from app.data.csgo_catalog import CsgoCatalog  # noqa: E402
from app.data.pricing_store import PricingStore  # noqa: E402
from app.data.tracked_skins import TRACKED_SKINS  # noqa: E402

DECAY_K = 2.5  # higher = steeper drop-off near Factory New
PRICE_FLOOR_FRACTION = 0.15  # price never decays below this fraction of base
NOISE_SIGMA = 0.12  # lognormal noise, mimics seller-to-seller ask variance

SEED = 20260812  # fixed, so re-running without --clear regenerates identically


def price_curve_cents(base_price_cents: int, float_value: float) -> int:
    factor = math.exp(-DECAY_K * float_value)
    shaped = PRICE_FLOOR_FRACTION + (1 - PRICE_FLOOR_FRACTION) * factor
    return max(1, round(base_price_cents * shaped))


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clear", action="store_true", help="wipe synthetic_snapshots first")
    parser.add_argument("--points-per-skin", type=int, default=80)
    args = parser.parse_args()

    settings = get_settings()
    catalog = CsgoCatalog(settings)
    await catalog.load()

    store = PricingStore(settings.pricing_db_path)
    rng = random.Random(SEED)
    generated_at = datetime.now(timezone.utc).isoformat()

    try:
        if args.clear:
            store.clear_synthetic()
            print("cleared synthetic_snapshots")

        total = 0
        for name in TRACKED_SKINS:
            skin = catalog.get(name)
            if skin is None:
                print(f"skipping {name!r}: not found in catalog")
                continue

            # Arbitrary-but-plausible base price per skin, deterministic given SEED.
            base_price_cents = rng.randint(500, 15000)

            for _ in range(args.points_per_skin):
                float_value = rng.uniform(skin.min_float, skin.max_float)
                noise = rng.lognormvariate(0, NOISE_SIGMA)
                price_cents = max(1, round(price_curve_cents(base_price_cents, float_value) * noise))
                store.insert_synthetic(
                    skin_name=skin.name,
                    stattrak=False,
                    float_value=float_value,
                    price_cents=price_cents,
                    generated_at=generated_at,
                )
                total += 1

        print(f"seeded {total} synthetic rows across {len(TRACKED_SKINS)} tracked skins")
    finally:
        store.close()


if __name__ == "__main__":
    asyncio.run(main())
