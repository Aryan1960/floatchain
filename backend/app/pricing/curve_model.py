"""Per-skin XGBoost price-vs-float curve for liquid skins with enough data.

Roadmap intuition: fit a dumb first tree (predict the mean for everyone),
compute residuals, fit a small tree to those residuals, add it in scaled by
the learning rate, repeat. XGBRegressor does that loop; we just call
.fit()/.predict(). Below MIN_POINTS_FOR_XGBOOST, a handful of trees with
this few examples memorizes noise rather than learning the curve's shape --
that's the threshold knn_fallback.py exists for.

Fits log(price), not raw price. Real listing prices are right-skewed --
a handful of very high asks pull the mean well above the typical case --
and a squared-error loss (what XGBoost minimizes by default) gets
disproportionately pulled toward those high values, biasing predictions
upward. Fitting in log-space makes the loss care about proportional
differences instead of absolute dollar differences, which matches how
price differences actually feel (being off by $50 matters a lot on a $60
skin, barely at all on a $3000 one) and stops a few expensive outliers
from dragging every prediction up.
"""

from __future__ import annotations

import json

import numpy as np
from xgboost import XGBRegressor

MIN_POINTS_FOR_XGBOOST = 50


def _convert_node(node: dict) -> dict:
    """XGBoost's raw JSON dump -> {"type": "leaf", "value": ...} or
    {"type": "split", "threshold": ..., "left": ..., "right": ...}. Only
    ever one feature ("f0", the float) here, so the split's own feature
    name carries no information worth keeping. "left" is the yes-branch
    (float < threshold), "right" the no-branch, matching how the curve
    chart already reads left-to-right along increasing float."""
    if "leaf" in node:
        return {"type": "leaf", "value": node["leaf"]}

    children = {child["nodeid"]: child for child in node["children"]}
    return {
        "type": "split",
        "threshold": node["split_condition"],
        "left": _convert_node(children[node["yes"]]),
        "right": _convert_node(children[node["no"]]),
    }


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
        self._model.fit(floats.reshape(-1, 1), np.log1p(prices_cents))
        self._fitted = True

    def predict(self, float_value: float) -> float:
        if not self._fitted:
            raise RuntimeError("CurveModel.fit() must be called before predict()")
        log_price = self._model.predict(np.array([[float_value]]))[0]
        return float(np.expm1(log_price))

    def get_first_tree(self) -> dict | None:
        """The first tree in the ensemble (tree 0), as a clean recursive
        dict -- meaningful to look at on its own in a way later trees
        aren't, since it's fit close to the raw signal while every tree
        after it is patching an ever-smaller, ever-noisier residual. Leaf
        values are the model's raw log-space contribution (it fits
        log1p(price), see the module docstring) -- NOT a dollar amount on
        their own; only the sum across all n_estimators trees, run back
        through expm1, is an actual price. Returns None before fit()."""
        if not self._fitted:
            return None
        dump = self._model.get_booster().get_dump(dump_format="json")
        raw = json.loads(dump[0])
        return _convert_node(raw)

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
