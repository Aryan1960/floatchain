// Mirrors backend/app/domain/wear.py — kept in sync manually since this is
// just for a live UI label, not the source of truth for pricing/EV math.
const WEAR_THRESHOLDS: [number, string][] = [
  [0.07, "Factory New"],
  [0.15, "Minimal Wear"],
  [0.38, "Field-Tested"],
  [0.45, "Well-Worn"],
  [1.0, "Battle-Scarred"],
];

export function wearName(floatValue: number): string {
  for (const [upperBound, name] of WEAR_THRESHOLDS) {
    if (floatValue < upperBound) return name;
  }
  return "Battle-Scarred";
}
