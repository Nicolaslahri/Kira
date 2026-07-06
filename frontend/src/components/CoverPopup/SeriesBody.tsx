import { useState, useEffect, useMemo, useCallback, type CSSProperties } from 'react';
import type { LibraryItem, LibEpisode, LibFile } from '../../lib/types';
import type { SonarrQueueEntry, PairedRowShape } from './types';
import { PairRowCell, SkeletonRow } from './rows';

interface SeriesBodyProps {
  /** One-click candidate switch, threaded down to each episode row. */
  onPickCandidate?: (fileId: string, candidate: { matchId?: number; title?: string; year?: number | null }) => void | Promise<void>;
  item: LibraryItem;
  rows: PairedRowShape[];
  updateFile: (idx: number, patch: Partial<LibFile>) => void;
  onManualSearch: (item: LibraryItem, episodeIdx?: number | null, fileIdx?: number | null) => void;
  onOpenDupeModal: (episode: LibEpisode, files: LibFile[]) => void;
  /** PB-2: when true, render skeleton rows instead of blank list. */
  episodesLoading?: boolean;
  /** Sonarr's in-flight downloads keyed by episode number — drives the
   *  per-row "Downloading" / "Queued" / "Importing" progress UI on the
   *  missing-episode rows. Null when Sonarr isn't configured. */
  queueByEpisode?: Map<number, SonarrQueueEntry>;
  /** Episode numbers whose Sonarr download finished but the file hasn't
   *  been picked up by a Kira scan yet → "Just imported, scanning…" state. */
  recentlyImported?: Map<number, number>;
  /** Toast handler threaded down to DownloadProgressRow's "Force import". */
  pushToast?: (toast: { title: string; sub?: string; kind?: 'success' | 'error' }) => void;
  /** Request a single missing episode from Sonarr (background search) — set
   *  only for Sonarr-eligible series; threaded to each missing-episode row. */
  onRequestEpisode?: (episode: number) => void;
}

// ─────────────────────────────────────────────────────────────────────
// SeriesBody — ONE unified scrolling list of pairing rows. Each row IS a
// pairing (episode + the file that fills it / a missing-episode state /
// an orphan file). The old two-column "files | episodes" synced-scroll
// layout is gone; PairRowCell renders the whole pairing in one connected
// row, so there's nothing left to sync.
//
// Preserved behavior:
//   • Adaptive missing-episode collapse on long-runners (One Piece).
//   • Progressive render so a 1000-row cluster opens instantly.
//   • Skeleton rows during the provider episode-list fetch.
//   • Sonarr queue / recently-imported lookups per row.
//   • Section dividers when a cluster genuinely spans >1 season.
// ─────────────────────────────────────────────────────────────────────
export function SeriesBody({
  item, rows, updateFile, onManualSearch, onOpenDupeModal,
  episodesLoading, queueByEpisode, recentlyImported, pushToast, onRequestEpisode,
  onPickCandidate,
}: SeriesBodyProps) {
  // ── Adaptive missing-episode collapse (the One Piece "1090 blank rows") ──
  // A "blank" row is an episode with no file. For a normal season that's a
  // useful at-a-glance gap. But a flat long-runner's provider list is the
  // WHOLE run (1,100+ episodes), so ~1,090 "No file" blanks bury the ~10
  // files you actually have. When the blanks are numerous we hide them
  // behind a "Show N missing" toggle in the list header; matched rows AND
  // orphans always stay visible. Normal seasons (≤ threshold) are untouched.
  const MISSING_COLLAPSE_THRESHOLD = 40;
  const blankCount = useMemo(
    () => rows.reduce((n, r) => n + (r.kind === 'blank' ? 1 : 0), 0),
    [rows],
  );
  const collapsibleMissing = blankCount > MISSING_COLLAPSE_THRESHOLD;
  const [showMissing, setShowMissing] = useState(false);
  // Reset the toggle only when the popup switches to a DIFFERENT cluster.
  useEffect(() => { setShowMissing(false); }, [item.id]);

  // "Upcoming" episodes — the ones just AFTER your latest owned episode (the
  // next to grab / newly aired) — stay VISIBLE even when collapsed; only the
  // older missing BACKLOG hides behind the toggle. Window-bounded so a
  // mid-binge library (ep 1-100 of 1,100) shows the next dozen, not 1,000 rows.
  const UPCOMING_WINDOW = 12;
  const maxOwnedEp = useMemo(() => {
    let mx = 0;
    for (const r of rows) {
      const e = r.kind !== 'blank' && r.episode ? r.episode.episode : null;
      if (typeof e === 'number' && e > mx) mx = e;
    }
    return mx;
  }, [rows]);
  const isUpcomingBlank = useCallback(
    (r: PairedRowShape) =>
      r.kind === 'blank' && r.episode != null &&
      typeof r.episode.episode === 'number' &&
      r.episode.episode > maxOwnedEp &&
      r.episode.episode <= maxOwnedEp + UPCOMING_WINDOW,
    [maxOwnedEp],
  );
  const displayRows = useMemo(
    () => (collapsibleMissing && !showMissing
      ? rows.filter(r => r.kind !== 'blank' || isUpcomingBlank(r))
      : rows),
    [rows, collapsibleMissing, showMissing, isUpcomingBlank],
  );
  // The toggle reveals the still-hidden BACKLOG (upcoming blanks already show).
  const hiddenMissingCount = useMemo(
    () => rows.reduce((n, r) => n + (r.kind === 'blank' && !isUpcomingBlank(r) ? 1 : 0), 0),
    [rows, isUpcomingBlank],
  );

  // ── Progressive render for huge clusters ──
  // Mount a small initial slice (so the popup opens instantly) and grow it
  // over subsequent frames until everything is in the DOM. Never shrinks an
  // already-expanded list, so per-row edits don't flicker.
  const INITIAL_ROWS = 60;
  const ROW_STEP = 120;
  const [visibleCount, setVisibleCount] = useState(() => Math.min(displayRows.length, INITIAL_ROWS));
  useEffect(() => {
    setVisibleCount(c => Math.min(Math.max(c, INITIAL_ROWS), displayRows.length));
  }, [displayRows.length]);
  useEffect(() => {
    if (visibleCount >= displayRows.length) return;
    const id = requestAnimationFrame(() => setVisibleCount(c => Math.min(displayRows.length, c + ROW_STEP)));
    return () => cancelAnimationFrame(id);
  }, [visibleCount, displayRows.length]);
  const shownRows = visibleCount >= displayRows.length ? displayRows : displayRows.slice(0, visibleCount);

  // ── Section grouping ──
  // A cluster is normally a single season's worth of episodes (the popup
  // fetches one season), so the header reads as one band. But when a
  // cluster genuinely spans more than one season number, split the list
  // into "Season N" sections so the run reads in chapters. Orphans (no
  // season) gather under a trailing "Unmatched files" divider.
  const distinctSeasons = useMemo(() => {
    const s = new Set<number>();
    for (const r of rows) if (r.episode) s.add(r.episode.season);
    return s;
  }, [rows]);
  const multiSeason = distinctSeasons.size > 1 && item.mediaType !== 'anime';

  const listHeaderRight = (() => {
    const noun = item.kind === 'album' ? 'tracks' : 'episodes';
    const providerTag =
      item.providers?.tmdb ? ' · TMDB' :
      item.providers?.tvdb ? ' · TVDB' :
      item.providers?.anidb ? ' · AniDB' :
      item.providers?.musicbrainz ? ' · MusicBrainz' : '';
    return (
      <span className="cx-list-meta">
        {item.files.length} {item.files.length === 1 ? 'file' : 'files'}
        {' · '}{item.episodes.length} {noun}{providerTag}
        {collapsibleMissing ? (
          <>
            {' · '}
            <button
              type="button"
              className="cx-missing-toggle"
              onClick={() => setShowMissing(s => !s)}
              aria-expanded={showMissing}
              title={showMissing
                ? 'Hide the older episodes you don’t have a file for'
                : 'Show the older missing episodes (upcoming ones are always shown; the backlog is hidden on long-runners to keep the popup fast)'}
            >
              {showMissing ? 'Hide missing' : `Show ${hiddenMissingCount} missing`}
            </button>
          </>
        ) : null}
      </span>
    );
  })();

  // Walk the shown rows, emitting a sticky section divider whenever the
  // season changes (only when the cluster is genuinely multi-season). The
  // stagger index drives the first-paint cascade on the visible rows.
  const elements: React.ReactNode[] = [];
  let lastSeason: number | null = null;
  let lastWasOrphan = false;
  shownRows.forEach((r, i) => {
    if (multiSeason) {
      if (r.episode && r.episode.season !== lastSeason) {
        lastSeason = r.episode.season;
        lastWasOrphan = false;
        elements.push(
          <div key={`sec-s${lastSeason}`} className="cx-list-section">
            Season {lastSeason}
          </div>,
        );
      } else if (!r.episode && !lastWasOrphan) {
        lastWasOrphan = true;
        elements.push(
          <div key="sec-orphans" className="cx-list-section orphan-section">
            Unmatched files
          </div>,
        );
      }
    }
    const qEntry = r.episode && queueByEpisode ? queueByEpisode.get(r.episode.episode) ?? null : null;
    const justImported = r.episode && recentlyImported ? recentlyImported.has(r.episode.episode) : false;
    elements.push(
      <PairRowCell
        key={r.key}
        row={r}
        item={item}
        updateFile={updateFile}
        onManualSearch={onManualSearch}
        onOpenDupeModal={onOpenDupeModal}
        queueEntry={qEntry}
        justImported={justImported}
        pushToast={pushToast}
        onRequestEpisode={onRequestEpisode}
        staggerIndex={i}
        onPickCandidate={onPickCandidate}
      />,
    );
  });

  return (
    <div className="cx-list">
      <div className="cx-list-head">
        <span className="cx-list-title">{item.kind === 'album' ? 'Track pairings' : 'Episode pairings'}</span>
        {listHeaderRight}
      </div>
      <div
        className="cx-list-body"
        aria-busy={episodesLoading ? 'true' : undefined}
        aria-label={episodesLoading ? 'Loading episodes' : undefined}
        style={{ ['--pair-stagger' as never]: '0.022s' } as CSSProperties}
      >
        {episodesLoading
          ? Array.from({ length: 8 }).map((_, i) => <SkeletonRow key={`sk-${i}`} />)
          : elements}
      </div>
    </div>
  );
}
