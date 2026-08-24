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

export interface PricingStatusSkin {
  skin_name: string;
  stattrak: boolean;
  sample_count: number;
  first_seen_at: string;
  last_seen_at: string;
  min_float: number | null;
  max_float: number | null;
}

export interface PricingStatus {
  collector_paused: boolean;
  real_rows: number;
  synthetic_rows: number;
  by_skin: PricingStatusSkin[];
}

export interface PricePrediction {
  skin_name: string;
  stattrak: boolean;
  float_value: number;
  model_price: number | null;
  model_type: "xgboost" | "knn" | "insufficient_data";
  sample_count: number;
  outliers_removed: number;
  csfloat_predicted_price: number | null;
  csfloat_reference_float_distance: number | null;
  nearest_real_float_distance: number | null;
}

export interface CurvePoint {
  float_value: number;
  price: number;
  csfloat_predicted_price: number | null;
  is_outlier: boolean;
}

export interface CurveSample {
  float_value: number;
  price: number;
}

export type TreeNode =
  | { type: "split"; threshold: number; left: TreeNode; right: TreeNode }
  | { type: "leaf"; value: number };

export interface CurveData {
  skin_name: string;
  stattrak: boolean;
  model_type: "xgboost" | "knn" | "insufficient_data";
  sample_count: number;
  outliers_removed: number;
  points: CurvePoint[];
  curve: CurveSample[];
  first_tree: TreeNode | null;
}

export interface EvalHistoryRow {
  timestamp: string;
  real_rows_total: number;
  evaluated_skins: number;
  beat_baseline: number;
  beat_csfloat: number;
  csfloat_comparable: number;
  avg_our_mae: number | null;
  avg_baseline_mae: number | null;
  avg_csfloat_mae: number | null;
}

export interface DealCandidate {
  skin_name: string;
  float_value: number;
  price: number;
  model_price: number;
  discount_pct: number;
  last_seen_at: string;
  verified_live: boolean;
  sample_count: number;
}

export interface DealsResponse {
  generated_at: string | null;
  candidates: DealCandidate[];
}
