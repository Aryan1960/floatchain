"""Dataclasses for the Phase 3 chain-search result -- a tree of "sell now vs.
feed into another contract" decisions rooted at one real contract's outcome
distribution.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ChainBranch:
    skin_name: str
    collection: str
    probability: float
    output_float: float
    wear: str
    sell_price: float | None
    price_source: str  # "model" | "local" | "live" | "none"

    # Populated only when this branch was actually explored (see
    # `explored`/`leaf_reason` below) -- the hypothetical next-level
    # contract's own numbers, treating `inputs_required(tier)` identical
    # copies of this branch's output (at this branch's float) as its inputs.
    child_contract_cost: float | None
    child_contract_sell_ev: float | None
    child_contract_chain_ev: float | None

    node_value: float  # leaf: sell_price or 0. explored: max(sell_price, child_contract_chain_ev / required_inputs) -- the PER-UNIT share, not the raw child EV (see search.py's _resolve_branch)
    chosen_action: Literal["sell", "chain"]
    explored: bool
    # Set whenever `explored` is False, or a decision couldn't be computed:
    # "beam_pruned" | "unpriceable" | "top_of_tier" | "max_depth_reached" |
    # "catalog_gap" | None (a real, computed sell-vs-chain decision).
    leaf_reason: str | None
    children: tuple["ChainBranch", ...]


@dataclass(frozen=True)
class ChainSearchResult:
    root_total_cost: float
    single_contract_ev: float  # the "just sell everything from the root" baseline
    chain_ev: float
    chain_ev_delta: float
    chain_ev_delta_pct: float
    chain_beats_single: bool
    materiality_threshold_pct: float
    beam_width: int
    max_depth: int
    root_branches: tuple[ChainBranch, ...]
