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

function NotificationCard({ id, title, sub, kind }: { id: string | number; title: string; sub?: string; kind?: NotifyKind }) {
  const k = KIND[kind ?? 'info'];
  return (
    <div className="pointer-events-auto flex w-[360px] max-w-[calc(100vw-2rem)] items-start gap-3 rounded-xl border border-secondary bg-[var(--panel-90)] px-3.5 py-3 shadow-[var(--shadow-3)] backdrop-blur-2xl">
      <FeaturedIcon size="md" color={k.color} icon={k.icon} />
      <div className="min-w-0 flex-1 pt-px">
        <div className="text-[13px] font-semibold text-primary">{title}</div>
        {sub ? <div className="mt-0.5 text-[12px] leading-relaxed text-secondary">{sub}</div> : null}
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
export function notify(t: { title: string; sub?: string; kind?: NotifyKind }) {
  const len = (t.title?.length ?? 0) + (t.sub?.length ?? 0);
  const baseMs = Math.max(4000, Math.min(15000, len * 60));
  const ms = t.kind === 'error' ? Math.round(baseMs * 1.5) : baseMs;
  return sonnerToast.custom(
    (id) => <NotificationCard id={id} title={t.title} sub={t.sub} kind={t.kind} />,
    { duration: ms },
  );
}

/** Sonner host — mount once. `offset` lifts the toast stack above the activity
 *  pill / scan bar when one is showing (they share the bottom-right corner). */
export function NotificationToaster({ offset = 24 }: { offset?: number }) {
  return (
    <Toaster
      position="bottom-right"
      offset={offset}
      gap={10}
      visibleToasts={6}
      toastOptions={{ unstyled: true }}
    />
  );
}
