const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? 'http://127.0.0.1:8000/api/v1';

export interface ApiMatch {
  id: number;
  provider: string;
  provider_id: string;
  match_type: string;
  confidence: number;
  title: string | null;
  year: number | null;
  season_number: number | null;
  episode_number: number | null;
  episode_title: string | null;
  poster_url: string | null;
  overview: string | null;
  is_selected: boolean;
  /** True when the user explicitly picked this match (manual search or
   *  bulk match). Backend protects these from auto-heal + /rematch-all. */
  is_manual: boolean;
  /** Franchise identity used for grouping cards on the Review page.
   *  Format: "{provider}:{canonical_id}" — for AniDB sequel chains this is
   *  the lowest AID in the franchise (e.g. all 5 seasons of Rent-a-Girlfriend
   *  share `anidb:15299`). Null for old matches written before this column
   *  existed; backfilled on next backend startup. */
  series_group_id: string | null;
  /** Rich popup metadata blob — genres, cast, director, network, studio,
   *  language, country, runtime, last_air_date, title_romaji, title_native,
   *  alt_titles. Single JSON blob so the wire format doesn't grow every
   *  time we add a field. Frontend reads keys defensively. */
  metadata: Record<string, unknown> | null;
}

export interface ApiParsedData {
  title?: string;
  year?: number | null;
  season?: number | null;
  episode?: number | null;
  absolute_episode?: number | null;
  artist?: string | null;
  album?: string | null;
  track?: number | null;
  track_title?: string | null;
  release_group?: string | null;
  quality?: string | null;
  source?: string | null;
  codec?: string | null;
  /** Normalized "10bit" / "8bit" — drives the dedupe ranker's bit-depth tier. */
  bit_depth?: string | null;
  /** MediaInfo-derived (when `parsing.read_mediainfo` is on): HDR flavor
   *  ("HDR10" / "HDR10+" / "DV" / "HLG"), speaker layout ("5.1" / "7.1"), and
   *  the primary audio codec(s) ("TrueHD" / "DTS-HD" / …). Surfaced as chips +
   *  fed into the duplicate "keep best" ranker. */
  hdr?: string | null;
  channels?: string | null;
  audio?: string[] | null;
  /** Per-track LANGUAGES read from the container (ISO-639-2/B codes, e.g.
   *  ["jpn","eng"]), in track order. Power the dual-audio / multi-sub chips.
   *  Empty/absent until the background MediaInfo pass runs. */
  audio_langs?: string[] | null;
  sub_langs?: string[] | null;
  duration?: number | null;
  confidence?: number;
}

export interface ApiMediaFile {
  id: number;
  file_path: string;
  file_size: number | null;
  media_type: string | null;
  status: string;
  parsed_data: ApiParsedData | null;
  series_key: string | null;
  /** Identity-variant suffix — empty for default-flavor files; non-empty
   *  for audio language / edition / bit-depth variants. Lets the UI show
   *  a "JAP" / "Directors Cut" / "10bit" chip next to the file row and
   *  prevents same-episode rename collisions. Backend computes via
   *  _compute_variant_key from parsed.subtitles / edition / bit_depth. */
  variant_key: string | null;
  created_at: string;
  updated_at: string;
  matches: ApiMatch[];
}

export interface ApiScan {
  id: number;
  root_path: string;
  // 'scanning' | 'matching' | 'completed' | 'completed_partial' | 'failed: ...'
  status: string;
  file_count: number;
  matched_count: number;
  // PB-4: set ONCE Phase 1 (file walk) completes — frontend uses it
  // for real-% progress + ETA in the global scan banner. Null while
  // Phase 1 is still in progress (we don't know the universe yet).
  estimated_total: number | null;
  current_path: string | null;
  created_at: string;
  completed_at: string | null;
}

// HTTP Basic auth (opt-in on the backend via KIRA_AUTH_USER/PASS). Since this
// SPA is a separate origin from the API, a fetch() 401 does NOT trigger the
// browser's native credential prompt — so we capture the credentials ourselves,
// keep them in sessionStorage (cleared when the tab closes), and attach them as
// an Authorization header. When auth is OFF (the default) none of this fires.
const AUTH_KEY = 'kira.basicauth';
const getStoredAuth = (): string | null => {
  try { return sessionStorage.getItem(AUTH_KEY); } catch { return null; }
};
const setStoredAuth = (v: string | null): void => {
  try { v ? sessionStorage.setItem(AUTH_KEY, v) : sessionStorage.removeItem(AUTH_KEY); } catch { /* ignore */ }
};
// Minimal credential capture. window.prompt is intentionally simple (single-user
// self-host); it can be upgraded to a styled login modal later. Returns the
// base64 user:pass or null if the user cancels.
const promptForAuth = (): string | null => {
  const user = window.prompt('Kira requires sign-in.\nUsername:');
  if (user === null) return null;
  const pass = window.prompt('Password:') ?? '';
  return btoa(`${user}:${pass}`);
};

// ── Backend connectivity signal ────────────────────────────────────────────
// Derived from the ACTUAL HTTP layer rather than a single probe: any response
// we receive — even a 4xx/5xx — proves the backend is reachable; only a
// network-level fetch failure (server down / unreachable / DNS) marks it
// offline. Because EVERY api call funnels through `request()` (including the
// continuous /activity poll), this self-heals: the moment the backend answers
// again, the next request flips the status back to online — no page reload, no
// stuck "Backend disconnected" after a transient blip or a slow cold start.
type ConnListener = (online: boolean) => void;
let _backendOnline: boolean | null = null;
const _connListeners = new Set<ConnListener>();

/** Current connectivity: true = reachable, false = unreachable, null = unknown
 *  (no request has completed yet). */
export function getBackendOnline(): boolean | null {
  return _backendOnline;
}

/** Subscribe to connectivity changes. Returns an unsubscribe fn. Fires
 *  immediately with the current value when it's already known. */
export function onBackendConnectivity(fn: ConnListener): () => void {
  _connListeners.add(fn);
  if (_backendOnline !== null) fn(_backendOnline);
  return () => { _connListeners.delete(fn); };
}

function markBackend(online: boolean): void {
  if (_backendOnline === online) return;
  _backendOnline = online;
  for (const fn of _connListeners) fn(online);
}

async function request<T>(path: string, init?: RequestInit, _retried = false): Promise<T> {
  const auth = getStoredAuth();
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: {
        'Content-Type': 'application/json',
        ...(auth ? { Authorization: `Basic ${auth}` } : {}),
        ...init?.headers,
      },
    });
  } catch (err) {
    // fetch() only rejects on a NETWORK-level failure (server down, refused,
    // DNS, CORS preflight fail) — i.e. the backend is genuinely unreachable.
    markBackend(false);
    throw err;
  }
  // We got a response. The backend is reachable even if it answered non-2xx
  // (a 500 on one endpoint is "connected but erroring", NOT "disconnected").
  markBackend(true);
  // 401 → credentials missing/rejected. Prompt once and retry; on a second 401
  // (wrong creds) clear them and surface the error.
  if (res.status === 401 && !_retried) {
    const creds = promptForAuth();
    if (creds !== null) {
      setStoredAuth(creds);
      return request<T>(path, init, true);
    }
    setStoredAuth(null);
  }
  if (!res.ok) {
    if (res.status === 401) setStoredAuth(null);   // bad creds — don't keep them
    const text = await res.text().catch(() => '');
    // FastAPI wraps errors as {"detail": ...} — pull the inner message for clean
    // toasts. HTTPException uses a STRING detail; 422 validation errors use a
    // LIST of {loc, msg, type}. Previously we only handled the string form, so a
    // 422 surfaced as a bare "422 Unprocessable Entity" with no clue WHICH field
    // failed — useless for debugging. Now we flatten the validation list to
    // "field: message" so the toast (and logs) name the offending field.
    let message = `${res.status} ${res.statusText}`;
    try {
      const body = JSON.parse(text);
      if (typeof body?.detail === 'string') {
        message = body.detail;
      } else if (Array.isArray(body?.detail) && body.detail.length) {
        message = body.detail
          .map((d: { loc?: unknown[]; msg?: string }) => {
            // loc is like ["body", "file_ids", 0] — drop the leading "body".
            const field = Array.isArray(d.loc)
              ? d.loc.filter((p) => p !== 'body').join('.') : '';
            return field ? `${field}: ${d.msg ?? 'invalid'}` : (d.msg ?? 'invalid');
          })
          .join(' · ');
      }
    } catch { /* not JSON, keep status line */ }
    throw new ApiError(message, res.status);
  }
  return res.json() as Promise<T>;
}

export interface ApiSearchResult {
  provider_id: string;
  title: string | null;
  year: number | null;
  overview: string | null;
  poster_url: string | null;
  popularity: number | null;
  media_type: 'movie' | 'tv' | 'anime' | 'music';
  /** Alternate titles when the provider exposes them (TVDB always, TMDB when
   *  original_name differs, AniDB always). Up to 5; null when none. */
  aliases: string[] | null;
}

export interface ApiSearchResponse {
  provider: string;
  results: ApiSearchResult[];
}

export interface ApiActivityJob {
  name: string;
  label: string;
  active: boolean;
  done: number;
  total: number | null;
}
export interface ApiActivity {
  jobs: ApiActivityJob[];
  active: boolean;
  /** One-shot summary of what a restart cleaned up, or null if none. */
  boot: { scans_reset: number; files_reset: number; at: number } | null;
}

export class ApiError extends Error {
  // Explicit field + assignment rather than a parameter property, so the
  // class is fully type-erasable (parameter properties emit runtime code,
  // which `erasableSyntaxOnly` disallows).
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export const api = {
  health: () => request<{ status: string; version: string }>('/health'),
  listFiles: (params?: { media_type?: string; status?: string; limit?: number }) => {
    const q = new URLSearchParams();
    if (params?.media_type) q.set('media_type', params.media_type);
    if (params?.status) q.set('status', params.status);
    if (params?.limit) q.set('limit', String(params.limit));
    const qs = q.toString();
    return request<ApiMediaFile[]>(`/files${qs ? `?${qs}` : ''}`);
  },
  listScans: () => request<ApiScan[]>('/scans'),
  getScan: (id: number) => request<ApiScan>(`/scans/${id}`),
  /** Background-activity snapshot — boot recovery summary + any running
   *  heal / warm-up job. Polled by the header activity indicator. */
  getActivity: () => request<ApiActivity>('/activity'),
  // Bug A: optional `root_paths` lets callers walk multiple library
  // roots in one scan (library_root + every watch folder). When
  // omitted, the backend falls back to walking only `root_path`.
  // The first entry of `root_paths` becomes the Scan history row's
  // primary display path; all entries get walked.
  createScan: (root_path: string, root_paths?: string[]) =>
    request<ApiScan>('/scans', {
      method: 'POST',
      body: JSON.stringify(root_paths && root_paths.length > 0
        ? { root_path, root_paths }
        : { root_path }),
    }),
  rematchFile: (fileId: number) =>
    request<ApiMediaFile>(`/files/${fileId}/rematch`, { method: 'POST' }),
  /** M5 — content-hash identify: hash the file's bytes (OSDb 64-bit), ask
   *  OpenSubtitles which release it is, and pin the resulting TMDB match.
   *  Works even when the filename is garbage. Requires an OpenSubtitles API
   *  key. Returns the updated file with its freshly-pinned match. */
  identifyByHash: (fileId: number) =>
    request<ApiMediaFile>(`/files/${fileId}/identify-by-hash`, { method: 'POST' }),
  /** #11 — download OpenSubtitles subtitles for one file as `<stem>.<lang>.srt`
   *  sidecars (hash-first, falls back to the selected match's TMDB id + S/E).
   *  Needs an API key; downloads also need account login. */
  fetchSubtitles: (fileId: number) =>
    request<{ saved: string[]; count: number; languages: string[] }>(
      `/files/${fileId}/fetch-subtitles`, { method: 'POST' }),
  /** Re-parse the EXISTING library in place and re-match it. A normal scan
   *  skips already-indexed files, so parser + folder-lock improvements only
   *  reach NEW files; this re-runs the parser on every stored file so they
   *  apply to the current library without a destructive DB reset. Manual
   *  pins + rename history are preserved. Returns a Scan row to poll. */
  reparseLibrary: () =>
    request<ApiScan>('/scans/reparse', { method: 'POST' }),
  /** Hard-delete a MediaFile and remove the underlying file from disk.
   *  Irreversible. UI must show a confirm dialog before calling this —
   *  the backend also requires ?confirm=true as a second guard. */
  deleteFile: (fileId: number, opts?: { keepOnDisk?: boolean }) => {
    const q = new URLSearchParams({ confirm: 'true' });
    if (opts?.keepOnDisk) q.set('keep_on_disk', 'true');
    return request<{ deleted: number; disk: string; path: string }>(
      `/files/${fileId}?${q.toString()}`,
      { method: 'DELETE' },
    );
  },
  /** Delete many files in ONE request (the duplicate "keep best, delete the
   *  rest" flow). Each file is processed independently server-side, so the
   *  result reports which ids were deleted and which failed (with a reason)
   *  — a locked/out-of-root file never aborts the rest of the batch. */
  deleteFiles: (fileIds: number[], opts?: { keepOnDisk?: boolean }) =>
    request<{ deleted: number[]; failed: { id: number; error: string }[]; count: number }>(
      '/files/bulk-delete',
      { method: 'POST', body: JSON.stringify({ file_ids: fileIds, keep_on_disk: !!opts?.keepOnDisk }) },
    ),
  rematchAll: (params?: { media_type?: string; limit?: number }) => {
    const q = new URLSearchParams();
    if (params?.media_type) q.set('media_type', params.media_type);
    if (params?.limit) q.set('limit', String(params.limit));
    const qs = q.toString();
    return request<{ files_processed: number; files_with_matches: number }>(
      `/rematch-all${qs ? `?${qs}` : ''}`,
      { method: 'POST' },
    );
  },
  search: (provider: string, query: string, type: 'movie' | 'tv' | 'auto' = 'auto') => {
    const q = new URLSearchParams({ q: query, type });
    return request<ApiSearchResponse>(`/search/${provider}?${q.toString()}`);
  },

  /** AniDB poster lookup — rate-limited per request on first call, then cached.
   *  `error` is set when AniDB's HTTP API rejects us (usually: client not
   *  registered). Once non-null it stays the same across calls until the user
   *  saves a new client/clientver in Settings. */
  anidbPicture: (aid: string) =>
    request<{
      aid: string;
      picture_url: string | null;
      error: string | null;
      /** 'banned' = transient IP block (wait + retry). 'rejected' = our
       *  client name/version isn't registered (user must fix in Settings).
       *  'error' = generic transient failure. Null = ok. */
      error_kind: 'banned' | 'rejected' | 'error' | null;
    }>(
      `/search/anidb/picture/${aid}`,
    ),

  /** Full episode list for one provider series. Used by the CoverPopup to
   *  overlay real titles + air dates onto the file-derived row list, and to
   *  surface "missing episode" gaps the user has no files for. Cached
   *  process-side on the backend. */
  seriesEpisodes: (provider: string, providerId: string, season?: number) => {
    const q = season != null ? `?season=${season}` : '';
    return request<{
      provider: string;
      provider_id: string;
      season: number | null;
      episodes: {
        season: number;
        episode: number;
        /** Series-wide absolute number (TVDB/TMDB cross-ref anime); null
         *  when the provider doesn't expose it. The popup pairs absolute-
         *  named files ("- 60") against this, not the local episode. */
        absolute_number: number | null;
        title: string | null;
        air_date: string | null;
        overview: string | null;
        runtime: number | null;
      }[];
    }>(`/series/${provider}/${providerId}/episodes${q}`);
  },

  updateFileStatus: (fileId: number, status: string) =>
    request<ApiMediaFile>(`/files/${fileId}`, {
      method: 'PATCH',
      body: JSON.stringify({ status }),
    }),

  bulkStatus: (ids: number[], status: string) =>
    request<{ updated: number }>('/files/bulk-status', {
      method: 'POST',
      body: JSON.stringify({ ids, status }),
    }),

  selectMatch: (fileId: number, matchId: number) =>
    request<ApiMediaFile>(`/files/${fileId}/select/${matchId}`, { method: 'POST' }),

  selectManualMatch: (fileId: number, match: {
    provider: string;
    provider_id: string;
    title?: string | null;
    year?: number | null;
    poster_url?: string | null;
    overview?: string | null;
    media_type?: string;
  }) =>
    request<ApiMediaFile>(`/files/${fileId}/select-manual`, {
      method: 'POST',
      body: JSON.stringify(match),
    }),

  /** Bulk-pin one match across N files. Used by the "Match all to..." flow
   *  in the Needs matching section. Server marks every new Match row with
   *  is_manual=true so subsequent heal/rematch leaves them alone. */
  bulkSelectManualMatch: (body: {
    file_ids: number[];
    provider: string;
    provider_id: string;
    title?: string | null;
    year?: number | null;
    poster_url?: string | null;
    overview?: string | null;
    media_type?: string;
  }) =>
    request<{ updated: number; skipped: number }>(`/files/bulk-select-manual`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  getSettings: () => request<Record<string, unknown>>('/settings'),

  putSettings: (values: Record<string, unknown>) =>
    request<{ updated: number }>('/settings', {
      method: 'PUT',
      body: JSON.stringify({ values }),
    }).then((res) => {
      // A save may have just started background work (e.g. the tech-tag backfill
      // when MediaInfo is enabled). Nudge the activity poller to check NOW
      // instead of waiting out its idle interval, so the progress pill appears
      // without a manual refresh. Guarded for non-DOM/test contexts.
      try { window.dispatchEvent(new Event('kira:activity-refresh')); } catch { /* no window */ }
      return res;
    }),

  testProvider: (provider: string) =>
    request<{ ok: boolean; detail: string | null; latency_ms: number | null }>(
      `/settings/providers/${provider}/test`,
      { method: 'POST' },
    ),

  /** Sonarr integration: validate URL+API key AND fetch the user's
   *  quality profiles + root folders in one call. Used by the Settings
   *  → Integrations panel both when the user clicks "Test connection"
   *  (passes url+api_key inline) and on initial settings-page load
   *  (omits both — backend reads from saved settings).
   *
   *  Returns ok=true on success with `quality_profiles` and `root_folders`
   *  populated for dropdown UI; ok=false with `detail` for any failure
   *  (unreachable, bad API key, sonarr-side 5xx, etc.). */
  testSonarr: (body?: { url?: string; api_key?: string }) =>
    request<{
      ok: boolean;
      detail: string | null;
      version: string | null;
      quality_profiles: Array<{ id: number; name: string }> | null;
      root_folders: Array<{ path: string; freeSpace?: number | null }> | null;
    }>('/integrations/sonarr/test', {
      method: 'POST',
      body: JSON.stringify(body ?? {}),
    }),

  /** Send Kira's missing-episode list to Sonarr in one round-trip.
   *  The backend resolves the TVDB id from the Match row (cross-refs
   *  Fribb for AniDB matches) and decides anime-vs-standard series
   *  type — frontend just supplies the Match id + season + episode
   *  numbers Kira computed as missing from the cluster.
   *
   *  Throws on 4xx (e.g. "Sonarr not configured" or "TMDB-only match")
   *  so the caller's catch shows the backend's detail in a toast. */
  sonarrSendMissing: (body: {
    match_id: number;
    season: number;
    episode_numbers: number[];
  }) =>
    request<{
      ok: boolean;
      detail: string | null;
      queued: number;
      series_was_added: boolean;
      sonarr_series_title: string | null;
      skipped_episodes: number[] | null;
    }>('/integrations/sonarr/send-missing', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  /** Tier 1.5 live template preview: render naming templates against the
   *  user's own recent matched files using the REAL backend engine, so the
   *  Settings → Naming preview is a true mirror of what a rename writes. Any
   *  omitted per-type template falls back to the backend's Plex default. */
  previewTemplate: (body: {
    movie?: string; tv?: string; anime?: string; music?: string; samples_per_type?: number;
  }) =>
    request<{
      samples: { media_type: string; filename: string; rendered: string; error: string | null }[];
    }>('/rename/preview-template', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  /** Phase 2 live progress: poll Sonarr's `/queue` and surface what
   *  it's currently downloading. With no match_id, returns every in-
   *  flight download (used by library-grid cover-card status pills);
   *  with match_id, filters to one series + season so the popup can
   *  paint per-row progress bars on the missing-episode rows.
   *
   *  Backend caches the raw queue for 4s in-process, so calling this
   *  every 4s from a popup AND every 12s from the library grid
   *  produces only ~1 Sonarr round-trip per 4s window in steady state.
   *
   *  Throws on 4xx — typically "Sonarr URL isn't configured." Callers
   *  should treat that as "feature not enabled" and stop polling. */
  /** Ask the backend to look at every unmatched / low-confidence file
   *  and pin a match using Sonarr's authoritative metadata when the
   *  file lives under a Sonarr-managed folder.
   *
   *  No body → heal everything Kira couldn't match. Pass `file_ids`
   *  to scope to a specific cluster (the popup's "Sync from Sonarr"
   *  button does this so the user only heals what they're looking at).
   *
   *  Returns counts: `healed` files actually pinned, `no_sonarr_match`
   *  files Kira couldn't tie to any Sonarr series, `series_pinned`
   *  distinct shows that contributed at least one heal.
   *
   *  Throws on 4xx (Sonarr unreachable). Empty heal returns ok=true
   *  with healed=0; caller decides whether to toast or stay silent. */
  sonarrHealUnmatched: (body?: { file_ids?: number[]; confidence_threshold?: number }) =>
    request<{
      ok: boolean;
      healed: number;
      skipped: number;
      no_sonarr_match: number;
      series_pinned: number;
      detail: string | null;
    }>('/integrations/sonarr/heal-unmatched', {
      method: 'POST',
      body: JSON.stringify(body ?? {}),
    }),

  sonarrQueue: (params?: { match_id?: number | null }) => {
    const q = new URLSearchParams();
    if (params?.match_id != null) q.set('match_id', String(params.match_id));
    const qs = q.toString();
    return request<{
      items: Array<{
        tvdb_id: number;
        // Reverse cross-ref via Fribb — null for non-anime or when Fribb
        // has no entry for this (tvdb_id, season). Lets the library-grid
        // cover-card pills match AniDB-only cards (which don't carry
        // their own TVDB id on `item.providers`).
        anidb_aid: number | null;
        season: number;
        episode_number: number;
        episode_title: string | null;
        // "queued" | "searching" | "downloading" | "importing" |
        // "completed" | "failed" | "warning" — see _normalize_status
        // in backend/kira/integrations/sonarr.py for the source of truth.
        status: string;
        progress_pct: number;   // 0..100
        eta_seconds: number | null;
        size_bytes: number | null;
        size_left_bytes: number | null;
        release_title: string | null;
        protocol: string | null;
        error_message: string | null;
        download_client: string | null;
        // Sonarr's own identifiers — opaque tokens we pass back to
        // /retry-import when the user clicks "Force import" on a stuck
        // entry. queue_id is the row id; download_id is the torrent
        // hash / NZB id.
        queue_id: number | null;
        download_id: string | null;
        // True when Sonarr's status messages indicate the "Downloaded
        // - Unable to Import Automatically" trap. The popup uses this
        // to render a distinct "Stuck — manual import needed" banner
        // + "Force import" button instead of the generic Warning UI.
        needs_manual_import: boolean;
      }>;
      cached_at: number;  // Unix seconds; the snapshot's freshness moment
    }>(`/integrations/sonarr/queue${qs ? `?${qs}` : ''}`);
  },

  /** Preview what Sonarr would do for a stuck import — surfaces the
   *  source path, destination root, and episode mapping BEFORE the
   *  user authorises the import. The Force Import confirmation modal
   *  calls this so the user knows exactly what's about to happen
   *  physically on disk (preventing data-loss surprises like the
   *  AoT S01E05/E06 incident). */
  sonarrPreviewImport: (downloadId: string) =>
    request<{
      ok: boolean;
      candidates: Array<{
        source_path: string;
        destination_root: string;
        series_title: string;
        series_id: number;
        episode_labels: string[];
        episode_ids: number[];
        quality_name: string | null;
        release_group: string | null;
        rejection_reasons: string[];
      }>;
      detail: string | null;
    }>(`/integrations/sonarr/preview-import?download_id=${encodeURIComponent(downloadId)}`),

  /** Force Sonarr past a "Downloaded - Unable to Import Automatically"
   *  state. Sonarr's grab history already knows the right (series,
   *  episode) mapping; its automatic-import safety check refused to
   *  act on it. This call hits Sonarr's manual-import API with the
   *  mapping Sonarr ALREADY computed — Sonarr accepts and processes
   *  the import.
   *
   *  Returns `imported_count` files Sonarr accepted (usually 1 per
   *  call), `command_id` for Sonarr's async-command tracking, a
   *  `detail` field with the human-readable outcome, and (when
   *  Sonarr's history confirms the import within ~2s) a
   *  `destinations` list of actual destination paths the file
   *  landed at. If Sonarr accepted but no history record appears,
   *  `history_warning` carries the diagnostic. */
  sonarrRetryImport: (body: { download_id: string; import_mode?: 'Copy' | 'Move' | 'Hardlink' | 'Auto' }) =>
    request<{
      ok: boolean;
      imported_count: number;
      command_id: number | null;
      detail: string | null;
      destinations: string[] | null;
      history_warning: string | null;
    }>('/integrations/sonarr/retry-import', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  rename: (body: { file_ids: number[]; profile: string; op: string; library_root?: string; dry_run?: boolean; overwrite?: boolean }) =>
    request<{
      succeeded: number;
      failed: number;
      items: { file_id: number; ok: boolean; old_path: string | null; new_path: string | null; error: string | null }[];
    }>('/rename', { method: 'POST', body: JSON.stringify(body) }),

  // Sweep (or, with dry_run, preview) leftover media-server artifacts across the
  // managed library roots. dry_run=true never deletes — it returns what WOULD go.
  cleanupArtifacts: (dryRun: boolean) =>
    request<{ removed: number; items: string[]; dry_run: boolean; trashed: boolean; roots: string[] }>(
      '/cleanup/artifacts', { method: 'POST', body: JSON.stringify({ dry_run: dryRun }) },
    ),

  listHistory: (params?: { period?: 'today' | 'week' | 'all'; operation?: string }) => {
    const q = new URLSearchParams();
    if (params?.period) q.set('period', params.period);
    if (params?.operation) q.set('operation', params.operation);
    const qs = q.toString();
    return request<ApiHistoryEntry[]>(`/history${qs ? `?${qs}` : ''}`);
  },
  historyCounts: () => request<{ today: number; week: number; all: number }>('/history/counts'),
  undoHistory: (id: number) => request<ApiHistoryEntry>(`/history/${id}/undo`, { method: 'POST' }),
  undoHistoryBulk: (ids: number[]) =>
    request<{ succeeded: number; failed: number }>('/history/undo-bulk', {
      method: 'POST',
      body: JSON.stringify({ ids }),
    }),
  exportHistoryUrl: () => `${API_BASE}/history/export.csv`,

  listFolders: (path: string) => {
    const q = new URLSearchParams({ path });
    return request<{
      path: string;
      parent: string | null;
      entries: { name: string; path: string; is_dir: boolean; file_count: number | null }[];
    }>(`/folders?${q.toString()}`);
  },

  resetDatabase: () =>
    request<{ ok: number }>('/database/reset?confirm=RESET', { method: 'POST' }),

  listNotifications: (unreadOnly = false) => {
    const q = new URLSearchParams();
    if (unreadOnly) q.set('unread_only', 'true');
    const qs = q.toString();
    return request<ApiNotification[]>(`/notifications${qs ? `?${qs}` : ''}`);
  },
  markNotificationRead: (id: number) =>
    request<ApiNotification>(`/notifications/${id}/read`, { method: 'POST' }),
  markAllNotificationsRead: () =>
    request<{ updated: number }>('/notifications/read-all', { method: 'POST' }),

  getProviders: () => request<ApiProvider[]>('/providers'),
};

export interface ApiHistoryEntry {
  id: number;
  media_file_id: number | null;
  old_path: string;
  new_path: string;
  operation: string;
  media_type: string | null;
  title: string | null;
  episode_title: string | null;
  poster_url: string | null;
  created_at: string;
  undone_at: string | null;
}

export interface ApiNotification {
  id: number;
  kind: string;
  title: string;
  body: string | null;
  read: boolean;
  created_at: string;
}

export interface ApiProvider {
  key: string;             // 'tmdb' | 'tvdb' | 'anidb' | 'musicbrainz' | 'acoustid'
  name: string;
  implemented: boolean;
  configured: boolean;
  keyless: boolean;
  supports: string[];      // ['movie', 'tv', 'anime', 'music']
  note: string | null;
  // Optional provider-specific status (AniDB ban surface).
  rate_limited?: boolean;
  banned_until?: number | null;     // Unix timestamp of ban expiry
  last_error?: string | null;
  fallback_chain?: string[] | null;
}
