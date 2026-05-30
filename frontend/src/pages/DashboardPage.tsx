import { useEffect, useMemo, useState, type ReactNode } from 'react';
import type { AppState, ModalState } from '../lib/types';
import { api, type ApiHistoryEntry, type ApiNotification, type ApiScan } from '../lib/api';
import {
  IcScan, IcCheck, IcX, IcHistory, IcArrowRight,
  IcFilm, IcTv, IcAnime, IcMusic, IcFolder, IcReview,
  IcShieldCheck, IcLink, IcAlertTri,
} from '../lib/icons';
import { cn } from '../lib/utils';
import { Button } from '../components/base/buttons/button';
import { FeaturedIcon } from '../components/base/featured-icons/featured-icon';
import { ProgressBar } from '../components/base/progress-indicators/progress-bar';
import { BadgeWithDot } from '../components/base/badges/badges';
import { Skeleton, EmptyState } from '../components/ui';

interface Props {
  state: AppState;
  openModal: (kind: NonNullable<ModalState>['kind'], payload?: unknown) => void;
  runScan: () => void;
  runReparse: () => void;
  setActive: (p: 'dashboard' | 'review' | 'history' | 'settings') => void;
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

const TYPE_META = [
  { k: 'movie' as const, label: 'Movies', icon: IcFilm,  color: '#bdc1d0' },
  { k: 'tv' as const,    label: 'TV',     icon: IcTv,    color: '#49b8fe' },
  { k: 'anime' as const, label: 'Anime',  icon: IcAnime, color: '#c89bff' },
  { k: 'music' as const, label: 'Music',  icon: IcMusic, color: '#ffb14a' },
];

const BUCKET_META = [
  { k: 'strong' as const, label: 'Strong',       color: '#28d9a0' },
  { k: 'likely' as const, label: 'Likely',       color: '#49b8fe' },
  { k: 'review' as const, label: 'Needs review', color: '#ffc94a' },
  { k: 'low' as const,    label: 'Low / none',   color: '#ff5b6e' },
];

// ── Shared card shell ───────────────────────────────────────────────
function Card({ title, icon, action, glow, divider, className, bodyClassName, children }: {
  title?: ReactNode;
  icon?: ReactNode;
  action?: ReactNode;
  glow?: boolean;
  divider?: boolean;
  className?: string;
  bodyClassName?: string;
  children: ReactNode;
}) {
  const hasHeader = title != null || action != null;
  return (
    <section className={cn(
      'group relative flex flex-col overflow-hidden rounded-2xl border border-line bg-[rgba(255,255,255,0.025)] transition-colors duration-300 hover:border-line-strong',
      className,
    )}>
      {glow ? (
        <div className="pointer-events-none absolute -right-16 -top-16 size-48 rounded-full bg-[radial-gradient(closest-side,var(--accent-soft),transparent)] opacity-0 blur-2xl transition-opacity duration-500 group-hover:opacity-100" />
      ) : null}
      {hasHeader ? (
        <div className={cn(
          'relative flex items-center justify-between gap-3 px-5',
          divider ? 'border-b border-line py-4' : 'pt-5',
        )}>
          <div className="flex min-w-0 items-center gap-2.5">
            {icon}
            {title ? <h2 className="truncate text-sm font-semibold text-ink">{title}</h2> : null}
          </div>
          {action}
        </div>
      ) : null}
      <div className={cn('relative', bodyClassName ?? (hasHeader && !divider ? 'px-5 pb-5 pt-4' : 'p-5'))}>
        {children}
      </div>
    </section>
  );
}

// Small "see more" link rendered with the Untitled UI Button (link style).
function CardLink({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <Button
      color="link-gray"
      size="sm"
      onClick={onClick}
      className="text-xs"
      iconTrailing={<IcArrowRight className="size-3" />}
    >
      {label}
    </Button>
  );
}

// ── KPI metric card ─────────────────────────────────────────────────
function Metric({ icon, color, label, value, sub, onClick }: {
  icon: ReactNode;
  color: 'brand' | 'success' | 'warning' | 'error' | 'gray';
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  onClick?: () => void;
}) {
  return (
    <div
      onClick={onClick}
      className={cn(
        'group relative flex flex-col overflow-hidden rounded-2xl border border-line bg-[rgba(255,255,255,0.025)] p-5 transition-colors duration-300 hover:border-line-strong',
        onClick && 'cursor-pointer hover:bg-[rgba(255,255,255,0.045)]',
      )}
    >
      <div className="pointer-events-none absolute -right-12 -top-12 size-36 rounded-full bg-[radial-gradient(closest-side,var(--accent-soft),transparent)] opacity-0 blur-2xl transition-opacity duration-500 group-hover:opacity-100" />
      <div className="relative flex items-center justify-between">
        <FeaturedIcon size="md" color={color} icon={icon} />
        {onClick ? (
          <IcArrowRight className="size-4 -translate-x-1 text-ink-faint opacity-0 transition-all group-hover:translate-x-0 group-hover:opacity-100" />
        ) : null}
      </div>
      <div className="relative mt-4 text-[11px] font-medium uppercase tracking-[0.09em] text-ink-soft">{label}</div>
      <div className="relative mt-1 text-[30px] font-bold leading-none tracking-tight tabular-nums text-ink">{value}</div>
      {sub ? <div className="relative mt-2 text-xs text-ink-soft">{sub}</div> : null}
    </div>
  );
}

export function DashboardPage({ state, openModal, runScan, runReparse, setActive, scanRoot: SCAN_ROOT }: Props) {
  void openModal; void runScan; void SCAN_ROOT;
  const [history, setHistory] = useState<ApiHistoryEntry[]>([]);
  const [notifications, setNotifications] = useState<ApiNotification[]>([]);
  const [lastScan, setLastScan] = useState<ApiScan | null>(null);
  const [scans, setScans] = useState<ApiScan[]>([]);
  const [providerStatus, setProviderStatus] = useState<Record<string, 'ok' | 'fail' | 'unknown'>>({
    tmdb: 'unknown', tvdb: 'unknown',
  });
  const [activityHydrated, setActivityHydrated] = useState(false);

  useEffect(() => {
    const load = async () => {
      try { const h = await api.listHistory({ period: 'all' }); setHistory(h); } catch { /* */ }
      try { const n = await api.listNotifications(); setNotifications(n); } catch { /* */ }
      try {
        const allScans = await api.listScans();
        setScans(allScans);
        setLastScan(allScans[0] ?? null);
      } catch { /* */ }
      setActivityHydrated(true);
    };
    void load();
    const t = setInterval(load, 10_000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    let cancelled = false;
    const fetchProviders = async () => {
      try {
        const list = await api.getProviders();
        if (cancelled) return;
        const next: Record<string, 'ok' | 'fail' | 'unknown'> = {};
        for (const p of list) {
          if (!p.implemented) next[p.key] = 'unknown';
          else next[p.key] = p.configured ? 'ok' : 'fail';
        }
        setProviderStatus(s => ({ ...s, ...next }));
      } catch {
        if (!cancelled) setProviderStatus(s => ({ ...s, tmdb: 'fail', tvdb: 'fail' }));
      }
    };
    void fetchProviders();
    const t = setInterval(fetchProviders, 10_000);
    return () => { cancelled = true; clearInterval(t); };
  }, []);

  const stats = useMemo(() => {
    const files = state.files;
    const total = files.length;
    const matched = files.filter(f => f.match && f.confidence >= 85).length;
    const pending = files.filter(f => f.status === 'pending').length;
    const approved = files.filter(f => f.status === 'approved').length;
    const lowConf = files.filter(f => f.confidence < 50).length;
    const totalSize = files.reduce((sum, f) => sum + (f.sizeBytes ?? 0), 0);
    const byType = {
      movie: files.filter(f => f.mediaType === 'movie').length,
      tv:    files.filter(f => f.mediaType === 'tv').length,
      anime: files.filter(f => f.mediaType === 'anime').length,
      music: files.filter(f => f.mediaType === 'music').length,
    };
    const sizeByType = {
      movie: files.filter(f => f.mediaType === 'movie').reduce((s, f) => s + (f.sizeBytes ?? 0), 0),
      tv:    files.filter(f => f.mediaType === 'tv').reduce((s, f) => s + (f.sizeBytes ?? 0), 0),
      anime: files.filter(f => f.mediaType === 'anime').reduce((s, f) => s + (f.sizeBytes ?? 0), 0),
      music: files.filter(f => f.mediaType === 'music').reduce((s, f) => s + (f.sizeBytes ?? 0), 0),
    };
    const buckets = {
      strong: files.filter(f => f.confidence >= 90).length,
      likely: files.filter(f => f.confidence >= 75 && f.confidence < 90).length,
      review: files.filter(f => f.confidence >= 50 && f.confidence < 75).length,
      low:    files.filter(f => f.confidence < 50).length,
    };
    return { total, matched, pending, approved, lowConf, totalSize, byType, sizeByType, buckets };
  }, [state.files]);

  const matchedPct = stats.total > 0 ? Math.round((stats.matched / stats.total) * 100) : 0;
  const maxType = Math.max(1, ...TYPE_META.map(t => stats.byType[t.k]));
  const typesPresent = TYPE_META.filter(t => stats.byType[t.k] > 0).length;

  const activity = useMemo(() => {
    const items: { id: string; kind: 'success' | 'error' | 'info'; when: string; text: React.ReactNode }[] = [];
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
          <>Scanning <span className="font-mono text-xs">{s.root_path}</span>{' — '}
            {s.status === 'scanning'
              ? <>discovered <b>{s.file_count}</b> files</>
              : <>matching <b>{s.matched_count}</b>/<b>{s.file_count}</b></>}</>
        ) : isFail ? (
          <>Scan failed for <span className="font-mono text-xs">{s.root_path}</span>{' — '}{s.status.replace(/^failed:\s*/, '')}</>
        ) : (
          <>Scanned <span className="font-mono text-xs">{s.root_path}</span>{' — '}<b>{s.file_count}</b> files, <b>{s.matched_count}</b> matched</>
        ),
      });
    }
    for (const h of history.slice(0, 15)) {
      const filename = h.new_path.split(/[\\/]/).pop() ?? h.new_path;
      items.push({
        id: `h${h.id}`,
        kind: h.undone_at ? 'info' : 'success',
        when: h.created_at,
        text: <><b>{h.title || filename}</b>{' — '}<span className="font-mono text-xs">{h.operation}</span>{h.undone_at ? <span className="text-ink-soft"> · undone</span> : null}</>,
      });
    }
    for (const n of notifications.slice(0, 10)) {
      items.push({
        id: `n${n.id}`,
        kind: (n.kind === 'error' ? 'error' : n.kind === 'success' ? 'success' : 'info'),
        when: n.created_at,
        text: <><b>{n.title}</b>{n.body ? <> — {n.body}</> : null}</>,
      });
    }
    items.sort((a, b) => new Date(b.when).getTime() - new Date(a.when).getTime());
    return items.slice(0, 16);
  }, [scans, history, notifications]);

  const providers = [
    { slug: 'tmdb', name: 'TMDB', note: 'Movies & TV metadata' },
    { slug: 'tvdb', name: 'TVDB', note: 'TV & anime episode data' },
  ];

  return (
    <div className="page relative">
      {/* ── Header ─────────────────────────────────────────────── */}
      <div className="relative z-10 mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <BadgeWithDot color={state.scanRunning ? 'warning' : 'brand'} pulse className="mb-3">
            {state.scanRunning ? 'Scanning…' : 'Live'}
          </BadgeWithDot>
          <h1 className="bg-gradient-to-br from-white via-white to-white/45 bg-clip-text text-[40px] font-bold leading-[1.04] tracking-[-0.03em] text-transparent">
            Welcome back
          </h1>
          <p className="mt-2.5 text-[13.5px] text-ink-soft">
            {stats.total === 0
              ? 'No library scanned yet — hit Scan now to get started.'
              : <><b className="text-ink-muted">{stats.pending}</b> pending review · <b className="text-ink-muted">{stats.matched}</b> matched{lastScan?.completed_at ? <> · last scan {relativeTime(lastScan.completed_at)}</> : null}</>}
          </p>
        </div>

        <Button
          size="md"
          color="secondary"
          iconLeading={IcScan}
          isLoading={state.scanRunning}
          isDisabled={state.scanRunning}
          showTextWhileLoading
          onClick={runReparse}
          title="Re-run the parser + re-match the existing library in place — applies parsing/grouping improvements without losing manual picks."
        >
          Re-parse &amp; re-match
        </Button>
      </div>

      {/* ── KPI strip ──────────────────────────────────────────── */}
      <div className="relative z-10 grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Metric
          icon={<IcFolder />}
          color="brand"
          label="Library"
          value={stats.total.toLocaleString()}
          sub={typesPresent > 0 ? `${typesPresent} media type${typesPresent === 1 ? '' : 's'}` : 'No files yet'}
        />
        <Metric
          icon={<IcCheck />}
          color="success"
          label="Matched"
          value={stats.matched.toLocaleString()}
          sub={`${matchedPct}% of library`}
          onClick={() => setActive('review')}
        />
        <Metric
          icon={<IcReview />}
          color="warning"
          label="Pending review"
          value={stats.pending.toLocaleString()}
          sub={stats.lowConf > 0
            ? <span className="inline-flex items-center gap-1 text-conf-low"><IcAlertTri className="size-3" />{stats.lowConf} low-confidence</span>
            : 'All reviewed'}
          onClick={() => setActive('review')}
        />
        <Metric
          icon={<IcShieldCheck />}
          color="brand"
          label="Approved"
          value={stats.approved.toLocaleString()}
          sub="Confirmed & ready"
        />
      </div>

      {/* ── Main region: left stack + full-height activity ─────── */}
      <div className="relative z-10 mt-4 grid grid-cols-1 gap-4 xl:grid-cols-3">
        <div className="flex flex-col gap-4 xl:col-span-2">
          {/* Match quality — donut + confidence buckets */}
          <Card
            title="Match quality"
            icon={<FeaturedIcon size="sm" color="success" icon={<IcShieldCheck />} />}
            action={<CardLink label="Review" onClick={() => setActive('review')} />}
            glow
          >
            <div className="flex flex-col items-center gap-7 sm:flex-row sm:gap-8">
              <div className="shrink-0 text-center sm:px-4">
                <div className="text-[44px] font-bold leading-none tracking-tight text-ink tabular-nums">{matchedPct}<span className="text-2xl text-ink-soft">%</span></div>
                <div className="mt-1.5 text-[11px] uppercase tracking-[0.08em] text-ink-soft">matched</div>
              </div>

              <div className="flex w-full flex-col gap-3.5">
                {BUCKET_META.map(b => {
                  const n = stats.buckets[b.k];
                  return (
                    <div key={b.k} className="flex items-center gap-3">
                      <span className="inline-flex w-28 shrink-0 items-center gap-2 text-[13px] text-ink-muted">
                        <span className="size-2 shrink-0 rounded-full" style={{ background: b.color }} />
                        {b.label}
                      </span>
                      <ProgressBar value={n} max={Math.max(1, stats.total)} color={b.color} className="flex-1" />
                      <span className="w-8 shrink-0 text-right font-mono text-xs tabular-nums text-ink-muted">{n}</span>
                    </div>
                  );
                })}
              </div>
            </div>
          </Card>

          {/* Library composition — per-type bars */}
          <Card
            title="Library composition"
            icon={<FeaturedIcon size="sm" color="gray" icon={<IcFilm />} />}
          >
            <div className="flex flex-col gap-3">
              {TYPE_META.map(t => {
                const n = stats.byType[t.k];
                return (
                  <div key={t.k} className="flex items-center gap-3">
                    <span className="inline-flex w-20 shrink-0 items-center gap-1.5 text-[13px] text-ink-muted [&_svg]:size-3.5">
                      <span style={{ color: t.color }} className="inline-flex"><t.icon /></span>
                      {t.label}
                    </span>
                    <ProgressBar value={n} max={maxType} color={t.color} minVisible={4} className="flex-1" />
                    <span className="w-9 shrink-0 text-right font-mono text-xs tabular-nums text-ink-muted">{n}</span>
                  </div>
                );
              })}
            </div>
          </Card>

          {/* Providers + Storage */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Card
              title="Providers"
              icon={<FeaturedIcon size="sm" color="brand" icon={<IcLink />} />}
              action={<CardLink label="Configure" onClick={() => setActive('settings')} />}
            >
              <div className="flex flex-col gap-3">
                {providers.map(p => {
                  const st = providerStatus[p.slug] ?? 'unknown';
                  const color = st === 'ok' ? 'success' : st === 'fail' ? 'error' : 'gray';
                  const label = st === 'ok' ? 'Connected' : st === 'fail' ? 'Not set up' : 'Checking…';
                  return (
                    <div key={p.slug} className="flex items-center justify-between gap-2">
                      <div className="min-w-0">
                        <div className="text-[13px] font-medium text-ink">{p.name}</div>
                        <div className="truncate text-[11px] text-ink-faint">{p.note}</div>
                      </div>
                      <BadgeWithDot color={color}>{label}</BadgeWithDot>
                    </div>
                  );
                })}
              </div>
            </Card>

            <Card
              title="Storage"
              icon={<FeaturedIcon size="sm" color="gray" icon={<IcFolder />} />}
            >
              {stats.totalSize > 0 ? (
                <div className="flex flex-col gap-3.5">
                  <div className="flex items-end gap-2">
                    <span className="text-2xl font-bold leading-none tracking-tight text-ink">{formatBytes(stats.totalSize)}</span>
                    <span className="mb-0.5 text-xs text-ink-soft">total</span>
                  </div>
                  <div className="flex h-2 w-full overflow-hidden rounded-full bg-white/[0.06]">
                    {TYPE_META.map(t => {
                      const frac = stats.sizeByType[t.k] / stats.totalSize;
                      if (frac <= 0) return null;
                      return <div key={t.k} style={{ width: `${frac * 100}%`, background: t.color, opacity: 0.85 }} />;
                    })}
                  </div>
                  <div className="grid grid-cols-2 gap-x-4 gap-y-1.5">
                    {TYPE_META.filter(t => stats.sizeByType[t.k] > 0).map(t => (
                      <div key={t.k} className="flex items-center justify-between gap-2 text-[12px]">
                        <span className="inline-flex items-center gap-1.5 text-ink-muted">
                          <span className="size-2 rounded-full" style={{ background: t.color }} />
                          {t.label}
                        </span>
                        <span className="font-mono tabular-nums text-ink-soft">{formatBytes(stats.sizeByType[t.k])}</span>
                      </div>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="flex h-full flex-col items-center justify-center gap-1 py-4 text-center">
                  <div className="text-2xl font-bold tracking-tight text-ink tabular-nums">{stats.total}</div>
                  <div className="text-xs text-ink-soft">files · size unavailable</div>
                </div>
              )}
            </Card>
          </div>
        </div>

        {/* Recent activity — full-height right rail */}
        <Card
          title="Recent activity"
          icon={<FeaturedIcon size="sm" color="gray" icon={<IcHistory />} />}
          action={<CardLink label="History" onClick={() => setActive('history')} />}
          divider
          className="min-h-[460px] xl:col-span-1 xl:h-full"
          bodyClassName="flex-1 min-h-0 overflow-y-auto p-3 [scrollbar-width:thin]"
        >
          {!activityHydrated ? (
            <div className="flex flex-col gap-1">
              {Array.from({ length: 7 }).map((_, i) => (
                <div key={i} className="flex items-center gap-3 px-2.5 py-2">
                  <Skeleton w={36} h={36} radius={8} />
                  <div className="min-w-0 flex-1"><Skeleton h={13} w={`${74 - i * 6}%`} /></div>
                  <Skeleton w={26} h={11} radius={4} />
                </div>
              ))}
            </div>
          ) : activity.length === 0 ? (
            <div className="grid h-full place-items-center">
              <EmptyState
                icon={<IcHistory />}
                title="No activity yet"
                sub="Scan a folder to start populating this feed."
              />
            </div>
          ) : (
            <div className="flex flex-col gap-0.5">
              {activity.map(a => (
                <div key={a.id} className="flex items-center gap-3 rounded-xl px-2.5 py-2 transition-colors hover:bg-glass-2">
                  <FeaturedIcon
                    size="md"
                    color={a.kind === 'success' ? 'success' : a.kind === 'error' ? 'error' : 'gray'}
                    icon={a.kind === 'success' ? <IcCheck /> : a.kind === 'error' ? <IcX /> : <IcHistory />}
                  />
                  <div className="min-w-0 flex-1 truncate text-[13px] text-ink-muted">{a.text}</div>
                  <div className="shrink-0 text-[11px] text-ink-faint">{relativeTime(a.when)}</div>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
