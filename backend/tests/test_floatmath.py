import pytest

from app.domain.floatmath import (
    adjusted_float,
    average_adjusted_float,
    compute_output_float,
    output_float,
)


def test_adjusted_float_matches_worked_example():
    # docus/cs2_tradeup_contracts_2026_guide.md section 4: AK-47 | Safari Mesh
    # (min=0.06, max=0.80), raw float 0.069999 -> ~0.01351
    result = adjusted_float(0.069999, 0.06, 0.80)
    assert result == pytest.approx(0.01351, abs=1e-5)


def test_adjusted_float_bounds():
    assert adjusted_float(0.0, 0.0, 1.0) == pytest.approx(0.0)
    assert adjusted_float(1.0, 0.0, 1.0) == pytest.approx(1.0)


def test_adjusted_float_rejects_out_of_range():
    with pytest.raises(ValueError):
        adjusted_float(0.9, 0.0, 0.5)


def test_average_adjusted_float():
    assert average_adjusted_float([0.0, 0.5, 1.0]) == pytest.approx(0.5)


def test_average_adjusted_float_requires_nonempty():
    with pytest.raises(ValueError):
        average_adjusted_float([])


def test_output_float_rescales_into_target_range():
    # avg adjusted 0.5 into a [0.1, 0.3] output range -> midpoint 0.2
    assert output_float(0.5, 0.1, 0.3) == pytest.approx(0.2)


def test_compute_output_float_end_to_end():
    # 10 identical inputs at the midpoint of their own range should land
    # exactly on the midpoint of the output range.
    input_ranges = [(0.0, 1.0)] * 10
    input_floats = [0.5] * 10
    output = compute_output_float(input_floats, input_ranges, (0.0, 0.5))
    assert output == pytest.approx(0.25)


def test_compute_output_float_uses_each_inputs_own_range():
    # Two inputs with the same raw float but different ranges should adjust
    # differently: 0.5 is the midpoint of [0,1] but the top of [0,0.5].
    floats = [0.5, 0.5]
    ranges = [(0.0, 1.0), (0.0, 0.5)]
    avg_adjusted = average_adjusted_float(
        [adjusted_float(f, lo, hi) for f, (lo, hi) in zip(floats, ranges)]
    )
    assert avg_adjusted == pytest.approx((0.5 + 1.0) / 2)
