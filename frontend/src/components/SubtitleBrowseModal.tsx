import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { api, type ApiSubtitleCandidate, type ApiPackEntry } from '../lib/api';
import { IcX, IcDownload, IcCheck, IcSpin, IcCaption, IcAlertTri, IcChevLeft } from '../lib/icons';
import { cn } from '../lib/utils';
import { Button } from './base/buttons/button';
import { FeaturedIcon } from './base/featured-icons/featured-icon';
import { BadgeWithDot } from './base/badges/badges';

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

// Sync-confidence chip → BadgeWithDot (Flow rule: "likely" + "unknown" are GREY,
// never blue). Only the LABEL distinguishes them; the dot carries the hue.
const SYNC: Record<string, { label: string; color: 'success' | 'gray' }> = {
  guaranteed: { label: 'in sync', color: 'success' },
  likely:     { label: 'likely sync', color: 'gray' },
  unknown:    { label: 'sync unknown', color: 'gray' },
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
  // Animated dismissal: flip `closing` to play the exit, then unmount once it's done.
  const [closing, setClosing] = useState(false);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const close = () => {
    if (closeTimer.current) return; // already animating out
    setClosing(true);
    closeTimer.current = setTimeout(() => { setTarget(null); setClosing(false); closeTimer.current = null; }, 190);
  };

  useEffect(() => {
    const onOpen = (e: Event) => {
      const detail = (e as CustomEvent).detail as BrowseTarget;
      if (closeTimer.current) { clearTimeout(closeTimer.current); closeTimer.current = null; }
      setTarget(detail); setCands(null); setError(null); setDone(new Set());
      setPackChoice(null); setExtracting(null); setPackOffer(null); setFilling(false); setClosing(false);
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
      if (packChoice) setPackChoice(null); else close();
    };
    if (target) window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [target, packChoice]);

  if (!target) return null;

  const epLabel = episodeLabel(target.filename);
  const allPacks = !!cands && cands.length > 0 && cands.every(c => c.is_pack);

  // Target switch mid-flight (§20 m): a pack choice initiated for one file
  // must never apply to the file the modal was re-targeted at — drop all
  // in-flight pick state whenever the target changes.
  useEffect(() => {
    setPicking(null);
    setPackChoice(null);
    setPackOffer(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target.fileId]);

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
      <div className={cn('absolute inset-0 bg-[var(--scrim-60)] backdrop-blur-sm', closing ? 'anim-fade-out' : 'anim-fade')} onClick={close} />
      <div className={cn(
        'relative flex max-h-[82vh] w-full max-w-[640px] flex-col overflow-hidden rounded-2xl bg-[var(--panel-90)] shadow-[var(--shadow-3)] ring-1 ring-inset ring-secondary',
        closing ? 'anim-pop-out pointer-events-none' : 'anim-pop',
      )}>
        {/* Header — a canonical eyebrow names the phase; the leading slot becomes
            a real Back button in the pack sub-view (you went a level deeper). */}
        <div className="flex items-center gap-3 border-b border-secondary px-5 py-3.5">
          {packChoice ? (
            <button className="press grid size-9 shrink-0 place-items-center rounded-lg bg-tertiary text-tertiary ring-1 ring-inset ring-secondary transition hover:text-secondary hover:ring-primary [&_svg]:size-4" onClick={() => setPackChoice(null)} aria-label="Back"><IcChevLeft /></button>
          ) : (
            <FeaturedIcon size="md" tint="var(--accent)" icon={<IcCaption />} />
          )}
          <div className="min-w-0 flex-1">
            {packChoice ? <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-quaternary">Confirm the episode</div> : null}
            <div className="truncate text-sm font-semibold text-primary">{packChoice ? `Choose ${packEpLabel} in this pack` : 'Browse subtitles'}</div>
            <div className="truncate font-mono text-[11px] text-tertiary">{target.filename}</div>
          </div>
          <button className="press grid size-7 place-items-center rounded-md text-tertiary transition hover:bg-tertiary hover:text-secondary [&_svg]:size-4" onClick={close} aria-label="Close"><IcX /></button>
        </div>

        <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto px-3 py-3">
          {packChoice ? (
            /* ── Pack contents picker — confirm which file inside the archive ── */
            <>
              <div className="mb-1 flex items-start gap-3 rounded-xl bg-[var(--conf-mid-8)] px-3.5 py-3 ring-1 ring-inset ring-[var(--conf-mid-32)]">
                <FeaturedIcon size="sm" tint="var(--conf-mid)" icon={<IcAlertTri />} />
                <div>
                  <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-[var(--conf-mid-bright)]">Confirm the episode</div>
                  <div className="mt-1 text-[11.5px] leading-relaxed text-[var(--conf-mid-bright)]">
                    Kira couldn't be sure which file inside this pack is <span className="font-semibold">{packEpLabel}</span>.
                    Entries are ranked by episode number, title, runtime and release group — the top one (the indigo-railed row)
                    is our best guess; pick the right file to save it.
                  </div>
                </div>
              </div>
              {packChoice.entries.length === 0 ? (
                <div className="px-3 py-10 text-center text-[13px] text-tertiary">The archive had no readable subtitle files.</div>
              ) : packChoice.entries.map((entry, i) => {
                const isBest = i === 0 && entry.score > 0;
                return (
                  <div key={entry.name} className={cn(
                    'flex items-center gap-3.5 rounded-xl px-3.5 py-3 shadow-xs ring-1 ring-inset transition-[background-color,box-shadow]',
                    isBest
                      ? 'bg-[var(--accent-8)] shadow-[inset_3px_0_0_var(--accent)] ring-[var(--accent-line)] hover:bg-[var(--accent-12)]'
                      : 'bg-tertiary ring-secondary hover:ring-primary',
                  )}>
                    <div className="relative grid size-10 shrink-0 place-items-center">
                      <svg viewBox="0 0 36 36" className="size-full -rotate-90">
                        <circle cx="18" cy="18" r="15.5" fill="none" stroke="var(--line)" strokeWidth="3.2" />
                        <circle cx="18" cy="18" r="15.5" fill="none" stroke={scoreColor(entry.score)} strokeWidth="3.2" strokeLinecap="round" strokeDasharray={`${(entry.score / 100) * 97.4} 97.4`} />
                      </svg>
                      <span className="absolute text-[10.5px] font-bold tabular-nums text-primary">{entry.score}</span>
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className="truncate font-mono text-[12px] text-primary" title={entry.name}>{entryBase(entry.name)}</span>
                        {isBest ? <span className="rounded bg-[var(--accent-12)] px-1.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--accent-bright)] ring-1 ring-inset ring-[var(--accent-line)]">best guess</span> : null}
                      </div>
                      {entry.reasons.length ? <div className="mt-0.5 truncate text-[11px] text-quaternary">{entry.reasons.join(' · ')}</div> : <div className="mt-0.5 text-[11px] text-quaternary">no matching signal</div>}
                    </div>
                    <Button color={isBest ? 'primary' : 'secondary'} size="sm" iconLeading={IcDownload} isLoading={extracting === entry.name} showTextWhileLoading isDisabled={extracting !== null} onClick={() => void chooseEntry(entry)}>
                      {extracting === entry.name ? 'Saving' : 'Use this'}
                    </Button>
                  </div>
                );
              })}
            </>
          ) : error ? (
            <div className="flex flex-col items-center gap-3 px-3 py-12 text-center">
              <FeaturedIcon size="md" tint="var(--conf-low)" icon={<IcAlertTri />} />
              <div className="text-[13px] text-[var(--conf-low-bright)]">Couldn't reach the providers</div>
              <div className="max-w-[80%] break-words text-[11px] text-quaternary">{error}</div>
            </div>
          ) : cands === null ? (
            <div className="flex flex-col items-center gap-3 px-3 py-12 text-center">
              <FeaturedIcon size="md" color="gray" icon={<IcSpin />} className="[&_svg]:animate-[spin_1.1s_linear_infinite]" />
              <div className="text-[13px] text-secondary">Searching every provider…</div>
              <div className="text-[11px] text-quaternary">Ranking results the same way auto-pick does</div>
            </div>
          ) : cands.length === 0 ? (
            <div className="flex flex-col items-center gap-3 px-3 py-12 text-center">
              <FeaturedIcon size="md" color="gray" icon={<IcCaption />} />
              <div className="text-[13px] text-secondary">No candidates found</div>
              <div className="text-[11px] text-quaternary">None of the enabled providers had a match for this file.</div>
            </div>
          ) : (
            <>
              {/* Opt-in season fill — a pack we just picked from can cover more
                  episodes. Neutral container + indigo action: an obvious-but-
                  optional next step (we do NOT auto-patch the library). */}
              {packOffer ? (
                <div className="mb-1 flex items-center gap-3 rounded-xl bg-[var(--info-8)] px-3.5 py-3 ring-1 ring-inset ring-[var(--info-32)]">
                  <FeaturedIcon size="sm" tint="var(--info)" icon={<IcCaption />} />
                  <div className="min-w-0 flex-1 text-[12px] leading-relaxed text-[var(--info-bright)]">
                    This came from a <span className="font-semibold">season pack</span> — {packOffer.count} other
                    episode{packOffer.count === 1 ? '' : 's'} in this series {packOffer.count === 1 ? 'is' : 'are'} missing
                    {' '}{packOffer.language.toUpperCase()}. Fill {packOffer.count === 1 ? 'it' : 'them'} from the same download?
                  </div>
                  <Button color="primary" size="sm" isLoading={filling} showTextWhileLoading onClick={() => void fillSeason()}>{filling ? 'Filling…' : `Fill ${packOffer.count}`}</Button>
                  <Button color="link-gray" size="sm" onClick={() => setPackOffer(null)}>Dismiss</Button>
                </div>
              ) : null}
              {/* When every result is a whole-season archive, say so up front and
                  foreshadow the confirm-the-episode step. */}
              {allPacks ? (
                <div className="mb-1 flex items-start gap-3 rounded-xl bg-[var(--conf-mid-8)] px-3.5 py-3 ring-1 ring-inset ring-[var(--conf-mid-32)]">
                  <FeaturedIcon size="sm" tint="var(--conf-mid)" icon={<IcAlertTri />} />
                  <div>
                    <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-[var(--conf-mid-bright)]">Only season packs found</div>
                    <div className="mt-1 text-[11.5px] leading-relaxed text-[var(--conf-mid-bright)]">
                      No single-episode subtitle was found — these are <span className="font-semibold">complete-season packs</span>.
                      Kira downloads the archive and pulls out <span className="font-semibold">{epLabel}</span> automatically; if it
                      can't be sure which file is yours, it'll ask you to confirm.
                    </div>
                  </div>
                </div>
              ) : null}
              {cands.map(c => {
                const key = `${c.provider}:${c.ref}`;
                const sync = SYNC[c.sync] ?? SYNC.unknown;
                const isDone = done.has(key);
                return (
                  <div key={key} className={cn(
                    'flex items-center gap-3.5 rounded-xl px-3.5 py-3 shadow-xs ring-1 ring-inset transition-[background-color,box-shadow]',
                    // The amber pack-rail only earns its place when packs are MIXED
                    // with singles; when everything's a pack the callout owns that.
                    c.is_pack && !allPacks
                      ? 'bg-[var(--conf-mid-8)] shadow-[inset_2px_0_0_var(--conf-mid)] ring-[var(--conf-mid-32)]'
                      : 'bg-tertiary ring-secondary hover:ring-primary',
                  )}>
                    <div className="relative grid size-10 shrink-0 place-items-center">
                      <svg viewBox="0 0 36 36" className="size-full -rotate-90">
                        <circle cx="18" cy="18" r="15.5" fill="none" stroke="var(--line)" strokeWidth="3.2" />
                        <circle cx="18" cy="18" r="15.5" fill="none" stroke={scoreColor(c.score)} strokeWidth="3.2" strokeLinecap="round" strokeDasharray={`${(c.score / 100) * 97.4} 97.4`} />
                      </svg>
                      <span className="absolute text-[10.5px] font-bold tabular-nums text-primary">{c.score}</span>
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className="text-[12.5px] font-semibold text-primary">{c.provider}</span>
                        <span className="rounded bg-secondary px-1 text-[10px] font-bold uppercase tabular-nums text-tertiary ring-1 ring-inset ring-secondary">{c.language}</span>
                        {/* Only surface sync when it's a positive signal — "unknown"
                            is the absence of info and just adds a repeated grey chip. */}
                        {c.sync === 'guaranteed' || c.sync === 'likely' ? <BadgeWithDot color={sync.color}>{sync.label}</BadgeWithDot> : null}
                        {c.hearing_impaired ? <span className="rounded bg-secondary px-1 text-[10px] text-tertiary ring-1 ring-inset ring-secondary">SDH</span> : null}
                        {c.is_pack && !allPacks ? (
                          <span className="rounded bg-[var(--conf-mid-16)] px-1.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--conf-mid-bright)] ring-1 ring-inset ring-[var(--conf-mid-32)]">season pack</span>
                        ) : null}
                      </div>
                      <div className="mt-1 truncate font-mono text-[11px] text-tertiary" title={c.release_name}>{c.release_name || '—'}</div>
                      {c.is_pack && !allPacks ? (
                        <div className="mt-0.5 truncate text-[11px] text-[var(--conf-mid-bright)]">Full-season archive — Kira extracts {epLabel}</div>
                      ) : c.reasons.length ? (
                        <div className="mt-0.5 truncate text-[11px] text-quaternary">{c.reasons.join(' · ')}</div>
                      ) : null}
                    </div>
                    {isDone ? (
                      <span className="inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-[var(--conf-high-16)] px-2.5 py-1.5 text-[12px] font-medium text-[var(--conf-high)] ring-1 ring-inset ring-[var(--conf-high-32)] [&_svg]:size-4"><IcCheck /> saved</span>
                    ) : (
                      <Button color="secondary" size="sm" iconLeading={IcDownload} isLoading={picking === key} showTextWhileLoading isDisabled={picking !== null} onClick={() => void pick(c)} title={c.is_pack ? `Download the pack and extract ${epLabel}` : undefined}>
                        {picking === key ? (c.is_pack ? 'Extracting' : 'Downloading') : (c.is_pack ? 'Extract episode' : 'Download')}
                      </Button>
                    )}
                  </div>
                );
              })}
            </>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
