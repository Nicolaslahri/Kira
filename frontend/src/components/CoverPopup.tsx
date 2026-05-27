// CoverPopup — the cover-expand modal.
// Ported from the design prototype (kira/project/src/coverpopup.jsx).
//
// Shared-element open transition: the clicked cover flies from its grid
// position into the popup's hero slot while the rest of the modal fades
// in. Close reverses, using the source card's CURRENT bounding rect (in
// case the layout shifted while the popup was open).
//
// Body content varies by kind:
//   series/album — two-column synced "Your files" ↔ "Matched episode"
//   movie        — single block with file row + cast + rename preview
//
// Rows are PAIRED. Episodes drive row order. If an episode has no file,
// the left side renders a blank "Find a file" CTA. Orphan files (no
// matched episode) get appended at the bottom with a blank right side.

import { memo, useEffect, useLayoutEffect, useRef, useState, useMemo, useCallback } from 'react';
import type { LibraryItem, LibEpisode, LibFile, MediaType } from '../lib/types';
import {
  IcCheck, IcX, IcSearch, IcRefresh, IcFolder, IcAlertTri, IcExternal, IcChevDown, IcTrash,
} from '../lib/icons';
import { api } from '../lib/api';
import { MediaTypeIcon } from './ui';
import { libraryStats, confTier } from './LibraryGrid';
import { fetchAnidbPoster, getCachedAnidbPoster } from '../lib/posters';
import { fetchSeriesEpisodes, getCachedEpisodes, type ProviderEpisode } from '../lib/episodes';
import { pluralize, prettyLanguage, prettyCountry } from '../lib/format';

interface CoverPopupProps {
  item: LibraryItem;
  originRect: DOMRect | null;
  onClose: () => void;
  onUpdateItem: (next: LibraryItem) => void;
  onManualSearch: (item: LibraryItem, episodeIdx?: number | null, fileIdx?: number | null) => void;
  pushToast?: (toast: { title: string; sub?: string; kind?: 'success' | 'error' }) => void;
  /** Direct-rename callback — same one ReviewPage uses for its
   *  green-check approve+rename flow. Lets the popup's
   *  "Approve & rename" button actually rename instead of just
   *  flipping status (which never moved files OR wrote History). */
  renameFilesDirectly?: (fileIds: string[]) => void | Promise<void>;
}

export function CoverPopup({
  item, originRect, onClose, onUpdateItem, onManualSearch, pushToast,
  renameFilesDirectly,
}: CoverPopupProps) {
  const stats = useMemo(() => libraryStats(item), [item]);
  const shape: 'poster' | 'square' = item.mediaType === 'music' ? 'square' : 'poster';
  const tint = item.poster.tint;

  // Cluster-level confidence summary. Used to warn the user when the
  // matcher's best guess is poor (e.g. the One Pace fan-edit files
  // matched to a placeholder series at 0% across the board). Excludes
  // already-renamed/rejected files since those aren't waiting on
  // approval — only "live" files affect the warning.
  const clusterMaxConfidence = useMemo(() => {
    const live = item.files.filter(f =>
      f.matchedToEpisode != null
      && f.status !== 'renamed'
      && f.status !== 'rejected'
    );
    if (live.length === 0) return null;
    return Math.max(...live.map(f => f.confidence ?? 0));
  }, [item.files]);
  const isLowConfidenceCluster = clusterMaxConfidence !== null && clusterMaxConfidence < 50;

  // Cluster "dead" state — every file is rejected or renamed (or no
  // files at all). Used to hide Re-match / Reject buttons since both
  // are no-ops, and to swap "Nothing to rename" for a real "Restore"
  // action when files are recoverable. Encountered when the user
  // navigates from the Rejected filter into a cluster they previously
  // ignored.
  const rejectedCount = item.files.filter(f => f.status === 'rejected').length;
  const renamedCount = item.files.filter(f => f.status === 'renamed').length;
  const liveCount = item.files.length - rejectedCount - renamedCount;
  const clusterIsDead = liveCount === 0 && item.files.length > 0;
  const canRestore = clusterIsDead && rejectedCount > 0;

  // AniDB items don't carry a posterUrl on the LibraryItem (the search dump
  // has no images). The CoverCard fetched the URL lazily into a shared
  // module-level cache — read from it here so the hero + flying cover show
  // the same image the grid card already has.
  const anidbAid = item.providers?.anidb;
  const [lazyPoster, setLazyPoster] = useState<string | null>(() =>
    anidbAid ? (getCachedAnidbPoster(String(anidbAid)) ?? null) : null
  );
  useEffect(() => {
    if (item.posterUrl || lazyPoster || !anidbAid) return;
    let cancelled = false;
    fetchAnidbPoster(String(anidbAid)).then(url => {
      if (!cancelled && url) setLazyPoster(url);
    });
    return () => { cancelled = true; };
  }, [item.posterUrl, lazyPoster, anidbAid]);
  const effectivePosterUrl = item.posterUrl ?? lazyPoster;

  const heroSlotRef = useRef<HTMLDivElement>(null);
  const flyRef = useRef<HTMLDivElement>(null);
  // PB-2: ref on the modal shell for focus-trap + ARIA labelling. Trap
  // engages once the open transition settles (no point trapping during
  // the 280ms shared-element flight).
  const shellRef = useRef<HTMLDivElement>(null);

  const [opening, setOpening] = useState(false);
  const [settled, setSettled] = useState(false);
  const [closing, setClosing] = useState(false);

  // ── Open transition: park the flying cover at the source rect, then
  // animate it to the hero slot. Triggered once on mount.
  //
  // Two-measurement landing — the popup shell uses transform: scale(0.985)
  // → 1 during its open animation (0.28s). The slot's initial
  // getBoundingClientRect() returns a SCALED position, which would land
  // the flyer ~1.5% inset from where the slot actually ends up. We start
  // the flight with that approximate target so the animation begins
  // immediately (responsive feel), then re-measure when the shell's
  // transform completes and redirect the flyer mid-flight to the exact
  // final position. The flyer then lands pixel-perfect on the slot —
  // the instant swap at settle time has zero visible discontinuity.
  useLayoutEffect(() => {
    if (!originRect || !flyRef.current) return;
    const fly = flyRef.current;
    fly.style.top = originRect.top + 'px';
    fly.style.left = originRect.left + 'px';
    fly.style.width = originRect.width + 'px';
    fly.style.height = originRect.height + 'px';
    fly.style.borderRadius = '10px';

    const raf = requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        setOpening(true);
        const slot = heroSlotRef.current;
        if (slot) {
          const r = slot.getBoundingClientRect();
          fly.style.top = r.top + 'px';
          fly.style.left = r.left + 'px';
          fly.style.width = r.width + 'px';
          fly.style.height = r.height + 'px';
          fly.style.borderRadius = getComputedStyle(slot).borderRadius;
        }
      });
    });

    // Re-measure after the shell's scale transform finishes — by then
    // the slot is at its true unscaled position. Redirect the flyer
    // mid-flight so its landing matches the slot exactly. Without this
    // the flyer lands 1-4px inset from the slot, producing a visible
    // ghost when the swap happens.
    //
    // CRITICAL: this listener must fire ONCE (for the open transform)
    // and then unbind. The shell ALSO transitions on close (back to
    // scale 0.985), and if we re-snapped the flyer to the slot during
    // the close animation we'd interrupt the return-to-source flight
    // — the flyer would mid-air-yank back to the popup. Self-removing
    // listener prevents that.
    const shell = document.querySelector('.cx-shell') as HTMLElement | null;
    let onShellSettled: ((e: TransitionEvent) => void) | null = null;
    let fired = false;
    if (shell) {
      onShellSettled = (e: TransitionEvent) => {
        if (fired) return;
        // Only react to the shell's own transform finishing — not to any
        // nested transition (e.g. backdrop-filter, child fades).
        if (e.target !== shell) return;
        if (e.propertyName !== 'transform') return;
        fired = true;
        if (shell && onShellSettled) shell.removeEventListener('transitionend', onShellSettled);
        const slot = heroSlotRef.current;
        if (!slot || !flyRef.current) return;
        const r = slot.getBoundingClientRect();
        flyRef.current.style.top = r.top + 'px';
        flyRef.current.style.left = r.left + 'px';
        flyRef.current.style.width = r.width + 'px';
        flyRef.current.style.height = r.height + 'px';
      };
      shell.addEventListener('transitionend', onShellSettled);
    }

    return () => {
      cancelAnimationFrame(raf);
      if (shell && onShellSettled) shell.removeEventListener('transitionend', onShellSettled);
    };
  }, [originRect]);

  // ── Hide the source card's cover while the popup is open so the "back
  // card" doesn't appear behind the closing flight. Restore on unmount.
  useEffect(() => {
    const sourceCover = document.querySelector(
      `.cc[data-cardid="${item.id}"] .cc-cover`
    ) as HTMLElement | null;
    if (sourceCover) sourceCover.style.visibility = 'hidden';
    return () => {
      if (sourceCover) sourceCover.style.visibility = '';
    };
  }, [item.id]);

  // PB-2: focus trap + focus restoration. While the modal is open, Tab/
  // Shift-Tab wrap inside the shell so keyboard users can't accidentally
  // tab into the page beneath (which is invisible to sighted users — no
  // visible focus indicator escapes the modal backdrop). On close, focus
  // is restored to whatever element opened the popup (typically the
  // CoverCard) so assistive tech users don't get dumped at <body>.
  useEffect(() => {
    if (!settled || closing) return;
    const shell = shellRef.current;
    if (!shell) return;
    const savedFocus = document.activeElement as HTMLElement | null;

    const selector =
      'a[href], button:not([disabled]), input:not([disabled]), ' +
      'select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
    const getFocusables = () =>
      Array.from(shell.querySelectorAll<HTMLElement>(selector))
        .filter(el => el.offsetParent !== null);   // skip hidden

    // Move focus into the modal so the next Tab keeps us inside.
    // `preventScroll: true` — without this, the browser auto-scrolls the
    // focused element into view, which (for a popup that opens with the
    // hero rail mounted off-screen) yanks the entire shell upward at
    // open time. The user sees the popup settle, then immediately jump.
    // We only need the focus for keyboard nav; visual position should
    // come from the open animation, not from .focus()'s scroll behavior.
    //
    // F-15: prefer the first <button> (typically Close in the footer)
    // over the first ANY focusable. With the wider selector the trap
    // sometimes landed on a hero-area element that rendered a stray
    // text-cursor artefact next to the title. Buttons are the natural
    // landing target for a modal anyway — they're action-y, visible,
    // and never have caret-rendering quirks.
    const focusables = getFocusables();
    const first = focusables.find(el => el.tagName === 'BUTTON') ?? focusables[0];
    first?.focus({ preventScroll: true });

    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Tab') return;
      const focusables = getFocusables();
      if (!focusables.length) return;
      const firstEl = focusables[0];
      const lastEl = focusables[focusables.length - 1];
      const active = document.activeElement as HTMLElement | null;
      // Tab-wraparound IS user-initiated, so allowing the natural scroll-
      // into-view here is correct (Tab to an off-screen control should
      // reveal it). preventScroll is ONLY for the initial focus-trap
      // entry above.
      if (e.shiftKey && active === firstEl) {
        lastEl.focus();
        e.preventDefault();
      } else if (!e.shiftKey && active === lastEl) {
        firstEl.focus();
        e.preventDefault();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      // Restore focus to opener on unmount (close). preventScroll here
      // too — if the popup closed while the page beneath had scrolled,
      // we don't want to yank the page back to where the card lives.
      try {
        savedFocus?.focus?.({ preventScroll: true });
      } catch {
        savedFocus?.focus?.();
      }
    };
  }, [settled, closing]);

  const handleFlyEnd = useCallback((e: React.TransitionEvent<HTMLDivElement>) => {
    // Close has its own arrival handler (handleClose's native listener).
    // Don't double-fire opacity/state changes when the flyer is returning
    // to the source card.
    if (closing) return;
    if (e.propertyName !== 'top' && e.propertyName !== 'width') return;
    // Atomic swap, same paint:
    //   - Reveal the slot (style.opacity = '1') BEFORE hiding the flyer.
    //   - Then hide the flyer.
    // Order matters — if the flyer hides first, the next paint shows
    // background for one frame while React commits the slot's class
    // change (the blink the user reported). Direct DOM mutation on both
    // sides lands them in the SAME paint, no gap.
    if (heroSlotRef.current) heroSlotRef.current.style.opacity = '1';
    if (flyRef.current) flyRef.current.style.opacity = '0';
    setSettled(true);  // keeps React state in sync; CSS-wise we already moved.
  }, [closing]);

  // ── Close: animate the flying cover back to the source's CURRENT rect
  // (not the cached originRect — layout may have shifted under us).
  const handleClose = useCallback(() => {
    if (closing) return;
    const fly = flyRef.current;
    const sourceCover = document.querySelector(
      `.cc[data-cardid="${item.id}"] .cc-cover`
    ) as HTMLElement | null;
    const targetRect = sourceCover ? sourceCover.getBoundingClientRect() : originRect;
    if (fly && targetRect) {
      // Re-show the flyer BEFORE the position change so the browser
      // has its previous position (the slot's rect) as the "from"
      // frame for the position transition. Direct style mutation,
      // not React class — needs to land in the same paint as the
      // top/left/width/height writes below.
      fly.style.opacity = '1';
      fly.style.top = targetRect.top + 'px';
      fly.style.left = targetRect.left + 'px';
      fly.style.width = targetRect.width + 'px';
      fly.style.height = targetRect.height + 'px';
      fly.style.borderRadius = '10px';
    }
    setClosing(true);
    setSettled(false);
    setOpening(false);

    // Atomic land-and-unmount: when the flyer's position transition
    // completes, restore the source card's cover visibility IN THE
    // SAME PAINT we unmount, so the user never sees a frame of
    // "flyer gone, source still invisible" → which was the close blink.
    //
    // Fallback setTimeout handles the case where the transitionend
    // never fires (no targetRect, missing source, etc.).
    let done = false;
    const finishClose = () => {
      if (done) return;
      done = true;
      // Restore visibility BEFORE unmount so the source cover is on
      // screen the same frame the flyer disappears.
      if (sourceCover) sourceCover.style.visibility = '';
      onClose();
    };
    if (fly) {
      const onArrive = (ev: TransitionEvent) => {
        if (ev.target !== fly) return;
        if (ev.propertyName !== 'top' && ev.propertyName !== 'width') return;
        fly.removeEventListener('transitionend', onArrive);
        finishClose();
      };
      fly.addEventListener('transitionend', onArrive);
    }
    // Belt: if transitionend never fires (e.g. flyer wasn't moving),
    // unmount on a timer matched to the CSS transition duration.
    setTimeout(finishClose, 480);
  }, [closing, onClose, originRect, item.id]);

  // ── Lock body scroll + ESC to close. Compensate for the disappearing
  // scrollbar so the page doesn't reflow when we lock/unlock overflow.
  useEffect(() => {
    const prevOverflow = document.body.style.overflow;
    const prevPaddingRight = document.body.style.paddingRight;
    const scrollbarWidth = window.innerWidth - document.documentElement.clientWidth;
    document.body.style.overflow = 'hidden';
    if (scrollbarWidth > 0) document.body.style.paddingRight = scrollbarWidth + 'px';
    const fn = (e: KeyboardEvent) => { if (e.key === 'Escape') handleClose(); };
    window.addEventListener('keydown', fn);
    return () => {
      window.removeEventListener('keydown', fn);
      document.body.style.overflow = prevOverflow;
      document.body.style.paddingRight = prevPaddingRight;
    };
  }, [handleClose]);

  // ── Lazy-fetch the provider's authoritative episode list.
  // Without it, the popup can only show episodes we have files for, and
  // their titles only when the scan-time cluster matcher wrote them.
  // With it, we show every episode in the season (including ones the user
  // is missing) with real titles + air dates from TMDB/TVDB/AniDB.
  const matchProvider = item.files[0]?.matchedToEpisode != null
    ? (state => state)(undefined) ? undefined : undefined  // placeholder
    : undefined;
  // The matched provider/id lives on the underlying LibraryItem.providers
  // object (set by the adapter from the top match). Pick whichever the item
  // has — we only support one provider per item.
  const providerKey: string | null =
    item.providers?.anidb != null ? 'anidb' :
    item.providers?.tvdb  != null ? 'tvdb'  :
    item.providers?.tmdb  != null ? 'tmdb'  :
    item.providers?.musicbrainz != null ? 'musicbrainz' :
    null;
  const providerId: string | null = providerKey
    ? String((item.providers as Record<string, unknown>)?.[providerKey])
    : null;
  // For multi-season series providers (TMDB/TVDB), we need the season number.
  // AniDB uses no season; pass undefined.
  const seasonForFetch: number | undefined =
    providerKey === 'anidb' ? undefined :
    (item.episodes[0]?.season ?? 1);

  const [providerEpisodes, setProviderEpisodes] = useState<ProviderEpisode[] | null>(() =>
    providerKey && providerId
      ? (getCachedEpisodes(providerKey, providerId, seasonForFetch) ?? null)
      : null
  );
  useEffect(() => {
    if (providerEpisodes || !providerKey || !providerId || item.kind === 'movie') return;
    let cancelled = false;
    fetchSeriesEpisodes(providerKey, providerId, seasonForFetch).then(eps => {
      if (!cancelled && eps.length) setProviderEpisodes(eps);
    });
    return () => { cancelled = true; };
  }, [providerKey, providerId, seasonForFetch, providerEpisodes, item.kind]);

  // Suppress unused-var hint from the placeholder line above (kept for
  // clarity that this useEffect intentionally only runs for series/albums).
  void matchProvider;

  // Files the user has deleted (optimistic) — hidden from the row list
  // immediately so the duplicate group collapses without waiting for a
  // backend refetch.
  const [deletedIds, setDeletedIds] = useState<Set<string>>(new Set());

  // Re-identify: the popup's primary "this cluster matched wrong, let
  // me pick the right show" affordance. Single click opens the manual
  // search modal in whole-cluster mode (no fileIdx) — the modal's
  // handleSelect routes to bulk-select-manual and the backend applies
  // per-file cour routing across all sibling files. Replaced the older
  // "Re-match" button which re-ran the matcher with no way to override
  // its decision.
  const handleReidentify = useCallback(() => {
    onManualSearch(item, null, null);
  }, [item, onManualSearch]);

  // Delete-confirmation modal state. null = no modal open.
  const [pendingDelete, setPendingDelete] = useState<LibFile | null>(null);

  const handleDeleteFile = useCallback(async (file: LibFile) => {
    try {
      await api.deleteFile(Number(file.id));
      setDeletedIds(prev => { const n = new Set(prev); n.add(file.id); return n; });
      pushToast?.({ title: 'File deleted', sub: file.filename, kind: 'success' });
    } catch (e) {
      pushToast?.({ title: 'Delete failed', sub: String(e), kind: 'error' });
    } finally {
      setPendingDelete(null);
    }
  }, [pushToast]);

  // ── Row pairing. When the provider list is available, use IT as the
  // canonical episode set (so missing episodes show up as gaps). Fall back
  // to the file-derived list before the fetch lands.
  //
  // Rows can be:
  //   - blank: episode with no file
  //   - single: episode with exactly one file
  //   - dupe-primary: episode with 2+ files. The "best" file (highest
  //     quality preferred, then BD source, then first encountered) renders
  //     as the visible row, with a clickable "+N more" pill that opens
  //     the duplicate-resolver sub-modal listing all candidates.
  //   - orphan: file paired to no episode
  interface PairedRow {
    key: string;
    kind: 'blank' | 'single' | 'dupe-primary' | 'orphan';
    episode: LibEpisode | null;
    episodeIdx: number | null;
    file: LibFile | undefined;
    /** All files in this dupe group — passed to the sub-modal on click. */
    dupeAll?: LibFile[];
  }

  // Sub-modal state — null when closed, otherwise the dupe group to resolve.
  const [dupeModal, setDupeModal] = useState<{ episode: LibEpisode; files: LibFile[] } | null>(null);
  // When the user clicks the footer "Resolve N duplicates" button we
  // walk through every dupe in sequence: opening one, waiting for the
  // user to resolve, auto-advancing to the next. Tracked as `remaining`
  // (still to do) + `total` (original size at queue start) so the
  // modal can show "Duplicate 2 of 5" progress.
  const [dupeQueue, setDupeQueue] = useState<Array<{ episode: LibEpisode; files: LibFile[] }>>([]);
  const [dupeQueueTotal, setDupeQueueTotal] = useState(0);
  const rows: PairedRow[] = useMemo(() => {
    if (item.kind === 'movie') return [];
    const out: PairedRow[] = [];

    // Build a lookup of files-per-key. Multiple files (e.g. two release
    // groups of the same episode, or a SxE-named file + an absolute-named
    // dupe) legitimately share one episode — we render one paired row
    // per file rather than orphaning the "loser" with last-write-wins.
    const filesByEp = new Map<string, LibFile[]>();
    const pushFile = (k: string, f: LibFile) => {
      const list = filesByEp.get(k);
      if (list) {
        if (!list.includes(f)) list.push(f);
      } else {
        filesByEp.set(k, [f]);
      }
    };
    item.files.forEach(f => {
      const ep = f.matchedToEpisode != null ? item.episodes[f.matchedToEpisode] : null;
      if (!ep) return;
      pushFile(`${ep.season}-${ep.episode}`, f);
      if (ep.absolute != null) pushFile(`abs-${ep.absolute}`, f);
      // Anime season-agnostic fallback. AniDB stores everything as
      // season=1; TVDB cross-ref returns per-season episodes that for
      // long-runners (One Piece S23 has ~15 TVDB episodes, but the
      // user's file is at S23E1158 using absolute numbering). The
      // ONLY thing the file and the provider's episode list reliably
      // share is the episode NUMBER. Publish each anime file under a
      // bare `ep-${N}` key so pairing can match it to any provider
      // episode with the same `.episode` value regardless of what
      // season either side claims.
      //
      // Safe within a single popup: one cluster = one season's worth
      // of episodes, so episode numbers don't collide.
      if (item.mediaType === 'anime') {
        pushFile(`ep-${ep.episode}`, f);
        // Plus the existing (1, ep) and abs-ep fallbacks for the
        // AniDB-native episode list case (everything stored as
        // season=1, episode N).
        if (ep.season !== 1) {
          pushFile(`1-${ep.episode}`, f);
          pushFile(`abs-${ep.episode}`, f);
        }
      }
    });

    const episodes: LibEpisode[] = providerEpisodes && providerEpisodes.length
      ? providerEpisodes.map(pe => {
          // For anime, the provider's episode number IS the absolute, so
          // also try matching by absolute when (season,episode) misses —
          // otherwise One Piece in a "Season 23" folder loses the absolute
          // tag and the thumb renders "S01E1157" instead of "1157".
          const repEp = item.episodes.find(e =>
            (e.season === pe.season && e.episode === pe.episode) ||
            (item.mediaType === 'anime' && e.absolute === pe.episode)
          );
          return {
            season: pe.season,
            episode: pe.episode,
            absolute: repEp?.absolute ?? (item.mediaType === 'anime' ? pe.episode : undefined),
            title: pe.title || repEp?.title || undefined,
            airDate: pe.air_date || repEp?.airDate || undefined,
            overview: pe.overview || repEp?.overview || undefined,
            runtime: pe.runtime ?? repEp?.runtime ?? undefined,
          };
        })
      : item.episodes;

    const matchedFileIds = new Set<string>();
    episodes.forEach((ep, idx) => {
      // Collect every (non-deleted) file matching this episode by ANY of:
      //   - exact (season, episode)
      //   - absolute=ep.absolute (when provider populated it)
      //   - anime fallback: absolute=ep.episode (AniDB returns season=1
      //     for everything, episode number IS the absolute)
      const candidates: LibFile[] = [];
      const seen = new Set<string>();
      const addAll = (list: LibFile[] | undefined) => {
        if (!list) return;
        for (const f of list) {
          if (deletedIds.has(f.id)) continue;
          if (!seen.has(f.id)) { seen.add(f.id); candidates.push(f); }
        }
      };
      addAll(filesByEp.get(`${ep.season}-${ep.episode}`));
      if (ep.absolute != null) addAll(filesByEp.get(`abs-${ep.absolute}`));
      if (item.mediaType === 'anime') {
        // Season-agnostic match by episode number. Handles the long-
        // runner case: TVDB cross-ref returns S23E1-S23E15 for One
        // Piece's S23, the user's file is keyed at ep-1158, the
        // strict (season, episode) match misses, this fallback rescues
        // the pair by looking up `ep-1158` directly.
        addAll(filesByEp.get(`abs-${ep.episode}`));
        addAll(filesByEp.get(`ep-${ep.episode}`));
      }

      if (candidates.length === 0) {
        out.push({ key: 'ep-' + idx, kind: 'blank', episode: ep, episodeIdx: idx, file: undefined });
      } else if (candidates.length === 1) {
        const file = candidates[0];
        matchedFileIds.add(file.id);
        out.push({ key: `ep-${idx}`, kind: 'single', episode: ep, episodeIdx: idx, file });
      } else {
        // Pick the "primary" file by quality, then by absolute presence
        // (BD rips typically use absolute numbering; WEB rips use SxE).
        // The non-primary files stay paired so they don't fall into the
        // orphan bucket, but only the primary shows on the main row —
        // the rest live inside the sub-modal opened via the "+N more" pill.
        const sorted = [...candidates].sort(rankFile);
        const primary = sorted[0];
        sorted.forEach(f => matchedFileIds.add(f.id));
        out.push({
          key: `ep-${idx}`,
          kind: 'dupe-primary',
          episode: ep, episodeIdx: idx,
          file: primary,
          dupeAll: sorted,
        });
      }
    });

    // Sort orphan files by filename using natural ordering so that
    // S17E5 < S17E10 < S17E14 < S17E39 instead of the original DB-
    // insertion / OS-scan order which interleaves them arbitrarily
    // (the screenshot complaint: E39, E40, E14, E15, E16 because
    // E39/E40 were scanned first). `localeCompare` with `numeric: true`
    // is built into V8 and correctly handles embedded integers in
    // filenames — no regex parsing needed.
    const orphanFiles = item.files
      .filter(f => !matchedFileIds.has(f.id) && !deletedIds.has(f.id))
      .slice()
      .sort((a, b) => a.filename.localeCompare(
        b.filename, undefined, { numeric: true, sensitivity: 'base' },
      ));
    orphanFiles.forEach((file, i) =>
      out.push({ key: 'orphan-' + i, kind: 'orphan', episode: null, episodeIdx: null, file }),
    );
    return out;
  }, [item, providerEpisodes, deletedIds]);

  // ── Bulk actions
  const handleApproveAll = useCallback(() => {
    const next: LibraryItem = {
      ...item,
      files: item.files.map(f => f.matchedToEpisode != null ? { ...f, status: 'approved' as const } : f),
    };
    onUpdateItem(next);
    pushToast?.({ title: 'All matched files approved', sub: `${item.title}${item.year ? ' · ' + item.year : ''}`, kind: 'success' });
  }, [item, onUpdateItem, pushToast]);

  const handleRejectAll = useCallback(() => {
    const next: LibraryItem = {
      ...item,
      files: item.files.map(f => ({ ...f, status: 'rejected' as const })),
    };
    onUpdateItem(next);
    pushToast?.({ title: 'All files rejected', sub: item.title, kind: 'error' });
  }, [item, onUpdateItem, pushToast]);

  // Debounced batch for per-row approves. Previously every per-row
  // approve fired its own `renameFilesDirectly([file.id])` + its own
  // `listFiles()` refetch. Clicking 8 rows produced 8 sequential
  // PATCH + 8 POST /rename + 8 refetches racing each other, with
  // refetches landing mid-mutation and clobbering optimistic state.
  // Collect file IDs into a Set, fire ONE batched rename 400ms after
  // the last per-row approve. Single click → rename after 400ms.
  // Rapid clicks → one batched rename after the last click settles.
  const pendingRenameRef = useRef<Set<string>>(new Set());
  const renameTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const flushPendingRename = useCallback(() => {
    const ids = Array.from(pendingRenameRef.current);
    pendingRenameRef.current = new Set();
    if (renameTimerRef.current) {
      clearTimeout(renameTimerRef.current);
      renameTimerRef.current = null;
    }
    if (ids.length && renameFilesDirectly) {
      void renameFilesDirectly(ids);
    }
  }, [renameFilesDirectly]);
  // Flush on unmount so a click + immediate close doesn't lose the rename.
  useEffect(() => () => flushPendingRename(), [flushPendingRename]);

  const updateFile = useCallback((idx: number, patch: Partial<LibFile>) => {
    const next: LibraryItem = {
      ...item,
      files: item.files.map((f, i) => i === idx ? { ...f, ...patch } : f),
    };
    onUpdateItem(next);
    // When the per-row approve check is clicked (status flipped to
    // 'approved'), queue the file for rename — debounced so multiple
    // approves in quick succession fire ONE batched rename instead of
    // N races. Card-level green check + bulk bar + hero button still
    // hit `renameFilesDirectly` directly (their semantics are "act
    // now"); per-row approve has weaker urgency so debouncing is fine.
    if (patch.status === 'approved' && renameFilesDirectly) {
      const file = item.files[idx];
      if (file && file.matchedToEpisode != null) {
        pendingRenameRef.current.add(file.id);
        if (renameTimerRef.current) clearTimeout(renameTimerRef.current);
        renameTimerRef.current = setTimeout(flushPendingRename, 400);
      }
    }
  }, [item, onUpdateItem, renameFilesDirectly, flushPendingRename]);

  return (
    <div
      className={`cx-overlay ${opening ? 'opening' : ''} ${closing ? 'closing' : ''}`}
      onClick={handleClose}
    >
      {/* Flying cover — sits on top of the shell during open / close.
          Renders the real poster when we have one; otherwise the gradient
          + initials fallback. Either way the rect animation is identical. */}
      <div
        ref={flyRef}
        // `handoff` fires once the cover settles into the hero slot —
        // fades out so the in-flow .cx-hero-cover-slot replica takes
        // over. Without this the fixed-position flying cover stays
        // visible after landing; scrolling the sidebar then drags the
        // ghost duplicate around as the slot beneath scrolls away.
        className={`cx-flying-cover ${settled && !closing ? 'handoff' : ''}`}
        onTransitionEnd={handleFlyEnd}
        style={{
          background: item.noMatch
            ? 'rgba(255,255,255,0.04)'
            : `linear-gradient(135deg, ${tint[0]}, ${tint[1]})`,
        }}
      >
        {effectivePosterUrl && !item.noMatch ? (
          <img
            src={effectivePosterUrl}
            alt=""
            referrerPolicy="no-referrer"
            style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'cover' }}
          />
        ) : !item.noMatch ? (
          <>
            <span className="pinit">{item.poster.init}</span>
            {item.year ? <span className="pyr">{item.year}</span> : null}
          </>
        ) : null}
      </div>

      <div
        ref={shellRef}
        className="cx-shell"
        // PB-2: ARIA modal semantics — screen readers announce this as
        // a dialog and trap virtual cursor inside until close. Without
        // role="dialog" + aria-modal="true" the screen-reader user can
        // tab into the page beneath the overlay, which is invisible to
        // sighted users (no visible focus indicator escapes the modal).
        role="dialog"
        aria-modal="true"
        aria-labelledby="cx-hero-title-id"
        onClick={(e) => e.stopPropagation()}
        style={{ ['--hue-a' as never]: tint[0], ['--hue-b' as never]: tint[1] } as React.CSSProperties}
      >
        {/* Re-match + Close moved into the footer (.cx-foot-right) — keeps
            every popup action in one row at the bottom alongside Approve /
            Reject / Resolve duplicates. The old top-right pair was easy to
            miss when scrolling down a long episode list. */}

        {/* Side-by-side layout: hero is a 360px left rail (full vertical
            height) with cover + metadata; body fills the right region.
            Wrapping both in .cx-main lets flex stretch them to equal
            height. Previously the hero was a top strip and the body
            sat below it — the rail layout gives the file/episode list
            way more vertical room for long-running shows. */}
        {isLowConfidenceCluster ? (
          // Low-confidence warning banner. Shown when the cluster's
          // best match is below the "needs review" threshold. Without
          // this, the user sees a popup that looks like a normal
          // matched series and might click "Approve" thinking the
          // matcher had a real answer — when in reality it didn't.
          // The banner explicitly says "search manually" + recommends
          // the action, and the footer's primary CTA is swapped to
          // "Search for a better match" to match.
          <div
            role="alert"
            style={{
              display: 'flex',
              alignItems: 'flex-start',
              gap: 12,
              padding: '12px 18px',
              margin: '0 0 -1px 0',
              background: 'rgba(255, 91, 110, 0.10)',
              borderBottom: '1px solid rgba(255, 91, 110, 0.28)',
              color: 'var(--ink-1)',
              fontSize: 13,
              lineHeight: 1.45,
            }}
          >
            <IcAlertTri style={{ width: 16, height: 16, color: 'var(--conf-low)', flex: '0 0 auto', marginTop: 2 }} />
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 600, marginBottom: 2 }}>
                Low-confidence match — best guess is {Math.round(clusterMaxConfidence!)}%.
              </div>
              <div style={{ color: 'var(--ink-3)', fontSize: 12 }}>
                The matcher couldn't find a confident provider entry for these files.
                Use <strong>Search for a better match</strong> below to pick the right one
                manually — renaming with the current data would write generic episode
                titles to disk.
              </div>
            </div>
          </div>
        ) : null}
        <div className="cx-main">
          <Hero
            item={item} stats={stats} tint={tint} shape={shape}
            heroSlotRef={heroSlotRef} settled={settled}
            posterUrl={effectivePosterUrl}
          />

          {item.kind === 'movie'
            ? <MovieBody item={item} />
            : <SeriesBody
                item={item}
                rows={rows}
                updateFile={updateFile}
                onManualSearch={onManualSearch}
                onOpenDupeModal={(episode, files) => setDupeModal({ episode, files })}
                // PB-2: skeleton placeholder while the authoritative
                // episode list is fetching. Without this, the right
                // column is blank for ~3s on first popup open after
                // a scan — looks broken. Truthy when we KNOW we'll
                // get episodes (provider exists, not a movie) and
                // haven't received them yet.
                episodesLoading={
                  item.kind !== 'movie' &&
                  !!providerKey && !!providerId &&
                  (providerEpisodes === null || providerEpisodes.length === 0) &&
                  rows.length === 0
                }
              />
          }
        </div>

        {dupeModal ? (
          <DupesResolverModal
            item={item}
            episode={dupeModal.episode}
            files={dupeModal.files.filter(f => !deletedIds.has(f.id))}
            queueProgress={dupeQueueTotal > 0 ? {
              current: dupeQueueTotal - dupeQueue.length,
              total: dupeQueueTotal,
            } : undefined}
            onClose={() => {
              // If a queue is active, advance to the next dupe instead
              // of closing the modal entirely. Lets the user walk every
              // duplicate in one continuous flow after clicking the
              // footer's "Resolve N duplicates" button.
              if (dupeQueue.length > 0) {
                const [next, ...rest] = dupeQueue;
                setDupeQueue(rest);
                setDupeModal(next);
              } else {
                setDupeModal(null);
                setDupeQueueTotal(0);
              }
            }}
            onRequestDelete={setPendingDelete}
          />
        ) : null}

        {pendingDelete ? (
          <DeleteConfirmModal
            file={pendingDelete}
            onCancel={() => setPendingDelete(null)}
            onConfirm={() => handleDeleteFile(pendingDelete)}
          />
        ) : null}

        <div className="cx-foot">
          <div className="cx-foot-summary">
            {item.kind === 'movie' ? (
              <span><b>{stats.matched}</b> file ready to rename</span>
            ) : (() => {
              // Eligible-for-rename = matched to an episode AND not yet
              // renamed AND not rejected. When `ready` differs from
              // `episodes.length`, surface BOTH numbers so the user
              // understands the math (a duplicate file pair counts as
              // 2 files but 1 episode — without the disambiguation,
              // "10 ready" for 8 episodes reads as a bug).
              const ready = item.files.filter(f =>
                f.matchedToEpisode != null
                && f.status !== 'renamed'
                && f.status !== 'rejected'
              ).length;
              const renamed = item.files.filter(f => f.status === 'renamed').length;
              const epCount = item.episodes.length;
              const showBoth = ready !== epCount;
              return (
                <>
                  <span>
                    <b>{ready}</b> file{ready === 1 ? '' : 's'} ready
                    {showBoth ? <> <span style={{ color: 'var(--ink-3)' }}>· {epCount} episode{epCount === 1 ? '' : 's'}</span></> : null}
                  </span>
                  {stats.approved > 0 ? <><span>·</span><span style={{ color: 'var(--conf-high)' }}><b>{stats.approved}</b> approved</span></> : null}
                  {renamed > 0 ? <><span>·</span><span style={{ color: 'var(--conf-high)' }}><b>{renamed}</b> renamed</span></> : null}
                  {stats.rejected > 0 ? <><span>·</span><span style={{ color: 'var(--conf-low)' }}><b>{stats.rejected}</b> rejected</span></> : null}
                  {stats.unmatched > 0 ? <><span>·</span><span style={{ color: 'var(--conf-mid)' }}><b>{stats.unmatched}</b> unmatched</span></> : null}
                </>
              );
            })()}
          </div>
          <div className="cx-foot-right">
            {/* Close + Re-match — same .btn class as Reject/Approve so
                they share padding, height, border-radius, font weight.
                The ghost styling was too weightless next to the colored
                action buttons. Close is icon-only via `cx-foot-close`
                which only zeroes the horizontal padding — vertical
                padding matches .btn so the height aligns. */}
            <button
              className="btn cx-foot-close"
              onClick={handleClose}
              title="Close (Esc)"
              aria-label="Close"
            >
              <IcX />
            </button>
            {(() => {
              // Re-identify button.
              //
              // Single action for "this whole cluster matched wrong, let
              // me pick the right show." Opens the manual search modal
              // in whole-cluster mode (no fileIdx). When the user picks
              // a result, ManualSearchModal's handleSelect routes to
              // bulk-select-manual and the backend applies per-file
              // cour routing (e.g. Bleach S17 fans out across Cour 1/
              // 2/3 AIDs without the user having to know AniDB's
              // sequel structure).
              //
              // Hide for dead clusters (all files already rejected or
              // renamed). The destructive Reject button has already
              // been clicked / the rename has already happened — there
              // is nothing left to re-identify here. Show for every
              // other state including low-confidence + unmatched, so
              // the user has one consistent escape hatch from anywhere.
              if (clusterIsDead) return null;
              const hasAnyMatch = item.files.some(f => f.matchedToEpisode != null);
              const label = !hasAnyMatch
                ? 'Search for a match'
                : item.kind === 'movie' ? 'Re-identify movie' : 'Re-identify';
              return (
                <button
                  className="btn"
                  onClick={handleReidentify}
                  title={!hasAnyMatch
                    ? 'Open manual search and pick the right show for these files.'
                    : 'Open manual search and pick a different show — applies to every file in this cluster.'}
                >
                  <IcSearch />
                  <span>{label}</span>
                </button>
              );
            })()}
            {/* Push the action triad (dupes / reject / approve) to the
                far right so Close + Re-match sit left, destructive +
                primary stay right — the classic "secondary | primary"
                footer layout. */}
            <span className="cx-foot-spacer" />
            {/* Dupe resolver — only renders when the cluster has any
                episodes with duplicate files. Click walks every dupe
                in sequence via the queue mechanism. */}
            {(() => {
              const dupes: Array<{ episode: LibEpisode; files: LibFile[] }> = [];
              for (const r of rows) {
                if (r.kind === 'dupe-primary' && r.episode && r.dupeAll && r.dupeAll.length > 1) {
                  const live = r.dupeAll.filter(f => !deletedIds.has(f.id));
                  if (live.length > 1) dupes.push({ episode: r.episode, files: live });
                }
              }
              if (dupes.length === 0) return null;
              return (
                <button
                  className="btn"
                  style={{
                    background: 'rgba(255,201,74,0.18)',
                    borderColor: 'rgba(255,201,74,0.5)',
                    color: 'var(--conf-mid)',
                    fontWeight: 600,
                  }}
                  title="Pick which copy to keep for each duplicate; the rest are deleted from disk."
                  onClick={() => {
                    const [first, ...rest] = dupes;
                    setDupeQueueTotal(dupes.length);
                    setDupeQueue(rest);
                    setDupeModal(first);
                  }}
                >
                  <IcAlertTri /> Resolve {dupes.length} duplicate{dupes.length === 1 ? '' : 's'}
                </button>
              );
            })()}
            {(() => {
              // Context-aware Reject label, three tiers:
              //
              // 1. Low-confidence cluster — the matcher's "match" is
              //    garbage. The user's options are "search manually" or
              //    "just ignore these files". "Reject" implies there's
              //    a real match decision to reject; for these, "Ignore"
              //    is the honest label — same backend effect (status =
              //    rejected, removed from the queue, skipped in future
              //    scans), clearer language.
              //
              // 2. Unmatched cluster but with normal confidence on the
              //    matched files (e.g. mid-confidence partial match) —
              //    "Skip these files" reads as "stop trying to match",
              //    which is what status=rejected does.
              //
              // 3. Normal matched cluster — "Reject" / "Reject all" is
              //    accurate: there's a real match decision to overturn.
              //
              // Hide entirely when the cluster is dead (no live files
              // left to reject). The primary slot below will offer
              // "Restore" instead so the user has an action that
              // actually does something.
              if (clusterIsDead) return null;
              const hasAnyMatch = item.files.some(f => f.matchedToEpisode != null);
              const fileCount = item.files.filter(f => f.status !== 'rejected' && f.status !== 'renamed').length;
              let label: string;
              let tooltip: string;
              if (isLowConfidenceCluster) {
                label = item.kind === 'movie' || fileCount <= 1
                  ? 'Ignore'
                  : `Ignore ${fileCount} files`;
                tooltip = 'Remove these files from the review queue and skip them in future scans. You can still find them later in the Rejected filter.';
              } else if (!hasAnyMatch) {
                label = item.kind === 'movie' || fileCount <= 1
                  ? 'Skip this file'
                  : `Skip ${fileCount} files`;
                tooltip = 'Mark these files as rejected — they won\'t appear in future scans until you re-add them.';
              } else {
                label = item.kind === 'movie' || fileCount <= 1
                  ? 'Reject'
                  : 'Reject all';
                tooltip = 'Mark these files as rejected — they won\'t be renamed.';
              }
              return (
                <button
                  className="btn btn-danger"
                  onClick={() => {
                    if (item.kind === 'movie') updateFile(0, { status: 'rejected' });
                    else handleRejectAll();
                    // Auto-close after the user has decided to ignore /
                    // reject / skip these files. Without this, the popup
                    // stays open in a dead state — every footer button
                    // ("Re-match", "Reject", "Nothing to rename") is
                    // either useless or redundant for files that are
                    // already rejected. Matches the same UX as
                    // Approve+rename, which also auto-closes after the
                    // action is committed. The 250ms delay lets the
                    // user see the "× Rejected" status badges flip on
                    // each row first so the action visibly registered.
                    setTimeout(() => handleClose(), 250);
                  }}
                  title={tooltip}
                >
                  <IcX /> {label}
                </button>
              );
            })()}
            {(() => {
              // Eligible-to-rename count: matched + not rejected + not
              // already renamed. Computed once and shared between the
              // primary button's onClick and its label so they can't
              // disagree.
              const eligible = item.files.filter(f =>
                f.matchedToEpisode != null
                && f.status !== 'renamed'
                && f.status !== 'rejected'
              );
              const eligibleCount = eligible.length;

              // When the cluster has nothing to rename, "Approve & rename
              // 0 files" is nonsense — there's literally nothing to do.
              // User-reported friction: One Piece S23E1160 cluster (1
              // orphaned file, 0 matched episodes) showed three buttons
              // ("Re-match", "Reject all", "Approve & rename 0 files")
              // and the primary action was a no-op.
              //
              // When there ARE unmatched files but no matched ones,
              // surface the *actually useful* action instead: open Manual
              // Search prefilled with this file so the user can fix the
              // match. For a single-file cluster we pass that file's
              // index; for multi-file clusters we pass the first orphan
              // (the Manual Search modal handles the rest implicitly
              // because all files share parsed.title).
              const orphanFileIdx = item.files.findIndex(f =>
                f.matchedToEpisode == null && f.status !== 'rejected' && f.status !== 'renamed'
              );
              const hasOrphans = orphanFileIdx >= 0;

              if (eligibleCount === 0 && hasOrphans) {
                return (
                  <button
                    className="btn btn-primary"
                    onClick={() => {
                      // Hand control to the Manual Search modal — same
                      // entry point the per-row "Search" link uses.
                      onManualSearch(item, null, orphanFileIdx);
                    }}
                    title="Open Manual Search to find a match for this file"
                  >
                    <IcSearch /> Search manually
                  </button>
                );
              }

              // Edge case: cluster is fully renamed / fully rejected,
              // nothing eligible AND no orphans. Render a disabled
              // "Nothing to do" button so the footer doesn't lose its
              // primary slot (visual rhythm). aria-disabled keeps the
              // tab order sane; the click is a no-op.
              if (eligibleCount === 0) {
                // Cluster is dead in current state. Two sub-cases:
                //
                // (a) Some files are rejected and could be restored —
                //     offer "Restore N files" as a real action. This
                //     is the recovery path for users who hit "Ignore"
                //     and now want to undo it (or who navigated here
                //     from the Rejected filter intentionally).
                //
                // (b) Everything's already renamed — no recovery
                //     possible from this popup (the user would need
                //     to undo from History). Render the disabled
                //     "Nothing to rename" as a visual anchor in the
                //     primary slot, since the cluster genuinely has
                //     no actions left.
                if (canRestore) {
                  return (
                    <button
                      className="btn btn-primary"
                      onClick={() => {
                        // Flip every rejected file back to pending so
                        // it re-enters the review queue. Sequential
                        // updates so each row's UI flips visibly; the
                        // 250ms close lets the user see the badges
                        // disappear before the popup folds.
                        item.files.forEach((f, idx) => {
                          if (f.status === 'rejected') {
                            updateFile(idx, { status: 'pending' });
                          }
                        });
                        setTimeout(() => handleClose(), 250);
                      }}
                      title="Move these files back to the review queue so they show up in Pending again."
                    >
                      <IcRefresh /> Restore {rejectedCount} file{rejectedCount === 1 ? '' : 's'}
                    </button>
                  );
                }
                return (
                  <button
                    className="btn btn-primary"
                    aria-disabled="true"
                    style={{ opacity: 0.4, cursor: 'not-allowed' }}
                    onClick={(e) => e.preventDefault()}
                    title="All files in this cluster are already renamed"
                  >
                    <IcCheck /> Nothing to rename
                  </button>
                );
              }

              // Confidence quality of this cluster. We use the MAX
              // confidence across eligible files (not avg) — a cluster
              // where one file matched well but others poorly is still
              // partly trustworthy and the user should be able to
              // approve it. But a cluster where EVERY file is below
              // the "needs review" threshold (50% by default) means
              // the matcher genuinely doesn't know what these are, and
              // promoting an "Approve & rename" green CTA primes the
              // user to confirm garbage.
              //
              // One Pace example: the matcher matched 10 fan-edit files
              // to a placeholder series with no real episode titles,
              // all at 0% confidence. The popup happily showed
              // "Approve & rename 10 files" — clicking would have
              // renamed the user's files using fabricated "Episode 1"
              // titles. Bad outcome. Now the primary button becomes
              // "Search for a better match" and the rename button
              // demotes to a small secondary action labelled
              // "Approve anyway" so the user can override if they
              // verified manually.
              const maxConf = Math.max(0, ...eligible.map(f => f.confidence ?? 0));
              const isLowConfidence = maxConf < 50;

              if (isLowConfidence) {
                return (
                  <button
                    className="btn btn-primary"
                    onClick={() => {
                      // Open Manual Search prefilled for the first
                      // eligible file — the user can search the right
                      // provider for the real match.
                      const firstIdx = item.files.indexOf(eligible[0]);
                      onManualSearch(item, null, firstIdx >= 0 ? firstIdx : null);
                    }}
                    title={`Matches are low-confidence (best is ${Math.round(maxConf)}%). Search manually to find the real series.`}
                  >
                    <IcSearch /> Search for a better match
                  </button>
                );
              }

              return (
                <button
                  className="btn btn-primary"
                  onClick={async () => {
                    // Cancel any in-flight debounce — the hero button takes
                    // precedence over per-row debounced renames (we're
                    // about to rename ALL eligible files anyway).
                    if (renameTimerRef.current) {
                      clearTimeout(renameTimerRef.current);
                      renameTimerRef.current = null;
                    }
                    pendingRenameRef.current = new Set();
                    if (item.kind === 'movie') {
                      if (item.files[0]?.status !== 'approved') updateFile(0, { status: 'approved' });
                    } else {
                      handleApproveAll();
                    }
                    if (renameFilesDirectly && eligible.length) {
                      await renameFilesDirectly(eligible.map(f => f.id));
                    }
                    setTimeout(() => handleClose(), 250);
                  }}
                >
                  <IcCheck /> {
                    item.kind === 'movie'
                      ? 'Approve & rename'
                      // Be explicit "files" not bare "(N)" so the user
                      // knows we're counting files, not episodes. A 10
                      // for 8 episodes (with dupes) used to read like a
                      // bug — now it says "Approve & rename 10 files".
                      : `Approve & rename ${eligibleCount} file${eligibleCount === 1 ? '' : 's'}`
                  }
                </button>
              );
            })()}
          </div>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Hero (side-by-side variant)
// ─────────────────────────────────────────────────────────────────────

function mediaTypeLong(item: LibraryItem): string {
  if (item.mediaType === 'tv') return 'TV Series';
  if (item.mediaType === 'anime') return 'Anime';
  if (item.mediaType === 'movie') return 'Movie';
  return 'Album';
}

interface HeroProps {
  item: LibraryItem;
  stats: ReturnType<typeof libraryStats>;
  tint: [string, string];
  shape: 'poster' | 'square';
  heroSlotRef: React.RefObject<HTMLDivElement | null>;
  settled: boolean;
  /** Real poster URL — resolved by the parent (handles AniDB lazy-fetch). */
  posterUrl: string | null;
}

function Hero({ item, stats, tint, shape, heroSlotRef, settled, posterUrl }: HeroProps) {
  const visibleAlts = (item.altTitles || []).filter(t => t !== item.title && t !== item.titleRomaji).slice(0, 3);
  return (
    <div className={`cx-hero variant-side shape-${shape}`}>
      <div
        ref={heroSlotRef}
        className={`cx-hero-cover-slot shape-${shape} ${settled ? 'settled' : ''}`}
        style={{ background: `linear-gradient(135deg, ${tint[0]}, ${tint[1]})` }}
      >
        {settled ? (
          posterUrl ? (
            <img
              src={posterUrl}
              // PB-2: meaningful alt for screen readers. The hero title h2
              // also describes the content but a single image alt makes
              // image-navigation modes (some screen readers, browser
              // image-only view) usable. Empty alt was acceptable for
              // pure decoration but the cover IS content here.
              alt={`Cover art for ${item.title}`}
              referrerPolicy="no-referrer"
              // PB-2: decoding=async lets the browser decode off the main
              // thread (paint isn't blocked while we decode 500 KB).
              // fetchPriority=high since this is above-the-fold and the
              // user is staring at the slot waiting for it. width/height
              // help prevent layout shift before src loads.
              decoding="async"
              // @ts-expect-error — fetchpriority is valid HTML5 attribute
              // but typings haven't landed in @types/react yet.
              fetchpriority="high"
              style={{
                position: 'absolute', inset: 0, width: '100%', height: '100%',
                objectFit: 'cover', borderRadius: 'inherit',
              }}
            />
          ) : (
            <div style={{
              position: 'absolute', inset: 0, display: 'flex',
              flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
              color: '#fff', fontWeight: 700,
            }}>
              <span style={{ fontSize: 56, filter: 'drop-shadow(0 2px 8px rgba(0,0,0,0.4))' }}>
                {item.poster.init}
              </span>
              {item.year ? (
                <span style={{ fontSize: 14, opacity: 0.85, marginTop: 6, letterSpacing: '0.04em' }}>
                  {item.year}
                </span>
              ) : null}
            </div>
          )
        ) : null}
      </div>

      <div className="cx-hero-info">
        <div className="cx-hero-meta-row">
          <span className={`cc-mediatype ${item.mediaType}`} style={{ position: 'static' }}>
            <MediaTypeIcon type={item.mediaType as MediaType} />{mediaTypeLong(item)}
          </span>
          {item.yearRange || item.year ? <span>{item.yearRange || item.year}</span> : null}
          {item.runtime ? <><span className="dot-sep" /><span>{item.runtime} min</span></> : null}
          {item.kind === 'series' ? <><span className="dot-sep" /><span>{pluralize(item.episodes.length, 'episode')}</span></> : null}
          {item.kind === 'album' ? <><span className="dot-sep" /><span>{pluralize(item.episodes.length, 'track')}</span></> : null}
        </div>

        <div className="cx-hero-titleblock">
          {/* PB-2: id targeted by .cx-shell's aria-labelledby so screen
              readers announce the show/movie title as the dialog name. */}
          <h2 id="cx-hero-title-id" className="cx-hero-title">
            {item.artist ? <span style={{ color: 'var(--ink-2)', fontWeight: 600 }}>{item.artist} — </span> : null}
            {item.title}
          </h2>
          {(item.titleRomaji || item.titleNative || visibleAlts.length > 0) ? (
            <div className="cx-hero-alt">
              {item.titleRomaji && item.titleRomaji !== item.title ? <span>{item.titleRomaji}</span> : null}
              {item.titleNative ? <span>{item.titleNative}</span> : null}
              {visibleAlts.map((t, i) => <span key={i} className="alt-chip">a.k.a. {t}</span>)}
            </div>
          ) : null}
        </div>

        {item.overview ? <p className="cx-hero-overview">{item.overview}</p> : null}

        <div className="cx-hero-details">
          {item.studio || item.label ? (
            <div className="cx-hero-detail">
              <span className="cx-hero-detail-label">{item.kind === 'album' ? 'Label' : 'Studio'}</span>
              <span className="cx-hero-detail-value">{item.studio || item.label}</span>
            </div>
          ) : null}
          {item.network ? (
            <div className="cx-hero-detail">
              <span className="cx-hero-detail-label">Network</span>
              <span className="cx-hero-detail-value">{item.network}</span>
            </div>
          ) : null}
          {item.director ? (
            <div className="cx-hero-detail">
              <span className="cx-hero-detail-label">Director</span>
              <span className="cx-hero-detail-value">{item.director}</span>
            </div>
          ) : null}
          {/* F-13: humanize ISO codes for display. TVDB/TMDB return
              3-letter codes like "eng" / "usa" which read as jargon.
              prettyLanguage / prettyCountry map them to "English" /
              "United States" with safe fallback to uppercased code. */}
          {item.language ? (
            <div className="cx-hero-detail">
              <span className="cx-hero-detail-label">Language</span>
              <span className="cx-hero-detail-value">{prettyLanguage(item.language)}</span>
            </div>
          ) : null}
          {item.country ? (
            <div className="cx-hero-detail">
              <span className="cx-hero-detail-label">Country</span>
              <span className="cx-hero-detail-value">{prettyCountry(item.country)}</span>
            </div>
          ) : null}
          {item.genres?.length ? (
            <div className="cx-hero-detail">
              <span className="cx-hero-detail-label">Genres</span>
              <span className="cx-hero-detail-value wrap">{item.genres.join(' · ')}</span>
            </div>
          ) : null}
        </div>

        <div className="cx-hero-statsline">
          <div className="group">
            {!item.noMatch ? (
              <span className={`cx-summary-chip ${confTier(stats.avgConf)}`}>
                <span className="swatch" style={{ background: confColorP(stats.avgConf) }} />
                {stats.avgConf}% avg confidence
              </span>
            ) : null}
            {stats.approved > 0 ? <span className="cx-summary-chip"><span className="swatch" style={{ background: 'var(--conf-high)' }} />{stats.approved} approved</span> : null}
            {stats.pending > 0 ? <span className="cx-summary-chip"><span className="swatch" style={{ background: 'var(--conf-mid)' }} />{stats.pending} pending</span> : null}
            {stats.rejected > 0 ? <span className="cx-summary-chip"><span className="swatch" style={{ background: 'var(--conf-low)' }} />{stats.rejected} rejected</span> : null}
            {stats.unmatched > 0 ? <span className="cx-summary-chip"><span className="swatch" style={{ background: 'var(--ink-3)' }} />{stats.unmatched} unmatched</span> : null}
          </div>
          <span className="spacer" />
          <div className="group">
            {item.providers?.tmdb ? <ProviderLink label="TMDB" href={`https://www.themoviedb.org/${item.mediaType === 'movie' ? 'movie' : 'tv'}/${item.providers.tmdb}`} /> : null}
            {item.providers?.tvdb ? <ProviderLink label="TVDB" href={`https://www.thetvdb.com/?id=${item.providers.tvdb}&tab=series`} /> : null}
            {item.providers?.anidb ? <ProviderLink label="AniDB" href={`https://anidb.net/anime/${item.providers.anidb}`} /> : null}
            {item.providers?.musicbrainz ? <ProviderLink label="MusicBrainz" href={`https://musicbrainz.org/release/${item.providers.musicbrainz}`} /> : null}
          </div>
        </div>
      </div>
    </div>
  );
}

function ProviderLink({ label, href }: { label: string; href: string }) {
  return (
    <a className="cx-prov-link" href={href} target="_blank" rel="noreferrer">
      {label} <IcExternal />
    </a>
  );
}

function confColorP(v: number): string {
  if (v >= 85) return 'var(--conf-high)';
  if (v >= 50) return 'var(--conf-mid)';
  return 'var(--conf-low)';
}

// 5-step dedupe ranker. Lower rank = "keep this file" in a duplicate group.
//   1. Resolution        2160p → 1080p → 720p → 480p → unknown
//   2. Source            BluRay/Remux → BDRip → WEB-DL → WEBRip → WEB → HDTV → DVDRip
//   3. Codec             AV1 → HEVC/x265 → AVC/x264 → XviD/unknown
//                        (modern efficiency wins; matters most for anime where
//                         x265 10-bit kills the color banding x264 8-bit can't.)
//   4. Bit depth         10-bit → 8-bit/unknown
//                        (gold standard for anime; flat colors + line art.)
//   5. File size         larger wins
//                        (more bytes ≈ higher bitrate ≈ less aggressive
//                         compression; only kicks in when 1-4 all tie.)
//
// Previous tie-breaker was alphabetical, which arbitrarily preferred files
// with spaces ("Reacher - S01E02") over files with periods ("Reacher.S01E02")
// — favoring Kira-renamed outputs over their richer original-source counterparts.
const _Q_RANK: Record<string, number> = { '2160p': 0, '1080p': 1, '720p': 2, '480p': 3 };
const _SRC_RANK: Record<string, number> = {
  bluray: 0, 'blu-ray': 0, bdrip: 1, bdremux: 0, remux: 0,
  'web-dl': 2, webdl: 2, webrip: 3, 'web-rip': 3, web: 4,
  hdtv: 5, dvdrip: 6,
};
const _CODEC_RANK: Record<string, number> = {
  av1: 0,
  'h.265': 1, h265: 1, x265: 1, hevc: 1,
  'h.264': 2, h264: 2, x264: 2, avc: 2,
  xvid: 3, divx: 3, mpeg2: 4, mpeg4: 4,
};
const _BIT_RANK: Record<string, number> = {
  '10bit': 0, '10-bit': 0, hi10p: 0, hi10: 0,
  '8bit': 1, '8-bit': 1,
};
function _normCodec(c: string | undefined): string {
  return (c ?? '').toLowerCase().replace(/[\s_]/g, '');
}
function _normBitDepth(b: string | undefined): string {
  return (b ?? '').toLowerCase().replace(/[\s_]/g, '');
}

// Filename-level fallbacks for when the backend parser hasn't repopulated
// parsed_data yet (rows scanned before the WxH resolution fix landed).
// Pure regex over the filename — cheap, no roundtrip needed.
const _RES_RE = /\b(2160p|1080p|720p|480p)\b/i;
const _WXH_RE = /\b(3840x2160|1920x1080|1280x720|854x480|720x576|720x480|640x480)\b/i;
const _SRC_RE = /\b(BluRay|Blu-Ray|BDRip|BDRemux|REMUX|WEB-DL|WEBRip|WEB-Rip|WEB|HDTV|DVDRip|BD)\b/i;
const _WXH_TO_P: Record<string, string> = {
  '3840x2160': '2160p', '1920x1080': '1080p', '1280x720': '720p',
  '854x480': '480p', '720x576': '576p', '720x480': '480p', '640x480': '480p',
};

/** Best-effort quality detection: prefer the parsed value, fall back to
 *  scanning the filename. Used both for chip rendering and ranking so a
 *  stale parsed_data row still shows the right info in the UI. */
export function inferQuality(file: LibFile): string | undefined {
  if (file.quality) return file.quality;
  const m1 = file.filename.match(_RES_RE);
  if (m1) return m1[1].toLowerCase();
  const m2 = file.filename.match(_WXH_RE);
  if (m2) return _WXH_TO_P[m2[1].toLowerCase()];
  // BluRay/BD without explicit resolution is almost always 1080p in 2024.
  if (/\b(BluRay|BDRip|BDRemux|REMUX|\bBD\b)/i.test(file.filename)) return '1080p';
  return undefined;
}
export function inferSource(file: LibFile): string | undefined {
  if (file.source) return file.source;
  const m = file.filename.match(_SRC_RE);
  if (!m) return undefined;
  const raw = m[1];
  // Normalize "BD" → "BluRay" so the chip reads consistently across releases.
  if (raw.toUpperCase() === 'BD') return 'BluRay';
  return raw;
}

function rankFile(a: LibFile, b: LibFile): number {
  // 1. Resolution
  const qa = _Q_RANK[inferQuality(a) ?? ''] ?? 9;
  const qb = _Q_RANK[inferQuality(b) ?? ''] ?? 9;
  if (qa !== qb) return qa - qb;
  // 2. Source
  const sa = _SRC_RANK[(inferSource(a) ?? '').toLowerCase()] ?? 9;
  const sb = _SRC_RANK[(inferSource(b) ?? '').toLowerCase()] ?? 9;
  if (sa !== sb) return sa - sb;
  // 3. Codec — modern efficiency wins. Files WITH a codec tag also beat
  //    files without one (the typed encode > unknown blob heuristic),
  //    which is the right call for our "renamed-output vs original-source"
  //    tie: the Kira-renamed file usually loses its codec token, so the
  //    KONTRAST/x265-style original surfaces correctly as the keep.
  const ca = _CODEC_RANK[_normCodec(a.codec)] ?? 9;
  const cb = _CODEC_RANK[_normCodec(b.codec)] ?? 9;
  if (ca !== cb) return ca - cb;
  // 4. Bit depth — 10-bit wins, anime gold standard.
  const ba = _BIT_RANK[_normBitDepth(a.bitDepth)] ?? 1;
  const bb = _BIT_RANK[_normBitDepth(b.bitDepth)] ?? 1;
  if (ba !== bb) return ba - bb;
  // 5. File size — larger usually = higher bitrate = less compression.
  //    Only kicks in when 1-4 all tie (e.g. byte-identical copies that
  //    differ only by name).
  if (a.sizeBytes != null && b.sizeBytes != null && a.sizeBytes !== b.sizeBytes) {
    return b.sizeBytes - a.sizeBytes; // descending
  }
  // 6. Stable alphabetical fallback for true ties (or missing size data).
  return a.filename.localeCompare(b.filename);
}

// ─────────────────────────────────────────────────────────────────────
// Series / album body — two-column synced scroll
// ─────────────────────────────────────────────────────────────────────

interface PairedRowShape {
  key: string;
  kind: 'blank' | 'single' | 'dupe-primary' | 'orphan';
  episode: LibEpisode | null;
  episodeIdx: number | null;
  file: LibFile | undefined;
  dupeAll?: LibFile[];
}

interface SeriesBodyProps {
  item: LibraryItem;
  rows: PairedRowShape[];
  updateFile: (idx: number, patch: Partial<LibFile>) => void;
  onManualSearch: (item: LibraryItem, episodeIdx?: number | null, fileIdx?: number | null) => void;
  onOpenDupeModal: (episode: LibEpisode, files: LibFile[]) => void;
  /** PB-2: when true, render skeleton rows instead of blank columns. */
  episodesLoading?: boolean;
}

function SeriesBody({ item, rows, updateFile, onManualSearch, onOpenDupeModal, episodesLoading }: SeriesBodyProps) {
  const leftRef = useRef<HTMLDivElement>(null);
  const rightRef = useRef<HTMLDivElement>(null);
  const syncing = useRef(false);
  // PB-2: rAF-coalesce the scroll-sync write. The original wrote
  // scrollTop synchronously on every scroll event — at 120Hz that's
  // 240 forced reflows/sec across two columns. Coalescing into one
  // write per frame cuts paint work to display-refresh rate without
  // changing the visual sync feel.
  const rafIdRef = useRef<number | null>(null);

  const onScroll = (e: React.UIEvent<HTMLDivElement>, otherRef: React.RefObject<HTMLDivElement | null>) => {
    if (syncing.current || !otherRef.current) return;
    const nextTop = (e.target as HTMLDivElement).scrollTop;
    if (rafIdRef.current != null) cancelAnimationFrame(rafIdRef.current);
    rafIdRef.current = requestAnimationFrame(() => {
      rafIdRef.current = null;
      const dst = otherRef.current;
      if (!dst) return;
      if (Math.abs(dst.scrollTop - nextTop) < 1) return; // already in sync
      syncing.current = true;
      dst.scrollTop = nextTop;
      // Release the echo guard on the NEXT frame so the dst's onScroll
      // event has dispatched + been swallowed by `syncing.current`.
      requestAnimationFrame(() => { syncing.current = false; });
    });
  };

  const leftLabel = 'Your files';
  const rightLabel = item.kind === 'album' ? 'Matched track' : 'Matched episode';
  const providerTag =
    item.providers?.tmdb ? ' · TMDB' :
    item.providers?.tvdb ? ' · TVDB' :
    item.providers?.anidb ? ' · AniDB' :
    item.providers?.musicbrainz ? ' · MusicBrainz' : '';

  return (
    <div className="cx-body">
      <div className="cx-col">
        <div className="cx-col-head left">
          <span>{leftLabel}</span>
          <span className="col-meta">{item.files.length} {item.files.length === 1 ? 'file' : 'files'}</span>
        </div>
        <div
          className="cx-col-body"
          ref={leftRef}
          onScroll={(e) => onScroll(e, rightRef)}
          aria-busy={episodesLoading ? 'true' : undefined}
          aria-label={episodesLoading ? 'Loading files' : undefined}
        >
          {episodesLoading
            ? Array.from({ length: 8 }).map((_, i) => <SkeletonRow key={`sk-l-${i}`} side="left" />)
            : rows.map(r => (
                <FileRowCell
                  key={r.key}
                  row={r}
                  item={item}
                  updateFile={updateFile}
                  onManualSearch={onManualSearch}
                  onOpenDupeModal={onOpenDupeModal}
                />
              ))}
        </div>
      </div>

      <div className="cx-col">
        <div className="cx-col-head">
          <span>{rightLabel}</span>
          <span className="col-meta">
            {item.episodes.length} {item.kind === 'album' ? 'tracks' : 'episodes'}{providerTag}
          </span>
        </div>
        <div
          className="cx-col-body"
          ref={rightRef}
          onScroll={(e) => onScroll(e, leftRef)}
          aria-busy={episodesLoading ? 'true' : undefined}
          aria-label={episodesLoading ? 'Loading episodes' : undefined}
        >
          {episodesLoading
            ? Array.from({ length: 8 }).map((_, i) => <SkeletonRow key={`sk-r-${i}`} side="right" />)
            : rows.map(r => (
                <EpisodeRowCell
                  key={r.key}
                  row={r}
                  item={item}
                  updateFile={updateFile}
                  onManualSearch={onManualSearch}
                  onOpenDupeModal={onOpenDupeModal}
                />
              ))}
        </div>
      </div>
    </div>
  );
}

// PB-2: skeleton placeholder row. Renders during the ~150ms-3s window
// between popup open and episode-list arrival. Shimmer animation is
// driven by CSS; respects prefers-reduced-motion via @media query in
// index.css.
function SkeletonRow({ side }: { side: 'left' | 'right' }) {
  return (
    <div className={`cx-row cx-row-skeleton sk-${side}`} aria-hidden="true">
      <div className="cx-skel cx-skel-thumb" />
      <div className="cx-skel-stack">
        <div className="cx-skel cx-skel-title" />
        <div className="cx-skel cx-skel-meta" />
      </div>
    </div>
  );
}

// ── Left side: a file row
function detectFromFilename(filename: string, item: LibraryItem): string | null {
  if (item.kind === 'album') {
    const m = filename.match(/^(\d{1,2})\b/) || filename.match(/[-_\s]+(\d{1,2})\b/);
    return m ? String(+m[1]).padStart(2, '0') : null;
  }
  if (item.mediaType === 'anime') {
    const m = filename.match(/-\s*(\d{1,3})\s*[\[\(]/);
    if (m) return m[1].padStart(2, '0');
  }
  const m = filename.match(/[Ss](\d{1,2})[Ee](\d{1,2})/);
  if (m) return `S${m[1].padStart(2, '0')}E${m[2].padStart(2, '0')}`;
  return null;
}

interface RowCellProps {
  row: PairedRowShape;
  item: LibraryItem;
  updateFile: (idx: number, patch: Partial<LibFile>) => void;
  onManualSearch: (item: LibraryItem, episodeIdx?: number | null, fileIdx?: number | null) => void;
  onOpenDupeModal: (episode: LibEpisode, files: LibFile[]) => void;
}

// PB-2: row-level memoization. Equality function checks the
// row-identifying + row-mutable fields. Without this, every state
// change in CoverPopup (e.g. approving one file out of 50) re-renders
// all 50 rows. With it, only the changed row re-renders. The price is
// one shallow object check per row per parent render — negligible vs
// the 49 avoided React reconciliations.
//
// Bug-fix: this used to compare `a.row.episode?.id !== b.row.episode?.id`
// but LibEpisode has no `id` field, so it always evaluated `undefined
// === undefined` and the memo SKIPPED legitimate re-renders. The
// symptom was: episode titles missing on first popup open (provider
// fetch hadn't returned yet), titles arrived asynchronously, the row
// object changed but the memo thought "still equal", titles never
// rendered. Closing + reopening worked because the new CoverPopup
// instance mounted fresh. Now we compare the actual user-visible
// content fields (title, air date, absolute, runtime) so an async
// merge that fills these in triggers the re-render it should.
const rowsEqualFile = (a: RowCellProps, b: RowCellProps): boolean => {
  if (a.row.key !== b.row.key) return false;
  if (a.row.kind !== b.row.kind) return false;
  if (a.row.file?.id !== b.row.file?.id) return false;
  if (a.row.file?.status !== b.row.file?.status) return false;
  // Bug-fix: when the user resolves duplicates (deletes one of N files
  // claiming an episode), the row's `dupeAll` array shrinks but the
  // primary file's id, status, and row.key all stay the same. Without
  // this check, the memo says "equal" and React keeps rendering the
  // stale `+N` badge even after the dupes are gone. Compare the
  // length so a shrunk-to-1 group correctly re-renders as a non-dupe row.
  if ((a.row.dupeAll?.length ?? 0) !== (b.row.dupeAll?.length ?? 0)) return false;
  // Episode content that can change after initial render (provider
  // fetch arrives, user edits, etc.). Compared by value because
  // there's no stable id.
  const ea = a.row.episode, eb = b.row.episode;
  if ((ea == null) !== (eb == null)) return false;
  if (ea && eb) {
    if (ea.season !== eb.season) return false;
    if (ea.episode !== eb.episode) return false;
    if (ea.absolute !== eb.absolute) return false;
    if (ea.title !== eb.title) return false;
    if (ea.airDate !== eb.airDate) return false;
    if (ea.runtime !== eb.runtime) return false;
    if (ea.overview !== eb.overview) return false;
  }
  // Callback identity not checked — they're recreated each render by
  // the parent, and the row already short-circuits via row.key / file /
  // episode content above.
  return true;
};

function FileRowCellImpl({ row, item, updateFile, onManualSearch, onOpenDupeModal }: RowCellProps) {
  const file = row.file;
  const fileIdx = file ? item.files.indexOf(file) : -1;

  // Blank — episode without a file.
  //
  // Note on the missing button: this row used to render a "Find a file"
  // CTA that opened Manual Search. That was misleading — Manual Search
  // picks a different SHOW/EPISODE metadata match, it has no way to
  // attach a file from disk. The only honest answer when you don't have
  // a file is "scan more folders" (top-bar Scan button) or "live with
  // the gap". So the row now just labels the gap without offering a
  // dead-end action. `onManualSearch` is still in scope (used by the
  // file-side row below) but deliberately not wired here.
  void onManualSearch;
  if (!file) {
    return (
      <div className="cx-row blank">
        <div className="cx-file-row">
          <div className="cx-pair-thumb file undetected"><span className="ep-num">—</span></div>
          <div className="cx-row-content blank-content">
            <span className="lbl">No file for this episode</span>
          </div>
          <div className="cx-row-aside"><span className="cx-row-conf muted">—</span></div>
        </div>
      </div>
    );
  }


  const wrong = file.matchedWrong;
  const conf = file.confidence ?? 0;
  const confT = confTier(conf);

  // Bug-fix: the file-side thumb used to render whatever pattern
  // `detectFromFilename` could pull from the FILENAME — so a "Show.S01E16.mkv"
  // showed "S01 E16" but a "Show - 17.mkv" showed bare "17", giving an
  // ugly mix of formats within the same series popup. Use the matched
  // episode data (canonical, provider-sourced) instead, with the SAME
  // per-media-type rule as the right-side EpisodeRowCell. Falls back to
  // filename detection only when no matched episode exists (which is
  // mostly a "wrong match" or pre-match transient state).
  const ep = row.episode;
  const isAlbum = item.kind === 'album';
  let thumbPrefix: string | null = null;
  let thumbNum = '?';
  if (ep) {
    if (isAlbum) {
      thumbPrefix = 'TRACK';
      thumbNum = String(ep.track ?? ep.episode).padStart(2, '0');
    } else if (item.mediaType === 'anime' && ep.absolute) {
      thumbNum = String(ep.absolute).padStart(2, '0');
    } else if (ep.season != null && ep.episode != null) {
      thumbPrefix = 'S' + String(ep.season).padStart(2, '0');
      thumbNum = 'E' + String(ep.episode).padStart(2, '0');
    } else if (ep.episode != null) {
      thumbNum = String(ep.episode).padStart(2, '0');
    }
  } else {
    // No paired episode (orphan / pre-match). Fall back to whatever
    // we can pull from the filename so the thumb isn't blank.
    const detected = detectFromFilename(file.filename, item);
    if (detected) {
      const m = detected.match(/^S(\d+)E(\d+)$/);
      if (m) { thumbPrefix = 'S' + m[1]; thumbNum = 'E' + m[2]; }
      else { thumbNum = detected; }
    }
  }
  const detected = ep ? true : false;  // drives the .detected vs .undetected styling
  void updateFile; // we expose actions but per-file approve lives on the right side

  const statusClass =
    file.status === 'approved' ? 'approved' :
    file.status === 'rejected' ? 'rejected' :
    file.status === 'renamed'  ? 'renamed'  : '';

  return (
    <div className={`cx-row ${statusClass} ${wrong ? 'wrong' : ''}`}>
      <div className="cx-file-row">
        <div className={`cx-pair-thumb file ${detected ? 'detected' : 'undetected'}`}>
          {thumbPrefix ? <span className="ep-prefix">{thumbPrefix}</span> : null}
          <span className="ep-num">{thumbNum}</span>
        </div>
        <div className="cx-row-content">
          <div className="cx-row-title mono">{file.filename}</div>
          <div className="cx-row-sub mono"><span className="seg">{file.folder}</span></div>
          <div className="cx-row-tags">
            {file.size ? <span className="cx-row-tag">{file.size}</span> : null}
            {(() => { const q = inferQuality(file); return q ? <span className="cx-row-tag">{q}</span> : null; })()}
            {(() => { const s = inferSource(file); return s ? <span className="cx-row-tag">{s}</span> : null; })()}
            {file.codec ? <span className="cx-row-tag">{file.codec}</span> : null}
            {file.releaseGroup ? <span className="cx-row-tag rg">[{file.releaseGroup}]</span> : null}
            {row.kind === 'dupe-primary' && row.dupeAll && row.dupeAll.length > 1 ? (
              // Compact chip — sized to fit inline with the format tags
              // (1.2 GB, 1080p, WEBRip, …). The old verbose "Duplicate ·
              // N files · review →" form blew past the row's max-width
              // and got truncated mid-word against the confidence pill
              // on the right. `+N` here means "N other files claim this
              // episode" (this row is the kept primary). Full context
              // lives on the title attribute + the modal that opens.
              <button
                className="cx-row-dupe"
                onClick={(e) => {
                  e.stopPropagation();
                  if (row.episode && row.dupeAll) onOpenDupeModal(row.episode, row.dupeAll);
                }}
                title={`${row.dupeAll.length} files claim this episode — click to pick which to keep`}
              >
                <IcAlertTri /> +{row.dupeAll.length - 1}
              </button>
            ) : null}
            {wrong ? <span className="cx-row-warn"><IcAlertTri /> Wrong episode</span> : null}
          </div>
        </div>
        <div className="cx-row-aside" onClick={(e) => e.stopPropagation()}>
          {/* Explicit status pill so the user can see at a glance which
              files are approved / renamed / rejected, instead of having
              to decode subtle line-through + opacity cues. */}
          {file.status === 'renamed' ? (
            <span className="cx-row-status renamed" title="File has been renamed"><IcCheck /> Renamed</span>
          ) : file.status === 'approved' ? (
            <span className="cx-row-status approved" title="Approved — queued for rename"><IcCheck /> Approved</span>
          ) : file.status === 'rejected' ? (
            <span className="cx-row-status rejected" title="Rejected"><IcX /> Rejected</span>
          ) : null}
          {/* Confidence pill semantics:
              - Paired to an episode (row.episode set): show the matcher's
                confidence — this is the *episode* match quality.
              - Orphan row (row.kind === 'orphan' or no episode paired):
                the file is in a matched series but couldn't be tied to a
                specific episode. Showing "100%" here is misleading — that
                percentage describes the SERIES match, not the episode.
                Render an explicit "No episode" pill instead so the user
                doesn't think "matched, all good" while staring at a row
                that literally can't be renamed.
              - Marked wrong by the user: keep the percentage but tinted
                conf-low + the existing "Wrong episode" chip in the tags
                row already does the talking. */}
          {row.kind === 'orphan' || !row.episode ? (
            <span
              className="cx-row-conf low"
              title={
                file.match
                  ? `Series matched at ${conf}% but no episode in that series matches this file's S/E number. Use Search to fix.`
                  : 'No match at all — use Search manually to find one.'
              }
            >
              No episode
            </span>
          ) : (
            <span className={`cx-row-conf ${confT}`}>{conf}%</span>
          )}
          <div className="cx-row-actions">
            <button
              className="cx-row-act"
              title="Search manually for this file"
              onClick={() => onManualSearch(item, null, fileIdx)}
            ><IcSearch /></button>
          </div>
        </div>
      </div>
    </div>
  );
}
// PB-2: memo wrapper — see rowsEqualFile comment above.
const FileRowCell = memo(FileRowCellImpl, rowsEqualFile);

function EpisodeRowCellImpl({ row, item, updateFile, onManualSearch, onOpenDupeModal }: RowCellProps) {
  void onOpenDupeModal;
  // Right column is one row per left-column row; no special handling for
  // dupes here — the left column emits a single `dupe-primary` row that
  // surfaces a "review →" pill, and clicking opens the resolver modal.
  const { episode, file } = row;
  const fileIdx = file ? item.files.indexOf(file) : -1;
  const epColor = item.poster.tint;
  const isAlbum = item.kind === 'album';

  // Blank — orphan file with no matching episode
  if (!episode) {
    return (
      <div className="cx-row blank">
        <div className="cx-ep-row">
          <div
            className="cx-pair-thumb ep"
            style={{
              ['--ep-a' as never]: 'rgba(255,255,255,0.03)',
              ['--ep-b' as never]: 'rgba(255,255,255,0.03)',
              borderStyle: 'dashed',
            } as React.CSSProperties}
          >
            <span className="ep-num" style={{ color: 'var(--ink-4)' }}>—</span>
          </div>
          <div className="cx-row-content blank-content">
            <span className="lbl">File is orphaned · no matching {isAlbum ? 'track' : 'episode'}</span>
            <button className="cx-blank-btn" onClick={() => onManualSearch(item, null, fileIdx)}>
              <IcSearch /> Search this file
            </button>
          </div>
          <div className="cx-row-aside"><span className="cx-row-conf muted">—</span></div>
        </div>
      </div>
    );
  }

  let thumbPrefix: string | null;
  let thumbNum: string;
  if (isAlbum) {
    thumbPrefix = 'TRACK';
    thumbNum = String(episode.track ?? episode.episode).padStart(2, '0');
  } else if (item.mediaType === 'anime' && episode.absolute) {
    thumbPrefix = null;
    thumbNum = String(episode.absolute).padStart(2, '0');
  } else {
    thumbPrefix = 'S' + String(episode.season).padStart(2, '0');
    thumbNum = 'E' + String(episode.episode).padStart(2, '0');
  }

  const conf = file?.confidence ?? 0;
  const confT = confTier(conf);
  const fullTag = isAlbum
    ? `Track ${String(episode.track ?? episode.episode).padStart(2, '0')}`
    : item.mediaType === 'anime' && episode.absolute
      ? `Episode ${String(episode.absolute).padStart(2, '0')}`
      : `S${String(episode.season).padStart(2, '0')}E${String(episode.episode).padStart(2, '0')}`;

  return (
    <div className={`cx-row ${file?.status === 'approved' ? 'approved' : ''} ${file?.status === 'rejected' ? 'rejected' : ''} ${file?.matchedWrong ? 'wrong' : ''}`}>
      <div className="cx-ep-row">
        <div
          className="cx-pair-thumb ep"
          style={{ ['--ep-a' as never]: epColor[0], ['--ep-b' as never]: epColor[1] } as React.CSSProperties}
        >
          {thumbPrefix ? <span className="ep-prefix">{thumbPrefix}</span> : null}
          <span className="ep-num">{thumbNum}</span>
        </div>
        <div className="cx-row-content">
          <div className="cx-row-title">
            {episode.title || (isAlbum ? `Track ${episode.track}` : `Episode ${episode.episode}`)}
            {isAlbum && episode.duration ? (
              <span style={{ color: 'var(--ink-3)', fontWeight: 500, marginLeft: 8, fontSize: 12 }}>
                · {episode.duration}
              </span>
            ) : null}
          </div>
          <div className="cx-row-sub">
            <span>{fullTag}</span>
            {episode.airDate && !isAlbum ? <><span className="dot-sep" /><span>{episode.airDate}</span></> : null}
            {episode.runtime && !isAlbum ? <><span className="dot-sep" /><span>{episode.runtime} min</span></> : null}
          </div>
          {file?.matchedWrong ? (
            <div className="cx-row-tags">
              <span className="cx-row-warn">
                <IcAlertTri /> Filename suggests a different {isAlbum ? 'track' : 'episode'}
              </span>
              <button
                className="cx-blank-btn"
                style={{ padding: '2px 8px' }}
                onClick={() => onManualSearch(item, row.episodeIdx, fileIdx)}
              >
                <IcSearch /> Find correct
              </button>
            </div>
          ) : null}
        </div>
        <div className="cx-row-aside" onClick={(e) => e.stopPropagation()}>
          <span className={`cx-row-conf ${confT}`}>{file ? `${conf}%` : '—'}</span>
          <div className="cx-row-actions">
            <button
              className="cx-row-act approve"
              title="Approve this episode"
              onClick={() => fileIdx >= 0 ? updateFile(fileIdx, { status: 'approved' }) : null}
              disabled={!file}
            ><IcCheck /></button>
            <button
              className="cx-row-act reject"
              title="Reject this episode"
              onClick={() => fileIdx >= 0 ? updateFile(fileIdx, { status: 'rejected' }) : null}
              disabled={!file}
            ><IcX /></button>
            <button className="cx-row-act" title="Pick a different episode for this file"><IcChevDown /></button>
          </div>
        </div>
      </div>
    </div>
  );
}
// PB-2: memo wrapper for EpisodeRowCell — same equality semantics as
// FileRowCell since both keys depend on the same row identity fields.
const EpisodeRowCell = memo(EpisodeRowCellImpl, rowsEqualFile);

// ─────────────────────────────────────────────────────────────────────
// Movie body
// ─────────────────────────────────────────────────────────────────────

function MovieBody({ item }: { item: LibraryItem }) {
  const file = item.files[0];
  if (!file) return null;
  const conf = file.confidence;
  const confT = confTier(conf);
  const wrong = file.matchedWrong;
  const ext = file.filename.split('.').pop() || 'mkv';

  return (
    <div className="cx-body single">
      <div className="cx-movie">
        <section className="cx-movie-section">
          <div className="cx-movie-section-label">Your file</div>
          <div className={`cx-row cx-row-static ${file.status === 'approved' ? 'approved' : ''} ${file.status === 'rejected' ? 'rejected' : ''} ${wrong ? 'wrong' : ''}`}>
            <div className="cx-file-row">
              <div className="cx-pair-thumb file detected">
                <span className="ep-prefix">FILM</span>
                <span className="ep-num">●</span>
              </div>
              <div className="cx-row-content">
                <div className="cx-row-title mono">{file.filename}</div>
                <div className="cx-row-sub mono"><span className="seg">{file.folder}</span></div>
                <div className="cx-row-tags">
                  {file.size ? <span className="cx-row-tag">{file.size}</span> : null}
                  {file.quality ? <span className="cx-row-tag">{file.quality}</span> : null}
                  {wrong ? <span className="cx-row-warn"><IcAlertTri /> Wrong match</span> : null}
                </div>
              </div>
              <div className="cx-row-aside">
                <span className={`cx-row-conf ${confT}`}>{conf}%</span>
                <span className="cx-movie-status">
                  {file.status === 'approved'
                    ? <span style={{ color: 'var(--conf-high)' }}>✓ Approved</span>
                    : file.status === 'rejected'
                      ? <span style={{ color: 'var(--conf-low)' }}>✕ Rejected</span>
                      : <span style={{ color: 'var(--ink-3)' }}>Pending</span>}
                </span>
              </div>
            </div>
          </div>
        </section>

        {item.cast?.length ? (
          <section className="cx-movie-section">
            <div className="cx-movie-section-label">Cast</div>
            <div className="cx-cast-list">
              {item.cast.map((c, i) => <span key={i} className="cx-cast-chip">{c}</span>)}
            </div>
          </section>
        ) : null}

        <section className="cx-movie-section">
          <div className="cx-movie-section-label">Will rename to</div>
          <div className="cx-rename-target">
            <span className="seg-dir">/media/library/Movies/{item.title} ({item.year})/</span>
            <span className="seg-new">{item.title} ({item.year}) [{file.quality || '1080p'}].{ext}</span>
          </div>
        </section>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Dupes resolver — drilled into from the "Duplicate · N files" pill on
// the main popup. Shows every file claiming this episode side-by-side
// with full quality/source/codec/release-group chips and a trash button
// per row. Closes itself when the count drops to 1 (problem solved).
// ─────────────────────────────────────────────────────────────────────

interface DupesResolverModalProps {
  item: LibraryItem;
  episode: LibEpisode;
  files: LibFile[];
  onClose: () => void;
  onRequestDelete: (file: LibFile) => void;
  /** Optional progress when the resolver is being walked through a
   *  queue (after the user clicks "Resolve N duplicates"). Lets the
   *  modal show e.g. "Duplicate 2 of 5". */
  queueProgress?: { current: number; total: number };
}

function DupesResolverModal({ item, episode, files, onClose, onRequestDelete, queueProgress }: DupesResolverModalProps) {
  void item;
  // Auto-close when no more duplicates — the parent has already filtered
  // out deletedIds, so files.length===1 means the user resolved this group.
  useEffect(() => {
    if (files.length <= 1) onClose();
  }, [files.length, onClose]);

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0,
        background: 'rgba(7, 6, 12, 0.78)',
        backdropFilter: 'blur(6px)',
        WebkitBackdropFilter: 'blur(6px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        zIndex: 9000,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: '#14121b',
          color: 'var(--ink)',
          borderRadius: 14,
          padding: 24,
          maxWidth: 760,
          width: '92%',
          maxHeight: '82vh',
          overflow: 'hidden',
          display: 'flex', flexDirection: 'column',
          border: '1px solid var(--line-strong)',
          boxShadow: '0 24px 60px rgba(0, 0, 0, 0.6)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 16, marginBottom: 18 }}>
          <div
            style={{
              flexShrink: 0,
              width: 44, height: 44, borderRadius: 8,
              background: 'rgba(255, 201, 74, 0.15)',
              color: 'var(--conf-mid)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontWeight: 700, fontSize: 18,
            }}
          >
            {files.length}
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <h3 style={{ margin: '0 0 4px 0', fontSize: 17, fontWeight: 600, display: 'flex', alignItems: 'baseline', gap: 10 }}>
              Duplicate files for {episode.season ? `S${String(episode.season).padStart(2, '0')}E${String(episode.episode).padStart(2, '0')}` : `Episode ${episode.episode}`}
              {queueProgress ? (
                <span
                  style={{
                    fontSize: 11, fontWeight: 600,
                    padding: '2px 8px',
                    borderRadius: 999,
                    background: 'rgba(255,201,74,0.18)',
                    color: 'var(--conf-mid)',
                    border: '1px solid rgba(255,201,74,0.4)',
                  }}
                  title={`${queueProgress.total - queueProgress.current + 1} more to go after this`}
                >
                  {queueProgress.current} / {queueProgress.total}
                </span>
              ) : null}
            </h3>
            <div style={{ fontSize: 13, color: 'var(--ink-2)' }}>
              {episode.title || `Episode ${episode.episode}`}
              <span style={{ color: 'var(--ink-3)', marginLeft: 8 }}>
                · Pick the version to keep, delete the rest
              </span>
            </div>
          </div>
          <button
            onClick={onClose}
            title="Close (Esc)"
            style={{
              appearance: 'none', border: 'none', background: 'transparent',
              color: 'var(--ink-3)', cursor: 'pointer', padding: 4,
            }}
          >
            <IcX />
          </button>
        </div>

        <div style={{ overflowY: 'auto', flex: 1, margin: '0 -24px', padding: '0 24px' }}>
          {files.map((f, i) => (
            <DupeFileCard
              key={f.id}
              file={f}
              recommended={i === 0}
              onDelete={() => onRequestDelete(f)}
            />
          ))}
        </div>

        <div
          style={{
            marginTop: 18, paddingTop: 14,
            borderTop: '1px solid var(--line)',
            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
            fontSize: 12, color: 'var(--ink-3)',
          }}
        >
          <span>Files are ranked by quality, then source. The top entry is the suggested keep.</span>
          <button
            onClick={onClose}
            style={{
              padding: '8px 14px', borderRadius: 8,
              background: 'var(--glass-2)', color: 'var(--ink)',
              border: '1px solid var(--line)',
              fontSize: 13, fontWeight: 500, cursor: 'pointer',
            }}
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
}

interface DupeFileCardProps {
  file: LibFile;
  recommended: boolean;
  onDelete: () => void;
}

function DupeFileCard({ file, recommended, onDelete }: DupeFileCardProps) {
  return (
    <div
      style={{
        padding: '12px 14px',
        borderRadius: 10,
        background: recommended ? 'rgba(40, 217, 160, 0.06)' : 'rgba(255, 255, 255, 0.025)',
        border: recommended
          ? '1px solid rgba(40, 217, 160, 0.35)'
          : '1px solid var(--line)',
        marginBottom: 10,
        display: 'flex', gap: 12, alignItems: 'flex-start',
      }}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
          {recommended ? (
            <span
              style={{
                fontSize: 10, fontWeight: 700, letterSpacing: '0.05em',
                textTransform: 'uppercase', padding: '3px 7px',
                borderRadius: 4,
                background: 'rgba(40, 217, 160, 0.18)', color: 'var(--conf-high)',
              }}
            >
              Suggested keep
            </span>
          ) : (
            <span
              style={{
                fontSize: 10, fontWeight: 600, letterSpacing: '0.05em',
                textTransform: 'uppercase', padding: '3px 7px',
                borderRadius: 4,
                background: 'var(--glass-2)', color: 'var(--ink-3)',
              }}
            >
              Alternate
            </span>
          )}
        </div>
        <div
          className="mono"
          style={{
            fontSize: 13, color: 'var(--ink)', wordBreak: 'break-all',
            marginBottom: 4, lineHeight: 1.4,
          }}
        >
          {file.filename}
        </div>
        <div
          className="mono"
          style={{ fontSize: 11, color: 'var(--ink-3)', marginBottom: 8, wordBreak: 'break-all' }}
        >
          {file.folder}
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
          {(() => { const q = inferQuality(file); return q ? <Chip>{q}</Chip> : null; })()}
          {(() => { const s = inferSource(file); return s ? <Chip>{s}</Chip> : null; })()}
          {file.codec ? <Chip>{file.codec}</Chip> : null}
          {file.releaseGroup ? <Chip accent>[{file.releaseGroup}]</Chip> : null}
        </div>
      </div>
      <button
        onClick={onDelete}
        title="Delete this file from disk (irreversible)"
        style={{
          flexShrink: 0,
          appearance: 'none',
          padding: '8px 12px', borderRadius: 8,
          background: 'rgba(255, 91, 110, 0.15)',
          color: 'var(--conf-low)',
          border: '1px solid rgba(255, 91, 110, 0.35)',
          fontSize: 12, fontWeight: 600,
          cursor: 'pointer',
          display: 'inline-flex', alignItems: 'center', gap: 6,
        }}
      >
        <IcTrash /> Delete
      </button>
    </div>
  );
}

function Chip({ children, accent }: { children: React.ReactNode; accent?: boolean }) {
  return (
    <span
      style={{
        fontSize: 11, padding: '3px 8px', borderRadius: 4,
        background: accent ? 'rgba(255, 151, 75, 0.14)' : 'var(--glass-2)',
        color: accent ? 'var(--brand-a)' : 'var(--ink-2)',
        border: '1px solid ' + (accent ? 'rgba(255, 151, 75, 0.3)' : 'var(--line)'),
        fontWeight: 500,
      }}
    >
      {children}
    </span>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Delete-confirm modal — second guard against the irreversible action.
// Backend ALSO requires ?confirm=true so a curl can't bypass this.
// ─────────────────────────────────────────────────────────────────────

interface DeleteConfirmModalProps {
  file: LibFile;
  onCancel: () => void;
  onConfirm: () => void;
}

function DeleteConfirmModal({ file, onCancel, onConfirm }: DeleteConfirmModalProps) {
  const [acknowledged, setAcknowledged] = useState(false);
  return (
    <div
      onClick={onCancel}
      style={{
        position: 'fixed', inset: 0,
        // Solid dark overlay with blur so the popup behind doesn't bleed through.
        background: 'rgba(7, 6, 12, 0.78)',
        backdropFilter: 'blur(6px)',
        WebkitBackdropFilter: 'blur(6px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        zIndex: 11000,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          // Opaque card, not glassy — this is a destructive prompt, it
          // needs to dominate. Solid #14121b reads above the popup behind.
          background: '#14121b',
          color: 'var(--ink)',
          borderRadius: 14,
          padding: 28,
          maxWidth: 540,
          width: '90%',
          border: '1px solid rgba(255, 91, 110, 0.4)',
          boxShadow: '0 24px 60px rgba(0, 0, 0, 0.6), 0 0 0 1px rgba(255, 91, 110, 0.18)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 14 }}>
          <span
            style={{
              display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
              width: 36, height: 36, borderRadius: 8,
              background: 'var(--conf-low-bg)', color: 'var(--conf-low)',
            }}
          >
            <IcAlertTri />
          </span>
          <h3 style={{ margin: 0, fontSize: 17, color: 'var(--ink)', fontWeight: 600 }}>
            Delete this file from disk?
          </h3>
        </div>
        <p style={{ color: 'var(--ink-2)', fontSize: 13, margin: '0 0 12px 0' }}>
          The .mkv will be permanently removed from your filesystem. This action
          cannot be undone.
        </p>
        <div
          className="mono"
          style={{
            fontSize: 12,
            padding: '10px 12px',
            borderRadius: 8,
            background: 'rgba(0, 0, 0, 0.35)',
            border: '1px solid var(--line)',
            color: 'var(--ink-2)',
            wordBreak: 'break-all',
            marginBottom: 18,
            lineHeight: 1.5,
          }}
        >
          {file.folder ? <span style={{ color: 'var(--ink-3)' }}>{file.folder}\</span> : null}
          <span style={{ color: 'var(--ink)', fontWeight: 600 }}>{file.filename}</span>
        </div>
        <label
          style={{
            display: 'flex', alignItems: 'center', gap: 10,
            fontSize: 13, color: 'var(--ink-2)',
            marginBottom: 20, cursor: 'pointer',
            padding: '8px 10px', borderRadius: 6,
            background: 'rgba(255, 255, 255, 0.03)',
          }}
        >
          <input
            type="checkbox"
            checked={acknowledged}
            onChange={(e) => setAcknowledged(e.target.checked)}
            style={{ accentColor: 'var(--conf-low)', width: 16, height: 16 }}
          />
          <span>I understand this is irreversible</span>
        </label>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
          <button
            onClick={onCancel}
            style={{
              padding: '9px 16px', borderRadius: 8,
              background: 'var(--glass-2)', color: 'var(--ink)',
              border: '1px solid var(--line)',
              fontSize: 13, fontWeight: 500, cursor: 'pointer',
            }}
          >
            Cancel
          </button>
          <button
            disabled={!acknowledged}
            onClick={onConfirm}
            style={{
              padding: '9px 16px', borderRadius: 8,
              background: acknowledged ? 'var(--conf-low)' : 'rgba(255, 91, 110, 0.25)',
              color: '#fff',
              border: 'none',
              fontSize: 13, fontWeight: 600,
              cursor: acknowledged ? 'pointer' : 'not-allowed',
              opacity: acknowledged ? 1 : 0.55,
              display: 'inline-flex', alignItems: 'center', gap: 6,
            }}
          >
            <IcTrash /> Delete from disk
          </button>
        </div>
      </div>
    </div>
  );
}
