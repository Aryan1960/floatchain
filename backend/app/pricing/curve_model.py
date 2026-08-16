"""Per-skin XGBoost price-vs-float curve for liquid skins with enough data.

Roadmap intuition: fit a dumb first tree (predict the mean for everyone),
compute residuals, fit a small tree to those residuals, add it in scaled by
the learning rate, repeat. XGBRegressor does that loop; we just call
.fit()/.predict(). Below MIN_POINTS_FOR_XGBOOST, a handful of trees with
this few examples memorizes noise rather than learning the curve's shape --
that's the threshold knn_fallback.py exists for.
"""

from __future__ import annotations

import numpy as np
from xgboost import XGBRegressor

MIN_POINTS_FOR_XGBOOST = 50


class CurveModel:
    def __init__(self) -> None:
        self._model = XGBRegressor(
            n_estimators=150,
            max_depth=3,
            learning_rate=0.1,
            reg_lambda=1.0,
            random_state=0,
        )
        self._fitted = False

    def fit(self, floats: np.ndarray, prices_cents: np.ndarray) -> None:
        self._model.fit(floats.reshape(-1, 1), prices_cents)
        self._fitted = True

    def predict(self, float_value: float) -> float:
        if not self._fitted:
            raise RuntimeError("CurveModel.fit() must be called before predict()")
        return float(self._model.predict(np.array([[float_value]]))[0])

    def save(self, path: str) -> None:
        import joblib

        joblib.dump(self._model, path)

    @classmethod
    def load(cls, path: str) -> "CurveModel":
        import joblib

        instance = cls()
        instance._model = joblib.load(path)
        instance._fitted = True
        return instance
