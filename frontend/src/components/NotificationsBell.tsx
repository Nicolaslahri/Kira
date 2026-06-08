import { useEffect, useRef, useState, type ReactNode } from 'react';
import { api, ApiError, type ApiNotification } from '../lib/api';
import { IcBell, IcCheck, IcAlertTri } from '../lib/icons';
import { cn } from '../lib/utils';
import { FeaturedIcon } from './base/featured-icons/featured-icon';
import { Button } from './base/buttons/button';
import { Alert } from './base/alert/alert';

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 60_000) return `${Math.max(1, Math.floor(diff / 1000))}s ago`;
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} min ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} hr ago`;
  return new Date(iso).toLocaleDateString();
}

// Map a notification kind to a FeaturedIcon color + glyph. Success/error/warning
// keep their semantic colors; anything else is neutral.
function kindColor(kind: string): 'success' | 'error' | 'warning' | 'gray' {
  if (kind === 'success') return 'success';
  if (kind === 'error') return 'error';
  if (kind === 'warning') return 'warning';
  return 'gray';
}
function kindIcon(kind: string): ReactNode {
  if (kind === 'success') return <IcCheck />;
  if (kind === 'error' || kind === 'warning') return <IcAlertTri />;
  return <IcBell />;
}

export function NotificationsBell() {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<ApiNotification[]>([]);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  // M11: surface fetch errors once per session so a silent broken bell
  // doesn't hide the fact that the backend is unreachable.
  const errorSurfacedRef = useRef(false);

  const refresh = async () => {
    try {
      const next = await api.listNotifications();
      setItems(next);
      if (fetchError) {
        setFetchError(null);
        errorSurfacedRef.current = false;
      }
    } catch (e) {
      // An ApiError means the backend ANSWERED (with a non-2xx) — it's
      // reachable, just erroring on this endpoint. Only a raw network failure
      // means "backend unreachable", so don't cry offline on a 500.
      if (e instanceof ApiError) return;
      const msg = (e as Error).message || 'fetch failed';
      if (!errorSurfacedRef.current) {
        setFetchError(msg);
        errorSurfacedRef.current = true;
      }
    }
  };

  // Poll every 15 seconds for new notifications. Cheap; could move to WS later.
  useEffect(() => {
    void refresh();
    const t = setInterval(() => void refresh(), 15_000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Close on outside click or Escape; move focus into the panel on open and
  // restore it to the bell on close so keyboard users aren't stranded.
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    // Defer so the panel is mounted before we move focus into it.
    const t = setTimeout(() => panelRef.current?.focus(), 0);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
      clearTimeout(t);
    };
  }, [open]);

  const unread = items.filter(i => !i.read).length;

  const markAllRead = async () => {
    try {
      await api.markAllNotificationsRead();
      void refresh();
    } catch { /* swallow */ }
  };

  return (
    <div ref={wrapRef} className="relative">
      <button
        ref={triggerRef}
        className="relative grid size-9 shrink-0 place-items-center rounded-lg border border-primary bg-primary text-fg-quaternary shadow-xs transition hover:bg-primary_hover hover:text-fg-tertiary [&_svg]:size-[16px]"
        title={unread > 0 ? `Notifications (${unread} unread)` : 'Notifications'}
        aria-label={unread > 0 ? `Notifications, ${unread} unread` : 'Notifications'}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls="notifications-panel"
        onClick={() => setOpen(o => !o)}
      >
        <IcBell />
        {unread > 0 ? (
          <span
            aria-hidden="true"
            className="absolute -right-1 -top-1 grid h-4 min-w-4 place-items-center rounded-full bg-conf-low px-1 text-[9px] font-bold leading-none text-white"
          >
            {unread > 99 ? '99+' : unread}
          </span>
        ) : null}
      </button>

      {open ? (
        <div
          ref={panelRef}
          id="notifications-panel"
          role="dialog"
          aria-label="Notifications"
          tabIndex={-1}
          className="absolute right-0 top-[calc(100%+8px)] z-50 w-[360px] overflow-hidden rounded-2xl border border-white/[0.12] bg-[rgba(20,19,28,0.96)] shadow-[0_18px_60px_rgba(0,0,0,0.55)] backdrop-blur-xl outline-none"
        >
          <div className="flex items-center justify-between border-b border-white/[0.1] px-4 py-3">
            <div className="text-[13px] font-semibold text-ink">Notifications</div>
            {items.some(i => !i.read) ? (
              <Button color="link-gray" size="sm" className="text-xs" onClick={() => void markAllRead()}>Mark all read</Button>
            ) : null}
          </div>

          {fetchError ? (
            <Alert color="error" icon={IcAlertTri} className="m-3">Notifications offline — backend unreachable.</Alert>
          ) : null}

          {items.length === 0 ? (
            <div className="flex flex-col items-center gap-2.5 px-6 py-10 text-center">
              <FeaturedIcon size="md" color="gray" icon={<IcBell />} />
              <div className="text-[13px] text-ink-muted">All caught up — no notifications.</div>
            </div>
          ) : (
            <div className="max-h-[420px] overflow-y-auto [scrollbar-width:thin]">
              {items.map(n => (
                <button
                  key={n.id}
                  className={cn(
                    'flex w-full items-start gap-3 border-b border-white/[0.06] px-4 py-3 text-left transition-colors last:border-0 hover:bg-glass',
                    !n.read && 'bg-[var(--accent-soft)]',
                  )}
                  onClick={async () => {
                    if (!n.read) { try { await api.markNotificationRead(n.id); void refresh(); } catch { /* */ } }
                  }}
                >
                  <FeaturedIcon size="sm" color={kindColor(n.kind)} icon={kindIcon(n.kind)} />
                  <div className="min-w-0 flex-1">
                    <div className="text-[13px] font-medium text-ink">{n.title}</div>
                    {n.body ? <div className="mt-0.5 text-[12px] leading-relaxed text-ink-muted">{n.body}</div> : null}
                    <div className="mt-1 text-[11px] text-ink-faint">{relativeTime(n.created_at)}</div>
                  </div>
                  {!n.read ? <span className="mt-1.5 size-2 shrink-0 rounded-full bg-accent" /> : null}
                </button>
              ))}
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}
