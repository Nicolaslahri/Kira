import { memo, useState, useEffect, useRef, useCallback, type CSSProperties } from 'react';
import type { LibraryItem, LibEpisode, LibFile } from '../../lib/types';
import { api } from '../../lib/api';
import { IcCheck, IcX, IcSearch, IcAlertTri, IcChevDown, IcDownload } from '../../lib/icons';
import { confTier } from '../LibraryGrid';
import { audioLangChip, subLangChip, inferQuality, inferSource } from './quality';
import { detectFromFilename, formatEta, formatBytes, statusLabel, formatUpcomingAirDate } from './format';
import { MarqueeText } from './MarqueeText';
import { ForceImportConfirmModal } from './ForceImportModal';
import type { SonarrQueueEntry, PairedRowShape } from './types';

// ─────────────────────────────────────────────────────────────────────
// Row cells for the SeriesBody synced-scroll columns. FileRowCell (left)
// and EpisodeRowCell (right) are memoized; the blank-state of FileRowCell
// fans out into DownloadProgressRow / JustImportedRow / UpcomingEpisodeRow
// depending on the episode's Sonarr / air-date state. Extracted from the
// 4k-line CoverPopup parent.
// ─────────────────────────────────────────────────────────────────────

// PB-2: skeleton placeholder row. Renders during the ~150ms-3s window
// between popup open and episode-list arrival. Shimmer animation is
// driven by CSS; respects prefers-reduced-motion via @media query in
// index.css.
export function SkeletonRow({ side }: { side: 'left' | 'right' }) {
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
            {file.hdr ? <span className="cx-row-tag hdr">{file.hdr}</span> : null}
            {file.channels ? <span className="cx-row-tag">{file.channels}</span> : null}
            {file.audio?.[0] ? <span className="cx-row-tag">{file.audio[0]}</span> : null}
            {(() => { const a = audioLangChip(file); return a ? <span className="cx-row-tag lang">{a}</span> : null; })()}
            {(() => { const s = subLangChip(file); return s ? <span className="cx-row-tag">{s}</span> : null; })()}
            {file.releaseGroup ? <span className="cx-row-tag rg" title={file.releaseGroup}>[{file.releaseGroup}]</span> : null}
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
export const FileRowCell = memo(FileRowCellImpl, rowsEqualFile);

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
            } as CSSProperties}
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
          style={{ ['--ep-a' as never]: epColor[0], ['--ep-b' as never]: epColor[1] } as CSSProperties}
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
export const EpisodeRowCell = memo(EpisodeRowCellImpl, rowsEqualFile);
