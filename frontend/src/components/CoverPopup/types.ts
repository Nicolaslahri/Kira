import type { LibEpisode, LibFile } from '../../lib/types';

// Shared CoverPopup types — extracted so the main orchestrator, the
// SeriesBody, and the row cells can all reference them without circular
// imports through the 4k-line parent.

// Live Sonarr queue item, as seen by the popup. Mirrors the backend
// QueueItemOut shape.
export interface SonarrQueueEntry {
  tvdb_id: number;
  /** Reverse Fribb cross-ref — populated for anime queue items only. */
  anidb_aid?: number | null;
  season: number;
  episode_number: number;
  episode_title: string | null;
  status: string;            // see normalized states in backend
  progress_pct: number;      // 0..100
  eta_seconds: number | null;
  size_bytes: number | null;
  size_left_bytes: number | null;
  release_title: string | null;
  protocol: string | null;
  error_message: string | null;
  download_client: string | null;
  /** Sonarr's queue.id (numeric) and downloadId (string). We pass
   *  `download_id` back to the retry-import endpoint when the user
   *  clicks "Force import" on a stuck entry. */
  queue_id?: number | null;
  download_id?: string | null;
  /** True when Sonarr is stuck on "Downloaded - Unable to Import
   *  Automatically". Renders a distinct "Stuck — manual import
   *  needed" banner with a one-click fix button in the popup row. */
  needs_manual_import?: boolean;
}

// One row in the series/album synced-scroll body: a parsed episode slot
// paired with the file (if any) that matched it.
export interface PairedRowShape {
  key: string;
  kind: 'blank' | 'single' | 'dupe-primary' | 'orphan';
  episode: LibEpisode | null;
  episodeIdx: number | null;
  file: LibFile | undefined;
  dupeAll?: LibFile[];
}
