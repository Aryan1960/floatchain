import { useState } from "react";
import { ContractForm } from "./components/ContractForm";
import { PricingInsights } from "./components/PricingInsights";
import { ResultsPanel } from "./components/ResultsPanel";
import { evaluateContract } from "./api/client";
import type { ContractInput, ContractResult } from "./types";
import "./App.css";

type Tab = "calculator" | "pricing-insights";

export default function App() {
  const [tab, setTab] = useState<Tab>("calculator");
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
        <p className="header-eyebrow">CS2 Trade-Up Engine</p>
        <h1>FloatChain</h1>
        <p className="subtitle">Float math, expected value, Monte Carlo simulation, and a real-data pricing model</p>
      </header>

      <nav className="tab-bar">
        <button
          type="button"
          className={tab === "calculator" ? "tab active" : "tab"}
          onClick={() => setTab("calculator")}
        >
          Contract Calculator
        </button>
        <button
          type="button"
          className={tab === "pricing-insights" ? "tab active" : "tab"}
          onClick={() => setTab("pricing-insights")}
        >
          Pricing Insights
        </button>
      </nav>

      {tab === "calculator" ? (
        <>
          <ContractForm onSubmit={handleSubmit} submitting={submitting} />
          {error && <div className="error-banner">{error}</div>}
          {result && <ResultsPanel result={result} />}
        </>
      ) : (
        <PricingInsights />
      )}
    </div>
  );
}
