import type { ReactNode } from 'react';
import { toast as sonnerToast, Toaster } from 'sonner';
import { FeaturedIcon } from '../featured-icons/featured-icon';
import { IcCheck, IcAlertTri, IcX, IcBell } from '../../../lib/icons';

// Untitled UI notifications, built on Sonner (https://sonner.emilkowal.ski/)
// and styled with Kira's dark-glass tokens. Sonner owns the stack / enter+exit
// animation / swipe-to-dismiss / hover-to-pause; this file is just the card
// chrome + a `notify()` trigger and the <Toaster> host.

export type NotifyKind = 'success' | 'error' | 'warning' | 'info';

const KIND: Record<NotifyKind, { color: 'success' | 'error' | 'warning' | 'brand'; icon: ReactNode }> = {
  success: { color: 'success', icon: <IcCheck /> },
  error:   { color: 'error',   icon: <IcAlertTri /> },
  warning: { color: 'warning', icon: <IcAlertTri /> },
  info:    { color: 'brand',   icon: <IcBell /> },
};

/** Optional inline action button rendered in a toast (e.g. "Undo" on a rename
 *  success). Clicking runs the handler then dismisses the toast. */
export interface NotifyAction { label: string; onClick: () => void }

function NotificationCard({ id, title, sub, kind, action }: { id: string | number; title: string; sub?: string; kind?: NotifyKind; action?: NotifyAction }) {
  const k = KIND[kind ?? 'info'];
  return (
    <div className="pointer-events-auto flex w-[360px] max-w-[calc(100vw-2rem)] items-start gap-3 rounded-xl border border-secondary bg-[var(--panel-90)] px-3.5 py-3 shadow-[var(--shadow-3)] backdrop-blur-2xl">
      <FeaturedIcon size="md" color={k.color} icon={k.icon} />
      <div className="min-w-0 flex-1 pt-px">
        <div className="text-[13px] font-semibold text-primary">{title}</div>
        {sub ? <div className="mt-0.5 text-[12px] leading-relaxed text-secondary">{sub}</div> : null}
        {action ? (
          <button
            type="button"
            onClick={() => { action.onClick(); sonnerToast.dismiss(id); }}
            className="mt-1.5 inline-flex items-center rounded-md bg-secondary px-2 py-1 text-[12px] font-semibold text-primary ring-1 ring-inset ring-secondary transition-colors hover:bg-primary_hover"
          >
            {action.label}
          </button>
        ) : null}
      </div>
      <button
        type="button"
        onClick={() => sonnerToast.dismiss(id)}
        aria-label="Dismiss"
        title="Dismiss"
        className="-mr-1 -mt-0.5 grid size-6 shrink-0 place-items-center rounded-md text-tertiary transition-colors hover:bg-primary_hover hover:text-primary [&_svg]:size-[14px]"
      >
        <IcX />
      </button>
    </div>
  );
}

/** Fire a notification. Duration scales with content length (errors linger 50%
 *  longer), mirroring the old pushToast timing so existing call sites are
 *  unchanged. */
// Duplicate suppression: identical (title+sub+kind) toasts within 4s collapse
// to one — an offline burst used to queue a long tail of identical error cards.
const _recentToasts = new Map<string, number>();

export function notify(t: { title: string; sub?: string; kind?: NotifyKind; action?: NotifyAction }) {
  const dedupeKey = `${t.kind ?? 'info'}|${t.title}|${t.sub ?? ''}`;
  const now = Date.now();
  const last = _recentToasts.get(dedupeKey);
  if (last !== undefined && now - last < 4000 && !t.action) {
    _recentToasts.set(dedupeKey, now);
    return undefined as unknown as ReturnType<typeof sonnerToast.custom>;
  }
  _recentToasts.set(dedupeKey, now);
  if (_recentToasts.size > 200) {
    for (const [k, ts] of _recentToasts) { if (now - ts > 10000) _recentToasts.delete(k); }
  }
  const len = (t.title?.length ?? 0) + (t.sub?.length ?? 0);
  const baseMs = Math.max(4000, Math.min(15000, len * 60));
  // An actionable toast (e.g. Undo) lingers longer so the user has time to
  // click before it auto-dismisses.
  const ms = t.kind === 'error' ? Math.round(baseMs * 1.5) : t.action ? Math.max(baseMs, 10000) : baseMs;
  return sonnerToast.custom(
    (id) => <NotificationCard id={id} title={t.title} sub={t.sub} kind={t.kind} action={t.action} />,
    { duration: ms },
  );
}

/** Sonner host — mount once. `offset` is the BOTTOM gap that lifts the toast
 *  stack above the activity pill / scan bar sharing the bottom-right corner. The
 *  RIGHT edge is pinned to 1.5rem so the toasts line up in ONE column with that
 *  pill (`right-6`): Sonner applies a bare `offset` number to every edge, which
 *  pushed the whole stack left whenever the pill grew the bottom offset. */
export function NotificationToaster({ offset = 24 }: { offset?: number }) {
  return (
    <Toaster
      position="bottom-right"
      offset={{ right: '1.5rem', bottom: offset }}
      mobileOffset={{ right: '1rem', bottom: offset }}
      gap={10}
      visibleToasts={6}
      toastOptions={{ unstyled: true }}
    />
  );
}
