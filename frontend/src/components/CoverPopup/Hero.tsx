import { useState, type RefObject } from 'react';
import type { LibraryItem, MediaType } from '../../lib/types';
import { libraryStats, confTier } from '../LibraryGrid';
import { MediaTypeIcon } from '../ui';
import { pluralize, prettyLanguage, prettyCountry } from '../../lib/format';
import { confColorP } from './quality';
import { mediaTypeLong, ProviderLink } from './format';

interface HeroProps {
  item: LibraryItem;
  stats: ReturnType<typeof libraryStats>;
  tint: [string, string];
  shape: 'poster' | 'square';
  heroSlotRef: RefObject<HTMLDivElement | null>;
  settled: boolean;
  /** Real poster URL — resolved by the parent (handles AniDB lazy-fetch). */
  posterUrl: string | null;
}

export function Hero({ item, stats, tint, shape, heroSlotRef, settled, posterUrl }: HeroProps) {
  // Overview clamp/expand. Collapsed to ~4 lines (CSS line-clamp) so the
  // rail fits on a typical desktop without scrolling; "More" reveals the
  // rest in place. Only show the toggle when the overview is long enough
  // to plausibly clip (cheap char heuristic — avoids a layout-measuring
  // effect for a purely cosmetic affordance).
  const [overviewExpanded, setOverviewExpanded] = useState(false);
  const overviewLong = (item.overview?.length ?? 0) > 220;

  return (
    <div className={`cx-hero variant-side shape-${shape}`}>
      <div
        ref={heroSlotRef}
        className={`cx-hero-cover-slot shape-${shape} ${settled ? 'settled' : ''}`}
        style={{ background: `linear-gradient(135deg, ${tint[0]}, ${tint[1]})` }}
      >
        {/* Mount the cover DURING the open flight (the slot is opacity:0 via
            CSS until `settled`), so the image is fully loaded + decoded by the
            time the slot flips visible — the flying cover then hands off to an
            already-painted cover. Previously this was gated on `settled`, so the
            <img> only mounted AT handoff; the browser's decode lag left a gap
            where the foreground cover was gone and the blurred background bleed
            showed through — the "flash ~1s after opening". */}
        {(
          posterUrl ? (
            <img
              src={posterUrl}
              // PB-2: meaningful alt for screen readers. The hero title h2
              // also describes the content but a single image alt makes
              // image-navigation modes (some screen readers, browser
              // image-only view) usable. Empty alt was acceptable for
              // pure decoration but the cover IS content here.
              alt={`Cover art for ${item.title}`}
              referrerPolicy="no-referrer"
              // PB-2: decoding=async lets the browser decode off the main
              // thread (paint isn't blocked while we decode 500 KB).
              // fetchPriority=high since this is above-the-fold and the
              // user is staring at the slot waiting for it. width/height
              // help prevent layout shift before src loads.
              decoding="async"
              // @ts-expect-error — fetchpriority is valid HTML5 attribute
              // but typings haven't landed in @types/react yet.
              fetchpriority="high"
              style={{
                position: 'absolute', inset: 0, width: '100%', height: '100%',
                objectFit: 'cover', borderRadius: 'inherit',
              }}
            />
          ) : (
            <div style={{
              position: 'absolute', inset: 0, display: 'flex',
              flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
              color: '#fff', fontWeight: 700,
            }}>
              <span style={{ fontSize: 56, filter: 'drop-shadow(0 2px 8px rgba(0,0,0,0.4))' }}>
                {item.poster.init}
              </span>
              {item.year ? (
                <span style={{ fontSize: 14, opacity: 0.85, marginTop: 6, letterSpacing: '0.04em' }}>
                  {item.year}
                </span>
              ) : null}
            </div>
          )
        )}
      </div>

      <div className="cx-hero-info">
        <div className="cx-hero-meta-row">
          <span className={`cc-mediatype ${item.mediaType}`} style={{ position: 'static' }}>
            <MediaTypeIcon type={item.mediaType as MediaType} />{mediaTypeLong(item)}
          </span>
          {item.yearRange || item.year ? <span>{item.yearRange || item.year}</span> : null}
          {item.runtime ? <><span className="dot-sep" /><span>{item.runtime} min</span></> : null}
          {item.kind === 'series' ? <><span className="dot-sep" /><span>{pluralize(item.episodes.length, 'episode')}</span></> : null}
          {item.kind === 'album' ? <><span className="dot-sep" /><span>{pluralize(item.episodes.length, 'track')}</span></> : null}
        </div>

        <div className="cx-hero-titleblock">
          {/* PB-2: id targeted by .cx-shell's aria-labelledby so screen
              readers announce the show/movie title as the dialog name. */}
          <h2 id="cx-hero-title-id" className="cx-hero-title">
            {item.artist ? <span style={{ color: 'var(--ink-2)', fontWeight: 600 }}>{item.artist} — </span> : null}
            {item.title}
          </h2>
          {/* Romaji + native-script title for anime/foreign series.
              The user explicitly removed the "a.k.a. <Localized name>"
              chips (Spanish/Italian/French/etc. translations) — they
              added noise without value in a single-locale UI. */}
          {(item.titleRomaji || item.titleNative) ? (
            <div className="cx-hero-alt">
              {item.titleRomaji && item.titleRomaji !== item.title ? <span>{item.titleRomaji}</span> : null}
              {item.titleNative ? <span>{item.titleNative}</span> : null}
            </div>
          ) : null}
        </div>

        {item.overview ? (
          <>
            <p className={`cx-hero-overview ${overviewLong && !overviewExpanded ? 'clamp' : ''}`}>
              {item.overview}
            </p>
            {overviewLong ? (
              <button
                type="button"
                className="cx-hero-overview-more"
                onClick={() => setOverviewExpanded(v => !v)}
                aria-expanded={overviewExpanded}
              >
                {overviewExpanded ? 'Show less' : 'More'}
              </button>
            ) : null}
          </>
        ) : null}

        <div className="cx-hero-details">
          {item.studio || item.label ? (
            <div className="cx-hero-detail">
              <span className="cx-hero-detail-label">{item.kind === 'album' ? 'Label' : 'Studio'}</span>
              <span className="cx-hero-detail-value">{item.studio || item.label}</span>
            </div>
          ) : null}
          {item.network ? (
            <div className="cx-hero-detail">
              <span className="cx-hero-detail-label">Network</span>
              <span className="cx-hero-detail-value">{item.network}</span>
            </div>
          ) : null}
          {item.director ? (
            <div className="cx-hero-detail">
              <span className="cx-hero-detail-label">Director</span>
              <span className="cx-hero-detail-value">{item.director}</span>
            </div>
          ) : null}
          {/* F-13: humanize ISO codes for display. TVDB/TMDB return
              3-letter codes like "eng" / "usa" which read as jargon.
              prettyLanguage / prettyCountry map them to "English" /
              "United States" with safe fallback to uppercased code. */}
          {item.language ? (
            <div className="cx-hero-detail">
              <span className="cx-hero-detail-label">Language</span>
              <span className="cx-hero-detail-value">{prettyLanguage(item.language)}</span>
            </div>
          ) : null}
          {item.country ? (
            <div className="cx-hero-detail">
              <span className="cx-hero-detail-label">Country</span>
              <span className="cx-hero-detail-value">{prettyCountry(item.country)}</span>
            </div>
          ) : null}
          {item.genres?.length ? (
            <div className="cx-hero-detail span2">
              <span className="cx-hero-detail-label">Genres</span>
              <span className="cx-hero-detail-value wrap">{item.genres.join(' · ')}</span>
            </div>
          ) : null}
        </div>

        <div className="cx-hero-statsline">
          <div className="group">
            {!item.noMatch ? (
              <span className={`cx-summary-chip ${confTier(stats.avgConf)}`}>
                <span className="swatch" style={{ background: confColorP(stats.avgConf) }} />
                {stats.avgConf}% avg confidence
              </span>
            ) : null}
            {stats.approved > 0 ? <span className="cx-summary-chip"><span className="swatch" style={{ background: 'var(--conf-high)' }} />{stats.approved} approved</span> : null}
            {stats.pending > 0 ? <span className="cx-summary-chip"><span className="swatch" style={{ background: 'var(--conf-mid)' }} />{stats.pending} pending</span> : null}
            {stats.rejected > 0 ? <span className="cx-summary-chip"><span className="swatch" style={{ background: 'var(--conf-low)' }} />{stats.rejected} rejected</span> : null}
            {stats.unmatched > 0 ? <span className="cx-summary-chip"><span className="swatch" style={{ background: 'var(--ink-3)' }} />{stats.unmatched} unmatched</span> : null}
          </div>
          <span className="spacer" />
          <div className="group">
            {item.providers?.tmdb ? <ProviderLink label="TMDB" href={`https://www.themoviedb.org/${item.mediaType === 'movie' ? 'movie' : 'tv'}/${item.providers.tmdb}`} /> : null}
            {item.providers?.tvdb ? <ProviderLink label="TVDB" href={`https://www.thetvdb.com/?id=${item.providers.tvdb}&tab=series`} /> : null}
            {item.providers?.anidb ? <ProviderLink label="AniDB" href={`https://anidb.net/anime/${item.providers.anidb}`} /> : null}
            {item.providers?.musicbrainz ? <ProviderLink label="MusicBrainz" href={`https://musicbrainz.org/release/${item.providers.musicbrainz}`} /> : null}
          </div>
        </div>
      </div>
    </div>
  );
}
