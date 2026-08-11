import { useEffect, useRef, useState } from "react";
import { SkinAutocomplete } from "./SkinAutocomplete";
import { FloatInput } from "./FloatInput";
import { getSkinPrice } from "../api/client";
import type { SkinCatalogEntry } from "../types";

export interface RowState {
  skinName: string;
  skin: SkinCatalogEntry | null;
  rawFloat: string;
  price: string;
}

export function emptyRow(): RowState {
  return { skinName: "", skin: null, rawFloat: "", price: "" };
}

interface Props {
  index: number;
  row: RowState;
  stattrak: boolean;
  requiredRarityId: string | null;
  hasNextRow: boolean;
  onChange: (patch: Partial<RowState>) => void;
  onCopyToEmpty: () => void;
  onCopyToNext: () => void;
}

export function ContractRow({
  index,
  row,
  stattrak,
  requiredRarityId,
  hasNextRow,
  onChange,
  onCopyToEmpty,
  onCopyToNext,
}: Props) {
  const [marketPrice, setMarketPrice] = useState<number | null>(null);
  const [priceLoading, setPriceLoading] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    setMarketPrice(null);
    const floatNum = Number(row.rawFloat);
    const skin = row.skin;
    if (!skin || row.rawFloat.trim() === "" || Number.isNaN(floatNum)) return;
    if (floatNum < skin.min_float || floatNum > skin.max_float) return;

    if (debounceRef.current) clearTimeout(debounceRef.current);
    setPriceLoading(true);
    debounceRef.current = setTimeout(async () => {
      try {
        const price = await getSkinPrice(skin.name, floatNum, stattrak);
        setMarketPrice(price);
        if (price !== null && row.price.trim() === "") {
          onChange({ price: price.toFixed(2) });
        }
      } finally {
        setPriceLoading(false);
      }
    }, 400);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [row.skin, row.rawFloat, stattrak]);

  const filled = Boolean(row.skinName.trim() && row.rawFloat.trim() && row.price.trim());

  return (
    <div className="contract-row">
      <div className="row-index">{index + 1}</div>

      <div className="row-field row-field-skin">
        <SkinAutocomplete
          value={row.skinName}
          requiredRarityId={requiredRarityId}
          onChange={(name, skin) => onChange({ skinName: name, skin, rawFloat: "", price: "" })}
        />
        {row.skin && (
          <div className="collection-hint">
            {row.skin.collections.length > 0
              ? `From: ${row.skin.collections.join(", ")}`
              : "No collection data for this skin"}
          </div>
        )}
      </div>

      <div className="row-field row-field-float">
        <FloatInput
          value={row.rawFloat}
          min={row.skin?.min_float ?? 0}
          max={row.skin?.max_float ?? 1}
          disabled={!row.skin}
          onChange={(value) => onChange({ rawFloat: value })}
        />
      </div>

      <div className="row-field row-field-price">
        <input
          type="number"
          step="0.01"
          min={0}
          value={row.price}
          placeholder="price paid ($)"
          onChange={(e) => onChange({ price: e.target.value })}
        />
        {priceLoading && <span className="price-hint">checking market…</span>}
        {!priceLoading && marketPrice !== null && (
          <button
            type="button"
            className="price-hint price-hint-button"
            onClick={() => onChange({ price: marketPrice.toFixed(2) })}
          >
            Market: ${marketPrice.toFixed(2)} (use)
          </button>
        )}
        {!priceLoading && marketPrice === null && row.skin && row.rawFloat.trim() && (
          <span className="price-hint">no live listings found</span>
        )}
      </div>

      <div className="row-field row-field-copy">
        <button
          type="button"
          disabled={!filled}
          onClick={onCopyToEmpty}
          title="Copy this skin, float, and price into every still-empty row"
        >
          Copy to all
        </button>
        <button
          type="button"
          className="tiny"
          disabled={!filled || !hasNextRow}
          onClick={onCopyToNext}
          title="Copy into just the next row below, overwriting whatever's there"
        >
          Copy ↓
        </button>
      </div>
    </div>
  );
}
