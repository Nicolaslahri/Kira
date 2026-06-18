/**
 * Shared lazy-fetch cache for AniDB poster URLs.
 *
 * AniDB's title-dump search doesn't include image URLs, so every consumer
 * that wants a poster (CoverCard, CoverPopup, etc.) has to fetch it via
 * the rate-limited /search/anidb/picture/{aid} endpoint. We centralize the
 * cache here so:
 *   1. Multiple components asking for the same AID share one network call
 *      (e.g. card + popup at the same time).
 *   2. Switching pages and coming back is instant (no re-fetch).
 *
 * The backend ALSO caches to disk, so the worst case is a 4-second wait
 * the first time an AID is ever seen, then instant forever after.
 */
import { api, posterSrc } from './api';

const cache = new Map<string, string | null>();
const inflight = new Map<string, Promise<string | null>>();

export function getCachedAnidbPoster(aid: string): string | null | undefined {
  return cache.get(aid);
}

export function fetchAnidbPoster(aid: string): Promise<string | null> {
  if (cache.has(aid)) return Promise.resolve(cache.get(aid) ?? null);
  const existing = inflight.get(aid);
  if (existing) return existing;
  const p = api.anidbPicture(aid)
    .then(({ picture_url }) => {
      // Route AniDB's slow CDN through Kira's image proxy/cache (fast hosts
      // pass through untouched).
      const proxied = posterSrc(picture_url);
      cache.set(aid, proxied);
      inflight.delete(aid);
      return proxied;
    })
    .catch(() => {
      inflight.delete(aid);
      return null;
    });
  inflight.set(aid, p);
  return p;
}
