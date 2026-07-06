import { useState, useMemo, useEffect, useRef, type ReactNode } from 'react';
import type { AppState, MediaFile, ModalState, LibraryItem } from '../lib/types';
import { IcCheck, IcX, IcSparkles, IcPlay, IcFilm, IcTv, IcAnime, IcMusic, IcSearch } from '../lib/icons';
import { cn } from '../lib/utils';
import { Button } from '../components/base/buttons/button';
import { LibraryGrid } from '../components/LibraryGrid';
import { CoverPopup } from '../components/CoverPopup';
import { ManualSearchModal } from '../components/modals';
import { buildLibraryItems } from '../lib/adapters';
import { confLevel, getConfBands } from '../lib/confBands';
import { api, posterSrc } from '../lib/api';
import { poster } from '../lib/data';

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
          // key={num}: remount on change so the pop animation replays — the
          // count visibly "ticks" when files resolve during a scan.
          key={num}
          className={cn('anim-pop ml-0.5 min-w-[1.25rem] rounded-md px-1 py-px text-center text-[10.5px] font-semibold tabular-nums', !on && 'bg-tertiary text-tertiary')}
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
  /** Switch a file to a different match candidate (POST /files/{id}/select/{matchId}). */
  onPickCandidate?: (fileId: string, candidate: { matchId?: number; title?: string; year?: number | null }) => void | Promise<void>;
}

// A "collection gap" = the missing parts of a TMDB collection you partially own
// (from GET /collections). ReviewPage merges these into the grid: owned films get
// the collection band key; missing parts become ghost cards.
type CollectionGap = {
  collection_id: string;
  name: string | null;
  owned: number;
  total: number;
  missing: Array<{ tmdb_id: string; title: string | null; year: number | null; poster_url: string | null; released: boolean }>;
};

// Persist the last-known collection gaps so a page REFRESH paints the grid WITH the
// collection bands + ghost covers on the FIRST frame — instead of fetching them
// ~300ms later and merging, which relocates owned films into a band and scrolls the
// viewport onto the collections (the "randomly scrolled to collections" bug). The
// background fetch then corrects only on a genuine change (the sig guard makes an
// unchanged result a no-op, so the cache hit never reflows).
const COLLECTIONS_CACHE_KEY = 'kira:collections-v1';
function collectionsSig(cs: CollectionGap[]): string {
  return JSON.stringify([...cs].sort((a, b) => a.collection_id.localeCompare(b.collection_id)));
}
function readCachedCollections(): CollectionGap[] {
  try {
    const raw = localStorage.getItem(COLLECTIONS_CACHE_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    return Array.isArray(parsed) ? (parsed as CollectionGap[]) : [];
  } catch {
    return [];
  }
}

// Build a GHOST LibraryItem for a collection part the user doesn't own. No files;
// the grid renders it dimmed with a one-click "Get from Radarr". It shares the
// collection's band key so it shelves next to the films they DO have.
function ghostItem(c: CollectionGap, m: CollectionGap['missing'][number], bandKey: string): LibraryItem {
  const title = m.title || 'Untitled';
  return {
    id: `ghost:tmdb:${m.tmdb_id}`,
    kind: 'movie',
    mediaType: 'movie',
    title,
    year: m.year ?? null,
    poster: poster(title, m.year ?? null),
    posterUrl: posterSrc(m.poster_url),
    seriesGroupId: bandKey,
    collectionId: c.collection_id,
    collectionName: c.name,
    ghost: { tmdbId: Number(m.tmdb_id), released: m.released },
    episodes: [],
    files: [],
  };
}

export function ReviewPage({
  state, openModal, focusedId, setFocusedId,
  setFileStatus, setFileStatusBulk, searchQuery, pushToast, onBulkManualMatch,
  renameFilesDirectly, onPickCandidate,
}: Props) {
  // Local state for the bulk-match modal — a tiny piece of UI that lives
  // entirely in this page; doesn't go through App's modal system because
  // it has its own onSelect handler (the global one assumes ONE file).
  const [bulkMatchSeed, setBulkMatchSeed] = useState<MediaFile | null>(null);
  const [conf, setConf] = useState<'all' | 'high' | 'mid' | 'low'>('all');
  const [type, setType] = useState<'all' | 'movie' | 'tv' | 'anime' | 'music'>('all');
  // Sort (§10): default keeps the grid's natural grouping; the others order
  // items inside the section layout by title, confidence, or file size.
  const [sortBy, setSortBy] = useState<'default' | 'title' | 'confidence' | 'size'>('default');
  // Duplicates lens: only cards where 2+ files landed on the same episode
  // slot (or a movie with 2+ files) — the popup's per-episode dupe tooling
  // already handles the cleanup; this makes them FINDABLE in one click.
  const [dupesOnly, setDupesOnly] = useState(false);
  // One-shot deep link from the Dashboard's duplicates stat: arrive with the
  // Duplicates filter already on. sessionStorage (not props) because the
  // Dashboard is unmounted before this page mounts.
  useEffect(() => {
    try {
      if (sessionStorage.getItem('kira.review.dupes') === '1') {
        sessionStorage.removeItem('kira.review.dupes');
        setDupesOnly(true);
      }
    } catch { /* private mode — the filter chip still works manually */ }
  }, []);
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
  // Movie-collection gaps (#14) — populated from GET /collections when Radarr is
  // configured (see the fetch effect below). Drives the ghost covers in the grid.
  // Seed from the localStorage cache so a page refresh paints the grid WITH the
  // collection bands/ghosts on the first frame (no late merge + relocation).
  const [collections, setCollections] = useState<CollectionGap[]>(readCachedCollections);
  // Sig baseline = the cached collections' signature (lazy, once). Lets the fetch
  // skip a no-op setCollections — re-running the displayItems merge would regroup
  // films into a band + re-insert ghosts → reflow the grid + scroll to collections.
  const collectionsSigRef = useRef<string | null>(null);
  if (collectionsSigRef.current === null) collectionsSigRef.current = collectionsSig(collections);

  // ── Apply filters to flat files first, then group into LibraryItems ────
  const visibleFiles = useMemo(() => {
    let xs = state.files;
    // SEARCH SPANS EVERYTHING (§10 M): a query bypasses the status slice —
    // searching for a renamed title used to return "Nothing matches" because
    // only the Pending slice was searched. Status/conf/type chips still apply
    // when the search box is empty.
    const _query = searchQuery.trim().toLowerCase();
    if (_query) {
      return state.files.filter(f => {
        const hay = [
          f.filename, f.folder,
          f.match?.title, f.match?.artist, f.match?.album, f.match?.trackTitle,
        ].filter(Boolean).join(' ').toLowerCase();
        return hay.includes(_query);
      });
    }
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

  // Prune orphaned selection ids whenever the visible items change. Hover
  // approve/reject, a filter switch, or a manual re-match (which mints a NEW
  // item id) all leave `selected` holding ids no longer present — the bulk bar
  // then reads "2 selected / Approve (0)" and silently no-ops. Dropping the
  // dead ids keeps the count honest. No-op (returns prev) when nothing changed
  // so it can't loop.
  useEffect(() => {
    const live = new Set(items.map(it => it.id));
    setSelected(prev => {
      let changed = false;
      const next = new Set<string>();
      prev.forEach(id => { if (live.has(id)) next.add(id); else changed = true; });
      return changed ? next : prev;
    });
  }, [items]);

  // Merge collection gaps into the grid: rewrite a collection's owned films to
  // share a band key (so they shelf together) + append ghost cards for the
  // missing parts. Only collections WITH gaps reach here (the endpoint omits
  // complete ones), so non-collection movies + non-Radarr users are untouched.
  const itemsWithGaps: LibraryItem[] = useMemo(() => {
    if (collections.length === 0) return items;
    const byColl = new Map(collections.map(c => [c.collection_id, c] as const));
    const bandKey = (cid: string) => `tmdb-collection:${cid}`;
    // Rebrand a collection's owned films into the band AND record which
    // collections still have an owned film visible after the active filters
    // (the status / confidence / media-type pills feed `items`).
    const visibleColls = new Set<string>();
    const out: LibraryItem[] = items.map(it => {
      if (it.kind === 'movie' && it.collectionId && byColl.has(it.collectionId)) {
        visibleColls.add(it.collectionId);
        return { ...it, seriesGroupId: bandKey(it.collectionId), collectionName: it.collectionName ?? byColl.get(it.collectionId)!.name };
      }
      return it;
    });
    // Append the missing-film ghosts ONLY for collections whose owned film passed
    // the filters — so the ghost covers FOLLOW the sort/filter pills with their
    // band instead of floating in unconditionally (which orphaned them under the
    // wrong pill / media type and made the collections look unsorted).
    for (const c of collections) {
      if (!visibleColls.has(c.collection_id)) continue;
      const key = bandKey(c.collection_id);
      for (const m of c.missing) out.push(ghostItem(c, m, key));
    }
    return out;
  }, [items, collections]);

  // Sorted view for the grid (§10 sort control). 'default' passes through
  // untouched (stable grouped order). Ghost cards sink to the end for every
  // explicit sort so real files stay in front.
  const sortedItems: LibraryItem[] = useMemo(() => {
    if (sortBy === 'default') return itemsWithGaps;
    const conf = (it: LibraryItem) => Math.max(0, ...it.files.map(f => f.confidence ?? 0));
    const size = (it: LibraryItem) => it.files.reduce((a, f) => a + (f.sizeBytes ?? 0), 0);
    const xs = [...itemsWithGaps];
    xs.sort((a, b) => {
      if (!!a.ghost !== !!b.ghost) return a.ghost ? 1 : -1;
      if (sortBy === 'title') return (a.title || '').localeCompare(b.title || '');
      if (sortBy === 'confidence') return conf(b) - conf(a);
      return size(b) - size(a);
    });
    return xs;
  }, [itemsWithGaps, sortBy]);

  const itemHasDupes = (it: LibraryItem): boolean => {
    if (it.ghost || it.files.length < 2) return false;
    if (it.kind === 'movie') return it.files.length > 1;
    const perSlot = new Map<number, number>();
    for (const f of it.files) {
      if (f.matchedToEpisode == null) continue;
      const n = (perSlot.get(f.matchedToEpisode) ?? 0) + 1;
      if (n > 1) return true;
      perSlot.set(f.matchedToEpisode, n);
    }
    return false;
  };
  const dupeCount = useMemo(() => sortedItems.filter(itemHasDupes).length,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [sortedItems]);
  const displayedItems = useMemo(
    () => (dupesOnly ? sortedItems.filter(itemHasDupes) : sortedItems),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [sortedItems, dupesOnly]);


  // One-click "Get from Radarr" for a ghost cover. Adds the movie (or searches
  // it if already in Radarr) + toasts the outcome; returns the result so the
  // ghost card can flip to "Requested".
  const handleGetMovie = async (tmdbId: number): Promise<{ ok: boolean; detail: string | null }> => {
    try {
      const r = await api.addMovieToRadarr(tmdbId);
      pushToast?.(r.ok
        ? { title: r.added ? 'Added to Radarr' : 'Searching in Radarr', sub: r.detail ?? undefined, kind: 'success' }
        : { title: 'Radarr request failed', sub: r.detail ?? undefined, kind: 'error' });
      return { ok: r.ok, detail: r.detail };
    } catch (e) {
      pushToast?.({ title: 'Radarr request failed', sub: (e as Error).message, kind: 'error' });
      return { ok: false, detail: (e as Error).message };
    }
  };

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

  // Re-sync the popup ONLY when the underlying data (state.files →
  // allItemsById) changes — NOT on every popup change. Depending on `popup`
  // made this effect fire right after handleUpdateItem's OPTIMISTIC setPopup,
  // and since allItemsById was still pre-mutation, it overwrote the optimistic
  // item with the stale one: every per-row approve/reject visibly reverted
  // until the PATCH landed (and invited double-clicks on a slow backend).
  const popupRef2 = useRef(popup);
  popupRef2.current = popup;
  useEffect(() => {
    const popup = popupRef2.current;
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allItemsById, fileIdToItem]);

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

  // Walk-free deletion check on load: ask the backend to drop any review-stage
  // file whose disk copy is gone (POST /files/reconcile). So a file you deleted
  // clears on REFRESH without a scan — and without the scan's "one unreadable
  // folder skips the whole sweep" fragility. Fire-and-forget; a removal fires
  // kira:files-changed, which reloads the list.
  useEffect(() => {
    let cancelled = false;
    void api.reconcileFiles()
      .then(r => {
        if (!cancelled && r.removed > 0) {
          try { window.dispatchEvent(new Event('kira:files-changed')); } catch { /* no window */ }
        }
      })
      .catch(() => { /* best-effort; the scan prune is the backstop */ });
    return () => { cancelled = true; };
  }, []);

  // Latest scanRunning, read via a ref inside the fetch so the effect's deps can
  // stay `[]` (a changing-size deps array trips React's Fast-Refresh rule).
  const scanRunningRef = useRef(state.scanRunning);
  scanRunningRef.current = state.scanRunning;

  // Fetch movie-collection gaps (#14) when Radarr is configured. Mount + on
  // kira:files-changed (a newly-added movie may close a gap). Gated on Radarr so
  // we never tease a gap the user can't act on; best-effort (silent on failure).
  useEffect(() => {
    // Keyed on `state.scanRunning`: the true→false transition (scan finished) is
    // what RE-FETCHES the collections once the data settles. The old `[]` deps +
    // "skip while scanning" meant a fetch that fired during a scan left the ghosts
    // hidden until a full page refresh re-mounted this — the bug the user hit.
    if (state.scanRunning) return;   // mid-scan: no fetch, no listener (cleaned up below)
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const load = async () => {
      try {
        const health = await api.integrationsHealth();
        if (!health['radarr']) return;   // unconfigured / health not yet polled — keep last-known
        const r = await api.getCollections();
        // Accept an empty set ONLY when not mid-scan (movie matches detach+reinsert
        // mid-scan emits a transient-empty that would flash the ghosts away).
        // Otherwise keep the last-known set rather than clearing it.
        if (!cancelled && (r.collections.length > 0 || !scanRunningRef.current)) {
          // Only commit when the data ACTUALLY changed. This effect re-fetches on
          // every kira:files-changed (TV approvals, undo, the scan tail) — none of
          // which alter movie collections — and a fresh array re-runs the merge and
          // reflows the grid (the "randomly scrolled to collections" bug). The sig
          // sorts by id so a non-deterministic backend order can't false-trigger.
          const sig = collectionsSig(r.collections);
          if (sig !== collectionsSigRef.current) {
            collectionsSigRef.current = sig;
            setCollections(r.collections);
            try { localStorage.setItem(COLLECTIONS_CACHE_KEY, JSON.stringify(r.collections)); } catch { /* quota / private mode */ }
          }
        }
      } catch {
        // Transient network/health blip — keep the last-known ghosts rather than
        // flashing them away (the bug was: every blip cleared them until a refresh).
      }
    };
    // Debounce the kira:files-changed burst (the scan tail fires several) into a
    // single settled fetch.
    const schedule = () => { if (timer) clearTimeout(timer); timer = setTimeout(() => void load(), 400); };
    void load();
    window.addEventListener('kira:files-changed', schedule);
    return () => { cancelled = true; if (timer) clearTimeout(timer); window.removeEventListener('kira:files-changed', schedule); };
  }, [state.scanRunning]);

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

  // Eligibility-scoped views of the selection (audit §16 M4/M5): the raw
  // selectedFileIds includes EVERY file of every selected card, so bulk
  // Approve used to shove no_match files into 'approved' limbo (then fail
  // their renames), and bulk Reject flipped already-RENAMED files to
  // 'rejected' (losing their Renamed record though the file moved on disk).
  const selectedApprovableIds = useMemo(() => {
    const ids: string[] = [];
    items.forEach(it => {
      if (!selected.has(it.id)) return;
      it.files.forEach(f => {
        if (f.matchedToEpisode != null && f.status !== 'renamed' && f.status !== 'rejected') ids.push(f.id);
      });
    });
    return ids;
  }, [items, selected]);
  const selectedRejectableIds = useMemo(() => {
    const ids: string[] = [];
    items.forEach(it => {
      if (!selected.has(it.id)) return;
      it.files.forEach(f => {
        if (f.status !== 'renamed' && f.status !== 'rejected') ids.push(f.id);
      });
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
    // Eligibility filter (§16 M4 sibling): a matched file that is ALREADY
    // 'renamed' must not re-rename, and a 'rejected' one must not be silently
    // un-rejected. Only pending/matched files are approvable — mirrors the
    // bulk-bar `selectedApprovableIds` and CoverPopup's eligible filter.
    const ids = item.files
      .filter(f => f.matchedToEpisode != null && f.status !== 'renamed' && f.status !== 'rejected')
      .map(f => f.id);
    if (!ids.length) return;
    await setFileStatusBulk(ids, 'approved');
    if (renameFilesDirectly) await renameFilesDirectly(ids);
  };
  const rejectItem = (item: LibraryItem) => {
    // Never flip already-RENAMED files (the move happened on disk — losing the
    // 'renamed' record corrupts the funnel) or re-reject rejected ones.
    const ids = item.files.filter(f => f.status !== 'renamed' && f.status !== 'rejected').map(f => f.id);
    void setFileStatusBulk(ids, 'rejected');
    // Reject was the one mutation with no success feedback — approve renames
    // (which toasts), manual match toasts, but a reject just silently greyed
    // the card. Confirm it landed.
    pushToast?.({
      title: `Rejected ${ids.length === 1 ? item.title || 'item' : `${ids.length} files`}`,
      // Rejects aren't renames, so they never appear on the History page —
      // point at where they actually live so they don't seem to vanish.
      sub: 'Find it under the “Rejected” filter here in Review — undo by restoring it there.',
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
    if (!file) return;
    // Scope the eventual pick to EXACTLY what the user is looking at:
    //  - per-file action (fileIdx set) → just that one file, so picking a show
    //    for one wrong orphan can't clobber correctly-matched siblings;
    //  - card-level Re-identify → the card's ACTUAL file set, so the pick
    //    covers every file on the card (the old parsed-seriesKey expansion
    //    could miss half a merged cluster — the cover then "didn't change").
    const scopeIds = fileIdx != null ? [file.id] : item.files.map(f => f.id);
    openModal('manualSearch', { ...file, _clusterFileIds: scopeIds });
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

  // ── Keyboard review flow (rebuilt on the LibraryItem focus model) ─────────
  // Operates on the SAME items the grid renders + their `data-cardid`, so the
  // `focused` ring lands and every action targets the focused card. Replaces
  // the old App-level handler that keyed off raw file ids (which never matched
  // the grid's `lib_…` ids, so j/k moved an invisible cursor and a/r/m/Enter
  // silently died after any card click).
  const navItemsRef = useRef<LibraryItem[]>([]);
  navItemsRef.current = displayedItems;
  const focusRef = useRef(focusedId);
  focusRef.current = focusedId;
  useEffect(() => {
    const openFocused = () => {
      const it = navItemsRef.current.find(i => i.id === focusRef.current);
      if (!it) return;
      const el = document.querySelector<HTMLElement>(`[data-cardid="${CSS.escape(it.id)}"]`);
      if (el) handleOpenCover(it, el);
    };
    const onKey = (e: KeyboardEvent) => {
      // Ignore while typing, holding a modifier (leave ⌘/Ctrl combos to
      // global shortcuts), or when a modal/popup is open.
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (popup) return;
      // Walk cards in DOM order — the grid groups items into shelves/franchise
      // bands, so the raw items-array order is NOT the visual order (j/k used
      // to jump around the screen seemingly at random). The rendered
      // [data-cardid] sequence IS what the user sees.
      const byId = new Map(navItemsRef.current.map(i => [i.id, i]));
      const list = Array.from(document.querySelectorAll<HTMLElement>('[data-cardid]'))
        .map(el => byId.get(el.dataset.cardid ?? ''))
        .filter((i): i is LibraryItem => !!i && !i.ghost);
      if (list.length === 0) return;
      const rawIdx = list.findIndex(i => i.id === focusRef.current);
      const hasFocus = rawIdx >= 0;
      const idx = hasFocus ? rawIdx : 0;
      const cur = hasFocus ? list[idx] : undefined;
      const focusAt = (n: number) => {
        const it = list[Math.max(0, Math.min(list.length - 1, n))];
        if (!it) return;
        setFocusedId(it.id);
        document.querySelector<HTMLElement>(`[data-cardid="${CSS.escape(it.id)}"]`)
          ?.scrollIntoView({ block: 'nearest' });
      };
      switch (e.key) {
        // First nav press with nothing focused yet → land on the first card.
        case 'j': case 'ArrowDown': e.preventDefault(); focusAt(hasFocus ? idx + 1 : 0); break;
        case 'k': case 'ArrowUp': e.preventDefault(); focusAt(hasFocus ? idx - 1 : 0); break;
        case 'a': if (cur && !cur.ghost) { e.preventDefault(); void approveItem(cur); } break;
        case 'r': if (cur && !cur.ghost) { e.preventDefault(); rejectItem(cur); } break;
        case 'm': if (cur && !cur.ghost) { e.preventDefault(); manualSearchItem(cur); } break;
        case 'x': if (cur && !cur.ghost) {
          e.preventDefault();
          setSelected(prev => { const s = new Set(prev); s.has(cur.id) ? s.delete(cur.id) : s.add(cur.id); return s; });
        } break;
        case 'Enter': case ' ': e.preventDefault(); openFocused(); break;
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [popup]);

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

          <span aria-hidden className="hidden h-5 w-px bg-white/10 sm:block" />
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="mr-0.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-quaternary">Sort</span>
            <FilterChip on={sortBy === 'default'} onClick={() => setSortBy('default')} label="Default" />
            <FilterChip on={sortBy === 'title'} onClick={() => setSortBy('title')} label="A–Z" />
            <FilterChip on={sortBy === 'confidence'} onClick={() => setSortBy('confidence')} label="Confidence" />
            <FilterChip on={sortBy === 'size'} onClick={() => setSortBy('size')} label="Size" />
          </div>

          {dupeCount > 0 ? (
            <>
              <span aria-hidden className="hidden h-5 w-px bg-white/10 sm:block" />
              <FilterChip
                on={dupesOnly}
                onClick={() => setDupesOnly(v => !v)}
                label="Duplicates"
                num={dupeCount}
                accent="var(--conf-mid)"
                dot
              />
            </>
          ) : null}
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
                const n = selectedRejectableIds.length;
                if (!n) return;
                void setFileStatusBulk(selectedRejectableIds, 'rejected');
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
                const ids = selectedApprovableIds;
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
            >{statusF === 'approved' ? 'Rename' : 'Approve & rename'} ({selectedApprovableIds.length})</Button>
          </div>
        </div>
      ) : null}

      <LibraryGrid
          defaultView={statusF === 'pending' && conf === 'all' && type === 'all' && !searchQuery.trim()}
        items={displayedItems}
        onGetMovie={handleGetMovie}
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
          onPickCandidate={onPickCandidate}
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
