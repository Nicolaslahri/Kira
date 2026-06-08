// Confidence badge cutoffs (percent, 0-100).
//
// The matcher's internal cascade scoring bands are fixed structural constants,
// but where the green / amber / red BADGES fall in the UI is user-tunable
// (Settings → Confidence → "Confidence thresholds"). Previously these cutoffs
// were hardcoded at 85 / 50 in three components, so the Confidence sliders
// wrote settings nothing ever read. This module is the single source of truth:
// App hydrates it once on load (and on every settings save), and badge
// components read the current cutoffs synchronously at render time.
let _high = 85;
let _mid = 50;

/** Hydrate the cutoffs from saved settings. Non-finite values are ignored so a
 *  missing/garbage setting falls back to the previous (default) value. */
export function setConfBands(high: number, mid: number): void {
  if (Number.isFinite(high)) _high = high;
  if (Number.isFinite(mid)) _mid = mid;
}

export function getConfBands(): { high: number; mid: number } {
  return { high: _high, mid: _mid };
}

/** Bucket a 0-100 confidence percent into the current high / mid / low band. */
export function confLevel(pct: number): 'high' | 'mid' | 'low' {
  return pct >= _high ? 'high' : pct >= _mid ? 'mid' : 'low';
}
