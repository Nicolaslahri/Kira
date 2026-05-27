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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    // FastAPI wraps errors as {"detail": "..."} — pull the inner message for clean toasts.
    let message = `${res.status} ${res.statusText}`;
    try {
      const body = JSON.parse(text);
      if (typeof body?.detail === 'string') message = body.detail;
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

export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message);
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
  createScan: (root_path: string) =>
    request<ApiScan>('/scans', { method: 'POST', body: JSON.stringify({ root_path }) }),
  rematchFile: (fileId: number) =>
    request<ApiMediaFile>(`/files/${fileId}/rematch`, { method: 'POST' }),
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
        title: string | null;
        air_date: string | null;
        overview: string | null;
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
    }),

  testProvider: (provider: string) =>
    request<{ ok: boolean; detail: string | null; latency_ms: number | null }>(
      `/settings/providers/${provider}/test`,
      { method: 'POST' },
    ),

  rename: (body: { file_ids: number[]; profile: string; op: string; library_root?: string; dry_run?: boolean; overwrite?: boolean }) =>
    request<{
      succeeded: number;
      failed: number;
      items: { file_id: number; ok: boolean; old_path: string | null; new_path: string | null; error: string | null }[];
    }>('/rename', { method: 'POST', body: JSON.stringify(body) }),

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
