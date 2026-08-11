import { wearName } from "../lib/wear";

interface Props {
  value: string;
  min: number;
  max: number;
  disabled: boolean;
  onChange: (value: string) => void;
}

export function FloatInput({ value, min, max, disabled, onChange }: Props) {
  const numeric = Number(value);
  const hasValue = value !== "" && !Number.isNaN(numeric);
  const outOfRange = hasValue && (numeric < min || numeric > max);
  const sliderValue = hasValue && !outOfRange ? numeric : min;

  return (
    <div className="float-input">
      <input
        type="range"
        min={min}
        max={max}
        step={(max - min) / 1000 || 0.0001}
        value={sliderValue}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
      />
      <div className="float-input-row">
        <input
          type="number"
          step="0.000001"
          value={value}
          disabled={disabled}
          className={outOfRange ? "invalid" : undefined}
          placeholder={disabled ? "pick a skin first" : `${min}–${max}`}
          onChange={(e) => onChange(e.target.value)}
        />
        {hasValue && !outOfRange && <span className="wear-badge">{wearName(numeric)}</span>}
      </div>
      <div className={`float-hint${outOfRange ? " float-hint-error" : ""}`}>
        {disabled
          ? "Pick a skin first to see its valid float range"
          : outOfRange
            ? `This skin can't exist at that float — it only ranges from ${min.toFixed(6)} to ${max.toFixed(6)}`
            : `Valid range for this skin: ${min.toFixed(6)} – ${max.toFixed(6)}`}
      </div>
    </div>
  );
}
