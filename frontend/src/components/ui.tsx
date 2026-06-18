import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { AnimatePresence, motion } from 'motion/react';
import type { PosterData, ToastData, Page, MediaType } from '../lib/types';
import type { SettingsSection } from '../App';
import {
  IcDashboard, IcReview, IcHistory, IcSettings, IcSearch,
  IcCheck, IcX, IcAlertTri, IcKeyboard, IcScan, IcSpin,
  IcLogoMark, IcFilm, IcTv, IcAnime, IcMusic, IcChevDown, IcMenu, IcChevLeft,
} from '../lib/icons';
import { NotificationsBell } from './NotificationsBell';
import { api, hasStoredAuth, clearStoredAuth } from '../lib/api';
import { cn } from '../lib/utils';
import { confLevel } from '../lib/confBands';
import { Button } from './base/buttons/button';
import { FeaturedIcon } from './base/featured-icons/featured-icon';

export function Poster({ data, imgUrl, size = 'md', shape = 'poster', className = '' }: {
  data: PosterData | null | undefined;
  imgUrl?: string | null;
  size?: string;
  shape?: 'poster' | 'square';
  className?: string;
}) {
  const shapeClass = shape === 'square' ? 'shape-square' : '';
  const [imgFailed, setImgFailed] = useState(false);

  // Real image path — render <img>, fall back to gradient on error or 404.
  if (imgUrl && !imgFailed) {
    return (
      <div className={`poster ${shapeClass} size-${size} ${className}`} style={{
        background: '#0a0815',
        overflow: 'hidden',
      }}>
        <img
          src={imgUrl}
          alt=""
          loading="lazy"
          referrerPolicy="no-referrer"
          onError={() => setImgFailed(true)}
          style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
        />
      </div>
    );
  }

  if (!data) {
    return (
      <div className={`poster ${shapeClass} size-${size} ${className}`} style={{
        background: 'rgba(255,255,255,0.04)',
        border: '1px dashed rgba(255,255,255,0.12)',
      }}>
        <span className="pinit" style={{ opacity: 0.4, fontSize: 14 }}>?</span>
      </div>
    );
  }
  const [a, b] = data.tint;
  return (
    <div className={`poster ${shapeClass} size-${size} ${className}`} style={{
      background: `linear-gradient(135deg, ${a}, ${b})`,
    }}>
      <span className="pinit">{data.init}</span>
      {data.year ? <span className="pyr">{data.year}</span> : null}
    </div>
  );
}

export function ConfidenceBadge({ value }: { value: number | null | undefined }) {
  if (value == null) return null;
  if (value === 0) return (
    <span className="badge badge-neutral"><span className="dot" />No match</span>
  );
  // Score → plain-English verdict. New users immediately know whether to
  // trust the match; the % becomes secondary detail.
  const level = confLevel(value);
  const label = value >= 90 ? 'Strong match'
    : value >= 75 ? 'Likely match'
    : value >= 50 ? 'Needs review'
    : 'Probably wrong';
  return (
    <span className={`badge badge-${level}`} title={`Match confidence: ${value}%`}>
      <span className="dot" />
      <span className="badge-label">{label}</span>
      <span className="badge-pct">{value}%</span>
    </span>
  );
}

export function StatusPill({ status }: { status: string }) {
  if (status === 'approved') return (
    <span className="status-pill"><span className="swatch" style={{ background: 'var(--conf-high)' }} />Approved</span>
  );
  if (status === 'rejected') return (
    <span className="status-pill"><span className="swatch" style={{ background: 'var(--conf-low)' }} />Rejected</span>
  );
  if (status === 'no_match') return (
    <span className="status-pill"><span className="swatch" style={{ background: 'var(--ink-4)' }} />No match</span>
  );
  return (
    <span className="status-pill"><span className="swatch" style={{ background: 'var(--conf-mid)' }} />Pending</span>
  );
}

export function MediaTypeIcon({ type }: { type: MediaType | string }) {
  if (type === 'tv')    return <IcTv />;
  if (type === 'anime') return <IcAnime />;
  if (type === 'music') return <IcMusic />;
  return <IcFilm />;
}

export function Sidebar({ active, setActive, settingsSection, setSettingsSection, pendingCount, scanRunning, backendOk, mobileOpen = false, onClose }: {
  active: Page;
  setActive: (p: Page) => void;
  settingsSection: string;
  setSettingsSection: (s: SettingsSection) => void;
  pendingCount: number;
  scanRunning: boolean;
  backendOk: boolean | null;
  /** Mobile drawer open state (ignored on lg+ where the sidebar is static). */
  mobileOpen?: boolean;
  /** Close the mobile drawer (called after navigating). */
  onClose?: () => void;
}) {
  const items: { key: Page; label: string; icon: ReactNode; count?: number }[] = [
    { key: 'dashboard', label: 'Dashboard', icon: <IcDashboard /> },
    { key: 'review', label: 'Review', icon: <IcReview />, count: pendingCount },
    { key: 'history', label: 'History', icon: <IcHistory /> },
    { key: 'settings', label: 'Settings', icon: <IcSettings /> },
  ];
  // Sub-settings revealed when Settings is the active page (nested nav).
  // Grouped into bands (a `group` label is rendered above the first item of
  // each band). Identification gets its own "Matching" home; Subtitles is now
  // a top-level Output section instead of a card buried under Naming.
  const settingsSub: { key: SettingsSection; label: string; group?: string }[] = [
    { key: 'connections', label: 'Connections', group: 'Sources & library' },
    { key: 'paths', label: 'Library & paths' },
    { key: 'integrations', label: 'Integrations' },
    { key: 'matching', label: 'Matching', group: 'Identification' },
    { key: 'naming', label: 'Naming & format', group: 'Output' },
    { key: 'subtitles', label: 'Subtitles' },
    { key: 'cleanup', label: 'Folder cleanup' },
    { key: 'advanced', label: 'Advanced', group: 'System' },
  ];
  const statusColor = backendOk === false ? 'var(--conf-low)'
    : scanRunning ? 'var(--conf-mid)' : 'var(--conf-high)';
  const statusLabel = backendOk === false ? 'Backend disconnected'
    : scanRunning ? 'Scanning…' : backendOk === null ? 'Connecting…' : 'Idle';
  const statusLive = scanRunning || backendOk === null;

  // Collapsible rail (desktop only). Persisted so it survives reloads, and
  // mirrored onto the document root's --side-w so App.tsx's grid track
  // (grid-cols-[var(--side-w)_1fr]) reflows automatically — no App.tsx state
  // change needed. Purely presentational; never affects mobile drawer width.
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try { return localStorage.getItem('kira.sidebar.collapsed') === '1'; } catch { return false; }
  });
  // Track the desktop breakpoint reactively — the collapse feature is desktop-
  // only, so on mobile the drawer always renders full (labels + sub-nav) at
  // 240px regardless of the persisted collapse flag.
  const [isDesktop, setIsDesktop] = useState<boolean>(() => {
    try { return window.matchMedia('(min-width: 1024px)').matches; } catch { return true; }
  });
  useEffect(() => {
    const mq = window.matchMedia('(min-width: 1024px)');
    const onChange = (e: MediaQueryListEvent) => setIsDesktop(e.matches);
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);

  // Live app version — /health is the single source (backend truth), the
  // hardcoded fallback only covers "backend unreachable". Plus a gentle
  // update notice from GitHub releases, gated by `advanced.update_check`
  // (default on; flip it off in Settings → Advanced). No releases published
  // / offline / rate-limited all degrade to silence.
  const [version, setVersion] = useState<string | null>(null);
  const [updateTo, setUpdateTo] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    const newer = (a: string, b: string) => {
      const pa = a.replace(/^v/i, '').split('.').map(n => parseInt(n, 10) || 0);
      const pb = b.replace(/^v/i, '').split('.').map(n => parseInt(n, 10) || 0);
      for (let i = 0; i < 3; i++) {
        if ((pa[i] ?? 0) !== (pb[i] ?? 0)) return (pa[i] ?? 0) > (pb[i] ?? 0);
      }
      return false;
    };
    void (async () => {
      try {
        const h = await api.health();
        if (cancelled || !h.version) return;
        setVersion(h.version);
        const s = await api.getSettings();
        if (cancelled || s['advanced.update_check'] === false) return;
        const r = await fetch('https://api.github.com/repos/Nicolaslahri/Kira/releases/latest');
        if (!r.ok) return;
        const j = await r.json() as { tag_name?: string };
        const latest = (j.tag_name || '').trim();
        if (!cancelled && latest && newer(latest, h.version)) setUpdateTo(latest.replace(/^v/i, ''));
      } catch { /* informational only */ }
    })();
    return () => { cancelled = true; };
  }, []);

  // `rail` is the effective icon-only mode: collapsed AND on desktop.
  const rail = collapsed && isDesktop;
  useEffect(() => {
    const root = document.documentElement;
    // The mobile drawer is always full --side-w; only collapse the desktop rail.
    root.style.setProperty('--side-w', rail ? '76px' : '240px');
    return () => { root.style.setProperty('--side-w', '240px'); };
  }, [rail]);
  useEffect(() => {
    try { localStorage.setItem('kira.sidebar.collapsed', collapsed ? '1' : '0'); } catch { /* private mode */ }
  }, [collapsed]);

  return (
    <aside className={cn(
      'kira-sidebar fixed inset-y-0 left-0 z-50 flex h-screen w-[var(--side-w)] flex-col gap-6 px-3 py-5 transition-transform duration-300 ease-[var(--ease-out)] lg:sticky lg:top-0 lg:z-20 lg:translate-x-0',
      mobileOpen ? 'translate-x-0' : '-translate-x-full',
      rail && 'lg:items-center',
    )}>
      {/* Brand */}
      <div className={cn('flex items-center gap-3 px-2', rail && 'lg:justify-center lg:px-0')}>
        <div className="kira-brandmark press grid size-9 shrink-0 place-items-center rounded-xl text-white [&_svg]:size-5">
          <IcLogoMark />
        </div>
        {!rail ? (
          <div className="leading-tight">
            <div className="text-[15px] font-bold tracking-tight text-primary">Kira</div>
            <div className="text-[11px] text-quaternary">
              v{version ?? '0.5.0'}
              {updateTo ? (
                <a
                  href="https://github.com/Nicolaslahri/Kira/releases"
                  target="_blank" rel="noreferrer"
                  className="ml-1.5 font-medium text-info hover:underline"
                  title={`Version ${updateTo} is available on GitHub`}
                >
                  · v{updateTo} out
                </a>
              ) : null}
            </div>
          </div>
        ) : null}
        {/* Desktop rail toggle — sits top-right of the brand row when expanded. */}
        <button
          onClick={() => setCollapsed(c => !c)}
          className={cn(
            'press ml-auto hidden size-7 shrink-0 place-items-center rounded-lg text-fg-quaternary transition hover:bg-[var(--surface-hover)] hover:text-fg-tertiary lg:grid [&_svg]:size-4',
            rail && 'lg:hidden',
          )}
          title="Collapse sidebar"
          aria-label="Collapse sidebar"
        >
          <IcChevLeft />
        </button>
      </div>

      {/* Nav */}
      <nav className="flex flex-1 flex-col gap-1">
        {!rail ? (
          <div className="px-3 pb-1.5 pt-2 text-[10px] font-semibold uppercase tracking-[0.08em] text-quaternary">
            Workspace
          </div>
        ) : (
          // Rail mode: a thin expand affordance replaces the section label.
          <button
            onClick={() => setCollapsed(false)}
            className="press mb-1 hidden size-9 place-items-center self-center rounded-lg text-fg-quaternary transition hover:bg-[var(--surface-hover)] hover:text-fg-tertiary lg:grid [&_svg]:size-4 [&_svg]:rotate-180"
            title="Expand sidebar"
            aria-label="Expand sidebar"
          >
            <IcChevLeft />
          </button>
        )}
        {items.map(it => {
          const isActive = active === it.key;
          const isSettings = it.key === 'settings';
          return (
            <div key={it.key}>
              <button
                onClick={() => { setActive(it.key); if (it.key !== 'settings') onClose?.(); }}
                title={rail ? it.label : undefined}
                aria-current={isActive ? 'page' : undefined}
                className={cn(
                  'kira-nav-item group press relative flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-semibold',
                  rail && 'lg:justify-center lg:px-0',
                  isActive ? 'text-primary' : 'text-tertiary hover:text-secondary',
                )}
              >
                {/* Morphing active indicator — a springy brand-gradient pill that
                    slides between items via a shared layoutId. Sits behind the
                    label/icon (z-0); content is z-10. */}
                {isActive ? (
                  <motion.span
                    layoutId="nav-active"
                    className="kira-nav-active absolute inset-0 rounded-xl"
                    transition={{ type: 'spring', stiffness: 520, damping: 38 }}
                  />
                ) : null}
                <span className={cn(
                  'relative z-10 inline-flex size-5 shrink-0 transition-transform duration-200 ease-[var(--ease-back)] [&_svg]:size-5',
                  'group-hover:scale-110 group-active:scale-95',
                  isActive ? 'text-white' : 'text-fg-quaternary group-hover:text-fg-tertiary',
                )}>
                  {it.icon}
                </span>
                {!rail ? <span className="relative z-10 flex-1 text-left">{it.label}</span> : null}
                {it.count != null && it.count > 0 ? (
                  rail ? (
                    // Rail mode: a tiny dot on the icon corner signals "items waiting".
                    <span className="absolute right-2 top-1.5 z-10 size-1.5 rounded-full bg-conf-mid lg:right-3" aria-hidden="true" />
                  ) : (
                    <span className={cn(
                      'relative z-10 rounded-full px-1.5 py-0.5 text-[11px] font-semibold tabular-nums ring-1 ring-inset transition-colors',
                      isActive ? 'bg-white/20 text-white ring-white/20' : 'bg-[var(--surface-2)] text-tertiary ring-[var(--border-2)]',
                    )}>
                      {it.count}
                    </span>
                  )
                ) : null}
                {/* Settings gets a chevron that rotates when expanded */}
                {isSettings && !rail ? (
                  <IcChevDown
                    style={{ width: 14, height: 14 }}
                    className={cn('relative z-10 shrink-0 transition-transform duration-200', isActive ? 'rotate-180 text-white/80' : 'text-fg-quaternary')}
                  />
                ) : null}
              </button>

              {/* Nested sub-settings — expand when Settings is active. Hidden in
                  rail mode (no room); expanding the rail reveals them again. */}
              {isSettings && !rail ? (
                <AnimatePresence initial={false}>
                  {isActive ? (
                    <motion.div
                      initial={{ height: 0, opacity: 0 }}
                      animate={{ height: 'auto', opacity: 1 }}
                      exit={{ height: 0, opacity: 0 }}
                      transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
                      className="overflow-hidden"
                    >
                      <div className="relative ml-[22px] mt-1 flex flex-col gap-0.5 border-l border-[var(--border-2)] pl-3">
                        {settingsSub.flatMap(s => {
                          const subActive = settingsSection === s.key;
                          // A band label sits above the first item of each group.
                          // flatMap keeps both the label and the button as direct
                          // flex children, so buttons still stretch full width.
                          const out: React.ReactNode[] = [];
                          if (s.group) {
                            out.push(
                              <div
                                key={`grp-${s.key}`}
                                className="px-2.5 pb-1 pt-3 text-[10px] font-semibold uppercase tracking-[0.09em] text-fg-quaternary first:pt-1"
                              >
                                {s.group}
                              </div>,
                            );
                          }
                          out.push(
                            <button
                              key={s.key}
                              onClick={() => { setSettingsSection(s.key); onClose?.(); }}
                              className={cn(
                                'relative rounded-lg px-2.5 py-1.5 text-left text-[13px] transition duration-100 ease-linear',
                                subActive ? 'font-semibold text-secondary' : 'text-tertiary hover:text-secondary hover:bg-[var(--surface-hover)]',
                              )}
                            >
                              {subActive ? (
                                <motion.span
                                  layoutId="settings-sub-active"
                                  className="absolute -left-[13px] top-1/2 h-4 w-[2px] -translate-y-1/2 rounded-full"
                                  style={{ background: 'var(--brand-grad)' }}
                                  transition={{ type: 'spring', stiffness: 600, damping: 40 }}
                                />
                              ) : null}
                              {s.label}
                            </button>,
                          );
                          return out;
                        })}
                      </div>
                    </motion.div>
                  ) : null}
                </AnimatePresence>
              ) : null}
            </div>
          );
        })}
      </nav>

      {/* Status footer */}
      <div className={cn(
        'mt-auto rounded-xl border border-[var(--border-2)] bg-[var(--surface-1)] shadow-[var(--shadow-1)]',
        rail ? 'lg:grid lg:size-9 lg:place-items-center lg:self-center lg:p-0' : 'px-3 py-2.5',
      )}>
        <div className={cn('flex items-center gap-2 text-xs text-tertiary', rail && 'lg:gap-0')}>
          <span
            className={cn('size-[7px] shrink-0 rounded-full', statusLive && 'breathe')}
            style={{ background: statusColor, boxShadow: `0 0 0 3px ${statusColor}2e` }}
          />
          {!rail ? <span>{statusLabel}</span> : null}
          {/* Sign out — only meaningful when this tab holds credentials
              (i.e. the server has auth enabled and we're signed in). */}
          {!rail && hasStoredAuth() ? (
            <button
              type="button"
              onClick={() => clearStoredAuth()}
              className="ml-auto shrink-0 text-[11px] text-fg-quaternary transition-colors hover:text-fg-secondary"
              title="Sign out of this tab"
            >
              Sign out
            </button>
          ) : null}
        </div>
      </div>
    </aside>
  );
}

export function Topbar({ active, onScan, scanRunning, onShortcuts, searchQuery, onSearchChange, onMenuClick }: {
  active: Page;
  onScan: () => void;
  scanRunning: boolean;
  onShortcuts: () => void;
  searchQuery: string;
  onSearchChange: (q: string) => void;
  /** Opens the mobile nav drawer (hamburger). Hidden on lg+. */
  onMenuClick?: () => void;
}) {
  const titles: Record<Page, string[]> = {
    dashboard: ['Workspace', 'Dashboard'],
    review: ['Workspace', 'Review queue'],
    history: ['Workspace', 'History'],
    settings: ['Settings'],
  };
  const trail = titles[active];
  return (
    <header className="topbar-glass sticky top-0 z-30 flex h-[62px] items-center gap-3 border-b border-[var(--border-2)] px-4 lg:gap-4 lg:px-7">
      <button
        className="press grid size-9 shrink-0 place-items-center rounded-lg text-fg-quaternary transition hover:bg-[var(--surface-hover)] hover:text-fg-tertiary lg:hidden [&_svg]:size-5"
        title="Menu"
        aria-label="Open navigation"
        onClick={onMenuClick}
      >
        <IcMenu />
      </button>
      {/* Breadcrumb — the active leaf animates in (key changes per page). */}
      <div className="flex items-center text-[13px] text-tertiary">
        {trail.map((s, i) => (
          <span key={i} className="flex items-center">
            {i > 0 ? <span className="mx-2 text-quaternary">/</span> : null}
            {i === trail.length - 1
              ? <motion.b
                  key={s}
                  initial={{ opacity: 0, y: -4 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
                  className="font-semibold text-secondary"
                >{s}</motion.b>
              : s}
          </span>
        ))}
      </div>

      <div
        className="topbar-search ml-auto flex h-9 w-full max-w-sm items-center gap-2 rounded-lg border border-[var(--border-2)] bg-[var(--surface-1)] px-3 transition-[border-color,box-shadow,background] duration-200 ease-[var(--ease-out)] focus-within:border-accent-line focus-within:bg-[var(--surface-2)] focus-within:shadow-[var(--glow-accent)]"
        onClick={(e) => { (e.currentTarget.querySelector('input') as HTMLInputElement)?.focus(); }}
      >
        <IcSearch style={{ width: 14, height: 14 }} className="text-fg-quaternary transition-colors" />
        <input
          className="min-w-0 flex-1 border-0 bg-transparent text-[13px] text-primary outline-none placeholder:text-placeholder"
          placeholder="Search files, titles, paths…"
          value={searchQuery}
          onChange={e => onSearchChange(e.target.value)}
        />
        {searchQuery ? (
          <button
            className="press grid size-[22px] place-items-center rounded-md text-fg-quaternary transition hover:bg-[var(--surface-hover)] hover:text-fg-tertiary"
            title="Clear"
            aria-label="Clear search"
            onClick={() => onSearchChange('')}
          >
            <IcX style={{ width: 11, height: 11 }} />
          </button>
        ) : (
          <span className="rounded border border-[var(--border-2)] px-1.5 py-0.5 font-mono text-[10px] text-quaternary">/</span>
        )}
      </div>

      <button
        className="press grid size-9 shrink-0 place-items-center rounded-lg border border-[var(--border-2)] bg-[var(--surface-1)] text-fg-quaternary shadow-[var(--shadow-1)] transition hover:bg-[var(--surface-hover)] hover:text-fg-tertiary [&_svg]:size-[16px]"
        title="Keyboard shortcuts (?)"
        aria-label="Keyboard shortcuts"
        onClick={onShortcuts}
      >
        <IcKeyboard />
      </button>
      <NotificationsBell />
      <Button
        size="md"
        color="primary"
        iconLeading={scanRunning ? IcSpin : IcScan}
        isDisabled={scanRunning}
        onClick={onScan}
      >
        {scanRunning ? 'Scanning…' : 'Scan now'}
      </Button>
    </header>
  );
}

export function Toast({ toasts, onDismiss, leading }: { toasts: ToastData[]; onDismiss?: (id: string) => void; leading?: ReactNode }) {
  return (
    <div className="toasts">
      <AnimatePresence initial={false}>
        {leading ? (
          <motion.div
            key="__leading"
            layout
            initial={{ opacity: 0, y: 12, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, scale: 0.96, transition: { duration: 0.15 } }}
            transition={{ type: 'spring', stiffness: 400, damping: 32 }}
          >
            {leading}
          </motion.div>
        ) : null}
        {toasts.map(t => (
          <motion.div
            key={t.id}
            layout
            initial={{ opacity: 0, y: 12, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, scale: 0.96, transition: { duration: 0.15 } }}
            transition={{ type: 'spring', stiffness: 400, damping: 32 }}
            className="flex w-[340px] max-w-[90vw] items-start gap-3 rounded-xl border border-white/[0.1] bg-[rgba(8,9,12,0.66)] px-3.5 py-3 shadow-[0_12px_32px_rgba(0,0,0,0.5)] backdrop-blur-2xl"
          >
            <FeaturedIcon
              size="sm"
              color={t.kind === 'error' ? 'error' : 'success'}
              icon={t.kind === 'error' ? <IcAlertTri /> : <IcCheck />}
            />
            <div className="min-w-0 flex-1">
              <div className="text-[13px] font-semibold text-ink">{t.title}</div>
              {t.sub ? <div className="mt-0.5 text-[12px] leading-relaxed text-ink-muted">{t.sub}</div> : null}
            </div>
            {onDismiss ? (
              <button
                onClick={() => onDismiss(t.id)}
                aria-label="Dismiss"
                title="Dismiss"
                className="grid size-6 shrink-0 place-items-center rounded-md text-ink-soft transition-colors hover:bg-glass-2 hover:text-ink [&_svg]:size-[14px]"
              >
                <IcX />
              </button>
            ) : null}
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}

export function Checkbox({ on, onChange, indeterminate, disabled, title }: {
  on: boolean;
  onChange?: () => void;
  indeterminate?: boolean;
  disabled?: boolean;
  title?: string;
}) {
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={indeterminate ? 'mixed' : on}
      aria-disabled={disabled || undefined}
      disabled={disabled}
      title={title}
      className={`cb ${on || indeterminate ? 'on' : ''} ${disabled ? 'cb-disabled' : ''}`}
      style={{ padding: 0 }}
      onClick={(e) => { e.stopPropagation(); if (!disabled) onChange?.(); }}
    >
      {on ? <IcCheck /> : indeterminate ? (
        <svg viewBox="0 0 24 24" style={{ width: 12, height: 12, color: '#061814' }}>
          <rect x="5" y="11" width="14" height="2" rx="1" fill="currentColor" />
        </svg>
      ) : null}
    </button>
  );
}

export function Segmented({ options, value, onChange }: {
  options: { value: string; label: string }[];
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="seg">
      {options.map(opt => (
        <button key={opt.value} className={`seg-btn ${value === opt.value ? 'on' : ''}`} onClick={() => onChange(opt.value)}>
          {opt.label}
        </button>
      ))}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// <Select> — themed dropdown replacement for native <select>
//
// The native element's open list is OS-painted and ignores CSS, which
// shipped a blinding white-on-blue Windows dropdown in the middle of
// Kira's dark theme. This component renders a button styled exactly
// like our `.input` class, and on click pops a custom panel below it
// with the options rendered as buttons. Click-outside / Escape close
// the panel; Arrow keys + Enter navigate.
//
// Generic over value type so it works for both numeric ids (Sonarr
// quality profile id) and string paths (root folder). Stringifies
// internally for comparison via the caller-supplied keyFor (defaults
// to JSON.stringify which works for primitives).
// ─────────────────────────────────────────────────────────────────────
export function Select<T>({
  options,
  value,
  onChange,
  placeholder,
  className = '',
  style,
  buttonClassName = '',
  disabled = false,
  'aria-label': ariaLabel,
}: {
  options: { value: T; label: string; secondary?: string }[];
  value: T | null | undefined;
  onChange: (v: T) => void;
  placeholder?: string;
  /** Class for the OUTER wrapper (used for width / flex sizing). */
  className?: string;
  /** Inline style for the OUTER wrapper. Pass `{ flex: 1, minWidth: 0 }`
   *  to make the Select fill its row inside a flex layout — the
   *  default `display: block` keeps the popup width tracking the
   *  trigger width without needing this. */
  style?: React.CSSProperties;
  /** Class for the trigger BUTTON — accepts e.g. `mono` for monospaced
   *  paths like root-folder pickers. The base `.select-trigger` class
   *  is always applied. */
  buttonClassName?: string;
  disabled?: boolean;
  /** Accessible name for the trigger button — REQUIRED for any Select whose
   *  visible label is a sibling element (not a wired <label>), so a screen
   *  reader announces what the dropdown controls instead of just its value. */
  'aria-label'?: string;
}) {
  const [open, setOpen] = useState(false);
  // Hover/keyboard-focused index for arrow-key navigation. -1 = none.
  const [activeIdx, setActiveIdx] = useState<number>(-1);
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const popupRef = useRef<HTMLDivElement | null>(null);
  // The dropdown renders in a body-level PORTAL so it escapes any
  // `overflow-hidden` / stacking-context ancestor (e.g. the collapsible
  // ProviderCard, whose clip used to trap the dropdown "inside the pill").
  // We position it `fixed` from the trigger's rect, recomputed on open and on
  // any scroll/resize so it stays pinned to the trigger.
  const [popPos, setPopPos] = useState<{ top: number; left: number; width: number } | null>(null);
  const placePopup = () => {
    const el = wrapperRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    setPopPos({ top: r.bottom + 6, left: r.left, width: r.width });
  };
  useLayoutEffect(() => {
    if (!open) { setPopPos(null); return; }
    placePopup();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const reflow = () => placePopup();
    // capture-phase scroll catches scrolling in ANY ancestor, not just window
    window.addEventListener('scroll', reflow, true);
    window.addEventListener('resize', reflow);
    return () => {
      window.removeEventListener('scroll', reflow, true);
      window.removeEventListener('resize', reflow);
    };
  }, [open]);

  // Match an option by deep-equal-ish comparison. We JSON-stringify
  // both sides so primitive values, dicts, and lists all compare
  // sanely without needing a custom keyFor prop.
  const keyOf = (v: T | null | undefined): string => {
    if (v === null || v === undefined) return '';
    try { return JSON.stringify(v); } catch { return String(v); }
  };
  const selectedKey = keyOf(value);
  const selected = options.find(o => keyOf(o.value) === selectedKey) ?? null;

  // Click-outside + Escape close the popup. Bound on document so a
  // click ANYWHERE off the dropdown collapses it — matches every
  // other native-feeling dropdown the user has seen.
  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      const t = e.target as Node;
      // Exclude BOTH the trigger wrapper AND the portaled popup — the popup is
      // no longer a DOM descendant of the wrapper, so without this an option
      // click would count as "outside" and close before its onClick fires.
      if (wrapperRef.current?.contains(t) || popupRef.current?.contains(t)) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { setOpen(false); return; }
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setActiveIdx(i => Math.min((i < 0 ? -1 : i) + 1, options.length - 1));
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        setActiveIdx(i => Math.max(i - 1, 0));
      } else if (e.key === 'Enter' && activeIdx >= 0 && activeIdx < options.length) {
        e.preventDefault();
        onChange(options[activeIdx].value);
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [open, options, activeIdx, onChange]);

  // When opening, jump the highlight to the currently-selected entry
  // so Up/Down feels natural — pre-fix Arrow Down ALWAYS started at
  // the top regardless of the current selection.
  const handleOpen = () => {
    if (disabled) return;
    setActiveIdx(selected ? options.findIndex(o => keyOf(o.value) === selectedKey) : -1);
    setOpen(o => !o);
  };

  // Folder/path selects pass buttonClassName="mono" — render their value and
  // options in the monospace face so they match the app's path fields.
  const mono = buttonClassName.includes('mono');

  return (
    <div ref={wrapperRef} className={cn('relative', className)} style={style}>
      <button
        type="button"
        className={cn(
          'flex w-full items-center justify-between gap-2 rounded-xl border bg-glass px-3.5 py-2.5 text-[13px] text-ink outline-none transition-colors hover:bg-glass-2 disabled:cursor-not-allowed disabled:opacity-55',
          open ? 'border-accent-line bg-glass-2' : 'border-line',
        )}
        onClick={handleOpen}
        onKeyDown={(e) => {
          // Open with Arrow keys from the closed trigger (Enter/Space already
          // open via the native button click). Once open, the document-level
          // handler drives Up/Down/Enter navigation.
          if (disabled || open) return;
          if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault();
            setActiveIdx(selected ? options.findIndex(o => keyOf(o.value) === selectedKey) : 0);
            setOpen(true);
          }
        }}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={ariaLabel}
      >
        <span className={cn('flex-1 truncate text-left', mono && 'font-mono text-[12px]', !selected && 'text-ink-soft')}>
          {selected ? selected.label : (placeholder ?? '— select —')}
        </span>
        <IcChevDown className={cn('size-4 shrink-0 text-ink-soft transition-transform duration-200', open && 'rotate-180')} />
      </button>
      {open && popPos && createPortal(
        <div
          ref={popupRef}
          role="listbox"
          style={{ position: 'fixed', top: popPos.top, left: popPos.left, width: popPos.width }}
          className="z-[1000] max-h-[280px] overflow-y-auto rounded-xl border border-line bg-[#0e0f14] p-1 shadow-[0_12px_32px_rgba(0,0,0,0.5),0_2px_6px_rgba(0,0,0,0.4)] [scrollbar-width:thin]"
        >
          {options.length === 0 ? (
            <div className="px-2.5 py-3 text-center text-[12px] text-ink-soft">No options available.</div>
          ) : options.map((opt, idx) => {
            const isSelected = keyOf(opt.value) === selectedKey;
            const isActive = idx === activeIdx;
            return (
              <button
                key={`${idx}-${keyOf(opt.value)}`}
                type="button"
                role="option"
                aria-selected={isSelected}
                className={cn(
                  'flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[13px] transition-colors [&_svg]:size-4 [&_svg]:shrink-0',
                  isActive && 'bg-glass-2',
                  isSelected ? 'text-accent' : 'text-ink',
                )}
                onMouseEnter={() => setActiveIdx(idx)}
                onClick={() => { onChange(opt.value); setOpen(false); }}
              >
                <span className={cn('flex-1 truncate', mono && 'font-mono text-[12px]', isSelected && 'font-medium')}>{opt.label}</span>
                {opt.secondary ? (
                  <span className="shrink-0 text-[11px] text-ink-soft">{opt.secondary}</span>
                ) : null}
                {isSelected ? <IcCheck /> : null}
              </button>
            );
          })}
        </div>,
        document.body,
      )}
    </div>
  );
}

export function FilterPill({ on, onClick, label, num }: {
  on: boolean;
  onClick: () => void;
  label: ReactNode;
  num?: number;
}) {
  return (
    <button
      onClick={onClick}
      aria-pressed={on}
      className={cn(
        'inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[12.5px] font-medium outline-none transition-colors duration-[var(--dur-1)] ease-[var(--ease-out)]',
        'focus-visible:ring-2 focus-visible:ring-accent-line focus-visible:ring-offset-0',
        on
          ? 'bg-accent-soft text-ink shadow-[inset_0_0_0_1px_var(--accent-line)]'
          : 'text-ink-muted hover:bg-white/[0.05] hover:text-ink',
      )}
    >
      {label}
      {num != null ? (
        <span className={cn(
          'rounded-md px-1.5 py-0.5 text-[10px] font-semibold tabular-nums',
          on ? 'bg-accent-line text-ink' : 'bg-white/[0.06] text-ink-soft',
        )}>{num}</span>
      ) : null}
    </button>
  );
}

// Wraps a set of FilterPills into a tidy segmented group (subtle inset bar).
// Each group reads as one cohesive control so the three filter dimensions
// (status / confidence / media) stay visually separated from each other.
export function FilterGroup({ children }: { children: ReactNode }) {
  return (
    <div className="inline-flex flex-wrap items-center gap-0.5 rounded-xl border border-line bg-white/[0.025] p-1 shadow-[inset_0_1px_0_rgba(255,255,255,0.03)]">
      {children}
    </div>
  );
}

/**
 * Inline shimmering placeholder. Use anywhere a value is loading and
 * `0` / `undefined` would be misleading — stat numbers, list rows,
 * cover art. Renders a pulsing rounded block at the given width/height
 * with the same baseline as adjacent text so the layout doesn't shift
 * when the real value lands.
 *
 * Usage:
 *   {isLoading ? <Skeleton w={80} h={32} /> : <h2>{count}</h2>}
 *
 * Sizes are passed as numbers (px) or any valid CSS length. Defaults
 * are sized for one line of body text.
 */
export function Skeleton({
  w = '100%',
  h = 14,
  radius = 6,
  style,
  className = '',
}: {
  w?: number | string;
  h?: number | string;
  radius?: number | string;
  style?: React.CSSProperties;
  className?: string;
}) {
  return (
    <span
      className={`kira-skeleton ${className}`}
      aria-busy="true"
      aria-hidden="true"
      style={{
        display: 'inline-block',
        width: typeof w === 'number' ? `${w}px` : w,
        height: typeof h === 'number' ? `${h}px` : h,
        borderRadius: typeof radius === 'number' ? `${radius}px` : radius,
        verticalAlign: 'middle',
        ...style,
      }}
    />
  );
}

export function EmptyState({ icon, title, sub, action }: {
  icon: ReactNode;
  title: string;
  sub: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-icon">{icon}</div>
      <div>
        <div className="empty-title">{title}</div>
        <div className="empty-sub">{sub}</div>
      </div>
      {action}
    </div>
  );
}

export function Modal({ title, sub, onClose, children, footer, size }: {
  title: string;
  sub?: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  size?: string;
}) {
  const titleId = useId();
  const modalRef = useRef<HTMLDivElement | null>(null);

  // Escape to close + Tab focus-trap + focus-in/restore. Without this the
  // base modal was weaker for AT users than the bespoke CoverPopup dialog:
  // focus could escape behind the overlay and never returned to the opener.
  useEffect(() => {
    const prevFocused = document.activeElement as HTMLElement | null;
    const node = modalRef.current;
    node?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { onClose(); return; }
      if (e.key !== 'Tab' || !node) return;
      const focusables = node.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      );
      if (focusables.length === 0) { e.preventDefault(); return; }
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
      // Restore focus to whatever opened the modal (a row/card/button).
      prevFocused?.focus?.();
    };
  }, [onClose]);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        ref={modalRef}
        className={`modal ${size ? 'size-' + size : ''}`}
        onClick={e => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
      >
        <div className="modal-head">
          <div>
            <div className="modal-title" id={titleId}>{title}</div>
            {sub ? <div className="modal-sub">{sub}</div> : null}
          </div>
          <button className="close-x" onClick={onClose} title="Close (Esc)" aria-label="Close"><IcX /></button>
        </div>
        <div className="modal-body">{children}</div>
        {footer ? <div className="modal-foot">{footer}</div> : null}
      </div>
    </div>
  );
}
