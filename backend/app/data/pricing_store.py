"""Local persistence for Phase 2's float/price training data.

Two tables, kept structurally separate rather than distinguished by a flag
column, so a query can never accidentally blend them:

- `real_snapshots`: live CSFloat listings, deduped by listing_id. Each row
  keeps the raw ask (`price_cents`) as the training target, and CSFloat's own
  `reference.predicted_price` (`predicted_price_cents`) purely as a benchmark
  to compare our model against later -- never as something we train on,
  since fitting to their model's output would just reproduce their model.
- `synthetic_snapshots`: locally-generated placeholder data used to exercise
  the ML pipeline before real data has accumulated. Never read by anything
  that trains a "real" model.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

# How close a stored real listing's float needs to be to trust it as a
# stand-in for a live price lookup -- tight enough to be a meaningful
# estimate, loose enough to actually hit given real data isn't uniformly
# spread across a skin's whole float range. Lives here (not in a router)
# since it's genuinely about how much the stored data should be trusted,
# and more than one caller (the /api/skins/price endpoint, the chain-search
# pricing oracle) needs the same threshold.
LOCAL_PRICE_MAX_FLOAT_DISTANCE = 0.02


class PricingStore:
    def __init__(self, db_path: str | Path):
        # check_same_thread defaults to True and every method here assumes
        # it: safe today because every caller (including the chain search's
        # asyncio.gather fan-out) runs cooperatively on the single event-loop
        # thread. Moving any of these calls behind run_in_executor/a thread
        # pool would immediately need check_same_thread=False plus a lock.
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "PricingStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS real_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                listing_id TEXT NOT NULL UNIQUE,
                market_hash_name TEXT NOT NULL,
                skin_name TEXT NOT NULL,
                stattrak INTEGER NOT NULL,
                float_value REAL,
                price_cents INTEGER NOT NULL,
                predicted_price_cents INTEGER,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                times_seen INTEGER NOT NULL DEFAULT 1
            );
            CREATE INDEX IF NOT EXISTS idx_real_skin
                ON real_snapshots(skin_name, stattrak);

            CREATE TABLE IF NOT EXISTS synthetic_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                skin_name TEXT NOT NULL,
                stattrak INTEGER NOT NULL,
                float_value REAL NOT NULL,
                price_cents INTEGER NOT NULL,
                generated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_synth_skin
                ON synthetic_snapshots(skin_name, stattrak);

            CREATE TABLE IF NOT EXISTS bandit_arm_stats (
                skin_name TEXT PRIMARY KEY,
                times_checked INTEGER NOT NULL DEFAULT 0,
                times_confirmed INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        self._conn.commit()

    def upsert_real_snapshot(
        self,
        *,
        listing_id: str,
        market_hash_name: str,
        skin_name: str,
        stattrak: bool,
        float_value: float | None,
        price_cents: int,
        predicted_price_cents: int | None,
        seen_at: str,
    ) -> bool:
        """Returns True if this listing_id was new, False if it already
        existed (in which case only last_seen_at/times_seen/price are
        refreshed -- the row's identity and first_seen_at are untouched)."""
        cur = self._conn.execute(
            "SELECT id FROM real_snapshots WHERE listing_id = ?", (listing_id,)
        )
        existing = cur.fetchone()
        if existing is None:
            self._conn.execute(
                """
                INSERT INTO real_snapshots (
                    listing_id, market_hash_name, skin_name, stattrak,
                    float_value, price_cents, predicted_price_cents,
                    first_seen_at, last_seen_at, times_seen
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    listing_id,
                    market_hash_name,
                    skin_name,
                    int(stattrak),
                    float_value,
                    price_cents,
                    predicted_price_cents,
                    seen_at,
                    seen_at,
                ),
            )
            self._conn.commit()
            return True

        self._conn.execute(
            """
            UPDATE real_snapshots
            SET last_seen_at = ?, times_seen = times_seen + 1,
                price_cents = ?, predicted_price_cents = ?
            WHERE listing_id = ?
            """,
            (seen_at, price_cents, predicted_price_cents, listing_id),
        )
        self._conn.commit()
        return False

    def insert_synthetic(
        self,
        *,
        skin_name: str,
        stattrak: bool,
        float_value: float,
        price_cents: int,
        generated_at: str,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO synthetic_snapshots (
                skin_name, stattrak, float_value, price_cents, generated_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (skin_name, int(stattrak), float_value, price_cents, generated_at),
        )
        self._conn.commit()

    def clear_synthetic(self) -> None:
        self._conn.execute("DELETE FROM synthetic_snapshots")
        self._conn.commit()

    def real_summary_by_skin(self) -> list[sqlite3.Row]:
        """One row per (skin_name, stattrak): count, first/last seen."""
        cur = self._conn.execute(
            """
            SELECT skin_name, stattrak, COUNT(*) AS n,
                   MIN(first_seen_at) AS first_seen_at,
                   MAX(last_seen_at) AS last_seen_at
            FROM real_snapshots
            GROUP BY skin_name, stattrak
            ORDER BY n DESC
            """
        )
        return cur.fetchall()

    def synthetic_summary_by_skin(self) -> list[sqlite3.Row]:
        cur = self._conn.execute(
            """
            SELECT skin_name, stattrak, COUNT(*) AS n
            FROM synthetic_snapshots
            GROUP BY skin_name, stattrak
            ORDER BY n DESC
            """
        )
        return cur.fetchall()

    def real_points_for_skin(
        self, skin_name: str, stattrak: bool
    ) -> list[tuple[float, int]]:
        """(float_value, price_cents) pairs for training -- real data only."""
        cur = self._conn.execute(
            """
            SELECT float_value, price_cents FROM real_snapshots
            WHERE skin_name = ? AND stattrak = ? AND float_value IS NOT NULL
            """,
            (skin_name, int(stattrak)),
        )
        return [(row["float_value"], row["price_cents"]) for row in cur.fetchall()]

    def real_points_with_benchmark(
        self, skin_name: str, stattrak: bool
    ) -> list[tuple[float, int, int | None]]:
        """(float_value, price_cents, predicted_price_cents) triples -- used
        only by the offline evaluation script, so it can compare our model
        against CSFloat's own benchmark on the exact same held-out points,
        not a different nearest-neighbor lookup."""
        cur = self._conn.execute(
            """
            SELECT float_value, price_cents, predicted_price_cents FROM real_snapshots
            WHERE skin_name = ? AND stattrak = ? AND float_value IS NOT NULL
            """,
            (skin_name, int(stattrak)),
        )
        return [
            (row["float_value"], row["price_cents"], row["predicted_price_cents"])
            for row in cur.fetchall()
        ]

    def real_points_with_metadata(
        self, skin_name: str, stattrak: bool
    ) -> list[tuple[float, int, str, str]]:
        """(float_value, price_cents, last_seen_at, listing_id) tuples --
        unlike real_points_for_skin/real_points_with_benchmark, this carries
        enough identity to tell which specific listing a point came from and
        how fresh it is. Needed by app/pricing/deal_radar.py to filter out
        stale rows and know what to spot-check; no existing caller needs
        listing_id/last_seen_at, so this is additive, not a replacement."""
        cur = self._conn.execute(
            """
            SELECT float_value, price_cents, last_seen_at, listing_id FROM real_snapshots
            WHERE skin_name = ? AND stattrak = ? AND float_value IS NOT NULL
            """,
            (skin_name, int(stattrak)),
        )
        return [
            (row["float_value"], row["price_cents"], row["last_seen_at"], row["listing_id"])
            for row in cur.fetchall()
        ]

    def synthetic_points_for_skin(
        self, skin_name: str, stattrak: bool
    ) -> list[tuple[float, int]]:
        """Test/demo only -- see app/pricing/dataset.py's module docstring for
        why this must never back a live prediction."""
        cur = self._conn.execute(
            """
            SELECT float_value, price_cents FROM synthetic_snapshots
            WHERE skin_name = ? AND stattrak = ?
            """,
            (skin_name, int(stattrak)),
        )
        return [(row["float_value"], row["price_cents"]) for row in cur.fetchall()]

    def nearest_real_snapshot(
        self, skin_name: str, stattrak: bool, target_float: float
    ) -> sqlite3.Row | None:
        """Whichever stored real listing has float closest to target_float,
        used to pull a CSFloat predicted_price benchmark from data we already
        have locally, without an extra live API call."""
        cur = self._conn.execute(
            """
            SELECT * FROM real_snapshots
            WHERE skin_name = ? AND stattrak = ? AND float_value IS NOT NULL
            """,
            (skin_name, int(stattrak)),
        )
        rows = cur.fetchall()
        if not rows:
            return None
        return min(rows, key=lambda r: abs(r["float_value"] - target_float))

    def total_counts(self) -> tuple[int, int]:
        """(real_row_count, synthetic_row_count)."""
        with closing(self._conn.execute("SELECT COUNT(*) FROM real_snapshots")) as c:
            real = c.fetchone()[0]
        with closing(
            self._conn.execute("SELECT COUNT(*) FROM synthetic_snapshots")
        ) as c:
            synthetic = c.fetchone()[0]
        return real, synthetic

    def get_bandit_stats(self) -> dict[str, tuple[int, int]]:
        """skin_name -> (times_checked, times_confirmed), for the deal
        radar's per-skin verification-budget bandit (app/pricing/
        deal_radar.py). A skin absent from the result has never been
        checked -- callers should treat that as "try it first", not as a
        confirmed-bad track record."""
        cur = self._conn.execute("SELECT skin_name, times_checked, times_confirmed FROM bandit_arm_stats")
        return {row["skin_name"]: (row["times_checked"], row["times_confirmed"]) for row in cur.fetchall()}

    def record_bandit_outcome(self, skin_name: str, confirmed: bool) -> None:
        """Call once per live verification actually spent on this skin --
        not once per scan, since unverified candidates don't produce a real
        outcome to learn from."""
        self._conn.execute(
            """
            INSERT INTO bandit_arm_stats (skin_name, times_checked, times_confirmed)
            VALUES (?, 1, ?)
            ON CONFLICT(skin_name) DO UPDATE SET
                times_checked = times_checked + 1,
                times_confirmed = times_confirmed + excluded.times_confirmed
            """,
            (skin_name, int(confirmed)),
        )
        self._conn.commit()
