import { useEffect, useState, type ReactNode } from 'react';
import type { PosterData, ToastData, Page, MediaType } from '../lib/types';
import {
  IcDashboard, IcReview, IcHistory, IcSettings, IcSearch,
  IcCheck, IcX, IcAlertTri, IcKeyboard, IcScan, IcSpin,
  IcLogoMark, IcFilm, IcTv, IcAnime, IcMusic,
} from '../lib/icons';
import { NotificationsBell } from './NotificationsBell';

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

export function Sidebar({ active, setActive, pendingCount, scanRunning, backendOk }: {
  active: Page;
  setActive: (p: Page) => void;
  pendingCount: number;
  scanRunning: boolean;
  backendOk: boolean | null;
}) {
  const items: { key: Page; label: string; icon: ReactNode; count?: number }[] = [
    { key: 'dashboard', label: 'Dashboard', icon: <IcDashboard /> },
    { key: 'review', label: 'Review', icon: <IcReview />, count: pendingCount },
    { key: 'history', label: 'History', icon: <IcHistory /> },
    { key: 'settings', label: 'Settings', icon: <IcSettings /> },
  ];
  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark"><IcLogoMark /></div>
        <div>
          <div className="brand-name">Kira</div>
          <div className="brand-tag">v0.4.1</div>
        </div>
      </div>

      <nav className="nav">
        <div className="nav-section-label">Workspace</div>
        {items.map(it => (
          <button key={it.key} className={`nav-item ${active === it.key ? 'active' : ''}`} onClick={() => setActive(it.key)}>
            {it.icon}
            <span>{it.label}</span>
            {it.count != null && it.count > 0 ? <span className="count">{it.count}</span> : null}
          </button>
        ))}
      </nav>

      <div className="sidebar-footer">
        <div className="row">
          <span className="dot" style={{
            background: backendOk === false ? 'var(--conf-low)'
                       : scanRunning ? 'var(--conf-mid)'
                       : 'var(--conf-high)',
            boxShadow: backendOk === false ? '0 0 0 3px rgba(255,91,110,0.18)'
                       : scanRunning ? '0 0 0 3px rgba(255,201,74,0.18)'
                       : '0 0 0 3px rgba(40,217,160,0.18)',
          }} />
          <span style={{ color: 'var(--ink-2)' }}>
            {backendOk === false ? 'Backend disconnected'
             : scanRunning ? 'Scanning...'
             : backendOk === null ? 'Connecting...'
             : 'Idle'}
          </span>
        </div>
      </div>
    </aside>
  );
}

export function Topbar({ active, onScan, scanRunning, onShortcuts, searchQuery, onSearchChange }: {
  active: Page;
  onScan: () => void;
  scanRunning: boolean;
  onShortcuts: () => void;
  searchQuery: string;
  onSearchChange: (q: string) => void;
}) {
  const titles: Record<Page, string[]> = {
    dashboard: ['Workspace', 'Dashboard'],
    review: ['Workspace', 'Review queue'],
    history: ['Workspace', 'History'],
    settings: ['Settings'],
  };
  const trail = titles[active];
  return (
    <header className="topbar">
      <div className="crumb">
        {trail.map((s, i) => (
          <span key={i}>
            {i > 0 ? <span style={{ margin: '0 8px', color: 'var(--ink-4)' }}>/</span> : null}
            {i === trail.length - 1 ? <b>{s}</b> : s}
          </span>
        ))}
      </div>

      <div className="search" onClick={(e) => { (e.currentTarget.querySelector('input') as HTMLInputElement)?.focus(); }}>
        <IcSearch style={{ width: 14, height: 14, color: 'var(--ink-3)' }} />
        <input
          placeholder="Search files, titles, paths..."
          value={searchQuery}
          onChange={e => onSearchChange(e.target.value)}
        />
        {searchQuery ? (
          <button
            className="icon-btn"
            title="Clear"
            style={{ width: 22, height: 22, padding: 0 }}
            onClick={() => onSearchChange('')}
          >
            <IcX style={{ width: 11, height: 11 }} />
          </button>
        ) : (
          <span className="kbd">/</span>
        )}
      </div>

      <button className="icon-btn" title="Keyboard shortcuts (?)" onClick={onShortcuts}><IcKeyboard /></button>
      <NotificationsBell />
      <button
        className={scanRunning ? 'btn' : 'btn btn-primary'}
        // Symmetric with the DashboardPage Quick-actions button: HTML
        // `disabled` swallows clicks entirely, so if scanRunning is stuck
        // true (previous scan crashed without resetting state), the user
        // can't even see the "Scan already in progress" toast that
        // explains the situation. Use aria-disabled + style for the
        // visual + a11y, let onScan handle the early-return through its
        // own scanRunning check.
        aria-disabled={scanRunning}
        onClick={onScan}
        style={{
          opacity: scanRunning ? 0.6 : undefined,
          cursor: scanRunning ? 'not-allowed' : undefined,
        }}
      >
        {scanRunning ? <IcSpin /> : <IcScan />}
        {scanRunning ? 'Scanning...' : 'Scan now'}
      </button>
    </header>
  );
}

export function Toast({ toasts, onDismiss }: { toasts: ToastData[]; onDismiss?: (id: string) => void }) {
  return (
    <div className="toasts">
      {toasts.map(t => (
        <div key={t.id} className={`toast ${t.kind || ''}`}>
          <div className="toast-icon">{t.kind === 'error' ? <IcAlertTri /> : <IcCheck />}</div>
          <div className="toast-body">
            <div className="toast-title">{t.title}</div>
            {t.sub ? <div className="toast-sub">{t.sub}</div> : null}
          </div>
          {onDismiss ? (
            <button
              className="toast-dismiss"
              onClick={() => onDismiss(t.id)}
              aria-label="Dismiss"
              title="Dismiss"
              style={{
                appearance: 'none', background: 'transparent', border: 0,
                color: 'var(--ink-3)', cursor: 'pointer',
                padding: 6, marginLeft: 4, alignSelf: 'flex-start',
                lineHeight: 0,
              }}
            >
              <IcX />
            </button>
          ) : null}
        </div>
      ))}
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

export function FilterPill({ on, onClick, label, num }: {
  on: boolean;
  onClick: () => void;
  label: ReactNode;
  num?: number;
}) {
  return (
    <button className={`filter-pill ${on ? 'on' : ''}`} onClick={onClick}>
      {label}
      {num != null ? <span className="num">{num}</span> : null}
    </button>
  );
}

/**
 * Generic page-level loader. Pages render this in place of their main
 * content while their initial data fetch is in flight, so the user
 * never sees the empty-state UI flash on refresh.
 *
 * Just a CSS-driven ring spinner + label. No props except a custom label
 * (defaults to "Loading…"). Styled by .lib-loading / .lib-loading-spinner
 * in index.css.
 *
 * NOTE: prefer `Skeleton` for individual values inside an otherwise
 * laid-out page — this loader blocks the entire region. Use it only
 * when there's literally nothing meaningful to render until the fetch
 * lands.
 */
export function PageLoader({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="lib-loading" aria-busy="true" role="status" aria-label={label}>
      <div className="lib-loading-spinner" />
      <div className="lib-loading-label">{label}</div>
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
      <div className={`modal ${size ? 'size-' + size : ''}`} onClick={e => e.stopPropagation()}>
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
