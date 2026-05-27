/**
 * Two-tier episode-list cache: localStorage (persists across page
 * refreshes) + in-memory (avoids repeated localStorage parse cost
 * within a single tab session).
 *
 * The CoverPopup fires `seriesEpisodes()` on mount to get the authoritative
 * episode count + titles + air dates. Previously the cache was Map-only,
 * which meant every page refresh started cold — the popup's right column
 * would show synthesized-from-files data for ~1s, then snap to real
 * provider data when the fetch resolved. That snap is the "data changes
 * after 1 second of opening the popup" the user called out.
 *
 * Fix: persist successful fetches to localStorage keyed by
 * `kira:cache:episodes:<provider>|<providerId>|<season>`. On popup open:
 *   1. Synchronously check the in-memory Map → if hit, instant data.
 *   2. Synchronously check localStorage → if hit, hydrate the Map + return.
 *   3. Otherwise issue a network fetch, store both places on success.
 *
 * Backend still caches process-side, so the worst case is one provider
 * call the first time anyone in this household requests a particular
 * (provider, providerId, season) tuple.
 */
import { api } from './api';
import { cacheGet, cacheSet } from './cache';

export interface ProviderEpisode {
  season: number;
  episode: number;
  title: string | null;
  air_date: string | null;
  overview: string | null;
  /** Episode-specific runtime in minutes — TMDB / TVDB / AniDB all expose it.
   *  Rendered next to the air date: "Jun 27, 2024 · 36 min". */
  runtime: number | null;
}

const cache = new Map<string, ProviderEpisode[]>();
const inflight = new Map<string, Promise<ProviderEpisode[]>>();

function key(provider: string, providerId: string, season?: number): string {
  return `${provider}|${providerId}|${season ?? '_'}`;
}

function storageKey(k: string): string {
  return `episodes:${k}`;
}

/**
 * Synchronous cache lookup. Checks the in-memory Map first; if that
 * misses, falls back to localStorage and hydrates the Map. Returns
 * `undefined` only when neither tier has the entry.
 *
 * Used by the popup to render the authoritative episode list on the
 * very first frame after the open animation settles — no flicker.
 */
export function getCachedEpisodes(provider: string, providerId: string, season?: number): ProviderEpisode[] | undefined {
  const k = key(provider, providerId, season);
  const inMem = cache.get(k);
  if (inMem) return inMem;
  const stored = cacheGet<ProviderEpisode[]>(storageKey(k));
  if (stored) {
    // Promote into the Map so subsequent calls in this tab session
    // skip the JSON.parse + localStorage hit.
    cache.set(k, stored);
    return stored;
  }
  return undefined;
}

export function fetchSeriesEpisodes(provider: string, providerId: string, season?: number): Promise<ProviderEpisode[]> {
  const k = key(provider, providerId, season);
  if (cache.has(k)) return Promise.resolve(cache.get(k)!);
  // Synchronous localStorage check — same logic as getCachedEpisodes
  // but inline to avoid the extra Map.get in the hot path.
  const stored = cacheGet<ProviderEpisode[]>(storageKey(k));
  if (stored) {
    cache.set(k, stored);
    return Promise.resolve(stored);
  }
  const existing = inflight.get(k);
  if (existing) return existing;
  const p = api.seriesEpisodes(provider, providerId, season)
    .then(({ episodes }) => {
      cache.set(k, episodes);
      // Persist for the next page refresh. Skip empty arrays — those
      // are usually transient (banned AniDB, network blip) and we
      // don't want to lock in "no episodes" as the persisted answer.
      if (episodes.length > 0) {
        cacheSet(storageKey(k), episodes);
      }
      inflight.delete(k);
      return episodes;
    })
    .catch(() => {
      inflight.delete(k);
      return [] as ProviderEpisode[];
    });
  inflight.set(k, p);
  return p;
}
