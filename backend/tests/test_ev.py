import pytest

from app.domain.ev import expected_value, profitability_pct


def test_expected_value_is_probability_weighted_sum():
    outcomes = [(0.5, 10.0), (0.5, 20.0)]
    assert expected_value(outcomes) == pytest.approx(15.0)


def test_expected_value_treats_unpriced_outcomes_as_zero():
    outcomes = [(0.5, 10.0), (0.5, None)]
    assert expected_value(outcomes) == pytest.approx(5.0)


def test_profitability_pct():
    assert profitability_pct(ev=115.0, total_cost=100.0) == pytest.approx(115.0)
    assert profitability_pct(ev=80.0, total_cost=100.0) == pytest.approx(80.0)


def test_profitability_pct_rejects_nonpositive_cost():
    with pytest.raises(ValueError):
        profitability_pct(ev=10.0, total_cost=0.0)
