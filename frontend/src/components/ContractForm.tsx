import { useState } from "react";
import { ContractRow, emptyRow, type RowState } from "./ContractRow";
import type { ContractInput } from "../types";

interface Props {
  onSubmit: (inputs: ContractInput[], monteCarloRuns: number) => void;
  submitting: boolean;
}

export function ContractForm({ onSubmit, submitting }: Props) {
  const [contractType, setContractType] = useState<"normal" | "covert-to-gold">("normal");
  const [stattrak, setStattrak] = useState(false);
  const [monteCarloRuns, setMonteCarloRuns] = useState(1000);
  const requiredCount = contractType === "covert-to-gold" ? 5 : 10;
  const [rows, setRows] = useState<RowState[]>(() => Array.from({ length: 10 }, emptyRow));

  function setRowCount(count: number) {
    setRows((prev) => {
      if (count <= prev.length) return prev.slice(0, count);
      return [...prev, ...Array.from({ length: count - prev.length }, emptyRow)];
    });
  }

  function handleContractTypeChange(type: "normal" | "covert-to-gold") {
    setContractType(type);
    setRowCount(type === "covert-to-gold" ? 5 : 10);
  }

  function updateRow(index: number, patch: Partial<RowState>) {
    setRows((prev) => prev.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  }

  function copyToEmptyRows(sourceIndex: number) {
    setRows((prev) => {
      const source = prev[sourceIndex];
      return prev.map((row, i) =>
        i === sourceIndex || row.skinName.trim()
          ? row
          : { ...source }
      );
    });
  }

  function copyToNextRow(sourceIndex: number) {
    const targetIndex = sourceIndex + 1;
    if (targetIndex >= requiredCount) return;
    setRows((prev) =>
      prev.map((row, i) => (i === targetIndex ? { ...prev[sourceIndex] } : row))
    );
  }

  function handleReset() {
    setContractType("normal");
    setStattrak(false);
    setMonteCarloRuns(1000);
    setRows(Array.from({ length: 10 }, emptyRow));
  }

  const visibleRows = rows.slice(0, requiredCount);
  const allFilled = visibleRows.every((row) => {
    if (!row.skinName.trim() || !row.rawFloat.trim() || !row.price.trim()) return false;
    const floatNum = Number(row.rawFloat);
    if (row.skin && (floatNum < row.skin.min_float || floatNum > row.skin.max_float)) {
      return false;
    }
    return true;
  });
  const filledCount = visibleRows.filter((row) => row.skinName.trim()).length;
  const runningTotal = visibleRows.reduce((sum, row) => {
    const price = Number(row.price);
    return row.price.trim() && !Number.isNaN(price) ? sum + price : sum;
  }, 0);
  // All contract inputs must share one rarity tier — lock to whichever
  // row was filled in first, so later rows' autocomplete can guard against
  // picking a mismatched skin instead of failing only after submit.
  const lockedRarityId = rows.find((row) => row.skin)?.skin?.rarity_id ?? null;

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const inputs: ContractInput[] = visibleRows.map((row) => ({
      skin_name: row.skinName,
      raw_float: Number(row.rawFloat),
      price: Number(row.price),
      stattrak,
    }));
    onSubmit(inputs, monteCarloRuns);
  }

  return (
    <form onSubmit={handleSubmit} className="contract-form">
      <div className="form-controls">
        <label>
          Contract type
          <select
            value={contractType}
            onChange={(e) => handleContractTypeChange(e.target.value as "normal" | "covert-to-gold")}
          >
            <option value="normal">Normal (10 inputs)</option>
            <option value="covert-to-gold">Covert → Gold (5 inputs)</option>
          </select>
        </label>
        <label>
          <input
            type="checkbox"
            checked={stattrak}
            onChange={(e) => setStattrak(e.target.checked)}
          />
          StatTrak™ inputs
        </label>
        <label>
          Monte Carlo runs
          <input
            type="number"
            min={100}
            max={20000}
            value={monteCarloRuns}
            onChange={(e) => setMonteCarloRuns(Number(e.target.value))}
          />
        </label>
      </div>

      <div className="contract-rows-header">
        <span />
        <span>Skin</span>
        <span>Float</span>
        <span>Price paid</span>
        <span />
      </div>

      <div className="contract-rows">
        {visibleRows.map((row, i) => (
          <ContractRow
            key={i}
            index={i}
            row={row}
            stattrak={stattrak}
            requiredRarityId={lockedRarityId}
            hasNextRow={i + 1 < requiredCount}
            onChange={(patch) => updateRow(i, patch)}
            onCopyToEmpty={() => copyToEmptyRows(i)}
            onCopyToNext={() => copyToNextRow(i)}
          />
        ))}
      </div>

      <div className="form-footer">
        <div className="running-total">
          <span className="label">Total so far</span>
          <span className="value">
            ${runningTotal.toFixed(2)} <span className="muted">({filledCount}/{requiredCount} filled)</span>
          </span>
        </div>
        <div className="form-footer-actions">
          <button type="button" className="secondary" onClick={handleReset}>
            Reset
          </button>
          <button type="submit" disabled={!allFilled || submitting}>
            {submitting ? "Evaluating..." : "Evaluate contract"}
          </button>
        </div>
      </div>
    </form>
  );
}
