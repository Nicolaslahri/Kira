import { useEffect, useRef, useState, type ReactNode } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import { api, ApiError, type ApiNotification } from '../lib/api';
import { IcBell, IcCheck, IcAlertTri } from '../lib/icons';
import { cn } from '../lib/utils';
import { FeaturedIcon } from './base/featured-icons/featured-icon';
import { Button } from './base/buttons/button';
import { ButtonUtility } from './base/buttons/button-utility';
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

export function NotificationsBell({ placement = 'down-right' }: { placement?: 'down-right' | 'up-left' } = {}) {
  // Panel opens downward-right beneath the topbar bell, or upward-left when the
  // bell lives at the bottom of the (narrow) sidebar — there it escapes to the
  // right over the content (sidebar drops overflow-hidden for this).
  const up = placement === 'up-left';
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
      {/* Bell = UUI ButtonUtility + an overlaid count badge (their notifications
          pattern). The icon nudges on hover and the ring turns emerald when
          unread — Kira touches layered on via className. */}
      <span className="relative inline-flex">
        <ButtonUtility
          ref={triggerRef}
          size="md"
          color="secondary"
          icon={IcBell}
          tooltip={unread > 0 ? `Notifications (${unread} unread)` : 'Notifications'}
          aria-haspopup="dialog"
          aria-expanded={open}
          aria-controls="notifications-panel"
          onClick={() => setOpen(o => !o)}
          className={cn(
            '[&_[data-icon]]:transition-transform [&_[data-icon]]:duration-300 hover:[&_[data-icon]]:-rotate-12',
            unread > 0 && 'ring-[var(--accent)]',
          )}
        />
        <AnimatePresence>
          {unread > 0 ? (
            <motion.span
              key="unread-badge"
              aria-hidden="true"
              initial={{ scale: 0, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0, opacity: 0 }}
              transition={{ type: 'spring', stiffness: 600, damping: 22 }}
              className="pointer-events-none absolute -right-1 -top-1 grid h-4 min-w-4 place-items-center rounded-full bg-[var(--color-fg-error-primary)] px-1 text-[9px] font-bold leading-none text-white shadow-[0_0_0_3px_var(--conf-low-24)]"
            >
              {unread > 99 ? '99+' : unread}
            </motion.span>
          ) : null}
        </AnimatePresence>
      </span>

      <AnimatePresence>
      {open ? (
        <motion.div
          ref={panelRef}
          id="notifications-panel"
          role="dialog"
          aria-label="Notifications"
          tabIndex={-1}
          initial={{ opacity: 0, y: up ? 8 : -8, scale: 0.97 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, y: up ? 6 : -6, scale: 0.98 }}
          transition={{ duration: 0.18, ease: [0.22, 1, 0.36, 1] }}
          style={{ transformOrigin: up ? 'bottom left' : 'top right' }}
          className={cn(
            'absolute z-50 w-[360px] max-w-[calc(100vw-2rem)] overflow-hidden rounded-2xl border border-[var(--border-3)] bg-[var(--panel-90)] shadow-[var(--shadow-3)] backdrop-blur-xl outline-none',
            up ? 'bottom-[calc(100%+8px)] left-0' : 'right-0 top-[calc(100%+8px)]',
          )}
        >
          <div className="flex items-center justify-between border-b border-white/[0.1] px-4 py-3">
            <div className="text-[13px] font-semibold text-primary">Notifications</div>
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
              <div className="text-[13px] text-secondary">All caught up — no notifications.</div>
            </div>
          ) : (
            <div className="max-h-[420px] overflow-y-auto [scrollbar-width:thin]">
              {items.map(n => (
                <button
                  key={n.id}
                  className={cn(
                    'flex w-full items-start gap-3 border-b border-white/[0.06] px-4 py-3 text-left transition-colors last:border-0 hover:bg-primary_hover',
                    !n.read && 'bg-[var(--accent-soft)]',
                  )}
                  onClick={async () => {
                    if (!n.read) { try { await api.markNotificationRead(n.id); void refresh(); } catch { /* */ } }
                  }}
                >
                  <FeaturedIcon size="sm" color={kindColor(n.kind)} icon={kindIcon(n.kind)} />
                  <div className="min-w-0 flex-1">
                    <div className="text-[13px] font-medium text-primary">{n.title}</div>
                    {/* whitespace-pre-line: multi-line bodies (e.g. the subtitle
                        summary's bulleted "To fix" list) keep their line breaks. */}
                    {n.body ? <div className="mt-0.5 whitespace-pre-line text-[12px] leading-relaxed text-secondary">{n.body}</div> : null}
                    <div className="mt-1 text-[11px] text-tertiary">{relativeTime(n.created_at)}</div>
                  </div>
                  {!n.read ? <span className="mt-1.5 size-2 shrink-0 rounded-full bg-[var(--color-fg-brand-primary)]" /> : null}
                </button>
              ))}
            </div>
          )}
        </motion.div>
      ) : null}
      </AnimatePresence>
    </div>
  );
}
