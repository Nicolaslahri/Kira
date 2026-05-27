// LibraryGrid — sectioned cover-grid view of the library.
// Ported from the design prototype (kira/project/src/grid.jsx).
// Owns: CoverCard (atomic unit), section headers, scan-in-progress floater,
//       empty / zero-result states.

import { useEffect, useMemo, useState } from 'react';
import type { LibraryItem, MediaType } from '../lib/types';
import { pluralize } from '../lib/format';
import {
  IcCheck, IcX, IcSearch, IcAlertTri,
  IcTv, IcAnime, IcFilm, IcDisc, IcReview,
} from '../lib/icons';
import { MediaTypeIcon } from './ui';
import { fetchAnidbPoster, getCachedAnidbPoster } from '../lib/posters';

// ─────────────────────────────────────────────────────────────────────
// Helpers (pure, no hooks)
// ─────────────────────────────────────────────────────────────────────

interface LibStats {
  total: number;
  matched: number;
  wrong: number;
  unmatched: number;
  approved: number;
  rejected: number;
  pending: number;
  avgConf: number;
  cardState: 'matched' | 'matching' | 'no_match' | 'mixed' | 'partial' | 'approved' | 'rejected';
}

export function libraryStats(item: LibraryItem): LibStats {
  const total = item.files.length;
  const matched = item.files.filter(f => f.matchedToEpisode != null && !f.matchedWrong).length;
  const wrong = item.files.filter(f => f.matchedWrong).length;
  const unmatched = item.files.filter(f => f.matchedToEpisode == null).length;
  const approved = item.files.filter(f => f.status === 'approved').length;
  const rejected = item.files.filter(f => f.status === 'rejected').length;
  const pending = item.files.filter(f => f.status === 'pending' || f.status === 'matching').length;
  const avgConf = total > 0
    ? Math.round(item.files.reduce((s, f) => s + (f.confidence || 0), 0) / total)
    : 0;
  let cardState: LibStats['cardState'] = 'matched';
  if (item.noMatch) cardState = 'no_match';
  else if (item.matchingState) cardState = 'matching';
  else if (item.overallStatus === 'rejected' || (rejected === total && total > 0)) cardState = 'rejected';
  else if (approved === total && total > 0) cardState = 'approved';
  else if (rejected > 0 || wrong > 0 || (approved > 0 && approved < total)) cardState = 'mixed';
  else if (unmatched > 0 && matched > 0) cardState = 'partial';
  else if (unmatched === total) cardState = 'no_match';
  return { total, matched, wrong, unmatched, approved, rejected, pending, avgConf, cardState };
}

export function confTier(v: number): 'high' | 'mid' | 'low' {
  if (v >= 85) return 'high';
  if (v >= 50) return 'mid';
  return 'low';
}

function confColor(v: number): string {
  if (v >= 85) return 'var(--conf-high)';
  if (v >= 50) return 'var(--conf-mid)';
  return 'var(--conf-low)';
}

function ccStatChipColor(s: LibStats): string {
  if (s.cardState === 'approved' || s.avgConf >= 85) return 'var(--conf-high)';
  if (s.cardState === 'rejected' || s.avgConf < 50) return 'var(--conf-low)';
  return 'var(--conf-mid)';
}

function subLabel(item: LibraryItem): string {
  if (item.kind === 'movie' && !item.noMatch) return item.runtime ? `${item.runtime} min` : 'Movie';
  if (item.kind === 'album') return pluralize(item.episodes.length, 'track');
  if (item.kind === 'series') {
    const seasons = new Set(item.episodes.map(e => e.season)).size;
    const eps = item.episodes.length;
    return seasons > 1 ? `S${seasons} · ${eps} eps` : pluralize(eps, 'episode');
  }
  return '';
}

interface Section { key: MediaType; label: string; icon: 'tv' | 'anime' | 'film' | 'disc'; desc: string }
const SECTIONS: Section[] = [
  { key: 'tv',    label: 'TV Series', icon: 'tv',    desc: 'Episodic television' },
  { key: 'anime', label: 'Anime',     icon: 'anime', desc: 'Japanese animation' },
  { key: 'movie', label: 'Movies',    icon: 'film',  desc: 'Single-file releases' },
  { key: 'music', label: 'Albums',    icon: 'disc',  desc: 'Music releases' },
];

function iconFor(name: Section['icon']) {
  if (name === 'tv')    return <IcTv />;
  if (name === 'anime') return <IcAnime />;
  if (name === 'film')  return <IcFilm />;
  return <IcDisc />;
}

// ─────────────────────────────────────────────────────────────────────
// CoverCard — the atomic unit
// ─────────────────────────────────────────────────────────────────────

interface CoverCardProps {
  item: LibraryItem;
  selected: boolean;
  anySelected: boolean;
  focused: boolean;
  index: number;
  /** True when the card is inside a multi-member franchise group.
   *  Triggers the "Season N" / title cleanup behavior — the actual
   *  season number is read from `item.season` (set by the adapter
   *  from the provider's canonical season_number, NOT a frontend
   *  heuristic). */
  inFranchise?: boolean;
  onSelect: (id: string) => void;
  onOpen: (item: LibraryItem, coverEl: HTMLElement) => void;
  onApprove: (item: LibraryItem) => void;
  onReject: (item: LibraryItem) => void;
  onManualSearch: (item: LibraryItem) => void;
}

export function CoverCard({
  item, selected, anySelected, focused, index, inFranchise,
  onSelect, onOpen, onApprove, onReject, onManualSearch,
}: CoverCardProps) {
  // Display label for the franchise context: prefer the provider's
  // canonical season number (Match.season_number, set by the backend
  // via Fribb cross-ref for AniDB). Falls back to the bare year if no
  // season is known (e.g. movie franchise members). Handles Season 0
  // (Specials/OVAs) correctly without any special-casing.
  const seasonLabel: string | null = inFranchise
    ? (typeof item.season === 'number'
        ? (item.season === 0 ? 'Specials' : `Season ${item.season}`)
        : (item.year ? String(item.year) : null))
    : null;
  const stats = useMemo(() => libraryStats(item), [item]);
  const isAnime = item.mediaType === 'anime';
  const isMusic = item.mediaType === 'music';
  const shape = isMusic ? 'square' : 'poster';

  let ringClass = 'ring-none';
  if (!item.noMatch && !item.matchingState && stats.cardState !== 'rejected') {
    ringClass = `ring-${confTier(stats.avgConf)}`;
  }

  const tint = item.poster.tint;

  // AniDB's title-dump search doesn't carry image URLs — fetch lazily.
  // Seed from the shared cache synchronously so cards re-render with the
  // poster already in place after a page switch / popup close.
  const anidbAid = item.providers?.anidb;
  const [lazyPoster, setLazyPoster] = useState<string | null>(() =>
    anidbAid ? (getCachedAnidbPoster(String(anidbAid)) ?? null) : null
  );
  const effectivePosterUrl = item.posterUrl ?? lazyPoster;
  useEffect(() => {
    if (item.posterUrl || lazyPoster || !anidbAid) return;
    let cancelled = false;
    fetchAnidbPoster(String(anidbAid)).then(url => {
      if (!cancelled && url) setLazyPoster(url);
    });
    return () => { cancelled = true; };
  }, [item.posterUrl, lazyPoster, anidbAid]);

  const handleCardClick = (e: React.MouseEvent<HTMLDivElement>) => {
    // Don't trigger expand when clicking an inner action/checkbox.
    const target = e.target as HTMLElement;
    if (target.closest('.cc-act, .cc-select, .cc-no-match-cta')) return;
    const coverEl = e.currentTarget.querySelector('.cc-cover');
    if (coverEl instanceof HTMLElement) onOpen(item, coverEl);
  };

  const cardClass = [
    'cc',
    'state-' + stats.cardState,
    ringClass,
    isAnime ? 'is-anime' : '',
    selected ? 'selected' : '',
    anySelected ? 'any-selected' : '',
    focused ? 'keyfocused' : '',
  ].filter(Boolean).join(' ');

  return (
    <div
      className={cardClass}
      style={{ ['--i' as never]: index } as React.CSSProperties}
      onClick={handleCardClick}
      data-cardid={item.id}
    >
      {item.noMatch ? (
        <div
          className={`cc-cover shape-${shape} cc-cover-nm`}
          style={{ background: `linear-gradient(135deg, ${tint[0]}, ${tint[1]})` }}
        >
          {/* Media-type icon centered as the placeholder "art" — same
              visual language as a missing-poster card but tinted with the
              media-type colour so anime/movie/tv stay distinguishable. */}
          <div className="cc-nm-icon">
            {item.mediaType === 'movie' ? <IcFilm /> :
             item.mediaType === 'anime' ? <IcAnime /> :
             item.mediaType === 'music' ? <IcMusic /> :
             <IcTv />}
          </div>
          {/* Small alert badge in the corner so the no-match state is
              still obvious without dominating the cover. */}
          <div className="cc-nm-alert" title="No metadata match found">
            <IcAlertTri />
          </div>
          {/* Parsed context — what we DO know about this file. The
              episode count comes from the cluster (multiple files of
              the same parsed series). */}
          {item.episodes.length > 1 ? (
            <span className="cc-year">{pluralize(item.episodes.length, 'file')}</span>
          ) : null}
        </div>
      ) : (
        <div
          className={`cc-cover shape-${shape}`}
          style={{ background: `linear-gradient(135deg, ${tint[0]}, ${tint[1]})` }}
        >
          {effectivePosterUrl ? (
            <img
              src={effectivePosterUrl}
              alt=""
              loading="lazy"
              referrerPolicy="no-referrer"
              onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none'; }}
              style={{
                position: 'absolute', inset: 0, width: '100%', height: '100%',
                objectFit: 'cover', zIndex: 0, display: 'block',
              }}
            />
          ) : (
            <>
              <span className="cc-init">{item.poster.init}</span>
              {seasonLabel
                ? <span className="cc-year">{seasonLabel}</span>
                : (item.year ? <span className="cc-year">{item.year}</span> : null)}
            </>
          )}

          {/* Bulk-select checkbox (top-left).
              F-12: descriptive aria-label so screen-reader users know
              WHICH card the checkbox belongs to + aria-pressed state. */}
          <button
            className={`cc-select ${selected ? 'on' : ''}`}
            onClick={(e) => { e.stopPropagation(); onSelect(item.id); }}
            title={selected ? 'Deselect' : 'Select'}
            aria-label={`${selected ? 'Deselect' : 'Select'} ${item.title || 'card'}`}
            aria-pressed={selected}
          >
            {selected ? <IcCheck /> : null}
          </button>

          {/* Confidence pill (top-right, replaces media-type pill) */}
          {!item.matchingState && !item.noMatch ? (
            <div className="cc-corner-r">
              <span className={`cc-conf-pill ${confTier(stats.avgConf)}`}>
                <span className="swatch" style={{ background: ccStatChipColor(stats) }} />
                {stats.cardState === 'mixed' || stats.cardState === 'partial'
                  ? `${stats.matched}/${stats.total}`
                  : `${stats.avgConf}%`}
              </span>
            </div>
          ) : null}

          {/* Hover quick actions (bottom-right) */}
          {!item.matchingState ? (
            <div className="cc-actions">
              <button
                className="cc-act approve"
                onClick={(e) => { e.stopPropagation(); onApprove(item); }}
                title="Approve all matched"
              ><IcCheck /></button>
              <button
                className="cc-act reject"
                onClick={(e) => { e.stopPropagation(); onReject(item); }}
                title="Reject all"
              ><IcX /></button>
              <button
                className="cc-act"
                onClick={(e) => { e.stopPropagation(); onManualSearch(item); }}
                title="Search manually"
              ><IcSearch /></button>
            </div>
          ) : null}
        </div>
      )}

      {/* Below-cover meta */}
      <div className="cc-meta">
        <div className={`cc-title ${stats.cardState === 'approved' ? 'approved' : ''}`}>
          {item.noMatch
            // Use the parsed title (already on item.title via the adapter
            // fallback chain). Title-case any all-lowercase parsed titles
            // so "one pace" → "One Pace" visually.
            ? (item.title || 'Unknown file')
            : (inFranchise
                // Inside a franchise group, the heading already names the show.
                // Drop the trailing "(YYYY)" provider artifact so the card
                // doesn't read "Rent-a-Girlfriend (2023)" right next to
                // "Rent-a-Girlfriend (2024)" cards under the same heading.
                ? (item.title || '').replace(/\s*\(\d{4}\)\s*$/, '')
                : item.title)}
        </div>
        <div className="cc-bottom-row">
          <div className="cc-sub">
            {item.noMatch ? (
              <>
                {/* What the parser DID extract — season + episode count
                    or just file count. Plus a "Search →" affordance
                    that opens manual search without leaving the grid. */}
                {(() => {
                  const ep = item.episodes[0];
                  if (item.episodes.length === 1 && ep) {
                    const label = item.mediaType === 'anime' && ep.absolute
                      ? `Episode ${ep.absolute}`
                      : `S${String(ep.season).padStart(2,'0')}E${String(ep.episode).padStart(2,'0')}`;
                    return <span>{label}</span>;
                  }
                  if (ep) {
                    return <span>Season {ep.season} · {pluralize(item.episodes.length, 'ep')}</span>;
                  }
                  return <span>{pluralize(item.files.length, 'file')}</span>;
                })()}
                <span className="dot-sep" />
                <button
                  className="cc-nm-search-link"
                  onClick={(e) => { e.stopPropagation(); onManualSearch(item); }}
                >
                  <IcSearch /> Search
                </button>
              </>
            ) : (
              <>
                {item.artist ? <span className="cc-sub-strong">{item.artist}</span> : null}
                {seasonLabel
                  ? <span>{seasonLabel}</span>
                  : ((item.yearRange || item.year) ? <span>{item.yearRange || item.year}</span> : null)}
                {subLabel(item) ? <span>{subLabel(item)}</span> : null}
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// LibraryGrid — sectioned grid wrapper
// ─────────────────────────────────────────────────────────────────────

interface LibraryGridProps {
  items: LibraryItem[];
  selected: Set<string>;
  setSelected: (s: Set<string>) => void;
  focusedId: string;
  setFocusedId: (id: string) => void;
  /** Total files in the library before any filters. Used to differentiate
   *  "library is genuinely empty" from "filters narrowed to zero".
   *  Defaults to undefined for backwards compat. */
  totalLibrarySize?: number;
  /** Called when the user clicks "Clear filters" in the empty state. */
  onClearFilters?: () => void;
  /** Hydration gate. False while the initial /files fetch is in flight;
   *  true once it resolves (success OR failure). When false, we suppress
   *  the "Library is empty" hero — that hero is meaningful only after
   *  the first fetch returned actual zero files, not during the brief
   *  loading window. Without this, every page refresh flashes the
   *  empty-state hero for 200-500ms which reads as a glitch. */
  hydrated?: boolean;
  scanRunning: boolean;
  scanProgress: number;
  scanMessage: string;
  scanFound: number;
  onOpenCover: (item: LibraryItem, coverEl: HTMLElement) => void;
  onApprove: (item: LibraryItem) => void;
  onReject: (item: LibraryItem) => void;
  onManualSearch: (item: LibraryItem) => void;
}

export function LibraryGrid({
  items, selected, setSelected, focusedId, setFocusedId,
  totalLibrarySize, onClearFilters, hydrated,
  scanRunning, scanProgress, scanMessage, scanFound,
  onOpenCover, onApprove, onReject, onManualSearch,
}: LibraryGridProps) {
  // Split items: no_match cards go to their own "Needs matching" section,
  // matched cards stay in their media-type section. Without this, the
  // 14-odd unmatched anime cards (e.g. all the One Pace seasons) mix in
  // with the 169 properly-matched anime cards and get lost in the grid.
  // Surfacing them as one block makes it obvious where the user needs
  // to intervene.
  const { needsMatching, grouped } = useMemo(() => {
    const out: Record<MediaType, LibraryItem[]> = { tv: [], anime: [], movie: [], music: [] };
    const nm: LibraryItem[] = [];
    items.forEach(it => {
      if (it.noMatch) nm.push(it);
      else out[it.mediaType]?.push(it);
    });
    return { needsMatching: nm, grouped: out };
  }, [items]);

  const anySelected = selected.size > 0;

  if (items.length === 0) {
    // First-paint guard: until the initial /files fetch returns, we don't
    // know whether the library is actually empty or whether the data
    // just hasn't arrived yet. Suppress the empty-state hero during this
    // window — `hydrated` flips true in App.tsx after listFiles resolves.
    // For pages that don't pass the prop (old callers / tests), default
    // to true so behavior is unchanged.
    if (hydrated === false) {
      // Skeleton cover-card grid — same shape as a real library page.
      // Way better than a centered spinner: the layout is already in
      // place, so when real cards land they slot into existing slots
      // instead of materializing from nothing. Each "card" is a
      // poster-shaped block + a couple of text lines.
      const skeletonCard = (key: number) => (
        <div key={key} style={{ pointerEvents: 'none' }}>
          <div
            className="kira-skeleton"
            style={{
              width: '100%',
              aspectRatio: '2 / 3',
              borderRadius: 10,
              display: 'block',
            }}
          />
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 10, padding: '0 4px' }}>
            <span
              className="kira-skeleton"
              style={{ display: 'block', width: '85%', height: 13, borderRadius: 4 }}
            />
            <span
              className="kira-skeleton"
              style={{ display: 'block', width: '55%', height: 11, borderRadius: 4 }}
            />
          </div>
        </div>
      );
      return (
        <div aria-busy="true" role="status" aria-label="Loading library" style={{ padding: '8px 0' }}>
          <div className="lib-section-head" style={{ opacity: 0.5, marginBottom: 12 }}>
            <span
              className="kira-skeleton"
              style={{ display: 'inline-block', width: 120, height: 16, borderRadius: 4 }}
            />
          </div>
          <div className="lib-grid">
            {Array.from({ length: 12 }, (_, i) => skeletonCard(i))}
          </div>
        </div>
      );
    }
    // Differentiate "library is genuinely empty" (no scan ever run) from
    // "you filtered to zero results" (scan ran, filters too narrow). The
    // first wants "Run a scan" + onboarding hand-holding, the second
    // wants "Clear filters".
    const isFiltered = (totalLibrarySize ?? 0) > 0;
    if (isFiltered) {
      return (
        <div className="lib-empty">
          <div className="hero"><IcReview /></div>
          <div>
            <h3>Nothing matches these filters</h3>
            <p>
              {totalLibrarySize} file{totalLibrarySize === 1 ? '' : 's'} in your library don't match the active filters.
            </p>
            {onClearFilters ? (
              <button
                className="btn btn-primary"
                style={{ marginTop: 12 }}
                onClick={onClearFilters}
              >Clear all filters</button>
            ) : null}
          </div>
        </div>
      );
    }
    // PB-4: genuinely-empty library. Replace generic "Run a scan" with a
    // 3-step guided hero. The CTAs deep-link to Settings panes so the
    // user lands exactly where they need to be (not "open Settings,
    // then figure out which tab"). Dismiss is implicit — once they
    // scan, the items render and this whole block disappears.
    return (
      <div className="lib-empty lib-empty-hero">
        <div className="hero"><IcReview /></div>
        <div className="lib-empty-content">
          <h3>Let's set up your library</h3>
          <p className="lib-empty-sub">
            Kira scans your media folder, identifies movies and shows, then
            renames them to Plex / Jellyfin conventions. Three quick steps:
          </p>
          <ol className="lib-empty-steps">
            <li>
              <span className="step-num">1</span>
              <div>
                <strong>Pick your media folder</strong>
                <a className="step-link" href="#/settings">Open Paths settings →</a>
              </div>
            </li>
            <li>
              <span className="step-num">2</span>
              <div>
                <strong>Add a TMDB API key</strong> <span className="step-meta">(free, takes 60s)</span>
                <a className="step-link" href="#/settings">Open Providers settings →</a>
              </div>
            </li>
            <li>
              <span className="step-num">3</span>
              <div>
                <strong>Run your first scan</strong>
                <span className="step-meta">— button is in the top bar, top-right of the page.</span>
              </div>
            </li>
          </ol>
        </div>
      </div>
    );
  }

  // Note: the page-level scan banner was removed. The global App-level
  // `.global-scan-bar` (rendered in App.tsx) already shows scan progress
  // sticky under the topbar on every page — duplicating it here gave the
  // user two banners during scans.
  void scanRunning; void scanFound; void scanMessage; void scanProgress;

  return (
    <div>
      {SECTIONS.map(sec => {
        const arr = grouped[sec.key];
        if (!arr || arr.length === 0) return null;
        return (
          <section key={sec.key} className="lib-section">
            <header className="lib-section-head">
              <span className={`lib-section-icon ${sec.key}`}>{iconFor(sec.icon)}</span>
              <h2 className="lib-section-title">{sec.label}</h2>
              <div className="lib-section-meta">
                <span className="lib-section-count">{arr.length}</span>
                <span>{arr.length === 1 ? 'card' : 'cards'}</span>
                <span className="dot-sep" />
                <span>{sec.desc}</span>
              </div>
            </header>

            {renderSectionBody(
              arr, sec.key,
              { selected, setSelected, focusedId, setFocusedId, anySelected,
                onOpenCover, onApprove, onReject, onManualSearch },
            )}
          </section>
        );
      })}

      {/* Needs-matching section — rendered LAST so it doesn't dominate
          the page on first paint. The properly-matched library is the
          primary content; the unmatched stuff is the punch list at the
          bottom for when the user is ready to deal with it. Pseudo-
          section: not in SECTIONS because it spans all media types. */}
      {needsMatching.length > 0 ? (
        <section className="lib-section lib-section-needs">
          <header className="lib-section-head">
            <span className="lib-section-icon needs"><IcAlertTri /></span>
            <h2 className="lib-section-title">Needs matching</h2>
            <div className="lib-section-meta">
              <span className="lib-section-count">{needsMatching.length}</span>
              <span>{needsMatching.length === 1 ? 'card' : 'cards'}</span>
              <span className="dot-sep" />
              <span>We couldn't find these in the metadata DBs — pick the right show manually</span>
            </div>
          </header>
          {renderSectionBody(
            needsMatching, 'anime',
            { selected, setSelected, focusedId, setFocusedId, anySelected,
              onOpenCover, onApprove, onReject, onManualSearch },
          )}
        </section>
      ) : null}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Franchise grouping within a section
// ─────────────────────────────────────────────────────────────────────

interface SectionCtx {
  selected: Set<string>;
  setSelected: (s: Set<string>) => void;
  focusedId: string;
  setFocusedId: (id: string) => void;
  anySelected: boolean;
  onOpenCover: (item: LibraryItem, coverEl: HTMLElement) => void;
  onApprove: (item: LibraryItem) => void;
  onReject: (item: LibraryItem) => void;
  onManualSearch: (item: LibraryItem) => void;
}

/**
 * Split a section's items into franchise blocks. Two or more items sharing
 * a non-null `seriesGroupId` form a labeled sub-group with a small heading
 * ("Rent-a-Girlfriend · 5 seasons"). Singletons keep their flat-grid look.
 *
 * Block order = order of first item with that groupId in the input array
 * (preserves whatever sort came in).
 */
function renderSectionBody(items: LibraryItem[], sectionKey: MediaType, ctx: SectionCtx) {
  type Block = { kind: 'group'; key: string; items: LibraryItem[] } | { kind: 'solo'; item: LibraryItem };

  const byGroup = new Map<string, LibraryItem[]>();
  items.forEach(it => {
    const gid = it.seriesGroupId;
    if (!gid) return;
    if (!byGroup.has(gid)) byGroup.set(gid, []);
    byGroup.get(gid)!.push(it);
  });

  // Partition: singles first, franchise groups second. Without this
  // partition, a section like Anime that interleaves single-card titles
  // with franchise blocks (Rent-a-Girlfriend · 5 seasons, Frieren · 2
  // seasons, etc.) renders as:
  //   [single, single]            ← flat grid
  //   [Rent-a-Girlfriend group]   ← bordered heading
  //   [single, single]            ← ANOTHER flat grid
  //   [Frieren group]             ← bordered heading
  //   [single, single]            ← yet another flat grid
  // The visual effect: standalone titles look "split above and below"
  // every franchise band, breaking the eye's scan flow. Grouping all
  // singles into one contiguous grid (rendered first) and putting all
  // franchise bands after keeps standalones visually unified and gives
  // franchise blocks a clean reading order at the bottom of the section.
  const soloBlocks: Block[] = [];
  const groupBlocks: Block[] = [];
  const consumed = new Set<string>();
  items.forEach(it => {
    if (consumed.has(it.id)) return;
    const gid = it.seriesGroupId;
    const members = gid ? byGroup.get(gid) : undefined;
    if (members && members.length >= 2) {
      groupBlocks.push({ kind: 'group', key: gid!, items: members });
      members.forEach(m => consumed.add(m.id));
    } else {
      soloBlocks.push({ kind: 'solo', item: it });
      consumed.add(it.id);
    }
  });
  // Singles first → groups after. Block order within each partition
  // preserves the input array's order (which carries the section's
  // sort: confidence-desc for Review, alphabetical for Library, etc.).
  const blocks: Block[] = [...soloBlocks, ...groupBlocks];

  const shape = sectionKey === 'music' ? 'shape-square' : '';

  // Singletons all render into one flat grid; groups render into their own
  // bordered grid with a heading. We collect contiguous singletons and
  // emit them as one grid so spacing stays consistent.
  const out: React.ReactNode[] = [];
  let bucket: LibraryItem[] = [];
  let cardIdx = 0;

  const flushBucket = () => {
    if (!bucket.length) return;
    out.push(
      <div key={`flat-${cardIdx}`} className={`lib-grid ${shape}`}>
        {bucket.map((item, i) => (
          <CoverCard
            key={item.id}
            item={item}
            index={cardIdx + i}
            selected={ctx.selected.has(item.id)}
            anySelected={ctx.anySelected}
            focused={ctx.focusedId === item.id}
            onSelect={(id) => {
              const next = new Set(ctx.selected);
              if (next.has(id)) next.delete(id); else next.add(id);
              ctx.setSelected(next);
            }}
            onOpen={(it, el) => { ctx.setFocusedId(it.id); ctx.onOpenCover(it, el); }}
            onApprove={ctx.onApprove}
            onReject={ctx.onReject}
            onManualSearch={ctx.onManualSearch}
          />
        ))}
      </div>
    );
    cardIdx += bucket.length;
    bucket = [];
  };

  blocks.forEach((b, bi) => {
    if (b.kind === 'solo') {
      bucket.push(b.item);
      return;
    }
    flushBucket();

    // Franchise heading: pick the canonical (first) item's title as the
    // franchise name. Strip the "(YYYY)" suffix if present so "Rent-a-
    // Girlfriend (2022)" doesn't look like a single season's title.
    // Sort by canonical season when available (Fribb cross-ref ground truth),
    // fall back to year for franchises whose seasons aren't catalogued.
    // Season 0 (Specials) sorts last so the regular run reads 1, 2, 3, … 0.
    const sorted = [...b.items].sort((a, b) => {
      const sa = typeof a.season === 'number' ? (a.season === 0 ? 9999 : a.season) : null;
      const sb = typeof b.season === 'number' ? (b.season === 0 ? 9999 : b.season) : null;
      if (sa !== null && sb !== null && sa !== sb) return sa - sb;
      return (a.year ?? 0) - (b.year ?? 0);
    });
    const earliest = sorted[0];
    const franchiseTitle = (earliest.title || '').replace(/\s*\(\d{4}\)\s*$/, '');
    const totalSeasons = b.items.length;
    const totalEpisodes = b.items.reduce((s, it) => s + (it.episodes?.length ?? 0), 0);
    const counter = totalEpisodes > 0
      ? `${pluralize(totalSeasons, 'season')} · ${pluralize(totalEpisodes, 'episode')}`
      : pluralize(totalSeasons, 'entry', 'entries');

    out.push(
      <div key={`franchise-${b.key}-${bi}`} className="lib-franchise-group">
        <header className="lib-franchise-head">
          <span className="lib-franchise-tick" />
          <h3 className="lib-franchise-title">{franchiseTitle}</h3>
          <span className="lib-franchise-meta">{counter}</span>
        </header>
        <div className={`lib-grid ${shape} lib-franchise-grid`}>
          {sorted.map((item, i) => (
            <CoverCard
              key={item.id}
              item={item}
              index={cardIdx + i}
              // Flag that the card sits inside a franchise group. The
              // CoverCard reads the canonical season from `item.season`
              // (provider ground truth, NOT a position-based heuristic).
              inFranchise
              selected={ctx.selected.has(item.id)}
              anySelected={ctx.anySelected}
              focused={ctx.focusedId === item.id}
              onSelect={(id) => {
                const next = new Set(ctx.selected);
                if (next.has(id)) next.delete(id); else next.add(id);
                ctx.setSelected(next);
              }}
              onOpen={(it, el) => { ctx.setFocusedId(it.id); ctx.onOpenCover(it, el); }}
              onApprove={ctx.onApprove}
              onReject={ctx.onReject}
              onManualSearch={ctx.onManualSearch}
            />
          ))}
        </div>
      </div>
    );
    cardIdx += b.items.length;
  });
  flushBucket();

  return <>{out}</>;
}

// Re-export for consumers that don't need to import MediaTypeIcon themselves
export { MediaTypeIcon };
