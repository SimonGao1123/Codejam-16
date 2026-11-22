// Score numbers are rounded to 1 decimal only here, right before they're shown, so floating-point
// leftovers (5 - 3.8 = 1.2000000000000002) never reach the screen.

/** Rounds half up to 1 decimal (0.35 -> 0.4; plain toFixed would give 0.3). EPSILON nudges values
 *  stored just under a half (1.05 is really 1.0499...) up, and keeps tiny leftovers like 8.9e-16
 *  from turning into anything but 0. */
function round1(value: number): number {
  return Math.round((value + Number.EPSILON) * 10) / 10
}

/** A score or points value, always with 1 decimal: 1.2, 15.0, 82.4. */
export function score1(value: number): string {
  return round1(value).toFixed(1)
}

/** A maximum, rounded to 1 decimal without a trailing ".0": 15, 12.5. */
export function max1(value: number): string {
  return String(round1(value))
}
