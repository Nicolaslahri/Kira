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
import { createPortal } from 'react-dom';
import type { LibraryItem, LibEpisode, LibFile, MediaType } from '../lib/types';
import {
  IcCheck, IcX, IcSearch, IcRefresh, IcAlertTri, IcExternal, IcChevDown, IcTrash, IcDownload,
} from '../lib/icons';
import { api } from '../lib/api';
import { MediaTypeIcon } from './ui';
import { Button } from './base/buttons/button';
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

interface MarqueeTextProps {
  children: React.ReactNode;
  className?: string;
  /** Approx scrolling speed in pixels-per-second. Higher = snappier.
   *  The math: one ping-pong cycle is two slides of (overflow_amount)
   *  each, plus two short pauses. At 100 px/s, a 200px overflow
   *  cycles in about 6s total — readable without being frenetic. */
  speed?: number;
}

function MarqueeText({ children, className, speed = 100 }: MarqueeTextProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const innerRef = useRef<HTMLSpanElement>(null);
  const [overflows, setOverflows] = useState(false);
  const [durationSec, setDurationSec] = useState(8);
  const [shiftPx, setShiftPx] = useState(0);

  useLayoutEffect(() => {
    const container = containerRef.current;
    const inner = innerRef.current;
    if (!container || !inner) return;

    const check = () => {
      const naturalWidth = inner.scrollWidth;
      const visibleWidth = container.clientWidth;
      const overflowAmount = naturalWidth - visibleWidth;
      const isOverflow = overflowAmount > 2;
      setOverflows(isOverflow);
      if (isOverflow) {
        // Negative shift: translateX(shiftPx) moves the inner LEFT
        // by exactly the overflow amount so the END of the text
        // aligns with the right edge of the container. The small
        // -4px padding gives a touch of breathing room so the last
        // character isn't kissing the container border.
        setShiftPx(-(overflowAmount + 4));
        // Cycle math: two slides of overflowAmount + 1.4s of pauses.
        // Slide time = overflowAmount / speed each direction.
        const slideTimeSec = overflowAmount / speed;
        const totalCycle = (slideTimeSec * 2) + 1.4;
        // Floor at 4s so tiny overflows still have a visible pause
        // rhythm; ceiling at 18s so absurd-length text doesn't
        // demand the user wait forever for the other end.
        const d = Math.max(4, Math.min(18, totalCycle));
        setDurationSec(d);
      }
    };
    check();
    const ro = new ResizeObserver(check);
    ro.observe(container);
    ro.observe(inner);
    return () => ro.disconnect();
  }, [children, speed]);

  return (
    <div ref={containerRef} className={`marquee-outer ${className ?? ''}`}>
      <span
        ref={innerRef}
        className={overflows ? 'marquee-inner scrolling' : 'marquee-inner'}
        style={overflows ? {
          ['--marquee-duration' as never]: `${durationSec}s`,
          ['--marquee-shift' as never]: `${shiftPx}px`,
        } as React.CSSProperties : undefined}
      >
        {children}
      </span>
    </div>
  );
}


// Live Sonarr queue item, as seen by the popup. Mirrors the backend
// QueueItemOut shape — kept inline (rather than imported from a shared
// types file) so the rest of the app doesn't take a hard dep on Sonarr
// types until/unless Phase B's library-grid pill code wants them too.
export interface SonarrQueueEntry {
  tvdb_id: number;
  /** Reverse Fribb cross-ref — populated for anime queue items only. */
  anidb_aid?: number | null;
  season: number;
  episode_number: number;
  episode_title: string | null;
  status: string;            // see normalized states in backend
  progress_pct: number;      // 0..100
  eta_seconds: number | null;
  size_bytes: number | null;
  size_left_bytes: number | null;
  release_title: string | null;
  protocol: string | null;
  error_message: string | null;
  download_client: string | null;
  /** Sonarr's queue.id (numeric) and downloadId (string). We pass
   *  `download_id` back to the retry-import endpoint when the user
   *  clicks "Force import" on a stuck entry. */
  queue_id?: number | null;
  download_id?: string | null;
  /** True when Sonarr is stuck on "Downloaded - Unable to Import
   *  Automatically". Renders a distinct "Stuck — manual import
   *  needed" banner with a one-click fix button in the popup row. */
  needs_manual_import?: boolean;
}

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
function useSonarrQueuePopup(matchId: number | null): SonarrQueueEntry[] | null {
  const [items, setItems] = useState<SonarrQueueEntry[] | null>(null);
  useEffect(() => {
    if (matchId == null) {
      setItems(null);
      return;
    }
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    // Backoff state — if the endpoint repeatedly errors we slow down
    // (4s → 12s → 30s → stop) so we don't burn a 4s tick forever on a
    // configuration that'll never succeed. Reset on first success.
    let errCount = 0;
    const tick = async () => {
      try {
        const r = await api.sonarrQueue({ match_id: matchId });
        if (cancelled) return;
        setItems(r.items);
        errCount = 0;
        // 1.5s while popup is open. The rAF extrapolation in
        // DownloadProgressRow interpolates smoothly between polls
        // using Sonarr's ETA, so the bar never looks stuck — fast
        // polling just means the extrapolated prediction gets
        // re-anchored against ground truth more often, reducing
        // any visible snap when reality diverges from prediction.
        timer = setTimeout(tick, 1500);
      } catch (e) {
        if (cancelled) return;
        errCount += 1;
        // 400 = Sonarr not configured. Don't keep polling forever —
        // surface the empty state to the caller and stop. The user
        // will reopen the popup after configuring.
        const msg = String(e ?? '');
        if (msg.includes('Sonarr URL') || msg.includes('Sonarr API key') || msg.includes('not configured')) {
          setItems(null);
          return; // intentionally NO further scheduling
        }
        // Transient (Sonarr down, network blip). Back off but keep
        // trying — Sonarr coming back online should auto-recover the
        // live progress without the user needing to reopen the popup.
        const delay = errCount <= 1 ? 4000 : errCount <= 3 ? 12000 : 30000;
        if (errCount > 8) return;   // give up entirely after ~6 minutes
        timer = setTimeout(tick, delay);
      }
    };
    void tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [matchId]);
  return items;
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
                <Button
                  color="secondary"
                  size="sm"
                  className="bg-[rgba(255,201,74,0.14)] text-conf-mid ring-[rgba(255,201,74,0.5)] hover:bg-[rgba(255,201,74,0.22)]"
                  iconLeading={<IcAlertTri className="size-4 text-conf-mid" />}
                  title="Pick which copy to keep for each duplicate; the rest are deleted from disk."
                  onClick={() => {
                    const [first, ...rest] = dupes;
                    setDupeQueueTotal(dupes.length);
                    setDupeQueue(rest);
                    setDupeModal(first);
                  }}
                >
                  Resolve {dupes.length} duplicate{dupes.length === 1 ? '' : 's'}
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
          {/* Romaji + native-script title for anime/foreign series.
              The user explicitly removed the "a.k.a. <Localized name>"
              chips (Spanish/Italian/French/etc. translations) — they
              added noise without value in a single-locale UI. */}
          {(item.titleRomaji || item.titleNative) ? (
            <div className="cx-hero-alt">
              {item.titleRomaji && item.titleRomaji !== item.title ? <span>{item.titleRomaji}</span> : null}
              {item.titleNative ? <span>{item.titleNative}</span> : null}
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
  /** Sonarr's in-flight downloads keyed by episode number. Drives the
   *  per-row "Downloading" / "Queued" / "Importing" progress UI in
   *  place of the static "No file for this episode" placeholder. Null
   *  when Sonarr isn't configured (or hasn't responded yet); the
   *  blank rows fall back to the regular static placeholder. */
  queueByEpisode?: Map<number, SonarrQueueEntry>;
  /** Episode numbers whose Sonarr download has finished and queue
   *  entry has vanished, but the file hasn't been picked up by a
   *  Kira scan yet. The blank row renders a "Just imported, scanning
   *  …" transitional placeholder instead of the static "No file"
   *  state during this window (~5 min, naturally cleared when the
   *  file appears or expires). */
  recentlyImported?: Map<number, number>;
  /** Toast handler threaded down to DownloadProgressRow so its
   *  "Force import" button can surface success/failure feedback. */
  pushToast?: (toast: { title: string; sub?: string; kind?: 'success' | 'error' }) => void;
}

function SeriesBody({ item, rows, updateFile, onManualSearch, onOpenDupeModal, episodesLoading, queueByEpisode, recentlyImported, pushToast }: SeriesBodyProps) {
  const leftRef = useRef<HTMLDivElement>(null);
  const rightRef = useRef<HTMLDivElement>(null);
  const syncing = useRef(false);
  // PB-2: rAF-coalesce the scroll-sync write. The original wrote
  // scrollTop synchronously on every scroll event — at 120Hz that's
  // 240 forced reflows/sec across two columns. Coalescing into one
  // write per frame cuts paint work to display-refresh rate without
  // changing the visual sync feel.
  const rafIdRef = useRef<number | null>(null);

  // ── Progressive render for huge clusters (One Piece = 1000+ episodes) ──
  // Rendering the full row list × 2 columns synchronously on open froze the
  // popup for ~5s. Instead we mount a small initial slice (so the popup opens
  // instantly) and grow it over subsequent frames until everything is in the
  // DOM. We never shrink an already-expanded list, so per-row edits (status
  // toggles, renames) don't cause a flicker — this only engages on first
  // mount and when the provider's full episode list arrives and balloons
  // `rows`. Small series (≤ INITIAL) render fully on the first frame, exactly
  // as before.
  const INITIAL_ROWS = 60;
  const ROW_STEP = 120;
  const [visibleCount, setVisibleCount] = useState(() => Math.min(rows.length, INITIAL_ROWS));
  useEffect(() => {
    // Clamp to the new length without collapsing below the initial chunk.
    setVisibleCount(c => Math.min(Math.max(c, INITIAL_ROWS), rows.length));
  }, [rows.length]);
  useEffect(() => {
    if (visibleCount >= rows.length) return;
    const id = requestAnimationFrame(() => setVisibleCount(c => Math.min(rows.length, c + ROW_STEP)));
    return () => cancelAnimationFrame(id);
  }, [visibleCount, rows.length]);
  const shownRows = visibleCount >= rows.length ? rows : rows.slice(0, visibleCount);

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
            : shownRows.map(r => {
                // Look up queue state by the row's episode number so
                // both columns (file-side blank → progress bar, episode-
                // side details → "downloading" badge) stay in sync.
                const qEntry = r.episode && queueByEpisode
                  ? queueByEpisode.get(r.episode.episode) ?? null
                  : null;
                const justImported = r.episode && recentlyImported
                  ? recentlyImported.has(r.episode.episode)
                  : false;
                return (
                  <FileRowCell
                    key={r.key}
                    row={r}
                    item={item}
                    updateFile={updateFile}
                    onManualSearch={onManualSearch}
                    onOpenDupeModal={onOpenDupeModal}
                    queueEntry={qEntry}
                    justImported={justImported}
                    pushToast={pushToast}
                  />
                );
              })}
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
            : shownRows.map(r => {
                const qEntry = r.episode && queueByEpisode
                  ? queueByEpisode.get(r.episode.episode) ?? null
                  : null;
                const justImported = r.episode && recentlyImported
                  ? recentlyImported.has(r.episode.episode)
                  : false;
                return (
                  <EpisodeRowCell
                    key={r.key}
                    row={r}
                    item={item}
                    updateFile={updateFile}
                    onManualSearch={onManualSearch}
                    onOpenDupeModal={onOpenDupeModal}
                    queueEntry={qEntry}
                    justImported={justImported}
                  />
                );
              })}
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
  /** Sonarr's in-flight progress for this row's episode, when known.
   *  Used by the FileRowCell blank-state to render a download-progress
   *  row in place of the static "No file for this episode" placeholder.
   *  Memo equality (rowsEqualFile) compares the progress/status fields
   *  so an updated tick re-renders this row but not its neighbors. */
  queueEntry?: SonarrQueueEntry | null;
  /** Set by the parent when this episode JUST finished a Sonarr download
   *  but Kira hasn't yet scanned the new file from disk. Renders a
   *  "Just imported, scanning…" transitional row instead of the static
   *  "No file" placeholder, bridging the gap between download-complete
   *  and file-on-disk-appears-in-Kira. */
  justImported?: boolean;
  /** Toast surface for action buttons inside the row (e.g. the
   *  stuck-import "Force import" retry). Threaded down from CoverPopup
   *  via SeriesBody. */
  pushToast?: (toast: { title: string; sub?: string; kind?: 'success' | 'error' }) => void;
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
  // Sonarr queue progress changes every poll. Compare the fields that
  // visibly affect the row — adding the queue entry to the memo
  // without checking these would freeze the progress bar at 0% on the
  // first render. Equal-by-identity short-circuits when both are null.
  const qa = a.queueEntry, qb = b.queueEntry;
  if ((qa == null) !== (qb == null)) return false;
  if (qa && qb) {
    if (qa.status !== qb.status) return false;
    if (qa.progress_pct !== qb.progress_pct) return false;
    if (qa.eta_seconds !== qb.eta_seconds) return false;
    if (qa.error_message !== qb.error_message) return false;
    // release_title can flip mid-download if Sonarr swaps to a better
    // release; rare but real, so compare for completeness.
    if (qa.release_title !== qb.release_title) return false;
  }
  // "Just imported" transitional flag — flips when a Sonarr completion
  // is detected, drives the post-import placeholder. Skipping this
  // would leave the row stuck on either the static "No file" state
  // (after a download finishes) or the imported placeholder forever
  // (after the real file appears).
  if (a.justImported !== b.justImported) return false;
  // Callback identity not checked — they're recreated each render by
  // the parent, and the row already short-circuits via row.key / file /
  // episode content above.
  return true;
};

function FileRowCellImpl({ row, item, updateFile, onManualSearch, onOpenDupeModal, queueEntry, justImported, pushToast }: RowCellProps) {
  const file = row.file;
  const fileIdx = file ? item.files.indexOf(file) : -1;

  // Blank — episode without a file.
  //
  // Three paths:
  // (1) Sonarr is actively working on it (queueEntry != null) → render
  //     a download-progress row with a green-fill bar + status label.
  //     Live progress that updates every 2s while the popup is open.
  //
  // (2) Sonarr JUST finished + the file isn't yet scanned in (queueEntry
  //     gone but justImported=true) → render an "Imported · scanning…"
  //     transitional row so the user doesn't see an empty placeholder
  //     during the brief window between Sonarr's import-complete and
  //     Kira's auto-scan finding the new file on disk. Self-clears
  //     when the file appears or after 5 minutes.
  //
  // (3) No queue entry and not recently imported → keep the original
  //     static placeholder. Manual Search wouldn't help (it picks
  //     metadata, not files from disk); the honest answers are "scan
  //     more folders" or "use Get Missing → Sonarr in the footer".
  //     No CTA on the row itself — the footer button is the
  //     discoverable entry point.
  void onManualSearch;
  if (!file) {
    if (queueEntry) {
      return <DownloadProgressRow queueEntry={queueEntry} episode={row.episode} pushToast={pushToast} />;
    }
    if (justImported) {
      return <JustImportedRow episode={row.episode} />;
    }
    // Has the episode aired yet? If the air date is in the future,
    // "No file for this episode" is misleading — the file CAN'T exist
    // yet. Render a friendlier "Upcoming · airs Monday" state so the
    // user can tell at a glance which gaps are "not yet aired" vs
    // "aired but I don't have it". Detection is based on the episode's
    // `airDate` (ISO date string from the provider).
    const upcomingText = row.episode?.airDate
      ? formatUpcomingAirDate(row.episode.airDate)
      : null;
    if (upcomingText) {
      return <UpcomingEpisodeRow episode={row.episode!} airDateLabel={upcomingText} />;
    }
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
          {/* Marquee both filename and folder so long release-group
              names and deep paths become readable. Plain truncated
              text + browser tooltip is the fallback when overflow
              isn't detected or motion is reduced. */}
          <MarqueeText className="cx-row-title mono">
            <span title={file.filename}>{file.filename}</span>
          </MarqueeText>
          <MarqueeText className="cx-row-sub mono">
            <span className="seg" title={file.folder}>{file.folder}</span>
          </MarqueeText>
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
                file.matchId != null
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

function formatEta(seconds: number | null): string | null {
  if (seconds == null || seconds <= 0) return null;
  if (seconds < 60) return `${seconds}s left`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min left`;
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  return m > 0 ? `${h}h ${m}m left` : `${h}h left`;
}

function formatBytes(n: number | null): string | null {
  if (n == null || n <= 0) return null;
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(0)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function statusLabel(status: string): string {
  switch (status) {
    case 'queued':       return 'Queued';
    case 'searching':    return 'Searching';
    case 'downloading':  return 'Downloading';
    case 'importing':    return 'Importing';
    case 'completed':    return 'Imported';
    case 'failed':       return 'Failed';
    case 'warning':      return 'Warning';
    default:             return status;
  }
}

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

interface ForceImportConfirmModalProps {
  candidates: Array<{
    source_path: string;
    destination_root: string;
    series_title: string;
    series_id: number;
    episode_labels: string[];
    episode_ids: number[];
    quality_name: string | null;
    release_group: string | null;
    rejection_reasons: string[];
  }>;
  importMode: 'Copy' | 'Move';
  onChangeMode: (m: 'Copy' | 'Move') => void;
  onCancel: () => void;
  onConfirm: () => void;
  confirming: boolean;
}

function ForceImportConfirmModal({
  candidates, importMode, onChangeMode, onCancel, onConfirm, confirming,
}: ForceImportConfirmModalProps) {
  const importableCount = candidates.filter(c => c.rejection_reasons.length === 0).length;
  const blockedCount = candidates.length - importableCount;

  // Portal to document.body so the modal escapes the DownloadProgressRow's
  // stacking context. The row sits deep inside cx-shell → cx-main →
  // cx-body → cx-col → cx-row; the popup's transform on cx-shell creates
  // a stacking context that traps any descendant regardless of z-index.
  // Portaling to body lets the modal stack above the entire popup like
  // the Dupes / Delete modals do (those are rendered at the popup root
  // and so naturally escape — same goal, different mechanism).
  return createPortal(
    <div
      onClick={onCancel}
      style={{
        position: 'fixed', inset: 0,
        background: 'rgba(7, 6, 12, 0.78)',
        backdropFilter: 'blur(6px)',
        WebkitBackdropFilter: 'blur(6px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        zIndex: 12000,
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
          maxHeight: '86vh',
          overflow: 'hidden',
          display: 'flex', flexDirection: 'column',
          border: '1px solid var(--line-strong)',
          boxShadow: '0 24px 60px rgba(0, 0, 0, 0.6)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 14, marginBottom: 16 }}>
          <span style={{
            display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
            width: 40, height: 40, borderRadius: 8,
            background: 'rgba(255, 201, 74, 0.15)',
            color: 'var(--conf-mid)',
            flexShrink: 0,
          }}>
            <IcAlertTri />
          </span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <h3 style={{ margin: 0, fontSize: 17, fontWeight: 600 }}>
              Confirm manual import
            </h3>
            <div style={{ fontSize: 13, color: 'var(--ink-2)', marginTop: 4, lineHeight: 1.45 }}>
              Sonarr will write {importableCount} file
              {importableCount === 1 ? '' : 's'} to your library using
              the mapping below.
              {blockedCount > 0 ? (
                <span style={{ color: 'var(--conf-low)', marginLeft: 6 }}>
                  {blockedCount} file{blockedCount === 1 ? '' : 's'} blocked by Sonarr rejections.
                </span>
              ) : null}
            </div>
          </div>
        </div>

        <div style={{ overflowY: 'auto', flex: 1, margin: '0 -8px', padding: '0 8px' }}>
          {candidates.map((c, i) => (
            <div
              key={i}
              style={{
                marginBottom: 12,
                padding: '12px 14px',
                borderRadius: 10,
                background: c.rejection_reasons.length > 0
                  ? 'rgba(255, 91, 110, 0.06)'
                  : 'rgba(40, 217, 160, 0.04)',
                border: '1px solid ' + (c.rejection_reasons.length > 0
                  ? 'rgba(255, 91, 110, 0.30)'
                  : 'rgba(40, 217, 160, 0.24)'),
              }}
            >
              <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink-1)', marginBottom: 8 }}>
                {c.series_title}
                {c.episode_labels.length > 0 ? (
                  <span style={{ color: 'var(--ink-3)', fontWeight: 500, marginLeft: 8 }}>
                    · {c.episode_labels.join(', ')}
                  </span>
                ) : null}
                {c.quality_name ? (
                  <span style={{
                    fontSize: 11, padding: '2px 8px', borderRadius: 4,
                    background: 'var(--glass-2)', color: 'var(--ink-2)',
                    marginLeft: 8, fontWeight: 500,
                  }}>{c.quality_name}</span>
                ) : null}
              </div>

              <div style={{ fontSize: 11.5, color: 'var(--ink-3)', marginBottom: 6 }}>
                <strong style={{ color: 'var(--ink-2)' }}>From:</strong>
                <code style={{
                  marginLeft: 6, color: 'var(--ink-2)', wordBreak: 'break-all',
                }}>{c.source_path}</code>
              </div>
              <div style={{ fontSize: 11.5, color: 'var(--ink-3)' }}>
                <strong style={{ color: 'var(--ink-2)' }}>To:</strong>
                <code style={{
                  marginLeft: 6,
                  color: c.rejection_reasons.length > 0 ? 'var(--ink-4)' : 'var(--conf-high)',
                  wordBreak: 'break-all',
                }}>{c.destination_root}</code>
                <span style={{ color: 'var(--ink-4)', marginLeft: 6, fontSize: 11 }}>
                  (under Sonarr's series folder; exact filename via Sonarr's template)
                </span>
              </div>

              {c.rejection_reasons.length > 0 ? (
                <div style={{ marginTop: 8, fontSize: 11, color: 'var(--conf-low)' }}>
                  <strong>Sonarr rejected:</strong>{' '}
                  {c.rejection_reasons.join(' · ')}
                </div>
              ) : null}
            </div>
          ))}
        </div>

        {/* Import-mode selector — defaults to Copy (safer). Move
            cleans up the source but if the move partially fails the
            source can vanish. We document the trade-off inline so
            the user makes an informed choice. */}
        <div
          style={{
            marginTop: 16,
            padding: '12px 14px',
            borderRadius: 8,
            background: 'var(--glass-2)',
            border: '1px solid var(--line)',
            fontSize: 12.5,
            lineHeight: 1.5,
          }}
        >
          <div style={{ fontWeight: 600, marginBottom: 8, color: 'var(--ink-1)' }}>
            Import mode
          </div>
          <label style={{ display: 'flex', alignItems: 'flex-start', gap: 8, marginBottom: 8, cursor: 'pointer' }}>
            <input
              type="radio"
              name="import-mode"
              checked={importMode === 'Copy'}
              onChange={() => onChangeMode('Copy')}
              style={{ accentColor: 'var(--conf-high)', marginTop: 3 }}
            />
            <div>
              <div style={{ color: 'var(--ink-1)', fontWeight: 500 }}>
                Copy <span style={{ color: 'var(--conf-high)', fontSize: 11 }}>(recommended)</span>
              </div>
              <div style={{ color: 'var(--ink-3)', fontSize: 11.5 }}>
                Source file stays in the download client's folder. Safer:
                if the import fails for any reason, the source survives.
                Costs disk space until your download client's retention rule
                cleans it up.
              </div>
            </div>
          </label>
          <label style={{ display: 'flex', alignItems: 'flex-start', gap: 8, cursor: 'pointer' }}>
            <input
              type="radio"
              name="import-mode"
              checked={importMode === 'Move'}
              onChange={() => onChangeMode('Move')}
              style={{ accentColor: 'var(--conf-mid)', marginTop: 3 }}
            />
            <div>
              <div style={{ color: 'var(--ink-1)', fontWeight: 500 }}>
                Move <span style={{ color: 'var(--conf-mid)', fontSize: 11 }}>(deletes source)</span>
              </div>
              <div style={{ color: 'var(--ink-3)', fontSize: 11.5 }}>
                Sonarr deletes the source after the move. Saves disk space
                but a partial-move failure on cross-device transfers can lose
                the source while leaving the destination incomplete. The
                AoT S01E05/E06 incident happened with this mode.
              </div>
            </div>
          </label>
        </div>

        <div
          style={{
            marginTop: 18, paddingTop: 14,
            borderTop: '1px solid var(--line)',
            display: 'flex', justifyContent: 'flex-end', gap: 10,
          }}
        >
          <button
            onClick={onCancel}
            disabled={confirming}
            style={{
              padding: '9px 16px', borderRadius: 8,
              background: 'var(--glass-2)', color: 'var(--ink)',
              border: '1px solid var(--line)',
              fontSize: 13, fontWeight: 500,
              cursor: confirming ? 'wait' : 'pointer',
            }}
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={confirming || importableCount === 0}
            style={{
              padding: '9px 18px', borderRadius: 8,
              background: importableCount > 0 ? 'var(--conf-high)' : 'rgba(40, 217, 160, 0.25)',
              color: importableCount > 0 ? '#022b1c' : 'var(--ink-3)',
              border: 'none',
              fontSize: 13, fontWeight: 600,
              cursor: confirming
                ? 'wait'
                : (importableCount === 0 ? 'not-allowed' : 'pointer'),
              opacity: importableCount === 0 ? 0.55 : 1,
              display: 'inline-flex', alignItems: 'center', gap: 6,
            }}
          >
            <IcDownload />
            {confirming
              ? 'Importing…'
              : importableCount === 0
                ? 'Nothing to import'
                : `Import ${importableCount} file${importableCount === 1 ? '' : 's'} (${importMode})`}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

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

const _WEEKDAYS = [
  'Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday',
];
const _MONTHS = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
];

function formatUpcomingAirDate(iso: string): string | null {
  // Provider dates are typically `YYYY-MM-DD` (date only, no time).
  // We compare against today's local-midnight so an episode airing
  // "today" is correctly treated as today even though its parsed
  // Date object lands at 00:00 UTC. Off-by-one timezone bugs in
  // this comparison would either show today's episode as "Airs
  // today" the day before it actually airs (annoying) or miss
  // calling out today's episode at all (worse).
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return null;

  // Truncate both sides to midnight of their respective local day.
  const air = new Date(parsed.getFullYear(), parsed.getMonth(), parsed.getDate());
  const today = new Date();
  today.setHours(0, 0, 0, 0);

  const dayMs = 86_400_000;
  const daysAhead = Math.round((air.getTime() - today.getTime()) / dayMs);

  if (daysAhead < 0) return null;     // already aired — caller uses "No file"
  if (daysAhead === 0) return 'Airs today';
  if (daysAhead === 1) return 'Airs tomorrow';
  if (daysAhead <= 7) return `Airs ${_WEEKDAYS[air.getDay()]}`;
  if (daysAhead <= 30) return `Airs in ${daysAhead} days`;
  const monthName = _MONTHS[air.getMonth()];
  const day = air.getDate();
  const includeYear = air.getFullYear() !== today.getFullYear();
  return includeYear
    ? `Airs ${monthName} ${day}, ${air.getFullYear()}`
    : `Airs ${monthName} ${day}`;
}


// ─────────────────────────────────────────────────────────────────────
// UpcomingEpisodeRow — placeholder for unaired episodes. Renders
// the air date prominently so the user understands the gap is
// "not yet aired" rather than "I forgot to download this".
// ─────────────────────────────────────────────────────────────────────

interface UpcomingEpisodeRowProps {
  episode: LibEpisode;
  airDateLabel: string;
}

function UpcomingEpisodeRow({ episode, airDateLabel }: UpcomingEpisodeRowProps) {
  return (
    <div className="cx-row blank cx-row-upcoming">
      <div className="cx-file-row">
        <div
          className="cx-pair-thumb file undetected"
          style={{
            borderColor: 'rgba(110, 168, 254, 0.35)',
            background: 'rgba(110, 168, 254, 0.08)',
            color: '#9ec5ff',
          }}
        >
          <span className="ep-prefix" style={{ color: '#9ec5ff' }}>EP</span>
          <span className="ep-num" style={{ color: '#9ec5ff' }}>
            {String(episode.episode).padStart(2, '0')}
          </span>
        </div>
        <div className="cx-row-content blank-content">
          <span className="lbl" style={{ color: 'var(--ink-2)' }}>
            <span style={{ color: '#9ec5ff', fontWeight: 600 }}>{airDateLabel}</span>
            <span style={{ color: 'var(--ink-3)', marginLeft: 8 }}>
              · upcoming · no file yet
            </span>
          </span>
        </div>
        <div className="cx-row-aside">
          <span
            className="cx-row-conf"
            style={{
              background: 'rgba(110, 168, 254, 0.14)',
              color: '#9ec5ff',
              border: '1px solid rgba(110, 168, 254, 0.32)',
              fontSize: 11,
              fontWeight: 600,
            }}
          >
            Upcoming
          </span>
        </div>
      </div>
    </div>
  );
}


interface JustImportedRowProps {
  episode: LibEpisode | null;
}

function JustImportedRow({ episode }: JustImportedRowProps) {
  return (
    <div className="cx-row dl dl-completed">
      <div className="cx-row-dl-fill" style={{ width: '100%', opacity: 0.10 }} />
      <div className="cx-file-row" style={{ position: 'relative', zIndex: 1 }}>
        <div className="cx-pair-thumb file undetected dl-thumb dl-thumb-importing">
          {episode ? (
            <>
              <span className="ep-prefix">EP</span>
              <span className="ep-num">{String(episode.episode).padStart(2, '0')}</span>
            </>
          ) : (
            <span className="ep-num">···</span>
          )}
        </div>
        <div className="cx-row-content">
          <div className="cx-row-title">
            <span style={{ color: 'var(--ink)' }}>Imported by Sonarr</span>
            <span style={{ color: 'var(--ink-3)', marginLeft: 8, fontSize: 12 }}>
              · scanning to pick up the file…
            </span>
          </div>
          <div className="cx-row-sub mono">
            <span className="seg" style={{ color: 'var(--ink-3)' }}>
              Kira is rescanning the library — the file should appear here in a few seconds.
            </span>
          </div>
        </div>
        <div className="cx-row-aside">
          <span className="cx-row-conf dl-pill dl-pill-completed">
            <IcCheck /> Imported
          </span>
        </div>
      </div>
    </div>
  );
}

interface DownloadProgressRowProps {
  queueEntry: SonarrQueueEntry;
  episode: LibEpisode | null;
  pushToast?: (toast: { title: string; sub?: string; kind?: 'success' | 'error' }) => void;
}

function DownloadProgressRow({ queueEntry, episode, pushToast }: DownloadProgressRowProps) {
  const pct = Math.max(0, Math.min(100, queueEntry.progress_pct));
  const status = queueEntry.status;
  // Whole-row classes drive the progress-fill colour + pulse animation.
  // `cx-row.dl` is the base; `dl-<status>` modifies per-state colouring.
  const rowClass = `cx-row dl dl-${status}`;
  const sizeText = formatBytes(queueEntry.size_bytes);
  const isLive = status === 'downloading' && pct > 0;
  const showShimmer = status === 'queued' || status === 'searching' || status === 'importing';

  // ── Smooth-fill via requestAnimationFrame ───────────────────────
  // Without extrapolation the bar only moves on poll ticks (every
  // 1.5s) — even a fast download "looks stuck" because the bar might
  // shift 1-2% then sit still for a second. Worse for slow downloads
  // where one poll tick reveals 0.1% movement.
  //
  // Fix: every animation frame, compute where the bar WOULD be based
  // on the last known baseline (pct + ETA at poll time) extrapolated
  // forward by elapsed time. Refs + direct DOM writes — no React
  // re-render storm at 60fps. When a new poll arrives, the baseline
  // resets and any drift between prediction and reality manifests as
  // at most a single small snap (usually invisible).
  //
  // ETA-driven rate: at baseline (pct=B, eta=E), the bar should reach
  // 100% in E seconds. So per-second rate = (100 - B) / E. After
  // elapsed seconds since baseline, extrapolated pct = B + rate * elapsed.
  const fillRef = useRef<HTMLDivElement>(null);
  const pctTextRef = useRef<HTMLSpanElement>(null);
  const etaTextRef = useRef<HTMLSpanElement>(null);
  const baselineRef = useRef({
    pct,
    eta: queueEntry.eta_seconds,
    timestamp: Date.now(),
  });
  // Re-anchor baseline whenever new data arrives. Both pct and ETA
  // matter — Sonarr might revise downward (release switch) or upward
  // (throttle change) at any poll. timestamp captures "when this
  // baseline was true" for the rAF math.
  useEffect(() => {
    baselineRef.current = {
      pct: Math.max(0, Math.min(100, queueEntry.progress_pct)),
      eta: queueEntry.eta_seconds,
      timestamp: Date.now(),
    };
    // Snap the DOM to the freshly-baselined value immediately so the
    // next rAF tick extrapolates from accurate ground truth.
    if (fillRef.current) fillRef.current.style.width = `${baselineRef.current.pct}%`;
    if (pctTextRef.current) pctTextRef.current.textContent = `${baselineRef.current.pct.toFixed(0)}%`;
    if (etaTextRef.current) {
      const e = formatEta(queueEntry.eta_seconds);
      etaTextRef.current.textContent = e ? `· ${e}` : '';
      etaTextRef.current.style.display = e ? '' : 'none';
    }
  }, [queueEntry.progress_pct, queueEntry.eta_seconds]);

  // rAF extrapolation loop — only runs while status === 'downloading'
  // and we have a usable ETA. For other statuses (queued / importing /
  // completed / failed) the bar is static and the CSS handles it.
  useEffect(() => {
    if (status !== 'downloading') return;
    if (queueEntry.eta_seconds == null || queueEntry.eta_seconds <= 0) return;

    let raf = 0;
    let lastWrittenPct = -1;
    let lastWrittenEta = -1;
    const loop = () => {
      const baseline = baselineRef.current;
      const baseEta = baseline.eta ?? 0;
      let extrapolated = baseline.pct;
      let etaNow = baseEta;
      if (baseEta > 0) {
        const elapsedSec = (Date.now() - baseline.timestamp) / 1000;
        const remainingPct = 100 - baseline.pct;
        extrapolated = Math.min(100, baseline.pct + (remainingPct * elapsedSec / baseEta));
        etaNow = Math.max(0, baseEta - elapsedSec);
      }
      // Only write to the DOM when the rendered value actually changes
      // by a perceptible amount. Saves the browser from re-layouting a
      // hundred times per second on a slow download where extrapolation
      // moves 0.001% per frame.
      if (Math.abs(extrapolated - lastWrittenPct) >= 0.1) {
        if (fillRef.current) fillRef.current.style.width = `${extrapolated}%`;
        if (pctTextRef.current) pctTextRef.current.textContent = `${extrapolated.toFixed(0)}%`;
        lastWrittenPct = extrapolated;
      }
      // ETA text updates once per second visually — only re-write when
      // the rounded value changes. Otherwise we'd re-render "12 min left"
      // every frame, which is wasted work and a visual flicker risk.
      const roundedEta = Math.round(etaNow);
      if (roundedEta !== lastWrittenEta) {
        if (etaTextRef.current) {
          const txt = formatEta(roundedEta);
          etaTextRef.current.textContent = txt ? `· ${txt}` : '';
          etaTextRef.current.style.display = txt ? '' : 'none';
        }
        lastWrittenEta = roundedEta;
      }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [status, queueEntry.eta_seconds]);

  // Compose the visible "subtitle" text. Priority order:
  //   1. error_message (failed/warning states) — that's the most
  //      important info.
  //   2. release_title (downloading/queued) — the concrete release
  //      Sonarr is grabbing.
  //   3. fallback "Waiting for Sonarr…" — generic placeholder.
  let subText: string | null = null;
  if (queueEntry.error_message) {
    subText = queueEntry.error_message;
  } else if (queueEntry.release_title) {
    subText = queueEntry.release_title;
  } else {
    subText = 'Waiting for Sonarr…';
  }

  // Initial-render ETA text — the rAF loop will overwrite this once
  // it starts ticking, but for the first paint we need something.
  const initialEtaText = formatEta(queueEntry.eta_seconds);

  // Stuck-import retry — two-step flow as of the AoT S01E05/E06
  // incident:
  //   1. User clicks "Force import" → preview modal opens showing
  //      source path, destination path, episode mapping, import mode
  //   2. User confirms → actual import command fires
  // This prevents data-loss surprises: the user knows exactly what
  // Sonarr is about to do BEFORE Sonarr does it. Default mode is
  // "Copy" not "Move" — keeps source intact so a failed import never
  // takes the user's file with it. Cost: disk space until the user
  // (or their download client retention rule) cleans the source.
  const [retrying, setRetrying] = useState(false);
  const [previewState, setPreviewState] = useState<
    | { kind: 'idle' }
    | { kind: 'loading' }
    | { kind: 'shown'; candidates: Array<{
        source_path: string;
        destination_root: string;
        series_title: string;
        series_id: number;
        episode_labels: string[];
        episode_ids: number[];
        quality_name: string | null;
        release_group: string | null;
        rejection_reasons: string[];
      }> }
  >({ kind: 'idle' });
  const [importMode, setImportMode] = useState<'Copy' | 'Move'>('Copy');

  const handleRetryImport = useCallback(async () => {
    if (retrying || previewState.kind === 'loading') return;
    if (!queueEntry.download_id) {
      pushToast?.({
        title: 'Cannot retry import',
        sub: "Sonarr didn't expose a download id for this entry.",
        kind: 'error',
      });
      return;
    }
    setPreviewState({ kind: 'loading' });
    try {
      const r = await api.sonarrPreviewImport(queueEntry.download_id);
      if (r.ok && r.candidates.length > 0) {
        setPreviewState({ kind: 'shown', candidates: r.candidates });
      } else {
        setPreviewState({ kind: 'idle' });
        pushToast?.({
          title: "Sonarr has nothing to import",
          sub: r.detail ?? 'The queue entry is stale or files were moved.',
          kind: 'error',
        });
        // Rescan in case the file IS already in the library.
        window.dispatchEvent(new CustomEvent('kira:request-rescan'));
      }
    } catch (e) {
      setPreviewState({ kind: 'idle' });
      pushToast?.({ title: 'Preview failed', sub: (e as Error).message, kind: 'error' });
    }
  }, [retrying, previewState.kind, queueEntry.download_id, pushToast]);

  const handleConfirmImport = useCallback(async () => {
    if (retrying || !queueEntry.download_id) return;
    setRetrying(true);
    try {
      const r = await api.sonarrRetryImport({
        download_id: queueEntry.download_id,
        import_mode: importMode,
      });
      if (r.ok) {
        // Toast shows ACTUAL destination paths from Sonarr's history
        // check (run server-side after the import command processes).
        // If Sonarr ran the command but didn't write a history row,
        // surface the warning so the user knows to verify in Sonarr.
        const destLine = r.destinations && r.destinations.length > 0
          ? `Landed at: ${r.destinations.join(' · ')}`
          : (r.history_warning
              ? `Sonarr accepted but history is silent — verify in Sonarr UI. (${r.history_warning})`
              : 'Sonarr accepted — verify location in Sonarr UI.');
        pushToast?.({
          title: `Sonarr imported ${r.imported_count} file${r.imported_count === 1 ? '' : 's'}`,
          sub: destLine,
          kind: r.history_warning ? 'error' : 'success',
        });
        window.dispatchEvent(new CustomEvent('kira:request-rescan'));
      } else {
        const isStaleQueue = (r.detail ?? '').toLowerCase().includes("couldn't find");
        pushToast?.({
          title: "Sonarr couldn't import",
          sub: r.detail ?? 'Check Sonarr UI for the rejection reason.',
          kind: 'error',
        });
        if (isStaleQueue) {
          window.dispatchEvent(new CustomEvent('kira:request-rescan'));
        }
      }
    } catch (e) {
      pushToast?.({ title: 'Import failed', sub: (e as Error).message, kind: 'error' });
    } finally {
      setRetrying(false);
      setPreviewState({ kind: 'idle' });
    }
  }, [retrying, queueEntry.download_id, importMode, pushToast]);

  return (
    <div className={rowClass}>
      {/* Confirmation modal — shown when the user clicks Force Import.
          Two-step interaction: preview-then-commit. Reduces data-loss
          surprises by showing source + destination paths BEFORE
          Sonarr touches anything. */}
      {previewState.kind === 'shown' ? (
        <ForceImportConfirmModal
          candidates={previewState.candidates}
          importMode={importMode}
          onChangeMode={setImportMode}
          onCancel={() => setPreviewState({ kind: 'idle' })}
          onConfirm={handleConfirmImport}
          confirming={retrying}
        />
      ) : null}

      {/* Progress-fill bar — width controlled inline + via rAF ref.
          When status === 'downloading' the rAF loop writes width
          directly to the DOM at 60fps. For other statuses the inline
          width snaps via the useEffect baseline reset. */}
      <div
        ref={fillRef}
        className={`cx-row-dl-fill ${status === 'downloading' ? 'live' : ''}`}
        style={{
          width: `${pct}%`,
          opacity: isLive ? 0.18 : showShimmer ? 0.12 : 0.10,
        }}
      />
      <div className="cx-file-row" style={{ position: 'relative', zIndex: 1 }}>
        <div className={`cx-pair-thumb file undetected dl-thumb dl-thumb-${status}`}>
          {episode ? (
            <>
              <span className="ep-prefix">EP</span>
              <span className="ep-num">{String(episode.episode).padStart(2, '0')}</span>
            </>
          ) : (
            <span className="ep-num">···</span>
          )}
        </div>
        <div className="cx-row-content">
          <div className="cx-row-title">
            <span style={{ color: 'var(--ink)' }}>
              {queueEntry.needs_manual_import ? 'Stuck — manual import needed' : statusLabel(status)}
            </span>
            {isLive ? (
              <span style={{ color: 'var(--ink-2)', fontWeight: 500, marginLeft: 8 }}>
                · <span ref={pctTextRef}>{pct.toFixed(0)}%</span>
              </span>
            ) : null}
            <span
              ref={etaTextRef}
              style={{
                color: 'var(--ink-3)', fontWeight: 500, marginLeft: 8, fontSize: 12,
                display: initialEtaText ? '' : 'none',
              }}
            >
              {initialEtaText ? `· ${initialEtaText}` : ''}
            </span>
          </div>
          <div className="cx-row-sub mono" title={subText ?? undefined}>
            <span className="seg" style={{
              display: 'inline-block',
              maxWidth: '100%',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}>{subText}</span>
          </div>
          <div className="cx-row-tags">
            {sizeText ? <span className="cx-row-tag">{sizeText}</span> : null}
            {queueEntry.protocol ? <span className="cx-row-tag">{queueEntry.protocol}</span> : null}
            {queueEntry.download_client ? <span className="cx-row-tag">{queueEntry.download_client}</span> : null}
            {/* Stuck-import action button. Renders alongside the
                regular tags so it sits inline with the row's existing
                metadata pills. Sonarr already knows the (series,
                episode) mapping; this just forces the import to
                proceed via the manual-import API. */}
            {queueEntry.needs_manual_import && queueEntry.download_id ? (
              <button
                onClick={handleRetryImport}
                disabled={retrying}
                className="cx-blank-btn"
                style={{
                  padding: '3px 10px',
                  fontSize: 11,
                  fontWeight: 600,
                  background: 'rgba(40, 217, 160, 0.16)',
                  color: 'var(--conf-high)',
                  border: '1px solid rgba(40, 217, 160, 0.36)',
                  borderRadius: 999,
                  cursor: retrying ? 'wait' : 'pointer',
                }}
                title="Force Sonarr to import using the (series, episode) mapping it already computed during grab. This is the same action as clicking the file in Sonarr's queue → Import → confirm."
              >
                <IcDownload /> {retrying ? 'Importing…' : 'Force import'}
              </button>
            ) : null}
          </div>
        </div>
        <div className="cx-row-aside">
          <span className={`cx-row-conf dl-pill dl-pill-${status}`}>
            {(status === 'failed' || status === 'warning' || queueEntry.needs_manual_import)
              ? <IcAlertTri /> : null}
            {queueEntry.needs_manual_import ? 'Stuck' : statusLabel(status)}
          </span>
        </div>
      </div>
    </div>
  );
}

function EpisodeRowCellImpl({ row, item, updateFile, onManualSearch, onOpenDupeModal, queueEntry, justImported }: RowCellProps) {
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

  // When no file is matched AND Sonarr is downloading this episode,
  // surface a small status pill in the aside instead of the bare "—"
  // confidence placeholder. Keeps the right column visually aligned
  // with the left column's DownloadProgressRow — both halves of the
  // row now indicate "Sonarr is working on this" instead of half the
  // row going dark/silent. justImported gets the same treatment so
  // both columns stay synchronised during the post-download window.
  // For unaired episodes the right aside mirrors the left's
  // "Upcoming" placeholder.
  const showQueueAside = !file && queueEntry != null;
  const showImportedAside = !file && queueEntry == null && justImported;
  const upcomingAsideText = !file && queueEntry == null && !justImported && episode.airDate
    ? formatUpcomingAirDate(episode.airDate)
    : null;
  const showUpcomingAside = upcomingAsideText != null;

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
          {/* Long episode titles like "All My Life, My Heart Has
              Yearned for a Thing I Cannot Name" overflow the same
              way filenames do. Same marquee treatment. */}
          <MarqueeText className="cx-row-title">
            <span title={episode.title || undefined}>
              {episode.title || (isAlbum ? `Track ${episode.track}` : `Episode ${episode.episode}`)}
            </span>
            {isAlbum && episode.duration ? (
              <span style={{ color: 'var(--ink-3)', fontWeight: 500, marginLeft: 8, fontSize: 12 }}>
                · {episode.duration}
              </span>
            ) : null}
          </MarqueeText>
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
          {showQueueAside ? (
            <span
              className={`cx-row-conf dl-pill dl-pill-${queueEntry.status}`}
              title={
                queueEntry.error_message
                  ? queueEntry.error_message
                  : queueEntry.release_title
                    ? `Sonarr: ${queueEntry.release_title}`
                    : `Sonarr status: ${queueEntry.status}`
              }
            >
              {queueEntry.status === 'downloading'
                ? `${Math.round(queueEntry.progress_pct)}%`
                : statusLabel(queueEntry.status)}
            </span>
          ) : showImportedAside ? (
            <span
              className="cx-row-conf dl-pill dl-pill-completed"
              title="Sonarr finished downloading. Kira is scanning to pick up the file."
            >
              Imported
            </span>
          ) : showUpcomingAside ? (
            <span
              className="cx-row-conf"
              style={{
                background: 'rgba(110, 168, 254, 0.14)',
                color: '#9ec5ff',
                border: '1px solid rgba(110, 168, 254, 0.32)',
                fontSize: 11,
                fontWeight: 600,
              }}
              title={`This episode hasn't aired yet — ${upcomingAsideText?.toLowerCase()}.`}
            >
              {upcomingAsideText}
            </span>
          ) : (
            <span className={`cx-row-conf ${confT}`}>{file ? `${conf}%` : '—'}</span>
          )}
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
