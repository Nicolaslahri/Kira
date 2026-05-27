/**
 * Convert the backend API shape into the existing MediaFile shape the polished
 * UI consumes. The mock prototype and the real API differ in a handful of small
 * ways — this adapter is the only place we paper over them.
 */
import type { ApiMediaFile } from './api';
import { poster } from './data';
import type {
  CandidateData, MatchData, MediaFile, MediaType,
  LibraryItem, LibFile, LibEpisode,
} from './types';

function basename(path: string): string {
  return path.split(/[\\/]/).pop() ?? path;
}
function dirname(path: string): string {
  const fn = basename(path);
  return path.substring(0, Math.max(0, path.length - fn.length - 1));
}

/**
 * TVDB/TMDB often include the year in the title — "Kanojo, Okarishimasu (2022)".
 * We display year separately, so strip the trailing "(YYYY)" to avoid
 * "Kanojo, Okarishimasu (2022) · 2022" duplication.
 */
function stripTrailingYear(title: string | null | undefined, year: number | null | undefined): string {
  if (!title) return '';
  if (!year) return title;
  const stripped = title.replace(new RegExp(`\\s*\\(${year}\\)\\s*$`), '');
  return stripped || title;
}

export function apiToMediaFile(api: ApiMediaFile): MediaFile {
  const filename = basename(api.file_path);
  const folder = dirname(api.file_path);
  const parsed = api.parsed_data ?? {};
  // Prefer the explicitly selected match (manual pin OR auto-top with
  // is_selected=true) over array position. The backend sorts matches by
  // confidence DESC, which is stable in Python — so when an auto-match
  // and a fresh manual pick BOTH hit confidence=1.0 (very common at the
  // tier-1 ceiling), the older row keeps index 0 by insertion order and
  // the manual pick lands at index 1+. Using `matches[0]` for the
  // display meant a manual match could write a new row + flip
  // is_selected correctly, but the cover would still read the stale
  // top-by-confidence row and "nothing visibly changed" even though
  // the toast confirmed success.
  const topMatch = api.matches.find(m => m.is_selected) ?? api.matches[0];

  const mediaType = (api.media_type as MediaType | null) ?? 'movie';
  const isMusic = mediaType === 'music';

  // Title fallback chain: top API match → parsed title → filename.
  // Strip any trailing "(YYYY)" so we don't double-print the year.
  const rawTitle = topMatch?.title ?? parsed.title ?? filename;
  const displayYear = topMatch?.year ?? parsed.year ?? null;
  const displayTitle = stripTrailingYear(rawTitle, displayYear);

  // Defensive helpers — read into the metadata blob safely. Backend may
  // not have populated it yet (auto-heal pending), so every key can be missing.
  const meta = (topMatch?.metadata ?? {}) as Record<string, unknown>;
  const strOrU = (k: string) =>
    typeof meta[k] === 'string' && (meta[k] as string).length ? (meta[k] as string) : undefined;
  const numOrU = (k: string) =>
    typeof meta[k] === 'number' ? (meta[k] as number) : undefined;
  const strArr = (k: string) =>
    Array.isArray(meta[k]) ? (meta[k] as unknown[]).filter(x => typeof x === 'string') as string[] : undefined;

  // Synthesize the year range for TV shows. Mocks show "2022 –" for The
  // Bear (ongoing) and "2020 –" for JJK. If the metadata blob carries a
  // last_air_date AND in_production is false, show "2022 – 2024"; if still
  // running, "2022 –"; movies just keep the bare year.
  let yearRange: string | undefined;
  if (mediaType !== 'movie' && displayYear) {
    const lastAir = strOrU('last_air_date');
    const lastYear = lastAir ? parseInt(lastAir.slice(0, 4), 10) : undefined;
    const inProd = meta['in_production'] !== false;  // default to ongoing
    if (lastYear && !Number.isNaN(lastYear) && !inProd && lastYear !== displayYear) {
      yearRange = `${displayYear} – ${lastYear}`;
    } else if (inProd || !lastYear) {
      yearRange = `${displayYear} –`;
    }
  }

  // Convert ISO-639 code → display name. Tiny lookup — fine for v1.
  const _LANG: Record<string, string> = {
    en: 'English', ja: 'Japanese', jpn: 'Japanese', fr: 'French', es: 'Spanish',
    de: 'German', it: 'Italian', ko: 'Korean', zh: 'Chinese', pt: 'Portuguese',
    ru: 'Russian', nl: 'Dutch', sv: 'Swedish', pl: 'Polish', tr: 'Turkish',
  };
  const _COUNTRY: Record<string, string> = {
    US: 'United States', GB: 'United Kingdom', UK: 'United Kingdom',
    JP: 'Japan', JPN: 'Japan', jpn: 'Japan',
    KR: 'South Korea', CN: 'China', FR: 'France', DE: 'Germany',
    IT: 'Italy', ES: 'Spain', CA: 'Canada', AU: 'Australia',
  };
  const langRaw = strOrU('original_language');
  const countryRaw = strOrU('original_country');
  const language = langRaw ? (_LANG[langRaw.toLowerCase()] ?? langRaw) : undefined;
  const country  = countryRaw ? (_COUNTRY[countryRaw.toUpperCase()] ?? _COUNTRY[countryRaw] ?? countryRaw) : undefined;

  const match: MatchData | null = topMatch || parsed.title
    ? {
        title: displayTitle,
        year: displayYear,
        provider: topMatch?.provider,
        providerId: topMatch?.provider_id,
        seriesGroupId: topMatch?.series_group_id ?? undefined,
        tmdbId: topMatch && topMatch.provider === 'tmdb' ? Number(topMatch.provider_id) || null : null,
        poster: poster(displayTitle, displayYear),
        posterUrl: topMatch?.poster_url ?? null,
        // Overview fallback chain: Match.overview column → metadata.overview
        // (populated by TVDB/TMDB extended payload, including via the
        // AniDB cross-ref). AniDB's search doesn't return descriptions,
        // so the metadata blob is the only path for anime hero excerpts.
        overview: topMatch?.overview ?? strOrU('overview') ?? undefined,
        // Match.season_number is the matcher's CANONICAL season — for
        // AniDB it comes from the Fribb cross-ref (each AID maps to
        // exactly one TVDB season), and the backend writes it on every
        // Match row. parsed.season is just the filename's claim, which
        // can disagree (e.g. `[ToonsHub] BLEACH Thousand-Year Blood War
        // - S01E01.mkv` parses as S1 but Fribb pins the AID to S17 of
        // Bleach). Trust the matcher first. Without this, two files
        // matched to the same AID land in different frontend clusters
        // (one keyed `|s1`, other `|s17`) and the popup splits the
        // franchise card. parsed.season stays as the second fallback
        // for files the matcher couldn't resolve a season for.
        season: topMatch?.season_number ?? parsed.season ?? undefined,
        episode: topMatch?.episode_number ?? parsed.episode ?? undefined,
        episodeTitle: topMatch?.episode_title ?? undefined,
        absoluteEpisode: parsed.absolute_episode ?? undefined,
        // Series/movie runtime in minutes from the details fetch — separate
        // from per-episode runtime (which lives on LibEpisode).
        runtime: numOrU('runtime'),
        // Rich popup hero fields — all defensive reads, all optional.
        genres: strArr('genres'),
        cast: strArr('cast'),
        director: strOrU('director'),
        network: strOrU('network'),
        studio: strOrU('studio'),
        label: strOrU('label'),
        language,
        country,
        yearRange,
        // Anime — AniDB writes these from its in-memory title cache.
        titleRomaji: strOrU('title_romaji'),
        titleNative: strOrU('title_native'),
        altTitles: strArr('alt_titles'),
        // Music-specific
        artist: parsed.artist ?? undefined,
        album: parsed.album ?? undefined,
        track: parsed.track ?? undefined,
        trackTitle: parsed.track_title ?? undefined,
        art: isMusic ? poster(parsed.album ?? parsed.artist ?? displayTitle, displayYear) : undefined,
      }
    : null;

  const candidates: CandidateData[] = api.matches.map(m => ({
    matchId: m.id,
    title: stripTrailingYear(m.title, m.year),
    year: m.year,
    confidence: Math.round(m.confidence * 100),
    poster: poster(m.title ?? '', m.year),
    posterUrl: m.poster_url ?? null,
    season: m.season_number ?? undefined,
    episode: m.episode_number ?? undefined,
  }));

  const confidencePct = topMatch ? Math.round(topMatch.confidence * 100) : 0;

  // Backend statuses we surface as-is for the UI: 'matching', 'approved',
  // 'rejected', 'no_match', 'renamed'. Anything else collapses to 'pending'.
  //
  // Bug-fix: 'renamed' used to NOT be in this list, so the adapter
  // collapsed it back to 'pending'. Result: a successfully renamed
  // file appeared back in the Pending queue forever, the Renamed tab
  // stayed empty (filter `f.status === 'renamed'` never matched), and
  // approving the same file repeatedly had no effect on what the user
  // saw. 'renamed' now passes through correctly.
  const status: MediaFile['status'] =
    api.status === 'matching' ? 'matching' :
    api.status === 'approved' ? 'approved' :
    api.status === 'rejected' ? 'rejected' :
    api.status === 'no_match' ? 'no_match' :
    api.status === 'renamed' ? 'renamed' :
    'pending';

  return {
    id: String(api.id),
    filename,
    folder,
    mediaType,
    status,
    confidence: confidencePct,
    releaseGroup: parsed.release_group ?? undefined,
    // Format-strip data — surfaces as small tags on the file row so
    // duplicate-episode pairs (different release groups of the same ep)
    // are visually distinguishable beyond just the [Group] chip.
    quality: parsed.quality ?? undefined,
    source: parsed.source ?? undefined,
    codec: parsed.codec ?? undefined,
    bitDepth: parsed.bit_depth ?? undefined,
    size: humanSize(api.file_size),
    sizeBytes: api.file_size ?? undefined,
    parsedTitle: parsed.title ?? undefined,
    match,
    candidates,
    seriesKey: api.series_key ?? null,
  };
}

// ─────────────────────────────────────────────────────────────────────
// Library-grid grouping: flat MediaFile[] → grouped LibraryItem[]
//
// A movie or a one-off file → kind:'movie' with files=[that one].
// Files sharing a series_key → kind:'series' (or 'album' for music).
// Inside each series item we synthesize the `episodes` array from each
// file's parsed season/episode so the popup can render the paired view.
// ─────────────────────────────────────────────────────────────────────

function humanSize(bytes: number | null | undefined): string | undefined {
  if (!bytes || bytes <= 0) return undefined;
  const KB = 1024, MB = KB * 1024, GB = MB * 1024;
  if (bytes >= GB) return `${(bytes / GB).toFixed(1)} GB`;
  if (bytes >= MB) return `${(bytes / MB).toFixed(0)} MB`;
  return `${(bytes / KB).toFixed(0)} KB`;
}

/** Friendly title cased from the series_key tail when no match exists yet. */
function titleFromKey(key: string | null | undefined, fallback: string): string {
  if (!key) return fallback;
  const parts = key.split('|');
  const raw = parts[1] || fallback;
  return raw.replace(/\b\w/g, c => c.toUpperCase());
}

/** Build a LibraryItem from one or more MediaFiles. */
function buildItem(group: MediaFile[]): LibraryItem {
  const head = group[0];
  const isMovie = head.mediaType === 'movie';
  const isMusic = head.mediaType === 'music';
  const kind: LibraryItem['kind'] = isMovie ? 'movie' : isMusic ? 'album' : 'series';

  // Use top-confidence file's match as the representative title for the card.
  const repFile = [...group].sort((a, b) => (b.confidence || 0) - (a.confidence || 0))[0];
  const repMatch = repFile.match;
  const title = repMatch?.title || titleFromKey(head.seriesKey, head.filename);
  const year = repMatch?.year ?? null;

  // Synthesize episodes from each file's parsed season/episode.
  // ALWAYS key by `${season}-${episode}` so files claiming the same
  // episode share one entry. Without this, a VARYG file (S01E16, no
  // absolute) and a Moozzi2 file (Nana-16 with absolute=16) of the same
  // episode created two separate entries, and the popup's single-file-
  // per-key lookup ended up orphaning one of them.
  const epMap = new Map<string, LibEpisode>();
  group.forEach(f => {
    if (kind === 'movie') return;
    const abs = f.match?.absoluteEpisode ?? null;
    const season = f.match?.season ?? 1;
    const episode = f.match?.episode ?? abs ?? null;
    if (episode == null) return;
    const key = `${season}-${episode}`;
    const existing = epMap.get(key);
    if (!existing) {
      epMap.set(key, {
        season,
        episode,
        absolute: abs ?? undefined,
        title: f.match?.episodeTitle || undefined,
        track: isMusic ? (f.match?.track ?? episode ?? undefined) : undefined,
      });
    } else {
      // Merge missing fields from a duplicate file into the existing entry.
      if (existing.absolute == null && abs != null) existing.absolute = abs;
      if (!existing.title && f.match?.episodeTitle) existing.title = f.match.episodeTitle;
    }
  });
  const episodes: LibEpisode[] = [...epMap.values()].sort((a, b) => {
    if (a.absolute != null && b.absolute != null) return a.absolute - b.absolute;
    if (a.season !== b.season) return a.season - b.season;
    return a.episode - b.episode;
  });

  // For movies, fabricate one "episode" so the popup math still works
  // (movie body branches on kind anyway, so episodes content is unused there).
  if (kind === 'movie' && episodes.length === 0) {
    episodes.push({ season: 1, episode: 1, title: title });
  }

  // Map each MediaFile → LibFile with its index into the episodes array.
  const files: LibFile[] = group.map((f, idx) => {
    let matchedToEpisode: number | null = null;
    if (kind === 'movie') {
      matchedToEpisode = f.match ? 0 : null;
    } else if (f.match) {
      const abs = f.match.absoluteEpisode ?? null;
      const ep = f.match.episode ?? null;
      const season = f.match.season ?? 1;
      const targetIdx = episodes.findIndex(e =>
        (abs != null && e.absolute === abs) ||
        (abs == null && e.season === season && e.episode === ep)
      );
      matchedToEpisode = targetIdx >= 0 ? targetIdx : null;
    }
    return {
      id: f.id,
      filename: f.filename,
      folder: f.folder,
      size: f.size,
      sizeBytes: f.sizeBytes,
      quality: f.quality,
      source: f.source,
      codec: f.codec,
      bitDepth: f.bitDepth,
      releaseGroup: f.releaseGroup ?? null,
      matchedToEpisode,
      matchedWrong: false, // future: detect filename-says-X but matched-to-Y
      status: f.status,
      confidence: f.confidence,
    };
  });

  const matching = group.some(f => f.status === 'matching');
  const allRejected = group.length > 0 && group.every(f => f.status === 'rejected');
  // A card is "no_match" when every file in the cluster has either an
  // explicit no_match status from the backend OR no real provider match
  // (the adapter synthesises a placeholder MatchData from parsed data
  // for display, so `!f.match` is unreliable here — we need to check
  // for a real provider id). Without this, no_match cards bloated the
  // media-type sections instead of landing in "Needs matching".
  const noMatch = group.every(f =>
    f.status === 'no_match' || !f.match?.provider || !f.match?.providerId
  ) && !matching;

  // Pull the rich metadata bag from whichever file's match has the most
  // populated blob (the matcher only writes it onto rank-0 of the cluster,
  // but repFile already IS the highest-confidence file, so this lines up).
  // Helpers mirror those in apiToMediaFile so the shape stays consistent.
  const _readMatchMeta = (m: typeof repMatch): Record<string, unknown> => {
    // Pull metadata back out of MatchData — we stash everything we read
    // from the API meta blob onto MatchData fields, but only the named
    // ones make it across. So we re-read from any file's match where
    // possible. For the hero we can just read from repMatch directly.
    if (!m) return {};
    return {
      genres: m.genres, cast: m.cast, director: m.director,
      network: m.network, studio: m.studio, language: m.language,
      country: m.country, runtime: m.runtime, label: m.label,
      titleRomaji: m.titleRomaji, titleNative: m.titleNative,
      altTitles: m.altTitles, yearRange: m.yearRange,
    };
  };
  const heroMeta = _readMatchMeta(repMatch);

  return {
    id: head.seriesKey ? `lib_${head.seriesKey}` : `lib_${head.id}`,
    kind, mediaType: head.mediaType,
    title,
    year,
    yearRange: (heroMeta.yearRange as string | undefined) ?? undefined,
    overview: repMatch?.overview,
    studio: heroMeta.studio as string | undefined,
    network: heroMeta.network as string | undefined,
    label: heroMeta.label as string | undefined,
    director: heroMeta.director as string | undefined,
    language: heroMeta.language as string | undefined,
    country: heroMeta.country as string | undefined,
    genres: heroMeta.genres as string[] | undefined,
    cast: heroMeta.cast as string[] | undefined,
    titleRomaji: heroMeta.titleRomaji as string | undefined,
    titleNative: heroMeta.titleNative as string | undefined,
    altTitles: (heroMeta.altTitles as string[] | undefined) ?? [],
    poster: poster(title, year),
    // Prefer the rep file's real poster; fall back to any other file in the
    // cluster that happens to have one (TMDB sometimes only returns artwork
    // on certain matches).
    posterUrl: repMatch?.posterUrl
      ?? group.map(g => g.match?.posterUrl).find(Boolean)
      ?? null,
    seriesGroupId: repMatch?.seriesGroupId ?? null,
    // Canonical season number from the provider — Fribb cross-ref for AniDB
    // (each AID → exactly one TVDB season), filename parser for others.
    // Replaces the old `i + 1` year-sort heuristic on the franchise grid.
    season: repMatch?.season ?? null,
    providers: {
      tmdb:  repMatch?.provider === 'tmdb'  ? repMatch.providerId : undefined,
      tvdb:  repMatch?.provider === 'tvdb'  ? repMatch.providerId : undefined,
      anidb: repMatch?.provider === 'anidb' ? repMatch.providerId : undefined,
    },
    episodes,
    files,
    noMatch,
    matchingState: matching,
    overallStatus: allRejected ? 'rejected' : undefined,
    runtime: repMatch?.runtime,
    // Music fields
    artist: isMusic ? repFile.match?.artist : undefined,
  };
}

/** Group a flat MediaFile list into LibraryItems for the cover grid.
 *
 *  Clustering priority (per file):
 *    1. matched (provider, provider_id) — ground truth from TMDB/TVDB/AniDB.
 *       This is what we trust the most: once 12 episodes have matched to
 *       AniDB AID 69, they ALL belong on the One Piece card, regardless of
 *       how messy the parsed titles are ("One Pace" vs "One Piece ep1156
 *       tver jpn" vs "One Piece").
 *    2. parsed series_key — for files the matcher didn't reach yet (still
 *       in 'matching' or 'no_match' state).
 *    3. solo bucket — last resort.
 *
 *  Without #1, parser sloppiness splits one show into 5 cards (one per
 *  distinct parsed title variant). Provider IDs collapse them back.
 */
export function buildLibraryItems(files: MediaFile[]): LibraryItem[] {
  const groups = new Map<string, MediaFile[]>();
  files.forEach(f => {
    let key: string;
    // Movies stay solo even when matched — each is its own card, regardless
    // of the franchise. (Otherwise all the Marvel movies would collapse.)
    if (f.mediaType === 'movie') {
      key = `__solo_${f.id}`;
    } else if (f.match?.provider && f.match.providerId) {
      // Per-season clustering: include the season number so Euphoria
      // S01 / S02 / S03 (all sharing TVDB id 360261) render as 3 cards
      // grouped under one "Euphoria · 3 seasons" sub-heading. The
      // franchise grouping happens downstream via `seriesGroupId`
      // (unchanged here — still `tvdb:360261` for all three). For
      // AniDB shows this is a no-op since each season already has its
      // own AID. Without the season suffix, the popup's per-season
      // episode fetch collapses to the cluster's first-listed season
      // and every other season's files render as orphaned rows.
      const seasonPart = f.match.season != null ? `|s${f.match.season}` : '';
      key = `match|${f.match.provider}|${f.match.providerId}${seasonPart}`;
    } else if (f.seriesKey) {
      key = f.seriesKey;
    } else {
      key = `__solo_${f.id}`;
    }
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(f);
  });
  return Array.from(groups.values()).map(buildItem);
}

