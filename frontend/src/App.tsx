import { useState } from "react";
import { ContractForm } from "./components/ContractForm";
import { ResultsPanel } from "./components/ResultsPanel";
import { evaluateContract } from "./api/client";
import type { ContractInput, ContractResult } from "./types";
import "./App.css";

export default function App() {
  const [result, setResult] = useState<ContractResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(inputs: ContractInput[], monteCarloRuns: number) {
    setSubmitting(true);
    setError(null);
    try {
      const evaluated = await evaluateContract({ inputs, monte_carlo_runs: monteCarloRuns });
      setResult(evaluated);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
      setResult(null);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="app">
      <header>
        <h1>FloatChain</h1>
        <p className="subtitle">CS2 trade-up float, EV, and Monte Carlo calculator</p>
      </header>

      <ContractForm onSubmit={handleSubmit} submitting={submitting} />

      {error && <div className="error-banner">{error}</div>}
      {result && <ResultsPanel result={result} />}
    </div>
  );
}
