"""Expected value and profitability."""

from __future__ import annotations


def expected_value(priced_outcomes: list[tuple[float, float | None]]) -> float:
    """priced_outcomes: [(probability, price_or_none), ...].

    Unpriced outcomes (price is None, e.g. no live listings found) contribute
    0 to EV rather than being dropped, so EV is always a conservative
    (never-overstated) estimate.
    """
    return sum(prob * price for prob, price in priced_outcomes if price is not None)


def profitability_pct(ev: float, total_cost: float) -> float:
    if total_cost <= 0:
        raise ValueError("total_cost must be positive")
    return (ev / total_cost) * 100
