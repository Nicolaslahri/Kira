import { useState, useMemo, useRef, useEffect } from 'react';
import type { MediaFile, SearchResult, CandidateData, ProviderKey, MatchData, MediaType } from '../lib/types';
import { PROVIDERS, NAMING_PROFILES, TYPE_COLOR, formatPath, poster } from '../lib/data';
import { api, ApiError, type ApiSearchResult, type ApiProvider } from '../lib/api';
import { IcSearch, IcCheck, IcX, IcArrowRight, IcShieldCheck, IcUndo, IcExternal, IcWaveform, IcSpin, IcAlertTri } from '../lib/icons';
import { Modal, Poster, ConfidenceBadge, StatusPill, Segmented } from './ui';

/** Returns the fraction (0..1) of a string's letters that are plain ASCII.
 *  Used to rank provider aliases — English / romaji titles score ~1.0,
 *  Cyrillic / Korean / Chinese score 0. Non-letters (digits, punctuation)
 *  are excluded from the denominator so "Sezon 3" doesn't get penalized
 *  just for the number, and a pure-digit string falls back to 1.0. */
export function asciiness(s: string): number {
  let letters = 0, ascii = 0;
  for (const ch of s) {
    if (/\p{L}/u.test(ch)) {
      letters++;
      if (ch.charCodeAt(0) < 128) ascii++;
    }
  }
  return letters === 0 ? 1 : ascii / letters;
}

export function highlightPath(path: string) {
  const parts = path.split('/');
  const last = parts.pop();
  const dir = parts.join('/');
  return (
    <>
      <span className="seg-dir">{dir}/</span>
      <span className="seg-new">{last}</span>
    </>
  );
}

// Map our UI ProviderKey ('TMDB') to backend slug ('tmdb').
const PROVIDER_SLUG: Record<ProviderKey, string> = {
  TMDB: 'tmdb',
  TVDB: 'tvdb',
  AniDB: 'anidb',
  MusicBrainz: 'musicbrainz',
  AcoustID: 'acoustid',
};

// Preference order per media type — picks the first one that's actually
// available (implemented AND configured) as the modal default.
const PROVIDER_PREFERENCE: Record<MediaType, ProviderKey[]> = {
  movie: ['TMDB', 'TVDB'],
  tv:    ['TVDB', 'TMDB'],
  anime: ['AniDB', 'TVDB', 'TMDB'],
  music: ['MusicBrainz'],
};

function pickDefault(file: MediaFile, providers: ApiProvider[]): ProviderKey {
  const slug = (k: ProviderKey) => PROVIDER_SLUG[k];
  const available = new Set(providers.filter(p => p.configured).map(p => p.key));
  for (const k of PROVIDER_PREFERENCE[file.mediaType] || ['TVDB']) {
    if (available.has(slug(k))) return k;
  }
  // Nothing configured — still pick the first preference so the user sees the
  // not-configured banner for the right provider.
  return (PROVIDER_PREFERENCE[file.mediaType] || ['TVDB'])[0];
}

export function ManualSearchModal({ file, onClose, onSelect }: {
  file: MediaFile;
  onClose: () => void;
  onSelect: (r: SearchResult & { _provider?: ProviderKey; _providerId?: string }) => void;
}) {
  // Seed the input with the parsed title so the first search is relevant.
  // Seed priority: the parser's title first (clean filename-derived text),
  // then artist (for music), and finally the raw filename. We deliberately
  // skip `file.match?.title` — the user opens Manual Search to REPLACE the
  // current match, so prefilling with that match's title is exactly wrong.
  const seedQuery = file.parsedTitle || file.match?.artist || file.filename;

  // Decide which TVDB/TMDB endpoint to hit based on what the file is. Without
  // this, a search for "The Drama" gets swamped by 50 TV shows when the file
  // is actually a 2026 movie.
  const searchType: 'movie' | 'tv' | 'auto' =
    file.mediaType === 'movie' ? 'movie' :
    file.mediaType === 'tv' || file.mediaType === 'anime' ? 'tv' :
    'auto';

  // Provider catalogue — loaded once on mount. Drives tab enablement.
  const [providers, setProviders] = useState<ApiProvider[]>([]);
  const [provider, setProvider] = useState<ProviderKey>('TVDB');
  const [q, setQ] = useState(seedQuery);
  const [typeFilter, setTypeFilter] = useState<'movie' | 'tv' | 'auto'>(searchType);
  const [results, setResults] = useState<ApiSearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [anidbApiError, setAnidbApiError] = useState<string | null>(null);
  const [anidbErrorKind, setAnidbErrorKind] = useState<'banned' | 'rejected' | 'error' | null>(null);
  const [sel, setSel] = useState<ApiSearchResult | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Pull provider catalogue once and pick the default tab from it.
  useEffect(() => {
    let cancelled = false;
    api.getProviders()
      .then(list => {
        if (cancelled) return;
        setProviders(list);
        setProvider(pickDefault(file, list));
      })
      .catch(() => { /* leave default 'TVDB' as-is */ });
    return () => { cancelled = true; };
  }, [file]);

  const providerStatus = useMemo(() => {
    const slug = PROVIDER_SLUG[provider];
    return providers.find(p => p.key === slug);
  }, [provider, providers]);

  useEffect(() => { inputRef.current?.focus(); }, []);

  // Debounced search whenever provider or query changes — but only if the
  // current provider is actually usable. Otherwise show the banner instead.
  useEffect(() => {
    setSel(null);
    if (providerStatus && (!providerStatus.implemented || !providerStatus.configured)) {
      setResults([]);
      setLoading(false);
      setError(null);
      return;
    }
    const query = q.trim();
    if (!query) {
      setResults([]);
      setLoading(false);
      setError(null);
      return;
    }
    setLoading(true);
    setError(null);
    // Track if the search has been superseded (provider/query change) so a
    // stale lazy-poster fetch doesn't mutate a newer result set.
    let cancelled = false;
    const handle = setTimeout(async () => {
      try {
        const res = await api.search(PROVIDER_SLUG[provider], query, typeFilter);
        if (cancelled) return;
        setResults(res.results);

        // AniDB doesn't include images in search (the title dump has no
        // poster URLs). Fire one /anidb/picture/{aid} per result and update
        // each row as the response comes back. Backend serializes them via
        // a 4-second-per-call rate limit, so posters fill in progressively.
        if (provider === 'AniDB') {
          // Reset any prior banner — this is a fresh search.
          setAnidbApiError(null);
          setAnidbErrorKind(null);
          // Track whether we've already noticed AniDB rejecting our client.
          // Once it does, every call returns the same error; stop firing.
          let apiDead = false;
          for (const r of res.results) {
            if (cancelled || apiDead) break;
            if (r.poster_url || !r.provider_id) continue;
            api.anidbPicture(r.provider_id)
              .then(({ picture_url, error: apiErr, error_kind }) => {
                if (cancelled) return;
                if (apiErr) {
                  apiDead = true;
                  setAnidbApiError(apiErr);
                  setAnidbErrorKind(error_kind);
                  return;
                }
                if (!picture_url) return;
                setResults(curr => curr.map(x =>
                  x.provider_id === r.provider_id ? { ...x, poster_url: picture_url } : x
                ));
              })
              .catch(() => { /* swallow — keep gradient initials */ });
          }
        }
      } catch (e) {
        const err = e as Error;
        if (e instanceof ApiError && e.status === 400) {
          setError(err.message);
        } else if (err.message.includes('Failed to fetch')) {
          setError('Backend not reachable — is uvicorn running?');
        } else {
          setError(err.message);
        }
        setResults([]);
      } finally {
        setLoading(false);
      }
    }, 300);
    return () => { cancelled = true; clearTimeout(handle); };
  }, [q, provider, typeFilter, providerStatus]);

  const allTabs: ProviderKey[] = ['TMDB', 'TVDB', 'AniDB', 'MusicBrainz'];
  const providerMeta = PROVIDERS[provider];
  const isMusic = provider === 'MusicBrainz';

  const handleSelect = (r: ApiSearchResult) => {
    // Adapt backend shape → frontend SearchResult so the rest of the app keeps
    // working with the existing onSelect contract. _providerId is the backend
    // id we'll send to POST /files/{id}/select-manual.
    //
    // `_posterUrl` carries the provider's actual remote poster URL through
    // to the App-level handler, which forwards it to the backend in the
    // select-manual / bulk-select-manual payload. Without this, the
    // backend's poster_url stayed at whatever the prior auto-match wrote
    // (or null), so the cover never updated after a manual pick — the
    // user got a "Match updated" toast but the cover looked identical.
    // `poster` (synthesized init+tint) is still computed for any UI
    // fallback that needs a placeholder while the remote URL loads.
    const sr: SearchResult & {
      _provider?: ProviderKey;
      _providerId?: string;
      _posterUrl?: string | null;
    } = {
      title: r.title ?? undefined,
      year: r.year,
      mediaType: r.media_type,
      poster: poster(r.title || '', r.year),
      overview: r.overview ?? undefined,
      _provider: provider,
      _providerId: r.provider_id,
      _posterUrl: r.poster_url ?? null,
    };
    onSelect(sr);
    onClose();
  };

  // Default-tab explainer: when pickDefault landed on a NON-preferred provider
  // for this media type (e.g. TVDB for anime because AniDB isn't configured),
  // show a one-line banner so the user understands why.
  const preferredProvider = (PROVIDER_PREFERENCE[file.mediaType] || [])[0];
  const defaultExplainer =
    preferredProvider && preferredProvider !== provider &&
    providers.length > 0 &&
    !providers.find(p => p.key === PROVIDER_SLUG[preferredProvider])?.configured
      ? `Showing ${provider} — set up ${preferredProvider} in Settings → Connections for best ${file.mediaType} results.`
      : null;

  // Sub-line shows parent folder + filename so 5 "movie.mp4" files in
  // different directories stay distinguishable in the modal header.
  const folderHint = file.folder ? `${file.folder} / ` : '';
  const modalSub = file?.filename ? `${folderHint}${file.filename}` : 'Find a match for this file';

  return (
    <Modal
      title="Manual search"
      sub={modalSub}
      onClose={onClose}
      size="lg"
      footer={
        <>
          <div className="text-muted text-sm">
            Couldn't find it? <a href={providerRootUrl(provider)} target="_blank" rel="noreferrer" style={{ color: 'var(--accent)' }}>Open {provider}.org</a>
          </div>
          <div className="right">
            <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
            <button className="btn btn-primary" disabled={!sel} onClick={() => { if (sel) handleSelect(sel); }}>
              <IcCheck /> Use this match
            </button>
          </div>
        </>
      }
    >
      <div className="provider-tabs">
        {allTabs.map(p => {
          const slug = PROVIDER_SLUG[p];
          const info = providers.find(x => x.key === slug);
          const unavailable = info ? (!info.implemented || !info.configured) : false;
          return (
            <button
              key={p}
              className={`provider-tab ${provider === p ? 'on' : ''} ${unavailable ? 'disabled' : ''}`}
              onClick={() => setProvider(p)}
              title={unavailable ? (info && !info.implemented ? 'Coming soon' : 'Not configured') : undefined}
            >
              <span className="provider-dot" style={{ background: PROVIDERS[p].color, opacity: unavailable ? 0.4 : 1 }} />
              {p}
              {info && !info.implemented ? <span className="pill pill-soon">soon</span> : null}
              {info && info.implemented && !info.configured ? <span className="pill pill-warn">setup</span> : null}
            </button>
          );
        })}
      </div>

      <div className="text-xs text-muted" style={{ margin: '8px 2px 12px', display: 'flex', gap: 8, alignItems: 'center' }}>
        <span className="provider-dot" style={{ background: providerMeta.color }} />
        {providerMeta.desc}
      </div>

      {providerStatus && !providerStatus.implemented ? (
        <div className="onboarding-state" style={{ marginBottom: 12, background: 'rgba(255,255,255,0.04)' }}>
          <IcAlertTri /><span>
            <b>{providerStatus.name} isn't available yet.</b>{' '}
            We'll wire it up in a future release. Try another tab.
          </span>
        </div>
      ) : providerStatus && !providerStatus.configured ? (
        <div className="onboarding-state error" style={{ marginBottom: 12, alignItems: 'center', gap: 12 }}>
          <IcAlertTri />
          <span style={{ flex: 1 }}>
            <b>{providerStatus.name} needs an API key.</b>{' '}
            Add one in Settings → Connections, then come back.
          </span>
          <button
            className="btn btn-sm btn-primary"
            onClick={() => { window.location.hash = '#/settings'; onClose(); }}
          >
            Open Settings
          </button>
        </div>
      ) : null}

      <div className="search-input-big">
        {loading ? (
          <span style={{ color: 'var(--ink-3)' }}><IcSpin /></span>
        ) : (
          <IcSearch style={{ width: 16, height: 16, color: 'var(--ink-3)' }} />
        )}
        <input
          ref={inputRef}
          value={q}
          onChange={e => setQ(e.target.value)}
          placeholder={
            isMusic ? 'Search artist or album…' :
            provider === 'AniDB' ? 'Search anime title…' :
            'Search title…'
          }
        />
        {!isMusic && provider !== 'AniDB' ? (
          <div className="seg" style={{ padding: 2, marginLeft: 8 }}>
            <button className={`seg-btn ${typeFilter === 'movie' ? 'on' : ''}`} onClick={() => setTypeFilter('movie')}>Movies</button>
            <button className={`seg-btn ${typeFilter === 'tv' ? 'on' : ''}`} onClick={() => setTypeFilter('tv')}>TV</button>
            <button className={`seg-btn ${typeFilter === 'auto' ? 'on' : ''}`} onClick={() => setTypeFilter('auto')}>Both</button>
          </div>
        ) : null}
      </div>

      {defaultExplainer ? (
        <div className="default-tab-banner">
          <IcAlertTri />
          <span>{defaultExplainer}</span>
          <button
            className="btn btn-sm btn-ghost"
            onClick={() => { window.location.hash = '#/settings'; onClose(); }}
          >Open Settings</button>
        </div>
      ) : null}

      {error ? (
        <div className="onboarding-state error" style={{ marginTop: 8 }}>
          <IcAlertTri /><span>{error}</span>
        </div>
      ) : null}

      {provider === 'AniDB' && anidbApiError ? (
        <div className="onboarding-state" style={{ marginTop: 8, background: 'rgba(255,200,80,0.06)', borderColor: 'rgba(255,200,80,0.20)', alignItems: 'flex-start', gap: 12 }}>
          <IcAlertTri />
          <span style={{ flex: 1, fontSize: 12, lineHeight: 1.5 }}>
            {anidbErrorKind === 'banned' ? (
              <>
                <b>AniDB temporarily banned our IP.</b> {anidbApiError}<br />
                This usually happens when too many requests go out in a short
                window. Kira will hold off on AniDB calls automatically for
                ~12 hours. Title search + matching against the local title
                dump keep working; only cover art and live episode metadata
                are paused.
              </>
            ) : anidbErrorKind === 'rejected' ? (
              <>
                <b>AniDB doesn't recognize this client.</b> {anidbApiError}<br />
                Register Kira at{' '}
                <a href="https://anidb.net/software/add_program" target="_blank" rel="noreferrer" style={{ color: 'var(--accent)' }}>
                  anidb.net/software/add_program
                </a>{' '}
                (requires moderator approval), then paste the approved name + version in{' '}
                <a href="#/settings" onClick={() => onClose()} style={{ color: 'var(--accent)' }}>Settings → AniDB</a>.
              </>
            ) : (
              <>
                <b>AniDB picture lookup failed.</b> {anidbApiError}<br />
                Title search and matching still work — only cover art is affected.
              </>
            )}
          </span>
        </div>
      ) : null}

      {!error && (
        <div className="search-grid" style={{ marginTop: 12 }}>
          {results.map((r, i) => {
            // AniDB results land without poster_url and fill in lazily at 1/4s.
            // Show a shimmer skeleton until each arrives so the grid doesn't
            // look broken with blank cells.
            const showAniDBShimmer = provider === 'AniDB' && !r.poster_url && !anidbApiError;
            // Show a short overview snippet under the title instead of the
            // old "a.k.a." alias dump. Aliases were dense and unreadable
            // (Polish / Kanji titles for the same show); overview tells
            // you what the show IS, which is what disambiguates "is this
            // the right result?". Falls back to nothing when the
            // provider didn't return an overview — cleaner cards in
            // that case rather than a stub line.
            const overviewSnippet = r.overview
              ? r.overview.length > 120
                ? r.overview.slice(0, 117).trimEnd() + '…'
                : r.overview
              : null;
            return (
              <div key={`${r.provider_id}-${i}`}
                className={`search-result ${sel === r ? 'selected' : ''}`}
                style={{ ['--i' as never]: i }}
                onClick={() => setSel(r)}
                onDoubleClick={() => handleSelect(r)}>
                {showAniDBShimmer ? (
                  <div className="poster size-md anidb-poster-loading">
                    <span className="shimmer-bar" />
                  </div>
                ) : (
                  <Poster
                    data={poster(r.title || '', r.year)}
                    imgUrl={r.poster_url}
                  />
                )}
                <div style={{ minWidth: 0 }}>
                  <div className="search-result-title">{r.title || '—'}</div>
                  {overviewSnippet ? (
                    <div
                      className="search-result-overview"
                      title={r.overview ?? undefined}
                      style={{
                        fontSize: 11.5,
                        color: 'var(--ink-3)',
                        lineHeight: 1.4,
                        marginTop: 2,
                        marginBottom: 4,
                        display: '-webkit-box',
                        WebkitLineClamp: 2,
                        WebkitBoxOrient: 'vertical',
                        overflow: 'hidden',
                      }}
                    >
                      {overviewSnippet}
                    </div>
                  ) : null}
                  <div className="search-result-meta">
                    <span>{r.year ?? '—'}</span>
                    <span style={{ color: 'var(--ink-4)' }}>·</span>
                    <span style={{ color: r.media_type === 'tv' || r.media_type === 'anime' ? 'var(--info)' : 'var(--ink-3)' }}>
                      {r.media_type === 'tv' ? 'TV' : r.media_type === 'anime' ? 'Anime' : 'Movie'}
                    </span>
                    {r.popularity ? <span style={{ marginLeft: 'auto', color: 'var(--ink-3)' }}>★ {r.popularity.toFixed(1)}</span> : null}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {!loading && !error && results.length === 0 && q.trim() ? (
        <div style={{ padding: 28, textAlign: 'center', color: 'var(--ink-3)', fontSize: 13 }}>
          No results from {provider} for "{q}". Try a different provider or query.
        </div>
      ) : null}
    </Modal>
  );
}

export function RenamePreviewModal({ files, onClose, onApply, defaultProfile = 'Plex', defaultOp = 'move' }: {
  files: MediaFile[];
  onClose: () => void;
  onApply: (opts: { profile: string; op: string }) => void;
  defaultProfile?: string;
  defaultOp?: string;
}) {
  const [profile, setProfile] = useState(defaultProfile);
  const [op, setOp] = useState(defaultOp);
  const [includeArt, setIncludeArt] = useState(true);

  // A file is renameable only when it has a REAL provider match — the
  // adapter synthesises a placeholder `match` object from parsed data
  // for display, so we filter on provider+providerId not just `match`.
  const eligible = files.filter(f => f.match?.provider && f.match?.providerId);
  const skipped = files.length - eligible.length;
  const preview = useMemo(() => eligible.slice(0, 4), [eligible]);

  const firstType = eligible[0]?.mediaType;
  const templateKey = firstType === 'tv' ? 'tv' : firstType === 'anime' ? 'anime' : firstType === 'music' ? 'music' : 'movie';

  // Real dry-run preview: render the EXACT path the backend rename would
  // write (Jinja2 engine + franchise-collapse + in-place rooting), keyed by
  // file id. Replaces the old client-side formatPath() which used legacy
  // {n} syntax and could disagree with what actually lands on disk. Reuses
  // the rename endpoint with dry_run:true → same code path = guaranteed
  // consistency. Recomputes when the shown files / profile / op change.
  const [targets, setTargets] = useState<Record<string, string>>({});
  const [previewLoading, setPreviewLoading] = useState(false);
  useEffect(() => {
    if (preview.length === 0) { setTargets({}); return; }
    let cancelled = false;
    setPreviewLoading(true);
    api.rename({ file_ids: preview.map(f => Number(f.id)), profile, op, dry_run: true })
      .then(res => {
        if (cancelled) return;
        const next: Record<string, string> = {};
        for (const it of res.items) if (it.new_path) next[String(it.file_id)] = it.new_path;
        setTargets(next);
      })
      .catch(() => { if (!cancelled) setTargets({}); })
      .finally(() => { if (!cancelled) setPreviewLoading(false); });
    return () => { cancelled = true; };
  }, [preview, profile, op]);

  return (
    <Modal
      title={files.length === 1 ? 'Rename preview' : `Rename ${eligible.length} files`}
      sub={files.length === 1 ? files[0].filename : `${eligible.length} files ready to be organized`}
      onClose={onClose}
      size="lg"
      footer={
        <>
          <div className="preview-counter">
            <IcShieldCheck />
            <span><b>No file is touched</b> until you click Apply.</span>
          </div>
          <div className="right">
            <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
            <button className="btn btn-primary" onClick={() => onApply({ profile, op })}>
              <IcCheck /> Apply · Rename {eligible.length} {eligible.length === 1 ? 'file' : 'files'}
            </button>
          </div>
        </>
      }
    >
      {skipped > 0 ? (
        <div style={{
          marginBottom: 14, padding: '10px 12px',
          borderRadius: 8,
          background: 'rgba(255,177,74,0.10)',
          border: '1px solid rgba(255,177,74,0.32)',
          color: 'var(--ink-2)', fontSize: 12,
        }}>
          <b style={{ color: 'var(--conf-mid)' }}>{skipped}</b> selected file{skipped === 1 ? '' : 's'} ha{skipped === 1 ? 's' : 've'} no match yet and will be skipped. Match {skipped === 1 ? 'it' : 'them'} first (or pick "Match all to…" in the bulk bar) to include {skipped === 1 ? 'it' : 'them'}.
        </div>
      ) : null}

      {eligible.length === 0 ? (
        <div style={{
          padding: '20px 12px', textAlign: 'center',
          color: 'var(--ink-3)', fontSize: 13,
        }}>
          Nothing to rename — all selected files are unmatched.
        </div>
      ) : null}

      {preview.map(f => (
        <div key={f.id} className="preview-pair" style={{ marginBottom: 12 }}>
          <div className="preview-side">
            <div className="preview-side-label">From</div>
            <div className="preview-path">
              <span className="seg-dir">{f.folder}/</span>
              {f.filename}
            </div>
          </div>
          <div className="preview-arrow"><IcArrowRight /></div>
          <div className="preview-side new">
            <div className="preview-side-label">To</div>
            <div className="preview-path">
              {targets[String(f.id)]
                ? highlightPath(targets[String(f.id)].replace(/\\/g, '/'))
                : previewLoading
                  ? <span style={{ color: 'var(--ink-3)' }}>Computing…</span>
                  : highlightPath(formatPath(f, profile))}
            </div>
          </div>
        </div>
      ))}

      {eligible.length > preview.length ? (
        <div style={{ textAlign: 'center', color: 'var(--ink-3)', fontSize: 12, padding: '4px 0 14px' }}>
          + {eligible.length - preview.length} more file{eligible.length - preview.length === 1 ? '' : 's'}…
        </div>
      ) : null}

      <div className="preview-options">
        <div className="opt-group">
          <div className="opt-label">File operation</div>
          <Segmented value={op} onChange={setOp} options={[
            { value: 'move', label: 'Move' },
            { value: 'copy', label: 'Copy' },
            { value: 'symlink', label: 'Symlink' },
            { value: 'hardlink', label: 'Hardlink' },
          ]} />
          <div className="text-xs text-muted">
            {op === 'move' && 'Source files are moved to the library.'}
            {op === 'copy' && 'Source files are duplicated; originals stay.'}
            {op === 'symlink' && 'Library entries are soft links to the source.'}
            {op === 'hardlink' && 'Library entries share inodes — zero extra disk used.'}
          </div>
        </div>
        <div className="opt-group">
          <div className="opt-label">Naming profile</div>
          <Segmented value={profile} onChange={setProfile} options={[
            { value: 'Plex', label: 'Plex' },
            { value: 'Jellyfin', label: 'Jellyfin' },
            { value: 'Kodi', label: 'Kodi' },
            { value: 'Custom', label: 'Custom' },
          ]} />
          <div className="text-xs text-muted text-mono" style={{ wordBreak: 'break-all' }}>
            {NAMING_PROFILES[profile][templateKey]}
          </div>
        </div>
      </div>

      <div style={{ marginTop: 16, display: 'flex', gap: 10, alignItems: 'center' }}>
        <label style={{ display: 'inline-flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 12, color: 'var(--ink-2)' }}>
          <input type="checkbox" checked={includeArt} onChange={() => setIncludeArt(!includeArt)} style={{ accentColor: 'var(--accent)' }} />
          Also download artwork and metadata sidecars (.nfo)
        </label>
      </div>
    </Modal>
  );
}

export function KeyboardShortcutsModal({ onClose }: { onClose: () => void }) {
  const groups = [
    { label: 'Navigation', items: [
      { keys: ['j'], desc: 'Next file' },
      { keys: ['k'], desc: 'Previous file' },
      { keys: ['/'], desc: 'Focus search' },
      { keys: ['g', 'd'], desc: 'Go to Dashboard' },
      { keys: ['g', 'r'], desc: 'Go to Review' },
      { keys: ['g', 'h'], desc: 'Go to History' },
      { keys: ['g', 's'], desc: 'Go to Settings' },
    ]},
    { label: 'Actions', items: [
      { keys: ['a'], desc: 'Approve current' },
      { keys: ['r'], desc: 'Reject current' },
      { keys: ['m'], desc: 'Manual search' },
      { keys: ['Enter'], desc: 'Open details' },
      { keys: ['⌘', '⇧', 'A'], desc: 'Approve all high-confidence' },
      { keys: ['⌘', 'Enter'], desc: 'Open rename preview' },
      { keys: ['?'], desc: 'This panel' },
    ]},
  ];
  return (
    <Modal title="Keyboard shortcuts" sub="Built for fast review" onClose={onClose}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 32 }}>
        {groups.map(g => (
          <div key={g.label}>
            <div className="opt-label" style={{ marginBottom: 8 }}>{g.label}</div>
            {g.items.map((it, i) => (
              <div key={i} className="kbd-help-row">
                <span style={{ color: 'var(--ink-2)' }}>{it.desc}</span>
                <span className="keys">
                  {it.keys.map((k, j) => <span key={j} className="kbd">{k}</span>)}
                </span>
              </div>
            ))}
          </div>
        ))}
      </div>
    </Modal>
  );
}

// Fake album tracklist for music FileDetails
function fakeTracklist(match: MatchData) {
  const SAMPLE = [
    'Airbag', 'Paranoid Android', 'Subterranean Homesick Alien', 'Exit Music (For a Film)',
    'Let Down', 'Karma Police', 'Fitter Happier', 'Electioneering', 'Climbing Up the Walls',
    'No Surprises', 'Lucky', 'The Tourist',
  ];
  const dur = (sec: number) => `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`;
  const total = match.totalTracks || 10;
  const out: { n: number; t: string; d: string }[] = [];
  for (let i = 1; i <= total; i++) {
    const isMatch = i === match.track;
    out.push({
      n: i,
      t: isMatch ? (match.trackTitle || `Track ${i}`) : (SAMPLE[(i - 1) % SAMPLE.length] || `Track ${i}`),
      d: dur(180 + ((i * 47) % 180)),
    });
  }
  return out;
}

function Meta({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <>
      <span className="text-muted" style={{ textTransform: 'uppercase', letterSpacing: '0.06em', fontWeight: 600, fontSize: 10.5 }}>{label}</span>
      <span style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{value}</span>
    </>
  );
}

function ProviderLink({ children, href }: { children: React.ReactNode; href: string }) {
  return (
    <a href={href} target="_blank" rel="noreferrer"
      style={{ color: 'var(--accent)', textDecoration: 'none', display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 12, fontFamily: 'var(--font-mono)' }}>
      {children} <IcExternal />
    </a>
  );
}

// Format the FileDetails sub line for an anime match. Falls back gracefully
// when season is missing (which is common — TVDB returns the series, not the
// specific season).
function formatAnimeSub(m: MatchData): string {
  const parts: string[] = [];
  if (m.absoluteEpisode != null) parts.push(`Episode ${String(m.absoluteEpisode).padStart(2, '0')}`);
  else if (m.episode != null)    parts.push(`Episode ${String(m.episode).padStart(2, '0')}`);
  if (m.season != null && m.episode != null) {
    parts.push(`(S${String(m.season).padStart(2, '0')}E${String(m.episode).padStart(2, '0')})`);
  }
  if (m.episodeTitle) parts.push(m.episodeTitle);
  return parts.length ? parts.join(' · ') : 'Anime · matched';
}

function formatTvSub(m: MatchData): string {
  const parts: string[] = [];
  if (m.season != null) parts.push(`Season ${m.season}`);
  if (m.episode != null) parts.push(`Episode ${m.episode}`);
  if (m.episodeTitle) parts.push(m.episodeTitle);
  return parts.length ? parts.join(' · ') : 'TV · matched';
}

// Render the SxxExx portion of a candidate sub-line. Returns empty when
// neither season nor episode is known.
function candidateEpisodeLabel(c: { season?: number; episode?: number; absoluteEpisode?: number }, isAnime: boolean): string {
  if (isAnime) {
    if (c.absoluteEpisode != null && c.season != null && c.episode != null) {
      return `Episode ${c.absoluteEpisode} (S${c.season}E${c.episode})`;
    }
    if (c.absoluteEpisode != null) return `Episode ${c.absoluteEpisode}`;
    if (c.episode != null && c.season != null) return `S${c.season}E${c.episode}`;
    if (c.episode != null) return `Episode ${c.episode}`;
    return 'Series';
  }
  if (c.season != null && c.episode != null) return `Season ${c.season} · Episode ${c.episode}`;
  if (c.episode != null) return `Episode ${c.episode}`;
  return 'Movie';
}

function tmdbUrl(id: number | string, isTV: boolean): string {
  return `https://www.themoviedb.org/${isTV ? 'tv' : 'movie'}/${id}`;
}
function anidbUrl(id: number | string): string {
  return `https://anidb.net/anime/${id}`;
}
function musicbrainzUrl(mbid: string): string {
  return `https://musicbrainz.org/release/${mbid}`;
}
function providerRootUrl(provider: string): string {
  const map: Record<string, string> = {
    TMDB: 'https://www.themoviedb.org',
    TVDB: 'https://thetvdb.com',
    AniDB: 'https://anidb.net',
    MusicBrainz: 'https://musicbrainz.org',
  };
  return map[provider] ?? '#';
}

export function FileDetailsModal({ file, onClose, onApprove, onReject, onManualSearch, onPickCandidate }: {
  file: MediaFile;
  onClose: () => void;
  onApprove: (id: string, status?: string) => void;
  onReject: (id: string) => void;
  onManualSearch: (f: MediaFile) => void;
  onPickCandidate: (id: string, c: CandidateData) => void;
}) {
  if (!file) return null;

  const isMusic = file.mediaType === 'music';
  const isAnime = file.mediaType === 'anime';
  const isTV    = file.mediaType === 'tv';
  const m       = file.match;

  // Load providers once to know what Manual Search will actually open into,
  // so the footer button says the truth instead of the wishful "Search AniDB".
  const [providers, setProviders] = useState<ApiProvider[]>([]);
  useEffect(() => { api.getProviders().then(setProviders).catch(() => { /* */ }); }, []);
  const providerName = useMemo(() => {
    const def = pickDefault(file, providers);
    return def;
  }, [file, providers]);

  const title = !m ? 'No match' :
    isMusic ? `${m.artist} — ${m.trackTitle}` :
    `${m.title}${m.year ? ' · ' + m.year : ''}`;
  const sub = !m ? 'Filename could not be matched automatically'
    : isMusic ? `From ${m.album} (${m.albumYear || m.year}) · Track ${m.track}/${m.totalTracks} · ${m.duration}`
    : isAnime ? formatAnimeSub(m)
    : isTV    ? formatTvSub(m)
    : 'Movie · TMDB match';

  return (
    <Modal
      title={title}
      sub={sub}
      onClose={onClose}
      size="lg"
      footer={
        <>
          <button className="btn btn-ghost" onClick={() => onManualSearch(file)}>
            <IcSearch /> Search {providerName} manually
          </button>
          <div className="right">
            {file.status === 'pending' ? (
              <>
                <button className="btn btn-danger" onClick={() => { onReject(file.id); onClose(); }}>
                  <IcX /> Reject
                </button>
                <button className="btn btn-primary" disabled={!file.match} onClick={() => { onApprove(file.id); onClose(); }}>
                  <IcCheck /> Approve
                </button>
              </>
            ) : (
              <button className="btn" onClick={() => { onApprove(file.id, 'pending'); onClose(); }}>
                <IcUndo /> Move back to Pending
              </button>
            )}
          </div>
        </>
      }
    >
      <div style={{ display: 'grid', gridTemplateColumns: isMusic ? '160px 1fr' : '120px 1fr', gap: 20, marginBottom: 20 }}>
        <Poster
          data={isMusic ? m?.art : m?.poster}
          imgUrl={m?.posterUrl}
          size="lg"
          shape={isMusic ? 'square' : 'poster'}
        />
        <div style={{ minWidth: 0, display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div className="flex items-center gap-2" style={{ flexWrap: 'wrap' }}>
            <ConfidenceBadge value={file.confidence} />
            <StatusPill status={file.status} />
            <span className="badge badge-neutral">
              <span className="dot" style={{ background: TYPE_COLOR[file.mediaType] }} />
              {isMusic ? 'Music' : isAnime ? 'Anime' : isTV ? 'TV Series' : 'Movie'}
            </span>
            {isAnime && file.releaseGroup ? (
              <span className="rg-chip">[{file.releaseGroup}]</span>
            ) : null}
            {isMusic && m?.acoustidMatch ? (
              <span className="acoustid-match">
                <IcWaveform /> AcoustID match · {m.acoustidConfidence}%
              </span>
            ) : null}
          </div>

          {isAnime && m?.titleRomaji && m.titleRomaji !== m.title ? (
            <div style={{ fontSize: 13, color: 'var(--ink-3)', fontStyle: 'italic' }}>
              {m.titleRomaji}
            </div>
          ) : null}

          {m?.overview ? (
            <p style={{ margin: 0, fontSize: 13, lineHeight: 1.55, color: 'var(--ink-2)' }}>{m.overview}</p>
          ) : null}

          {isMusic && m ? (
            <div style={{ marginTop: 2 }}>
              <div className="opt-label" style={{ marginBottom: 6 }}>Track context</div>
              <div style={{
                background: 'rgba(0,0,0,0.22)',
                border: '1px solid var(--line)',
                borderRadius: 10,
                padding: '6px 8px',
                fontFamily: 'var(--font-mono)',
                fontSize: 11.5,
                color: 'var(--ink-3)',
                lineHeight: 1.7,
                maxHeight: 132,
                overflowY: 'auto',
              }}>
                {fakeTracklist(m).map((t, i) => (
                  <div key={i} className="flex items-center justify-between gap-3"
                    style={{ padding: '1px 4px', borderRadius: 4, background: t.n === m.track ? 'var(--accent-soft)' : 'transparent', color: t.n === m.track ? 'var(--accent)' : 'inherit' }}>
                    <span>
                      <span style={{ display: 'inline-block', minWidth: 26, color: t.n === m.track ? 'var(--accent)' : 'var(--ink-4)' }}>{String(t.n).padStart(2, '0')}</span>
                      <span style={{ color: t.n === m.track ? 'var(--accent)' : 'var(--ink-2)', fontWeight: t.n === m.track ? 600 : 400 }}>{t.t}</span>
                    </span>
                    <span style={{ color: 'var(--ink-4)' }}>{t.d}</span>
                  </div>
                ))}
              </div>
            </div>
          ) : null}

          <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '6px 14px', alignItems: 'center', fontSize: 12, marginTop: 4 }}>
            <Meta label="Filename" value={<span className="text-mono" style={{ color: 'var(--ink-2)' }}>{file.filename}</span>} />
            <Meta label="Source folder" value={<span className="text-mono" style={{ color: 'var(--ink-3)' }}>{file.folder}</span>} />
            {isMusic && m?.mbid ? <Meta label="MusicBrainz" value={<ProviderLink href={musicbrainzUrl(m.mbid)}>{m.mbid}</ProviderLink>} /> : null}
            {isAnime && m?.anidbId ? <Meta label="AniDB ID" value={<ProviderLink href={anidbUrl(m.anidbId)}>{m.anidbId}</ProviderLink>} /> : null}
            {!isMusic && !isAnime && m?.tmdbId ? <Meta label="TMDB ID" value={<ProviderLink href={tmdbUrl(m.tmdbId, isTV)}>{m.tmdbId}</ProviderLink>} /> : null}
            {isMusic && m?.genre ? <Meta label="Genre" value={m.genre} /> : null}
            {isMusic && m?.duration ? <Meta label="Duration" value={<span className="text-mono">{m.duration}</span>} /> : null}
          </div>
        </div>
      </div>

      {m ? (
        <div style={{ marginBottom: 18 }}>
          <div className="opt-label" style={{ marginBottom: 8 }}>Will be renamed to</div>
          <div className="preview-side new" style={{ padding: '10px 14px' }}>
            <div className="preview-path">{highlightPath(formatPath(file, 'Plex'))}</div>
          </div>
        </div>
      ) : null}

      {file.candidates.length > 0 ? (
        <div>
          <div className="opt-label" style={{ marginBottom: 8, display: 'flex', alignItems: 'center', gap: 8 }}>
            Other candidates
            <span style={{ color: 'var(--ink-4)', fontWeight: 500 }}>({file.candidates.length})</span>
          </div>
          <div className="candidates" style={{ padding: 4 }}>
            {file.candidates.map((c, i) => {
              const isChosen = i === 0 && (
                isMusic ? (m?.album === c.album && m?.track === c.track)
                        : (m?.title === c.title && m?.year === c.year)
              );
              const level = c.confidence >= 85 ? 'high' : c.confidence >= 50 ? 'mid' : 'low';
              return (
                <div key={i} className={`candidate ${isChosen ? 'chosen' : ''}`} style={{ ['--i' as never]: i }}>
                  <Poster data={isMusic ? c.art : c.poster} imgUrl={c.posterUrl} size="xs" shape={isMusic ? 'square' : 'poster'} />
                  <div style={{ minWidth: 0 }}>
                    <div className="candidate-title">
                      {isMusic
                        ? <>{c.artist} — <span style={{ color: 'var(--ink-2)' }}>{c.album}</span></>
                        : <>{c.title}{c.year ? ` (${c.year})` : ''}</>}
                      {isChosen ? <span className="badge badge-high" style={{ marginLeft: 8, padding: '1px 6px', fontSize: 10 }}>Current pick</span> : null}
                    </div>
                    <div className="candidate-meta">
                      {isMusic ? `Track ${c.track} · ${c.year}` : (
                        <>
                          {candidateEpisodeLabel(c, isAnime)}
                          {c.year ? <span style={{ color: 'var(--ink-4)' }}> · {c.year}</span> : null}
                        </>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="confidence-bar"><div style={{ width: c.confidence + '%', background: `var(--conf-${level})` }} /></div>
                    <span className="text-xs font-medium" style={{ color: `var(--conf-${level})`, minWidth: 32, textAlign: 'right' }}>{c.confidence}%</span>
                  </div>
                  {isChosen ? (
                    <span style={{ padding: '6px 10px', color: 'var(--ink-3)', fontSize: 11 }}>Selected</span>
                  ) : (
                    <button className="btn btn-sm" onClick={() => onPickCandidate(file.id, c)}>
                      <IcCheck /> Use
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      ) : (
        <div className="card" style={{ padding: 16, textAlign: 'center' }}>
          <div className="text-sm font-medium" style={{ marginBottom: 4 }}>No automatic matches.</div>
          <div className="text-xs text-muted" style={{ marginBottom: 12 }}>The filename couldn't be parsed confidently.</div>
          <button className="btn btn-sm btn-primary" onClick={() => onManualSearch(file)}><IcSearch /> Open manual search</button>
        </div>
      )}
    </Modal>
  );
}
