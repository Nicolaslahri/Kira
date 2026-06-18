import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { api, type ApiSubtitleCandidate, type ApiPackEntry } from '../lib/api';
import { IcX, IcDownload, IcCheck, IcSpin, IcCaption, IcAlertTri, IcChevLeft } from '../lib/icons';
import { cn } from '../lib/utils';

type PushToast = (t: { title: string; sub?: string; kind?: 'success' | 'error' }) => void;

interface BrowseTarget { fileId: number; filename: string; language?: string }

/** When a pack is ambiguous, /pick hands back the ranked archive contents and
 *  we drop into a "choose the episode" sub-view carrying this. */
interface PackChoice {
  provider: string;
  ref: string;
  language: string;
  episode: number | null;
  entries: ApiPackEntry[];
}

const SYNC: Record<string, { label: string; cls: string }> = {
  guaranteed: { label: 'in sync', cls: 'text-[var(--conf-high)] border-[color-mix(in_srgb,var(--conf-high)_45%,transparent)] bg-[color-mix(in_srgb,var(--conf-high)_12%,transparent)]' },
  likely:     { label: 'likely sync', cls: 'text-[#49b8fe] border-[rgba(73,184,254,0.4)] bg-[rgba(73,184,254,0.12)]' },
  unknown:    { label: 'sync unknown', cls: 'text-ink-soft border-line bg-white/[0.04]' },
};
function scoreColor(s: number): string {
  return s >= 85 ? 'var(--conf-high)' : s >= 55 ? 'var(--conf-mid)' : 'var(--conf-low)';
}

/** Best-effort episode label from the video filename, for the pack messaging
 *  ("Kira pulls out Episode 6"). Display-only — the backend does the real
 *  extraction off parsed_data. Falls back to a generic phrase. */
function episodeLabel(filename: string): string {
  const se = filename.match(/s(\d{1,2})[\s._-]*e(\d{1,3})/i);
  if (se) return `Episode ${parseInt(se[2], 10)}`;
  const ep = filename.match(/(?:\bepisode\b|\bep\b)[\s._-]*(\d{1,3})/i);
  if (ep) return `Episode ${parseInt(ep[1], 10)}`;
  return 'your episode';
}

/** Strip the directory part of an archive entry path for display. */
function entryBase(name: string): string {
  return name.replace(/\\/g, '/').split('/').pop() || name;
}

/**
 * Manual subtitle browse-and-pick. Mounted once at the app root; opens when
 * any "No EN" chip dispatches `kira:browse-subtitles` with the file. Lists the
 * SCORED candidates across all providers (the same ranking the auto-pick uses)
 * so the user can see WHY each is ranked and choose a specific one.
 *
 * When the chosen result is a SEASON PACK that Kira can't resolve confidently,
 * it switches to a second view listing the archive's contents — each entry
 * ranked by the SAME signals the matcher uses (S/E, absolute number, episode
 * title, runtime, release group) — so the user confirms the right episode
 * instead of getting a silent wrong-episode save or a dead end.
 */
export function SubtitleBrowseModal({ pushToast }: { pushToast: PushToast }) {
  const [target, setTarget] = useState<BrowseTarget | null>(null);
  const [cands, setCands] = useState<ApiSubtitleCandidate[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [picking, setPicking] = useState<string | null>(null);
  const [done, setDone] = useState<Set<string>>(new Set());
  const [packChoice, setPackChoice] = useState<PackChoice | null>(null);
  const [extracting, setExtracting] = useState<string | null>(null);
  // Opt-in offer: a pack we just picked from could fill N other episodes.
  const [packOffer, setPackOffer] = useState<{ provider: string; ref: string; language: string; count: number } | null>(null);
  const [filling, setFilling] = useState(false);

  useEffect(() => {
    const onOpen = (e: Event) => {
      const detail = (e as CustomEvent).detail as BrowseTarget;
      setTarget(detail); setCands(null); setError(null); setDone(new Set());
      setPackChoice(null); setExtracting(null); setPackOffer(null); setFilling(false);
    };
    window.addEventListener('kira:browse-subtitles', onOpen);
    return () => window.removeEventListener('kira:browse-subtitles', onOpen);
  }, []);

  useEffect(() => {
    if (!target) return;
    let cancelled = false;
    void api.subtitleCandidates(target.fileId, target.language)
      .then(c => { if (!cancelled) setCands(c); })
      .catch(e => { if (!cancelled) setError((e as Error).message); });
    return () => { cancelled = true; };
  }, [target]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      // Esc backs out of the pack sub-view first, then closes the modal.
      if (packChoice) setPackChoice(null); else setTarget(null);
    };
    if (target) window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [target, packChoice]);

  if (!target) return null;

  const epLabel = episodeLabel(target.filename);
  const allPacks = !!cands && cands.length > 0 && cands.every(c => c.is_pack);

  const pick = async (c: ApiSubtitleCandidate) => {
    const key = `${c.provider}:${c.ref}`;
    setPicking(key);
    try {
      const res = await api.pickSubtitle({ file_id: target.fileId, provider: c.provider, language: c.language, ref: c.ref });
      if (res.needs_choice) {
        // Ambiguous pack — drop into the "choose the episode" sub-view.
        setPackChoice({
          provider: c.provider, ref: c.ref, language: c.language,
          episode: res.episode ?? null, entries: res.entries ?? [],
        });
        return;
      }
      setDone(prev => new Set(prev).add(key));
      window.dispatchEvent(new Event('kira:files-changed'));
      if (res.pack_more && res.provider && res.ref) {
        setPackOffer({ provider: res.provider, ref: res.ref, language: res.language ?? c.language, count: res.pack_more });
      }
      pushToast(res.already_present ? {
        title: `${res.language.toUpperCase()} already on disk`,
        sub: 'Coverage refreshed — it was already saved next to this file.',
        kind: 'success',
      } : {
        title: c.is_pack
          ? `${res.language.toUpperCase()} extracted from pack`
          : `${res.language.toUpperCase()} subtitle saved`,
        sub: `${res.provider} · ${res.score}% · ${res.sync}`,
        kind: 'success',
      });
    } catch (e) {
      pushToast({ title: 'Download failed', sub: (e as Error).message, kind: 'error' });
    } finally {
      setPicking(null);
    }
  };

  const chooseEntry = async (entry: ApiPackEntry) => {
    if (!packChoice) return;
    setExtracting(entry.name);
    try {
      const res = await api.extractPackEntry({
        file_id: target.fileId, provider: packChoice.provider,
        language: packChoice.language, ref: packChoice.ref, entry: entry.name,
      });
      window.dispatchEvent(new Event('kira:files-changed'));
      setDone(prev => new Set(prev).add(`${packChoice.provider}:${packChoice.ref}`));
      if (res.pack_more && res.provider && res.ref) {
        setPackOffer({ provider: res.provider, ref: res.ref, language: res.language ?? packChoice.language, count: res.pack_more });
      }
      pushToast(res.already_present ? {
        title: `${res.language.toUpperCase()} already on disk`,
        sub: 'Coverage refreshed — it was already saved next to this file.',
        kind: 'success',
      } : {
        title: `${res.language.toUpperCase()} extracted from pack`,
        sub: `${res.provider} · ${entryBase(entry.name)}`,
        kind: 'success',
      });
      setPackChoice(null);
    } catch (e) {
      pushToast({ title: 'Extract failed', sub: (e as Error).message, kind: 'error' });
    } finally {
      setExtracting(null);
    }
  };

  const fillSeason = async () => {
    if (!packOffer || filling) return;
    setFilling(true);
    try {
      const res = await api.harvestPack({
        file_id: target.fileId, provider: packOffer.provider,
        ref: packOffer.ref, language: packOffer.language,
      });
      window.dispatchEvent(new Event('kira:files-changed'));
      pushToast(res.harvested ? {
        title: `Filled ${res.harvested} more episode${res.harvested === 1 ? '' : 's'}`,
        sub: `${packOffer.language.toUpperCase()} extracted from the same pack`,
        kind: 'success',
      } : {
        title: 'Nothing more to fill',
        sub: 'Those episodes weren’t in this pack — try a different one.',
        kind: 'success',
      });
      setPackOffer(null);
    } catch (e) {
      pushToast({ title: 'Fill failed', sub: (e as Error).message, kind: 'error' });
    } finally {
      setFilling(false);
    }
  };

  const packEpLabel = packChoice?.episode != null ? `Episode ${packChoice.episode}` : epLabel;

  return createPortal(
    <div className="fixed inset-0 z-[200] grid place-items-center p-4" role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={() => setTarget(null)} />
      <div className="anim-pop relative flex max-h-[82vh] w-full max-w-[640px] flex-col overflow-hidden rounded-2xl border border-[var(--border-2)] bg-[rgba(12,12,15,0.96)] shadow-[var(--shadow-3)]">
        <div className="flex items-center gap-3 border-b border-line px-5 py-3.5">
          {packChoice ? (
            <button className="press grid size-8 shrink-0 place-items-center rounded-lg bg-[var(--surface-3)] text-ink-soft hover:text-ink [&_svg]:size-4" onClick={() => setPackChoice(null)} aria-label="Back"><IcChevLeft /></button>
          ) : (
            <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-[var(--surface-3)] text-accent [&_svg]:size-4"><IcCaption /></span>
          )}
          <div className="min-w-0 flex-1">
            <div className="text-[14px] font-semibold text-ink">{packChoice ? `Choose ${packEpLabel} in this pack` : 'Browse subtitles'}</div>
            <div className="truncate font-mono text-[11.5px] text-ink-soft">{target.filename}</div>
          </div>
          <button className="press grid size-7 place-items-center rounded-md text-ink-soft hover:bg-white/[0.07] hover:text-ink [&_svg]:size-4" onClick={() => setTarget(null)} aria-label="Close"><IcX /></button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
          {packChoice ? (
            /* ── Pack contents picker ─────────────────────────────────── */
            <div className="flex flex-col gap-2">
              <div className="mb-1 flex items-start gap-2 rounded-xl border border-[rgba(245,180,90,0.35)] bg-[rgba(245,180,90,0.08)] px-3.5 py-2.5 text-[11.5px] leading-relaxed text-[#f5b45a] [&_svg]:mt-0.5 [&_svg]:size-4 [&_svg]:shrink-0">
                <IcAlertTri />
                <span>
                  Kira couldn't be sure which file inside this pack is <span className="font-semibold">{packEpLabel}</span>.
                  Entries are ranked by everything we know — episode number, title, runtime and release group.
                  The top one is our best guess; pick the right file to save it.
                </span>
              </div>
              {packChoice.entries.length === 0 ? (
                <div className="px-3 py-8 text-center text-[13px] text-ink-soft">The archive had no readable subtitle files.</div>
              ) : packChoice.entries.map((entry, i) => {
                const isBest = i === 0 && entry.score > 0;
                return (
                  <div key={entry.name} className={cn(
                    'flex items-center gap-3 rounded-xl border px-3.5 py-2.5',
                    isBest ? 'border-[rgba(73,184,254,0.4)] bg-[rgba(73,184,254,0.06)]' : 'border-line bg-white/[0.025]',
                  )}>
                    <div className="relative grid size-10 shrink-0 place-items-center">
                      <svg viewBox="0 0 36 36" className="size-full -rotate-90">
                        <circle cx="18" cy="18" r="15.5" fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth="3.2" />
                        <circle cx="18" cy="18" r="15.5" fill="none" stroke={scoreColor(entry.score)} strokeWidth="3.2" strokeLinecap="round" strokeDasharray={`${(entry.score / 100) * 97.4} 97.4`} />
                      </svg>
                      <span className="absolute text-[10.5px] font-bold tabular-nums text-ink">{entry.score}</span>
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className="truncate font-mono text-[12px] text-ink" title={entry.name}>{entryBase(entry.name)}</span>
                        {isBest ? <span className="rounded border border-[rgba(73,184,254,0.45)] bg-[rgba(73,184,254,0.12)] px-1.5 text-[10px] font-semibold uppercase tracking-wide text-[#49b8fe]">best guess</span> : null}
                      </div>
                      {entry.reasons.length ? <div className="mt-0.5 truncate text-[11px] text-ink-muted">{entry.reasons.join(' · ')}</div> : <div className="mt-0.5 text-[11px] text-ink-faint">no matching signal</div>}
                    </div>
                    <button
                      className="press inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-line bg-white/[0.05] px-2.5 py-1.5 text-[12px] font-medium text-ink transition hover:bg-white/[0.1] disabled:opacity-50 [&_svg]:size-3.5"
                      disabled={extracting !== null}
                      onClick={() => void chooseEntry(entry)}
                    >
                      {extracting === entry.name ? <IcSpin className="animate-spin" /> : <IcDownload />}
                      {extracting === entry.name ? 'Saving' : 'Use this'}
                    </button>
                  </div>
                );
              })}
            </div>
          ) : error ? (
            <div className="flex items-center gap-2 px-3 py-6 text-[13px] text-[var(--conf-low)] [&_svg]:size-4"><IcAlertTri />{error}</div>
          ) : cands === null ? (
            <div className="flex items-center justify-center gap-2 px-3 py-10 text-[13px] text-ink-soft [&_svg]:size-4 [&_svg]:animate-[spin_1.1s_linear_infinite]"><IcSpin /> Searching every provider…</div>
          ) : cands.length === 0 ? (
            <div className="px-3 py-10 text-center text-[13px] text-ink-soft">No candidates found across the enabled providers.</div>
          ) : (
            <div className="flex flex-col gap-2">
              {/* Opt-in season fill — a pack we just picked from can cover more
                  episodes. We do NOT auto-patch the library off one click; the
                  user decides here. */}
              {packOffer ? (
                <div className="mb-1 flex items-center gap-3 rounded-xl border border-[rgba(73,184,254,0.4)] bg-[rgba(73,184,254,0.08)] px-3.5 py-2.5">
                  <div className="min-w-0 flex-1 text-[12px] leading-relaxed text-[#9cd6ff]">
                    This came from a <span className="font-semibold">season pack</span> — {packOffer.count} other
                    episode{packOffer.count === 1 ? '' : 's'} in this series {packOffer.count === 1 ? 'is' : 'are'} missing
                    {' '}{packOffer.language.toUpperCase()}. Fill {packOffer.count === 1 ? 'it' : 'them'} from the same download?
                  </div>
                  <button
                    className="press inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-[rgba(73,184,254,0.5)] bg-[rgba(73,184,254,0.15)] px-3 py-1.5 text-[12px] font-semibold text-[#9cd6ff] transition hover:bg-[rgba(73,184,254,0.25)] disabled:opacity-50 [&_svg]:size-3.5"
                    disabled={filling}
                    onClick={() => void fillSeason()}
                  >
                    {filling ? <IcSpin className="animate-spin" /> : null}
                    {filling ? 'Filling…' : `Fill ${packOffer.count}`}
                  </button>
                  <button
                    className="press shrink-0 rounded-md px-1.5 text-[12px] text-ink-soft hover:text-ink"
                    onClick={() => setPackOffer(null)}
                  >
                    Dismiss
                  </button>
                </div>
              ) : null}
              {/* When every result is a whole-season archive, say so up front —
                  the user isn't doing anything wrong, that's just all the
                  provider has, and Kira extracts the right episode for them. */}
              {allPacks ? (
                <div className="mb-1 flex items-start gap-2 rounded-xl border border-[rgba(245,180,90,0.35)] bg-[rgba(245,180,90,0.08)] px-3.5 py-2.5 text-[11.5px] leading-relaxed text-[#f5b45a] [&_svg]:mt-0.5 [&_svg]:size-4 [&_svg]:shrink-0">
                  <IcAlertTri />
                  <span>
                    No single-episode subtitle was found — these are <span className="font-semibold">complete-season packs</span>.
                    Kira downloads the archive and pulls out <span className="font-semibold">{epLabel}</span> automatically; if it
                    can't be sure which file is yours, it'll ask you to confirm.
                  </span>
                </div>
              ) : null}
              {cands.map(c => {
                const key = `${c.provider}:${c.ref}`;
                const sync = SYNC[c.sync] ?? SYNC.unknown;
                const isDone = done.has(key);
                return (
                  <div key={key} className={cn(
                    'flex items-center gap-3 rounded-xl border px-3.5 py-2.5',
                    c.is_pack
                      ? 'border-[rgba(245,180,90,0.28)] bg-[rgba(245,180,90,0.04)]'
                      : 'border-line bg-white/[0.025]',
                  )}>
                    <div className="relative grid size-10 shrink-0 place-items-center">
                      <svg viewBox="0 0 36 36" className="size-full -rotate-90">
                        <circle cx="18" cy="18" r="15.5" fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth="3.2" />
                        <circle cx="18" cy="18" r="15.5" fill="none" stroke={scoreColor(c.score)} strokeWidth="3.2" strokeLinecap="round" strokeDasharray={`${(c.score / 100) * 97.4} 97.4`} />
                      </svg>
                      <span className="absolute text-[10.5px] font-bold tabular-nums text-ink">{c.score}</span>
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className="text-[12.5px] font-semibold text-ink">{c.provider}</span>
                        <span className="rounded border border-line px-1 text-[10px] font-bold uppercase text-ink-muted">{c.language}</span>
                        <span className={cn('rounded border px-1.5 text-[10px]', sync.cls)}>{sync.label}</span>
                        {c.hearing_impaired ? <span className="rounded border border-line px-1 text-[10px] text-ink-soft">SDH</span> : null}
                        {c.is_pack ? (
                          <span className="rounded border border-[rgba(245,180,90,0.4)] bg-[rgba(245,180,90,0.1)] px-1.5 text-[10px] font-semibold uppercase tracking-wide text-[#f5b45a]">season pack</span>
                        ) : null}
                      </div>
                      <div className="mt-0.5 truncate font-mono text-[11px] text-ink-soft" title={c.release_name}>{c.release_name || '—'}</div>
                      {c.is_pack ? (
                        <div className="mt-0.5 text-[11px] text-[#f5b45a]/80">Full-season archive — Kira extracts {epLabel}</div>
                      ) : c.reasons.length ? (
                        <div className="mt-0.5 truncate text-[11px] text-ink-muted">{c.reasons.join(' · ')}</div>
                      ) : null}
                    </div>
                    {isDone ? (
                      <span className="inline-flex shrink-0 items-center gap-1 text-[12px] font-medium text-[var(--conf-high)] [&_svg]:size-4"><IcCheck /> saved</span>
                    ) : (
                      <button
                        className="press inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-line bg-white/[0.05] px-2.5 py-1.5 text-[12px] font-medium text-ink transition hover:bg-white/[0.1] disabled:opacity-50 [&_svg]:size-3.5"
                        disabled={picking !== null}
                        onClick={() => void pick(c)}
                        title={c.is_pack ? `Download the pack and extract ${epLabel}` : undefined}
                      >
                        {picking === key ? <IcSpin className="animate-spin" /> : <IcDownload />}
                        {picking === key ? (c.is_pack ? 'Extracting' : 'Downloading') : (c.is_pack ? 'Extract episode' : 'Download')}
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
