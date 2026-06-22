import { useEffect, useState } from 'react';
import { api, type ApiSubtitleAsset } from '../lib/api';
import { IcTrash, IcAlertTri, IcCheck, IcCaption } from '../lib/icons';
import { EmptyState, Skeleton } from '../components/ui';
import { Button } from '../components/base/buttons/button';
import { Badge, BadgeWithDot } from '../components/base/badges/badges';
import { cn } from '../lib/utils';
import type { ToastData } from '../lib/types';

type PushToast = (t: Omit<ToastData, 'id'>) => void;

// Sync-confidence language. Dot carries the hue (green=guaranteed,
// blue=likely, grey=unknown) — blue is outside UUI's 5-dot ramp so it's
// fed inline, mirroring HistoryPage's OpBadge.
const SYNC_DOT: Record<string, { label: string; color: string }> = {
  guaranteed: { label: 'in sync', color: 'var(--conf-high)' },
  likely:     { label: 'likely sync', color: 'var(--info)' },
  unknown:    { label: 'sync unknown', color: 'var(--ink-3)' },
};

function SyncBadge({ sync }: { sync: string }) {
  const s = SYNC_DOT[sync] ?? SYNC_DOT.unknown;
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-secondary bg-white/[0.04] px-2.5 py-0.5 text-[10.5px] font-medium text-secondary backdrop-blur">
      <span className="size-1.5 rounded-full" style={{ background: s.color }} />
      {s.label}
    </span>
  );
}

function scoreColor(s: number): string {
  if (s >= 85) return 'var(--conf-high)';
  if (s >= 55) return 'var(--conf-mid)';
  return 'var(--conf-low)';
}

// Match-score ring — the per-row visual anchor. Color tiers with the score;
// the number sits centered inside.
function ScoreRing({ score }: { score: number }) {
  return (
    <div className="relative grid size-12 shrink-0 place-items-center">
      <svg viewBox="0 0 36 36" className="size-full -rotate-90">
        <circle cx="18" cy="18" r="15.5" fill="none" stroke="var(--line)" strokeWidth="3.2" />
        <circle
          cx="18" cy="18" r="15.5" fill="none"
          stroke={scoreColor(score)} strokeWidth="3.2" strokeLinecap="round"
          strokeDasharray={`${(score / 100) * 97.4} 97.4`}
        />
      </svg>
      <span className="absolute text-[12px] font-bold tabular-nums text-primary">{score}</span>
    </div>
  );
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
      <div className="flex flex-col gap-2.5">
        {[0, 1, 2].map(i => <Skeleton key={i} w="100%" h={88} radius={12} />)}
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

  // Aggregate ledger stats for the summary header.
  const inSync = items.filter(a => a.sync === 'guaranteed').length;
  const blacklisted = items.filter(a => a.blacklisted).length;
  const removed = items.filter(a => !a.active).length;
  // Match-quality mix (by score tier, same thresholds as the per-row ScoreRing)
  // — the subtitles "wow", mirroring the Renames undo-status strip.
  const strong = items.filter(a => a.score >= 85).length;
  const fair = items.filter(a => a.score >= 55 && a.score < 85).length;
  const weak = items.length - strong - fair;
  const avgScore = items.length ? Math.round(items.reduce((s, a) => s + a.score, 0) / items.length) : 0;
  const qualityMix = [
    { key: 'strong', n: strong, color: 'var(--conf-high)' },
    { key: 'fair', n: fair, color: 'var(--conf-mid)' },
    { key: 'weak', n: weak, color: 'var(--conf-low)' },
  ].filter(s => s.n > 0);

  return (
    <div className="anim-rise-sm flex flex-col gap-2.5">
      {/* Summary header — count + a match-quality bar (strong/fair/weak by
          score) + at-a-glance ledger health, in the History flow grammar. */}
      <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-2">
        <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-quaternary">
          {items.length} {items.length === 1 ? 'subtitle' : 'subtitles'}
        </span>
        <div className="flex min-w-[150px] max-w-[360px] flex-1 items-center gap-2">
          <div role="group" aria-label={`Match quality: avg ${avgScore}, ${strong} strong, ${fair} fair, ${weak} weak`} className="flex h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-tertiary ring-1 ring-inset ring-secondary">
            {qualityMix.map(s => <span key={s.key} className="h-full" style={{ width: `${(s.n / Math.max(items.length, 1)) * 100}%`, background: s.color }} />)}
          </div>
          <span className="shrink-0 whitespace-nowrap text-[11px] font-medium tabular-nums text-tertiary">avg <b className="text-primary">{avgScore}</b></span>
        </div>
        {inSync > 0 ? <BadgeWithDot color="success">{inSync} in sync</BadgeWithDot> : null}
        {blacklisted > 0 ? <BadgeWithDot color="error">{blacklisted} blacklisted</BadgeWithDot> : null}
        {removed > 0 ? <BadgeWithDot color="gray">{removed} removed</BadgeWithDot> : null}
      </div>

      {items.map(a => {
        const dim = !a.active;
        return (
          <div
            key={a.id}
            className={cn(
              'flex items-start gap-4 rounded-xl bg-secondary p-3.5 shadow-xs ring-1 ring-inset ring-secondary transition-[background-color,box-shadow]',
              dim ? 'opacity-60' : 'hover:bg-tertiary hover:ring-primary',
            )}
          >
            <ScoreRing score={a.score} />

            <div className="min-w-0 flex-1">
              {/* Title + language + sync + flag badges */}
              <div className="flex flex-wrap items-center gap-2">
                <span className={cn('text-[13.5px] font-semibold', dim ? 'text-tertiary line-through' : 'text-primary')}>
                  {a.title || `File #${a.media_file_id ?? '?'}`}
                </span>
                <Badge>{a.language}</Badge>
                <SyncBadge sync={a.sync} />
                {a.hearing_impaired ? <Badge>SDH</Badge> : null}
                {a.forced ? <Badge>forced</Badge> : null}
                {a.blacklisted ? (
                  <Badge className="bg-[var(--danger-bg)] text-[var(--conf-low)] ring-1 ring-[var(--danger-line)] ring-inset">blacklisted</Badge>
                ) : null}
              </div>

              {/* Provider + the release this matched against */}
              <div className="mt-1.5 flex min-w-0 flex-wrap items-center gap-2">
                <span className="inline-flex shrink-0 items-center rounded-md bg-tertiary px-1.5 py-0.5 text-[10.5px] font-semibold lowercase tracking-wide text-secondary ring-1 ring-secondary ring-inset">
                  {a.provider}
                </span>
                {a.release_name ? (
                  <span className="truncate font-mono text-[11.5px] text-tertiary" title={a.release_name}>{a.release_name}</span>
                ) : null}
              </div>

              {/* Match facts — each signal (source, downloads, rating…) as its own chip. */}
              {a.reasons && a.reasons.length ? (
                <div className="mt-2 flex flex-wrap items-center gap-1">
                  {a.reasons.map((r, i) => (
                    <span
                      key={i}
                      className="inline-flex items-center rounded-md bg-white/[0.04] px-1.5 py-0.5 text-[10px] font-medium text-quaternary ring-1 ring-white/[0.06] ring-inset"
                    >
                      {r}
                    </span>
                  ))}
                </div>
              ) : null}
            </div>

            {/* Right rail — timestamp over the management actions / final state. */}
            <div className="flex shrink-0 flex-col items-end gap-2">
              <span className="whitespace-nowrap text-[11px] text-quaternary">{relTime(a.created_at)}</span>
              {a.active ? (
                <div className="flex items-center gap-1.5">
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
                <span className="inline-flex items-center gap-1 text-[12px] text-tertiary [&_svg]:size-3.5">
                  {a.blacklisted ? <IcAlertTri /> : <IcCheck />}{a.blacklisted ? 'removed' : 'gone'}
                </span>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
