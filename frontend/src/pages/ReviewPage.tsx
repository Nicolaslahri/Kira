import { useState, useMemo, useEffect } from 'react';
import type { AppState, MediaFile, ModalState, LibraryItem } from '../lib/types';
import { IcCheck, IcX, IcSparkles, IcPlay, IcFilm, IcTv, IcAnime, IcMusic, IcSearch } from '../lib/icons';
import { FilterPill } from '../components/ui';
import { LibraryGrid } from '../components/LibraryGrid';
import { CoverPopup } from '../components/CoverPopup';
import { ManualSearchModal } from '../components/modals';
import { buildLibraryItems } from '../lib/adapters';

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
        // users filtering by confidence can find them too.
        if (conf === 'high') return f.confidence >= 85 && f.status !== 'no_match';
        if (conf === 'mid')  return f.confidence >= 50 && f.confidence < 85 && f.status !== 'no_match';
        if (conf === 'low')  return f.confidence < 50 || f.status === 'no_match';
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
  useEffect(() => {
    if (!popup) return;
    const fresh = allItemsById.get(popup.item.id);
    if (fresh && fresh !== popup.item) {
      setPopup(p => p ? { ...p, item: fresh } : p);
    }
  }, [allItemsById, popup]);

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
      high: inStatus.filter(f => f.confidence >= 85 && f.status !== 'no_match').length,
      mid:  inStatus.filter(f => f.confidence >= 50 && f.confidence < 85 && f.status !== 'no_match').length,
      low:  inStatus.filter(f => f.confidence < 50 || f.status === 'no_match').length,
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
                    it.files.reduce((s, f) => s + f.confidence, 0) / it.files.length >= 85)
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
    void setFileStatusBulk(item.files.map(f => f.id), 'rejected');
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
    next.files.forEach((nf, i) => {
      const pf = prev.files[i];
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

  return (
    <div className="page">
      {(isLoading || isLibraryEmpty) ? null : (
      <div className="lib-header">
        <div className="lib-header-top">
          <div>
            <h1 className="page-title">Library</h1>
            <p className="page-sub">
              <span className="sub-strong">{statusCounts.pending}</span> pending
              {statusCounts.noMatch > 0 ? (
                <> (<span className="sub-strong" style={{ color: 'var(--conf-low)' }}>{statusCounts.noMatch}</span> no match)</>
              ) : null}
              {' · '}
              <span className="sub-strong">{statusCounts.approved}</span> approved ·{' '}
              <span className="sub-strong" style={{ color: 'var(--conf-high)' }}>{statusCounts.renamed}</span> renamed ·{' '}
              <span className="sub-strong">{statusCounts.rejected}</span> rejected
            </p>
          </div>
          <div className="lib-header-actions">
            {statusF === 'approved' && statusCounts.approved > 0 ? (
              // Stuck-approved sweep: when looking at the Approved view,
              // a single button to push every approved file through rename.
              // Saves the user from having to select-all + click again.
              <button
                className="btn btn-primary"
                onClick={async () => {
                  const ids = state.files
                    .filter(f => f.status === 'approved')
                    .map(f => f.id);
                  if (ids.length && renameFilesDirectly) await renameFilesDirectly(ids);
                }}
              >
                <IcPlay /> Rename {statusCounts.approved} approved
              </button>
            ) : (
              // Just the selector — the redundant "Preview rename (0)"
              // button used to sit here, but it was disabled until the
              // user clicked Select high-confidence, AND the action bar
              // that appears post-selection already has its own Preview
              // button. Two preview buttons in the same flow was UI
              // noise. The action bar is the single source of truth for
              // any selection-based action (preview, approve, reject,
              // manual search) once anything is selected.
              <button className="btn btn-primary" onClick={selectHighConf}>
                <IcSparkles /> Select high-confidence
              </button>
            )}
          </div>
        </div>

        <div className="lib-header-filters">
          <div className="filter-group">
            <FilterPill on={statusF === 'pending'}  onClick={() => setStatusF('pending')}  label="Pending"  num={statusCounts.pending} />
            <FilterPill
              on={statusF === 'no_match'}
              onClick={() => setStatusF('no_match')}
              label={<span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}><span className="dot" style={{ display: 'inline-block', width: 6, height: 6, borderRadius: '50%', background: 'var(--conf-low)' }} />No match</span>}
              num={statusCounts.noMatch}
            />
            <FilterPill on={statusF === 'approved'} onClick={() => setStatusF('approved')} label="Approved" num={statusCounts.approved} />
            <FilterPill
              on={statusF === 'renamed'}
              onClick={() => setStatusF('renamed')}
              label={<span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}><IcCheck style={{ width: 11, height: 11, color: 'var(--conf-high)' }} />Renamed</span>}
              num={statusCounts.renamed}
            />
            <FilterPill on={statusF === 'rejected'} onClick={() => setStatusF('rejected')} label="Rejected" num={statusCounts.rejected} />
            <FilterPill on={statusF === 'all'}      onClick={() => setStatusF('all')}      label="All"      num={statusCounts.all} />
          </div>

          <div className="filter-group">
            <FilterPill on={conf === 'all'}  onClick={() => setConf('all')}  label="Any" num={counts.all} />
            <FilterPill on={conf === 'high'} onClick={() => setConf('high')} label={<span><span className="dot" style={{ display: 'inline-block', width: 6, height: 6, borderRadius: '50%', background: 'var(--conf-high)', marginRight: 6 }} />Strong</span>} num={counts.high} />
            {/* F-04: pill labeled "Needs review" — the prior "Review"
                label collided with the sidebar nav item AND the
                "Review queue" breadcrumb in the same viewport, forcing
                the user to context-switch each time the word appeared. */}
            <FilterPill on={conf === 'mid'}  onClick={() => setConf('mid')}  label={<span><span className="dot" style={{ display: 'inline-block', width: 6, height: 6, borderRadius: '50%', background: 'var(--conf-mid)', marginRight: 6 }} />Needs review</span>} num={counts.mid} />
            <FilterPill on={conf === 'low'}  onClick={() => setConf('low')}  label={<span><span className="dot" style={{ display: 'inline-block', width: 6, height: 6, borderRadius: '50%', background: 'var(--conf-low)', marginRight: 6 }} />Low</span>}  num={counts.low} />
          </div>

          <div className="filter-group">
            <FilterPill on={type === 'all'}   onClick={() => setType('all')}   label="All media" />
            <FilterPill on={type === 'movie'} onClick={() => setType('movie')} label={<span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}><IcFilm style={{ width: 11, height: 11 }} />Movies</span>}  num={counts.movie} />
            <FilterPill on={type === 'tv'}    onClick={() => setType('tv')}    label={<span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}><IcTv style={{ width: 11, height: 11 }} />TV</span>}      num={counts.tv} />
            <FilterPill on={type === 'anime'} onClick={() => setType('anime')} label={<span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, color: type === 'anime' ? '#c89bff' : undefined }}><IcAnime style={{ width: 11, height: 11 }} />Anime</span>} num={counts.anime} />
            <FilterPill on={type === 'music'} onClick={() => setType('music')} label={<span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, color: type === 'music' ? '#ffb14a' : undefined }}><IcMusic style={{ width: 11, height: 11 }} />Music</span>} num={counts.music} />
          </div>
        </div>
      </div>
      )}

      {selected.size > 0 ? (
        <div className="bulkbar" style={{ borderRadius: 12, marginBottom: 12 }}>
          <div>
            <b>{selected.size} selected</b>
            {selectedNoMatchInfo.items.length > 0 ? (
              <span style={{ color: 'var(--ink-3)', marginLeft: 8, fontSize: 12 }}>
                · {selectedNoMatchInfo.items.length} need matching ({selectedNoMatchInfo.fileIds.length} files)
              </span>
            ) : null}
          </div>
          <div className="ml-auto flex gap-2" style={{ alignItems: 'center' }}>
            {/* Left → right flow:
                Clear (escape) → Reject (destructive) → divider →
                Match N (curative) → Preview (inspect) → Approve (commit).
                All buttons cluster at the right edge via the parent's
                ml-auto; the thin divider just gives a visual breath
                between the dismissive pair and the commit pipeline. */}
            <button className="btn btn-sm" onClick={() => setSelected(new Set())}>Clear</button>
            <button
              className="btn btn-sm btn-danger"
              onClick={() => {
                void setFileStatusBulk(selectedFileIds, 'rejected');
                setSelected(new Set());
              }}
            ><IcX /> Reject</button>

            {/* Thin vertical divider — separates dismissive from commit
                without the chasm a flex-grow spacer would create. */}
            <span
              aria-hidden="true"
              style={{
                width: 1,
                height: 22,
                background: 'rgba(255,255,255,0.12)',
                margin: '0 4px',
                flex: '0 0 auto',
              }}
            />

            {/* "Match all to..." — visible only when no_match cards are
                in the selection. Opens Manual Search seeded with the
                first no_match file; on pick, applies to EVERY file
                across every selected no_match cluster via the bulk
                endpoint. Prerequisite for the rest of the pipeline. */}
            {selectedNoMatchInfo.items.length > 0 && selectedNoMatchInfo.seed ? (
              <button
                className="btn btn-sm"
                style={{ borderColor: 'var(--conf-low)', color: 'var(--conf-low)' }}
                onClick={() => setBulkMatchSeed(selectedNoMatchInfo.seed)}
              ><IcSearch /> Match {selectedNoMatchInfo.fileIds.length} files to…</button>
            ) : null}
            {/* "Manual search" used to live here. Removed: it only seeded
                the modal with the FIRST selected item, which (a) silently
                ignored every other cluster in a multi-cluster selection,
                and (b) duplicated the cover magnify icon for the
                single-cluster case. */}
            <button
              className="btn btn-sm"
              title="Open preview modal to customize op/profile before renaming"
              onClick={() => openModal('renamePreview', state.files.filter(f => selectedFileIds.includes(f.id)))}
            ><IcPlay /> Preview rename</button>
            <button
              className="btn btn-sm btn-primary"
              onClick={async () => {
                // Approve + rename in one shot. Direct rename uses saved
                // profile + op; no modal, no second click. Files immediately
                // go through approved → renamed and show up in History.
                const ids = selectedFileIds;
                if (!ids.length) return;
                await setFileStatusBulk(ids, 'approved');
                if (renameFilesDirectly) await renameFilesDirectly(ids);
                setSelected(new Set());
              }}
            ><IcCheck /> Approve & rename ({selectedFileIds.length})</button>
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
