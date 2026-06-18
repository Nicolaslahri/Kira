/**
 * Two-tier episode-list cache: localStorage (persists across page
 * refreshes) + in-memory (avoids repeated localStorage parse cost
 * within a single tab session).
 *
 * Stale-while-revalidate. `getCachedEpisodes` paints last-known data
 * (in-memory Map → localStorage) synchronously so the popup never flickers;
 * `fetchSeriesEpisodes` ALWAYS revalidates against the backend once per tab
 * session and updates both tiers.
 *
 * IMPORTANT — the bug this comment used to describe wrongly: the persisted
 * entry has NO TTL, so `fetchSeriesEpisodes` must NOT short-circuit to it.
 * It used to `return stored` on a localStorage hit, so an episode list cached
 * before a provider added a brand-new episode's title (e.g. a just-aired One
 * Piece episode showing "Episode 1166" forever) was never refreshed — the
 * backend had the real title but the frontend never asked. Revalidation is now
 * gated by `revalidated` (keys successfully fetched THIS session), NOT by Map
 * presence — `getCachedEpisodes` fills the Map with the (possibly stale)
 * localStorage copy for instant paint, so trusting the Map would re-freeze it.
 *
 * Backend still caches process-side (6h), so a session's first request for a
 * given (provider, providerId, season) tuple is at worst one provider call.
 */
import { api } from './api';
import { cacheGet, cacheSet } from './cache';

export interface ProviderEpisode {
  season: number;
  episode: number;
  /** Series-wide absolute number, when the provider exposes it. For
   *  cross-ref anime (TVDB/TMDB) a "Season 4" lists local E1..E30 but
   *  carries absolute 60..89; the popup pairs absolute-named files
   *  ("- 60") against THIS, not the local episode number. Null for
   *  providers/episodes that don't supply it (AniDB-native: episode IS
   *  the absolute, so pairing falls back to `.episode`). */
  absolute_number: number | null;
  title: string | null;
  air_date: string | null;
  overview: string | null;
  /** Episode-specific runtime in minutes — TMDB / TVDB / AniDB all expose it.
   *  Rendered next to the air date: "Jun 27, 2024 · 36 min". */
  runtime: number | null;
}

const cache = new Map<string, ProviderEpisode[]>();
const inflight = new Map<string, Promise<ProviderEpisode[]>>();
// Keys successfully revalidated against the backend THIS tab session. Gates
// refetch — NOT Map presence, which getCachedEpisodes fills with the (possibly
// stale) localStorage copy for instant paint.
const revalidated = new Set<string>();

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
  // Reuse ONLY a result already revalidated against the backend this session.
  // Map presence alone is NOT enough — getCachedEpisodes promotes the (maybe
  // stale) localStorage copy into the Map for instant paint, and trusting that
  // is exactly what kept a just-titled episode showing "Episode N" forever.
  if (revalidated.has(k) && cache.has(k)) return Promise.resolve(cache.get(k)!);
  const existing = inflight.get(k);
  if (existing) return existing;
  const lastKnown = () => cache.get(k) ?? cacheGet<ProviderEpisode[]>(storageKey(k)) ?? [];
  const p = api.seriesEpisodes(provider, providerId, season)
    .then(({ episodes }) => {
      inflight.delete(k);
      if (episodes.length > 0) {
        revalidated.add(k);
        cache.set(k, episodes);
        cacheSet(storageKey(k), episodes);  // persist for next page's instant paint
        return episodes;
      }
      // Empty is usually transient (AniDB blip / ban) — keep last-known rather
      // than lock in "no episodes", and DON'T mark revalidated so we retry.
      return lastKnown();
    })
    .catch(() => {
      inflight.delete(k);
      return lastKnown();
    });
  inflight.set(k, p);
  return p;
}
