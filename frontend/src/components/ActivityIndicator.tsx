import { useEffect, useRef, useState } from 'react';
import { api, type ApiActivity, type ApiActivityJob } from '../lib/api';
import { IcSpin } from '../lib/icons';

type PushToast = (t: { title: string; sub?: string; kind?: 'success' | 'error' }) => void;

/**
 * Polls /api/v1/activity for BACKGROUND work the user didn't directly
 * trigger — chiefly the boot auto-heal sweep (re-matching stale rows, e.g.
 * the One Piece episode-drift class) and the first-boot anime-mapping
 * download. Returns the first active job (or null) for the caller to render.
 *
 * Also fires a ONE-TIME toast when a restart recovered files a crash left
 * mid-scan, so an interrupted scan is acknowledged instead of silently
 * swallowed (the "kill backend mid-scan and the cover sticks" complaint).
 *
 * Lives as an always-mounted hook so it keeps polling across page changes
 * and the boot toast can't re-fire on a remount. Cadence is adaptive: fast
 * while something is active, slow when idle; paused while the tab is hidden.
 */
export function useActivity(pushToast: PushToast): ApiActivityJob | null {
  const [activity, setActivity] = useState<ApiActivity | null>(null);
  const bootShown = useRef(false);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let kickRetry: ReturnType<typeof setTimeout> | undefined;

    const poll = async () => {
      if (timer) clearTimeout(timer);   // keep a single polling chain
      let delay = 12000;
      if (!document.hidden) {
        try {
          const a = await api.getActivity();
          if (cancelled) return;
          setActivity(a);
          if (!bootShown.current && a.boot && a.boot.files_reset > 0) {
            bootShown.current = true;
            const n = a.boot.files_reset;
            pushToast({
              title: 'Recovered after restart',
              sub: `Reset ${n} file${n === 1 ? '' : 's'} left mid-scan by an interrupted run — they'll be re-matched on the next scan.`,
              kind: 'success',
            });
          }
          delay = a.active ? 4000 : 12000;
        } catch {
          /* backend unreachable — stay quiet; the connectivity banner covers it */
        }
      }
      if (!cancelled) timer = setTimeout(poll, delay);
    };

    // An action elsewhere (e.g. saving a setting that starts the MediaInfo
    // backfill) just fired `kira:activity-refresh` — poll NOW instead of waiting
    // out the idle interval, plus one short retry since the job's begin() lands
    // a beat after the HTTP response that triggered us. This is why the pill no
    // longer needs a manual page refresh to appear.
    const kick = () => {
      void poll();
      if (kickRetry) clearTimeout(kickRetry);
      kickRetry = setTimeout(() => { if (!cancelled) void poll(); }, 1200);
    };

    window.addEventListener('kira:activity-refresh', kick);
    void poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      if (kickRetry) clearTimeout(kickRetry);
      window.removeEventListener('kira:activity-refresh', kick);
    };
  }, [pushToast]);

  return activity?.jobs.find(j => j.active) ?? null;
}

/**
 * Bottom-left glass pill matching ScanProgress's surface, shown in the Toast
 * `leading` slot when a background job is active and no user scan is running.
 */
export function ActivityPill({ job }: { job: ApiActivityJob }) {
  const count = job.done > 0
    ? (job.total ? `${job.done.toLocaleString()}/${job.total.toLocaleString()}` : job.done.toLocaleString())
    : null;
  return (
    <div
      className="flex items-center gap-3 rounded-2xl border border-white/[0.1] bg-[rgba(8,9,12,0.6)] px-4 py-3 shadow-[0_18px_60px_rgba(0,0,0,0.55)] backdrop-blur-2xl"
      role="status"
      aria-live="polite"
    >
      <span className="grid size-7 shrink-0 place-items-center rounded-lg bg-white/[0.08] text-ink-muted [&_svg]:size-3.5">
        <IcSpin />
      </span>
      <div className="min-w-0">
        <div className="text-[13px] font-semibold text-ink">{job.label}</div>
        <div className="mt-0.5 text-[11.5px] text-ink-muted">
          Working in the background{count ? <> · <span className="font-mono tabular-nums text-ink-soft">{count}</span></> : null}
        </div>
      </div>
    </div>
  );
}
