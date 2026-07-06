import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { animate } from 'motion/react';
import { PieChart, Pie, ResponsiveContainer, Tooltip } from 'recharts';
import type { AppState, ModalState } from '../lib/types';
import { api, type ApiHistoryEntry, type ApiNotification, type ApiScan } from '../lib/api';
import {
  IcScan, IcCheck, IcX, IcHistory, IcArrowRight,
  IcFilm, IcTv, IcAnime, IcMusic, IcFolder, IcReview,
  IcShieldCheck, IcLink, IcAlertTri, IcSparkles, IcRefresh,
  IcDownload, IcCaption,
} from '../lib/icons';
import { cn } from '../lib/utils';
import { getConfBands } from '../lib/confBands';
import { buildLibraryItems } from '../lib/adapters';
import { Button } from '../components/base/buttons/button';
import { FeaturedIcon } from '../components/base/featured-icons/featured-icon';
import { MetricCard } from '../components/base/metrics/metric-card';
import { ActivityFeed, type ActivityFeedItem } from '../components/base/activity-feed/activity-feed';
import { ChartTooltipContent } from '../components/base/charts/charts-base';
import { ProgressBar } from '../components/base/progress-indicators/progress-bar';
import { BadgeWithDot } from '../components/base/badges/badges';
import { Skeleton, EmptyState } from '../components/ui';

interface Props {
  state: AppState;
  openModal: (kind: NonNullable<ModalState>['kind'], payload?: unknown) => void;
  runScan: () => void;
  runReparse: (scope?: { media_type?: string; file_ids?: number[] }) => void;
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
  { k: 'movie' as const, label: 'Movies', icon: IcFilm,  color: '#4ec5b3' },
  { k: 'tv' as const,    label: 'TV',     icon: IcTv,    color: '#b3e5fc' },
  { k: 'anime' as const, label: 'Anime',  icon: IcAnime, color: 'var(--media-anime)' },
  { k: 'music' as const, label: 'Music',  icon: IcMusic, color: 'var(--media-music)' },
];

const BUCKET_META = [
  { k: 'strong' as const, label: 'Strong',       color: 'var(--conf-high)' },
  { k: 'likely' as const, label: 'Likely',       color: 'var(--info)' },
  { k: 'review' as const, label: 'Needs review', color: 'var(--conf-mid)' },
  { k: 'low' as const,    label: 'Low / none',   color: 'var(--conf-low)' },
];

// ── Shared card shell (Untitled UI card: ring + subtle shadow) ──────
function Card({ title, icon, action, divider, className, bodyClassName, children }: {
  title?: ReactNode;
  icon?: ReactNode;
  action?: ReactNode;
  divider?: boolean;
  className?: string;
  bodyClassName?: string;
  children: ReactNode;
}) {
  const hasHeader = title != null || action != null;
  return (
    <section className={cn(
      'dash-lift relative flex flex-col overflow-hidden rounded-xl bg-secondary shadow-xs ring-1 ring-inset ring-secondary',
      className,
    )}>
      {hasHeader ? (
        <div className={cn(
          'flex items-center justify-between gap-3 px-5',
          divider ? 'border-b border-secondary py-4' : 'pt-5',
        )}>
          <div className="flex min-w-0 items-center gap-2.5">
            {icon}
            {title ? <h2 className="truncate text-sm font-semibold text-primary">{title}</h2> : null}
          </div>
          {action}
        </div>
      ) : null}
      <div className={cn(bodyClassName ?? (hasHeader && !divider ? 'px-5 pb-5 pt-4' : 'p-5'))}>
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

// Count-up tween for KPI numbers. Animates from 0 → target on mount (and
// between values on update) with an ease-out curve, so the dashboard's
// headline figures "land" instead of snapping in. Honors
// prefers-reduced-motion by jumping straight to the value. Writes the DOM
// node's text directly via motion's `animate` (no React state) so the
// per-frame updates don't re-render the card — and so toLocaleString
// thousands separators match the static render.
function CountUp({ value, duration = 0.7, suffix }: { value: number; duration?: number; suffix?: string }) {
  const ref = useRef<HTMLSpanElement>(null);
  const fromRef = useRef(0);
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const from = fromRef.current;
    fromRef.current = value;
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    const fmt = (v: number) => `${Math.round(v).toLocaleString()}${suffix ?? ''}`;
    if (reduce || from === value) {
      node.textContent = fmt(value);
      return;
    }
    const controls = animate(from, value, {
      duration,
      ease: [0.2, 0.9, 0.3, 1],
      onUpdate: (v) => { node.textContent = fmt(v); },
    });
    return () => controls.stop();
  }, [value, duration, suffix]);
  return <span ref={ref}>{`${(0).toLocaleString()}${suffix ?? ''}`}</span>;
}

// ── Confidence donut ────────────────────────────────────────────────
// Untitled UI pie/donut chart (recharts) — one rounded segment per confidence
// band, with the % matched read out in the center and a UUI tooltip on hover.
// An empty library renders a single neutral track ring.
function ConfidenceRing({ segments, centerValue, centerLabel }: {
  segments: { k: string; label: string; color: string; value: number }[];
  centerValue: ReactNode;
  centerLabel: string;
}) {
  const data = segments.filter(s => s.value > 0).map(s => ({ name: s.label, value: s.value, fill: s.color }));
  const hasData = data.length > 0;
  // Recharts needs a non-empty dataset — fall back to one neutral track ring.
  const chartData = hasData ? data : [{ name: 'Empty', value: 1, fill: 'var(--hover)' }];

  return (
    <div className="relative grid size-[156px] shrink-0 place-items-center">
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie
            key="pie"
            data={chartData}
            dataKey="value"
            nameKey="name"
            innerRadius={54}
            outerRadius={70}
            startAngle={90}
            endAngle={-270}
            paddingAngle={hasData ? 2.5 : 0}
            cornerRadius={hasData ? 7 : 0}
            stroke="none"
            isAnimationActive={hasData}
          />
          {hasData ? <Tooltip key="tooltip" cursor={false} content={<ChartTooltipContent isPieChart />} /> : null}
        </PieChart>
      </ResponsiveContainer>
      <div className="pointer-events-none absolute inset-0 grid place-items-center text-center">
        <div>
          <div className="text-[34px] font-bold leading-none tracking-tight tabular-nums text-primary">{centerValue}</div>
          <div className="mt-1 text-[10px] uppercase tracking-[0.14em] text-quaternary">{centerLabel}</div>
        </div>
      </div>
    </div>
  );
}

// ── Hero poster ambiance ────────────────────────────────────────────
// A fanned cluster of recent poster thumbnails behind the hero, lazy-loaded
// and heavily blurred/dimmed so the headline stays readable. Posters that
// fail to load simply hide themselves. Decorative only.
function PosterFan({ urls }: { urls: string[] }) {
  if (urls.length === 0) return null;
  return (
    <div aria-hidden className="dash-poster-fan pointer-events-none absolute inset-y-0 right-0 hidden w-[440px] overflow-hidden lg:block">
      <div className="dash-poster-fan-inner absolute right-6 top-1/2 flex -translate-y-1/2">
        {urls.slice(0, 6).map((u, i) => (
          <img
            key={u + i}
            src={u}
            alt=""
            loading="lazy"
            decoding="async"
            className="dash-poster-card"
            style={{ '--i': i } as React.CSSProperties}
            onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none'; }}
          />
        ))}
      </div>
    </div>
  );
}

// ── Subtitle coverage tile ──────────────────────────────────────────
// Library-wide "% of inspected files that have every wanted subtitle
// language", with a one-click backfill of the whole missing set. Fetches
// its own coverage snapshot (cheap, pure-read endpoint) and narrates the
// fetch through the activity pill. Hidden entirely when no source is
// configured or nothing's been inspected yet (nothing useful to say).
function SubtitleCoverageCard({ setActive }: { setActive: (p: 'dashboard' | 'review' | 'history' | 'settings') => void }) {
  const [cov, setCov] = useState<Awaited<ReturnType<typeof api.subtitleCoverage>> | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const load = () => { void api.subtitleCoverage().then(setCov).catch(() => {}); };
  useEffect(() => {
    load();
    // Refresh when subtitle work lands elsewhere ("Get all" backfill finishes,
    // a per-file fetch completes) — the tile used to stay stale until a full
    // page remount even though the numbers had changed on disk.
    const onChange = () => load();
    window.addEventListener('kira:files-changed', onChange);
    return () => window.removeEventListener('kira:files-changed', onChange);
  }, []);

  if (!cov || cov.inspected === 0) return null;

  const pct = cov.inspected > 0 ? Math.round((cov.covered / cov.inspected) * 100) : 100;
  const topLangs = Object.entries(cov.by_language).sort((a, b) => b[1] - a[1]).slice(0, 4);

  const getAll = async () => {
    if (busy) return;
    setBusy(true);
    setNote(null);
    try {
      const res = await api.backfillSubtitles({ scope: 'library' });
      window.dispatchEvent(new Event('kira:activity-refresh'));
      setNote(res.started ? `Fetching for ${res.queued} file${res.queued === 1 ? '' : 's'}…` : (res.detail ?? 'Nothing to fetch.'));
    } catch (e) {
      setNote((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card
      title="Subtitle coverage"
      icon={<FeaturedIcon size="sm" color={cov.missing_files > 0 ? 'warning' : 'success'} icon={<IcCaption />} />}
      action={<CardLink label="Settings" onClick={() => setActive('settings')} />}
    >
      <div className="flex flex-col gap-3.5">
        <div className="flex items-end justify-between gap-3">
          <div className="flex items-end gap-2">
            <span className="text-2xl font-bold leading-none tracking-tight text-primary tabular-nums">{pct}%</span>
            <span className="mb-0.5 text-xs text-tertiary">covered · {cov.wanted.map(l => l.toUpperCase()).join(', ') || 'EN'}</span>
          </div>
          {cov.missing_files > 0 && cov.enabled ? (
            <Button color="secondary" size="sm" iconLeading={IcDownload} isLoading={busy} showTextWhileLoading onClick={getAll}>
              Get all ({cov.missing_files})
            </Button>
          ) : null}
        </div>
        <ProgressBar value={cov.covered} max={Math.max(1, cov.inspected)} color="var(--accent)" className="w-full" />
        {topLangs.length > 0 ? (
          <div className="flex flex-wrap gap-x-4 gap-y-1.5">
            {topLangs.map(([lang, n]) => (
              <span key={lang} className="text-[12px] text-secondary">
                <span className="font-mono uppercase text-tertiary">{lang}</span> · {n} missing
              </span>
            ))}
          </div>
        ) : (
          <div className="text-[12px] text-tertiary">Every inspected file has your preferred languages.</div>
        )}
        {!cov.enabled ? (
          <div className="text-[11px] text-tertiary">No subtitle source configured — add one in Settings → Subtitles.</div>
        ) : null}
        {note ? <div className="text-[11px] text-secondary">{note}</div> : null}
      </div>
    </Card>
  );
}

export function DashboardPage({ state, openModal, runScan, runReparse, setActive, scanRoot: SCAN_ROOT }: Props) {
  // Hardlink savings (fetched once per mount + after files change).
  const [hardlinkSaved, setHardlinkSaved] = useState<{ files: number; bytes_saved: number } | null>(null);
  useEffect(() => {
    const load = () => { void api.hardlinkSavings().then(setHardlinkSaved).catch(() => {}); };
    load();
    window.addEventListener('kira:files-changed', load);
    return () => window.removeEventListener('kira:files-changed', load);
  }, []);

  // ── Duplicates (same logic as the Review page's Duplicates lens) ─────
  // Groups where 2+ files landed on the same episode slot / movie, plus the
  // bytes you'd reclaim keeping only the largest file of each group.
  const dupes = useMemo(() => {
    let groups = 0, wasted = 0;
    const wastedOf = (files: { sizeBytes?: number }[]) => {
      const sizes = files.map(f => f.sizeBytes ?? 0).sort((a, b) => b - a);
      return sizes.slice(1).reduce((a, b) => a + b, 0);
    };
    for (const it of buildLibraryItems(state.files)) {
      if (it.ghost || it.files.length < 2) continue;
      if (it.kind === 'movie') {
        groups++;
        wasted += wastedOf(it.files);
        continue;
      }
      const perSlot = new Map<number, typeof it.files>();
      for (const f of it.files) {
        if (f.matchedToEpisode == null) continue;
        let slot = perSlot.get(f.matchedToEpisode);
        if (!slot) { slot = []; perSlot.set(f.matchedToEpisode, slot); }
        slot.push(f);
      }
      let hit = false;
      for (const files of perSlot.values()) {
        if (files.length < 2) continue;
        hit = true;
        wasted += wastedOf(files);
      }
      if (hit) groups++;
    }
    return { groups, wasted };
  }, [state.files]);

  // ── Automation status (which hands-off features are actually armed) ──
  const [automation, setAutomation] = useState<{
    watch: boolean; scheduled: boolean; schedTime: string;
    autoApprove: boolean; subsAuto: boolean; webhook: boolean;
  } | null>(null);
  useEffect(() => {
    void api.getSettings().then(st => {
      const b = (v: unknown): boolean =>
        v === true || (typeof v === 'object' && v !== null && (v as { value?: unknown }).value === true);
      const wc = st['watch.config'] as { auto_scan?: unknown } | undefined;
      const time = st['scanning.scheduled_time'];
      setAutomation({
        watch: !!wc && wc.auto_scan === true,
        scheduled: b(st['scanning.scheduled']),
        schedTime: typeof time === 'string' && /^\d{2}:\d{2}$/.test(time) ? time : '03:00',
        autoApprove: b(st['matching.auto_approve']),
        subsAuto: b(st['subtitles.auto_fetch']),
        webhook: (() => {
          const v = st['integrations.webhook.token'];
          return (typeof v === 'object' && v !== null && (v as { set?: boolean }).set === true)
            || (typeof v === 'string' && v.length > 0);
        })(),
      });
    }).catch(() => {});
  }, []);

  // ── Library quality insights (the tech-tag payoff) ──────────────────
  // Resolution mix, HDR share, codec split, and the "worth re-downloading"
  // list — all derived from the MediaInfo tags scans already collect.
  const quality = useMemo(() => {
    const files = state.files.filter(f => f.mediaType !== 'music');
    const tier = (q?: string) => {
      const v = (q || '').toLowerCase();
      if (v.includes('2160') || v.includes('4k')) return '4K';
      if (v.includes('1080')) return 'HD';
      if (v.includes('720')) return '720p';
      if (v.includes('480') || v.includes('576')) return 'SD';
      return null;
    };
    const mix = { '4K': 0, HD: 0, '720p': 0, SD: 0 } as Record<string, number>;
    let tagged = 0, hdr = 0;
    const codecs = new Map<string, number>();
    const weak: { title: string; q: string }[] = [];
    const seen = new Set<string>();
    for (const f of files) {
      const t = tier(f.quality);
      if (!t) continue;
      tagged++;
      mix[t]++;
      if (f.hdr) hdr++;
      const c = (f.codec || '').toUpperCase();
      if (c) codecs.set(c, (codecs.get(c) ?? 0) + 1);
      if ((t === '720p' || t === 'SD') && f.match?.title && !seen.has(f.match.title)) {
        seen.add(f.match.title);
        weak.push({ title: f.match.title, q: t });
      }
    }
    const topCodecs = [...codecs.entries()].sort((a, b) => b[1] - a[1]).slice(0, 3);
    return { tagged, mix, hdr, topCodecs, weak: weak.slice(0, 5), weakTotal: seen.size };
  }, [state.files]);

  void openModal; void SCAN_ROOT;
  // Re-parse scope menu (whole library vs one media type). Per-album reparse lives
  // in the cover popup; this is the bulk "just music / anime / …" entry point.
  const [reparseOpen, setReparseOpen] = useState(false);
  const reparseRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!reparseOpen) return;
    const onDown = (ev: MouseEvent) => {
      if (reparseRef.current && !reparseRef.current.contains(ev.target as Node)) setReparseOpen(false);
    };
    window.addEventListener('mousedown', onDown);
    return () => window.removeEventListener('mousedown', onDown);
  }, [reparseOpen]);
  const [history, setHistory] = useState<ApiHistoryEntry[]>([]);
  // Authoritative all-time rename count for the "Organized" KPI. `history` is
  // backend-capped (period:'all' returns at most N rows), so its length
  // under-reports on a large library; /counts is the true total.
  const [totalRenames, setTotalRenames] = useState<number | null>(null);
  const [notifications, setNotifications] = useState<ApiNotification[]>([]);
  const [lastScan, setLastScan] = useState<ApiScan | null>(null);
  const [scans, setScans] = useState<ApiScan[]>([]);
  const [providerStatus, setProviderStatus] = useState<Record<string, 'ok' | 'fail' | 'unknown'>>({
    tmdb: 'unknown', tvdb: 'unknown',
  });
  const [activityHydrated, setActivityHydrated] = useState(false);

  // Single 10s poll for the whole dashboard — history, notifications, scans
  // and provider status used to run on two independent intervals, doubling the
  // timers and the request bursts. The `cancelled` guard also stops any
  // setState after unmount.
  useEffect(() => {
    let cancelled = false;
    let inFlight = false;   // reentrancy guard: a slow backend (>10s across the
    // sequential requests) let interval ticks pile up overlapping loads, and an
    // OLDER response could overwrite a newer one.
    const load = async () => {
      if (inFlight) return;
      inFlight = true;
      try { const h = await api.listHistory({ period: 'all' }); if (!cancelled) setHistory(h); } catch { /* */ }
      try { const c = await api.historyCounts(); if (!cancelled) setTotalRenames(c.all); } catch { /* */ }
      try { const n = await api.listNotifications(); if (!cancelled) setNotifications(n); } catch { /* */ }
      try {
        const allScans = await api.listScans();
        if (!cancelled) { setScans(allScans); setLastScan(allScans[0] ?? null); }
      } catch { /* */ }
      try {
        const list = await api.getProviders();
        if (!cancelled) {
          const next: Record<string, 'ok' | 'fail' | 'unknown'> = {};
          for (const p of list) {
            if (!p.implemented) next[p.key] = 'unknown';
            else next[p.key] = p.configured ? 'ok' : 'fail';
          }
          setProviderStatus(s => ({ ...s, ...next }));
        }
      } catch {
        if (!cancelled) setProviderStatus(s => ({ ...s, tmdb: 'fail', tvdb: 'fail' }));
      }
      if (!cancelled) setActivityHydrated(true);
      inFlight = false;
    };
    void load();
    const t = setInterval(load, 10_000);
    return () => { cancelled = true; clearInterval(t); };
  }, []);

  const stats = useMemo(() => {
    const files = state.files;
    // "matched" = has a REAL provider match, regardless of confidence band — a
    // library fully matched at mid confidence is still matched. The old
    // `confidence >= high` gate made the donut read a scary-low "% matched" for
    // a fully-matched library and disagreed with Review's pending/matched split.
    // `lowConf` honors the user's Confidence sliders (Settings → Confidence);
    // `buckets` is the fixed 4-tier verdict scale (Strong/Likely/Review/Low)
    // shared with the match badges, independent of the slider.
    const { mid } = getConfBands();
    const total = files.length;
    const matched = files.filter(f => !!f.match?.provider && !!f.match?.providerId).length;
    const pending = files.filter(f => f.status === 'pending').length;
    const approved = files.filter(f => f.status === 'approved').length;
    const lowConf = files.filter(f => f.confidence < mid).length;
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
  // Composition only lists types the library actually has — a permanent
  // "Music · 0" row is noise. Empty library falls back to showing all four.
  const typesWithFiles = TYPE_META.filter(t => stats.byType[t.k] > 0);
  const compositionTypes = typesWithFiles.length > 0 ? typesWithFiles : TYPE_META;
  const typesPresent = typesWithFiles.length;

  // Recent poster URLs for the hero ambiance — de-duplicated, capped. Derived
  // from the existing file list (no extra fetch); empty when no real covers.
  const posterUrls = useMemo(() => {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const f of state.files) {
      const u = f.match?.posterUrl;
      if (u && !seen.has(u)) { seen.add(u); out.push(u); }
      if (out.length >= 6) break;
    }
    return out;
  }, [state.files]);

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
        text: <><b>{h.title || filename}</b>{' — '}<span className="font-mono text-xs">{h.operation}</span>{h.undone_at ? <span className="text-tertiary"> · undone</span> : null}</>,
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
    { slug: 'anidb', name: 'AniDB', note: 'Anime episodes & franchises' },
  ];

  const scanning = state.scanRunning;
  const scanPct = Math.round(state.scanProgress);

  return (
    <div className="page relative">
      {/* ── Hero band ───────────────────────────────────────────── */}
      {/* NOTE: no overflow-hidden on the section — it was clipping the
          Re-parse dropdown (the menu lives inside this stacking context).
          The decorative layers that DO need clipping (poster fan + scrim)
          are wrapped in their own clipped, rounded inset layer instead. */}
      <section className={cn('dash-hero anim-rise relative z-10 mb-5 rounded-3xl border border-secondary p-7 sm:p-8', scanning && 'dash-hero-live')}>
        <div className="pointer-events-none absolute inset-0 overflow-hidden rounded-3xl">
          <PosterFan urls={posterUrls} />
          {/* readability scrim over the poster fan */}
          <div className="dash-hero-scrim absolute inset-0" />
        </div>

        <div className="relative z-10 flex flex-wrap items-end justify-between gap-x-6 gap-y-5">
          <div className="min-w-0 max-w-xl">
            <BadgeWithDot color={scanning ? 'warning' : 'brand'} pulse className="mb-3">
              {scanning ? (state.scanPhase === 'matching' ? 'Matching…' : 'Scanning…') : 'Live'}
            </BadgeWithDot>
            <h1 className="dash-hero-title bg-gradient-to-br from-white via-white to-white/45 bg-clip-text text-[40px] font-bold leading-[1.04] tracking-[-0.03em] text-transparent sm:text-[46px]">
              Welcome back
            </h1>
            {/* Hardlink bragging rights — how much disk the link strategy is
                saving right now (each hardlinked file would otherwise exist
                twice). Hidden until at least one hardlink exists. */}
            {hardlinkSaved && hardlinkSaved.bytes_saved > 0 ? (
              <div className="mt-1.5 flex items-center gap-1.5 text-[12.5px] text-tertiary">
                <IcLink className="size-3.5 text-[var(--accent-bright)]" />
                <span><b className="font-semibold text-secondary">{formatBytes(hardlinkSaved.bytes_saved)}</b> saved by hardlinks across {hardlinkSaved.files.toLocaleString()} files</span>
              </div>
            ) : null}

            {scanning ? (
              <div className="mt-4 max-w-md">
                <div className="mb-2 flex items-center justify-between gap-3 text-[13px]">
                  <span className="truncate text-secondary">
                    {state.scanMessage || (state.scanPhase === 'matching' ? 'Matching against providers…' : 'Discovering files…')}
                  </span>
                  <span className="shrink-0 font-mono tabular-nums text-tertiary">
                    {state.scanPhase === 'matching' ? `${scanPct}%` : `${state.scanFound} found`}
                  </span>
                </div>
                <ProgressBar
                  value={scanPct}
                  max={100}
                  color="var(--accent)"
                  height={8}
                  indeterminate={state.scanPhase !== 'matching'}
                  className="w-full"
                />
              </div>
            ) : (
              <p className="mt-3 text-[13.5px] text-tertiary">
                {stats.total === 0
                  ? 'No library scanned yet — hit Scan now to get started.'
                  : <><b className="text-secondary">{stats.pending}</b> pending review · <b className="text-secondary">{stats.matched}</b> matched{lastScan?.completed_at ? <> · last scan {relativeTime(lastScan.completed_at)}</> : null}</>}
              </p>
            )}
          </div>

          <div className="flex shrink-0 items-center gap-2.5">
            <div className="relative" ref={reparseRef}>
              <Button
                size="md"
                color="secondary"
                iconLeading={IcRefresh}
                isDisabled={scanning}
                onClick={() => setReparseOpen(v => !v)}
                className="dash-hero-btn-secondary"
                title="Re-run the parser + re-match + re-read tech tags in place — applies parsing/grouping improvements without losing manual picks. Pick the whole library or just one media type."
              >
                Re-parse ▾
              </Button>
              {reparseOpen ? (
                <div className="absolute right-0 z-50 mt-1.5 min-w-[180px] overflow-hidden rounded-xl bg-secondary p-1 shadow-lg ring-1 ring-inset ring-secondary">
                  {([
                    ['Whole library', undefined],
                    ['Music only', 'music'],
                    ['Anime only', 'anime'],
                    ['TV only', 'tv'],
                    ['Movies only', 'movie'],
                  ] as Array<[string, string | undefined]>).map(([label, mt]) => (
                    <button
                      key={label}
                      type="button"
                      onClick={() => { setReparseOpen(false); runReparse(mt ? { media_type: mt } : undefined); }}
                      className="block w-full rounded-lg px-3 py-2 text-left text-[13px] font-medium text-secondary transition-colors hover:bg-tertiary hover:text-primary"
                    >
                      {label}
                    </button>
                  ))}
                  {/* Lighter than Re-parse: keeps parse data, just re-runs matching. */}
                  <div className="my-1 h-px bg-white/[0.08]" />
                  <button
                    type="button"
                    onClick={() => {
                      setReparseOpen(false);
                      void api.rematchAll().then(() => {
                        window.dispatchEvent(new CustomEvent('kira:files-changed'));
                      }).catch(() => { /* connectivity UI covers failures */ });
                    }}
                    className="block w-full rounded-lg px-3 py-2 text-left text-[13px] font-medium text-secondary transition-colors hover:bg-tertiary hover:text-primary"
                    title="Re-run matching only (keeps existing parse data) — faster than a full re-parse"
                  >
                    Re-match only (faster)
                  </button>
                </div>
              ) : null}
            </div>
            <Button
              size="md"
              color="primary"
              iconLeading={scanning ? undefined : IcScan}
              isLoading={scanning}
              isDisabled={scanning}
              showTextWhileLoading
              onClick={runScan}
              className="dash-hero-cta"
              title="Scan the configured library root for new files and match them."
            >
              {scanning ? 'Scanning…' : 'Scan now'}
            </Button>
          </div>
        </div>
      </section>

      {/* ── KPI strip ──────────────────────────────────────────── */}
      <div className="relative z-10 grid grid-cols-2 gap-4 lg:grid-cols-4">
        <MetricCard
          icon={<IcFolder />}
          tint="#6ea8ff"
          label="Library"
          value={<CountUp value={stats.total} />}
          sub={typesPresent > 0 ? `${typesPresent} media type${typesPresent === 1 ? '' : 's'}` : 'No files yet'}
        />
        <MetricCard
          icon={<IcCheck />}
          color="success"
          label="Matched"
          value={<CountUp value={stats.matched} />}
          sub={`${matchedPct}% of library`}
          onClick={() => setActive('review')}
        />
        <MetricCard
          icon={<IcReview />}
          color="warning"
          label="Pending review"
          value={<CountUp value={stats.pending} />}
          sub={stats.lowConf > 0
            ? <span className="inline-flex items-center gap-1 text-error-primary"><IcAlertTri className="size-3" />{stats.lowConf} low-confidence</span>
            : 'All reviewed'}
          onClick={() => setActive('review')}
        />
        {/* All-time renames from the history feed — "approved" was a dead
            metric (it's a transient state: approved files are renamed moments
            later, so the count read 0 forever). */}
        <MetricCard
          icon={<IcSparkles />}
          tint="#b48cff"
          label="Organized"
          value={<CountUp value={totalRenames ?? history.length} />}
          sub={(totalRenames ?? history.length) > 0 ? 'renames in history' : 'No renames yet'}
          onClick={() => setActive('history')}
        />
      </div>

      {/* ── Main region: left stack + full-height activity ─────── */}
      <div className="relative z-10 mt-4 grid grid-cols-1 gap-4 xl:grid-cols-3">
        <div className="flex flex-col gap-4 xl:col-span-2">
          {/* Library quality — resolution mix bar + HDR share + upgrade list.
              Monochrome ramp (brighter = better) per the design language;
              hidden until MediaInfo has tagged at least a few files. */}
          {quality.tagged >= 3 ? (
            <Card
              title="Library quality"
              icon={<FeaturedIcon size="sm" color="gray" icon={<IcFilm />} />}
              action={quality.weakTotal > 0 ? <span className="text-[11.5px] text-tertiary">{quality.weakTotal} title{quality.weakTotal === 1 ? '' : 's'} below 1080p</span> : undefined}
            >
              <div className="flex flex-col gap-3.5">
                {/* Mix bar — one segment per resolution tier present. */}
                <div className="flex h-2 gap-px overflow-hidden rounded-full" role="img" aria-label="Resolution mix">
                  {([['4K', 'rgba(255,255,255,0.92)'], ['HD', 'rgba(255,255,255,0.55)'], ['720p', 'rgba(255,255,255,0.28)'], ['SD', 'var(--conf-low)']] as const).map(([k, color]) => (
                    quality.mix[k] > 0 ? (
                      <span key={k} title={`${k}: ${quality.mix[k]}`} style={{ flex: quality.mix[k], background: color }} />
                    ) : null
                  ))}
                </div>
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[12.5px] text-tertiary">
                  {(['4K', 'HD', '720p', 'SD'] as const).map(k => quality.mix[k] > 0 ? (
                    <span key={k} className="tabular-nums"><b className="font-semibold text-secondary">{Math.round((quality.mix[k] / quality.tagged) * 100)}%</b> {k}</span>
                  ) : null)}
                  <span className="tabular-nums"><b className="font-semibold text-secondary">{Math.round((quality.hdr / quality.tagged) * 100)}%</b> HDR</span>
                  {quality.topCodecs.map(([c, n]) => (
                    <span key={c} className="tabular-nums text-quaternary">{c} ×{n}</span>
                  ))}
                </div>
                {quality.weak.length > 0 ? (
                  <div className="border-t border-secondary pt-3">
                    <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-quaternary">Upgrade candidates</div>
                    <div className="flex flex-wrap gap-1.5">
                      {quality.weak.map(w => (
                        <span key={w.title} className="inline-flex items-center gap-1.5 rounded-md bg-tertiary px-2 py-1 text-[12px] text-secondary ring-1 ring-inset ring-secondary">
                          {w.title}
                          <span className="tech-badge">{w.q}</span>
                        </span>
                      ))}
                    </div>
                  </div>
                ) : null}
              </div>
            </Card>
          ) : null}

          {/* Match quality — ring + confidence buckets */}
          <Card
            title="Match quality"
            icon={<FeaturedIcon size="sm" color="success" icon={<IcShieldCheck />} />}
            action={<CardLink label="Review" onClick={() => setActive('review')} />}
          >
            <div className="flex flex-col items-center gap-7 sm:flex-row sm:gap-8">
              <ConfidenceRing
                segments={[
                  { k: 'strong', label: 'Strong', color: 'var(--conf-high)', value: stats.buckets.strong },
                  { k: 'likely', label: 'Likely', color: 'var(--info)', value: stats.buckets.likely },
                  { k: 'review', label: 'Needs review', color: 'var(--conf-mid)', value: stats.buckets.review },
                  { k: 'low',    label: 'Low / none', color: 'var(--conf-low)', value: stats.buckets.low },
                ]}
                centerValue={<><CountUp value={matchedPct} suffix="" /><span className="text-xl text-tertiary">%</span></>}
                centerLabel="matched"
              />

              <div className="flex w-full flex-col gap-3.5">
                {BUCKET_META.map(b => {
                  const n = stats.buckets[b.k];
                  return (
                    <div key={b.k} className="flex items-center gap-3">
                      <span className="inline-flex w-28 shrink-0 items-center gap-2 text-[13px] text-secondary">
                        <span className="size-2 shrink-0 rounded-full" style={{ background: b.color }} />
                        {b.label}
                      </span>
                      <ProgressBar value={n} max={Math.max(1, stats.total)} color={b.color} className="flex-1" />
                      <span className="w-8 shrink-0 text-right font-mono text-xs tabular-nums text-secondary">{n}</span>
                    </div>
                  );
                })}
              </div>
            </div>
          </Card>

          {/* Library composition — per-type bars */}
          <Card
            title="Library composition"
            icon={<FeaturedIcon size="sm" tint="#8b9eff" icon={<IcFilm />} />}
          >
            <div className="flex flex-col gap-3">
              {compositionTypes.map(t => {
                const n = stats.byType[t.k];
                return (
                  <div key={t.k} className="flex items-center gap-3">
                    <span className="inline-flex w-20 shrink-0 items-center gap-1.5 text-[13px] text-secondary [&_svg]:size-3.5">
                      <span style={{ color: t.color }} className="inline-flex"><t.icon /></span>
                      {t.label}
                    </span>
                    <ProgressBar value={n} max={maxType} color={t.color} minVisible={4} className="flex-1" />
                    <span className="w-9 shrink-0 text-right font-mono text-xs tabular-nums text-secondary">{n}</span>
                  </div>
                );
              })}
            </div>
          </Card>

          {/* Providers + Storage */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Card
              title="Providers"
              icon={<FeaturedIcon size="sm" tint="#4ec5b3" icon={<IcLink />} />}
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
                        <div className="text-[13px] font-medium text-primary">{p.name}</div>
                        <div className="truncate text-[11px] text-quaternary">{p.note}</div>
                      </div>
                      <BadgeWithDot color={color}>{label}</BadgeWithDot>
                    </div>
                  );
                })}
              </div>
            </Card>

            <Card
              title="Storage"
              icon={<FeaturedIcon size="sm" tint="#e0a44e" icon={<IcFolder />} />}
            >
              {stats.totalSize > 0 ? (
                <div className="flex flex-col gap-3.5">
                  <div className="flex items-end gap-2">
                    <span className="text-2xl font-bold leading-none tracking-tight text-primary">{formatBytes(stats.totalSize)}</span>
                    <span className="mb-0.5 text-xs text-tertiary">total</span>
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
                        <span className="inline-flex items-center gap-1.5 text-secondary">
                          <span className="size-2 rounded-full" style={{ background: t.color }} />
                          {t.label}
                        </span>
                        <span className="font-mono tabular-nums text-tertiary">{formatBytes(stats.sizeByType[t.k])}</span>
                      </div>
                    ))}
                  </div>
                  {dupes.groups > 0 ? (
                    <button
                      type="button"
                      onClick={() => {
                        try { sessionStorage.setItem('kira.review.dupes', '1'); } catch { /* */ }
                        setActive('review');
                      }}
                      className="group -mx-1 flex items-center justify-between gap-2 rounded-lg px-1 py-1 text-left text-[12px] transition-colors hover:bg-white/[0.04]"
                      title="Open the Review page with the Duplicates filter on"
                    >
                      <span className="inline-flex items-center gap-1.5 text-secondary">
                        <IcAlertTri className="size-3.5 text-[var(--conf-mid)]" />
                        {dupes.groups} duplicate group{dupes.groups === 1 ? '' : 's'}
                      </span>
                      <span className="inline-flex items-center gap-1 font-mono tabular-nums text-tertiary">
                        {dupes.wasted > 0 ? <>{formatBytes(dupes.wasted)} reclaimable</> : 'review'}
                        <IcArrowRight className="size-3 opacity-0 transition-opacity group-hover:opacity-100" />
                      </span>
                    </button>
                  ) : null}
                </div>
              ) : (
                <div className="flex h-full flex-col items-center justify-center gap-1 py-4 text-center">
                  <div className="text-2xl font-bold tracking-tight text-primary tabular-nums">{stats.total}</div>
                  <div className="text-xs text-tertiary">files · size unavailable</div>
                </div>
              )}
            </Card>

            {/* Automation — which hands-off features are actually armed. The
                settings exist whether or not anyone turned them on; this makes
                a silently-off watcher/schedule visible at a glance. */}
            {automation ? (
              <Card
                title="Automation"
                icon={<FeaturedIcon size="sm" tint="#8b95ff" icon={<IcRefresh />} />}
                action={<CardLink label="Configure" onClick={() => setActive('settings')} />}
              >
                <div className="flex flex-col gap-3">
                  {[
                    { label: 'Watch folders', note: 'scan when new files appear', on: automation.watch, detail: null as string | null },
                    { label: 'Nightly rescan', note: 'full sweep for files the watcher missed', on: automation.scheduled, detail: automation.scheduled ? automation.schedTime : null },
                    { label: 'Auto-approve', note: 'confident matches skip Review', on: automation.autoApprove, detail: null },
                    { label: 'Subtitle auto-fetch', note: 'grab missing subs after rename + scan', on: automation.subsAuto, detail: null },
                    { label: 'Inbound webhooks', note: 'Sonarr / Radarr push imports instantly', on: automation.webhook, detail: null },
                  ].map(r => (
                    <div key={r.label} className="flex items-center justify-between gap-2">
                      <div className="min-w-0">
                        <div className="text-[13px] font-medium text-primary">{r.label}</div>
                        <div className="truncate text-[11px] text-quaternary">{r.note}</div>
                      </div>
                      <BadgeWithDot color={r.on ? 'success' : 'gray'}>
                        {r.on ? (r.detail ?? 'On') : 'Off'}
                      </BadgeWithDot>
                    </div>
                  ))}
                </div>
              </Card>
            ) : null}
          </div>

          {/* Subtitle coverage — hidden until there's something to report */}
          <SubtitleCoverageCard setActive={setActive} />
        </div>

        {/* Recent activity — full-height right rail, as a timeline */}
        <Card
          title="Recent activity"
          icon={<FeaturedIcon size="sm" color="gray" icon={<IcHistory />} />}
          action={<CardLink label="History" onClick={() => setActive('history')} />}
          divider
          className="min-h-[460px] xl:col-span-1 xl:h-full"
          bodyClassName="flex-1 min-h-0 overflow-y-auto p-4 [scrollbar-width:thin]"
        >
          {!activityHydrated ? (
            <div className="flex flex-col gap-1">
              {Array.from({ length: 7 }).map((_, i) => (
                <div key={i} className="flex items-center gap-3 px-1.5 py-2">
                  <Skeleton w={32} h={32} radius={8} />
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
            <ActivityFeed
              items={activity.map((a): ActivityFeedItem => ({
                id: a.id,
                icon: a.kind === 'success' ? <IcCheck /> : a.kind === 'error' ? <IcX /> : <IcSparkles />,
                color: a.kind === 'success' ? 'success' : a.kind === 'error' ? 'error' : 'gray',
                text: a.text,
                time: relativeTime(a.when),
              }))}
            />
          )}
        </Card>
      </div>
    </div>
  );
}
