import { useEffect, useRef, useState } from 'react';
import { api, type ApiActivity, type ApiActivityJob } from '../lib/api';
import { IcSpin, IcCheck, IcAlertTri, IcX } from '../lib/icons';
import { FeaturedIcon } from './base/featured-icons/featured-icon';

type PushToast = (t: { title: string; sub?: string; kind?: 'success' | 'error' }) => void;

/**
 * Polls /api/v1/activity for background work — boot heal, MediaInfo passes,
 * subtitle fetches, ffmpeg install. Returns the job the pill should show:
 * the running one, or the most recently FINISHED one (the backend keeps
 * done/error states in the snapshot for a linger window precisely so a
 * sub-second failure still reaches us).
 *
 * The pill is the single live surface: spinner while running, a green
 * summary beat on success, a sticky red card with the explanation on
 * failure. No completion toasts, no manual refresh — state flows from the
 * poll. Errors stay until dismissed (or the backend's long error-linger
 * expires).
 *
 * Also fires a ONE-TIME toast when a restart recovered files a crash left
 * mid-scan, and re-pulls the files list when a subtitle job ends (so the
 * missing-sub chips flip without a reload).
 *
 * Lives as an always-mounted hook so polling survives page changes. Cadence
 * is adaptive: fast while something is active, slow when idle; paused while
 * the tab is hidden.
 */
export function useActivity(pushToast: PushToast): {
  job: ApiActivityJob | null;
  /** The full live snapshot — so a surface (e.g. the scan popup) can pull a
   *  specific named job (`mediainfo_enrich`) and fold it in as its own line. */
  jobs: ApiActivityJob[];
  dismissJob: (job: ApiActivityJob) => void;
} {
  const [activity, setActivity] = useState<ApiActivity | null>(null);
  const bootShown = useRef(false);
  // Finished jobs we've already reacted to (files refresh) and the ones the
  // user dismissed — keyed `${name}:${ended_at}` so a RE-RUN of the same job
  // is a fresh key and shows again.
  const handledRef = useRef<Set<string>>(new Set());
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());

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
          // React ONCE per finished job (even one that started and failed
          // between two polls): subtitle runs refresh the files list so the
          // missing-sub chips update live.
          for (const j of a.jobs) {
            if (j.active || j.ended_at == null) continue;
            const key = `${j.name}:${j.ended_at}`;
            if (handledRef.current.has(key)) continue;
            handledRef.current.add(key);
            // Both the manual/scan backfill (`subtitle_backfill`) AND the
            // post-rename auto-fetch (`subtitles`, now a background task since
            // the rename returns the instant files are moved) write .srt
            // sidecars — refresh the files list either way so the missing-sub
            // chips flip the moment the fetch lands, without a manual reload.
            if (j.name === 'subtitle_backfill' || j.name === 'subtitles') {
              window.dispatchEvent(new Event('kira:files-changed'));
            }
            if (j.name === 'ffmpeg_install' && j.state === 'done') {
              // Settings/onboarding ffmpeg rows re-check their status.
              window.dispatchEvent(new Event('kira:ffmpeg-changed'));
            }
            if (j.name === 'fpcalc_install' && j.state === 'done') {
              // Settings AcoustID row re-checks its fpcalc status.
              window.dispatchEvent(new Event('kira:fpcalc-changed'));
            }
          }
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

    // An action elsewhere (clicking "Get subtitles", saving a setting that
    // starts the MediaInfo backfill) just fired `kira:activity-refresh` —
    // poll NOW so the pill appears immediately, plus one short retry since
    // the job's begin() lands a beat after the HTTP response that triggered us.
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

  const dismissJob = (job: ApiActivityJob) => {
    if (job.ended_at == null) return;
    setDismissed(prev => new Set(prev).add(`${job.name}:${job.ended_at}`));
  };

  const jobs = activity?.jobs ?? [];
  const running = jobs.find(j => j.active) ?? null;
  // No running job → surface the most recently finished, undismissed one.
  const finished = jobs
    .filter(j => !j.active && j.ended_at != null && !dismissed.has(`${j.name}:${j.ended_at}`))
    .sort((x, y) => (y.ended_at ?? 0) - (x.ended_at ?? 0))[0] ?? null;

  return { job: running ?? finished, jobs, dismissJob };
}

/**
 * Bottom-right activity card. Shares its chrome verbatim with the notification
 * toast (NotificationCard) — same card, FeaturedIcon chip, and typography — so
 * the two read as one design and stack in one column. Three live states, all
 * driven by the poll — no refresh:
 *   running → indigo spinner + narrated label + N/M counter
 *   done    → green check + outcome line (backend expires it after ~15s)
 *   error   → red alert + explanation, sticky until dismissed
 */
export function ActivityPill({ job, onDismiss }: { job: ApiActivityJob; onDismiss?: (job: ApiActivityJob) => void }) {
  const count = job.done > 0
    ? (job.total ? `${job.done.toLocaleString()}/${job.total.toLocaleString()}` : job.done.toLocaleString())
    : null;
  const state = job.active ? 'running' : job.state;

  // Same chrome as the notification toast: a FeaturedIcon chip carries the live/
  // outcome colour (indigo spinner → green done → red error), so no separate top
  // accent line is needed and the two surfaces read as one.
  const iconColor: 'brand' | 'success' | 'error' =
    state === 'error' ? 'error' : state === 'done' ? 'success' : 'brand';
  const iconEl = state === 'error' ? <IcAlertTri /> : state === 'done' ? <IcCheck /> : <IcSpin />;

  return (
    <div
      className="anim-pop pointer-events-auto flex w-[360px] max-w-[calc(100vw-2rem)] items-start gap-3 rounded-xl border border-secondary bg-[var(--panel-90)] px-3.5 py-3 shadow-[var(--shadow-3)] backdrop-blur-2xl"
      role={state === 'error' ? 'alert' : 'status'}
      aria-live="polite"
    >
      <FeaturedIcon
        size="md"
        color={iconColor}
        icon={iconEl}
        className={state === 'running' ? '[&_svg]:animate-[spin_1.1s_linear_infinite]' : undefined}
      />
      <div className="min-w-0 flex-1 pt-px">
        <div className="text-[13px] font-semibold text-primary">
          {state === 'running' ? job.label : state === 'error' ? 'Something needs attention' : 'Done'}
        </div>
        <div className="mt-0.5 text-[12px] leading-relaxed text-secondary">
          {state === 'running'
            ? <>Working in the background{count ? <> · <span className="font-mono tabular-nums text-tertiary">{count}</span></> : null}</>
            : (job.detail ?? job.label)}
        </div>
      </div>
      {/* Finished states are dismissible — errors especially, since they
          deliberately stick around long enough to be read. */}
      {state !== 'running' && onDismiss ? (
        <button
          type="button"
          className="-mr-1 -mt-0.5 grid size-6 shrink-0 place-items-center rounded-md text-tertiary transition-colors hover:bg-primary_hover hover:text-primary [&_svg]:size-[14px]"
          onClick={() => onDismiss(job)}
          aria-label="Dismiss"
          title="Dismiss"
        >
          <IcX />
        </button>
      ) : null}
    </div>
  );
}
