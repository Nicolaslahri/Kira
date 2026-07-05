import { useEffect, useRef, useState } from 'react';
import type { ToastData } from '../lib/types';
import { api, type ApiHistoryEntry } from '../lib/api';
import { poster as makePoster } from '../lib/data';
import { fetchAnidbPoster, getCachedAnidbPoster } from '../lib/posters';
import { IcDownload, IcUndo, IcFolder, IcArrowRight, IcHistory, IcSearch, IcX, IcCheck, IcSparkles } from '../lib/icons';
import { FilterChip } from './ReviewPage';
import { Poster, Checkbox, EmptyState, Skeleton } from '../components/ui';
import { Button } from '../components/base/buttons/button';
import { Input } from '../components/base/input/input';
import { BadgeWithDot } from '../components/base/badges/badges';
import { SegmentedControl } from '../components/base/segmented/segmented-control';
import { cn } from '../lib/utils';
import { cacheGet, cacheSet } from '../lib/cache';
import { SubtitleHistory } from './SubtitleHistory';

interface Props {
  pushToast: (t: Omit<ToastData, 'id'>) => void;
}

type Period = 'today' | 'week' | 'all';
type OpFilter = 'all' | 'move' | 'copy' | 'symlink' | 'hardlink';

function relativeTime(iso: string): string {
  const t = new Date(iso).getTime();
  const diff = Date.now() - t;
  if (diff < 60_000) return `${Math.max(1, Math.floor(diff / 1000))}s ago`;
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} min ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} hr ago`;
  const d = new Date(iso);
  return `${d.toLocaleDateString()} ${d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`;
}

// Day-bucket label for the timeline group header. Pure render derivation —
// the underlying rows / data are untouched.
function dayLabel(iso: string): string {
  const d = new Date(iso);
  const now = new Date();
  const startOf = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const dayDiff = Math.round((startOf(now) - startOf(d)) / 86_400_000);
  if (dayDiff <= 0) return 'Today';
  if (dayDiff === 1) return 'Yesterday';
  if (dayDiff < 7) return d.toLocaleDateString([], { weekday: 'long' });
  return d.toLocaleDateString([], { month: 'short', day: 'numeric', year: now.getFullYear() === d.getFullYear() ? undefined : 'numeric' });
}

// Operation colour language — each file-op gets a consistent hue (the dot in
// OpBadge), so the ledger is scannable at a glance. All four resolve to the
// shared palette tokens (move=accent, copy=info, hardlink=conf-mid,
// symlink=media-anime); see index.css :root `--op-*`. Neutral fallback.
const OP_STYLE: Record<string, string> = {
  move:     'var(--op-move)',
  copy:     'var(--op-copy)',
  hardlink: 'var(--op-hardlink)',
  symlink:  'var(--op-symlink)',
};
function opStyle(op: string): string {
  return OP_STYLE[op] ?? 'var(--ink-3)';
}

// Operation badge — a BadgeWithDot-shaped pill whose dot carries the per-op
// hue (move=emerald, copy=blue, hardlink=amber, symlink=violet). The dot color
// is outside UUI's 5-color dot ramp, so it's fed inline while the pill chrome
// stays on UUI tokens.
function OpBadge({ op }: { op: string }) {
  const node = opStyle(op);
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-secondary bg-white/[0.04] px-2.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.05em] text-secondary backdrop-blur">
      <span className="size-1.5 rounded-full" style={{ background: node }} />
      {op}
    </span>
  );
}

/** Row poster with the same lazy AniDB resolution as the library grid:
 *  AniDB matches carry no poster_url (the title dump has no images), so a
 *  history entry for an anime rename arrives with a null poster + the
 *  match's provider identity. Resolve the cover through the shared
 *  per-AID cache — usually already warm from the grid, so it paints
 *  synchronously; first-ever AIDs fill in as the fetch lands. */
function HistPoster({ entry, filename }: { entry: ApiHistoryEntry; filename: string }) {
  const aid = entry.provider === 'anidb' && entry.provider_id ? entry.provider_id : null;
  const [lazyPoster, setLazyPoster] = useState<string | null>(() =>
    aid ? (getCachedAnidbPoster(aid) ?? null) : null
  );
  useEffect(() => {
    if (entry.poster_url || lazyPoster || !aid) return;
    let cancelled = false;
    void fetchAnidbPoster(aid).then(url => {
      if (!cancelled && url) setLazyPoster(url);
    });
    return () => { cancelled = true; };
  }, [entry.poster_url, lazyPoster, aid]);
  return (
    <Poster
      data={makePoster(entry.title || filename, null)}
      imgUrl={entry.poster_url ?? lazyPoster}
      size="sm"
      shape={entry.media_type === 'music' ? 'square' : 'poster'}
    />
  );
}

export function HistoryPage({ pushToast }: Props) {
  // Stale-while-revalidate: synchronously hydrate from localStorage so
  // on second+ refresh the previous list renders instantly, then the
  // background fetch silently updates it. First-ever visit starts
  // empty + firstLoadDone=false so skeleton rows render.
  const cachedItems = cacheGet<ApiHistoryEntry[]>('history.items');
  const cachedCounts = cacheGet<{ today: number; week: number; all: number }>('history.counts');
  const [items, setItems] = useState<ApiHistoryEntry[]>(cachedItems ?? []);
  const [counts, setCounts] = useState<{ today: number; week: number; all: number }>(
    cachedCounts ?? { today: 0, week: 0, all: 0 }
  );
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [view, setView] = useState<'renames' | 'subtitles' | 'trash'>('renames');
  const [period, setPeriod] = useState<Period>('all');
  const [opFilter, setOpFilter] = useState<OpFilter>('all');
  const [query, setQuery] = useState('');
  // Selection must not outlive the view it was made in: rows selected under
  // "All" stayed selected after switching to "Today", so the bulk bar counted
  // (and "Undo selected" acted on) rows the user could no longer SEE.
  useEffect(() => { setSelected(new Set()); }, [period, opFilter, query]);
  // Initial value `true` so the empty-state EmptyState doesn't flash
  // on first paint — the useEffect below kicks off refresh() which sets
  // loading=true anyway, but during the very first render before the
  // effect fires, `loading=false && items=[]` would trip the empty UI
  // for one frame.
  const [loading, setLoading] = useState(true);
  // `firstLoadDone` flips to true once we have data to show — either
  // from cache (instant) or from the first network fetch. Subsequent
  // filter changes don't reset it.
  const [firstLoadDone, setFirstLoadDone] = useState(cachedItems !== null);
  // Snapshot of IDs we'd seen BEFORE the latest refresh, so the next
  // render can highlight just-added rows with a brief pulse animation.
  // Resets every time the user changes filters (a "new" row by filter
  // change isn't actually new — it was just filtered out before).
  const seenIdsRef = useRef<Set<number>>(new Set((cachedItems ?? []).map(r => r.id)));
  const [freshIds, setFreshIds] = useState<Set<number>>(new Set());
  // Per-row undo-viability map, keyed by history id. Filled lazily by an
  // effect that calls api.verifyUndoable() for the visible NOT-undone rows;
  // a row absent from the map is treated as undoable (optimistic default —
  // we never block render on the check). `undoable:false` greys that row's
  // Undo button and surfaces `reason` in its tooltip.
  const [verifyMap, setVerifyMap] = useState<Record<number, { undoable: boolean; reason: string }>>({});
  // IDs we've already asked the backend about, so re-renders / refetches
  // don't re-verify the same rows every pass (mirrors CoverPopup's
  // verifiedExistRef). Cleared on refetch so a refreshed list re-checks.
  const verifiedRef = useRef<Set<number>>(new Set());
  // Undo requests currently in flight (row ids; -1 = the bulk button) — the
  // double-click guard for undoOne/undoBatch.
  const undoInFlight = useRef<Set<number>>(new Set());
  // IDs the user just undid in THIS session — fires a one-shot "Restored"
  // celebration on the row. Purely visual; the undo data flow is unchanged.
  const [justRestored, setJustRestored] = useState<Set<number>>(new Set());
  const restoredTimer = useRef<number | undefined>(undefined);
  const markRestored = (ids: number[]) => {
    setJustRestored(prev => { const next = new Set(prev); ids.forEach(i => next.add(i)); return next; });
    window.clearTimeout(restoredTimer.current);
    restoredTimer.current = window.setTimeout(() => setJustRestored(new Set()), 1800);
  };

  const refreshGen = useRef(0);
  const refresh = async () => {
    // Generation guard: rapid filter switches fire overlapping refreshes; if
    // the OLDER one resolved last it painted the wrong filter's rows.
    const gen = ++refreshGen.current;
    setLoading(true);
    try {
      const [rows, c] = await Promise.all([
        api.listHistory({ period, operation: opFilter === 'all' ? undefined : opFilter }),
        api.historyCounts(),
      ]);
      // Detect newly-appeared rows so we can pulse-highlight them.
      // A row is "fresh" if its id wasn't in the snapshot from the
      // previous refresh. Skipped on the very first load (when the
      // snapshot starts at cachedItems' ids — everything would be
      // "old" against that baseline anyway).
      if (gen !== refreshGen.current) return;  // superseded by a newer refresh
      const prevIds = seenIdsRef.current;
      const newFresh = new Set<number>();
      if (prevIds.size > 0) {
        for (const r of rows) {
          if (!prevIds.has(r.id)) newFresh.add(r.id);
        }
      }
      seenIdsRef.current = new Set(rows.map(r => r.id));
      // A fresh list invalidates prior viability checks (an undo just changed
      // what's on disk). Reset the dedupe guard so the verify effect re-asks
      // about every still-undoable row; the map itself is left in place so the
      // old chips don't flicker away before the new answer lands.
      verifiedRef.current = new Set();
      setItems(rows);
      setCounts(c);
      if (newFresh.size > 0) {
        setFreshIds(newFresh);
        // Clear the highlight after the animation duration so it
        // doesn't visually re-fire if the user scrolls past the row.
        setTimeout(() => setFreshIds(new Set()), 2500);
      }
      // Persist for next refresh's stale-while-revalidate paint.
      // Only cache the all-filter results so subsequent loads default
      // to "show everything immediately" — applying filters refetches
      // on its own and overwrites items state for the duration of the
      // session.
      if (period === 'all' && opFilter === 'all') {
        cacheSet('history.items', rows);
      }
      cacheSet('history.counts', c);
    } catch (e) {
      pushToast({ title: 'Failed to load history', sub: (e as Error).message, kind: 'error' });
    } finally {
      setLoading(false);
      setFirstLoadDone(true);
    }
  };

  useEffect(() => { void refresh(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [period, opFilter]);

  // ── Live-refresh on rename success ────────────────────────────────
  // Without this, the user has to manually re-visit the page or change
  // a filter to see freshly-renamed entries. App.tsx dispatches
  // `kira:rename-success` whenever a rename batch completes. Each fire
  // gets debounced 250ms so a batch of 10 sequential renames produces
  // ONE refresh instead of 10 racing each other.
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    const onRenameSuccess = () => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        timer = null;
        void refresh();
      }, 250);
    };
    window.addEventListener('kira:rename-success', onRenameSuccess);
    return () => {
      window.removeEventListener('kira:rename-success', onRenameSuccess);
      if (timer) clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [period, opFilter]);

  const undoOne = async (id: number) => {
    // In-flight guard: the row's Undo button is only disabled by SERVER-derived
    // state (undone/stale), refreshed after the fact — a double-click fired two
    // undo calls, the second 4xx'ing into a confusing "Undo failed" toast right
    // after the success one.
    if (undoInFlight.current.has(id)) return;
    undoInFlight.current.add(id);
    try {
      await api.undoHistory(id);
      markRestored([id]);
      pushToast({ title: 'Rename undone', sub: 'File restored to its original location.', kind: 'success' });
      // The undo flipped the file's status back to "matched" (the Review queue).
      // Tell the rest of the app so the Review cover recovers IMMEDIATELY instead
      // of staying stuck on "Renamed" until a manual page reload.
      try { window.dispatchEvent(new Event('kira:files-changed')); } catch { /* no window */ }
      void refresh();
    } catch (e) {
      pushToast({ title: 'Undo failed', sub: (e as Error).message, kind: 'error' });
    } finally {
      undoInFlight.current.delete(id);
    }
  };
  const undoBatch = async () => {
    if (selected.size === 0) return;
    if (undoInFlight.current.has(-1)) return;  // bulk guard
    undoInFlight.current.add(-1);
    try {
      // Skip rows we already KNOW are stale — sending them would just inflate
      // the "N failed" count with predictable failures. The remaining ids still
      // go through the server's own guards.
      const ids = Array.from(selected).filter(id => verifyMap[id]?.undoable !== false);
      if (ids.length === 0) {
        pushToast({
          title: 'Nothing to undo',
          sub: 'The selected renames can no longer be undone (files changed on disk).',
          kind: 'error',
        });
        return;
      }
      const res = await api.undoHistoryBulk(ids);
      // Celebrate only the rows that ACTUALLY restored — flagging every
      // attempted id flashed green on failed rows too.
      markRestored(res.succeeded_ids ?? ids);
      pushToast({
        title: `${res.succeeded} renames undone`,
        sub: res.failed > 0 ? `${res.failed} failed` : 'Files restored to their original locations.',
        kind: res.failed > 0 ? 'error' : 'success',
      });
      setSelected(new Set());
      // Refresh the Review covers too (undone files are back in the queue).
      try { window.dispatchEvent(new Event('kira:files-changed')); } catch { /* no window */ }
      void refresh();
    } catch (e) {
      pushToast({ title: 'Bulk undo failed', sub: (e as Error).message, kind: 'error' });
    } finally {
      undoInFlight.current.delete(-1);
    }
  };
  const toggleOne = (id: number) => {
    // Undone rows aren't selectable — restoring them is a no-op and would only
    // pad bulk-undo's "N failed" count, so the per-row checkbox ignores them.
    const row = items.find(i => i.id === id);
    if (row?.undone_at) return;
    const next = new Set(selected);
    if (next.has(id)) next.delete(id); else next.add(id);
    setSelected(next);
  };

  // "Clean undo leftovers" — sweep stray sidecars (NFO/poster/subs) that undone
  // renames left on disk, then refresh so any rows affected re-verify.
  const [cleaning, setCleaning] = useState(false);
  const cleanupOrphans = async () => {
    if (cleaning) return;
    setCleaning(true);
    try {
      const { removed } = await api.cleanupOrphans();
      pushToast({
        title: removed > 0 ? `Removed ${removed} leftover ${removed === 1 ? 'file' : 'files'}` : 'Nothing to clean',
        sub: removed > 0
          ? 'Stray sidecars from undone renames were cleared.'
          : 'No leftover sidecars from undone renames.',
        kind: 'success',
      });
      void refresh();
    } catch (e) {
      pushToast({ title: 'Cleanup failed', sub: (e as Error).message, kind: 'error' });
    } finally {
      setCleaning(false);
    }
  };

  // Client-side substring search over the loaded rows (title, both paths,
  // operation). The backend caps the list at 500, which is plenty to filter
  // locally for a personal library, and matches the Review queue's behaviour.
  const q = query.trim().toLowerCase();
  const visibleItems = q
    ? items.filter(h => {
        const fn = h.new_path.split(/[\\/]/).pop() || '';
        return [h.title, h.episode_title, h.old_path, h.new_path, h.operation, fn]
          .filter(Boolean).join(' ').toLowerCase().includes(q);
      })
    : items;

  // Only NOT-undone rows are selectable — an undone rename can't be undone
  // again, so including it would make select-all enqueue guaranteed failures.
  // The header checkbox + bulk actions reason over this subset, never the raw
  // visible list.
  const selectableItems = visibleItems.filter(i => !i.undone_at);
  const allChecked = selectableItems.length > 0 && selectableItems.every(i => selected.has(i.id));
  const someChecked = selectableItems.some(i => selected.has(i.id));
  const toggleAll = () => {
    if (allChecked) setSelected(new Set());
    else setSelected(new Set(selectableItems.map(i => i.id)));
  };

  // ── Stale-undo detection ──────────────────────────────────────────
  // Ask the backend which NOT-undone rows can still be safely undone (file
  // changed on disk / original location now occupied / target gone). Mirrors
  // CoverPopup's verify-exist effect: fire-and-forget, dedup-guarded by
  // verifiedRef, never blocks render (rows default to undoable until an answer
  // lands). Keyed off `items` (not the search-filtered list) so typing in the
  // search box doesn't re-trigger it. refresh() clears verifiedRef so the list
  // re-checks after any undo.
  useEffect(() => {
    const toCheck = items
      .filter(i => !i.undone_at && !verifiedRef.current.has(i.id))
      .map(i => i.id);
    if (toCheck.length === 0) return;
    toCheck.forEach(id => verifiedRef.current.add(id));
    let cancelled = false;
    api.verifyUndoable(toCheck)
      .then(res => {
        if (cancelled || !res) return;
        setVerifyMap(prev => {
          const next = { ...prev };
          for (const [idStr, v] of Object.entries(res)) {
            next[Number(idStr)] = v;
          }
          return next;
        });
      })
      .catch(() => {
        // Verification is best-effort: on failure the rows stay enabled
        // (the undo itself still guards server-side). Drop them from the
        // dedupe set so a later refetch can retry.
        toCheck.forEach(id => verifiedRef.current.delete(id));
      });
    return () => { cancelled = true; };
  }, [items]);

  // Group the visible rows into ordered day buckets for the timeline. The
  // backend already returns rows newest-first, so iterating in order keeps the
  // groups + rows chronologically descending. This is a render-only view over
  // `visibleItems`; nothing about the data or filters changes.
  const groups: { label: string; rows: ApiHistoryEntry[] }[] = [];
  for (const h of visibleItems) {
    const label = dayLabel(h.created_at);
    const last = groups[groups.length - 1];
    if (last && last.label === label) last.rows.push(h);
    else groups.push({ label, rows: [h] });
  }

  // Undo-status overview — purely derived from the visible rows + verifyMap.
  // (Op-mix would lie: `items` is server-filtered by operation, so it collapses
  // when an op chip is active. active/undone/undoable survive any filter.)
  const totalN = visibleItems.length;
  const undoneN = visibleItems.filter(h => h.undone_at).length;
  const activeN = totalN - undoneN;
  // Optimistic default (?.undoable !== false) — matches undoBatch's predicate.
  const undoableN = visibleItems.filter(h => !h.undone_at && verifyMap[h.id]?.undoable !== false).length;
  // Selected rows the server says can no longer be undone — undoBatch skips them.
  const staleSel = Array.from(selected).filter(id => verifyMap[id]?.undoable === false).length;

  return (
    <div className="page">
      <div className="mb-4">
        {/* ROW 1 — title + undo-status strip + view toggle + actions */}
        <div className="mb-2.5 flex flex-wrap items-center gap-x-3 gap-y-2">
          <h1 className="mr-1 text-[22px] font-semibold leading-none tracking-tight text-primary">History</h1>

          {/* UNDO-STATUS STRIP — the one compact "wow", nested in the title-row
              dead space (≈0 added height). Presentational: there is no
              active/undone filter, so the bar doesn't filter — the chips do. */}
          {view === 'renames' && firstLoadDone ? (
            <div className="flex min-w-[170px] max-w-[420px] flex-1 items-center gap-2.5">
              <div role="group" aria-label={`Undo status: ${activeN} active, ${undoneN} undone, ${undoableN} undoable`} className="flex h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-tertiary ring-1 ring-inset ring-secondary">
                <span className="h-full" style={{ width: `${(activeN / Math.max(totalN, 1)) * 100}%`, background: 'var(--conf-high)' }} />
                <span className="h-full" style={{ width: `${(undoneN / Math.max(totalN, 1)) * 100}%`, background: 'var(--conf-low)' }} />
              </div>
              <span className="shrink-0 whitespace-nowrap text-[11px] font-medium tabular-nums text-tertiary">
                <b className="text-primary">{activeN}</b> active{undoneN > 0 ? <> · <b style={{ color: 'var(--conf-low)' }}>{undoneN}</b> undone</> : null} · <b style={{ color: 'var(--conf-high)' }}>{undoableN}</b> undoable
              </span>
            </div>
          ) : null}

          {/* View toggle stays a SegmentedControl — it's a mode switch, not a filter. */}
          <SegmentedControl
            value={view}
            onChange={v => setView(v as 'renames' | 'subtitles' | 'trash')}
            options={[{ value: 'renames', label: 'Renames' }, { value: 'subtitles', label: 'Subtitles' }, { value: 'trash', label: 'Trash' }]}
          />

          {view === 'renames' ? (
            <div className="ml-auto flex items-center gap-2">
              <Button color="secondary" size="sm" iconLeading={IcSparkles} isLoading={cleaning} onClick={() => void cleanupOrphans()} title="Remove leftover sidecar files (NFO, posters, subtitles) that undone renames left on disk">Clean undo leftovers</Button>
              {/* Fetch-blob download (NOT a plain href): a link navigation
                  carries no Authorization header, so with auth enabled the
                  old link 401'd instead of downloading. */}
              <Button color="secondary" size="sm" iconLeading={IcDownload} onClick={() => {
                void api.downloadHistoryCsv().catch(e =>
                  pushToast({ title: 'Export failed', sub: (e as Error).message, kind: 'error' }));
              }}>Export CSV</Button>
            </div>
          ) : null}
        </div>

        {/* ROW 2 — period + operation FilterChips + search (renames view only) */}
        {view === 'renames' ? (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="mr-0.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-quaternary">Period</span>
              <FilterChip on={period === 'today'} onClick={() => setPeriod('today')} label="Today" num={counts.today} />
              <FilterChip on={period === 'week'} onClick={() => setPeriod('week')} label="This week" num={counts.week} />
              <FilterChip on={period === 'all'} onClick={() => setPeriod('all')} label="All" num={counts.all} />
            </div>
            {/* Honest truncation notice: the API caps at 500 rows, so on a
                big ledger the visible list is a window, not the whole thing. */}
            {items.length >= 500 ? (
              <span className="text-[11px] text-quaternary">Showing latest {items.length}{counts.all > items.length ? ` of ${counts.all}` : ''}</span>
            ) : null}
            <span aria-hidden className="hidden h-5 w-px bg-white/10 sm:block" />
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="mr-0.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-quaternary">Operation</span>
              <FilterChip on={opFilter === 'all'} onClick={() => setOpFilter('all')} label="All" />
              <FilterChip on={opFilter === 'move'} onClick={() => setOpFilter('move')} label="Move" accent="var(--op-move)" dot />
              <FilterChip on={opFilter === 'hardlink'} onClick={() => setOpFilter('hardlink')} label="Hardlink" accent="var(--op-hardlink)" dot />
              <FilterChip on={opFilter === 'symlink'} onClick={() => setOpFilter('symlink')} label="Symlink" accent="var(--op-symlink)" dot />
              <FilterChip on={opFilter === 'copy'} onClick={() => setOpFilter('copy')} label="Copy" accent="var(--op-copy)" dot />
            </div>
            <div className="ml-auto">
              <Input
                icon={IcSearch}
                placeholder="Search renames…"
                value={query}
                onChange={e => setQuery(e.target.value)}
                wrapperClassName="h-9 w-full max-w-[260px] !rounded-xl !py-0"
                trailing={query ? (
                  <button
                    className="press grid size-[22px] shrink-0 place-items-center rounded-md text-ink-soft transition hover:bg-white/[0.06] hover:text-ink-muted"
                    title="Clear"
                    aria-label="Clear search"
                    onClick={() => setQuery('')}
                  >
                    <IcX style={{ width: 11, height: 11 }} />
                  </button>
                ) : null}
              />
            </div>
          </div>
        ) : null}
      </div>

      {view === 'trash' ? <TrashView pushToast={pushToast} /> : view === 'subtitles' ? <SubtitleHistory pushToast={pushToast} /> : (
      <>
      {/* Bulk-undo selection bar — indigo-armed, mirrors the Review page.
          Surfaces verifyMap's "can no longer be undone" so the bar tells the
          truth about what Undo will actually attempt. */}
      {selected.size > 0 ? (
        <div className="mb-3 flex flex-wrap items-center gap-3 rounded-xl bg-secondary px-4 py-2.5" style={{ boxShadow: 'inset 0 0 0 1px color-mix(in srgb, var(--accent) 45%, transparent)' }}>
          <div className="flex items-center gap-2 text-[13px] text-primary">
            <span className="grid size-5 place-items-center rounded-md text-white [&_svg]:size-3" style={{ background: 'var(--accent-deep)' }} aria-hidden="true"><IcCheck /></span>
            <b className="font-semibold tabular-nums">{selected.size} selected</b>
            {staleSel > 0 ? <span className="text-[12px] text-secondary">· {staleSel} can no longer be undone</span> : null}
          </div>
          <div className="ml-auto flex flex-wrap items-center gap-2">
            <Button color="secondary" size="sm" onClick={() => setSelected(new Set())}>Clear</Button>
            <Button color="secondary-destructive" size="sm" iconLeading={IcUndo} onClick={() => void undoBatch()}>Undo selected</Button>
          </div>
        </div>
      ) : null}

      {/* Count strip — sits above the timeline. */}
      <div className="mb-3 flex items-center gap-3 px-0.5">
        <Checkbox on={allChecked} indeterminate={!allChecked && someChecked} onChange={toggleAll} />
        <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-quaternary">
          {firstLoadDone ? `${visibleItems.length} ${visibleItems.length === 1 ? 'rename' : 'renames'}` : 'Loading…'}
        </span>
      </div>

      {!firstLoadDone ? (
        <div className="overflow-hidden rounded-2xl border border-secondary bg-secondary shadow-xs">
          {[0, 1, 2, 3].map(i => (
            <div key={`sk-${i}`} className="flex items-center gap-4 border-b border-secondary px-4 py-3 last:border-0">
              <Skeleton w={18} h={18} radius={4} />
              <Skeleton w={40} h={56} radius={6} />
              <div className="flex min-w-0 flex-1 flex-col gap-1.5">
                <Skeleton w="52%" h={14} />
                <Skeleton w="78%" h={11} />
                <Skeleton w="66%" h={11} />
              </div>
              <Skeleton w={64} h={11} />
              <Skeleton w={72} h={30} radius={8} />
            </div>
          ))}
        </div>
      ) : null}

      {/* ── Timeline ────────────────────────────────────────────────
          Day-grouped UUI card list. Each rename is a self-contained card
          (poster + title + op badge + from→to paths + undo). Freshly-renamed
          rows get a brief emerald ring; just-undone rows swap Undo→Restored. */}
      {firstLoadDone && groups.length > 0 ? (
        <div className="flex flex-col gap-8">
          {groups.map((g, gi) => (
            <section key={`${g.label}-${gi}`} className="anim-rise-sm" style={{ ['--i' as string]: Math.min(gi, 6) }}>
              <div className="mb-3 flex items-center gap-3">
                <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-quaternary">{g.label}</span>
                <span className="inline-grid h-5 min-w-[20px] place-items-center rounded-full bg-tertiary px-1.5 text-[11px] font-semibold text-tertiary ring-1 ring-secondary ring-inset">{g.rows.length}</span>
                <span className="h-px flex-1 bg-gradient-to-r from-[var(--line-strong)] to-transparent" />
              </div>

              <div className="anim-stagger flex flex-col gap-2.5">
                {g.rows.map((h, ri) => {
                  const filename = h.new_path.split(/[\\/]/).pop() || h.new_path;
                  const isFresh = freshIds.has(h.id);
                  const isRestored = justRestored.has(h.id);
                  const undone = !!h.undone_at;
                  // Backend viability check — absent = optimistic "undoable".
                  // A row the server says we can't undo is greyed with its reason.
                  const verdict = verifyMap[h.id];
                  const stale = !undone && verdict?.undoable === false;
                  const staleReason = stale ? (verdict?.reason || 'Can no longer be undone') : '';
                  return (
                    <div
                      key={h.id}
                      className={cn(
                        'group/hrow flex items-center gap-4 rounded-xl bg-secondary p-3 shadow-xs ring-1 ring-inset ring-secondary transition-[background-color,box-shadow]',
                        'hover:bg-tertiary hover:ring-primary',
                        selected.has(h.id) && '!bg-[var(--accent-soft)] !ring-[var(--accent-line)]',
                        isFresh && !selected.has(h.id) && '!ring-[var(--accent-line)]',
                        undone && 'opacity-70',
                      )}
                      // Undone rows skip the stagger entrance: kRiseSm's `both`
                      // fill-mode would otherwise pin opacity:1 and override the
                      // opacity-70 dim. Inline animation:none beats the
                      // `.anim-stagger > *` rule (they're old rows — no need to
                      // animate them in anyway).
                      style={{ ['--i' as string]: Math.min(ri, 11), animation: undone ? 'none' : undefined }}
                    >
                      <Checkbox
                        on={selected.has(h.id)}
                        onChange={() => toggleOne(h.id)}
                        disabled={undone}
                        title={undone ? 'Already undone' : undefined}
                      />
                      <HistPoster entry={h} filename={filename} />
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className={cn('text-[13.5px] font-semibold', undone ? 'text-tertiary line-through' : 'text-primary')}>
                            {h.title || filename}
                          </span>
                          {h.episode_title && !undone ? (
                            <span className="truncate text-[12px] text-tertiary">{h.episode_title}</span>
                          ) : null}
                          <OpBadge op={h.operation} />
                          {undone ? <BadgeWithDot color="gray">Undone</BadgeWithDot> : null}
                          {stale ? (
                            <BadgeWithDot color="error" className="max-w-[240px]">
                              <span className="min-w-0 truncate" title={staleReason}>{staleReason}</span>
                            </BadgeWithDot>
                          ) : null}
                        </div>
                        <div className="mt-1.5 flex items-center gap-1.5 text-[11.5px] text-tertiary [&_svg]:size-3 [&_svg]:shrink-0">
                          <IcFolder /><span className="truncate font-mono" title={h.old_path}>{h.old_path}</span>
                        </div>
                        <div className="mt-0.5 flex items-center gap-1.5 text-[11.5px] text-secondary [&_svg]:size-3 [&_svg]:shrink-0">
                          <IcArrowRight className="text-[var(--accent)]" /><span className="truncate font-mono" title={h.new_path}>{h.new_path}</span>
                        </div>
                      </div>
                      <span className="shrink-0 whitespace-nowrap text-[12px] text-tertiary">{relativeTime(h.created_at)}</span>
                      <div className="flex min-w-[92px] shrink-0 justify-end">
                        {isRestored ? (
                          <span className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--accent-line)] bg-[var(--accent-soft)] px-3 py-1.5 text-[12px] font-semibold text-[var(--accent)] [&_svg]:size-3.5"><IcCheck />Restored</span>
                        ) : (
                          <Button
                            color="secondary"
                            size="sm"
                            iconLeading={IcUndo}
                            isDisabled={undone || stale}
                            title={undone ? 'Already undone' : staleReason || undefined}
                            onClick={() => void undoOne(h.id)}
                            className="press"
                          >
                            Undo
                          </Button>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </section>
          ))}
        </div>
      ) : null}

      {firstLoadDone && !loading && visibleItems.length === 0 ? (
        <div className="overflow-hidden rounded-2xl border border-secondary bg-secondary">
          {q ? (
            <EmptyState
              icon={<IcSearch />}
              title="No matching renames"
              sub={`Nothing matches “${query.trim()}”. Try a different title or path.`}
            />
          ) : (
            <EmptyState
              icon={<IcHistory />}
              title="Nothing renamed yet"
              sub="Approve files in the Review queue and click Apply to populate this log."
            />
          )}
        </div>
      ) : null}
      </>
      )}
    </div>
  );
}


/** Trash tab (§10): restore/remove swept items right next to Undo, instead of
 *  digging through Settings. Same API the Settings trash card uses. */
function TrashView({ pushToast }: { pushToast: (t: Omit<ToastData, 'id'>) => void }) {
  type TrashItem = { name: string; is_dir: boolean; size_bytes: number; trashed_at: string | null; mtime: number; original: string | null };
  const [items, setItems] = useState<TrashItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const load = async () => {
    setLoading(true);
    try {
      const r = await api.listTrash();
      setItems(r.items);
    } catch { /* connectivity UI covers it */ } finally { setLoading(false); }
  };
  useEffect(() => { void load(); }, []);
  const fmtSize = (b: number) => b >= 1 << 30 ? `${(b / (1 << 30)).toFixed(1)} GB` : b >= 1 << 20 ? `${(b / (1 << 20)).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`;
  const restore = async (it: TrashItem) => {
    setBusy(it.name);
    try {
      const r = await api.restoreTrashItem(it.name);
      pushToast({ title: 'Restored', sub: r.to, kind: 'success' });
      await load();
    } catch (e) {
      pushToast({ title: 'Restore failed', sub: (e as Error).message, kind: 'error' });
    } finally { setBusy(null); }
  };
  const remove = async (it: TrashItem) => {
    setBusy(it.name);
    try {
      await api.deleteTrashItem(it.name);
      pushToast({ title: 'Deleted permanently', sub: it.name, kind: 'error' });
      await load();
    } catch (e) {
      pushToast({ title: 'Delete failed', sub: (e as Error).message, kind: 'error' });
    } finally { setBusy(null); }
  };
  if (loading) return <div className="py-10 text-center text-[13px] text-tertiary">Loading trash…</div>;
  if (!items.length) return (
    <div className="grid place-items-center py-10">
      <EmptyState icon={<IcSparkles />} title="Trash is empty" sub="Items the cleanup sweep recycles land here, ready to restore." />
    </div>
  );
  return (
    <div className="flex flex-col gap-1.5">
      {items.map(it => (
        <div key={it.name} className="flex items-center gap-3 rounded-xl bg-secondary px-3.5 py-2.5 ring-1 ring-inset ring-secondary">
          <div className="min-w-0 flex-1">
            <div className="truncate font-mono text-[12.5px] text-primary">{it.name}</div>
            <div className="truncate text-[11px] text-tertiary">
              {fmtSize(it.size_bytes)}{it.trashed_at ? ` · trashed ${new Date(it.trashed_at).toLocaleString()}` : ''}{it.original ? ` · from ${it.original}` : ' · original location unknown'}
            </div>
          </div>
          <Button color="secondary" size="sm" iconLeading={IcUndo} isDisabled={busy === it.name || !it.original} isLoading={busy === it.name} onClick={() => void restore(it)} title={it.original ? `Restore to ${it.original}` : 'Original location unknown — restore by hand from the trash folder'}>Restore</Button>
          <Button color="secondary-destructive" size="sm" iconLeading={IcX} isDisabled={busy === it.name} onClick={() => void remove(it)}>Delete</Button>
        </div>
      ))}
    </div>
  );
}
