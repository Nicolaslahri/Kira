import { IcSpin, IcCheck, IcAlertTri } from '../lib/icons';
import { cn } from '../lib/utils';
import type { TechProgress } from '../lib/types';
import { ProgressBar } from './base/progress-indicators/progress-bar';

/**
 * Bottom-right popup shown while a scan runs. Phases:
 *  - scanning: files are being discovered (total unknown) → indeterminate Scan
 *    bar climbs a file count; Match bar waits.
 *  - matching: discovery done (Scan bar complete) → Match bar shows live %.
 *  - tech tags (optional): real container metadata is read off disk.
 *  - stalled / offline: the poll saw no progress for a while, or lost the
 *    backend — the bars freeze and an explicit banner explains why instead of
 *    a frozen "running" that looks broken.
 * The `message` line surfaces what the backend is doing right now.
 */
export function ScanProgress({ phase, progress, found, message, tech, status = 'live' }: {
  phase: 'idle' | 'scanning' | 'matching' | 'done';
  progress: number;
  found: number;
  message: string;
  tech?: TechProgress | null;
  /** Health of the tracking poll — drives the frozen/stalled/offline banner. */
  status?: 'live' | 'stalled' | 'offline';
}) {
  const done = phase === 'done';
  const scanDone = phase === 'matching' || done;
  const matchActive = phase === 'matching' || done;
  // The tech-tag phase is "live" while the pass is queued or running; only then
  // does the header stay a spinner and read "Reading tech tags".
  const techPhase = !!tech && (tech.active || !!tech.queued);
  const fullyDone = done && !techPhase;
  const techPct = tech && tech.total ? Math.round((tech.done / tech.total) * 100) : tech?.state === 'done' ? 100 : 0;
  const unhealthy = status !== 'live' && !done;
  const title = unhealthy
    ? (status === 'offline' ? 'Connection lost' : 'Scan may be stalled')
    : techPhase ? 'Reading tech tags'
    : done ? 'Scan complete'
    : phase === 'matching' ? 'Matching metadata' : 'Scanning library';

  return (
    <div
      className="relative w-[360px] max-w-[calc(100vw-2rem)] overflow-hidden rounded-xl border border-secondary bg-[var(--panel-90)] p-4 shadow-[var(--shadow-3)] backdrop-blur-2xl"
      role="status"
      aria-live="polite"
    >
      <div className="flex items-center gap-3">
        <span className={cn(
          'grid size-8 shrink-0 place-items-center rounded-lg [&_svg]:size-4',
          fullyDone ? 'bg-[var(--conf-high-bg)] text-conf-high'
            : unhealthy ? 'bg-[var(--conf-low-bg)] text-conf-low'
            : 'bg-white/[0.08] text-ink-muted',
        )}>
          {fullyDone ? <IcCheck /> : unhealthy ? <IcAlertTri /> : <IcSpin />}
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-[13px] font-semibold text-ink">{title}</div>
          <div className="mt-0.5 truncate font-mono text-[11.5px] text-ink-muted" title={message}>
            {status === 'offline' ? 'The job keeps running on the server — reconnecting…'
              : status === 'stalled' ? 'No progress recently — the server may be busy or the job may have stopped.'
              : message}
          </div>
        </div>
      </div>

      <div className="mt-3.5 flex flex-col gap-3">
        {/* Scan phase */}
        <div>
          <div className="mb-1.5 flex items-center justify-between text-[11px]">
            <span className="inline-flex items-center gap-1.5 font-medium text-ink-muted [&_svg]:size-3 [&_svg]:shrink-0">
              {scanDone ? <IcCheck className="text-conf-high" /> : null}Scan
            </span>
            <span className="font-mono tabular-nums text-ink-soft">{found.toLocaleString()} files</span>
          </div>
          <ProgressBar value={scanDone ? 100 : 0} indeterminate={!scanDone && !unhealthy} color="var(--info)" label="Scanning for media files" />
        </div>

        {/* Match phase */}
        <div>
          <div className="mb-1.5 flex items-center justify-between text-[11px]">
            <span className="font-medium text-ink-muted">Match</span>
            <span className="font-mono tabular-nums text-ink-soft">{matchActive ? `${progress}%` : 'waiting…'}</span>
          </div>
          <ProgressBar value={matchActive ? progress : 0} color="var(--accent)" label="Matching files to providers" />
        </div>

        {/* Tech-tag phase — only when the "Read file metadata" feature is on.
            Detached on the backend, so it climbs after Scan + Match complete. */}
        {tech ? (
          <div>
            <div className="mb-1.5 flex items-center justify-between text-[11px]">
              <span className="inline-flex items-center gap-1.5 font-medium text-ink-muted [&_svg]:size-3 [&_svg]:shrink-0">
                {tech.state === 'done' && !tech.active ? <IcCheck className="text-conf-high" /> : null}Tech tags
              </span>
              <span className="font-mono tabular-nums text-ink-soft">
                {tech.queued ? 'queued…'
                  : tech.total ? `${tech.done.toLocaleString()} / ${tech.total.toLocaleString()}`
                  : tech.active ? 'reading…'
                  : 'done'}
              </span>
            </div>
            <ProgressBar value={techPct} indeterminate={tech.active && !tech.total} color="var(--violet)" label="Reading file media info" />
          </div>
        ) : null}
      </div>

      {/* Glass sheen sweeping across the surface — suppressed when the job is
          frozen (stalled/offline) and for reduced-motion users. */}
      {!unhealthy ? (
        <span
          aria-hidden="true"
          className="pointer-events-none absolute inset-y-0 w-1/3 -skew-x-12 bg-gradient-to-r from-transparent via-white/[0.1] to-transparent motion-reduce:hidden"
          style={{ animation: 'kira-shine 4s ease-in-out infinite' }}
        />
      ) : null}
    </div>
  );
}
