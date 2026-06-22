import { Fragment, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
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

// Day bucketing — Today / Yesterday / weekday / date (local copy of the History
// page's helper so the notification list groups by day like the rename ledger).
function dayLabel(iso: string): string {
  const d = new Date(iso);
  const now = new Date();
  const startOf = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const dayDiff = Math.round((startOf(now) - startOf(d)) / 86_400_000);
  if (dayDiff <= 0) return 'Today';
  if (dayDiff === 1) return 'Yesterday';
  if (dayDiff < 7) return d.toLocaleDateString([], { weekday: 'long' });
  return d.toLocaleDateString([], { month: 'short', day: 'numeric', year: now.getFullYear() === d.getFullYear() ? undefined : 'numeric' });
}

// Collapse CONSECUTIVE runs of the same title+kind into one ×N group so a busy
// list (e.g. four identical "Auto-heal" events in a row) reads as a single line.
// Render-only: no API change, no reordering (items arrive newest-first).
interface NotifGroup {
  rep: ApiNotification;        // newest member of the run (drives title/body/time)
  members: ApiNotification[];  // every member — so a click marks them all read
  count: number;
  anyUnread: boolean;
  latest: string;              // rep.created_at
}
function groupNotifications(items: ApiNotification[]): NotifGroup[] {
  const groups: NotifGroup[] = [];
  for (const n of items) {
    const head = groups[groups.length - 1];
    if (head && head.rep.title === n.title && head.rep.kind === n.kind) {
      head.members.push(n);
      head.count += 1;
      if (!n.read) head.anyUnread = true;
    } else {
      groups.push({ rep: n, members: [n], count: 1, anyUnread: !n.read, latest: n.created_at });
    }
  }
  return groups;
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
  // Collapse duplicate runs + memoise so the 15s poll only re-buckets on change.
  const groups = useMemo(() => groupNotifications(items), [items]);

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
              className="pointer-events-none absolute -right-1 -top-1 grid h-4 min-w-4 place-items-center rounded-full bg-[var(--accent)] px-1 text-[9px] font-bold leading-none text-white tabular-nums shadow-[0_0_0_3px_var(--panel-90)]"
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
            'absolute z-50 w-[360px] max-w-[calc(100vw-2rem)] overflow-hidden rounded-2xl bg-[var(--panel-90)] ring-1 ring-inset ring-secondary shadow-[var(--shadow-3)] backdrop-blur-xl outline-none',
            up ? 'bottom-[calc(100%+8px)] left-0' : 'right-0 top-[calc(100%+8px)]',
          )}
        >
          <div className="flex items-center justify-between gap-2 px-4 py-3 shadow-[inset_0_-1px_0_var(--line-strong)]">
            <div className="flex items-baseline gap-2">
              <span className="text-[13px] font-semibold text-primary">Notifications</span>
              {unread > 0 ? (
                <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-[var(--accent-16)] px-1.5 text-[11px] font-semibold tabular-nums text-[var(--accent-bright)] ring-1 ring-inset ring-[var(--accent-32)]">{unread > 99 ? '99+' : unread}</span>
              ) : null}
            </div>
            {items.some(i => !i.read) ? (
              <Button color="link-gray" size="sm" className="text-xs" iconLeading={IcCheck} onClick={() => void markAllRead()}>Mark all read</Button>
            ) : null}
          </div>

          {fetchError ? (
            <Alert color="error" icon={IcAlertTri} className="m-3">Notifications offline — backend unreachable.</Alert>
          ) : null}

          {items.length === 0 ? (
            <div className="flex flex-col items-center gap-3 px-6 py-12 text-center">
              <FeaturedIcon size="md" color="gray" icon={<IcBell />} />
              <div>
                <div className="text-[13px] font-medium text-secondary">All caught up</div>
                <div className="mt-0.5 text-[12px] text-quaternary">No new notifications.</div>
              </div>
            </div>
          ) : (
            <div className="max-h-[420px] overflow-y-auto overscroll-contain [scrollbar-width:thin]">
              {groups.map((g, gi) => {
                const label = dayLabel(g.latest);
                const newDay = gi === 0 || label !== dayLabel(groups[gi - 1].latest);
                const isUnread = g.anyUnread;
                return (
                  <Fragment key={g.rep.id}>
                    {newDay ? (
                      <div className="sticky top-0 z-10 bg-[var(--panel-90)] px-4 py-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-quaternary backdrop-blur-sm">{label}</div>
                    ) : null}
                    <button
                      className={cn(
                        // Unread wears the app's selected idiom (3px indigo rail over
                        // accent-8); read recedes to a flat, dim, rail-less line.
                        'group relative flex w-full items-start gap-3 px-4 py-3 text-left transition-[background-color] duration-100',
                        isUnread
                          ? 'bg-[var(--accent-8)] shadow-[inset_3px_0_0_var(--accent),inset_0_-1px_0_var(--line-strong)] last:shadow-[inset_3px_0_0_var(--accent)] hover:bg-[var(--accent-12)]'
                          : 'shadow-[inset_0_-1px_0_var(--line-strong)] last:shadow-none hover:bg-tertiary',
                      )}
                      onClick={async () => {
                        const unreadMembers = g.members.filter(m => !m.read);
                        if (!unreadMembers.length) return;
                        try { await Promise.all(unreadMembers.map(m => api.markNotificationRead(m.id))); void refresh(); } catch { /* */ }
                      }}
                    >
                      <FeaturedIcon size="sm" color={kindColor(g.rep.kind)} icon={kindIcon(g.rep.kind)} className={cn(!isUnread && 'opacity-60')} />
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-x-1.5">
                          <span className={cn('text-[13px]', isUnread ? 'font-semibold text-primary' : 'font-medium text-tertiary')}>{g.rep.title}</span>
                          {g.count > 1 ? (
                            <span className={cn(
                              'inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-full px-1.5 text-[10px] font-semibold tabular-nums ring-1 ring-inset',
                              isUnread ? 'bg-[var(--accent-16)] text-[var(--accent-bright)] ring-[var(--accent-32)]' : 'bg-tertiary text-tertiary ring-secondary',
                            )}>×{g.count}</span>
                          ) : null}
                        </div>
                        {/* whitespace-pre-line: multi-line bodies (e.g. the subtitle
                            summary's bulleted "To fix" list) keep their line breaks. */}
                        {g.rep.body ? <div className={cn('mt-0.5 whitespace-pre-line text-[12px] leading-relaxed', isUnread ? 'text-secondary' : 'text-quaternary')}>{g.rep.body}</div> : null}
                        <div className={cn('mt-1 text-[11px] tabular-nums', isUnread ? 'text-tertiary' : 'text-quaternary')}>{relativeTime(g.latest)}</div>
                      </div>
                    </button>
                  </Fragment>
                );
              })}
            </div>
          )}
        </motion.div>
      ) : null}
      </AnimatePresence>
    </div>
  );
}
