import { useState, type ReactNode, type RefObject } from 'react';
import type { LibraryItem, MediaType } from '../../lib/types';
import { libraryStats, confTier } from '../LibraryGrid';
import { MediaTypeIcon } from '../ui';
import { Button } from '../base/buttons/button';
import { BadgeWithDot } from '../base/badges/badges';
import { cn } from '../../lib/utils';
import { pluralize, prettyLanguage, prettyCountry } from '../../lib/format';
import { mediaTypeLong } from './format';

// Confidence tier → BadgeWithDot color.
const CONF_DOT = { high: 'success', mid: 'warning', low: 'error' } as const;

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
  const overviewLong = (item.overview?.length ?? 0) > 200;

  // One key/value cell in the details grid (rendered only when value exists).
  const renderDetail = (label: string, value: ReactNode, span = false) =>
    value ? (
      <div key={label} className={cn('flex min-w-0 flex-col gap-0.5', span && 'col-span-2')}>
        <span className="text-[10px] font-semibold uppercase tracking-[0.06em] text-quaternary">{label}</span>
        <span className={cn('text-[13px] text-secondary', !span && 'truncate')}>{value}</span>
      </div>
    ) : null;

  const hasDetails = !!(item.studio || item.label || item.network || item.director || item.language || item.country || item.genres?.length);
  const hasProviders = !!(item.providers?.tmdb || item.providers?.tvdb || item.providers?.anidb || item.providers?.musicbrainz);

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
              fetchPriority="high"
              style={{
                position: 'absolute', inset: 0, width: '100%', height: '100%',
                objectFit: 'cover', borderRadius: 'inherit',
              }}
            />
          ) : (
            <div style={{
              position: 'absolute', inset: 0, display: 'flex',
              flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
              color: 'var(--ink)', fontWeight: 700,
            }}>
              <span style={{ fontSize: 56, filter: 'drop-shadow(0 2px 8px var(--scrim-40))' }}>
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

      <div className="cx-hero-info flex-1">
        {/* Title + alt titles + meta line */}
        <div className="flex flex-col gap-1.5">
          {/* PB-2: id targeted by .cx-shell's aria-labelledby so screen
              readers announce the show/movie title as the dialog name. */}
          <h2 id="cx-hero-title-id" className="line-clamp-2 text-[20px] font-semibold leading-tight tracking-tight text-primary">
            {item.artist ? <span className="font-medium text-secondary">{item.artist} — </span> : null}
            {item.title}
          </h2>
          {(item.titleRomaji && item.titleRomaji !== item.title) || item.titleNative ? (
            <p className="truncate text-xs italic text-tertiary">
              {[item.titleRomaji && item.titleRomaji !== item.title ? item.titleRomaji : null, item.titleNative].filter(Boolean).join('  ·  ')}
            </p>
          ) : null}
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-tertiary">
            <span className="inline-flex items-center gap-1 rounded-md bg-white/[0.06] px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.04em] text-secondary [&_svg]:size-3">
              <MediaTypeIcon type={item.mediaType as MediaType} />{mediaTypeLong(item)}
            </span>
            {item.yearRange || item.year ? <span>{item.yearRange || item.year}</span> : null}
            {item.runtime ? <><span className="dot-sep" /><span>{item.runtime} min</span></> : null}
            {item.kind === 'series' ? <><span className="dot-sep" /><span>{pluralize(item.episodes.length, 'episode')}</span></> : null}
            {item.kind === 'album' ? <><span className="dot-sep" /><span>{pluralize(item.episodes.length, 'track')}</span></> : null}
          </div>
        </div>

        {/* Overview — clamped to 3 lines, expandable in place via Show more */}
        {item.overview ? (
          <div>
            <p className={cn('text-[12.5px] leading-relaxed text-secondary', !overviewExpanded && 'line-clamp-3')}>
              {item.overview}
            </p>
            {overviewLong ? (
              <Button
                color="link-gray"
                size="sm"
                className="mt-1 text-[12px]"
                onClick={() => setOverviewExpanded(v => !v)}
                aria-expanded={overviewExpanded}
              >
                {overviewExpanded ? 'Show less' : 'Show more'}
              </Button>
            ) : null}
          </div>
        ) : null}

        {/* Details — compact 2-column key/value grid.
            F-13: prettyLanguage / prettyCountry humanize ISO codes (eng→English). */}
        {hasDetails ? (
          <div className="grid grid-cols-2 gap-x-4 gap-y-2.5 border-t border-secondary pt-3">
            {renderDetail(item.kind === 'album' ? 'Label' : 'Studio', item.studio || item.label)}
            {renderDetail('Network', item.network)}
            {renderDetail('Director', item.director)}
            {renderDetail('Language', item.language ? prettyLanguage(item.language) : null)}
            {renderDetail('Country', item.country ? prettyCountry(item.country) : null)}
            {item.genres?.length ? renderDetail('Genres', item.genres.join(' · '), true) : null}
          </div>
        ) : null}

        {/* Confidence / status (UUI BadgeWithDot) + provider links — pinned to
            the bottom of the rail so the column uses the full vertical space. */}
        <div className="mt-auto flex flex-col gap-2 border-t border-secondary pt-3">
          <div className="flex flex-wrap items-center gap-1.5">
            {!item.noMatch ? <BadgeWithDot color={CONF_DOT[confTier(stats.avgConf)]}>{stats.avgConf}% avg confidence</BadgeWithDot> : null}
            {stats.approved > 0 ? <BadgeWithDot color="success">{stats.approved} approved</BadgeWithDot> : null}
            {stats.pending > 0 ? <BadgeWithDot color="warning">{stats.pending} pending</BadgeWithDot> : null}
            {stats.rejected > 0 ? <BadgeWithDot color="error">{stats.rejected} rejected</BadgeWithDot> : null}
            {stats.unmatched > 0 ? <BadgeWithDot color="gray">{stats.unmatched} unmatched</BadgeWithDot> : null}
          </div>
          {hasProviders ? (
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] font-medium">
              {item.providers?.tmdb ? <a href={`https://www.themoviedb.org/${item.mediaType === 'movie' ? 'movie' : 'tv'}/${item.providers.tmdb}`} target="_blank" rel="noreferrer" className="text-[var(--color-fg-brand-primary)] transition-colors hover:underline">TMDB ↗</a> : null}
              {item.providers?.tvdb ? <a href={`https://www.thetvdb.com/?id=${item.providers.tvdb}&tab=series`} target="_blank" rel="noreferrer" className="text-[var(--color-fg-brand-primary)] transition-colors hover:underline">TVDB ↗</a> : null}
              {item.providers?.anidb ? <a href={`https://anidb.net/anime/${item.providers.anidb}`} target="_blank" rel="noreferrer" className="text-[var(--color-fg-brand-primary)] transition-colors hover:underline">AniDB ↗</a> : null}
              {item.providers?.musicbrainz ? <a href={`https://musicbrainz.org/release/${item.providers.musicbrainz}`} target="_blank" rel="noreferrer" className="text-[var(--color-fg-brand-primary)] transition-colors hover:underline">MusicBrainz ↗</a> : null}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
