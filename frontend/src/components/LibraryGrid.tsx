// LibraryGrid — sectioned cover-grid view of the library.
// Ported from the design prototype (kira/project/src/grid.jsx).
// Owns: CoverCard (atomic unit), section headers, scan-in-progress floater,
//       empty / zero-result states.

import { useEffect, useMemo, useState } from 'react';
import type { LibraryItem, MediaType } from '../lib/types';
import { pluralize } from '../lib/format';
import {
  IcCheck, IcX, IcSearch, IcAlertTri, IcDownload,
  IcTv, IcAnime, IcFilm, IcMusic, IcDisc, IcReview,
} from '../lib/icons';
import { MediaTypeIcon } from './ui';
import { fetchAnidbPoster, getCachedAnidbPoster } from '../lib/posters';
import { api } from '../lib/api';
import { confLevel, getConfBands } from '../lib/confBands';

// ─────────────────────────────────────────────────────────────────────
// Sonarr live queue — library-grid scope
// ─────────────────────────────────────────────────────────────────────
//
// Polls /integrations/sonarr/queue every 12s while LibraryGrid is
// mounted (so the Dashboard / History / Settings tabs don't pay for
// Sonarr requests). Returns two maps so each CoverCard can find its
// items via either provider key:
//   * byTvdb  : tvdb_id → entries[]  (TVDB-direct cards)
//   * byAnidb : anidb_aid → entries[] (anime cards; backend reverse-
//               cross-refs via Fribb so AniDB-only cards get pills too)
//
// Stops polling on the first 4xx (Sonarr-not-configured) so we don't
// hammer a misconfigured endpoint. Stays paused until the next time
// the LibraryGrid remounts — user reopens Settings, configures Sonarr,
// navigates back to Review, fresh poll begins.

export interface QueueEntry {
  tvdb_id: number;
  anidb_aid: number | null;
  season: number;
  episode_number: number;
  episode_title: string | null;
  status: string;
  progress_pct: number;
  eta_seconds: number | null;
  size_bytes: number | null;
  size_left_bytes: number | null;
  release_title: string | null;
  protocol: string | null;
  error_message: string | null;
  download_client: string | null;
}

interface QueueMaps {
  byTvdb: Map<number, QueueEntry[]>;
  byAnidb: Map<number, QueueEntry[]>;
}

const EMPTY_MAPS: QueueMaps = { byTvdb: new Map(), byAnidb: new Map() };

function useSonarrQueueLibrary(): QueueMaps {
  const [maps, setMaps] = useState<QueueMaps>(EMPTY_MAPS);
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let errCount = 0;
    const tick = async () => {
      try {
        const r = await api.sonarrQueue();
        if (cancelled) return;
        const byTvdb = new Map<number, QueueEntry[]>();
        const byAnidb = new Map<number, QueueEntry[]>();
        for (const it of r.items) {
          const arrT = byTvdb.get(it.tvdb_id);
          if (arrT) arrT.push(it as QueueEntry); else byTvdb.set(it.tvdb_id, [it as QueueEntry]);
          if (it.anidb_aid != null) {
            const arrA = byAnidb.get(it.anidb_aid);
            if (arrA) arrA.push(it as QueueEntry); else byAnidb.set(it.anidb_aid, [it as QueueEntry]);
          }
        }
        setMaps({ byTvdb, byAnidb });
        errCount = 0;
        // 8s global poll for cover-card pills. Tight enough that "I
        // just clicked Get Missing → Sonarr in the popup and now I'm
        // back at the grid" shows the new pill within seconds, loose
        // enough that a 50-card library doesn't spam Sonarr. Sharing
        // the 1s backend cache with the popup's 2s poll keeps total
        // Sonarr load under 1 req/s in steady state.
        timer = setTimeout(tick, 8000);
      } catch (e) {
        if (cancelled) return;
        errCount += 1;
        const msg = String(e ?? '');
        // Sonarr not configured — don't keep retrying; the pill code
        // tolerates empty maps fine, the user just doesn't see pills.
        if (msg.includes('Sonarr URL') || msg.includes('Sonarr API key') || msg.includes('not configured')) {
          setMaps(EMPTY_MAPS);
          return;
        }
        // Transient — slow ramp-up, eventually give up.
        const delay = errCount <= 1 ? 12000 : errCount <= 3 ? 30000 : 60000;
        if (errCount > 6) return;
        timer = setTimeout(tick, delay);
      }
    };
    void tick();
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
  }, []);
  return maps;
}

/** Per-card summary computed once at render time. Drives the cover-
 *  card status pill: total in-flight count, the highest-priority
 *  status to label the pill, and the max progress across all entries
 *  (the "leading" download — most visually informative number).
 *
 *  Status priority order (worst-first, so the pill surfaces problems):
 *    failed > warning > downloading > importing > queued > searching > completed
 */
function summarizeQueue(entries: QueueEntry[]): {
  count: number;
  status: string;
  maxProgress: number;
} {
  if (entries.length === 0) return { count: 0, status: 'queued', maxProgress: 0 };
  const priority = ['failed', 'warning', 'downloading', 'importing', 'queued', 'searching', 'completed'];
  let bestStatus = 'queued';
  let bestRank = priority.length;
  let maxProgress = 0;
  for (const e of entries) {
    const idx = priority.indexOf(e.status);
    const rank = idx >= 0 ? idx : priority.length;
    if (rank < bestRank) { bestRank = rank; bestStatus = e.status; }
    if (e.progress_pct > maxProgress) maxProgress = e.progress_pct;
  }
  return { count: entries.length, status: bestStatus, maxProgress };
}

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
  return confLevel(v);
}


function ccStatChipColor(s: LibStats): string {
  const { high, mid } = getConfBands();
  if (s.cardState === 'approved' || s.avgConf >= high) return 'var(--conf-high)';
  if (s.cardState === 'rejected' || s.avgConf < mid) return 'var(--conf-low)';
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
  /** Pre-computed franchise display title (collision-aware — keeps the
   *  "(YYYY)" disambiguator only when two cours would read identically).
   *  When omitted, the card strips the year itself. */
  displayTitle?: string;
  onSelect: (id: string) => void;
  onOpen: (item: LibraryItem, coverEl: HTMLElement) => void;
  onApprove: (item: LibraryItem) => void;
  onReject: (item: LibraryItem) => void;
  onManualSearch: (item: LibraryItem) => void;
  /** Sonarr queue entries for this card's series (resolved via
   *  lookupQueue in the parent). Renders a status pill on the cover
   *  with "Downloading N · 47%" / "Queued N" / "Failed" etc. when
   *  non-empty. Empty when Sonarr is offline, not configured, or no
   *  in-flight downloads exist for this series. */
  sonarrQueue?: QueueEntry[];
}

export function CoverCard({
  item, selected, anySelected, focused, index, inFranchise, displayTitle,
  onSelect, onOpen, onApprove, onReject, onManualSearch, sonarrQueue,
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
  // Any poster that fails to load (404, dead host) falls back to the initials
  // card instead of a blank gradient. Reset when the URL changes so a fresh
  // poster gets a fair retry.
  const [imgFailed, setImgFailed] = useState(false);
  useEffect(() => { setImgFailed(false); }, [effectivePosterUrl]);
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
    if (target.closest('.cc-act, .cc-select, .cc-no-match-cta, .cc-nm-search-link')) return;
    const coverEl = e.currentTarget.querySelector('.cc-cover');
    if (coverEl instanceof HTMLElement) onOpen(item, coverEl);
  };

  // Keyboard access for the card itself (it's role="button"). Only act when
  // focus is on the card, not an inner control — Enter/Space on a nested
  // action button must run THAT button, not also expand the card.
  const handleCardKey = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.target !== e.currentTarget) return;
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      const coverEl = e.currentTarget.querySelector('.cc-cover');
      if (coverEl instanceof HTMLElement) onOpen(item, coverEl);
    }
  };

  const cardClass = [
    'cc',
    'cinema',  // CodePen-inspired hover-reveal cover treatment (see .cc.cinema in index.css)
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
      onKeyDown={handleCardKey}
      role="button"
      tabIndex={0}
      aria-label={`Open ${item.title || 'file'}${item.year ? `, ${item.year}` : ''}`}
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
          {effectivePosterUrl && !imgFailed ? (
            <img
              src={effectivePosterUrl}
              alt=""
              loading="lazy"
              referrerPolicy="no-referrer"
              onError={() => setImgFailed(true)}
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

          {/* Quick actions moved into the caption's bottom row (see
              .cc-bottom-row below) so they sit beside the season/episode
              sub-line instead of overlapping it. */}

          {/* Sonarr live activity pill (bottom-left, in front of cover
              art). Renders only when there's at least one in-flight
              download for this series + season. Visible above the
              cover art (z-index above the img); doesn't shift on
              hover so the user can read it without moving the mouse. */}
          {sonarrQueue && sonarrQueue.length > 0 ? (
            <CardSonarrPill entries={sonarrQueue} />
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
                // Inside a franchise group, use the block's collision-aware
                // display title: the heading already names the show, so the
                // trailing "(YYYY)" is dropped — UNLESS keeping it is the only
                // thing that distinguishes two cours of the same season
                // (Attack on Titan "Season 3" vs "Season 3 (2019)").
                ? (displayTitle ?? (item.title || '').replace(/\s*\(\d{4}\)\s*$/, ''))
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
          {/* Quick actions — right side of the caption row (sub-info on the
              left, actions on the right via space-between), so they never
              overlap the season/episode text. Revealed with the row on hover. */}
          {!item.noMatch && !item.matchingState ? (
            <div className="cc-actions">
              <button
                className="cc-act approve"
                onClick={(e) => { e.stopPropagation(); onApprove(item); }}
                title="Approve all matched"
                aria-label={`Approve all matched for ${item.title || 'this title'}`}
              ><IcCheck /></button>
              <button
                className="cc-act reject"
                onClick={(e) => { e.stopPropagation(); onReject(item); }}
                title="Reject all"
                aria-label={`Reject all for ${item.title || 'this title'}`}
              ><IcX /></button>
              <button
                className="cc-act"
                onClick={(e) => { e.stopPropagation(); onManualSearch(item); }}
                title="Search manually"
                aria-label={`Search manually for ${item.title || 'this file'}`}
              ><IcSearch /></button>
            </div>
          ) : null}
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
  // Live Sonarr queue — polled while this grid is mounted. Each card
  // gets its slice via the byTvdb / byAnidb lookups below.
  const sonarrQueueMaps = useSonarrQueueLibrary();
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
                <a className="step-link" href="#/settings/paths">Open Paths settings →</a>
              </div>
            </li>
            <li>
              <span className="step-num">2</span>
              <div>
                <strong>Add a TMDB API key</strong> <span className="step-meta">(free, takes 60s)</span>
                <a className="step-link" href="#/settings/connections">Open Providers settings →</a>
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
                onOpenCover, onApprove, onReject, onManualSearch,
                sonarrQueueMaps },
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
              onOpenCover, onApprove, onReject, onManualSearch, sonarrQueueMaps },
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
  /** Live Sonarr queue maps — `useSonarrQueueLibrary()` output. Each
   *  CoverCard render site uses `lookupQueue(item)` below to pull its
   *  own slice. Keeping the maps on ctx avoids passing them through
   *  every intermediate prop while still scoping lookup to the grid. */
  sonarrQueueMaps: QueueMaps;
}

/** Resolve a card's Sonarr queue entries.
 *
 *  Looks up by `item.providers.tvdb` first (most accurate); falls back
 *  to `item.providers.anidb` for AniDB-only cards (the backend reverse-
 *  cross-refs via Fribb so anime cards still get a hit).
 *
 *  CRITICAL FILTER: entries for episodes the user already has a file
 *  for are SKIPPED. These are stale Sonarr queue records — old failed
 *  downloads, manual cleanups, or imports that succeeded outside
 *  Sonarr. Counting them on the cover pill gave us misleading badges
 *  like "30 warnings" on AoT Final Season when every episode was
 *  actually present and matched at 100%. The popup already correctly
 *  ignores these (FileRowCellImpl renders the matched-file row, not
 *  the queue progress row, when both exist); the pill needs to
 *  match that semantic.
 *
 *  Returns an empty array when neither provider id is present OR when
 *  Sonarr isn't configured (maps are empty). The CoverCard renders no
 *  pill in either case — UI silently degrades.
 */
function lookupQueue(item: LibraryItem, maps: QueueMaps): QueueEntry[] {
  let entries: QueueEntry[] = [];

  const tvdbId = item.providers?.tvdb;
  if (tvdbId != null) {
    const hits = maps.byTvdb.get(Number(tvdbId));
    if (hits && hits.length) {
      // Card represents ONE season for TVDB/TMDB providers; filter to
      // matching season so a series-wide queue doesn't paint the same
      // pill on every season card.
      if (typeof item.season === 'number') {
        const seasonHits = hits.filter(h => h.season === item.season);
        if (seasonHits.length) entries = seasonHits;
        else entries = hits;
      } else {
        entries = hits;
      }
    }
  }
  if (entries.length === 0) {
    const anidbAid = item.providers?.anidb;
    if (anidbAid != null) {
      const hits = maps.byAnidb.get(Number(anidbAid));
      if (hits && hits.length) entries = hits;
    }
  }

  // Drop queue entries for episodes the user already has. Match on
  // episode_number alone (not season+episode), same dedup rule the
  // popup uses for the same reason: AniDB-native lists report
  // season=1 for everything while the file's recorded season comes
  // from the matcher's cross-ref. Episode numbers ARE consistent.
  if (entries.length > 0 && item.files.length > 0) {
    const haveEpisodeNums = new Set<number>();
    for (const f of item.files) {
      if (typeof f.matchedToEpisode !== 'number') continue;
      const merged = item.episodes[f.matchedToEpisode];
      if (merged && typeof merged.episode === 'number') {
        haveEpisodeNums.add(merged.episode);
      }
    }
    if (haveEpisodeNums.size > 0) {
      entries = entries.filter(e => !haveEpisodeNums.has(e.episode_number));
    }
  }

  return entries;
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
            sonarrQueue={lookupQueue(item, ctx.sonarrQueueMaps)}
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
    // Title helpers — shared by the sort tiebreak AND the per-card label below.
    const stripYear = (t: string) => (t || '').replace(/\s*\(\d{4}\)\s*$/, '');
    const hasYear = (t: string) => /\(\d{4}\)\s*$/.test(t || '');
    // Year embedded in the title ("(2019)"); bare (no year) → 0 = earliest,
    // matching AniDB's convention of leaving the FIRST cour of a season unyeared.
    const titleYear = (t: string) => {
      const m = (t || '').match(/\((\d{4})\)\s*$/);
      return m ? parseInt(m[1], 10) : 0;
    };
    const sorted = [...b.items].sort((a, b) => {
      const sa = typeof a.season === 'number' ? (a.season === 0 ? 9999 : a.season) : null;
      const sb = typeof b.season === 'number' ? (b.season === 0 ? 9999 : b.season) : null;
      if (sa !== null && sb !== null && sa !== sb) return sa - sb;
      // Tiebreak by year — the item's own year, else a year embedded in the
      // title ("(2019)"); bare titles sort first. Keeps cards in the same
      // chronological order as their "Part N" labels.
      const ya = (a.year ?? 0) || titleYear(a.title || '');
      const yb = (b.year ?? 0) || titleYear(b.title || '');
      if (ya !== yb) return ya - yb;
      return (a.title || '').localeCompare(b.title || '');
    });
    const earliest = sorted[0];
    // #14: movie collections name the band by the TMDB collection ("The Matrix
    // Collection") rather than the earliest film's bare title. Falls back to
    // the earliest title (anime franchises / collections without a name).
    const collectionLabel = sorted.find(it => it.collectionName)?.collectionName;
    const franchiseTitle = collectionLabel
      || (earliest.title || '').replace(/\s*\(\d{4}\)\s*$/, '');

    // Per-card title disambiguation. Inside a franchise group we drop the
    // trailing "(YYYY)" (the heading already names the show). The wrinkle:
    // AniDB tags only the LATER cours of a split season with a year and leaves
    // the first bare — so a group reads "The Final Season", "…(2022)",
    // "…(2023)": some cards with years, some without. That's the inconsistency.
    //
    // Rule (uniform distinguisher per collision group):
    //   - unique base name        → drop the year, show the bare name
    //   - collision, ALL have years → keep them (already consistent)
    //   - collision, mixed/none    → relabel the whole group "<base> Part N"
    //                                in chronological order (bare/earliest = 1)
    // So "Season 3" + "Season 3 (2019)" → "… Season 3 Part 1" + "Part 2", and
    // the three Final Season cours → "… The Final Season Part 1/2/3".
    // (stripYear / hasYear / titleYear are defined above, by the sort.)
    const byBase = new Map<string, LibraryItem[]>();
    for (const it of sorted) {
      const base = stripYear(it.title || '');
      const arr = byBase.get(base);
      if (arr) arr.push(it); else byBase.set(base, [it]);
    }
    const titleById = new Map<string, string>();
    for (const [base, members] of byBase) {
      if (members.length <= 1) {
        titleById.set(members[0].id, base);
      } else if (members.every(m => hasYear(m.title || ''))) {
        for (const m of members) titleById.set(m.id, m.title || base);
      } else {
        [...members]
          .sort((a, b) => titleYear(a.title || '') - titleYear(b.title || ''))
          .forEach((m, i) => titleById.set(m.id, `${base} Part ${i + 1}`));
      }
    }
    const displayTitleFor = (it: LibraryItem): string =>
      titleById.get(it.id) ?? stripYear(it.title || '');
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
              // Collision-aware title: keeps the "(YYYY)" only when two
              // cours would otherwise read identically.
              displayTitle={displayTitleFor(item)}
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
              sonarrQueue={lookupQueue(item, ctx.sonarrQueueMaps)}
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

// ─────────────────────────────────────────────────────────────────────
// CardSonarrPill — bottom-left pill on the cover when Sonarr is
// actively working on episodes for this series + season.
// ─────────────────────────────────────────────────────────────────────

// Per-status explanations surfaced on hover so the user doesn't have to
// guess what "Importing" / "Queued" / "Searching" actually mean. The
// status-word UI is intentionally short for the small pill footprint;
// the tooltip carries the full explanation.
const SONARR_STATUS_EXPLAIN: Record<string, string> = {
  queued:
    'Sonarr handed the download to your download client (sabnzbd / qBittorrent / etc.) and is waiting for it to start.',
  searching:
    'Sonarr is searching indexers for a usable release. Usually resolves in a few seconds.',
  downloading:
    'Bytes are flowing. Open the popup to see live percentage and ETA.',
  importing:
    "Download finished. Sonarr is moving the file from your download client's completed folder into your media library. Usually under 30 seconds.",
  completed:
    'Just imported. The queue entry will disappear shortly and Kira will rescan to pick up the new file.',
  failed:
    "Sonarr couldn't complete the download. Check Sonarr's UI for the specific error.",
  warning:
    'Sonarr flagged a concern with this download (quality / indexer / import policy). Check Sonarr for details.',
};

function CardSonarrPill({ entries }: { entries: QueueEntry[] }) {
  const { count, status, maxProgress } = useMemo(() => summarizeQueue(entries), [entries]);
  if (count === 0) return null;

  // Label varies by status — "Downloading 3 · 47%" reads cleaner than
  // "Downloading 3 of 9 · 47%" for the small pill footprint. The
  // popup gives the precise breakdown when the user clicks through.
  let label: string;
  if (status === 'downloading' && maxProgress > 0) {
    label = `Downloading ${count} · ${maxProgress.toFixed(0)}%`;
  } else if (status === 'failed') {
    label = count === 1 ? 'Failed' : `${count} failed`;
  } else if (status === 'warning') {
    label = count === 1 ? 'Warning' : `${count} warnings`;
  } else if (status === 'importing') {
    label = count === 1 ? 'Importing' : `Importing ${count}`;
  } else if (status === 'searching') {
    label = count === 1 ? 'Searching' : `Searching ${count}`;
  } else if (status === 'completed') {
    label = count === 1 ? 'Imported' : `${count} imported`;
  } else {
    // queued / fallback
    label = count === 1 ? 'Queued' : `${count} queued`;
  }

  // Compose the tooltip: status meaning first (the "what is this?"
  // answer), then the specifics of what Sonarr is grabbing. Title
  // attribute is multi-line via \n so native tooltip rendering
  // (which respects newlines on most platforms) shows both.
  const explanation = SONARR_STATUS_EXPLAIN[status]
    ?? 'Sonarr is processing this series.';
  const specifics = entries.length === 1 && entries[0].release_title
    ? `\n\nRelease: ${entries[0].release_title}`
    : count > 1
      ? `\n\n${count} downloads in flight for this series.`
      : '';
  const errorNote = entries.find(e => e.error_message)?.error_message;
  const errorLine = errorNote ? `\n\n⚠ ${errorNote}` : '';

  return (
    <span
      className={`cc-sonarr-pill cc-sonarr-${status}`}
      title={`${explanation}${specifics}${errorLine}`}
    >
      <IcDownload />
      <span className="cc-sonarr-pill-text">{label}</span>
    </span>
  );
}

// Re-export for consumers that don't need to import MediaTypeIcon themselves
export { MediaTypeIcon };
