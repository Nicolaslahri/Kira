export type MediaType = 'movie' | 'tv' | 'anime' | 'music';

export interface PosterData {
  init: string;
  tint: [string, string];
  year: number | null;
}

export interface MatchData {
  // Movie / TV / Anime common
  title?: string;
  year?: number | null;
  /** Provider that produced this match ('tmdb' | 'tvdb' | 'anidb' | 'musicbrainz').
   *  Combined with `providerId`, this is the ground-truth identity used for
   *  series-card clustering — beats parser heuristics. */
  provider?: string;
  providerId?: string;
  /** Backend Match row id — the server-side handle cluster actions need
   *  (Sonarr "send missing", future NFO writers). Null for synthesized
   *  matches built from parsed data when no provider hit. */
  matchId?: number | null;
  /** Franchise identity for visual grouping on the Review page.
   *  Cards sharing this value sit together under a sub-heading. */
  seriesGroupId?: string;
  /** #14: TMDB movie-collection display name, when the film belongs to one. */
  collectionName?: string | null;
  tmdbId?: number | null;
  runtime?: number;
  poster?: PosterData;
  posterUrl?: string | null;   // real cover from TMDB/TVDB; falls back to `poster` gradient
  overview?: string;
  season?: number;
  episode?: number;
  episodeTitle?: string;

  // Anime
  titleRomaji?: string;
  titleNative?: string;
  altTitles?: string[];
  anidbId?: number;
  absoluteEpisode?: number;

  // Rich popup metadata — hydrated from Match.metadata_blob by the
  // adapter. Every field optional; UI renders only what's present.
  genres?: string[];
  cast?: string[];
  director?: string;
  network?: string;
  studio?: string;
  label?: string;
  language?: string;
  country?: string;
  /** Pre-formatted "2022 – 2024" or "2022 –" — set on series only. */
  yearRange?: string;

  // Music
  artist?: string;
  album?: string;
  albumYear?: number;
  track?: number;
  trackTitle?: string;
  totalTracks?: number;
  duration?: string;
  mbid?: string;
  acoustidMatch?: boolean;
  acoustidConfidence?: number;
  art?: PosterData;
  genre?: string;
}

export interface CandidateData {
  // Movie / TV / Anime
  matchId?: number;            // backend Match row id — used by POST /files/{id}/select/{matchId}
  title?: string;
  year?: number | null;
  confidence: number;
  poster?: PosterData;
  posterUrl?: string | null;
  season?: number;
  episode?: number;
  absoluteEpisode?: number;

  // Music
  artist?: string;
  album?: string;
  track?: number;
  trackTitle?: string;
  art?: PosterData;
}

export interface MediaFile {
  id: string;
  filename: string;
  folder: string;
  mediaType: MediaType;
  // 'matching' = backend is currently fetching candidates for this file.
  // Drives the per-row shimmer; reverts to 'pending' once a Match row exists
  // (or to 'no_match' if matching returned nothing).
  // 'renamed' = file was successfully moved/hardlinked to the library
  // root. Without this state in the union, the adapter's status-mapping
  // fell through to 'pending' and the file kept appearing in the
  // pending queue forever — even after a successful rename.
  status: 'pending' | 'matching' | 'approved' | 'rejected' | 'no_match' | 'renamed';
  confidence: number;
  match: MatchData | null;
  candidates: CandidateData[];
  releaseGroup?: string;
  /** Format-strip findings — surfaced so the FileRow can show "1080p" /
   *  "WEB-DL" / "x265" tags. Critical when two release groups of the
   *  same episode end up on the same row pair and the user needs to
   *  tell them apart at a glance. */
  quality?: string;
  source?: string;
  codec?: string;
  /** Pre-formatted size string ("3.4 GB"). Backend stores raw bytes;
   *  adapter formats via humanSize() before reaching the UI. */
  size?: string;
  /** Raw size in bytes — needed by the dedupe ranker as a tie-breaker. */
  sizeBytes?: number;
  /** Normalized "10bit" / "8bit" from the format-stripper. */
  bitDepth?: string;
  /** MediaInfo-derived (opt-in `parsing.read_mediainfo`): HDR flavor, speaker
   *  layout, primary audio codec(s). Surfaced as chips + used by the ranker. */
  hdr?: string;
  channels?: string;
  audio?: string[];
  /** Per-track languages (ISO-639-2/B) read from the container → dual-audio /
   *  multi-sub chips. */
  audio_langs?: string[];
  sub_langs?: string[];
  /** The clean title the parser extracted from the filename — e.g.
   *  "Kanojo, Okarishimasu" from "[Moozzi2] Kanojo, Okarishimasu-01.mkv".
   *  Used as the Manual Search seed because the *current* match.title is
   *  what the user is trying to replace. */
  parsedTitle?: string;
  /** Clustering key from the backend. Files sharing this become one SeriesCard.
   *  Null for movies and any file we couldn't cluster (e.g. parser missed the title). */
  seriesKey?: string | null;
}

export interface SearchResult {
  title?: string;
  titleRomaji?: string;
  year?: number | null;
  mediaType?: MediaType;
  poster?: PosterData;
  art?: PosterData;
  overview?: string;
  votes?: number;
  /** Alternate titles for disambiguation in Manual Search (anime romaji,
   *  TVDB language variants, TMDB original_name when distinct). */
  aliases?: string[] | null;

  // Provider-specific extras
  tmdbId?: number;
  tvdbId?: number;
  anidbId?: number;
  mbid?: string;
  eps?: number;
  studio?: string;
  artist?: string;
  album?: string;
  tracks?: number;
}

export type ProviderKey = 'TMDB' | 'TVDB' | 'AniDB' | 'MusicBrainz' | 'AcoustID' | 'fanart.tv';

export interface ProviderMeta {
  name: string;
  for: MediaType[];
  color: string;
  icon: 'film' | 'tv' | 'anime' | 'disc' | 'waveform';
  desc: string;
}

export interface NamingProfile {
  movie: string;
  tv: string;
  anime: string;
  music: string;
}

export interface ContentTypes {
  movies: boolean;
  tv: boolean;
  anime: boolean;
  music: boolean;
}

export interface ToastData {
  id: string;
  title: string;
  sub?: string;
  kind?: 'success' | 'error';
}

export interface AppState {
  files: MediaFile[];
  scanRunning: boolean;
  scanProgress: number;
  scanFound: number;
  scanMessage: string;
  /** Which phase the live scan is in, so the banner can show two distinct
   *  full-range bars: an indeterminate sweep while DISCOVERING files (no
   *  total is known yet), then a real 0–100% bar while MATCHING. */
  scanPhase: 'idle' | 'scanning' | 'matching' | 'done';
  /** False until the first /files fetch resolves (success OR failure).
   *  Pages use this to suppress empty-state UIs during the initial load
   *  window — without it, the user sees "No library scanned yet" / "Library
   *  is empty" hero for ~200-500ms on every refresh before the real file
   *  list lands, which reads as a glitch. */
  hydrated: boolean;
}

export type ModalState =
  | { kind: 'manualSearch'; payload: MediaFile }
  | { kind: 'renamePreview'; payload: MediaFile[] }
  | { kind: 'shortcuts'; payload?: undefined }
  | { kind: 'fileDetails'; payload: MediaFile }
  | null;

export type Page = 'dashboard' | 'review' | 'history' | 'settings';

// ─────────────────────────────────────────────────────────────────────
// Library grid model — derived shape consumed by CoverCard / CoverPopup.
// Built in adapters.ts by grouping flat MediaFile[] under series_key.
// ─────────────────────────────────────────────────────────────────────

/** A single file as it appears inside a LibraryItem's `files` array. */
export interface LibFile {
  id: string;
  filename: string;
  folder: string;
  /** Pre-formatted size for display ("3.4 GB"). */
  size?: string;
  /** Raw size in bytes — used by the dedupe ranker as the ultimate
   *  tie-breaker (larger usually = higher bitrate = better quality). */
  sizeBytes?: number;
  quality?: string;
  source?: string;
  codec?: string;
  /** Normalized "10bit" | "8bit" — drives the bit-depth step of the
   *  dedupe ranker. 10-bit is the anime gold standard for killing
   *  color banding in gradients. */
  bitDepth?: string;
  /** MediaInfo-derived (when `parsing.read_mediainfo` is on): HDR flavor
   *  ("HDR10" / "HDR10+" / "DV" / "HLG"), speaker layout ("5.1" / "7.1"), and
   *  primary audio codec(s) ("TrueHD" / "DTS-HD" / …). Shown as chips + used by
   *  the dedupe ranker (HDR > SDR, more channels win). */
  hdr?: string;
  channels?: string;
  audio?: string[];
  /** Per-track languages (ISO-639-2/B) read from the container → dual-audio /
   *  multi-sub chips. */
  audio_langs?: string[];
  sub_langs?: string[];
  releaseGroup?: string | null;
  /** Index into the parent item's `episodes` array; null when unmatched. */
  matchedToEpisode: number | null;
  /** True when matched to an episode but the filename suggests a different one. */
  matchedWrong: boolean;
  // See MediaFile.status above — 'renamed' is a real state the backend
  // emits and the UI must carry forward, else renamed files appear
  // back in the pending queue.
  status: 'pending' | 'matching' | 'approved' | 'rejected' | 'no_match' | 'renamed';
  confidence: number;
  /** Backend Match row id for the selected match on this file. Used by
   *  cluster-level actions that need a server-side handle to the match
   *  (Sonarr "send missing", future Radarr / NFO writers). Null when
   *  the file has no real provider match (synthesised no_match cards). */
  matchId?: number | null;
}

/** One episode/track entry on a series or album item. */
export interface LibEpisode {
  season: number;
  episode: number;
  absolute?: number | null;
  title?: string;
  airDate?: string;
  runtime?: number;
  overview?: string;
  duration?: string; // music tracks
  /** Track number for albums (same data as `episode` but named per convention). */
  track?: number;
}

/** The atomic unit on the Review page — series, movie, or album. */
export interface LibraryItem {
  id: string;
  kind: 'series' | 'movie' | 'album';
  mediaType: MediaType;
  title: string;
  year?: number | null;
  yearRange?: string;
  overview?: string;
  studio?: string;
  network?: string;
  label?: string;          // music label
  director?: string;       // movies
  language?: string;
  country?: string;
  genres?: string[];
  cast?: string[];         // movies
  altTitles?: string[];
  titleRomaji?: string;
  titleNative?: string;
  artist?: string;         // music
  runtime?: number;        // movies
  providers?: { tmdb?: number | string; tvdb?: number | string; anidb?: number | string; musicbrainz?: string };
  poster: PosterData;
  /** Real cover art from the matched provider (TMDB, TVDB, AniDB CDN).
   *  When null/missing, the CoverCard falls back to the gradient + initials. */
  posterUrl?: string | null;
  /** Franchise identity — cards sharing this value cluster under one
   *  sub-heading inside their media-type section on the Review page. */
  seriesGroupId?: string | null;
  /** #14: TMDB movie-collection display name ("The Matrix Collection"). When
   *  set, the franchise band heading uses it instead of the earliest film's
   *  title. Only populated for movies that belong to a collection. */
  collectionName?: string | null;
  /** Per-cluster key from the backend (`tv|breaking bad|1`). Distinct from
   *  the franchise `seriesGroupId`; used to re-find the same item after a
   *  re-match shifts its synthesized `id`. */
  seriesKey?: string | null;
  /** Canonical season number from the matched provider (AniDB Fribb
   *  cross-ref or TMDB/TVDB season layout). Displayed as "Season N" on
   *  the card meta row when ≥ 1. Authoritative — replaces the old
   *  year-sort heuristic that picked Season 1/2/3 by index. */
  season?: number | null;
  episodes: LibEpisode[];
  files: LibFile[];
  /** True when the item couldn't be matched to any provider at all. */
  noMatch?: boolean;
  /** True while backend is actively matching this item's files. */
  matchingState?: boolean;
  /** Override aggregate state ('rejected' if user explicitly killed the whole item). */
  overallStatus?: 'approved' | 'rejected';
}
