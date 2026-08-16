"""KNN fallback for sparse-data skins (below XGBoost's minimum point
threshold -- knives/gloves and low-liquidity skins per the roadmap).
Predicted price is just a distance-weighted average of the k closest-float
points actually seen, which needs far fewer examples than a tree ensemble
to produce a sane (if wide-uncertainty) number.
"""

from __future__ import annotations

import numpy as np
from sklearn.neighbors import KNeighborsRegressor

MIN_POINTS_FOR_KNN = 5
DEFAULT_K = 5


class KnnModel:
    def __init__(self, k: int = DEFAULT_K) -> None:
        self._k = k
        self._model: KNeighborsRegressor | None = None
        self._fitted = False

    def fit(self, floats: np.ndarray, prices_cents: np.ndarray) -> None:
        if len(floats) < 1:
            raise ValueError("need at least one point to fit KNN")
        k = min(self._k, len(floats))
        self._model = KNeighborsRegressor(n_neighbors=k, weights="distance")
        self._model.fit(floats.reshape(-1, 1), prices_cents)
        self._fitted = True

    def predict(self, float_value: float) -> float:
        if not self._fitted or self._model is None:
            raise RuntimeError("KnnModel.fit() must be called before predict()")
        return float(self._model.predict(np.array([[float_value]]))[0])
