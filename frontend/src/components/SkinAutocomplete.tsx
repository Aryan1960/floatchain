import { useEffect, useRef, useState } from "react";
import { searchSkins } from "../api/client";
import type { SkinCatalogEntry } from "../types";

interface Props {
  value: string;
  onChange: (name: string, skin: SkinCatalogEntry | null) => void;
  /** Once any row has a skin selected, every row locks to that rarity tier —
   * a trade-up contract requires all inputs to share one tier. Matches are
   * still shown but disabled, so the user sees *why* a name they typed
   * isn't selectable instead of it just silently vanishing. */
  requiredRarityId?: string | null;
}

export function SkinAutocomplete({ value, onChange, requiredRarityId = null }: Props) {
  const [suggestions, setSuggestions] = useState<SkinCatalogEntry[]>([]);
  const [open, setOpen] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(async () => {
      try {
        const results = await searchSkins(value);
        setSuggestions(results);
      } catch {
        setSuggestions([]);
      }
    }, 250);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [value]);

  return (
    <div className="autocomplete">
      <input
        type="text"
        value={value}
        placeholder="Search skin name..."
        onChange={(e) => {
          onChange(e.target.value, null);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
      />
      {open && suggestions.length > 0 && (
        <ul className="autocomplete-list">
          {suggestions.map((skin) => {
            const disabled = requiredRarityId !== null && skin.rarity_id !== requiredRarityId;
            return (
              <li
                key={skin.name}
                className={disabled ? "disabled" : undefined}
                title={disabled ? "Different rarity tier than your other inputs" : undefined}
                onMouseDown={() => {
                  if (disabled) return;
                  onChange(skin.name, skin);
                  setOpen(false);
                }}
              >
                {skin.name}
                <span className="float-range">
                  {disabled
                    ? "different tier"
                    : `[${skin.min_float.toFixed(2)}–${skin.max_float.toFixed(2)}]`}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
