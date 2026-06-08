import { IcSpin, IcCheck } from '../lib/icons';
import { cn } from '../lib/utils';
import { ProgressBar } from './base/progress-indicators/progress-bar';

/**
 * Bottom-left popup shown while a scan runs. Two phases:
 *  - scanning: files are being discovered (total unknown) → indeterminate Scan
 *    bar climbs a file count; Match bar waits.
 *  - matching: discovery done (Scan bar complete) → Match bar shows live %.
 * The `message` line surfaces what the backend is doing right now.
 */
export function ScanProgress({ phase, progress, found, message }: {
  phase: 'idle' | 'scanning' | 'matching' | 'done';
  progress: number;
  found: number;
  message: string;
}) {
  const done = phase === 'done';
  const scanDone = phase === 'matching' || done;
  const matchActive = phase === 'matching' || done;
  const title = done ? 'Scan complete' : phase === 'matching' ? 'Matching metadata' : 'Scanning library';

  return (
    <div
      className="relative w-[340px] max-w-[calc(100vw-2rem)] overflow-hidden rounded-2xl border border-white/[0.1] bg-[rgba(8,9,12,0.6)] p-4 shadow-[0_18px_60px_rgba(0,0,0,0.55)] backdrop-blur-2xl"
      role="status"
      aria-live="polite"
    >
      <div className="flex items-center gap-3">
        <span className={cn(
          'grid size-8 shrink-0 place-items-center rounded-lg [&_svg]:size-4',
          done ? 'bg-[var(--conf-high-bg)] text-conf-high' : 'bg-white/[0.08] text-ink-muted',
        )}>
          {done ? <IcCheck /> : <IcSpin />}
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-[13px] font-semibold text-ink">{title}</div>
          <div className="mt-0.5 truncate font-mono text-[11.5px] text-ink-muted" title={message}>{message}</div>
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
          <ProgressBar value={scanDone ? 100 : 0} indeterminate={!scanDone} color="#49b8fe" />
        </div>

        {/* Match phase */}
        <div>
          <div className="mb-1.5 flex items-center justify-between text-[11px]">
            <span className="font-medium text-ink-muted">Match</span>
            <span className="font-mono tabular-nums text-ink-soft">{matchActive ? `${progress}%` : 'waiting…'}</span>
          </div>
          <ProgressBar value={matchActive ? progress : 0} color="var(--accent)" />
        </div>
      </div>

      {/* Glass sheen sweeping across the surface. */}
      <span
        aria-hidden="true"
        className="pointer-events-none absolute inset-y-0 w-1/3 -skew-x-12 bg-gradient-to-r from-transparent via-white/[0.1] to-transparent"
        style={{ animation: 'kira-shine 4s ease-in-out infinite' }}
      />
    </div>
  );
}
