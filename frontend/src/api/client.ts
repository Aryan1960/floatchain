import type { ApiError, ContractRequest, ContractResult, SkinCatalogEntry } from "../types";

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
