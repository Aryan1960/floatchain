export interface SkinCatalogEntry {
  name: string;
  rarity_id: string;
  min_float: number;
  max_float: number;
  stattrak: boolean;
  souvenir: boolean;
  collections: string[];
}

export interface ContractInput {
  skin_name: string;
  raw_float: number;
  price: number;
  stattrak: boolean;
}

export interface ContractRequest {
  inputs: ContractInput[];
  monte_carlo_runs: number;
}

export interface OutcomeProbability {
  skin_name: string;
  collection: string;
  probability: number;
  output_float: number;
  wear: string;
  price: number | null;
}

export interface MonteCarloSummary {
  runs: number;
  profit_rate: number;
  breakeven_rate: number;
  loss_rate: number;
  average_net_profit: number;
  median_net_profit: number;
}

export interface ContractResult {
  avg_adjusted_float: number;
  total_input_cost: number;
  outcomes: OutcomeProbability[];
  expected_value: number;
  profitability_pct: number;
  monte_carlo: MonteCarloSummary;
}

export interface ApiError {
  detail: string;
}
