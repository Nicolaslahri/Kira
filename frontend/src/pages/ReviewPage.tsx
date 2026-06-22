import { useState, useMemo, useEffect, type ReactNode } from 'react';
import type { AppState, MediaFile, ModalState, LibraryItem } from '../lib/types';
import { IcCheck, IcX, IcSparkles, IcPlay, IcFilm, IcTv, IcAnime, IcMusic, IcSearch } from '../lib/icons';
import { cn } from '../lib/utils';
import { Button } from '../components/base/buttons/button';
import { LibraryGrid } from '../components/LibraryGrid';
import { CoverPopup } from '../components/CoverPopup';
import { ManualSearchModal } from '../components/modals';
import { buildLibraryItems } from '../lib/adapters';
import { confLevel, getConfBands } from '../lib/confBands';

// Colour-coded filter chip — a detached toggle that lights up in its OWN
// semantic colour when active (tinted fill + colour ring + colour label/count),
// and sits as a quiet dark chip when off. `accent` = the option's colour (omit
// for neutral options like Pending / Any / All — they light up white). Replaces
// the old uniform grey segmented pills so each status/confidence/media option
// reads as its own colour-keyed object with its label always visible.
export function FilterChip({ on, onClick, label, num, accent, icon, dot }: {
  on: boolean; onClick: () => void; label: ReactNode;
  num?: number; accent?: string; icon?: ReactNode; dot?: boolean;
}) {
  const c = accent ?? 'var(--accent)';
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={on}
      style={on ? {
        background: `color-mix(in srgb, ${c} 13%, transparent)`,
        boxShadow: `inset 0 0 0 1px color-mix(in srgb, ${c} 34%, transparent)`,
      } : undefined}
      className={cn(
        'group inline-flex h-7 items-center gap-1.5 rounded-lg px-2.5 text-[12.5px] font-medium leading-none outline-brand transition-colors focus-visible:outline-2 focus-visible:outline-offset-2',
        on ? 'text-primary' : 'bg-secondary text-secondary ring-1 ring-inset ring-secondary hover:bg-primary_hover hover:text-primary',
      )}
    >
      {icon ? (
        <span className="inline-flex [&_svg]:size-3.5" style={accent ? { color: accent } : undefined}>{icon}</span>
      ) : dot ? (
        <span className="size-1.5 rounded-full" style={{ background: c, opacity: on ? 1 : 0.7 }} />
      ) : null}
      <span style={on && accent ? { color: accent } : undefined}>{label}</span>
      {num != null ? (
        <span
          className={cn('ml-0.5 min-w-[1.25rem] rounded-md px-1 py-px text-center text-[10.5px] font-semibold tabular-nums', !on && 'bg-tertiary text-tertiary')}
          style={on ? { background: `color-mix(in srgb, ${c} 20%, transparent)`, color: c } : undefined}
        >{num}</span>
      ) : null}
    </button>
  );
}

interface Props {
  state: AppState;
  openModal: (kind: NonNullable<ModalState>['kind'], payload?: unknown) => void;
  focusedId: string;
  setFocusedId: (id: string) => void;
  setFileStatus: (id: string, status: 'approved' | 'rejected' | 'pending') => void | Promise<void>;
  setFileStatusBulk: (ids: string[], status: 'approved' | 'rejected' | 'pending') => void | Promise<void>;
  searchQuery: string;
  pushToast?: (toast: { title: string; sub?: string; kind?: 'success' | 'error' }) => void;
  /** Called when the user picks a show in the bulk "Match all to..." flow.
   *  fileIds = every file in every selected no_match cluster. */
  onBulkManualMatch?: (
    fileIds: string[],
    selection: { title?: string | null; year?: number | null; overview?: string | null; mediaType?: string; _provider?: string; _providerId?: string },
    contextMediaType?: string,
  ) => void | Promise<void>;
  /** One-click rename — uses saved profile + op. The default Approve and
   *  Approve & rename actions now go through this so the user doesn't
   *  have to click through a modal every time. */
  renameFilesDirectly?: (fileIds: string[]) => void | Promise<void>;
}

export function ReviewPage({
  state, openModal, focusedId, setFocusedId,
  setFileStatus, setFileStatusBulk, searchQuery, pushToast, onBulkManualMatch,
  renameFilesDirectly,
}: Props) {
  // Local state for the bulk-match modal — a tiny piece of UI that lives
  // entirely in this page; doesn't go through App's modal system because
  // it has its own onSelect handler (the global one assumes ONE file).
  const [bulkMatchSeed, setBulkMatchSeed] = useState<MediaFile | null>(null);
  const [conf, setConf] = useState<'all' | 'high' | 'mid' | 'low'>('all');
  const [type, setType] = useState<'all' | 'movie' | 'tv' | 'anime' | 'music'>('all');
  // `pending` = anything needing user action: matched-but-unreviewed,
  // mid-match (`matching`), AND no_match files (which the user has to
  // manually point at the right show). `no_match` filter isolates JUST
  // those so the user can sweep through them.
  const [statusF, setStatusF] = useState<'pending' | 'no_match' | 'approved' | 'rejected' | 'renamed' | 'all'>('pending');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  // In-flight flag for the approve-&-rename actions. Drives the buttons'
  // loading spinner so a multi-hundred-ms rename doesn't look like a dead
  // click. A single boolean is enough — the UI only needs "is a rename
  // happening", not which one.
  const [renaming, setRenaming] = useState(false);

  // ── Apply filters to flat files first, then group into LibraryItems ────
  const visibleFiles = useMemo(() => {
    let xs = state.files;
    if (statusF === 'pending') {
      xs = xs.filter(f => f.status === 'pending' || f.status === 'matching' || f.status === 'no_match');
    } else if (statusF !== 'all') {
      xs = xs.filter(f => f.status === statusF);
    }
    if (conf !== 'all') {
      xs = xs.filter(f => {
        // no_match files have confidence 0 — surface them in "Low" so
        // users filtering by confidence can find them too. Bands come from
        // the user's Confidence sliders via confLevel(), not hardcoded 85/50.
        const lvl = confLevel(f.confidence);
        if (conf === 'high') return lvl === 'high' && f.status !== 'no_match';
        if (conf === 'mid')  return lvl === 'mid' && f.status !== 'no_match';
        if (conf === 'low')  return lvl === 'low' || f.status === 'no_match';
        return true;
      });
    }
    if (type !== 'all') xs = xs.filter(f => f.mediaType === type);

    const query = searchQuery.trim().toLowerCase();
    if (query) {
      xs = xs.filter(f => {
        const hay = [
          f.filename, f.folder,
          f.match?.title, f.match?.artist, f.match?.album, f.match?.trackTitle,
        ].filter(Boolean).join(' ').toLowerCase();
        return hay.includes(query);
      });
    }
    return xs;
  }, [state.files, conf, type, statusF, searchQuery]);

  // ── Group into library items (series cards + singletons) ──────────────
  // `items` is keyed by series_key + file.id so it stays stable across
  // re-renders unless the underlying file set changes.
  const items: LibraryItem[] = useMemo(
    () => buildLibraryItems(visibleFiles),
    [visibleFiles]
  );

  // Build a SECOND view of items from ALL files (no status filter) so the
  // popup can render the full cluster — approved/renamed/rejected files
  // stay visible inside the popup even when filtered out of the grid.
  // Without this, approving or renaming an episode makes its file vanish
  // from the popup row, which reads as "the rename broke something".
  const allItemsById: Map<string, LibraryItem> = useMemo(() => {
    const arr = buildLibraryItems(state.files);
    return new Map(arr.map(it => [it.id, it]));
  }, [state.files]);

  // ── Local optimistic state for the popup. When the user clicks a cover
  // we snapshot the LibraryItem; per-row mutations inside the popup update
  // both the local copy (so the popup updates instantly) AND fire the
  // backend mutation in the background.
  const [popup, setPopup] = useState<{ item: LibraryItem; rect: DOMRect } | null>(null);

  // Refresh the popup's snapshot whenever the underlying files change.
  // Use the UNFILTERED `allItemsById` so the popup keeps showing
  // approved/renamed files — they're still part of the cluster, just
  // hidden from the grid view.
  //
  // Falls back to a seriesKey + file-overlap match when the id
  // misses. This happens after a Sonarr Force Import: a previously-
  // unmatched cluster gains its first matched file, the LibraryItem
  // id flips from `lib_<seriesKey>` → `lib_<seriesKey>_<provider>_<id>`,
  // and the popup's stored id no longer points anywhere. Without the
  // fallback, the popup stays frozen showing "Just imported" forever
  // even though the real file row is sitting in the new cluster.
  // Index file-id → its current LibraryItem so the popup re-sync fallback is
  // O(popup's files) instead of re-scanning every cluster's files each tick.
  const fileIdToItem = useMemo(() => {
    const m = new Map<string, LibraryItem>();
    for (const it of allItemsById.values())
      for (const f of it.files) m.set(f.id, it);
    return m;
  }, [allItemsById]);

  useEffect(() => {
    if (!popup) return;
    let fresh = allItemsById.get(popup.item.id);
    if (!fresh) {
      // The item id is `lib_<seriesKey>_<provider>_<providerId>`, so a manual
      // re-match (which flips provider/providerId) CHANGES the id — and movies
      // have no seriesKey (Nobody 2). Match instead by FILE-ID OVERLAP: the
      // popup's files are stable across re-matches (only their match changes),
      // so whichever cluster now holds the most of them IS the fresh item.
      // This re-syncs movies, id-changed clusters, and even media_type shifts
      // (e.g. a file moving TV → Anime after pinning an AniDB show).
      const overlapByItem = new Map<LibraryItem, number>();
      for (const f of popup.item.files) {
        const it = fileIdToItem.get(f.id);
        if (it) overlapByItem.set(it, (overlapByItem.get(it) ?? 0) + 1);
      }
      let bestOverlap = 0;
      for (const [it, n] of overlapByItem) {
        if (n > bestOverlap) { bestOverlap = n; fresh = it; }
      }
      if (bestOverlap === 0) fresh = undefined;
    }
    if (fresh && fresh !== popup.item) {
      setPopup(p => p ? { ...p, item: fresh as LibraryItem } : p);
    }
  }, [allItemsById, popup, fileIdToItem]);

  // User feedback: do NOT auto-switch tabs after a rename. The previous
  // behavior jumped to the "Renamed" filter automatically, which was
  // jarring — the user lost their place on whichever tab they were
  // browsing. We still clear the selection (so the next rename batch
  // doesn't carry stale picks) but leave statusF where the user put it.
  useEffect(() => {
    const onRename = () => {
      setSelected(new Set()); // clear stale selection
    };
    window.addEventListener('kira:rename-success', onRename);
    return () => window.removeEventListener('kira:rename-success', onRename);
  }, []);

  // ── Counts for filter pills ───────────────────────────────────────────
  const counts = useMemo(() => {
    const inStatus = state.files.filter(f => {
      if (statusF === 'all') return true;
      if (statusF === 'pending') return f.status === 'pending' || f.status === 'matching' || f.status === 'no_match';
      return f.status === statusF;
    });
    return {
      all: inStatus.length,
      high: inStatus.filter(f => confLevel(f.confidence) === 'high' && f.status !== 'no_match').length,
      mid:  inStatus.filter(f => confLevel(f.confidence) === 'mid' && f.status !== 'no_match').length,
      low:  inStatus.filter(f => confLevel(f.confidence) === 'low' || f.status === 'no_match').length,
      movie: inStatus.filter(f => f.mediaType === 'movie').length,
      tv:    inStatus.filter(f => f.mediaType === 'tv').length,
      anime: inStatus.filter(f => f.mediaType === 'anime').length,
      music: inStatus.filter(f => f.mediaType === 'music').length,
    };
  }, [state.files, statusF]);

  const statusCounts = useMemo(() => ({
    all: state.files.length,
    // Pending bucket now includes no_match — those NEED user action
    // (manual search), so they belong in the default review queue.
    pending: state.files.filter(f =>
      f.status === 'pending' || f.status === 'matching' || f.status === 'no_match'
    ).length,
    noMatch: state.files.filter(f => f.status === 'no_match').length,
    approved: state.files.filter(f => f.status === 'approved').length,
    rejected: state.files.filter(f => f.status === 'rejected').length,
    renamed: state.files.filter(f => f.status === 'renamed').length,
  }), [state.files]);

  // ── Selection helpers ─────────────────────────────────────────────────
  // Selection is on LibraryItem.id (not file.id) so card clicks map cleanly
  // to the whole cluster. When the user hits Apply we expand each item back
  // to its file ids.
  const selectedFileIds = useMemo(() => {
    const ids: string[] = [];
    items.forEach(it => {
      if (selected.has(it.id)) it.files.forEach(f => ids.push(f.id));
    });
    return ids;
  }, [items, selected]);

  // Subset of selected items that have NO match — drives the "Match all
  // to..." bulk affordance. Composite count of underlying files because
  // a single no_match card may cluster 10+ files (e.g. One Pace Season 14
  // has 21 episodes all parsed as the same cluster).
  const selectedNoMatchInfo = useMemo(() => {
    const noMatchItems = items.filter(it => selected.has(it.id) && it.noMatch);
    const fileIds: string[] = [];
    noMatchItems.forEach(it => it.files.forEach(f => fileIds.push(f.id)));
    const firstFile = noMatchItems[0]?.files[0];
    const seed = firstFile ? state.files.find(f => f.id === firstFile.id) ?? null : null;
    return { items: noMatchItems, fileIds, seed };
  }, [items, selected, state.files]);

  const selectHighConf = () => {
    const next = new Set<string>();
    items
      .filter(it => !it.noMatch && !it.matchingState && it.files.every(f => f.status === 'pending') &&
                    it.files.reduce((s, f) => s + f.confidence, 0) / it.files.length >= getConfBands().high)
      .forEach(it => next.add(it.id));
    setSelected(next);
  };

  // ── Backend mutation wrappers ─────────────────────────────────────────
  // "Approve" = approve + rename in one shot. The previous "approve only
  // status flip" was useless on its own — the file just sat there in
  // approved limbo, never reaching disk, never appearing in History.
  // Now the green check does the whole thing using saved profile + op.
  const approveItem = async (item: LibraryItem) => {
    const ids = item.files.filter(f => f.matchedToEpisode != null).map(f => f.id);
    if (!ids.length) return;
    await setFileStatusBulk(ids, 'approved');
    if (renameFilesDirectly) await renameFilesDirectly(ids);
  };
  const rejectItem = (item: LibraryItem) => {
    const ids = item.files.map(f => f.id);
    void setFileStatusBulk(ids, 'rejected');
    // Reject was the one mutation with no success feedback — approve renames
    // (which toasts), manual match toasts, but a reject just silently greyed
    // the card. Confirm it landed.
    pushToast?.({
      title: `Rejected ${ids.length === 1 ? item.title || 'item' : `${ids.length} files`}`,
      kind: 'error',
    });
  };
  const manualSearchItem = (item: LibraryItem, _epIdx?: number | null, fileIdx?: number | null) => {
    // Choose the file to seed the Manual Search modal with. When the user
    // clicked an episode/orphan-specific action we get a fileIdx; otherwise
    // we seed with the highest-confidence file in the cluster so the search
    // box is pre-filled with the cleanest filename.
    const target = (fileIdx != null && item.files[fileIdx])
      ? item.files[fileIdx]
      : [...item.files].sort((a, b) => b.confidence - a.confidence)[0];
    const file = state.files.find(f => f.id === target?.id);
    if (file) openModal('manualSearch', file);
  };

  // Popup-local handler — fires individual setFileStatus calls per changed file.
  const handleUpdateItem = (next: LibraryItem) => {
    setPopup(p => p && p.item.id === next.id ? { ...p, item: next } : p);
    const prev = popup?.item;
    if (!prev) return;
    // Diff by file id, not array position — a re-match/reorder between
    // snapshots could otherwise compare a file against an unrelated sibling
    // and fire a status change against the wrong file.
    const prevById = new Map(prev.files.map(f => [f.id, f]));
    next.files.forEach((nf) => {
      const pf = prevById.get(nf.id);
      if (pf && pf.status !== nf.status) {
        if (nf.status === 'approved' || nf.status === 'rejected' || nf.status === 'pending') {
          void setFileStatus(nf.id, nf.status);
        }
      }
    });
  };

  const handleOpenCover = (item: LibraryItem, coverEl: HTMLElement) => {
    // Open the popup with the UNFILTERED cluster so all files (incl.
    // approved/renamed) are visible inside. The grid card stays filtered.
    const full = allItemsById.get(item.id) ?? item;
    setPopup({ item: full, rect: coverEl.getBoundingClientRect() });
  };

  // ── Scan progress relayed from AppState for the floating banner ───────
  const scanRunning = state.scanRunning;
  const scanProgress = state.scanProgress;
  const scanMessage = state.scanMessage;
  const scanFound = state.scanFound;

  // Hide the toolbar entirely when the library is genuinely empty (no
  // files scanned yet). The EmptyLibraryHero below offers the 3-step
  // setup CTAs; the toolbar's "0 pending · 0 approved · …" stats,
  // filter pills (all reading 0), and "Select high-confidence" /
  // "Preview rename (0)" buttons are all dead-ended noise pre-scan.
  // Render returns only after a scan has produced at least one file.
  //
  // `isLoading` gates BOTH the toolbar and the empty hero on first
  // mount — without it, the user sees the "Library is empty" hero
  // hero flash for ~200-500ms on every refresh before listFiles
  // resolves and the real library renders.
  const isLoading = !state.hydrated;
  const isLibraryEmpty = !isLoading && state.files.length === 0;

  // Library-progress funnel — the one library-wide orientation cue. A clean
  // partition of the library (renamed + approved + pendingPlain + noMatch +
  // rejected === statusCounts.all): noMatch is a SUBSET of pending, so carve it
  // OUT (subtract, don't add) to keep widths ≤ 100%. Each segment is also a
  // status filter, so the bar reinforces the chips instead of duplicating them.
  const total = Math.max(statusCounts.all, 1);
  const pendingPlain = Math.max(statusCounts.pending - statusCounts.noMatch, 0);
  const funnel = ([
    { key: 'renamed',  n: statusCounts.renamed,  color: 'var(--conf-high)',                                            status: 'renamed',  label: 'Renamed' },
    { key: 'approved', n: statusCounts.approved,  color: 'var(--accent)',                                              status: 'approved', label: 'Approved' },
    { key: 'pending',  n: pendingPlain,           color: 'color-mix(in srgb, var(--conf-mid) 50%, var(--line-strong))', status: 'pending',  label: 'Pending' },
    { key: 'nomatch',  n: statusCounts.noMatch,   color: 'var(--conf-low)',                                            status: 'no_match', label: 'No match' },
    { key: 'rejected', n: statusCounts.rejected,  color: 'color-mix(in srgb, var(--conf-low) 55%, var(--line-strong))', status: 'rejected', label: 'Rejected' },
  ] as { key: string; n: number; color: string; status: 'renamed' | 'approved' | 'pending' | 'no_match' | 'rejected'; label: string }[]).filter(s => s.n > 0);

  return (
    <div className="page">
      {(isLoading || isLibraryEmpty) ? null : (
      <div className="mb-4">
        {/* Filter toolbar — colour-coded chips (FilterChip): each status /
            confidence / media option is a detached chip that lights up in its
            OWN colour when active, label + count always visible. Row 1 = title +
            status + action; row 2 = confidence + media (each a labelled
            cluster). The verbose "544 pending · …" stat line stays dropped —
            every count lives on its chip. */}
        <div className="mb-2.5 flex flex-wrap items-center gap-x-3 gap-y-2">
          <h1 className="mr-1 text-[22px] font-semibold leading-none tracking-tight text-primary">Library</h1>

          {/* Library-progress funnel — the workspace's one library-wide overview.
              Nests in the title row's dead space (≈0 added height); each segment
              is also a status filter, mutually consistent with the chips. */}
          <div className="flex min-w-[150px] max-w-[380px] flex-1 items-center gap-2">
            <div role="group" aria-label="Library progress" className="flex h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-tertiary ring-1 ring-inset ring-secondary">
              {funnel.map(s => (
                <button
                  key={s.key}
                  type="button"
                  onClick={() => setStatusF(s.status)}
                  title={`${s.label}: ${s.n}`}
                  aria-label={`${s.label}: ${s.n} — filter`}
                  style={{ width: `${(s.n / total) * 100}%`, background: s.color }}
                  className="h-full min-w-[2px] outline-brand transition-[width,filter] duration-300 hover:brightness-125 focus-visible:outline-2 focus-visible:outline-offset-2"
                />
              ))}
            </div>
            <span className="shrink-0 whitespace-nowrap text-[11px] font-medium tabular-nums text-tertiary">
              <b className="text-primary">{statusCounts.renamed}</b> / {statusCounts.all} renamed
            </span>
          </div>

          <div className="flex flex-wrap items-center gap-1.5">
            <FilterChip on={statusF === 'pending'}  onClick={() => setStatusF('pending')}  label="Pending"  num={statusCounts.pending} />
            <FilterChip on={statusF === 'no_match'} onClick={() => setStatusF('no_match')} label="No match" num={statusCounts.noMatch} accent="var(--conf-low)" dot />
            <FilterChip on={statusF === 'approved'} onClick={() => setStatusF('approved')} label="Approved" num={statusCounts.approved} />
            <FilterChip on={statusF === 'renamed'}  onClick={() => setStatusF('renamed')}  label="Renamed"  num={statusCounts.renamed} accent="var(--conf-high)" icon={<IcCheck />} />
            <FilterChip on={statusF === 'rejected'} onClick={() => setStatusF('rejected')} label="Rejected" num={statusCounts.rejected} />
            <FilterChip on={statusF === 'all'}      onClick={() => setStatusF('all')}      label="All"      num={statusCounts.all} />
          </div>

          <div className="ml-auto">
            {statusF === 'approved' && statusCounts.approved > 0 ? (
              <Button
                color="primary"
                size="sm"
                iconLeading={IcPlay}
                isLoading={renaming}
                showTextWhileLoading
                onClick={async () => {
                  const ids = state.files.filter(f => f.status === 'approved').map(f => f.id);
                  if (!ids.length || !renameFilesDirectly) return;
                  setRenaming(true);
                  try { await renameFilesDirectly(ids); } finally { setRenaming(false); }
                }}
              >
                Rename {statusCounts.approved} approved
              </Button>
            ) : (
              <Button color="secondary" size="sm" iconLeading={IcSparkles} onClick={selectHighConf}>
                Select high-confidence
              </Button>
            )}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="mr-0.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-quaternary">Confidence</span>
            <FilterChip on={conf === 'all'}  onClick={() => setConf('all')}  label="Any" num={counts.all} />
            <FilterChip on={conf === 'high'} onClick={() => setConf('high')} label="Strong" num={counts.high} accent="var(--conf-high)" dot />
            <FilterChip on={conf === 'mid'}  onClick={() => setConf('mid')}  label="Needs review" num={counts.mid} accent="var(--conf-mid)" dot />
            <FilterChip on={conf === 'low'}  onClick={() => setConf('low')}  label="Low" num={counts.low} accent="var(--conf-low)" dot />
          </div>

          <span aria-hidden className="hidden h-5 w-px bg-white/10 sm:block" />

          <div className="flex flex-wrap items-center gap-1.5">
            <span className="mr-0.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-quaternary">Media</span>
            <FilterChip on={type === 'all'}   onClick={() => setType('all')}   label="All" />
            <FilterChip on={type === 'movie'} onClick={() => setType('movie')} label="Movies" num={counts.movie} accent="#4ec5b3" icon={<IcFilm />} />
            <FilterChip on={type === 'tv'}    onClick={() => setType('tv')}    label="TV" num={counts.tv} accent="#b3e5fc" icon={<IcTv />} />
            <FilterChip on={type === 'anime'} onClick={() => setType('anime')} label="Anime" num={counts.anime} accent="var(--media-anime)" icon={<IcAnime />} />
            <FilterChip on={type === 'music'} onClick={() => setType('music')} label="Music" num={counts.music} accent="var(--media-music)" icon={<IcMusic />} />
          </div>
        </div>
      </div>
      )}

      {selected.size > 0 ? (
        <div className="mb-3 flex flex-wrap items-center gap-3 rounded-xl bg-secondary px-4 py-2.5" style={{ boxShadow: 'inset 0 0 0 1px color-mix(in srgb, var(--accent) 45%, transparent)' }}>
          <div className="flex items-center gap-2 text-[13px] text-primary">
            <span className="grid size-5 place-items-center rounded-md text-white [&_svg]:size-3" style={{ background: 'var(--accent-deep)' }} aria-hidden="true"><IcCheck /></span>
            <b className="font-semibold tabular-nums">{selected.size} selected</b>
            {selectedNoMatchInfo.items.length > 0 ? (
              <span className="text-[12px] text-secondary">
                · {selectedNoMatchInfo.items.length} need matching ({selectedNoMatchInfo.fileIds.length} files)
              </span>
            ) : null}
          </div>
          {/* Left → right: Clear (escape) → Reject (destructive) → divider →
              Match N (curative) → Preview (inspect) → Approve (commit). */}
          <div className="ml-auto flex flex-wrap items-center gap-2">
            <Button color="secondary" size="sm" onClick={() => setSelected(new Set())}>Clear</Button>
            <Button
              color="secondary-destructive"
              size="sm"
              iconLeading={IcX}
              onClick={() => {
                const n = selectedFileIds.length;
                void setFileStatusBulk(selectedFileIds, 'rejected');
                setSelected(new Set());
                pushToast?.({ title: `Rejected ${n} file${n === 1 ? '' : 's'}`, kind: 'error' });
              }}
            >Reject</Button>

            <span aria-hidden="true" className="mx-1 h-5 w-px bg-[var(--accent-line)]" />

            {selectedNoMatchInfo.items.length > 0 && selectedNoMatchInfo.seed ? (
              <Button
                color="secondary"
                size="sm"
                iconLeading={IcSearch}
                onClick={() => setBulkMatchSeed(selectedNoMatchInfo.seed)}
              >Match {selectedNoMatchInfo.fileIds.length} files to…</Button>
            ) : null}
            <Button
              color="secondary"
              size="sm"
              iconLeading={IcPlay}
              title="Open preview modal to customize op/profile before renaming"
              onClick={() => openModal('renamePreview', state.files.filter(f => selectedFileIds.includes(f.id)))}
            >Preview rename</Button>
            <Button
              color="primary"
              size="sm"
              iconLeading={IcCheck}
              isLoading={renaming}
              showTextWhileLoading
              onClick={async () => {
                // Approve + rename in one shot using saved profile + op. On the
                // Approved tab the approve is a no-op (already approved), so this
                // is effectively just "rename" — hence the label below.
                const ids = selectedFileIds;
                if (!ids.length) return;
                setRenaming(true);
                try {
                  await setFileStatusBulk(ids, 'approved');
                  if (renameFilesDirectly) await renameFilesDirectly(ids);
                  setSelected(new Set());
                } finally {
                  setRenaming(false);
                }
              }}
            >{statusF === 'approved' ? 'Rename' : 'Approve & rename'} ({selectedFileIds.length})</Button>
          </div>
        </div>
      ) : null}

      <LibraryGrid
        items={items}
        selected={selected}
        setSelected={setSelected}
        focusedId={focusedId}
        setFocusedId={setFocusedId}
        totalLibrarySize={state.files.length}
        hydrated={state.hydrated}
        onClearFilters={() => {
          setStatusF('pending');
          setConf('all');
          setType('all');
          setSelected(new Set());
        }}
        scanRunning={scanRunning}
        scanProgress={scanProgress}
        scanMessage={scanMessage}
        scanFound={scanFound}
        onOpenCover={handleOpenCover}
        onApprove={approveItem}
        onReject={rejectItem}
        onManualSearch={manualSearchItem}
      />

      {popup ? (
        <CoverPopup
          item={popup.item}
          originRect={popup.rect}
          onClose={() => setPopup(null)}
          onUpdateItem={handleUpdateItem}
          onManualSearch={manualSearchItem}
          pushToast={pushToast}
          renameFilesDirectly={renameFilesDirectly}
        />
      ) : null}

      {bulkMatchSeed ? (
        <ManualSearchModal
          file={bulkMatchSeed}
          onClose={() => setBulkMatchSeed(null)}
          onSelect={async (sel) => {
            // Pin every file in every selected no_match cluster to this show.
            if (onBulkManualMatch) {
              await onBulkManualMatch(
                selectedNoMatchInfo.fileIds,
                sel,
                bulkMatchSeed.mediaType,
              );
            }
            setBulkMatchSeed(null);
            setSelected(new Set());
          }}
        />
      ) : null}
    </div>
  );
}

// Marker so the file-level void below works even when no media is in scope.
void ({} as MediaFile);
