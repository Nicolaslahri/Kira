import { useEffect, useRef, useState, type ReactNode } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import type { PosterData, ToastData, Page, MediaType } from '../lib/types';
import type { SettingsSection } from '../App';
import {
  IcDashboard, IcReview, IcHistory, IcSettings, IcSearch,
  IcCheck, IcX, IcAlertTri, IcKeyboard, IcScan, IcSpin,
  IcLogoMark, IcFilm, IcTv, IcAnime, IcMusic, IcChevDown, IcMenu,
} from '../lib/icons';
import { NotificationsBell } from './NotificationsBell';
import { cn } from '../lib/utils';
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
  const level = value >= 85 ? 'high' : value >= 50 ? 'mid' : 'low';
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
  const settingsSub: { key: SettingsSection; label: string }[] = [
    { key: 'connections', label: 'Connections' },
    { key: 'paths', label: 'Paths & folders' },
    { key: 'integrations', label: 'Integrations' },
    { key: 'naming', label: 'Naming & format' },
    { key: 'cleanup', label: 'Folder cleanup' },
    { key: 'confidence', label: 'Confidence' },
    { key: 'advanced', label: 'Advanced' },
  ];
  const statusColor = backendOk === false ? 'var(--conf-low)'
    : scanRunning ? 'var(--conf-mid)' : 'var(--conf-high)';
  const statusLabel = backendOk === false ? 'Backend disconnected'
    : scanRunning ? 'Scanning…' : backendOk === null ? 'Connecting…' : 'Idle';

  return (
    <aside className={cn(
      'fixed inset-y-0 left-0 z-50 flex h-screen w-[var(--side-w)] flex-col gap-8 border-r border-secondary bg-secondary px-4 py-6 transition-transform duration-300 ease-out lg:sticky lg:top-0 lg:z-20 lg:translate-x-0',
      mobileOpen ? 'translate-x-0' : '-translate-x-full',
    )}>
      {/* Brand */}
      <div className="flex items-center gap-3 px-2">
        <div className="grid size-9 place-items-center rounded-xl bg-brand-solid text-white [&_svg]:size-5">
          <IcLogoMark />
        </div>
        <div className="leading-tight">
          <div className="text-[15px] font-bold tracking-tight text-primary">Kira</div>
          <div className="text-[11px] text-quaternary">v0.5.0</div>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex flex-col gap-1">
        <div className="px-3 pb-1.5 pt-3 text-[10px] font-semibold uppercase tracking-[0.08em] text-quaternary">
          Workspace
        </div>
        {items.map(it => {
          const isActive = active === it.key;
          const isSettings = it.key === 'settings';
          return (
            <div key={it.key}>
              <button
                onClick={() => { setActive(it.key); if (it.key !== 'settings') onClose?.(); }}
                className={cn(
                  'group relative flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-semibold transition duration-100 ease-linear',
                  isActive ? 'bg-active text-secondary' : 'text-tertiary hover:bg-primary_hover hover:text-secondary',
                )}
              >
                <span className={cn('inline-flex size-5 shrink-0 [&_svg]:size-5', isActive ? 'text-fg-brand-primary' : 'text-fg-quaternary')}>
                  {it.icon}
                </span>
                <span className="flex-1 text-left">{it.label}</span>
                {it.count != null && it.count > 0 ? (
                  <span className="rounded-full bg-primary px-1.5 py-0.5 text-[11px] font-semibold tabular-nums text-tertiary ring-1 ring-secondary ring-inset">
                    {it.count}
                  </span>
                ) : null}
                {/* Settings gets a chevron that rotates when expanded */}
                {isSettings ? (
                  <IcChevDown
                    style={{ width: 14, height: 14 }}
                    className={cn('shrink-0 transition-transform duration-200', isActive ? 'rotate-180 text-fg-tertiary' : 'text-fg-quaternary')}
                  />
                ) : null}
              </button>

              {/* Nested sub-settings — expand when Settings is active */}
              {isSettings ? (
                <AnimatePresence initial={false}>
                  {isActive ? (
                    <motion.div
                      initial={{ height: 0, opacity: 0 }}
                      animate={{ height: 'auto', opacity: 1 }}
                      exit={{ height: 0, opacity: 0 }}
                      transition={{ duration: 0.22, ease: 'easeOut' }}
                      className="overflow-hidden"
                    >
                      <div className="relative ml-[22px] mt-1 flex flex-col gap-0.5 border-l border-secondary pl-3">
                        {settingsSub.map(s => {
                          const subActive = settingsSection === s.key;
                          return (
                            <button
                              key={s.key}
                              onClick={() => { setSettingsSection(s.key); onClose?.(); }}
                              className={cn(
                                'relative rounded-lg px-2.5 py-1.5 text-left text-[13px] transition duration-100 ease-linear',
                                subActive ? 'font-semibold text-secondary' : 'text-tertiary hover:text-secondary',
                              )}
                            >
                              {subActive ? (
                                <span className="absolute -left-[13px] top-1/2 h-4 w-[2px] -translate-y-1/2 rounded-full bg-brand-solid" />
                              ) : null}
                              {s.label}
                            </button>
                          );
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
      <div className="mt-auto rounded-xl border border-secondary bg-primary px-3 py-2.5">
        <div className="flex items-center gap-2 text-xs text-tertiary">
          <span className="size-[7px] shrink-0 rounded-full" style={{ background: statusColor, boxShadow: `0 0 0 3px ${statusColor}2e` }} />
          <span>{statusLabel}</span>
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
    <header className="sticky top-0 z-30 flex h-[60px] items-center gap-3 border-b border-secondary bg-primary/80 px-4 backdrop-blur-xl lg:gap-4 lg:px-7">
      <button
        className="grid size-9 shrink-0 place-items-center rounded-lg text-fg-quaternary transition hover:bg-primary_hover hover:text-fg-tertiary lg:hidden [&_svg]:size-5"
        title="Menu"
        aria-label="Open navigation"
        onClick={onMenuClick}
      >
        <IcMenu />
      </button>
      <div className="flex items-center text-[13px] text-tertiary">
        {trail.map((s, i) => (
          <span key={i} className="flex items-center">
            {i > 0 ? <span className="mx-2 text-quaternary">/</span> : null}
            {i === trail.length - 1 ? <b className="font-semibold text-secondary">{s}</b> : s}
          </span>
        ))}
      </div>

      <div
        className="ml-auto flex h-9 w-full max-w-sm items-center gap-2 rounded-lg border border-primary bg-primary px-3 shadow-xs transition focus-within:ring-2 focus-within:ring-brand"
        onClick={(e) => { (e.currentTarget.querySelector('input') as HTMLInputElement)?.focus(); }}
      >
        <IcSearch style={{ width: 14, height: 14 }} className="text-fg-quaternary" />
        <input
          className="min-w-0 flex-1 border-0 bg-transparent text-[13px] text-primary outline-none placeholder:text-placeholder"
          placeholder="Search files, titles, paths…"
          value={searchQuery}
          onChange={e => onSearchChange(e.target.value)}
        />
        {searchQuery ? (
          <button
            className="grid size-[22px] place-items-center rounded-md text-fg-quaternary transition hover:bg-primary_hover hover:text-fg-tertiary"
            title="Clear"
            onClick={() => onSearchChange('')}
          >
            <IcX style={{ width: 11, height: 11 }} />
          </button>
        ) : (
          <span className="rounded border border-secondary px-1.5 py-0.5 font-mono text-[10px] text-quaternary">/</span>
        )}
      </div>

      <button
        className="grid size-9 shrink-0 place-items-center rounded-lg border border-primary bg-primary text-fg-quaternary shadow-xs transition hover:bg-primary_hover hover:text-fg-tertiary [&_svg]:size-[16px]"
        title="Keyboard shortcuts (?)"
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

export function Checkbox({ on, onChange, indeterminate }: {
  on: boolean;
  onChange?: () => void;
  indeterminate?: boolean;
}) {
  return (
    <div className={`cb ${on || indeterminate ? 'on' : ''}`} onClick={(e) => { e.stopPropagation(); onChange?.(); }}>
      {on ? <IcCheck /> : indeterminate ? (
        <svg viewBox="0 0 24 24" style={{ width: 12, height: 12, color: '#061814' }}>
          <rect x="5" y="11" width="14" height="2" rx="1" fill="currentColor" />
        </svg>
      ) : null}
    </div>
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
}) {
  const [open, setOpen] = useState(false);
  // Hover/keyboard-focused index for arrow-key navigation. -1 = none.
  const [activeIdx, setActiveIdx] = useState<number>(-1);
  const wrapperRef = useRef<HTMLDivElement | null>(null);

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
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
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
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <span className={cn('flex-1 truncate text-left', mono && 'font-mono text-[12px]', !selected && 'text-ink-soft')}>
          {selected ? selected.label : (placeholder ?? '— select —')}
        </span>
        <IcChevDown className={cn('size-4 shrink-0 text-ink-soft transition-transform duration-200', open && 'rotate-180')} />
      </button>
      {open && (
        <div
          role="listbox"
          className="absolute left-0 right-0 top-[calc(100%+6px)] z-[1000] max-h-[280px] overflow-y-auto rounded-xl border border-line bg-[#0e0f14] p-1 shadow-[0_12px_32px_rgba(0,0,0,0.5),0_2px_6px_rgba(0,0,0,0.4)] [scrollbar-width:thin]"
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
        </div>
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
      className={cn(
        'inline-flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-[12.5px] font-medium transition-colors',
        on
          ? 'bg-white/[0.1] text-ink shadow-[0_1px_2px_rgba(0,0,0,0.3)]'
          : 'text-ink-muted hover:bg-white/[0.05] hover:text-ink',
      )}
    >
      {label}
      {num != null ? (
        <span className={cn(
          'rounded-md px-1.5 py-0.5 text-[10px] font-semibold tabular-nums',
          on ? 'bg-white/[0.16] text-ink' : 'bg-white/[0.06] text-ink-soft',
        )}>{num}</span>
      ) : null}
    </button>
  );
}

// Wraps a set of FilterPills into a tidy segmented group (subtle inset bar).
export function FilterGroup({ children }: { children: ReactNode }) {
  return (
    <div className="inline-flex flex-wrap items-center gap-1 rounded-xl border border-line bg-white/[0.025] p-1">
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
  useEffect(() => {
    const fn = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', fn);
    return () => window.removeEventListener('keydown', fn);
  }, [onClose]);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className={`modal ${size ? 'size-' + size : ''}`}
        onClick={e => e.stopPropagation()}
      >
        <div className="modal-head">
          <div>
            <div className="modal-title">{title}</div>
            {sub ? <div className="modal-sub">{sub}</div> : null}
          </div>
          <button className="close-x" onClick={onClose} title="Close (Esc)"><IcX /></button>
        </div>
        <div className="modal-body">{children}</div>
        {footer ? <div className="modal-foot">{footer}</div> : null}
      </div>
    </div>
  );
}
