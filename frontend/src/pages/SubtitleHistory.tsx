import { useEffect, useState } from 'react';
import { api, type ApiSubtitleAsset } from '../lib/api';
import { IcTrash, IcAlertTri, IcCheck, IcCaption } from '../lib/icons';
import { EmptyState, Skeleton } from '../components/ui';
import { Button } from '../components/base/buttons/button';
import { cn } from '../lib/utils';
import type { ToastData } from '../lib/types';

type PushToast = (t: Omit<ToastData, 'id'>) => void;

const SYNC_STYLE: Record<string, { label: string; cls: string }> = {
  guaranteed: { label: 'in sync', cls: 'text-[var(--conf-high)] border-[color-mix(in_srgb,var(--conf-high)_45%,transparent)] bg-[color-mix(in_srgb,var(--conf-high)_12%,transparent)]' },
  likely:     { label: 'likely sync', cls: 'text-[#49b8fe] border-[rgba(73,184,254,0.4)] bg-[rgba(73,184,254,0.12)]' },
  unknown:    { label: 'sync unknown', cls: 'text-ink-soft border-line bg-white/[0.04]' },
};

function scoreColor(s: number): string {
  if (s >= 85) return 'var(--conf-high)';
  if (s >= 55) return 'var(--conf-mid)';
  return 'var(--conf-low)';
}

function relTime(iso: string): string {
  const d = Date.now() - new Date(iso).getTime();
  if (d < 60_000) return 'just now';
  if (d < 3_600_000) return `${Math.floor(d / 60_000)} min ago`;
  if (d < 86_400_000) return `${Math.floor(d / 3_600_000)} hr ago`;
  return new Date(iso).toLocaleDateString();
}

/**
 * Subtitle history — the ledger of every subtitle Kira fetched, with the
 * metric behind each (provider, release, score, sync) and management actions:
 * delete the sidecar, or blacklist a bad one so it's never re-picked. Rendered
 * as a tab on the History page.
 */
export function SubtitleHistory({ pushToast }: { pushToast: PushToast }) {
  const [items, setItems] = useState<ApiSubtitleAsset[] | null>(null);
  const [busy, setBusy] = useState<number | null>(null);

  const refresh = () => {
    void api.subtitleHistory().then(setItems).catch((e) => {
      // Don't conflate a fetch FAILURE with a genuinely-empty ledger — the empty
      // state ("No subtitles fetched yet") would lie. Surface the error so the
      // user knows it's a load problem, not "nothing here".
      setItems([]);
      pushToast({ title: "Couldn't load subtitle history", sub: (e as Error).message, kind: 'error' });
    });
  };
  useEffect(() => { refresh(); }, []);

  const act = async (a: ApiSubtitleAsset, blacklist: boolean) => {
    setBusy(a.id);
    try {
      await api.deleteSubtitleAsset(a.id, blacklist);
      pushToast({
        title: blacklist ? 'Subtitle blacklisted' : 'Subtitle deleted',
        sub: blacklist ? `${a.language.toUpperCase()} from ${a.provider} won't be picked again for this file.`
                       : `Removed the ${a.language.toUpperCase()} sidecar.`,
        kind: 'success',
      });
      window.dispatchEvent(new Event('kira:files-changed'));
      refresh();
    } catch (e) {
      pushToast({ title: 'Action failed', sub: (e as Error).message, kind: 'error' });
    } finally {
      setBusy(null);
    }
  };

  if (items === null) {
    return (
      <div className="flex flex-col gap-2">
        {[0, 1, 2].map(i => <Skeleton key={i} w="100%" h={64} radius={12} />)}
      </div>
    );
  }
  if (items.length === 0) {
    return (
      <EmptyState
        icon={<IcCaption />}
        title="No subtitles fetched yet"
        sub="Use “Get subtitles” on a title, or the dashboard coverage card, and every download lands here with its match score."
      />
    );
  }

  return (
    <div className="flex flex-col gap-2.5">
      {items.map(a => {
        const sync = SYNC_STYLE[a.sync] ?? SYNC_STYLE.unknown;
        const dim = !a.active;
        return (
          <div
            key={a.id}
            className={cn(
              'flex items-center gap-3.5 rounded-xl border border-line bg-white/[0.025] px-4 py-3 transition',
              dim && 'opacity-55',
            )}
          >
            {/* Score dial */}
            <div className="relative grid size-11 shrink-0 place-items-center">
              <svg viewBox="0 0 36 36" className="size-full -rotate-90">
                <circle cx="18" cy="18" r="15.5" fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth="3" />
                <circle cx="18" cy="18" r="15.5" fill="none" stroke={scoreColor(a.score)} strokeWidth="3"
                  strokeLinecap="round" strokeDasharray={`${(a.score / 100) * 97.4} 97.4`} />
              </svg>
              <span className="absolute text-[11px] font-bold tabular-nums text-ink">{a.score}</span>
            </div>

            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className={cn('text-[13.5px] font-semibold', dim ? 'text-ink-soft line-through' : 'text-ink')}>
                  {a.title || `File #${a.media_file_id ?? '?'}`}
                </span>
                <span className="rounded-md border border-line bg-white/[0.05] px-1.5 py-0.5 text-[10.5px] font-bold uppercase tracking-wide text-ink-muted">
                  {a.language}
                </span>
                <span className={cn('rounded-md border px-1.5 py-0.5 text-[10.5px] font-medium', sync.cls)}>
                  {sync.label}
                </span>
                {a.hearing_impaired ? <span className="rounded-md border border-line px-1.5 py-0.5 text-[10.5px] text-ink-soft">SDH</span> : null}
                {a.forced ? <span className="rounded-md border border-line px-1.5 py-0.5 text-[10.5px] text-ink-soft">forced</span> : null}
                {a.blacklisted ? <span className="rounded-md border border-[rgba(255,45,68,0.4)] bg-[rgba(255,45,68,0.12)] px-1.5 py-0.5 text-[10.5px] text-[#ff2d44]">blacklisted</span> : null}
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11.5px] text-ink-soft">
                <span className="font-medium text-ink-muted">{a.provider}</span>
                {a.release_name ? <><span className="dot-sep" /><span className="truncate font-mono">{a.release_name}</span></> : null}
                {a.reasons && a.reasons.length ? <><span className="dot-sep" /><span className="truncate">{a.reasons.join(' · ')}</span></> : null}
                <span className="dot-sep" /><span>{relTime(a.created_at)}</span>
              </div>
            </div>

            {a.active ? (
              <div className="flex shrink-0 items-center gap-1.5">
                <Button color="secondary" size="sm" iconLeading={IcTrash}
                  isDisabled={busy === a.id} onClick={() => void act(a, false)} title="Delete this subtitle">
                  Delete
                </Button>
                <Button color="secondary-destructive" size="sm" iconLeading={IcAlertTri}
                  isDisabled={busy === a.id} onClick={() => void act(a, true)}
                  title="Delete and never auto-pick this one again">
                  Blacklist
                </Button>
              </div>
            ) : (
              <span className="inline-flex shrink-0 items-center gap-1 text-[12px] text-ink-soft [&_svg]:size-3.5">
                {a.blacklisted ? <IcAlertTri /> : <IcCheck />}{a.blacklisted ? 'removed' : 'gone'}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}
