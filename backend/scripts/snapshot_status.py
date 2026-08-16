#!/usr/bin/env python3
"""Prints current Phase 2 data-collection progress: how many real (CSFloat)
and synthetic rows exist, broken down per skin. Safe to run any time, does
not touch the network.

    backend/.venv/bin/python backend/scripts/snapshot_status.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
os.chdir(BACKEND_DIR)
sys.path.insert(0, str(BACKEND_DIR))

from app.config import get_settings  # noqa: E402
from app.data.pricing_store import PricingStore  # noqa: E402

HEALTH_FILE = BACKEND_DIR / ".cache" / "collector_health.json"


def _print_health() -> None:
    if not HEALTH_FILE.exists():
        return
    try:
        health = json.loads(HEALTH_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return

    ts = health.get("timestamp", "")
    attempted = health.get("attempted", 0)
    failed = health.get("failed", 0)
    note = health.get("note", "")

    age_note = ""
    try:
        age_minutes = (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds() / 60
        age_note = f" ({age_minutes:.0f} min ago)"
    except ValueError:
        pass

    if note:
        print(f"⚠  LAST SWEEP: {note}{age_note}")
    elif attempted > 0:
        print(f"Last sweep: {attempted - failed}/{attempted} fetches OK{age_note}")


def main() -> None:
    settings = get_settings()
    pause_flag = Path(settings.collector_pause_flag_path)
    store = PricingStore(settings.pricing_db_path)
    try:
        real_total, synthetic_total = store.total_counts()
        print(f"Collector: {'PAUSED' if pause_flag.exists() else 'active'}")
        _print_health()
        print(f"Real rows: {real_total}   Synthetic rows: {synthetic_total}\n")

        print("Real data (CSFloat live listings) by skin:")
        rows = store.real_summary_by_skin()
        if not rows:
            print("  (none yet)")
        else:
            print(f"  {'skin':<34} {'n':>5}  {'first seen (UTC)':<28} {'last seen (UTC)':<28}")
            for r in rows:
                print(
                    f"  {r['skin_name']:<34} {r['n']:>5}  "
                    f"{r['first_seen_at']:<28} {r['last_seen_at']:<28}"
                )

        print("\nSynthetic data by skin (never used for real training):")
        srows = store.synthetic_summary_by_skin()
        if not srows:
            print("  (none)")
        else:
            for r in srows:
                print(f"  {r['skin_name']:<34} {r['n']:>5}")
    finally:
        store.close()


if __name__ == "__main__":
    main()
