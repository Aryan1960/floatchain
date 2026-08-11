"""Monte Carlo simulation over an exact outcome probability distribution.

The output float (and therefore the exact probability table) for a fixed set
of 10 inputs is deterministic — there's nothing to sample there. What's
random is *which* eligible output skin you actually get. This samples that
draw repeatedly to show the realistic spread of results, not just the EV
average.
"""

from __future__ import annotations

import random
import statistics

from app.domain.models import MonteCarloSummary

# Within this fraction of total cost, a run counts as "breakeven" rather than
# a clean win/loss.
BREAKEVEN_BAND = 0.01


def run_monte_carlo(
    outcomes: list[tuple[float, float | None]],
    total_cost: float,
    runs: int = 1000,
    seed: int | None = None,
) -> MonteCarloSummary:
    """outcomes: [(probability, price_or_none), ...], probabilities sum to 1."""
    if not outcomes:
        raise ValueError("need at least one outcome to simulate")
    if runs <= 0:
        raise ValueError("runs must be positive")

    rng = random.Random(seed)
    prices = [price if price is not None else 0.0 for _, price in outcomes]
    weights = [prob for prob, _ in outcomes]

    sampled_prices = rng.choices(prices, weights=weights, k=runs)
    net_profits = [price - total_cost for price in sampled_prices]

    band = BREAKEVEN_BAND * total_cost
    profit_count = sum(1 for p in net_profits if p > band)
    loss_count = sum(1 for p in net_profits if p < -band)
    breakeven_count = runs - profit_count - loss_count

    return MonteCarloSummary(
        runs=runs,
        profit_rate=profit_count / runs,
        breakeven_rate=breakeven_count / runs,
        loss_rate=loss_count / runs,
        average_net_profit=statistics.fmean(net_profits),
        median_net_profit=statistics.median(net_profits),
    )
