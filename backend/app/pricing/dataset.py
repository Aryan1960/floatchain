"""Turns PricingStore rows into arrays ready for scikit-learn / XGBoost.

`load_real_dataset` is the only loader the live prediction service (service.py)
is allowed to call. `load_synthetic_dataset` exists purely so tests can
exercise the same fitting code before real data has accumulated -- it reads
a structurally separate table and must never be wired into anything that
backs a real prediction, matching the point of keeping the two tables apart
in the first place.

Prices stay in cents throughout the pipeline (matching pricing_store); only
the API router converts to dollars at the edge, same convention as
csfloat_client.py.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.data.pricing_store import PricingStore


@dataclass(frozen=True)
class Dataset:
    floats: np.ndarray
    prices_cents: np.ndarray

    def __len__(self) -> int:
        return len(self.floats)


def _to_dataset(points: list[tuple[float, int]]) -> Dataset:
    floats = np.array([p[0] for p in points], dtype=float)
    prices = np.array([p[1] for p in points], dtype=float)
    return Dataset(floats, prices)


def load_real_dataset(store: PricingStore, skin_name: str, stattrak: bool) -> Dataset:
    return _to_dataset(store.real_points_for_skin(skin_name, stattrak))


def load_synthetic_dataset(store: PricingStore, skin_name: str, stattrak: bool) -> Dataset:
    """Test/demo only -- see module docstring."""
    return _to_dataset(store.synthetic_points_for_skin(skin_name, stattrak))
