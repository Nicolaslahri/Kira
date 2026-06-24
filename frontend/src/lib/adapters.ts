/**
 * Convert the backend API shape into the existing MediaFile shape the polished
 * UI consumes. The mock prototype and the real API differ in a handful of small
 * ways — this adapter is the only place we paper over them.
 */
import type { ApiMediaFile } from './api';
import { posterSrc } from './api';
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
  if (mediaType !== 'movie' && mediaType !== 'music' && displayYear) {  // an album isn't an ongoing series → bare year
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
        // Backend Match row id — needed by cluster-level cross-system
        // actions (Sonarr "send missing" being the first). Without it
        // the downstream LibFile.matchId field stays null and any
        // "act on this match" button can't find the server-side
        // handle. Pulled from topMatch only; synthesised matches
        // (from parsed.title when no provider hit) legitimately have
        // no backend id.
        matchId: topMatch?.id ?? undefined,
        provider: topMatch?.provider,
        providerId: topMatch?.provider_id,
        seriesGroupId: topMatch?.series_group_id ?? undefined,
        collectionName: strOrU('collection_name') ?? null,  // #14
        collectionId: strOrU('collection_id') ?? null,       // #14 collection band key
        tmdbId: topMatch && topMatch.provider === 'tmdb' ? Number(topMatch.provider_id) || null : null,
        poster: poster(displayTitle, displayYear),
        posterUrl: posterSrc(topMatch?.poster_url),
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
        // Music-specific. Prefer the MusicBrainz-corrected metadata (the matcher
        // wrote artist/album/title into the blob) over the filename parse, which
        // is unreliable for music — these folder layouts put the year in the
        // artist slot ("music|2014|justin bieber journals").
        artist: (isMusic ? strOrU('artist') : undefined) ?? parsed.artist ?? undefined,
        album: (isMusic ? strOrU('album') : undefined) ?? parsed.album ?? undefined,
        track: parsed.track ?? undefined,
        trackTitle: (isMusic ? strOrU('title') : undefined) ?? parsed.track_title ?? undefined,
        // This track's length + the album genre + the release MBID (for the
        // popup's rows, details, and MusicBrainz link).
        duration: isMusic ? fmtDuration(typeof parsed.duration === 'number' ? parsed.duration : null) : undefined,
        genre: isMusic ? (strOrU('genre') ?? (typeof parsed.genre === 'string' ? parsed.genre : undefined)) : undefined,
        mbid: isMusic ? mbReleaseId(topMatch?.series_group_id) : undefined,
        art: isMusic ? poster(parsed.album ?? parsed.artist ?? displayTitle, displayYear) : undefined,
      }
    : null;

  const candidates: CandidateData[] = api.matches.map(m => ({
    matchId: m.id,
    title: stripTrailingYear(m.title, m.year),
    year: m.year,
    confidence: Math.round(m.confidence * 100),
    poster: poster(m.title ?? '', m.year),
    posterUrl: posterSrc(m.poster_url),
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
    hdr: parsed.hdr ?? undefined,
    channels: parsed.channels ?? undefined,
    audioBitrate: typeof parsed.audio_bitrate === 'number' ? parsed.audio_bitrate : undefined,
    // How this file matched (music) — surfaced as the popup's "via …" chip.
    matchedVia: isMusic ? strOrU('matched_via') : undefined,
    sampleRate: typeof parsed.sample_rate === 'number' ? parsed.sample_rate : undefined,
    audioBitDepth: typeof parsed.audio_bit_depth === 'number' ? parsed.audio_bit_depth : undefined,
    lossless: typeof parsed.lossless === 'boolean' ? parsed.lossless : undefined,
    audio: Array.isArray(parsed.audio) ? parsed.audio : undefined,
    durationSec: typeof parsed.duration === 'number' ? parsed.duration : undefined,
    audio_langs: Array.isArray(parsed.audio_langs) ? parsed.audio_langs : undefined,
    sub_langs: Array.isArray(parsed.sub_langs) ? parsed.sub_langs : undefined,
    // Backend-computed coverage gap (top-level, not in parsed_data). Keep only
    // a non-empty array → the chip/button render exactly when there's a gap.
    missingSubs: Array.isArray(api.missing_subs) && api.missing_subs.length > 0
      ? api.missing_subs : undefined,
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

/** Seconds → "m:ss" (music track length). */
function fmtDuration(sec: number | null | undefined): string | undefined {
  if (!sec || sec <= 0) return undefined;
  const m = Math.floor(sec / 60), s = Math.round(sec % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
}

/** Extract the MusicBrainz release MBID from a music seriesGroupId
 *  (`musicbrainz:<mbid>`), skipping the synthetic `loose:` group (no real MB
 *  release → no link). */
function mbReleaseId(gid: string | null | undefined): string | undefined {
  if (!gid || !gid.startsWith('musicbrainz:')) return undefined;
  const id = gid.slice('musicbrainz:'.length);
  return id.startsWith('loose:') ? undefined : id;
}

/** Friendly title cased from the series_key tail when no match exists yet. */
function titleFromKey(key: string | null | undefined, fallback: string): string {
  if (!key) return fallback;
  const parts = key.split('|');
  const raw = parts[1] || fallback;
  return raw.replace(/\b\w/g, c => c.toUpperCase());
}

/** Build a LibraryItem from one or more MediaFiles. */
/** Japanese/Chinese/Korean characters — used to prefer a readable (romaji/eng)
 *  title over a CJK master name when a merged card has both to choose from. */
const hasCjk = (s: string): boolean => /[぀-ヿ㐀-鿿가-힯]/.test(s);

function buildItem(group: MediaFile[]): LibraryItem {
  const head = group[0];
  const isMovie = head.mediaType === 'movie';
  const isMusic = head.mediaType === 'music';
  const kind: LibraryItem['kind'] = isMovie ? 'movie' : isMusic ? 'album' : 'series';

  // Use top-confidence file's match as the representative title for the card.
  // Tiebreak on a readable title: when a merged AniDB+TVDB anime card has two
  // equally-confident matches, prefer the non-CJK one so it reads "Modaete yo,
  // Adam-kun", not the Japanese master "悶えてよ、アダムくん".
  const repFile = [...group].sort((a, b) => {
    const byConf = (b.confidence || 0) - (a.confidence || 0);
    if (byConf !== 0) return byConf;
    return (hasCjk(a.match?.title || '') ? 1 : 0) - (hasCjk(b.match?.title || '') ? 1 : 0);
  })[0];
  const repMatch = repFile.match;
  // Music cards are titled by the ALBUM / folder ("Singles") — the cover IS the
  // group; its songs live inside the popup. TV/movie keep their match title.
  const title = (isMusic && repMatch?.album)
    ? repMatch.album
    : (repMatch?.title || titleFromKey(head.seriesKey, head.filename));
  const year = repMatch?.year ?? null;

  // Synthesize episodes from each file's parsed season/episode.
  // ALWAYS key by `${season}-${episode}` so files claiming the same
  // episode share one entry. Without this, a VARYG file (S01E16, no
  // absolute) and a Moozzi2 file (Nana-16 with absolute=16) of the same
  // episode created two separate entries, and the popup's single-file-
  // per-key lookup ended up orphaning one of them.
  // Music dedup is by SONG, not track number. Loose singles often share a missing
  // / placeholder track number from the backend (all "1"), but they are DIFFERENT
  // songs — they must NOT collapse into one "duplicate" track (the 34-singles bug).
  // When the track numbers collide, derive a distinct position per song (same
  // title → same track = a genuine duplicate); real albums (distinct track
  // numbers) are left untouched.
  let musicEpByFile: Map<string, number> | null = null;
  if (isMusic) {
    const epCounts = new Map<number, number>();
    group.forEach(f => { const e = f.match?.episode ?? 0; epCounts.set(e, (epCounts.get(e) ?? 0) + 1); });
    if ([...epCounts.values()].some(c => c > 1)) {
      const byFile = (musicEpByFile = new Map<string, number>());
      let pos = 0;
      const byTitle = new Map<string, number>();
      group.forEach(f => {
        const songKey = (f.match?.trackTitle || f.match?.title || '').trim().toLowerCase();
        let epNo: number;
        if (songKey && byTitle.has(songKey)) epNo = byTitle.get(songKey)!;     // same song → same track (a real dup)
        else { epNo = ++pos; if (songKey) byTitle.set(songKey, epNo); }
        byFile.set(f.id, epNo);   // local non-null ref — closure loses the narrowing
      });
    }
  }

  const epMap = new Map<string, LibEpisode>();
  group.forEach(f => {
    if (kind === 'movie') return;
    const abs = f.match?.absoluteEpisode ?? null;
    const season = isMusic ? 1 : (f.match?.season ?? 1);
    const episode = (isMusic && musicEpByFile) ? (musicEpByFile.get(f.id) ?? null) : (f.match?.episode ?? abs ?? null);
    if (episode == null) return;
    const key = `${season}-${episode}`;
    const existing = epMap.get(key);
    if (!existing) {
      epMap.set(key, {
        season,
        episode,
        absolute: abs ?? undefined,
        title: f.match?.episodeTitle || (isMusic ? (f.match?.trackTitle || f.match?.title) : undefined) || undefined,
        track: isMusic ? episode : undefined,
        duration: isMusic ? (fmtDuration(f.durationSec) ?? undefined) : undefined,
        durationSec: isMusic ? f.durationSec : undefined,
        artist: isMusic ? (f.match?.artist ?? undefined) : undefined,
        coverUrl: isMusic ? (f.match?.posterUrl ?? null) : undefined,
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
  const files: LibFile[] = group.map((f) => {
    let matchedToEpisode: number | null = null;
    if (kind === 'movie') {
      matchedToEpisode = f.match ? 0 : null;
    } else if (isMusic && musicEpByFile) {
      // Music with colliding track numbers — map each file to its per-song track.
      const epNo = musicEpByFile.get(f.id);
      const targetIdx = epNo != null ? episodes.findIndex(e => e.season === 1 && e.episode === epNo) : -1;
      matchedToEpisode = targetIdx >= 0 ? targetIdx : null;
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
      hdr: f.hdr,
      channels: f.channels,
      audioBitrate: f.audioBitrate,
      sampleRate: f.sampleRate,
      audioBitDepth: f.audioBitDepth,
      lossless: f.lossless,
      matchedVia: f.matchedVia,
      audio: f.audio,
      audio_langs: f.audio_langs,
      sub_langs: f.sub_langs,
      missingSubs: f.missingSubs,
      releaseGroup: f.releaseGroup ?? null,
      matchedToEpisode,
      matchedWrong: false, // future: detect filename-says-X but matched-to-Y
      status: f.status,
      confidence: f.confidence,
      // Surface the backend Match.id so cluster-level cross-system
      // actions (Sonarr "send missing", future NFO writers) have a
      // server-side handle without re-querying. We previously stripped
      // `f.match` here to keep LibFile lean, then realized the cluster
      // popup actions needed it — exposing just the id keeps the win
      // (no nested match blob duplicated 26× per cluster) while
      // unblocking these actions.
      matchId: f.match?.matchId ?? null,
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

  // Library item id: must be unique across ALL items in the grid, and must
  // track the CLUSTERING granularity in buildLibraryItems — one id per
  // cluster, never two clusters sharing an id.
  //
  // The naive `lib_<seriesKey>` collides when one series_key spans multiple
  // provider matches — cour-routing splits a multi-cour anime (Bleach S17
  // across AIDs 15449/17849/18671) into distinct cards that keep the same
  // series_key ("anime|bleach|17|bleach") on different provider_ids. So we
  // append the provider+id. But the cluster key ALSO splits TVDB/TMDB by
  // season, and two such clusters can re-collide when the filename-parsed
  // season (in series_key) agrees while the matcher's canonical season
  // disagrees — so we append the season too, mirroring the cluster key's
  // `|s{season}` exactly. AniDB is keyed by AID alone in buildLibraryItems,
  // so it gets no season suffix here either; the two stay in lock-step.
  // Pre-fix: React warns "Encountered two children with the same key" and
  // reconciliation mis-attributes card state across renders.
  const _seasonSuffix =
    repMatch?.provider && repMatch.provider !== 'anidb' && repMatch?.season != null
      ? `_s${repMatch.season}`
      : '';
  const _matchSuffix = repMatch?.provider && repMatch?.providerId
    ? `_${repMatch.provider}_${repMatch.providerId}${_seasonSuffix}`
    : '';
  const _idStem = head.seriesKey ? `lib_${head.seriesKey}` : `lib_${head.id}`;

  // Music: the card's album/folder artist is the MOST COMMON track artist, not the
  // rep file's — a "Singles" folder's rep may be a collab ("X & Justin Bieber"),
  // which would mislabel the whole card. Counter over the group → the dominant.
  let musicArtist: string | undefined;
  if (isMusic) {
    const counts = new Map<string, number>();
    group.forEach(g => { const a = g.match?.artist?.trim(); if (a) counts.set(a, (counts.get(a) ?? 0) + 1); });
    musicArtist = [...counts.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] ?? repFile.match?.artist ?? undefined;
  }

  return {
    id: `${_idStem}${_matchSuffix}`,
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
    collectionName: repMatch?.collectionName ?? null,  // #14 movie collections
    collectionId: repMatch?.collectionId ?? null,      // #14 collection band key
    // Per-cluster key (distinct from the franchise group id) — lets the
    // Review page re-find this exact item after a re-match shifts its id.
    seriesKey: head.seriesKey ?? null,
    // Canonical season number from the provider — Fribb cross-ref for AniDB
    // (each AID → exactly one TVDB season), filename parser for others.
    // Replaces the old `i + 1` year-sort heuristic on the franchise grid.
    season: repMatch?.season ?? null,
    providers: {
      tmdb:  repMatch?.provider === 'tmdb'  ? repMatch.providerId : undefined,
      tvdb:  repMatch?.provider === 'tvdb'  ? repMatch.providerId : undefined,
      anidb: repMatch?.provider === 'anidb' ? repMatch.providerId : undefined,
      musicbrainz: isMusic ? repMatch?.mbid : undefined,   // release MBID → MusicBrainz ↗ link
    },
    episodes,
    files,
    noMatch,
    matchingState: matching,
    overallStatus: allRejected ? 'rejected' : undefined,
    runtime: repMatch?.runtime,
    // Music fields
    artist: musicArtist,
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
    } else if (f.match?.provider === 'pack' && f.match.seriesGroupId) {
      // A pack's provider_id is per-EPISODE ("<pack>:<season>:<episode>"), so the
      // generic `match|provider|providerId|season` key below would give every
      // episode its own card (76 "One Pace Part N" cards). Cluster by the
      // SERIES-level group id + season instead, so each arc (a pack "season")
      // collapses to ONE card with its episodes — Romance Dawn = one card, not 4.
      const seasonPart = f.match.season != null ? `|s${f.match.season}` : '';
      key = `pack|${f.match.seriesGroupId}${seasonPart}`;
    } else if (f.mediaType === 'music' && f.match?.seriesGroupId) {
      // Music: the provider_id is the per-TRACK recording MBID, so the generic
      // `match|provider|providerId` key below would give every track its own card.
      // Cluster by the ALBUM-level group id so a whole album OR a Singles folder
      // collapses to ONE card; its songs live INSIDE the popup (music rows).
      key = `album|${f.match.seriesGroupId}`;
    } else if (f.match?.provider && f.match.providerId) {
      // Per-season clustering: include the season number so Euphoria
      // S01 / S02 / S03 (all sharing TVDB id 360261) render as 3 cards
      // grouped under one "Euphoria · 3 seasons" sub-heading. The
      // franchise grouping happens downstream via `seriesGroupId`
      // (unchanged here — still `tvdb:360261` for all three). Without
      // the season suffix, the popup's per-season episode fetch collapses
      // to the cluster's first-listed season and every other season's
      // files render as orphaned rows.
      //
      // EXCEPTION — AniDB: each sequel-season gets its OWN AID, so the AID
      // alone already IS the season identity. A single AID that spans many
      // "seasons" is a long-running series catalogued as ONE AniDB entry
      // (One Piece = AID 69, 1100+ episodes). Its season_number is a TVDB
      // cross-ref derivation that can disagree file-to-file (ep 1166 came
      // back season 1, ep 1167 season 23). Splitting on it manufactures a
      // phantom duplicate card AND collides the React grid key (both
      // clusters reduce to the same `lib_<series_key>_anidb_<aid>` id). Key
      // AniDB by AID only so the whole series stays one card.
      const seasonPart =
        f.match.provider !== 'anidb' && f.match.season != null
          ? `|s${f.match.season}`
          : '';
      key = `match|${f.match.provider}|${f.match.providerId}${seasonPart}`;
    } else if (f.seriesKey) {
      key = f.seriesKey;
    } else {
      key = `__solo_${f.id}`;
    }
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(f);
  });
  const items = mergeDuplicateClusters(Array.from(groups.values())).map(buildItem);
  markCrossAlbumDuplicates(items);
  return items;
}

/** Music cross-album dedupe (flag-only). A "Singles" folder often re-includes
 *  songs you already have on a real album (e.g. "Yummy" in Singles AND on
 *  Changes). Index every song on a REAL album, then flag any LOOSE single whose
 *  song matches one — so the row shows an "Also on <album>" badge instead of
 *  silently presenting the duplicate as a distinct single. Keyed on song title +
 *  album artist (so a different artist's same-titled song is NOT a false dup);
 *  exact title only, so remixes ("Yummy (Country remix)") correctly DON'T match. */
function markCrossAlbumDuplicates(items: LibraryItem[]): void {
  const keyOf = (title: string | undefined | null, artist: string | undefined | null) =>
    `${(title || '').trim().toLowerCase()}|${(artist || '').trim().toLowerCase()}`;
  const onAlbum = new Map<string, string>();   // song key → album title
  for (const it of items) {
    if (it.kind !== 'album' || it.seriesGroupId?.includes(':loose:')) continue;   // REAL albums only
    for (const e of it.episodes) {
      if (e.title) onAlbum.set(keyOf(e.title, it.artist), it.title);
    }
  }
  if (!onAlbum.size) return;
  for (const it of items) {
    if (it.kind !== 'album' || !it.seriesGroupId?.includes(':loose:')) continue;   // LOOSE singles only
    for (const e of it.episodes) {
      const alb = e.title ? onAlbum.get(keyOf(e.title, it.artist)) : undefined;
      if (alb) e.dupOf = alb;
    }
  }
}

/**
 * Merge cross-provider duplicate COPIES into ONE cluster before card-building.
 * Two clusters in the same franchise (`seriesGroupId`) that cover the same
 * (season, episode) via DIFFERENT providers are two copies of one show — e.g.
 * Kira's earlier AniDB-organized copy plus a Sonarr `{tvdb-…}` re-download of the
 * same eight episodes. Collapsing them into one group means the card carries BOTH
 * files per episode, so Kira's existing per-episode dedupe procedure (pick the
 * best, delete the rest) takes over — instead of leaving two separate cards.
 *
 * The cross-provider requirement keeps it honest: same-provider overlap is
 * legitimately distinct content the provider numbered separately — Attack on
 * Titan's seasons, Bleach's cours, One Piece's movies (all sharing a fabricated
 * S1E1) — and is NEVER merged. Merging only happens when episode numbers already
 * ALIGN (that IS the overlap), so the merged card's per-episode pairing is clean.
 */
export function mergeDuplicateClusters(groups: MediaFile[][]): MediaFile[][] {
  // Per-cluster facts: franchise id, matched provider, covered (season,episode)s.
  const facts = groups.map(g => {
    const m = g.find(f => f.match)?.match;
    const eps = new Set<string>();
    for (const f of g) {
      const e = f.match?.episode ?? f.match?.absoluteEpisode ?? null;
      if (e != null) eps.add(`s${f.match?.season ?? 1}e${e}`);
    }
    return { gid: m?.seriesGroupId ?? null, provider: m?.provider ?? null, eps };
  });

  // Union-find: link clusters that share a franchise, DIFFER in provider, and
  // overlap on an episode → one connected component = one merged group.
  const parent = groups.map((_, i) => i);
  const find = (i: number): number => {
    let r = i;
    while (parent[r] !== r) r = parent[r]!;
    while (parent[i] !== r) { const n = parent[i]!; parent[i] = r; i = n; }
    return r;
  };
  const union = (a: number, b: number) => { parent[find(a)] = find(b); };

  const byGid = new Map<string, number[]>();
  facts.forEach((f, i) => {
    if (!f.gid) return;
    const arr = byGid.get(f.gid);
    if (arr) arr.push(i); else byGid.set(f.gid, [i]);
  });
  for (const idxs of byGid.values()) {
    for (let a = 0; a < idxs.length; a++) {
      for (let b = a + 1; b < idxs.length; b++) {
        const ia = idxs[a]!, ib = idxs[b]!;
        const A = facts[ia]!, B = facts[ib]!;
        if (!A.provider || !B.provider || A.provider === B.provider) continue;  // same provider → not a copy
        let overlap = false;
        for (const k of A.eps) { if (B.eps.has(k)) { overlap = true; break; } }
        if (overlap) union(ia, ib);
      }
    }
  }

  // Collect each component's files into one group, preserving first-seen order.
  const byRoot = new Map<number, MediaFile[]>();
  const order: number[] = [];
  groups.forEach((g, i) => {
    const r = find(i);
    let arr = byRoot.get(r);
    if (!arr) { arr = []; byRoot.set(r, arr); order.push(r); }
    arr.push(...g);
  });
  return order.map(r => byRoot.get(r)!);
}

