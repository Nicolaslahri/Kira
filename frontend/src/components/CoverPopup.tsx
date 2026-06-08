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

import { useEffect, useLayoutEffect, useRef, useState, useMemo, useCallback } from 'react';
import type { LibraryItem, LibEpisode, LibFile } from '../lib/types';
import {
  IcCheck, IcX, IcSearch, IcRefresh, IcAlertTri, IcDownload,
} from '../lib/icons';
import { api } from '../lib/api';
import { Button } from './base/buttons/button';
import { libraryStats } from './LibraryGrid';
import { fetchAnidbPoster, getCachedAnidbPoster } from '../lib/posters';
import { fetchSeriesEpisodes, getCachedEpisodes, type ProviderEpisode } from '../lib/episodes';
import { rankFile } from './CoverPopup/quality';
import { DupesResolverModal, DeleteConfirmModal, BulkDeleteConfirmModal } from './CoverPopup/dupeModals';
import { MovieBody } from './CoverPopup/MovieBody';
import { Hero } from './CoverPopup/Hero';
import { SeriesBody } from './CoverPopup/SeriesBody';
import { useSonarrQueuePopup } from './CoverPopup/useSonarrQueuePopup';

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

// ─────────────────────────────────────────────────────────────────────
// MarqueeText — ping-pong auto-scroll for overflowing text.
//
// When the wrapped content is wider than its container (the row's
// title cell is fixed-width; long release-group filenames overflow),
// we translate the inner text leftward to reveal the end, pause
// briefly, then translate back to the start, pause, repeat. The
// pauses give the eye a moment to read both edges; the slides are
// fast enough that nothing feels stuck.
//
// (Previous v1 was a seamless duplicate-text continuous loop — but
// the user couldn't tell where the text actually ended; the
// duplicate looked like the file had another release tagged at
// the end. Ping-pong has clearer "this is one filename, here are
// its bookends" semantics.)
//
// When content fits, we just render plain text with ellipsis. Detection
// happens once on mount + on any resize via ResizeObserver.
//
// Hover pauses. prefers-reduced-motion disables the animation
// entirely — users with vestibular sensitivity see plain truncated
// text + the native `title` tooltip on hover for the full string.
// ─────────────────────────────────────────────────────────────────────



// Live Sonarr queue item, as seen by the popup. Mirrors the backend
// QueueItemOut shape — kept inline (rather than imported from a shared
// types file) so the rest of the app doesn't take a hard dep on Sonarr
// types until/unless Phase B's library-grid pill code wants them too.
// Popup-only hook. Polls /integrations/sonarr/queue?match_id=N every
// 4 seconds while the popup is mounted with a usable matchId. Stops
// polling on the first 400 (Sonarr-not-configured) so we don't hammer
// an endpoint that structurally can't help — the user opens Settings,
// configures, reopens popup, fresh poll begins.
//
// Returns null while we haven't fetched yet OR if Sonarr is
// unreachable. Returns [] for a configured Sonarr with no active
// downloads for this series. Both states render the same way in the
// popup (no progress rows shown), so the caller doesn't need to
// distinguish — the empty state matches the "no Sonarr" state.

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

  // ── Sonarr: live queue progress ───────────────────────────────────
  // Poll Sonarr's queue every ~4s while the popup is open. Items are
  // filtered server-side to this match's TVDB series + season, so the
  // result is "what's currently downloading for the show I'm looking
  // at." Used to paint per-row progress bars on the missing-episode
  // blanks below.
  //
  // Failure mode: backend returns 400 when Sonarr isn't configured.
  // We catch and stop polling — no point hammering an endpoint that
  // structurally can't help us. (If the user configures Sonarr while
  // a popup is open, they'll need to close + reopen; cheap and rare.)
  //
  // Key + interval lift to a tiny hook so the cover-card pills (Phase
  // B, separate file) can reuse the same shape without duplicating
  // the lifecycle.
  const sonarrMatchId = item.files.find(f => f.matchId != null)?.matchId ?? null;
  const sonarrEnabled =
    item.kind === 'series'
    && (providerKey === 'tvdb' || providerKey === 'anidb')
    && sonarrMatchId != null;
  const queueItems = useSonarrQueuePopup(sonarrEnabled ? sonarrMatchId : null);

  // Map keyed by episode number for O(1) lookup when rendering rows.
  // We dedup by episode number alone (not season+episode tuples) for
  // the same reason the missing-numbers computation does: AniDB native
  // vs TVDB cross-ref disagree on the season tag but agree on the
  // episode number for a given physical episode.
  const queueByEpisode = useMemo(() => {
    const m = new Map<number, NonNullable<typeof queueItems>[number]>();
    for (const q of queueItems ?? []) {
      if (typeof q.episode_number === 'number') m.set(q.episode_number, q);
    }
    return m;
  }, [queueItems]);

  // ── "Just imported" transitional state ────────────────────────────
  // When a Sonarr download finishes, the /queue entry vanishes within
  // ~30s. The file is on disk but Kira's library DB has no record of
  // it until the next scan. Without intervention the row reverts to
  // "No file for this episode" — the user sees an empty state right
  // after watching a 90%-filled green bar.
  //
  // Fix: track which episode numbers were "in flight" (downloading /
  // importing / queued) in the previous poll. When one vanishes from
  // the current poll, mark it "recentlyImported" with a timestamp.
  // The blank-row branch in FileRowCell checks this Set and renders
  // a "Just imported · scanning…" placeholder instead of the empty
  // state — survives 5 minutes, plenty of time for the auto-scan
  // (dispatched below) to find the new file.
  const prevQueueRef = useRef<Map<number, string>>(new Map());
  const [recentlyImported, setRecentlyImported] = useState<Map<number, number>>(new Map());
  useEffect(() => {
    if (queueItems == null) return; // first poll hasn't landed yet
    const prev = prevQueueRef.current;
    const cur = new Map<number, string>();
    queueByEpisode.forEach((q, ep) => cur.set(ep, q.status));

    // Diff: entries that were active in prev but now vanished from
    // cur → likely imported. Plus entries currently in "completed"
    // state — Sonarr keeps these briefly before removal; we want to
    // shift them to the post-import placeholder too, since the
    // download bar is already at 100% and "completed" → "imported"
    // is the natural narrative.
    const activeStatuses = new Set(['downloading', 'importing', 'queued', 'searching', 'completed']);
    const newlyImported: number[] = [];
    prev.forEach((prevStatus, epNum) => {
      if (!activeStatuses.has(prevStatus)) return;
      const curStatus = cur.get(epNum);
      if (curStatus == null) {
        // Vanished entirely — Sonarr finished and cleaned up.
        newlyImported.push(epNum);
      }
    });

    prevQueueRef.current = cur;

    if (newlyImported.length > 0) {
      const now = Date.now();
      setRecentlyImported(prev => {
        const next = new Map(prev);
        newlyImported.forEach(ep => next.set(ep, now));
        return next;
      });
      // Ask App.tsx to run a debounced rescan so the new files on
      // disk get indexed and the placeholder transitions into real
      // file rows. Debouncing lives in App; we just dispatch on
      // every detected completion and let it coalesce.
      window.dispatchEvent(new CustomEvent('kira:request-rescan'));
    }
  }, [queueItems, queueByEpisode]);

  // Auto-expire entries from recentlyImported after 5 minutes —
  // plenty for any auto-scan to land. If the file never appears (Sonarr
  // import failed silently, file landed in a folder Kira doesn't scan,
  // etc.) the placeholder gracefully reverts to the static "No file"
  // state rather than staying forever.
  useEffect(() => {
    if (recentlyImported.size === 0) return;
    const interval = setInterval(() => {
      const now = Date.now();
      setRecentlyImported(prev => {
        let changed = false;
        const next = new Map(prev);
        next.forEach((seenAt, epNum) => {
          if (now - seenAt > 5 * 60 * 1000) {
            next.delete(epNum);
            changed = true;
          }
        });
        return changed ? next : prev;
      });
    }, 30_000);
    return () => clearInterval(interval);
  }, [recentlyImported.size]);

  // Also: drop entries from recentlyImported once the corresponding
  // file actually appears on the library item — at that point the
  // row is rendering as a real matched file, no placeholder needed,
  // and the Set should shrink so memory doesn't drift. (`item.files`
  // changes whenever App.tsx refreshes /files after a scan.)
  useEffect(() => {
    if (recentlyImported.size === 0) return;
    const presentEpisodeNumbers = new Set<number>();
    for (const f of item.files) {
      if (typeof f.matchedToEpisode !== 'number') continue;
      const merged = item.episodes[f.matchedToEpisode];
      if (merged && typeof merged.episode === 'number') {
        presentEpisodeNumbers.add(merged.episode);
      }
    }
    setRecentlyImported(prev => {
      let changed = false;
      const next = new Map(prev);
      next.forEach((_, epNum) => {
        if (presentEpisodeNumbers.has(epNum)) {
          next.delete(epNum);
          changed = true;
        }
      });
      return changed ? next : prev;
    });
  }, [item.files, item.episodes, recentlyImported.size]);

  // ── Sonarr: send missing episodes ─────────────────────────────────
  // One-click handoff for the gaps Kira already knows about. The popup
  // already renders missing-episode rows in the right column (blank-on-
  // left, real episode title on right) — this button turns that data
  // into a Sonarr action without making the user retype anything.
  //
  // We don't check Sonarr-is-configured locally; the backend returns
  // a clear 400 if not, and the toast surfaces the message. That
  // avoids needing to wire a "Sonarr ready?" probe into every popup
  // open — most users either have it configured or don't.
  const [sonarrSending, setSonarrSending] = useState(false);
  // Sonarr-heal: pulls authoritative metadata from Sonarr for files
  // Kira couldn't match. Scoped to this cluster's file IDs only —
  // doesn't touch other low-confidence clusters in the library.
  const [sonarrHealing, setSonarrHealing] = useState(false);
  const handleSonarrHeal = useCallback(async () => {
    if (sonarrHealing) return;
    const fileIds = item.files
      .map(f => Number(f.id))
      .filter(n => Number.isFinite(n));
    if (fileIds.length === 0) return;
    setSonarrHealing(true);
    try {
      const r = await api.sonarrHealUnmatched({ file_ids: fileIds });
      if (r.ok && r.healed > 0) {
        pushToast?.({
          title: `Pinned ${r.healed} file${r.healed === 1 ? '' : 's'} from Sonarr`,
          sub: r.series_pinned === 1
            ? 'Metadata synced. The popup will refresh with the new match.'
            : `${r.series_pinned} series synced. The popup will refresh.`,
          kind: 'success',
        });
        // Close so the parent's listFiles polling picks up the freshly-
        // pinned matches — the cluster will re-render with the correct
        // identity next time the user opens it. (Leaving the popup
        // open would show stale data until React props refresh.)
        setTimeout(() => handleClose(), 600);
      } else {
        const reason = r.detail
          ?? (r.no_sonarr_match > 0
            ? "These files aren't in any of your Sonarr-managed folders."
            : 'Nothing to heal.');
        pushToast?.({
          title: 'Sonarr couldn\'t identify these files',
          sub: reason,
          kind: 'error',
        });
      }
    } catch (e) {
      pushToast?.({ title: 'Sonarr heal failed', sub: (e as Error).message, kind: 'error' });
    } finally {
      setSonarrHealing(false);
    }
  }, [sonarrHealing, item.files, pushToast, handleClose]);
  const handleSonarrSendMissing = useCallback(async (
    matchId: number, season: number, episodeNumbers: number[],
  ) => {
    if (sonarrSending) return;
    setSonarrSending(true);
    // Helper: feed the toast OR fall back to a native alert when the
    // popup is mounted somewhere the parent forgot to pass pushToast.
    // We discovered an earlier silent-fail where the API call fired
    // successfully but the toast was a no-op — surfacing zero
    // feedback to the user. window.alert is ugly but guaranteed to
    // be visible.
    const notify = (title: string, sub: string, kind: 'success' | 'error') => {
      if (typeof pushToast === 'function') {
        pushToast({ title, sub, kind });
      } else {
        // eslint-disable-next-line no-alert
        window.alert(`${title}\n\n${sub}`);
      }
    };
    try {
      const r = await api.sonarrSendMissing({
        match_id: matchId,
        season,
        episode_numbers: episodeNumbers,
      });
      const addedNote = r.series_was_added && r.sonarr_series_title
        ? ` "${r.sonarr_series_title}" added to Sonarr.`
        : '';
      const notInListNote = r.skipped_episodes && r.skipped_episodes.length > 0
        ? ` ${r.skipped_episodes.length} ep${r.skipped_episodes.length === 1 ? '' : 's'} weren't in Sonarr's episode list — try refreshing the series in Sonarr.`
        : '';
      if (r.ok && r.queued > 0) {
        const detailNote = r.detail ? ` ${r.detail}` : '';
        notify(
          `Sent ${r.queued} episode${r.queued === 1 ? '' : 's'} to Sonarr`,
          `Sonarr will search and import.${addedNote}${notInListNote}${detailNote}`,
          'success',
        );
      } else if (r.ok) {
        // Search succeeded but nothing was queued — NOT an error. Usually the
        // episodes are already in Sonarr, or its quality profile won't replace
        // the existing files. Show it as a neutral outcome, not a red failure.
        notify(
          'Nothing to queue',
          `${r.detail ?? 'All requested episodes are already in Sonarr.'}${notInListNote}`,
          'success',
        );
      } else {
        notify(
          'Sonarr couldn\'t queue the search',
          r.detail ?? 'See Sonarr logs.',
          'error',
        );
      }
    } catch (e) {
      // Backend 4xx (e.g. "Sonarr URL isn't configured.") surfaces here
      // with the specific message — no need to swap toast text by case.
      notify('Sonarr handoff failed', (e as Error).message, 'error');
    } finally {
      setSonarrSending(false);
    }
  }, [sonarrSending, pushToast]);

  // Delete-confirmation modal state. null = no modal open.
  const [pendingDelete, setPendingDelete] = useState<LibFile | null>(null);
  // Bulk delete-confirmation state — the "keep best, delete the rest" flow.
  // `files` are the ones that WILL be deleted; `keepCount`/`epCount` describe
  // what's being kept for the summary line. null = no modal open.
  const [bulkConfirm, setBulkConfirm] = useState<
    { files: LibFile[]; keepCount: number; epCount: number } | null
  >(null);

  const handleDeleteFile = useCallback(async (file: LibFile) => {
    try {
      await api.deleteFile(Number(file.id));
      setDeletedIds(prev => { const n = new Set(prev); n.add(file.id); return n; });
      // Re-pull the global files cache so the deleted row is gone for good — not
      // just hidden by this popup's optimistic `deletedIds` (which resets on
      // close, leaving a stale duplicate sign when the popup is reopened).
      window.dispatchEvent(new CustomEvent('kira:files-changed'));
      pushToast?.({ title: 'File deleted', sub: file.filename, kind: 'success' });
    } catch (e) {
      pushToast?.({ title: 'Delete failed', sub: String(e), kind: 'error' });
    } finally {
      setPendingDelete(null);
    }
  }, [pushToast]);

  // Delete MANY files in one request + one confirmation (duplicate resolution).
  // Marks every server-confirmed id as deleted so the affected groups collapse
  // at once; surfaces only the real per-file failures.
  const handleDeleteFiles = useCallback(async (files: LibFile[]) => {
    const ids = files.map(f => Number(f.id)).filter(n => Number.isFinite(n));
    if (ids.length === 0) { setBulkConfirm(null); return; }
    try {
      const res = await api.deleteFiles(ids);
      const okIds = new Set(res.deleted.map(String));
      setDeletedIds(prev => {
        const n = new Set(prev);
        files.forEach(f => { if (okIds.has(String(f.id))) n.add(f.id); });
        return n;
      });
      // Re-pull the global files cache so the dupe group is gone on reopen too
      // (the optimistic `deletedIds` above only lasts this popup's lifetime).
      if (res.count > 0) window.dispatchEvent(new CustomEvent('kira:files-changed'));
      const failed = res.failed?.length ?? 0;
      if (failed > 0) {
        pushToast?.({
          title: `Deleted ${res.count} file${res.count === 1 ? '' : 's'} · ${failed} failed`,
          sub: res.failed.map(x => x.error).slice(0, 2).join(' · '),
          kind: 'error',
        });
      } else {
        pushToast?.({
          title: `Deleted ${res.count} duplicate${res.count === 1 ? '' : 's'}`,
          sub: 'Best copy of each kept.',
          kind: 'success',
        });
      }
    } catch (e) {
      pushToast?.({ title: 'Bulk delete failed', sub: String(e), kind: 'error' });
    } finally {
      setBulkConfirm(null);
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

  // Per-episode resolver state — null when closed, otherwise the dupe group to
  // resolve. Opened from the "+N more" pill; the user picks a keeper and bulk-
  // deletes the rest in one confirm (no more click-delete-confirm per file).
  const [dupeModal, setDupeModal] = useState<{ episode: LibEpisode; files: LibFile[] } | null>(null);
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
            // Prefer the provider's OWN absolute number (TVDB/TMDB supply it
            // on cross-ref anime: "Season 4" E1 carries abs 60). That's what
            // lets absolute-named files ("Shingeki no Kyojin - 60") pair to
            // the local-numbered episode row. Fall back to the scan-synthesized
            // repEp.absolute, then to pe.episode for AniDB-native lists (where
            // the episode number already IS the absolute).
            absolute: pe.absolute_number ?? repEp?.absolute ?? (item.mediaType === 'anime' ? pe.episode : undefined),
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

    // ── Fallback: TVDB-numbered file → AniDB-relative episode ──
    // Common pain for anime: file "Attack.On.Titan.S04E17.mkv" matched
    // to an AniDB AID whose authoritative episode list is E01-E12.
    // The file's Match row stored episode=17, which doesn't appear
    // anywhere in 1-12, so the normal pairing leaves it orphaned.
    //
    // For each still-unmatched file in an anime cluster, extract the
    // episode number from its filename, compute offset from the
    // smallest unmatched episode, and drop the file into the blank
    // slot at that offset. Offset (rather than index) preserves
    // alignment when episodes are missing — e.g. user has S04E17
    // and S04E19 but is missing E18; result: E17→AniDB E01,
    // E19→AniDB E03 (not E02, which would be the index-based bug).
    //
    // Only applies to anime clusters where providerEpisodes is the
    // authoritative list. TVDB/TMDB clusters use the same episode
    // numbering on both sides so this fallback would never fire there.
    if (item.mediaType === 'anime' && providerEpisodes && providerEpisodes.length > 0) {
      const blankSlots: { outIdx: number; episode: LibEpisode }[] = [];
      out.forEach((r, outIdx) => {
        if (r.kind === 'blank' && r.episode) {
          blankSlots.push({ outIdx, episode: r.episode });
        }
      });
      // Extract season-local episode number from filename. Handles
      // standard SxxExx form. Anime-absolute forms ("- 17" / "[17]")
      // skip — those usually match correctly via the normal
      // abs-{N} path and don't need this fallback.
      const extractFileEp = (filename: string): number | null => {
        const m = filename.match(/[Ss]\d{1,2}[Ee](\d{1,3})/);
        return m ? parseInt(m[1], 10) : null;
      };
      const orphansWithEp = item.files
        .filter(f => !matchedFileIds.has(f.id) && !deletedIds.has(f.id))
        .map(f => ({ file: f, parsedEp: extractFileEp(f.filename) }))
        .filter(o => o.parsedEp !== null)
        .sort((a, b) => a.parsedEp! - b.parsedEp!);
      if (orphansWithEp.length > 0 && blankSlots.length > 0) {
        const baseEp = orphansWithEp[0].parsedEp!;
        for (const { file, parsedEp } of orphansWithEp) {
          const offset = parsedEp! - baseEp;
          const slot = blankSlots[offset];
          if (slot) {
            // Replace the blank row in-place with a paired row.
            // matchedWrong stays false — we're inferring this pairing
            // from filename positions, but the user can still see
            // exactly what we paired by reading the row.
            out[slot.outIdx] = {
              key: out[slot.outIdx].key,
              kind: 'single',
              episode: slot.episode,
              episodeIdx: out[slot.outIdx].episodeIdx,
              file,
            };
            matchedFileIds.add(file.id);
          }
        }
      }
    }

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
        {/* Ambient cover-color bleed — a blurred, enlarged copy of the poster
            behind the content so the cover's real colors spread across the
            popup. Falls back to the shell's tint gradient when there's no
            image (no-match / not yet fetched). */}
        {effectivePosterUrl && !item.noMatch ? (
          <div className="cx-bg-bleed" aria-hidden="true" style={{ backgroundImage: `url("${effectivePosterUrl}")` }} />
        ) : null}
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
              <div style={{ color: 'var(--ink-2)', fontSize: 12 }}>
                The matcher couldn't find a confident provider entry for these files.
                If Sonarr downloaded them, click <strong>Sync from Sonarr</strong> below
                to pull authoritative metadata — otherwise use{' '}
                <strong>Search for a better match</strong> to pick the right show
                manually.
              </div>
            </div>
            {/* Sync-from-Sonarr fast path. Only render when we have any
                files to heal (always true inside this banner) — the
                backend handles the "no Sonarr config" case with a
                graceful toast. Eye-catching so the user sees it before
                they reach for the slower manual-search route below. */}
            <button
              className="btn btn-primary"
              onClick={handleSonarrHeal}
              disabled={sonarrHealing}
              style={{ flex: '0 0 auto', whiteSpace: 'nowrap' }}
              title="Pull metadata from Sonarr for these files. Works when Sonarr already imported them — Sonarr knows the correct TVDB/AniDB identity."
            >
              <IcDownload />
              <span>{sonarrHealing ? 'Syncing…' : 'Sync from Sonarr'}</span>
            </button>
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
                  // (Already inside the non-movie branch — `item.kind` is
                  // 'series' | 'album' here, so no movie check needed.)
                  !!providerKey && !!providerId &&
                  (providerEpisodes === null || providerEpisodes.length === 0) &&
                  rows.length === 0
                }
                queueByEpisode={queueByEpisode}
                recentlyImported={recentlyImported}
                pushToast={pushToast}
              />
          }
        </div>

        {dupeModal ? (
          <DupesResolverModal
            item={item}
            episode={dupeModal.episode}
            files={dupeModal.files.filter(f => !deletedIds.has(f.id))}
            onClose={() => setDupeModal(null)}
            onRequestDelete={setPendingDelete}
            onBulkDelete={(losers) => setBulkConfirm({ files: losers, keepCount: 1, epCount: 1 })}
          />
        ) : null}

        {bulkConfirm ? (
          <BulkDeleteConfirmModal
            files={bulkConfirm.files}
            keepCount={bulkConfirm.keepCount}
            epCount={bulkConfirm.epCount}
            onCancel={() => setBulkConfirm(null)}
            onConfirm={() => void handleDeleteFiles(bulkConfirm.files)}
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
            <Button
              color="secondary"
              size="sm"
              iconLeading={IcX}
              onClick={handleClose}
              title="Close (Esc)"
              aria-label="Close"
            />
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
                <Button
                  color="secondary"
                  size="sm"
                  iconLeading={IcSearch}
                  onClick={handleReidentify}
                  title={!hasAnyMatch
                    ? 'Open manual search and pick the right show for these files.'
                    : 'Open manual search and pick a different show — applies to every file in this cluster.'}
                >
                  {label}
                </Button>
              );
            })()}

            {/* ── Sync from Sonarr (no-match clusters) ────────────────
                Visible specifically on no-match clusters where the
                low-confidence banner DOESN'T render (banner only fires
                when at least one file has a matched-to-episode row at
                <50% conf). True no-match clusters get this footer
                button instead so the user always has the fast path.
                Low-confidence clusters get the SAME action via the
                button embedded in the banner above — we don't render
                it here to avoid two identical buttons. */}
            {!clusterIsDead && item.noMatch ? (
              <Button
                color="secondary"
                size="sm"
                iconLeading={IcDownload}
                isLoading={sonarrHealing}
                showTextWhileLoading
                onClick={handleSonarrHeal}
                title="Pull metadata from Sonarr for these files. Works when Sonarr already imported them."
              >
                {sonarrHealing ? 'Syncing…' : 'Sync from Sonarr'}
              </Button>
            ) : null}

            {/* ── Get missing → Sonarr ────────────────────────────────
                One-click handoff for missing episodes. Hidden unless:
                  * Cluster is a TV/anime series (movies + albums skip)
                  * Provider is TVDB or AniDB (Sonarr is TVDB-centric;
                    AniDB matches cross-ref to TVDB server-side)
                  * The cluster has at least one matched file (we need
                    a Match row id to send) AND at least one missing
                    episode in the provider's authoritative list
                The backend handles "Sonarr not configured" via 400 +
                a clear toast — no need to gate the button on a probe
                of Settings here. */}
            {(() => {
              if (clusterIsDead) return null;
              if (item.kind !== 'series') return null;
              // TMDB-only matches can't go to Sonarr (it's TVDB-centric);
              // backend would reject anyway, but suppress the button to
              // avoid a button that always toasts an error.
              if (!(providerKey === 'tvdb' || providerKey === 'anidb')) return null;

              // Find a rep match id from any file in the cluster.
              // Reads the adapter-surfaced `matchId` (LibFile.matchId)
              // — the bare integer is enough for cluster-level cross-
              // system actions without re-fetching the full match.
              const repWithMatch = item.files.find(f => f.matchId != null);
              const matchId = repWithMatch?.matchId;
              if (matchId == null) return null;

              // Missing episodes = episodes in the PROVIDER's full list
              // that no file in the cluster is matched to.
              //
              // We use `providerEpisodes` (the lazy-fetched authoritative
              // list from `/series/{provider}/{id}/episodes`) — not
              // `item.episodes`. The adapter populates `item.episodes`
              // from MATCH ROWS only, which means it only contains
              // episodes the user has files for. The popup's right
              // column renders from a DIFFERENT merged list built each
              // render from `providerEpisodes` (see line ~564). Pre-fix,
              // we were reading from the matches-only list and finding
              // "0 missing" even when the popup was visually rendering
              // 20 blank-on-left missing-episode rows.
              //
              // If `providerEpisodes` hasn't loaded yet (initial render,
              // before the lazy fetch resolves), hide the button — we
              // can't know what's missing without the full list. The
              // button will appear once the fetch completes and React
              // re-renders.
              if (!providerEpisodes || providerEpisodes.length === 0) return null;

              // Build the set of episode NUMBERS the user has files for.
              // We dedup by episode number alone (not (season, episode)
              // tuples) because the popup's `providerEpisodes` and the
              // matched `item.episodes[idx]` can disagree on the season
              // tag — AniDB's native episode list reports season=1 for
              // everything (it has one AID per season), while the file's
              // match.season_number came from Fribb's TVDB cross-ref
              // (e.g. season=2 for Frieren S2). Both lists are scoped to
              // a single season's fetch, so collapsing to "is this
              // episode number present?" gives the right missing set
              // regardless of how the two layers disagree on labels.
              const haveEpisodeNumbers = new Set<number>();
              for (const f of item.files) {
                if (typeof f.matchedToEpisode !== 'number') continue;
                const merged = item.episodes[f.matchedToEpisode];
                if (merged && typeof merged.episode === 'number') {
                  haveEpisodeNumbers.add(merged.episode);
                }
              }
              const missingNumbers: number[] = [];
              for (const pe of providerEpisodes) {
                if (typeof pe.episode !== 'number') continue;
                if (haveEpisodeNumbers.has(pe.episode)) continue;
                missingNumbers.push(pe.episode);
              }
              if (missingNumbers.length === 0) return null;

              // Season: the provider list is fetched against ONE season
              // (via seasonForFetch). For AniDB clusters where the
              // backend cross-refs to TVDB, providerEpisodes[0].season
              // is the TVDB-side season number — which is what Sonarr
              // wants. For TVDB-direct clusters it's the TVDB season
              // directly. Either way, taking the first provider
              // episode's season gives us the right value for Sonarr.
              const seasonNum = providerEpisodes[0]?.season ?? 1;

              const providerLabel = providerKey === 'anidb' ? 'AniDB→TVDB' : 'TVDB';
              return (
                <Button
                  color="secondary"
                  size="sm"
                  iconLeading={IcDownload}
                  isLoading={sonarrSending}
                  showTextWhileLoading
                  onClick={() => void handleSonarrSendMissing(matchId, seasonNum, missingNumbers)}
                  title={`Tell Sonarr to search for the ${missingNumbers.length} missing episode${missingNumbers.length === 1 ? '' : 's'} of this season. (${providerLabel})`}
                >
                  {sonarrSending ? 'Sending…' : `Get missing (${missingNumbers.length}) → Sonarr`}
                </Button>
              );
            })()}

            {/* Push the action triad (dupes / reject / approve) to the
                far right so Close + Re-match sit left, destructive +
                primary stay right — the classic "secondary | primary"
                footer layout. */}
            <span className="cx-foot-spacer" />
            {/* Dupe resolver — only renders when the cluster has any episodes
                with duplicate files. One click gathers the loser copies across
                EVERY duplicated episode (keeping the best of each) and opens a
                single confirmation, so 10 duplicates take one confirm, not ten
                delete-then-confirm round trips. To override a keeper, open a
                single episode via its "+N more" pill instead. */}
            {(() => {
              // Each group's live files are already sorted best-first (dupeAll),
              // so the first is the keeper and the rest are losers.
              const losers: LibFile[] = [];
              let epCount = 0;
              for (const r of rows) {
                if (r.kind === 'dupe-primary' && r.episode && r.dupeAll && r.dupeAll.length > 1) {
                  const live = r.dupeAll.filter(f => !deletedIds.has(f.id));
                  if (live.length > 1) {
                    epCount += 1;
                    losers.push(...live.slice(1));
                  }
                }
              }
              if (losers.length === 0) return null;
              return (
                <Button
                  color="secondary"
                  size="sm"
                  className="bg-[rgba(255,201,74,0.14)] text-conf-mid ring-[rgba(255,201,74,0.5)] hover:bg-[rgba(255,201,74,0.22)]"
                  iconLeading={<IcAlertTri className="size-4 text-conf-mid" />}
                  title="Keep the best copy of every duplicated episode and delete the rest — in one confirmation."
                  onClick={() => setBulkConfirm({ files: losers, keepCount: epCount, epCount })}
                >
                  Resolve {losers.length} duplicate{losers.length === 1 ? '' : 's'}
                </Button>
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
                <Button
                  color="secondary-destructive"
                  size="sm"
                  iconLeading={IcX}
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
                  {label}
                </Button>
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
                  <Button
                    color="primary"
                    size="sm"
                    iconLeading={IcSearch}
                    onClick={() => {
                      // Hand control to the Manual Search modal — same
                      // entry point the per-row "Search" link uses.
                      onManualSearch(item, null, orphanFileIdx);
                    }}
                    title="Open Manual Search to find a match for this file"
                  >
                    Search manually
                  </Button>
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
                    <Button
                      color="primary"
                      size="sm"
                      iconLeading={IcRefresh}
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
                      Restore {rejectedCount} file{rejectedCount === 1 ? '' : 's'}
                    </Button>
                  );
                }
                return (
                  <Button
                    color="primary"
                    size="sm"
                    iconLeading={IcCheck}
                    isDisabled
                    title="All files in this cluster are already renamed"
                  >
                    Nothing to rename
                  </Button>
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
                  <Button
                    color="primary"
                    size="sm"
                    iconLeading={IcSearch}
                    onClick={() => {
                      // Open Manual Search prefilled for the first
                      // eligible file — the user can search the right
                      // provider for the real match.
                      const firstIdx = item.files.indexOf(eligible[0]);
                      onManualSearch(item, null, firstIdx >= 0 ? firstIdx : null);
                    }}
                    title={`Matches are low-confidence (best is ${Math.round(maxConf)}%). Search manually to find the real series.`}
                  >
                    Search for a better match
                  </Button>
                );
              }

              return (
                <Button
                  color="primary"
                  size="sm"
                  iconLeading={IcCheck}
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
                  Approve all {eligibleCount}
                </Button>
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


// ─────────────────────────────────────────────────────────────────────
// Series / album body — two-column synced scroll
// ─────────────────────────────────────────────────────────────────────



// ─────────────────────────────────────────────────────────────────────
// DownloadProgressRow — replaces the "No file for this episode" blank
// when Sonarr is actively working on the episode. Live updates every
// 4s via the parent's useSonarrQueuePopup hook.
//
// Visual design:
//   * The whole row is a low-opacity green band (cx-row.downloading)
//     when status === 'downloading'. The green-fill is rendered as a
//     ::before pseudo-element with `width: <progress>%` — see
//     index.css `.cx-row.downloading::before`.
//   * Status chips (queued / importing / failed / warning) get a
//     coloured pill on the right side mirroring the existing
//     status-pill style.
//   * Release name (Sonarr-side filename) and ETA show as the row's
//     secondary text — gives the user concrete progress signal.
//
// The thumb on the left reuses .cx-pair-thumb.file with a pulsing
// "···" indicator so the user understands this slot is in motion.
// ─────────────────────────────────────────────────────────────────────

// ─────────────────────────────────────────────────────────────────────
// ForceImportConfirmModal — preview-then-commit confirmation for the
// Force Import button on a stuck Sonarr queue entry.
//
// Why it exists: a "Force import" click previously fired the import
// command immediately with importMode="Move". On a cross-device move
// where copy-then-delete-source can partially fail, Sonarr ended up
// deleting the source while the destination was incomplete or at a
// different path than expected. Two real episodes vanished (AoT
// S01E05 + E06) before this modal landed. Now: the user sees
// EXACTLY where Sonarr plans to write the file + can pick Copy
// (safer, source stays) or Move (Sonarr's default, source gone).
// ─────────────────────────────────────────────────────────────────────


// ─────────────────────────────────────────────────────────────────────
// JustImportedRow — bridges the gap between Sonarr's "queue entry
// disappeared" moment and Kira's auto-scan picking up the new file
// on disk. Without this, the row would revert to "No file for this
// episode" right after the user watched a green bar fill to 100%.
//
// Auto-clears when:
//   * The real file appears on the LibraryItem (popup re-renders with
//     a matched file, the row swaps to the proper FileRowCell content)
//   * 5 minutes elapse (popup's recentlyImported expiry interval)
// ─────────────────────────────────────────────────────────────────────

// ─────────────────────────────────────────────────────────────────────
// formatUpcomingAirDate — relative date formatter for unaired episodes
//
// Returns null when the date isn't in the future (so the caller
// falls through to the generic "No file" placeholder), and a
// human-readable label otherwise. Tiers:
//   today                → "Airs today"
//   tomorrow             → "Airs tomorrow"
//   within 7 days        → "Airs Monday" / "Airs Friday"
//   within 30 days       → "Airs in N days"
//   farther out          → "Airs Mar 15" / "Airs Mar 15, 2027"
// Year suffix appears only when the air date isn't this year.
// ─────────────────────────────────────────────────────────────────────


// ─────────────────────────────────────────────────────────────────────
// UpcomingEpisodeRow — placeholder for unaired episodes. Renders
// the air date prominently so the user understands the gap is
// "not yet aired" rather than "I forgot to download this".
// ─────────────────────────────────────────────────────────────────────



// ─────────────────────────────────────────────────────────────────────
// Movie body
// ─────────────────────────────────────────────────────────────────────


// ─────────────────────────────────────────────────────────────────────
// Dupes resolver — drilled into from the "Duplicate · N files" pill on
// the main popup. Shows every file claiming this episode side-by-side
// with full quality/source/codec/release-group chips and a trash button
// per row. Closes itself when the count drops to 1 (problem solved).
// ─────────────────────────────────────────────────────────────────────

