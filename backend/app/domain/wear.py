"""Wear-condition classification from a raw float value."""

from __future__ import annotations

_WEAR_THRESHOLDS: list[tuple[float, str]] = [
    (0.07, "Factory New"),
    (0.15, "Minimal Wear"),
    (0.38, "Field-Tested"),
    (0.45, "Well-Worn"),
    (1.00, "Battle-Scarred"),
]


def wear_name(float_value: float) -> str:
    for upper_bound, name in _WEAR_THRESHOLDS:
        if float_value < upper_bound:
            return name
    return "Battle-Scarred"
