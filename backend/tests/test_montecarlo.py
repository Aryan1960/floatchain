import pytest

from app.domain.montecarlo import run_monte_carlo


def test_monte_carlo_deterministic_win_is_always_profit():
    outcomes = [(1.0, 200.0)]  # certain payout of 200, cost 100
    summary = run_monte_carlo(outcomes, total_cost=100.0, runs=500, seed=1)
    assert summary.runs == 500
    assert summary.profit_rate == pytest.approx(1.0)
    assert summary.loss_rate == pytest.approx(0.0)
    assert summary.average_net_profit == pytest.approx(100.0)


def test_monte_carlo_deterministic_loss_is_always_loss():
    outcomes = [(1.0, 5.0)]  # certain payout of 5, cost 100
    summary = run_monte_carlo(outcomes, total_cost=100.0, runs=500, seed=1)
    assert summary.loss_rate == pytest.approx(1.0)
    assert summary.profit_rate == pytest.approx(0.0)


def test_monte_carlo_rates_sum_to_one():
    outcomes = [(0.25, 400.0), (0.75, 5.0)]
    summary = run_monte_carlo(outcomes, total_cost=100.0, runs=2000, seed=42)
    total = summary.profit_rate + summary.breakeven_rate + summary.loss_rate
    assert total == pytest.approx(1.0)


def test_monte_carlo_converges_toward_ev_weighted_average():
    # 25% chance of $400, 75% chance of $1.40, cost $100 -> mirrors the
    # variance example in docus/cs2_chained_tradeup_optimizer_feasibility_report.md
    outcomes = [(0.25, 400.0), (0.75, 1.40)]
    expected_average_payout = 0.25 * 400.0 + 0.75 * 1.40
    summary = run_monte_carlo(outcomes, total_cost=100.0, runs=20000, seed=7)
    assert summary.average_net_profit == pytest.approx(
        expected_average_payout - 100.0, abs=2.0
    )


def test_monte_carlo_rejects_empty_outcomes():
    with pytest.raises(ValueError):
        run_monte_carlo([], total_cost=100.0)
