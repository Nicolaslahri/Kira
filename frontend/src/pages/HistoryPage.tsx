import { useEffect, useRef, useState } from 'react';
import type { ToastData } from '../lib/types';
import { api, type ApiHistoryEntry } from '../lib/api';
import { poster as makePoster } from '../lib/data';
import { fetchAnidbPoster, getCachedAnidbPoster } from '../lib/posters';
import { IcDownload, IcUndo, IcFolder, IcArrowRight, IcHistory, IcSearch, IcX, IcCheck, IcSparkles } from '../lib/icons';
import { Poster, Checkbox, EmptyState, Skeleton, Select } from '../components/ui';
import { Button } from '../components/base/buttons/button';
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

// Operation colour language — each file-op gets a consistent hue across the
// node dot + the inline op badge, so the ledger is scannable at a glance.
// (move = the destructive one → accent/green "landed"; copy = blue; hardlink =
// amber; symlink = violet.) Falls back to neutral for anything unexpected.
const OP_STYLE: Record<string, { node: string; chip: string; ring: string }> = {
  move:     { node: 'var(--accent)',  chip: 'hist-op-move',     ring: 'rgba(40,217,160,0.5)' },
  copy:     { node: '#5b9dff',        chip: 'hist-op-copy',     ring: 'rgba(91,157,255,0.5)' },
  hardlink: { node: 'var(--conf-mid)',chip: 'hist-op-hardlink', ring: 'rgba(255,201,74,0.5)' },
  symlink:  { node: '#b48cff',        chip: 'hist-op-symlink',  ring: 'rgba(180,140,255,0.5)' },
};
function opStyle(op: string) {
  return OP_STYLE[op] ?? { node: 'var(--ink-3)', chip: 'hist-op-move', ring: 'rgba(255,255,255,0.3)' };
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
  const [view, setView] = useState<'renames' | 'subtitles'>('renames');
  const [period, setPeriod] = useState<Period>('all');
  const [opFilter, setOpFilter] = useState<OpFilter>('all');
  const [query, setQuery] = useState('');
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
  // IDs the user just undid in THIS session — fires a one-shot "Restored"
  // celebration on the row. Purely visual; the undo data flow is unchanged.
  const [justRestored, setJustRestored] = useState<Set<number>>(new Set());
  const restoredTimer = useRef<number | undefined>(undefined);
  const markRestored = (ids: number[]) => {
    setJustRestored(prev => { const next = new Set(prev); ids.forEach(i => next.add(i)); return next; });
    window.clearTimeout(restoredTimer.current);
    restoredTimer.current = window.setTimeout(() => setJustRestored(new Set()), 1800);
  };

  const refresh = async () => {
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
    try {
      await api.undoHistory(id);
      markRestored([id]);
      pushToast({ title: 'Rename undone', sub: 'File restored to its original location.', kind: 'success' });
      void refresh();
    } catch (e) {
      pushToast({ title: 'Undo failed', sub: (e as Error).message, kind: 'error' });
    }
  };
  const undoBatch = async () => {
    if (selected.size === 0) return;
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
      markRestored(ids);
      pushToast({
        title: `${res.succeeded} renames undone`,
        sub: res.failed > 0 ? `${res.failed} failed` : 'Files restored to their original locations.',
        kind: res.failed > 0 ? 'error' : 'success',
      });
      setSelected(new Set());
      void refresh();
    } catch (e) {
      pushToast({ title: 'Bulk undo failed', sub: (e as Error).message, kind: 'error' });
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

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">History</h1>
          <p className="page-sub">
            {view === 'renames'
              ? 'Every rename Kira has performed — undo any of them at any time'
              : 'Every subtitle Kira fetched — provider, match score, and sync confidence'}
          </p>
        </div>
        {view === 'renames' ? (
          <div className="flex items-center gap-2.5">
            <Button
              color="secondary"
              size="md"
              iconLeading={IcSparkles}
              isLoading={cleaning}
              onClick={() => void cleanupOrphans()}
              title="Remove leftover sidecar files (NFO, posters, subtitles) that undone renames left on disk"
            >
              Clean undo leftovers
            </Button>
            <Button color="secondary" size="md" iconLeading={IcDownload} href={api.exportHistoryUrl()} download>
              Export CSV
            </Button>
          </div>
        ) : null}
      </div>

      {/* Renames ↔ Subtitles ledger toggle. */}
      <div className="mb-5">
        <SegmentedControl
          value={view}
          onChange={v => setView(v as 'renames' | 'subtitles')}
          options={[{ value: 'renames', label: 'Renames' }, { value: 'subtitles', label: 'Subtitles' }]}
        />
      </div>

      {view === 'subtitles' ? <SubtitleHistory pushToast={pushToast} /> : (
      <>
      {/* Toolbar — period + operation filters, plus bulk actions. */}
      <div className="mb-5 flex flex-wrap items-center gap-3">
        <SegmentedControl
          value={period}
          onChange={v => setPeriod(v as Period)}
          options={[
            { value: 'today', label: `Today ${counts.today}` },
            { value: 'week', label: `This week ${counts.week}` },
            { value: 'all', label: `All ${counts.all}` },
          ]}
        />
        <div className="w-[180px]">
          <Select<OpFilter>
            value={opFilter}
            onChange={v => setOpFilter(v)}
            options={[
              { value: 'all', label: 'All operations' },
              { value: 'move', label: 'Move' },
              { value: 'hardlink', label: 'Hardlink' },
              { value: 'symlink', label: 'Symlink' },
              { value: 'copy', label: 'Copy' },
            ]}
          />
        </div>

        <div
          className="hist-search flex h-9 w-full max-w-[260px] items-center gap-2 rounded-xl border border-line bg-white/[0.025] px-3 transition focus-within:border-white/[0.2] focus-within:bg-white/[0.04]"
          onClick={(e) => { (e.currentTarget.querySelector('input') as HTMLInputElement)?.focus(); }}
        >
          <IcSearch style={{ width: 14, height: 14 }} className="shrink-0 text-ink-soft" />
          <input
            className="min-w-0 flex-1 border-0 bg-transparent text-[13px] text-ink outline-none placeholder:text-ink-soft"
            placeholder="Search renames…"
            value={query}
            onChange={e => setQuery(e.target.value)}
          />
          {query ? (
            <button
              className="press grid size-[22px] shrink-0 place-items-center rounded-md text-ink-soft transition hover:bg-white/[0.06] hover:text-ink-muted"
              title="Clear"
              aria-label="Clear search"
              onClick={() => setQuery('')}
            >
              <IcX style={{ width: 11, height: 11 }} />
            </button>
          ) : null}
        </div>

        {/* Bulk-undo bar — slides in springily when rows are selected. */}
        {selected.size > 0 ? (
          <div className="hist-bulkbar ml-auto flex items-center gap-2.5 rounded-full border border-white/[0.12] bg-white/[0.05] py-1 pl-3.5 pr-1.5 shadow-[0_8px_24px_-12px_rgba(0,0,0,0.6)]">
            <span className="text-[12px] font-medium text-ink-muted">{selected.size} selected</span>
            <Button color="secondary-destructive" size="sm" iconLeading={IcUndo} onClick={() => void undoBatch()}>
              Undo selected
            </Button>
          </div>
        ) : null}
      </div>

      {/* Count strip — sits above the timeline. */}
      <div className="mb-3 flex items-center gap-3 px-0.5">
        <Checkbox on={allChecked} indeterminate={!allChecked && someChecked} onChange={toggleAll} />
        <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-soft">
          {firstLoadDone ? `${visibleItems.length} ${visibleItems.length === 1 ? 'rename' : 'renames'}` : 'Loading…'}
        </span>
      </div>

      {!firstLoadDone ? (
        <div className="overflow-hidden rounded-2xl border border-white/[0.12] bg-white/[0.045] shadow-[0_1px_3px_rgba(0,0,0,0.35)]">
          {[0, 1, 2, 3].map(i => (
            <div key={`sk-${i}`} className="flex items-center gap-4 border-b border-white/[0.06] px-4 py-3 last:border-0">
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
          Day-grouped rail: a sticky day header, then a vertical line with
          one coloured node per rename. Rows stagger in; freshly-renamed and
          just-restored rows get their own one-shot celebration. */}
      {firstLoadDone && groups.length > 0 ? (
        <div className="flex flex-col gap-7">
          {groups.map((g, gi) => (
            <section key={`${g.label}-${gi}`} className="hist-day anim-rise-sm" style={{ ['--i' as string]: Math.min(gi, 6) }}>
              <div className="hist-day-head">
                <span className="hist-day-label">{g.label}</span>
                <span className="hist-day-count">{g.rows.length}</span>
                <span className="hist-day-line" />
              </div>

              <div className="hist-rail anim-stagger">
                {g.rows.map((h, ri) => {
                  const filename = h.new_path.split(/[\\/]/).pop() || h.new_path;
                  const isFresh = freshIds.has(h.id);
                  const isRestored = justRestored.has(h.id);
                  const op = opStyle(h.operation);
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
                        'hist-row',
                        isFresh && 'hist-row-fresh',
                        isRestored && 'hist-row-restored',
                        undone && 'hist-row-undone',
                        selected.has(h.id) && 'hist-row-selected',
                      )}
                      style={{ ['--i' as string]: Math.min(ri, 11), ['--node' as string]: op.node, ['--node-ring' as string]: op.ring }}
                    >
                      {/* Rail node — coloured by operation. */}
                      <span className="hist-node" aria-hidden="true">
                        <span className="hist-node-dot" />
                      </span>

                      <div className="hist-card lift">
                        <Checkbox
                          on={selected.has(h.id)}
                          onChange={() => toggleOne(h.id)}
                          disabled={undone}
                          title={undone ? 'Already undone' : undefined}
                        />
                        <HistPoster entry={h} filename={filename} />
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className={cn('text-[13.5px] font-semibold', undone ? 'text-ink-soft line-through' : 'text-ink')}>
                              {h.title || filename}
                            </span>
                            {h.episode_title && !undone ? (
                              <span className="truncate text-[12px] text-ink-muted">{h.episode_title}</span>
                            ) : null}
                            <span className={cn('hist-op', op.chip)}>{h.operation}</span>
                            {undone ? <span className="hist-undone-pill">Undone</span> : null}
                            {stale ? <span className="hist-stale-pill" title={staleReason}>{staleReason}</span> : null}
                          </div>
                          <div className="mt-1 flex items-center gap-1.5 text-[11.5px] text-ink-soft [&_svg]:size-3 [&_svg]:shrink-0">
                            <IcFolder /><span className="truncate font-mono" title={h.old_path}>{h.old_path}</span>
                          </div>
                          <div className="mt-0.5 flex items-center gap-1.5 text-[11.5px] text-ink-muted [&_svg]:size-3 [&_svg]:shrink-0">
                            <IcArrowRight /><span className="truncate font-mono" title={h.new_path}>{h.new_path}</span>
                          </div>
                        </div>
                        <span className="shrink-0 whitespace-nowrap text-[12px] text-ink-soft">{relativeTime(h.created_at)}</span>
                        <div className="hist-undo-slot">
                          {/* Restored confirmation flashes over the Undo button, then
                              the button settles into its disabled state. */}
                          {isRestored ? (
                            <span className="hist-restored-tag"><IcCheck style={{ width: 13, height: 13 }} />Restored</span>
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
                    </div>
                  );
                })}
              </div>
            </section>
          ))}
        </div>
      ) : null}

      {firstLoadDone && !loading && visibleItems.length === 0 ? (
        <div className="overflow-hidden rounded-2xl border border-white/[0.12] bg-white/[0.045]">
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
