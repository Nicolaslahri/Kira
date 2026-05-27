import { useEffect, useState } from 'react';
import type { ToastData } from '../lib/types';
import { api, type ApiHistoryEntry } from '../lib/api';
import { poster as makePoster } from '../lib/data';
import { IcDownload, IcUndo, IcFolder, IcArrowRight, IcHistory } from '../lib/icons';
import { Poster, Checkbox, FilterPill, EmptyState, Skeleton } from '../components/ui';
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

  const refresh = async () => {
    setLoading(true);
    try {
      const [rows, c] = await Promise.all([
        api.listHistory({ period, operation: opFilter === 'all' ? undefined : opFilter }),
        api.historyCounts(),
      ]);
      setItems(rows);
      setCounts(c);
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
  const allChecked = items.length > 0 && items.every(i => selected.has(i.id));
  const someChecked = items.some(i => selected.has(i.id));
  const toggleAll = () => {
    if (allChecked) setSelected(new Set());
    else setSelected(new Set(items.map(i => i.id)));
  };

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">History</h1>
          <p className="page-sub">Every rename Kira has performed · undo at any time</p>
        </div>
        <div className="flex gap-2">
          <a
            className="btn"
            href={api.exportHistoryUrl()}
            download
            style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
          >
            <IcDownload /> Export log (.csv)
          </a>
        </div>
      </div>

      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        <div className="review-toolbar">
          <div className="filter-group">
            <FilterPill on={period === 'today'} label="Today" num={counts.today} onClick={() => setPeriod('today')} />
            <FilterPill on={period === 'week'} label="This week" num={counts.week} onClick={() => setPeriod('week')} />
            <FilterPill on={period === 'all'} label="All time" num={counts.all} onClick={() => setPeriod('all')} />
          </div>

          <div className="toolbar-divider" />

          <div className="filter-group">
            <FilterPill on={opFilter === 'all'} label="All operations" onClick={() => setOpFilter('all')} />
            <FilterPill on={opFilter === 'move'} label="Move" onClick={() => setOpFilter('move')} />
            <FilterPill on={opFilter === 'hardlink'} label="Hardlink" onClick={() => setOpFilter('hardlink')} />
            <FilterPill on={opFilter === 'symlink'} label="Symlink" onClick={() => setOpFilter('symlink')} />
            <FilterPill on={opFilter === 'copy'} label="Copy" onClick={() => setOpFilter('copy')} />
          </div>

          <div className="ml-auto flex items-center gap-2">
            {selected.size > 0 ? (
              <>
                <span className="text-sm text-muted">{selected.size} selected</span>
                <button className="btn btn-sm btn-danger" onClick={() => void undoBatch()}><IcUndo /> Undo selected</button>
              </>
            ) : null}
          </div>
        </div>

        <div className="row row-header" style={{ gridTemplateColumns: '36px 56px 1fr auto auto', padding: '8px 20px' }}>
          <Checkbox on={allChecked} indeterminate={!allChecked && someChecked} onChange={toggleAll} />
          <span />
          <span>Operation</span>
          <span>Time</span>
          <span style={{ textAlign: 'right', width: 90 }}>Action</span>
        </div>

        {!firstLoadDone ? (
          // Skeleton rows mirror the real history-row layout (checkbox,
          // poster, paths block, timestamp, button) so the page has real
          // visual presence during the initial fetch instead of a
          // collapsed empty card. Renders 4 rows by default — enough to
          // fill the visible card area without scrolling.
          <>
            {[0, 1, 2, 3].map(i => (
              <div key={`sk-${i}`} className="history-row" style={{ gridTemplateColumns: '36px 56px 1fr auto auto', gap: 16, pointerEvents: 'none' }}>
                <Skeleton w={18} h={18} radius={4} />
                <Skeleton w={48} h={64} radius={6} />
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  <Skeleton w={'52%'} h={14} />
                  <Skeleton w={'78%'} h={11} />
                  <Skeleton w={'66%'} h={11} />
                </div>
                <Skeleton w={70} h={11} />
                <Skeleton w={64} h={26} radius={6} />
              </div>
            ))}
          </>
        ) : null}

        {items.map(h => {
          const filename = h.new_path.split(/[\\/]/).pop() || h.new_path;
          return (
            <div key={h.id} className="history-row" style={{ gridTemplateColumns: '36px 56px 1fr auto auto', gap: 16 }}>
              <Checkbox on={selected.has(h.id)} onChange={() => toggleOne(h.id)} />
              <Poster
                data={makePoster(h.title || filename, null)}
                imgUrl={h.poster_url}
                size="sm"
                shape={h.media_type === 'music' ? 'square' : 'poster'}
              />
              <div className="history-paths">
                <div className="flex items-center gap-2" style={{ marginBottom: 2 }}>
                  <span className="font-semibold text-sm" style={h.undone_at ? { textDecoration: 'line-through', color: 'var(--ink-3)' } : undefined}>{h.title || filename}</span>
                  <span className="badge badge-neutral" style={{ padding: '1px 6px', fontSize: 10 }}>{h.operation}</span>
                  {h.undone_at ? (
                    // Bumped from dim gray to amber so users don't try
                    // to undo something that's already been undone. Used
                    // to be `color: var(--ink-3)` at 10px — invisible.
                    <span
                      className="badge"
                      style={{
                        padding: '2px 8px', fontSize: 11, fontWeight: 600,
                        background: 'rgba(255,201,74,0.14)',
                        color: 'var(--conf-mid)',
                        border: '1px solid rgba(255,201,74,0.34)',
                        borderRadius: 6,
                      }}
                    >↶ Undone</span>
                  ) : null}
                </div>
                <div className="history-path old" title={h.old_path}>
                  <IcFolder className="ico" /><span>{h.old_path}</span>
                </div>
                <div className="history-path new" title={h.new_path}>
                  <IcArrowRight className="ico" /><span>{h.new_path}</span>
                </div>
              </div>
              <span className="text-sm text-muted" style={{ whiteSpace: 'nowrap' }}>{relativeTime(h.created_at)}</span>
              <button
                className="btn btn-sm"
                onClick={() => void undoOne(h.id)}
                disabled={!!h.undone_at}
              >
                <IcUndo /> Undo
              </button>
            </div>
          );
        })}

        {firstLoadDone && !loading && items.length === 0 ? (
          <EmptyState
            icon={<IcHistory />}
            title="Nothing renamed yet"
            sub="Approve files in the Review queue and click Apply to populate this log."
          />
        ) : null}
      </div>
    </div>
  );
}
