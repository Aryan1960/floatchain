import type { ContractResult } from "../types";

function money(value: number | null): string {
  return value === null ? "—" : `$${value.toFixed(2)}`;
}

function pct(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

export function ResultsPanel({ result }: { result: ContractResult }) {
  const outcomes = [...result.outcomes].sort((a, b) => b.probability - a.probability);
  const profitable = result.profitability_pct >= 100;

  return (
    <div className="results-panel">
      <div className={`hero-readout ${profitable ? "positive" : "negative"}`}>
        <span className="hero-readout__label">Profitability</span>
        <span className="hero-readout__value">{result.profitability_pct.toFixed(1)}%</span>
        <span className="hero-readout__note">
          {profitable ? "Expected value clears total input cost" : "Expected value falls short of total input cost"}
        </span>
      </div>

      <div className="stat-strip">
        <div className="stat-strip__item">
          <span className="label">Total input cost</span>
          <span className="value">{money(result.total_input_cost)}</span>
        </div>
        <div className="stat-strip__item">
          <span className="label">Expected value</span>
          <span className="value">{money(result.expected_value)}</span>
        </div>
        <div className="stat-strip__item">
          <span className="label">Avg adjusted float</span>
          <span className="value">{result.avg_adjusted_float.toFixed(5)}</span>
        </div>
      </div>

      <h3>Monte Carlo ({result.monte_carlo.runs.toLocaleString()} runs)</h3>
      <div className="monte-carlo-bar">
        <div
          className="segment profit"
          style={{ width: pct(result.monte_carlo.profit_rate) }}
          title={`Profit: ${pct(result.monte_carlo.profit_rate)}`}
        />
        <div
          className="segment breakeven"
          style={{ width: pct(result.monte_carlo.breakeven_rate) }}
          title={`Breakeven: ${pct(result.monte_carlo.breakeven_rate)}`}
        />
        <div
          className="segment loss"
          style={{ width: pct(result.monte_carlo.loss_rate) }}
          title={`Loss: ${pct(result.monte_carlo.loss_rate)}`}
        />
      </div>
      <p className="monte-carlo-legend">
        <span className="dot profit" /> {pct(result.monte_carlo.profit_rate)} profit
        &nbsp;&nbsp;
        <span className="dot breakeven" /> {pct(result.monte_carlo.breakeven_rate)} breakeven
        &nbsp;&nbsp;
        <span className="dot loss" /> {pct(result.monte_carlo.loss_rate)} loss
      </p>
      <p>
        Average net profit per run: <strong>{money(result.monte_carlo.average_net_profit)}</strong>
        {" "}(median {money(result.monte_carlo.median_net_profit)})
      </p>

      <h3>Possible outcomes</h3>
      <table className="outcomes-table">
        <thead>
          <tr>
            <th>Skin</th>
            <th>Collection</th>
            <th>Probability</th>
            <th>Output float</th>
            <th>Wear</th>
            <th>Price</th>
          </tr>
        </thead>
        <tbody>
          {outcomes.map((outcome) => (
            <tr key={outcome.skin_name}>
              <td>{outcome.skin_name}</td>
              <td>{outcome.collection}</td>
              <td>{pct(outcome.probability)}</td>
              <td>{outcome.output_float.toFixed(5)}</td>
              <td>{outcome.wear}</td>
              <td>{outcome.price === null ? <em>no listings</em> : money(outcome.price)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
