import { useEffect, useState } from 'react';
import { api, type ApiDataset } from '../lib/api';
import { IcCheck, IcAlertTri } from '../lib/icons';

/**
 * Local anime dataset health — Settings → Connections, under the provider grid.
 *
 * Answers the questions the provider pills can't: is the AniDB title dump /
 * offline episode database on disk, how old is each copy, when does it refresh,
 * and — while a refresh download runs — live MB-by-MB progress (the backend
 * narrates through the activity job; GET /system/datasets relays its label).
 */

function ago(iso: string): string {
  const diff = Date.now() - new Date(iso + (iso.endsWith('Z') ? '' : 'Z')).getTime();
  if (diff < 60_000) return 'just now';
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} h ago`;
  return `${Math.floor(diff / 86_400_000)} d ago`;
}

function fmtSize(n: number | null): string | null {
  if (n === null) return null;
  if (n >= 1 << 20) return `${(n / (1 << 20)).toFixed(1)} MB`;
  if (n >= 1 << 10) return `${Math.round(n / (1 << 10))} KB`;
  return `${n} B`;
}

export function DatasetStatusBlock() {
  const [datasets, setDatasets] = useState<ApiDataset[] | null>(null);

  const refresh = () => { void api.datasetsStatus().then(r => setDatasets(r.datasets)).catch(() => {}); };
  useEffect(() => {
    refresh();
    return undefined;
  }, []);

  // Poll fast while a download narrates, slow otherwise (a scan can kick a
  // refresh at any time — the row should pick it up without a page change).
  const downloading = !!datasets?.some(d => d.downloading);
  useEffect(() => {
    const t = window.setInterval(refresh, downloading ? 1500 : 15000);
    return () => clearInterval(t);
  }, [downloading]);

  if (!datasets) return null;

  return (
    <div className="flex flex-col gap-2.5">
      {datasets.map(d => (
        <div key={d.id} className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="text-[13px] text-ink">{d.label}</div>
            <div className="mt-0.5 text-[11.5px] text-ink-soft">{d.desc}</div>
            <div className="mt-0.5 text-[11px] text-ink-muted">{d.refresh}</div>
          </div>
          <div className="shrink-0 text-right">
            {d.downloading ? (
              <span className="inline-flex items-center gap-1.5 text-[12px] text-ink-muted">
                <span className="size-1.5 shrink-0 animate-pulse rounded-full bg-[var(--accent)]" aria-hidden />
                {d.downloading.split('·').pop()?.trim()}
              </span>
            ) : d.exists ? (
              <>
                <span className="inline-flex items-center gap-1.5 text-[12px] font-medium text-[var(--conf-high)] [&_svg]:size-3.5">
                  <IcCheck /> Updated {d.updated_at ? ago(d.updated_at) : ''}
                </span>
                {fmtSize(d.size_bytes) ? (
                  <div className="mt-0.5 text-[11px] text-ink-muted">{fmtSize(d.size_bytes)}</div>
                ) : null}
              </>
            ) : (
              <span className="inline-flex items-center gap-1.5 text-[12px] text-[var(--conf-mid)] [&_svg]:size-3.5">
                <IcAlertTri /> Not downloaded yet
              </span>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
