"""Domain dataclasses shared across the math engine."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SkinCatalogEntry:
    name: str  # base pattern name, e.g. "AK-47 | Redline"
    rarity_id: str
    min_float: float
    max_float: float
    stattrak: bool
    souvenir: bool
    collections: tuple[str, ...]


@dataclass(frozen=True)
class ContractInput:
    skin_name: str
    raw_float: float
    price: float  # what the user paid, in dollars
    stattrak: bool = False


@dataclass(frozen=True)
class OutcomeProbability:
    """One possible output skin. `output_float`/`wear` are specific to this
    skin's own float-cap range, even though every outcome in a contract
    shares the same `avg_adjusted_float` (see ContractResult)."""

    skin_name: str
    collection: str
    probability: float
    output_float: float
    wear: str
    price: float | None  # None if no price could be found


@dataclass(frozen=True)
class MonteCarloSummary:
    runs: int
    profit_rate: float
    breakeven_rate: float
    loss_rate: float
    average_net_profit: float
    median_net_profit: float


@dataclass(frozen=True)
class ContractResult:
    avg_adjusted_float: float  # shared basis, before rescaling into each output's range
    total_input_cost: float
    outcomes: tuple[OutcomeProbability, ...]
    expected_value: float
    profitability_pct: float  # ev / cost * 100
    monte_carlo: MonteCarloSummary
