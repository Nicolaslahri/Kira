import { useEffect, useMemo, useState } from 'react';
import type { AppState, ModalState } from '../lib/types';
import { api, type ApiHistoryEntry, type ApiNotification, type ApiScan } from '../lib/api';
import {
  IcScan, IcCheck, IcX, IcAlertTri, IcHistory,
  IcFilm, IcTv, IcAnime, IcMusic, IcArrowRight,
} from '../lib/icons';
import { Skeleton } from '../components/ui';
import { cacheGet, cacheSet } from '../lib/cache';

interface Props {
  state: AppState;
  openModal: (kind: ModalState['kind'], payload?: unknown) => void;
  runScan: () => void;
  setActive: (p: 'dashboard' | 'review' | 'history' | 'settings') => void;
  /** Library root the next scan will walk — sourced from
   *  `paths.library_root` setting via App. Previously this was a
   *  hardcoded `Z:\\media` constant which broke for any non-this-user
   *  setup. */
  scanRoot: string;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let n = bytes / 1024;
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(n < 10 ? 1 : 0)} ${units[i]}`;
}

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 60_000) return `${Math.max(1, Math.floor(diff / 1000))}s ago`;
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return `${Math.floor(diff / 86_400_000)}d ago`;
}

export function DashboardPage({ state, openModal, runScan, setActive, scanRoot: SCAN_ROOT }: Props) {
  // openModal is currently unused on this page — the Shortcuts button was
  // removed from the header (the topbar already owns it). Kept on the
  // props signature so App.tsx can keep wiring the same shape uniformly.
  void openModal;
  // Stale-while-revalidate hydration from localStorage cache. On second+
  // refresh these populate synchronously so the page renders with the
  // previous values instantly; the background load below replaces them
  // with fresh data when it lands.
  const [history, setHistory] = useState<ApiHistoryEntry[]>(
    () => cacheGet<ApiHistoryEntry[]>('dashboard.history') ?? []
  );
  const [notifications, setNotifications] = useState<ApiNotification[]>(
    () => cacheGet<ApiNotification[]>('dashboard.notifications') ?? []
  );
  const [lastScan, setLastScan] = useState<ApiScan | null>(
    () => cacheGet<ApiScan | null>('dashboard.lastScan') ?? null
  );
  const [scans, setScans] = useState<ApiScan[]>(
    () => cacheGet<ApiScan[]>('dashboard.scans') ?? []
  );
  const [providerStatus, setProviderStatus] = useState<Record<string, 'ok' | 'fail' | 'unknown'>>({
    tmdb: 'unknown', tvdb: 'unknown',
  });
  // Local hydration flag for Dashboard-specific data. If we restored
  // from cache, the page is already "hydrated enough to render real
  // numbers" — so the activity-empty placeholder skips the loading
  // state. First-ever visit starts false and shows skeletons.
  const hadCache = cacheGet<ApiScan[]>('dashboard.scans') !== null;
  const [activityHydrated, setActivityHydrated] = useState(hadCache);

  // Hydrate everything once + refresh every 10s so the page feels alive.
  useEffect(() => {
    const load = async () => {
      try {
        const h = await api.listHistory({ period: 'all' });
        setHistory(h);
        cacheSet('dashboard.history', h);
      } catch { /* */ }
      try {
        const n = await api.listNotifications();
        setNotifications(n);
        cacheSet('dashboard.notifications', n);
      } catch { /* */ }
      try {
        const allScans = await api.listScans();
        setScans(allScans);
        setLastScan(allScans[0] ?? null);
        cacheSet('dashboard.scans', allScans);
        cacheSet('dashboard.lastScan', allScans[0] ?? null);
      } catch { /* */ }
      // Flip the hydrated flag after the first load round-trip — even
      // if some calls failed, we tried and the data we have is what
      // we'll show.
      setActivityHydrated(true);
    };
    void load();
    const t = setInterval(load, 10_000);
    return () => clearInterval(t);
  }, []);

  // F-01: pull provider status from `/providers` (the same source the
  // Settings page uses). The previous implementation hit `testProvider`
  // (a live network probe to TMDB/TVDB) which was slow, flaky, and
  // could show "Disconnected" for a configured provider during a
  // transient network blip — while Settings would simultaneously show
  // "Connected" because it reads `configured` from `/providers`. Both
  // surfaces must agree.
  useEffect(() => {
    let cancelled = false;
    const fetchProviders = async () => {
      try {
        const list = await api.getProviders();
        if (cancelled) return;
        const next: Record<string, 'ok' | 'fail' | 'unknown'> = {};
        for (const p of list) {
          // Map the {implemented, configured} pair to the legacy
          // ok / fail / unknown tri-state the panel below expects:
          //  - implemented + configured → ok ("Connected")
          //  - implemented + !configured → fail ("Not configured")
          //  - !implemented → unknown ("Coming soon" — hidden by panel)
          if (!p.implemented) next[p.key] = 'unknown';
          else next[p.key] = p.configured ? 'ok' : 'fail';
        }
        setProviderStatus(s => ({ ...s, ...next }));
      } catch {
        if (!cancelled) setProviderStatus(s => ({ ...s, tmdb: 'fail', tvdb: 'fail' }));
      }
    };
    void fetchProviders();
    // Reuse the existing 10s refresh cadence — provider state changes
    // rarely so this is more than sufficient.
    const t = setInterval(fetchProviders, 10_000);
    return () => { cancelled = true; clearInterval(t); };
  }, []);

  const stats = useMemo(() => {
    const total = state.files.length;
    const matched = state.files.filter(f => f.match && f.confidence >= 85).length;
    const pending = state.files.filter(f => f.status === 'pending').length;
    const approved = state.files.filter(f => f.status === 'approved').length;
    const lowConf = state.files.filter(f => f.confidence < 50).length;
    const totalSize = state.files.reduce((sum, f) => sum + ((f.parsed_data as { file_size?: number } | undefined)?.file_size ?? 0), 0);
    const byType = {
      movie: state.files.filter(f => f.mediaType === 'movie').length,
      tv:    state.files.filter(f => f.mediaType === 'tv').length,
      anime: state.files.filter(f => f.mediaType === 'anime').length,
      music: state.files.filter(f => f.mediaType === 'music').length,
    };
    return { total, matched, pending, approved, lowConf, byType, totalSize };
  }, [state.files]);

  const matchedPct = stats.total > 0 ? Math.round((stats.matched / stats.total) * 100) : 0;

  // Combined activity feed — scan events + rename history + recent notifications.
  const activity = useMemo(() => {
    const items: { id: string; kind: 'success' | 'error' | 'info'; when: string; text: React.ReactNode }[] = [];

    // Scan events — fresh first, both completed and in-progress.
    // F-10: filter out completed zero-result scans (e.g. accidental
    // scans of empty paths like /media on Windows). They pollute the
    // feed with identical "Scanned X — 0 files, 0 matched" rows that
    // bury real signal. In-progress and failed scans always show.
    for (const s of scans.slice(0, 8)) {
      const isFail = s.status.startsWith('failed');
      const inProgress = s.status === 'scanning' || s.status === 'matching';
      const isCompleted = !inProgress && !isFail;
      if (isCompleted && s.file_count === 0 && s.matched_count === 0) continue;
      items.push({
        id: `s${s.id}`,
        kind: isFail ? 'error' : inProgress ? 'info' : 'success',
        when: s.completed_at ?? s.created_at,
        text: inProgress ? (
          <>
            Scanning <span className="text-mono text-xs">{s.root_path}</span>
            {' — '}
            {s.status === 'scanning'
              ? <>discovered <b>{s.file_count}</b> file{s.file_count === 1 ? '' : 's'}</>
              : <>matching <b>{s.matched_count}</b>/<b>{s.file_count}</b> against TVDB</>
            }
          </>
        ) : isFail ? (
          <>
            Scan failed for <span className="text-mono text-xs">{s.root_path}</span>
            {' — '}{s.status.replace(/^failed:\s*/, '')}
          </>
        ) : (
          <>
            Scanned <span className="text-mono text-xs">{s.root_path}</span>
            {' — '}<b>{s.file_count}</b> file{s.file_count === 1 ? '' : 's'},
            {' '}<b>{s.matched_count}</b> matched
          </>
        ),
      });
    }

    // Rename history.
    for (const h of history.slice(0, 15)) {
      const filename = h.new_path.split(/[\\/]/).pop() ?? h.new_path;
      items.push({
        id: `h${h.id}`,
        kind: h.undone_at ? 'info' : 'success',
        when: h.created_at,
        text: (
          <>
            <b>{h.title || filename}</b>
            {' — '}
            <span className="text-mono text-xs">{h.operation}</span>
            {h.undone_at ? <span style={{ color: 'var(--ink-3)' }}> · undone</span> : null}
          </>
        ),
      });
    }

    // Notifications.
    for (const n of notifications.slice(0, 10)) {
      items.push({
        id: `n${n.id}`,
        kind: (n.kind === 'error' ? 'error' : n.kind === 'success' ? 'success' : 'info'),
        when: n.created_at,
        text: <><b>{n.title}</b>{n.body ? <> — {n.body}</> : null}</>,
      });
    }

    items.sort((a, b) => new Date(b.when).getTime() - new Date(a.when).getTime());
    return items.slice(0, 15);
  }, [scans, history, notifications]);

  // Layout always renders. Individual values that are loading get a
  // skeleton placeholder instead of `0` — preserves the page's visual
  // structure (no jump from empty layout to filled layout) and reads as
  // "loading" rather than "your library has 0 files".
  //
  // Combines with cache hydration above: on second+ refresh, `loading`
  // starts false because we restored from localStorage, so the user
  // sees their previous values instantly. First-ever visit shows
  // skeletons for ~200-500ms.
  const loading = !state.hydrated || !activityHydrated;

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Welcome back</h1>
          <p className="page-sub">
            {loading ? (
              // Inline shimmer while data loads — preserves line-height
              // so layout doesn't reflow when real copy lands. On second+
              // refresh, cache hydration above means loading=false from
              // the start, so the user sees previous values instantly.
              <Skeleton w={340} h={14} />
            ) : stats.total === 0 ? (
              'No library scanned yet. Click "Scan now" to get started.'
            ) : (
              <>
                {stats.pending} files pending review · {stats.matched} high-confidence matches
                {lastScan?.completed_at ? <> · last scan {relativeTime(lastScan.completed_at)}</> : null}
              </>
            )}
          </p>
        </div>
        {/* Header action buttons removed — Scan + Shortcuts already live
            in the global topbar, and the page-local scan banner is
            redundant with the App-level .global-scan-bar that stays
            sticky on every page during a scan. */}
      </div>

      <div className="grid-4">
        <div className="card stat">
          <div className="stat-label">Files scanned</div>
          <div className="stat-value">
            {loading ? <Skeleton w={90} h={32} /> : stats.total.toLocaleString()}
          </div>
          <div className="stat-breakdown">
            {loading ? (
              <Skeleton w={140} h={18} radius={9} />
            ) : (
              [
                { k: 'movie', label: 'Movies', n: stats.byType.movie, icon: <IcFilm style={{ width: 11, height: 11 }} />, color: 'var(--ink-2)' },
                { k: 'tv',    label: 'TV',     n: stats.byType.tv,    icon: <IcTv style={{ width: 11, height: 11 }} />,   color: 'var(--info)' },
                { k: 'anime', label: 'Anime',  n: stats.byType.anime, icon: <IcAnime style={{ width: 11, height: 11 }} />, color: '#c89bff' },
                { k: 'music', label: 'Music',  n: stats.byType.music, icon: <IcMusic style={{ width: 11, height: 11 }} />, color: '#ffb14a' },
              ].filter(t => t.n > 0).map(t => (
                // F-09: chips now show the label text alongside the icon
                // + count. Previously only the icon and number rendered;
                // users had to hover to identify what each chip meant.
                <span key={t.k} className="stat-bd-chip" title={t.label}>
                  <span style={{ color: t.color, display: 'inline-flex' }}>{t.icon}</span>
                  <span className="font-mono">{t.n}</span>
                  <span style={{ color: 'var(--ink-3)', fontSize: 11, marginLeft: 2 }}>{t.label}</span>
                </span>
              ))
            )}
          </div>
        </div>
        <div className="card stat">
          <div className="stat-label">High-confidence matches</div>
          <div className="stat-value" style={{ color: 'var(--conf-high)' }}>
            {loading ? <Skeleton w={70} h={32} /> : stats.matched}
          </div>
          <div className="stat-delta">
            <span style={{ color: 'var(--ink-3)' }}>
              {loading ? <Skeleton w={100} h={12} /> : `${matchedPct}% of library`}
            </span>
          </div>
        </div>
        <div className="card stat">
          <div className="stat-label">Pending review</div>
          <div className="stat-value" style={{ color: 'var(--conf-mid)' }}>
            {loading ? <Skeleton w={70} h={32} /> : stats.pending}
          </div>
          <div className="stat-delta">
            <span style={{ color: 'var(--ink-3)' }}>
              {loading ? (
                <Skeleton w={150} h={12} />
              ) : stats.lowConf > 0 ? `${stats.lowConf} need manual attention` : 'No low-confidence matches'}
            </span>
          </div>
        </div>
        <div className="card stat">
          <div className="stat-label">Library size</div>
          {/* F-08: when the file-size sum is unknown (older scans
              before file_size capture, or files on inaccessible
              mounts), the value used to render as a bare em-dash
              which reads as "data missing / broken". Fall back to
              the file count so the tile always shows something
              actionable. */}
          <div className="stat-value">
            {loading ? (
              <Skeleton w={120} h={32} />
            ) : stats.totalSize > 0 ? (
              formatBytes(stats.totalSize)
            ) : (
              `${stats.total.toLocaleString()} files`
            )}
          </div>
          <div className="stat-delta">
            <span style={{ color: 'var(--ink-3)' }}>
              {loading ? <Skeleton w={140} h={12} /> : `${stats.approved} approved · ready to rename`}
            </span>
          </div>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1.5fr 1fr', gap: 16 }}>
        {/* Recent activity */}
        <div className="card">
          <div className="card-head">
            <div>
              <div className="card-title">Recent activity</div>
              <div className="card-sub">Latest renames + notifications</div>
            </div>
            <button className="btn btn-ghost btn-sm" onClick={() => setActive('history')}>
              View history <IcArrowRight style={{ width: 11, height: 11 }} />
            </button>
          </div>
          <div className="activity">
            {!activityHydrated ? (
              // Skeleton rows that mirror an .activity-item layout so the
              // card has real visual presence during the load window.
              <>
                {[0, 1, 2].map(i => (
                  <div key={i} className="activity-item" style={{ pointerEvents: 'none' }}>
                    <Skeleton w={28} h={28} radius={14} />
                    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 6 }}>
                      <Skeleton w={'78%'} h={12} />
                      <Skeleton w={'52%'} h={10} />
                    </div>
                    <Skeleton w={48} h={10} />
                  </div>
                ))}
              </>
            ) : activity.length === 0 ? (
              <div style={{ padding: 28, textAlign: 'center', color: 'var(--ink-3)', fontSize: 13 }}>
                Nothing has happened yet. Scan a folder to populate this feed.
              </div>
            ) : (
              activity.map(a => (
                <div key={a.id} className="activity-item">
                  <div className={`activity-dot ${a.kind === 'info' ? '' : a.kind}`}>
                    {a.kind === 'success' ? <IcCheck /> : a.kind === 'error' ? <IcX /> : <IcHistory />}
                  </div>
                  <div className="activity-text">{a.text}</div>
                  <div className="activity-time">{relativeTime(a.when)}</div>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Side column — provider status + quick actions */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div className="card">
            <div className="card-head">
              <div className="card-title">Providers</div>
            </div>
            <div className="card-pad" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              {[
                { slug: 'tmdb', name: 'TMDB', purpose: 'Movies + TV' },
                { slug: 'tvdb', name: 'TVDB', purpose: 'TV + anime' },
              ].map(p => {
                // F-01: tri-state matches /providers — "fail" now reads
                // as "Not configured" rather than "Disconnected" since
                // we no longer probe the network. The user fixes it by
                // adding a key in Settings (link below).
                const st = providerStatus[p.slug] ?? 'unknown';
                const colour = st === 'ok' ? 'var(--conf-high)' : st === 'fail' ? 'var(--conf-low)' : 'var(--ink-4)';
                const label = st === 'ok' ? 'Connected' : st === 'fail' ? 'Not configured' : 'Checking…';
                return (
                  <div key={p.slug} className="flex items-center justify-between gap-3">
                    <div style={{ minWidth: 0 }}>
                      <div className="text-sm font-medium">{p.name}</div>
                      <div className="text-xs text-muted">{p.purpose}</div>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-xs" style={{ color: colour }}>{label}</span>
                      <span className="dot" style={{ background: colour }} />
                    </div>
                  </div>
                );
              })}
              <button className="btn btn-sm" onClick={() => setActive('settings')}>
                Configure providers <IcArrowRight style={{ width: 11, height: 11 }} />
              </button>
            </div>
          </div>

          <div className="card">
            <div className="card-head">
              <div className="card-title">Quick actions</div>
            </div>
            <div className="card-pad" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <button
                className="btn btn-primary"
                // Disabled attribute REMOVED temporarily — HTML buttons with
                // `disabled` don't fire click events at all, which would
                // suppress both the console.log diagnostic AND the toast.
                // If scanRunning is stuck true after a previous scan
                // crashed mid-flight, every click would silently no-op
                // with no feedback. With the attribute gone, runScan()'s
                // own scanRunning check still early-returns through the
                // toast — but at least the click reaches the handler and
                // the user sees the toast explaining what's wrong. The
                // visual `:disabled` opacity is replaced below with an
                // `aria-disabled` so screen readers still announce the
                // state correctly.
                aria-disabled={state.scanRunning}
                onClick={() => {
                  // Diagnostic for user-reported "Dashboard scan button doesn't
                  // work" issue. If this log doesn't appear in DevTools when
                  // the user clicks, the click never reached React's handler
                  // (CSS overlay / stale bundle / hard refresh needed). If it
                  // DOES appear, scanRunning was stuck true and the toast
                  // explains what to do (reload page).
                  // eslint-disable-next-line no-console
                  console.log('[Kira] Dashboard Scan button clicked. scanRunning =', state.scanRunning);
                  runScan();
                }}
                style={{
                  justifyContent: 'flex-start',
                  opacity: state.scanRunning ? 0.4 : 1,
                  cursor: state.scanRunning ? 'not-allowed' : 'pointer',
                }}
              >
                <IcScan /> Scan now ({SCAN_ROOT})
              </button>
              <button
                className="btn"
                onClick={() => setActive('review')}
                style={{ justifyContent: 'flex-start' }}
              >
                <IcCheck /> Review queue ({stats.pending} pending)
              </button>
              <button
                className="btn"
                onClick={() => setActive('history')}
                style={{ justifyContent: 'flex-start' }}
              >
                <IcHistory /> History ({history.length} entries)
              </button>
              {stats.lowConf > 0 ? (
                <div className="flex items-center gap-2" style={{ marginTop: 4, padding: '8px 10px', background: 'var(--conf-low-bg)', borderRadius: 8, border: '1px solid rgba(255,91,110,0.2)' }}>
                  <IcAlertTri style={{ width: 12, height: 12, color: 'var(--conf-low)' }} />
                  <span className="text-xs" style={{ color: 'var(--ink-2)' }}>
                    {stats.lowConf} files need manual review
                  </span>
                </div>
              ) : null}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
