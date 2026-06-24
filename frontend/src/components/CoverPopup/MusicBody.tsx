import { useState, useEffect, type CSSProperties } from 'react';
import type { LibraryItem, LibEpisode, LibFile } from '../../lib/types';
import type { PairedRowShape } from './types';
import { MusicRow } from './rowsMusic';
import { SkeletonRow } from './rows';

interface MusicBodyProps {
  item: LibraryItem;
  rows: PairedRowShape[];
  updateFile: (idx: number, patch: Partial<LibFile>) => void;
  onManualSearch: (item: LibraryItem, episodeIdx?: number | null, fileIdx?: number | null) => void;
  onOpenDupeModal: (episode: LibEpisode, files: LibFile[]) => void;
  /** Skeleton placeholder while the track list is still resolving. */
  episodesLoading?: boolean;
}

// ─────────────────────────────────────────────────────────────────────
// MusicBody — the MUSIC-ONLY track list (sibling of SeriesBody). One scrolling
// list of MusicRow cells, each a track + the file filling it. Music has no
// Sonarr queue, no multi-season splits, and no huge missing-episode backlog, so
// this is the trimmed shell: the shared cx-list-* chrome + progressive render +
// a skeleton-loading state, nothing more.
// ─────────────────────────────────────────────────────────────────────
export function MusicBody({
  item, rows, updateFile, onManualSearch, onOpenDupeModal, episodesLoading,
}: MusicBodyProps) {
  // Progressive render — a Singles folder can be 30-40 tracks; mount a slice
  // first so the popup opens instantly, then grow it over frames. Never shrinks.
  const INITIAL_ROWS = 60;
  const ROW_STEP = 120;
  const [visibleCount, setVisibleCount] = useState(() => Math.min(rows.length, INITIAL_ROWS));
  useEffect(() => {
    setVisibleCount(c => Math.min(Math.max(c, INITIAL_ROWS), rows.length));
  }, [rows.length]);
  useEffect(() => {
    if (visibleCount >= rows.length) return;
    const id = requestAnimationFrame(() => setVisibleCount(c => Math.min(rows.length, c + ROW_STEP)));
    return () => cancelAnimationFrame(id);
  }, [visibleCount, rows.length]);
  const shownRows = visibleCount >= rows.length ? rows : rows.slice(0, visibleCount);

  const providerTag = item.providers?.musicbrainz ? ' · MusicBrainz' : '';

  return (
    <div className="cx-list">
      <div className="cx-list-head">
        <span className="cx-list-title">Tracklist</span>
        <span className="cx-list-meta">
          {item.files.length} {item.files.length === 1 ? 'file' : 'files'}
          {' · '}{item.episodes.length} {item.episodes.length === 1 ? 'track' : 'tracks'}{providerTag}
        </span>
      </div>
      <div
        className="cx-list-body"
        aria-busy={episodesLoading ? 'true' : undefined}
        aria-label={episodesLoading ? 'Loading tracks' : undefined}
        style={{ ['--pair-stagger' as never]: '0.022s' } as CSSProperties}
      >
        {episodesLoading
          ? Array.from({ length: 8 }).map((_, i) => <SkeletonRow key={`sk-${i}`} />)
          : shownRows.map((r, i) => (
              <MusicRow
                key={r.key}
                row={r}
                item={item}
                updateFile={updateFile}
                onManualSearch={onManualSearch}
                onOpenDupeModal={onOpenDupeModal}
                staggerIndex={i}
              />
            ))}
      </div>
    </div>
  );
}
