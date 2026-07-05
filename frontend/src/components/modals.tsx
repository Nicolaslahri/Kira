import { useState, useMemo, useRef, useEffect } from 'react';
import type { MediaFile, SearchResult, ProviderKey, MediaType } from '../lib/types';
import { PROVIDERS, NAMING_PROFILES, poster } from '../lib/data';
import { api, ApiError, type ApiSearchResult, type ApiProvider } from '../lib/api';
import { IcSearch, IcCheck, IcArrowRight, IcShieldCheck, IcSpin, IcAlertTri } from '../lib/icons';
import { Modal, Poster, Segmented } from './ui';

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
  'fanart.tv': 'fanarttv',
  // Subtitle providers — not metadata-search providers, so these slugs only
  // satisfy the exhaustive Record (never passed to api.search / pickDefault).
  OpenSubtitles: 'opensubtitles',
  SubDL: 'subdl',
  SubSource: 'subsource',
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

export function ManualSearchModal({ file, onClose, onSelect, onIdentifyByContent }: {
  file: MediaFile;
  onClose: () => void;
  onSelect: (r: SearchResult & { _provider?: ProviderKey; _providerId?: string }) => void;
  /** Content-hash identify (OpenSubtitles). Resolves on success (modal then
   *  closes), throws on failure (modal stays open — error already toasted). */
  onIdentifyByContent?: (f: MediaFile) => Promise<void>;
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
  const [identifying, setIdentifying] = useState(false);
  const selectingRef = useRef(false);  // one-shot guard so button + double-click can't fire the match twice
  const inputRef = useRef<HTMLInputElement>(null);

  // Pull provider catalogue once and pick the default tab from it — UNLESS the
  // user already clicked a tab while the catalogue was loading (their choice
  // must win; the async default used to clobber it).
  const userPickedProviderRef = useRef(false);
  useEffect(() => {
    let cancelled = false;
    api.getProviders()
      .then(list => {
        if (cancelled) return;
        setProviders(list);
        if (!userPickedProviderRef.current) setProvider(pickDefault(file, list));
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
          // Fetch pictures SEQUENTIALLY (await each) rather than firing all N
          // at once. The backend already serializes AniDB at 1 req/4s, so this
          // is no slower — but it lets the "client rejected" guard actually
          // STOP the burst: the old fire-and-forget loop dispatched every call
          // before any response arrived, so `apiDead` never short-circuited and
          // a banned client hammered AniDB with the whole result set.
          void (async () => {
            for (const r of res.results) {
              if (cancelled) break;
              if (r.poster_url || !r.provider_id) continue;
              try {
                const { picture_url, error: apiErr, error_kind } = await api.anidbPicture(r.provider_id);
                if (cancelled) return;
                if (apiErr) {
                  setAnidbApiError(apiErr);
                  setAnidbErrorKind(error_kind);
                  break;   // client rejected / banned → stop firing
                }
                if (!picture_url) continue;
                setResults(curr => curr.map(x =>
                  x.provider_id === r.provider_id ? { ...x, poster_url: picture_url } : x
                ));
              } catch {
                /* swallow — keep gradient initials */
              }
            }
          })();
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
        if (!cancelled) setResults([]);
      } finally {
        // Guard on `cancelled`: a superseded (older) search resolving after a
        // provider/query change must NOT clear the spinner while the newer
        // request is still in flight.
        if (!cancelled) setLoading(false);
      }
    }, 300);
    return () => { cancelled = true; clearTimeout(handle); };
  }, [q, provider, typeFilter, providerStatus]);

  const allTabs: ProviderKey[] = ['TMDB', 'TVDB', 'AniDB', 'MusicBrainz'];
  const providerMeta = PROVIDERS[provider];
  const isMusic = provider === 'MusicBrainz';

  const handleSelect = (r: ApiSearchResult) => {
    if (selectingRef.current) return;   // already submitting — ignore the repeat click/dblclick
    selectingRef.current = true;
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
          <div className="flex items-center gap-3">
            {onIdentifyByContent ? (
              <button
                className="btn btn-ghost btn-sm"
                disabled={identifying}
                title="Hash the file's content and identify it via OpenSubtitles — works even when the filename is garbage"
                onClick={async () => {
                  setIdentifying(true);
                  try { await onIdentifyByContent(file); onClose(); }
                  catch { /* error already toasted; keep the modal open */ }
                  finally { setIdentifying(false); }
                }}
              >
                {identifying ? <IcSpin /> : <IcShieldCheck />} Identify by content
              </button>
            ) : null}
            <div className="text-muted text-sm">
              Couldn't find it? <a href={providerRootUrl(provider)} target="_blank" rel="noreferrer" style={{ color: 'var(--accent)' }}>Open {provider}.org</a>
            </div>
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
              type="button"
              className={`provider-tab ${provider === p ? 'on' : ''} ${unavailable ? 'disabled' : ''}`}
              onClick={() => { if (!unavailable) { userPickedProviderRef.current = true; setProvider(p); } }}
              disabled={unavailable}
              aria-disabled={unavailable || undefined}
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
        <div className="onboarding-state" style={{ marginBottom: 12, background: 'var(--glass)' }}>
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
        <div className="onboarding-state" style={{ marginTop: 8, background: 'var(--conf-mid-4)', borderColor: 'var(--conf-mid-24)', alignItems: 'flex-start', gap: 12 }}>
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
              <button key={`${r.provider_id}-${i}`}
                type="button"
                className={`search-result ${sel === r ? 'selected' : ''}`}
                style={{ ['--i' as never]: i }}
                aria-pressed={sel === r}
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
              </button>
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
  const [applying, setApplying] = useState(false);  // guard the disk-rename Apply against double-submit

  // A file is renameable only when it has a REAL provider match — the
  // adapter synthesises a placeholder `match` object from parsed data
  // for display, so we filter on provider+providerId not just `match`.
  // MEMOIZED on `files`: an unmemoized `.filter()` produced a NEW array
  // identity every render, which invalidated the `preview` memo, which
  // re-fired the dry-run effect below, which setTargets → re-render → an
  // infinite loop of `POST /rename {dry_run:true}` while the modal was open.
  const eligible = useMemo(
    () => files.filter(f => f.match?.provider && f.match?.providerId),
    [files],
  );
  const skipped = files.length - eligible.length;
  // Show the FULL plan (scrollable), not the first 4 — approving a 200-file
  // batch while seeing 2% of it defeated "review every change". Capped at 500
  // rows to keep the dry-run request + DOM bounded; the cap is surfaced below.
  const preview = useMemo(() => eligible.slice(0, 500), [eligible]);

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
    // Guard: only ship valid integer file ids. A synthesised / non-numeric id
    // would become NaN → JSON null → the backend's `file_ids: list[int]`
    // rejects the whole request with a 422 (and here it's silently swallowed,
    // so the preview just shows nothing). Matches the filtering every other
    // rename path already does.
    api.rename({ file_ids: preview.map(f => Number(f.id)).filter(Number.isInteger), profile, op, dry_run: true })
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
            <button className="btn btn-primary" disabled={applying} onClick={async () => {
              if (applying) return;   // Apply triggers a real disk rename — never fire it twice
              setApplying(true);
              try { await onApply({ profile, op }); } finally { setApplying(false); }
            }}>
              <IcCheck /> {applying ? 'Applying…' : <>Apply · Rename {eligible.length} {eligible.length === 1 ? 'file' : 'files'}</>}
            </button>
          </div>
        </>
      }
    >
      {skipped > 0 ? (
        <div style={{
          marginBottom: 14, padding: '10px 12px',
          borderRadius: 8,
          background: 'var(--media-music-12)',
          border: '1px solid var(--media-music-32)',
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

      {/* Full plan, scrollable — every from→to pair the apply will execute.
          content-visibility keeps huge batches cheap (off-screen rows skip
          layout/paint). */}
      <div style={{ maxHeight: '42vh', overflowY: 'auto', paddingRight: 4 }}>
        {preview.map(f => (
          <div key={f.id} className="preview-pair" style={{ marginBottom: 12, contentVisibility: 'auto', containIntrinsicSize: 'auto 72px' }}>
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
                    // Honest gap (§10 m): the old fallback rendered the LEGACY
                    // client template, which can disagree with what the backend
                    // renderer actually writes — a wrong path shown as truth.
                    : <span style={{ color: 'var(--ink-3)' }}>Preview unavailable — the rename itself uses the saved profile.</span>}
              </div>
            </div>
          </div>
        ))}
      </div>

      {eligible.length > preview.length ? (
        <div style={{ textAlign: 'center', color: 'var(--ink-3)', fontSize: 12, padding: '4px 0 14px' }}>
          + {eligible.length - preview.length} more file{eligible.length - preview.length === 1 ? '' : 's'} beyond the 500-row preview (they WILL be renamed on Apply)
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
    </Modal>
  );
}

export function KeyboardShortcutsModal({ onClose }: { onClose: () => void }) {
  const groups = [
    { label: 'Navigation', items: [
      { keys: ['j'], desc: 'Next card' },
      { keys: ['k'], desc: 'Previous card' },
      { keys: ['/'], desc: 'Focus search' },
      { keys: ['g', 'd'], desc: 'Go to Dashboard' },
      { keys: ['g', 'r'], desc: 'Go to Review' },
      { keys: ['g', 'h'], desc: 'Go to History' },
      { keys: ['g', 's'], desc: 'Go to Settings' },
    ]},
    { label: 'Actions', items: [
      { keys: ['a'], desc: 'Approve + rename current' },
      { keys: ['r'], desc: 'Reject current' },
      { keys: ['m'], desc: 'Manual search' },
      { keys: ['x'], desc: 'Toggle selection' },
      { keys: ['Enter'], desc: 'Open cover / candidates' },
      { keys: ['⌘', '⇧', 'A'], desc: 'Select all high-confidence' },
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

/** Provider search/home page — the "Couldn't find it?" escape hatch link. */
function providerRootUrl(provider: ProviderKey): string {
  switch (provider) {
    case 'TVDB': return 'https://thetvdb.com';
    case 'AniDB': return 'https://anidb.net';
    case 'MusicBrainz': return 'https://musicbrainz.org';
    default: return 'https://www.themoviedb.org';
  }
}

// (FileDetailsModal removed — it was UNREACHABLE (nothing opened the
//  'fileDetails' modal kind) and its client-side legacy-Plex rename
//  preview ignored the saved naming profile. Its useful part — the
//  candidates list — lives in CoverPopup/CandidateList now.)
