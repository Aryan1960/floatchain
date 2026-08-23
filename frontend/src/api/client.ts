import type {
  ApiError,
  ContractRequest,
  ContractResult,
  CurveData,
  DealsResponse,
  EvalHistoryRow,
  PricePrediction,
  PricingStatus,
  SkinCatalogEntry,
} from "../types";

async function handle<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as ApiError | null;
    throw new Error(body?.detail ?? `Request failed with status ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function searchSkins(query: string, limit = 15): Promise<SkinCatalogEntry[]> {
  if (!query.trim()) return [];
  const params = new URLSearchParams({ q: query, limit: String(limit) });
  const response = await fetch(`/api/skins/search?${params}`);
  return handle<SkinCatalogEntry[]>(response);
}

export async function evaluateContract(request: ContractRequest): Promise<ContractResult> {
  const response = await fetch("/api/contracts/evaluate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  return handle<ContractResult>(response);
}

export async function getSkinPrice(
  skinName: string,
  rawFloat: number,
  stattrak: boolean
): Promise<number | null> {
  const params = new URLSearchParams({
    skin_name: skinName,
    raw_float: String(rawFloat),
    stattrak: String(stattrak),
  });
  const response = await fetch(`/api/skins/price?${params}`);
  if (!response.ok) return null;
  const body = (await response.json()) as { price: number | null };
  return body.price;
}

export async function getPricingStatus(): Promise<PricingStatus> {
  const response = await fetch("/api/pricing/status");
  return handle<PricingStatus>(response);
}

export async function getPricePrediction(
  skinName: string,
  rawFloat: number,
  stattrak: boolean
): Promise<PricePrediction> {
  const params = new URLSearchParams({
    skin_name: skinName,
    raw_float: String(rawFloat),
    stattrak: String(stattrak),
  });
  const response = await fetch(`/api/pricing/predict?${params}`);
  return handle<PricePrediction>(response);
}

export async function getEvalHistory(): Promise<EvalHistoryRow[]> {
  const response = await fetch("/api/pricing/eval-history");
  return handle<EvalHistoryRow[]>(response);
}

export async function getCurveData(skinName: string, stattrak: boolean): Promise<CurveData> {
  const params = new URLSearchParams({ skin_name: skinName, stattrak: String(stattrak) });
  const response = await fetch(`/api/pricing/curve?${params}`);
  return handle<CurveData>(response);
}

export async function getDeals(): Promise<DealsResponse> {
  const response = await fetch("/api/pricing/deals");
  return handle<DealsResponse>(response);
}
