/**
 * Tiny localStorage-backed cache for "show last-known data instantly
 * on refresh, then refresh in the background" (stale-while-revalidate).
 *
 * This is NOT a general-purpose cache — it's intentionally minimal:
 *   - No TTL (we always background-fetch on mount).
 *   - No eviction (sizes are tiny: counts, scan summaries, ≤500 file
 *     rows). localStorage's ~5MB quota is comfortably enough.
 *   - Synchronous read so pages can hydrate state on first render
 *     (avoids the one-frame flash that an async hydrate has).
 *   - Quiet failures — if storage is full, blocked, or JSON parsing
 *     blows up, we return null and the page renders its empty/loading
 *     state. Nothing crashes.
 *
 * Versioning: bump CACHE_VERSION when an entry's shape changes in a way
 * that would crash the renderer (e.g. a previously-required field
 * becomes nullable). Old keys are ignored.
 */

// v2: episode-list entries gained `absolute_number`. Old v1 entries lack it,
// so the popup would keep pairing absolute-named anime files (e.g. AoT
// "Shingeki no Kyojin - 60") against local episode numbers and show them
// "orphaned" until the stale entry was evicted. Bumping ignores all v1 keys
// → the popup refetches /series fresh (now carrying absolute_number).
const CACHE_VERSION = 2;
const PREFIX = `kira:cache:v${CACHE_VERSION}:`;

export function cacheGet<T>(key: string): T | null {
  try {
    const raw = localStorage.getItem(PREFIX + key);
    if (!raw) return null;
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

export function cacheSet<T>(key: string, value: T): void {
  try {
    localStorage.setItem(PREFIX + key, JSON.stringify(value));
  } catch {
    // Quota full or storage disabled — fine, next refresh just won't
    // get the instant-paint benefit. Don't disturb the UI.
  }
}
