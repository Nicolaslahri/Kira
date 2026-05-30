import { useEffect, useRef, useState } from 'react';
import type { ToastData } from '../lib/types';
import { api, type ApiHistoryEntry } from '../lib/api';
import { poster as makePoster } from '../lib/data';
import { IcDownload, IcUndo, IcFolder, IcArrowRight, IcHistory, IcSearch, IcX } from '../lib/icons';
import { Poster, Checkbox, EmptyState, Skeleton, Select } from '../components/ui';
import { Button } from '../components/base/buttons/button';
import { Badge, BadgeWithDot } from '../components/base/badges/badges';
import { SegmentedControl } from '../components/base/segmented/segmented-control';
import { cn } from '../lib/utils';
import { cacheGet, cacheSet } from '../lib/cache';

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
      pushToast({ title: 'Rename undone', sub: 'File restored to its original location.', kind: 'success' });
      void refresh();
    } catch (e) {
      pushToast({ title: 'Undo failed', sub: (e as Error).message, kind: 'error' });
    }
  };
  const undoBatch = async () => {
    if (selected.size === 0) return;
    try {
      const res = await api.undoHistoryBulk(Array.from(selected));
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
    const next = new Set(selected);
    if (next.has(id)) next.delete(id); else next.add(id);
    setSelected(next);
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

  const allChecked = visibleItems.length > 0 && visibleItems.every(i => selected.has(i.id));
  const someChecked = visibleItems.some(i => selected.has(i.id));
  const toggleAll = () => {
    if (allChecked) setSelected(new Set());
    else setSelected(new Set(visibleItems.map(i => i.id)));
  };

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">History</h1>
          <p className="page-sub">Every rename Kira has performed — undo any of them at any time</p>
        </div>
        <Button color="secondary" size="md" iconLeading={IcDownload} href={api.exportHistoryUrl()} download>
          Export CSV
        </Button>
      </div>

      {/* Toolbar — period + operation filters, plus bulk actions. */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
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
          className="flex h-9 w-full max-w-[260px] items-center gap-2 rounded-xl border border-line bg-white/[0.025] px-3 transition focus-within:border-white/[0.2] focus-within:bg-white/[0.04]"
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
              className="grid size-[22px] shrink-0 place-items-center rounded-md text-ink-soft transition hover:bg-white/[0.06] hover:text-ink-muted"
              title="Clear"
              onClick={() => setQuery('')}
            >
              <IcX style={{ width: 11, height: 11 }} />
            </button>
          ) : null}
        </div>

        {selected.size > 0 ? (
          <div className="ml-auto flex items-center gap-2.5">
            <span className="text-[12px] text-ink-muted">{selected.size} selected</span>
            <Button color="secondary-destructive" size="sm" iconLeading={IcUndo} onClick={() => void undoBatch()}>
              Undo selected
            </Button>
          </div>
        ) : null}
      </div>

      <div className="overflow-hidden rounded-2xl border border-white/[0.12] bg-white/[0.045] shadow-[0_1px_3px_rgba(0,0,0,0.35)]">
        {/* Header strip — select-all + count. */}
        <div className="flex items-center gap-4 border-b border-white/[0.1] bg-white/[0.02] px-4 py-2.5">
          <Checkbox on={allChecked} indeterminate={!allChecked && someChecked} onChange={toggleAll} />
          <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-soft">
            {firstLoadDone ? `${visibleItems.length} ${visibleItems.length === 1 ? 'rename' : 'renames'}` : 'Loading…'}
          </span>
        </div>

        {!firstLoadDone ? (
          <>
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
          </>
        ) : null}

        {visibleItems.map(h => {
          const filename = h.new_path.split(/[\\/]/).pop() || h.new_path;
          const isFresh = freshIds.has(h.id);
          return (
            <div
              key={h.id}
              className={cn(
                'flex items-center gap-4 border-b border-white/[0.06] px-4 py-3 transition-colors last:border-0 hover:bg-glass',
                isFresh && 'bg-[var(--accent-soft)]',
              )}
            >
              <Checkbox on={selected.has(h.id)} onChange={() => toggleOne(h.id)} />
              <Poster
                data={makePoster(h.title || filename, null)}
                imgUrl={h.poster_url}
                size="sm"
                shape={h.media_type === 'music' ? 'square' : 'poster'}
              />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className={cn('text-[13.5px] font-semibold', h.undone_at ? 'text-ink-soft line-through' : 'text-ink')}>
                    {h.title || filename}
                  </span>
                  <Badge>{h.operation}</Badge>
                  {h.undone_at ? <BadgeWithDot color="warning">Undone</BadgeWithDot> : null}
                </div>
                <div className="mt-1 flex items-center gap-1.5 text-[11.5px] text-ink-soft [&_svg]:size-3 [&_svg]:shrink-0">
                  <IcFolder /><span className="truncate font-mono" title={h.old_path}>{h.old_path}</span>
                </div>
                <div className="mt-0.5 flex items-center gap-1.5 text-[11.5px] text-ink-muted [&_svg]:size-3 [&_svg]:shrink-0">
                  <IcArrowRight /><span className="truncate font-mono" title={h.new_path}>{h.new_path}</span>
                </div>
              </div>
              <span className="shrink-0 whitespace-nowrap text-[12px] text-ink-soft">{relativeTime(h.created_at)}</span>
              <Button color="secondary" size="sm" iconLeading={IcUndo} isDisabled={!!h.undone_at} onClick={() => void undoOne(h.id)}>
                Undo
              </Button>
            </div>
          );
        })}

        {firstLoadDone && !loading && visibleItems.length === 0 ? (
          q ? (
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
          )
        ) : null}
      </div>
    </div>
  );
}
