import { useEffect, useRef, useState } from 'react';
import { api, type ApiActivity, type ApiActivityJob } from '../lib/api';
import { IcSpin, IcCheck, IcAlertTri, IcX } from '../lib/icons';

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
 * Bottom-left glass pill matching ScanProgress's surface, shown in the Toast
 * `leading` slot. Three live states, all driven by the poll — no refresh:
 *   running → spinner + narrated label + N/M counter
 *   done    → green check + outcome line (backend expires it after ~15s)
 *   error   → red alert + explanation, sticky until dismissed
 */
export function ActivityPill({ job, onDismiss }: { job: ApiActivityJob; onDismiss?: (job: ApiActivityJob) => void }) {
  const count = job.done > 0
    ? (job.total ? `${job.done.toLocaleString()}/${job.total.toLocaleString()}` : job.done.toLocaleString())
    : null;
  const state = job.active ? 'running' : job.state;

  const edge = state === 'error' ? 'var(--conf-low)'
    : state === 'done' ? 'var(--conf-high)'
    : 'var(--brand-grad)';
  const iconBox = state === 'error'
    ? 'bg-[rgba(255,91,110,0.14)] text-[var(--conf-low)]'
    : state === 'done'
      ? 'bg-[rgba(40,217,160,0.14)] text-[var(--conf-high)]'
      : 'bg-[var(--surface-3)] text-accent [&_svg]:animate-[spin_1.1s_linear_infinite]';

  return (
    <div
      className="anim-pop relative flex max-w-[440px] items-center gap-3 overflow-hidden rounded-2xl border border-[var(--border-2)] bg-[rgba(10,10,13,0.72)] px-4 py-3 shadow-[var(--shadow-3)] backdrop-blur-2xl"
      role={state === 'error' ? 'alert' : 'status'}
      aria-live="polite"
    >
      {/* Top-edge accent — brand while live, green/red for the outcome. */}
      <span aria-hidden="true" className="pointer-events-none absolute inset-x-0 top-0 h-px" style={{ background: edge, opacity: 0.6 }} />
      <span className={`grid size-7 shrink-0 place-items-center rounded-lg [&_svg]:size-3.5 ${iconBox}`}>
        {state === 'error' ? <IcAlertTri /> : state === 'done' ? <IcCheck /> : <IcSpin />}
      </span>
      <div className="min-w-0">
        <div className="text-[13px] font-semibold text-ink">
          {state === 'running' ? job.label : state === 'error' ? 'Something needs attention' : 'Done'}
        </div>
        <div className="mt-0.5 text-[11.5px] leading-relaxed text-ink-muted">
          {state === 'running'
            ? <>Working in the background{count ? <> · <span className="font-mono tabular-nums text-ink-soft">{count}</span></> : null}</>
            : (job.detail ?? job.label)}
        </div>
      </div>
      {/* Finished states are dismissible — errors especially, since they
          deliberately stick around long enough to be read. */}
      {state !== 'running' && onDismiss ? (
        <button
          className="press ml-1 grid size-6 shrink-0 place-items-center self-start rounded-md text-ink-soft transition hover:bg-white/[0.07] hover:text-ink [&_svg]:size-3"
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
