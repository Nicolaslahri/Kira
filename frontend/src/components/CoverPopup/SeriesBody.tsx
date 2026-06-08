import { useRef, useState, useEffect, useMemo, type UIEvent, type RefObject } from 'react';
import type { LibraryItem, LibEpisode, LibFile } from '../../lib/types';
import type { SonarrQueueEntry, PairedRowShape } from './types';
import { FileRowCell, EpisodeRowCell, SkeletonRow } from './rows';

interface SeriesBodyProps {
  item: LibraryItem;
  rows: PairedRowShape[];
  updateFile: (idx: number, patch: Partial<LibFile>) => void;
  onManualSearch: (item: LibraryItem, episodeIdx?: number | null, fileIdx?: number | null) => void;
  onOpenDupeModal: (episode: LibEpisode, files: LibFile[]) => void;
  /** PB-2: when true, render skeleton rows instead of blank columns. */
  episodesLoading?: boolean;
  /** Sonarr's in-flight downloads keyed by episode number. Drives the
   *  per-row "Downloading" / "Queued" / "Importing" progress UI in
   *  place of the static "No file for this episode" placeholder. Null
   *  when Sonarr isn't configured (or hasn't responded yet); the
   *  blank rows fall back to the regular static placeholder. */
  queueByEpisode?: Map<number, SonarrQueueEntry>;
  /** Episode numbers whose Sonarr download has finished and queue
   *  entry has vanished, but the file hasn't been picked up by a
   *  Kira scan yet. The blank row renders a "Just imported, scanning
   *  …" transitional placeholder instead of the static "No file"
   *  state during this window (~5 min, naturally cleared when the
   *  file appears or expires). */
  recentlyImported?: Map<number, number>;
  /** Toast handler threaded down to DownloadProgressRow so its
   *  "Force import" button can surface success/failure feedback. */
  pushToast?: (toast: { title: string; sub?: string; kind?: 'success' | 'error' }) => void;
}

export function SeriesBody({ item, rows, updateFile, onManualSearch, onOpenDupeModal, episodesLoading, queueByEpisode, recentlyImported, pushToast }: SeriesBodyProps) {
  const leftRef = useRef<HTMLDivElement>(null);
  const rightRef = useRef<HTMLDivElement>(null);
  const syncing = useRef(false);
  // PB-2: rAF-coalesce the scroll-sync write. The original wrote
  // scrollTop synchronously on every scroll event — at 120Hz that's
  // 240 forced reflows/sec across two columns. Coalescing into one
  // write per frame cuts paint work to display-refresh rate without
  // changing the visual sync feel.
  const rafIdRef = useRef<number | null>(null);

  // ── Adaptive missing-episode collapse (the One Piece "1090 blank rows") ──
  // A "blank" row is an episode with no file. For a normal season that's a
  // useful at-a-glance gap ("you're missing E07"). But a FLAT long-runner's
  // provider list is the WHOLE run — One Piece via AniDB is 1,100+ episodes —
  // so ~1,090 "Find a file" blanks bury the ~10 files you actually have, and
  // each one mounts a MarqueeText/ResizeObserver on the episode side. When the
  // blanks are numerous we hide them behind a "Show N missing" toggle in the
  // episode-column header; matched rows AND orphans (files you HAVE but
  // couldn't pair) always stay visible. Normal seasons (≤ threshold blanks)
  // are untouched — their gaps still render inline.
  const MISSING_COLLAPSE_THRESHOLD = 40;
  const blankCount = useMemo(
    () => rows.reduce((n, r) => n + (r.kind === 'blank' ? 1 : 0), 0),
    [rows],
  );
  const collapsibleMissing = blankCount > MISSING_COLLAPSE_THRESHOLD;
  const [showMissing, setShowMissing] = useState(false);
  // Reset the toggle only when the popup switches to a DIFFERENT cluster — not
  // on every file edit (delete/approve) — so the user's choice survives
  // in-popup mutations that re-derive `rows`.
  useEffect(() => { setShowMissing(false); }, [item.id]);
  const displayRows = useMemo(
    () => (collapsibleMissing && !showMissing ? rows.filter(r => r.kind !== 'blank') : rows),
    [rows, collapsibleMissing, showMissing],
  );

  // ── Progressive render for huge clusters (One Piece = 1000+ episodes) ──
  // Rendering the full row list × 2 columns synchronously on open froze the
  // popup for ~5s. Instead we mount a small initial slice (so the popup opens
  // instantly) and grow it over subsequent frames until everything is in the
  // DOM. We never shrink an already-expanded list, so per-row edits (status
  // toggles, renames) don't cause a flicker — this only engages on first
  // mount, when the provider's full episode list arrives and balloons `rows`,
  // and when the user reveals the collapsed missing episodes. Small series (≤
  // INITIAL) render fully on the first frame, exactly as before. Operates on
  // `displayRows` (post-collapse) so a hidden 1,090-blank tail never mounts.
  const INITIAL_ROWS = 60;
  const ROW_STEP = 120;
  const [visibleCount, setVisibleCount] = useState(() => Math.min(displayRows.length, INITIAL_ROWS));
  useEffect(() => {
    // Clamp to the new length without collapsing below the initial chunk.
    setVisibleCount(c => Math.min(Math.max(c, INITIAL_ROWS), displayRows.length));
  }, [displayRows.length]);
  useEffect(() => {
    if (visibleCount >= displayRows.length) return;
    const id = requestAnimationFrame(() => setVisibleCount(c => Math.min(displayRows.length, c + ROW_STEP)));
    return () => cancelAnimationFrame(id);
  }, [visibleCount, displayRows.length]);
  const shownRows = visibleCount >= displayRows.length ? displayRows : displayRows.slice(0, visibleCount);

  const onScroll = (e: UIEvent<HTMLDivElement>, otherRef: RefObject<HTMLDivElement | null>) => {
    if (syncing.current || !otherRef.current) return;
    const nextTop = (e.target as HTMLDivElement).scrollTop;
    if (rafIdRef.current != null) cancelAnimationFrame(rafIdRef.current);
    rafIdRef.current = requestAnimationFrame(() => {
      rafIdRef.current = null;
      const dst = otherRef.current;
      if (!dst) return;
      if (Math.abs(dst.scrollTop - nextTop) < 1) return; // already in sync
      syncing.current = true;
      dst.scrollTop = nextTop;
      // Release the echo guard on the NEXT frame so the dst's onScroll
      // event has dispatched + been swallowed by `syncing.current`.
      requestAnimationFrame(() => { syncing.current = false; });
    });
  };

  const leftLabel = 'Your files';
  const rightLabel = item.kind === 'album' ? 'Matched track' : 'Matched episode';
  const providerTag =
    item.providers?.tmdb ? ' · TMDB' :
    item.providers?.tvdb ? ' · TVDB' :
    item.providers?.anidb ? ' · AniDB' :
    item.providers?.musicbrainz ? ' · MusicBrainz' : '';

  return (
    <div className="cx-body">
      <div className="cx-col">
        <div className="cx-col-head left">
          <span>{leftLabel}</span>
          <span className="col-meta">{item.files.length} {item.files.length === 1 ? 'file' : 'files'}</span>
        </div>
        <div
          className="cx-col-body"
          ref={leftRef}
          onScroll={(e) => onScroll(e, rightRef)}
          aria-busy={episodesLoading ? 'true' : undefined}
          aria-label={episodesLoading ? 'Loading files' : undefined}
        >
          {episodesLoading
            ? Array.from({ length: 8 }).map((_, i) => <SkeletonRow key={`sk-l-${i}`} side="left" />)
            : shownRows.map(r => {
                // Look up queue state by the row's episode number so
                // both columns (file-side blank → progress bar, episode-
                // side details → "downloading" badge) stay in sync.
                const qEntry = r.episode && queueByEpisode
                  ? queueByEpisode.get(r.episode.episode) ?? null
                  : null;
                const justImported = r.episode && recentlyImported
                  ? recentlyImported.has(r.episode.episode)
                  : false;
                return (
                  <FileRowCell
                    key={r.key}
                    row={r}
                    item={item}
                    updateFile={updateFile}
                    onManualSearch={onManualSearch}
                    onOpenDupeModal={onOpenDupeModal}
                    queueEntry={qEntry}
                    justImported={justImported}
                    pushToast={pushToast}
                  />
                );
              })}
        </div>
      </div>

      <div className="cx-col">
        <div className="cx-col-head">
          <span>{rightLabel}</span>
          <span className="col-meta">
            {item.episodes.length} {item.kind === 'album' ? 'tracks' : 'episodes'}{providerTag}
            {collapsibleMissing ? (
              <>
                {' · '}
                <button
                  type="button"
                  className="cx-missing-toggle"
                  onClick={() => setShowMissing(s => !s)}
                  aria-expanded={showMissing}
                  title={showMissing
                    ? 'Hide the episodes you don’t have a file for'
                    : 'Render the episodes you’re missing (off by default on long-runners to keep the popup fast)'}
                >
                  {showMissing
                    ? 'Hide missing'
                    : `Show ${blankCount} missing`}
                </button>
              </>
            ) : null}
          </span>
        </div>
        <div
          className="cx-col-body"
          ref={rightRef}
          onScroll={(e) => onScroll(e, leftRef)}
          aria-busy={episodesLoading ? 'true' : undefined}
          aria-label={episodesLoading ? 'Loading episodes' : undefined}
        >
          {episodesLoading
            ? Array.from({ length: 8 }).map((_, i) => <SkeletonRow key={`sk-r-${i}`} side="right" />)
            : shownRows.map(r => {
                const qEntry = r.episode && queueByEpisode
                  ? queueByEpisode.get(r.episode.episode) ?? null
                  : null;
                const justImported = r.episode && recentlyImported
                  ? recentlyImported.has(r.episode.episode)
                  : false;
                return (
                  <EpisodeRowCell
                    key={r.key}
                    row={r}
                    item={item}
                    updateFile={updateFile}
                    onManualSearch={onManualSearch}
                    onOpenDupeModal={onOpenDupeModal}
                    queueEntry={qEntry}
                    justImported={justImported}
                  />
                );
              })}
        </div>
      </div>
    </div>
  );
}
