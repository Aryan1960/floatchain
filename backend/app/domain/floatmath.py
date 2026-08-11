"""Post-2026 trade-up float formula.

Each input's raw float is normalized ("adjusted") to its own skin's float-cap
range, the adjusted values are averaged, and the average is rescaled into the
output skin's float-cap range. See docus/cs2_tradeup_contracts_2026_guide.md
section 4 for the worked example this is checked against in tests.
"""

from __future__ import annotations


def adjusted_float(raw_float: float, min_float: float, max_float: float) -> float:
    if max_float <= min_float:
        raise ValueError("max_float must be greater than min_float")
    if not (min_float <= raw_float <= max_float):
        raise ValueError(
            f"raw_float {raw_float} is outside this skin's float range "
            f"[{min_float}, {max_float}]"
        )
    return (raw_float - min_float) / (max_float - min_float)


def average_adjusted_float(adjusted_floats: list[float]) -> float:
    if not adjusted_floats:
        raise ValueError("need at least one adjusted float to average")
    return sum(adjusted_floats) / len(adjusted_floats)


def output_float(avg_adjusted: float, out_min_float: float, out_max_float: float) -> float:
    return out_min_float + avg_adjusted * (out_max_float - out_min_float)


def compute_output_float(
    input_raw_floats: list[float],
    input_ranges: list[tuple[float, float]],
    output_range: tuple[float, float],
) -> float:
    """Convenience wrapper: raw input floats + their ranges -> output float."""
    if len(input_raw_floats) != len(input_ranges):
        raise ValueError("input_raw_floats and input_ranges must be the same length")
    adjusted = [
        adjusted_float(raw, lo, hi)
        for raw, (lo, hi) in zip(input_raw_floats, input_ranges)
    ]
    avg = average_adjusted_float(adjusted)
    out_min, out_max = output_range
    return output_float(avg, out_min, out_max)
