import { useEffect, useRef, useState } from 'react';
import { api, type ApiNotification } from '../lib/api';
import { IcBell, IcCheck, IcAlertTri } from '../lib/icons';

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 60_000) return `${Math.max(1, Math.floor(diff / 1000))}s ago`;
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} min ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} hr ago`;
  return new Date(iso).toLocaleDateString();
}

export function NotificationsBell() {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<ApiNotification[]>([]);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  // M11: surface fetch errors once per session so a silent broken bell
  // doesn't hide the fact that the backend is unreachable. We track
  // whether we've already shown the warning so 15s polls don't spam
  // the popover with duplicate "Connection lost" rows.
  const errorSurfacedRef = useRef(false);

  const refresh = async () => {
    try {
      const next = await api.listNotifications();
      setItems(next);
      // Recovery — clear the error banner if a poll succeeds.
      if (fetchError) {
        setFetchError(null);
        errorSurfacedRef.current = false;
      }
    } catch (e) {
      // M11: was a bare swallow. Now we record the error so the popover
      // can render a one-line "Notifications offline — backend
      // unreachable" banner. Only surface ONCE per disconnection so a
      // user clicking the bell during an outage isn't bombarded.
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

  // Close on outside click.
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);

  const unread = items.filter(i => !i.read).length;

  const markAllRead = async () => {
    try {
      await api.markAllNotificationsRead();
      void refresh();
    } catch { /* swallow */ }
  };

  const dotColor = (kind: string): string => {
    if (kind === 'success') return 'var(--conf-high)';
    if (kind === 'error') return 'var(--conf-low)';
    if (kind === 'warning') return 'var(--conf-mid)';
    return 'var(--ink-3)';
  };

  return (
    <div ref={wrapRef} style={{ position: 'relative' }}>
      {/* F-14: aria-label includes the live count so screen-reader
          users know there are unread notifications without expanding
          the popover. Also gives sighted users a tooltip that reflects
          state instead of the static "Notifications" label. */}
      <button
        className="icon-btn"
        title={unread > 0 ? `Notifications (${unread} unread)` : 'Notifications'}
        aria-label={unread > 0 ? `Notifications, ${unread} unread` : 'Notifications'}
        onClick={() => setOpen(o => !o)}
      >
        <IcBell />
        {unread > 0 ? (
          <span
            // aria-hidden because the parent button already announces
            // the count — letting the badge be announced would read
            // "Notifications, 3 unread, 3" which is awkward.
            aria-hidden="true"
            style={{
              position: 'absolute', top: 4, right: 4,
              minWidth: 14, height: 14, padding: '0 4px',
              borderRadius: 7,
              background: 'var(--conf-low)', color: 'white',
              fontSize: 9, fontWeight: 700,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              lineHeight: 1,
            }}>{unread > 99 ? '99+' : unread}</span>
        ) : null}
      </button>

      {open ? (
        <div style={{
          position: 'absolute', top: 'calc(100% + 8px)', right: 0, zIndex: 50,
          width: 360, maxHeight: 480, overflowY: 'auto',
          background: 'rgba(20,16,32,0.95)',
          border: '1px solid var(--line)', borderRadius: 12,
          boxShadow: '0 18px 60px rgba(0,0,0,0.5)',
          backdropFilter: 'blur(20px)',
        }}>
          <div className="flex items-center justify-between" style={{ padding: '12px 14px', borderBottom: '1px solid var(--line)' }}>
            <div className="font-semibold text-sm">Notifications</div>
            {items.some(i => !i.read) ? (
              <button className="btn btn-sm btn-ghost" onClick={() => void markAllRead()}>Mark all read</button>
            ) : null}
          </div>
          {/* M11: surface fetch failure so a broken bell never lies. */}
          {fetchError ? (
            <div style={{
              padding: '10px 14px',
              background: 'rgba(255,80,90,0.08)',
              borderBottom: '1px solid rgba(255,80,90,0.18)',
              color: 'var(--conf-low)',
              fontSize: 12,
              display: 'flex', alignItems: 'center', gap: 8,
            }}>
              <IcAlertTri style={{ width: 12, height: 12, flexShrink: 0 }} />
              <span>Notifications offline — backend unreachable.</span>
            </div>
          ) : null}
          {items.length === 0 ? (
            <div style={{ padding: 28, textAlign: 'center', color: 'var(--ink-3)', fontSize: 13 }}>
              All caught up — no notifications.
            </div>
          ) : (
            <div>
              {items.map(n => (
                <div
                  key={n.id}
                  className="flex items-start gap-3"
                  style={{
                    padding: '10px 14px',
                    borderBottom: '1px solid rgba(255,255,255,0.04)',
                    background: n.read ? 'transparent' : 'rgba(40,217,160,0.04)',
                    cursor: n.read ? 'default' : 'pointer',
                  }}
                  onClick={async () => {
                    if (!n.read) { try { await api.markNotificationRead(n.id); void refresh(); } catch { /* */ } }
                  }}
                >
                  <span className="dot" style={{
                    background: dotColor(n.kind),
                    width: 8, height: 8, marginTop: 6, flexShrink: 0,
                  }} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="text-sm font-medium">
                      {n.kind === 'success' ? <IcCheck style={{ width: 11, height: 11, display: 'inline', marginRight: 4, color: 'var(--conf-high)' }} /> : null}
                      {n.kind === 'error' ? <IcAlertTri style={{ width: 11, height: 11, display: 'inline', marginRight: 4, color: 'var(--conf-low)' }} /> : null}
                      {n.title}
                    </div>
                    {n.body ? (
                      <div className="text-xs text-muted" style={{ marginTop: 2, lineHeight: 1.4 }}>{n.body}</div>
                    ) : null}
                    <div className="text-xs" style={{ marginTop: 4, color: 'var(--ink-4)' }}>{relativeTime(n.created_at)}</div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}
