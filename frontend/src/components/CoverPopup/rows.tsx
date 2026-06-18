import { memo, useState, useEffect, useRef, useCallback, type CSSProperties } from 'react';
import type { LibraryItem, LibEpisode, LibFile } from '../../lib/types';
import { api } from '../../lib/api';
import { IcCheck, IcX, IcSearch, IcAlertTri, IcDownload } from '../../lib/icons';
import { confTier } from '../LibraryGrid';
import { audioLangChip, subLangChip, missingSubChip, inferQuality, inferSource } from './quality';
import { detectFromFilename, formatEta, formatBytes, statusLabel, formatUpcomingAirDate } from './format';
import { MarqueeText } from './MarqueeText';
import { ForceImportConfirmModal } from './ForceImportModal';
import type { SonarrQueueEntry, PairedRowShape } from './types';

// ─────────────────────────────────────────────────────────────────────
// Unified pairing rows for the CoverPopup. ONE row == ONE pairing:
// episode number + episode title + the file that fills it (name, size,
// tech chips) + a single confidence indicator + actions, in one
// connected row. Orphan files (no episode) and missing episodes (no
// file) are distinct row STATES of the same list — not separate columns.
//
//   PairRowCell  → the single memoized row (was FileRowCell + EpisodeRowCell).
//   DownloadProgressRow / JustImportedRow / UpcomingEpisodeRow
//                → blank-episode variants (Sonarr live / post-import / unaired).
//
// All approve/reject/search/dupe handlers and the memo comparator are
// preserved from the old two-cell design — this is a VIEW recomposition,
// not a logic change.
// ─────────────────────────────────────────────────────────────────────

// Skeleton placeholder row. Renders during the ~150ms-3s window between
// popup open and episode-list arrival. Shimmer animation is driven by
// CSS; respects prefers-reduced-motion via @media query in index.css.
export function SkeletonRow() {
  return (
    <div className="cx-pair cx-pair-skeleton" aria-hidden="true">
      <div className="cx-skel cx-skel-thumb" />
      <div className="cx-skel-stack">
        <div className="cx-skel cx-skel-title" />
        <div className="cx-skel cx-skel-meta" />
      </div>
      <div className="cx-skel cx-skel-pill" />
    </div>
  );
}

/** "No EN" — the missing-preferred-subtitle chip. Clicking opens the browse
 *  modal (scored candidates across all providers, pick a specific one) rather
 *  than a blind auto-fetch — the per-cluster "Get subtitles" button keeps the
 *  one-click auto path. The chip clears when the files list refreshes after a
 *  pick. */
function MissingSubAction({ file }: { file: LibFile }) {
  const label = missingSubChip(file);
  if (!label) return null;
  const browse = (ev: React.MouseEvent) => {
    ev.stopPropagation();
    const id = Number(file.id);
    if (!Number.isFinite(id)) return;
    window.dispatchEvent(new CustomEvent('kira:browse-subtitles', {
      detail: { fileId: id, filename: file.filename, language: (file.missingSubs ?? [])[0] },
    }));
  };
  return (
    <button
      className="cx-row-tag missing-sub press inline-flex items-center gap-1"
      onClick={browse}
      title={`Missing preferred subtitles (${(file.missingSubs ?? []).map(l => l.toUpperCase()).join(', ')}) — click to browse & pick`}
    >
      {label}
      <IcDownload style={{ width: 10, height: 10 }} />
    </button>
  );
}

interface RowCellProps {
  row: PairedRowShape;
  item: LibraryItem;
  updateFile: (idx: number, patch: Partial<LibFile>) => void;
  onManualSearch: (item: LibraryItem, episodeIdx?: number | null, fileIdx?: number | null) => void;
  onOpenDupeModal: (episode: LibEpisode, files: LibFile[]) => void;
  /** Sonarr's in-flight progress for this row's episode, when known.
   *  Drives the blank-state download-progress row in place of the static
   *  "No file for this episode" placeholder. */
  queueEntry?: SonarrQueueEntry | null;
  /** Set by the parent when this episode JUST finished a Sonarr download
   *  but Kira hasn't yet scanned the new file from disk. Renders a
   *  "Just imported, scanning…" transitional row. */
  justImported?: boolean;
  /** Toast surface for the stuck-import "Force import" retry button. */
  pushToast?: (toast: { title: string; sub?: string; kind?: 'success' | 'error' }) => void;
  /** First-paint stagger index — drives the CSS entrance delay. Only the
   *  first ~24 rows stagger; the rest mount flat (cheap). */
  staggerIndex?: number;
}

// Row-level memoization. Equality checks the row-identifying + row-mutable
// fields so only the changed row re-renders on a per-row approve, queue
// poll, or async episode-title merge. (Same semantics as the old
// rowsEqualFile — see history for the LibEpisode-has-no-id bug-fix that
// made us compare title/airDate/etc. by value.)
const pairRowsEqual = (a: RowCellProps, b: RowCellProps): boolean => {
  if (a.row.key !== b.row.key) return false;
  if (a.row.kind !== b.row.kind) return false;
  if (a.row.file?.id !== b.row.file?.id) return false;
  if (a.row.file?.status !== b.row.file?.status) return false;
  if (a.row.file?.matchedWrong !== b.row.file?.matchedWrong) return false;
  if (a.row.file?.confidence !== b.row.file?.confidence) return false;
  // Coverage gap can change after a subtitle fetch — re-render the chip away.
  if ((a.row.file?.missingSubs?.join(',') ?? '') !== (b.row.file?.missingSubs?.join(',') ?? '')) return false;
  // Dupe group can shrink to 1 after the user resolves duplicates — the
  // primary file's id/status/key all stay the same, so compare length to
  // re-render the "+N" badge away.
  if ((a.row.dupeAll?.length ?? 0) !== (b.row.dupeAll?.length ?? 0)) return false;
  // Episode content that can change after initial render (provider fetch
  // arrives, user edits). Compared by value — there's no stable id.
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
  // Sonarr queue progress changes every poll. Compare the visible fields
  // (equal-by-identity short-circuits when both are null).
  const qa = a.queueEntry, qb = b.queueEntry;
  if ((qa == null) !== (qb == null)) return false;
  if (qa && qb) {
    if (qa.status !== qb.status) return false;
    if (qa.progress_pct !== qb.progress_pct) return false;
    if (qa.eta_seconds !== qb.eta_seconds) return false;
    if (qa.error_message !== qb.error_message) return false;
    if (qa.release_title !== qb.release_title) return false;
  }
  if (a.justImported !== b.justImported) return false;
  // staggerIndex only affects first-paint CSS delay; it's stable per key
  // so no need to bust the memo on it. Callbacks are recreated each render
  // by the parent but the row short-circuits via the content fields above.
  return true;
};

// ── Leading episode badge content. Mirrors the per-media-type numbering
// rule the old EpisodeRowCell + FileRowCell both used (canonical,
// provider-sourced when an episode is paired; filename-derived fallback
// only for orphan / pre-match files so the badge isn't blank).
function badgeContent(
  item: LibraryItem,
  ep: LibEpisode | null,
  file: LibFile | undefined,
): { prefix: string | null; num: string; detected: boolean } {
  const isAlbum = item.kind === 'album';
  if (ep) {
    if (isAlbum) {
      return { prefix: 'TRACK', num: String(ep.track ?? ep.episode).padStart(2, '0'), detected: true };
    }
    if (item.mediaType === 'anime' && ep.absolute) {
      return { prefix: null, num: String(ep.absolute).padStart(2, '0'), detected: true };
    }
    if (ep.season != null && ep.episode != null) {
      return {
        prefix: 'S' + String(ep.season).padStart(2, '0'),
        num: 'E' + String(ep.episode).padStart(2, '0'),
        detected: true,
      };
    }
    if (ep.episode != null) {
      return { prefix: null, num: String(ep.episode).padStart(2, '0'), detected: true };
    }
  }
  // No paired episode (orphan / pre-match): pull from the filename so the
  // badge isn't blank.
  if (file) {
    const detected = detectFromFilename(file.filename, item);
    if (detected) {
      const m = detected.match(/^S(\d+)E(\d+)$/);
      if (m) return { prefix: 'S' + m[1], num: 'E' + m[2], detected: false };
      return { prefix: null, num: detected, detected: false };
    }
  }
  return { prefix: null, num: '—', detected: false };
}

function PairRowCellImpl({
  row, item, updateFile, onManualSearch, onOpenDupeModal,
  queueEntry, justImported, pushToast, staggerIndex,
}: RowCellProps) {
  const { episode: ep, file } = row;
  const fileIdx = file ? item.files.indexOf(file) : -1;
  const isAlbum = item.kind === 'album';
  const epColor = item.poster.tint;

  // ── Blank — an episode with no file. Fans out to the contextual states:
  //   (1) Sonarr actively downloading  → DownloadProgressRow (live bar)
  //   (2) Sonarr just finished, file not yet scanned → JustImportedRow
  //   (3) episode hasn't aired yet → UpcomingEpisodeRow (air-date pill)
  //   (4) otherwise → static "No file for this episode" placeholder.
  //       (Get-missing → Sonarr lives in the footer, not on the row.)
  if (!file && ep) {
    if (queueEntry) {
      return <DownloadProgressRow queueEntry={queueEntry} episode={ep} pushToast={pushToast} staggerIndex={staggerIndex} />;
    }
    if (justImported) {
      return <JustImportedRow episode={ep} staggerIndex={staggerIndex} />;
    }
    const upcomingText = ep.airDate ? formatUpcomingAirDate(ep.airDate) : null;
    if (upcomingText) {
      return <UpcomingEpisodeRow episode={ep} airDateLabel={upcomingText} staggerIndex={staggerIndex} />;
    }
    const { prefix, num } = badgeContent(item, ep, undefined);
    return (
      <div className="cx-pair blank anim-pair" style={staggerVar(staggerIndex)}>
        <div className="cx-pair-thumb ep blank-thumb" style={{ ['--ep-a' as never]: epColor[0], ['--ep-b' as never]: epColor[1] } as CSSProperties}>
          {prefix ? <span className="ep-prefix">{prefix}</span> : null}
          <span className="ep-num">{num}</span>
        </div>
        <div className="cx-pair-body">
          <MarqueeText className="cx-pair-eptitle">
            <span title={ep.title || undefined}>
              {ep.title || (isAlbum ? `Track ${ep.track}` : `Episode ${ep.episode}`)}
            </span>
          </MarqueeText>
          <div className="cx-pair-empty">No file for this episode</div>
        </div>
        <div className="cx-pair-aside">
          <span className="cx-row-conf muted">—</span>
        </div>
      </div>
    );
  }

  // ── Orphan — a file with no matching episode. The badge is dashed, the
  //    body shows the filename + a prominent "Search this file" CTA, and
  //    the confidence reads "No episode" (the % we have describes the
  //    SERIES match, not an episode the file can be renamed into).
  if (!ep && file) {
    return (
      <div className={`cx-pair orphan anim-pair ${statusClass(file)}`} style={staggerVar(staggerIndex)}>
        <div className="cx-pair-thumb file undetected orphan-thumb">
          <span className="ep-num">—</span>
        </div>
        <div className="cx-pair-body">
          <MarqueeText className="cx-pair-filename mono">
            <span title={file.filename}>{file.filename}</span>
          </MarqueeText>
          <MarqueeText className="cx-pair-folder mono">
            <span className="seg" title={file.folder}>{file.folder}</span>
          </MarqueeText>
          <div className="cx-pair-orphan-row">
            <span className="cx-pair-empty">Orphaned · no matching {isAlbum ? 'track' : 'episode'}</span>
            <button className="cx-blank-btn" onClick={() => onManualSearch(item, null, fileIdx)}>
              <IcSearch /> Search this file
            </button>
          </div>
        </div>
        <div className="cx-pair-aside" onClick={(e) => e.stopPropagation()}>
          <span
            className="cx-row-conf low"
            title={
              file.matchId != null
                ? `Series matched but no episode in it matches this file's number. Use "Search this file" to fix.`
                : 'No match at all — use "Search this file" to find one.'
            }
          >
            No episode
          </span>
          {/* No aside search icon — the body's "Search this file" CTA is the
              one and only search affordance on an orphan row. */}
        </div>
      </div>
    );
  }

  // ── Paired — the common case: episode + the file filling it. ──
  // (file is defined and ep is defined here.)
  const f = file!;
  const e = ep!;
  const wrong = f.matchedWrong;
  const conf = f.confidence ?? 0;
  const confT = confTier(conf);
  const { prefix, num } = badgeContent(item, e, f);

  const fullTag = isAlbum
    ? `Track ${String(e.track ?? e.episode).padStart(2, '0')}`
    : item.mediaType === 'anime' && e.absolute
      ? `Episode ${String(e.absolute).padStart(2, '0')}`
      : `S${String(e.season).padStart(2, '0')}E${String(e.episode).padStart(2, '0')}`;

  return (
    <div className={`cx-pair anim-pair ${statusClass(f)} ${wrong ? 'wrong' : ''}`} style={staggerVar(staggerIndex)}>
      {/* Leading episode badge — the strong "this is episode N" anchor. */}
      <div
        className="cx-pair-thumb ep"
        style={{ ['--ep-a' as never]: epColor[0], ['--ep-b' as never]: epColor[1] } as CSSProperties}
      >
        {prefix ? <span className="ep-prefix">{prefix}</span> : null}
        <span className="ep-num">{num}</span>
      </div>

      {/* Body — episode identity on top, the file that fills it beneath. */}
      <div className="cx-pair-body">
        <div className="cx-pair-head">
          <MarqueeText className="cx-pair-eptitle">
            <span title={e.title || undefined}>
              {e.title || (isAlbum ? `Track ${e.track}` : `Episode ${e.episode}`)}
            </span>
            {isAlbum && e.duration ? <span className="cx-pair-dur">· {e.duration}</span> : null}
          </MarqueeText>
          <div className="cx-pair-epmeta">
            <span>{fullTag}</span>
            {e.airDate && !isAlbum ? <><span className="dot-sep" /><span>{e.airDate}</span></> : null}
            {e.runtime && !isAlbum ? <><span className="dot-sep" /><span>{e.runtime} min</span></> : null}
          </div>
        </div>

        {/* The file row — name, folder, tech chips. The visual "this file
            fills the slot" connection. */}
        <div className="cx-pair-file">
          <MarqueeText className="cx-pair-filename mono">
            <span title={f.filename}>{f.filename}</span>
          </MarqueeText>
          <div className="cx-pair-tags">
            {f.size ? <span className="cx-row-tag">{f.size}</span> : null}
            {(() => { const q = inferQuality(f); return q ? <span className="cx-row-tag">{q}</span> : null; })()}
            {(() => { const s = inferSource(f); return s ? <span className="cx-row-tag">{s}</span> : null; })()}
            {f.codec ? <span className="cx-row-tag">{f.codec}</span> : null}
            {f.hdr ? <span className="cx-row-tag hdr">{f.hdr}</span> : null}
            {f.channels ? <span className="cx-row-tag">{f.channels}</span> : null}
            {f.audio?.[0] ? <span className="cx-row-tag">{f.audio[0]}</span> : null}
            {(() => { const a = audioLangChip(f); return a ? <span className="cx-row-tag lang">{a}</span> : null; })()}
            {(() => { const s = subLangChip(f); return s ? <span className="cx-row-tag">{s}</span> : null; })()}
            <MissingSubAction file={f} />
            {f.releaseGroup ? <span className="cx-row-tag rg" title={f.releaseGroup}>[{f.releaseGroup}]</span> : null}
            {row.kind === 'dupe-primary' && row.dupeAll && row.dupeAll.length > 1 ? (
              <button
                className="cx-row-dupe"
                onClick={(ev) => {
                  ev.stopPropagation();
                  if (row.episode && row.dupeAll) onOpenDupeModal(row.episode, row.dupeAll);
                }}
                title={`${row.dupeAll.length} files claim this episode — click to pick which to keep`}
              >
                <IcAlertTri /> +{row.dupeAll.length - 1}
              </button>
            ) : null}
          </div>
          {wrong ? (
            <div className="cx-pair-wrong">
              <span className="cx-row-warn">
                <IcAlertTri /> Filename suggests a different {isAlbum ? 'track' : 'episode'}
              </span>
              <button
                className="cx-blank-btn"
                onClick={() => onManualSearch(item, row.episodeIdx, fileIdx)}
              >
                <IcSearch /> Find correct
              </button>
            </div>
          ) : null}
        </div>
      </div>

      {/* Aside — ONE confidence pill, the status pill, the per-row actions.
          stopPropagation so clicks here never bubble to a future row click. */}
      <div className="cx-pair-aside" onClick={(ev) => ev.stopPropagation()}>
        {f.status === 'renamed' ? (
          <span className="cx-row-status renamed" title="File has been renamed"><IcCheck /> Renamed</span>
        ) : f.status === 'approved' ? (
          <span className="cx-row-status approved" title="Approved — queued for rename"><IcCheck /> Approved</span>
        ) : f.status === 'rejected' ? (
          <span className="cx-row-status rejected" title="Rejected"><IcX /> Rejected</span>
        ) : null}
        <span className={`cx-row-conf ${confT}`}>{conf}%</span>
        <div className="cx-pair-actions">
          {/* ✓ / ✗ as a joined segmented pair — one connected control so
              the approve/reject decision reads as a single toggle, not two
              loose dots. No per-row search: re-identification lives on the
              orphan/wrong CTAs and the footer's Re-identify (owner call —
              a per-row icon on every cell was noise without a job). */}
          <div className="seg-pair">
            <button
              className="cx-row-act approve press"
              title="Approve this file"
              aria-label="Approve this file"
              onClick={() => fileIdx >= 0 ? updateFile(fileIdx, { status: 'approved' }) : null}
              disabled={fileIdx < 0}
            ><IcCheck /></button>
            <button
              className="cx-row-act reject press"
              title="Reject this file"
              aria-label="Reject this file"
              onClick={() => fileIdx >= 0 ? updateFile(fileIdx, { status: 'rejected' }) : null}
              disabled={fileIdx < 0}
            ><IcX /></button>
          </div>
        </div>
      </div>
    </div>
  );
}
// Memo wrapper — see pairRowsEqual above.
export const PairRowCell = memo(PairRowCellImpl, pairRowsEqual);

// ── Small shared helpers ───────────────────────────────────────────────
function statusClass(file: LibFile | undefined): string {
  return file?.status === 'approved' ? 'approved'
    : file?.status === 'rejected' ? 'rejected'
    : file?.status === 'renamed' ? 'renamed' : '';
}
function staggerVar(i?: number): CSSProperties | undefined {
  // Only the first slice of rows stagger; the rest get index 0 (no delay).
  // The CSS clamps the delay anyway, but capping here keeps the inline
  // var tidy and avoids a 1000-deep cascade on long-runners.
  return i != null && i >= 0 && i < 24 ? ({ ['--pair-i' as never]: i } as CSSProperties) : undefined;
}

interface UpcomingEpisodeRowProps {
  episode: LibEpisode;
  airDateLabel: string;
  staggerIndex?: number;
}

function UpcomingEpisodeRow({ episode, airDateLabel, staggerIndex }: UpcomingEpisodeRowProps) {
  return (
    <div className="cx-pair blank cx-pair-upcoming anim-pair" style={staggerVar(staggerIndex)}>
      <div className="cx-pair-thumb file undetected upcoming-thumb">
        <span className="ep-prefix">EP</span>
        <span className="ep-num">{String(episode.episode).padStart(2, '0')}</span>
      </div>
      <div className="cx-pair-body">
        <MarqueeText className="cx-pair-eptitle">
          <span title={episode.title || undefined}>{episode.title || `Episode ${episode.episode}`}</span>
        </MarqueeText>
        <div className="cx-pair-empty upcoming-label">
          <span className="upcoming-date">{airDateLabel}</span>
          <span className="upcoming-sub">· upcoming · no file yet</span>
        </div>
      </div>
      <div className="cx-pair-aside">
        <span className="cx-row-conf upcoming-pill">Upcoming</span>
      </div>
    </div>
  );
}

interface JustImportedRowProps {
  episode: LibEpisode | null;
  staggerIndex?: number;
}

function JustImportedRow({ episode, staggerIndex }: JustImportedRowProps) {
  return (
    <div className="cx-pair dl dl-completed anim-pair" style={staggerVar(staggerIndex)}>
      <div className="cx-row-dl-fill" style={{ width: '100%', opacity: 0.10 }} />
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
      <div className="cx-pair-body">
        <MarqueeText className="cx-pair-eptitle">
          <span title={episode?.title || undefined}>
            {episode?.title || (episode ? `Episode ${episode.episode}` : 'Imported by Sonarr')}
          </span>
        </MarqueeText>
        <div className="cx-pair-empty">Imported by Sonarr · scanning to pick up the file…</div>
      </div>
      <div className="cx-pair-aside">
        <span className="cx-row-conf dl-pill dl-pill-completed"><IcCheck /> Imported</span>
      </div>
    </div>
  );
}

interface DownloadProgressRowProps {
  queueEntry: SonarrQueueEntry;
  episode: LibEpisode | null;
  pushToast?: (toast: { title: string; sub?: string; kind?: 'success' | 'error' }) => void;
  staggerIndex?: number;
}

function DownloadProgressRow({ queueEntry, episode, pushToast, staggerIndex }: DownloadProgressRowProps) {
  const pct = Math.max(0, Math.min(100, queueEntry.progress_pct));
  const status = queueEntry.status;
  const rowClass = `cx-pair dl dl-${status} anim-pair`;
  const sizeText = formatBytes(queueEntry.size_bytes);
  const isLive = status === 'downloading' && pct > 0;
  const showShimmer = status === 'queued' || status === 'searching' || status === 'importing';

  // Smooth-fill via requestAnimationFrame: extrapolate the bar between
  // poll ticks using Sonarr's ETA so a live download never "looks stuck."
  // Refs + direct DOM writes — no React re-render storm at 60fps.
  const fillRef = useRef<HTMLDivElement>(null);
  const pctTextRef = useRef<HTMLSpanElement>(null);
  const etaTextRef = useRef<HTMLSpanElement>(null);
  const baselineRef = useRef({ pct, eta: queueEntry.eta_seconds, timestamp: Date.now() });
  useEffect(() => {
    baselineRef.current = {
      pct: Math.max(0, Math.min(100, queueEntry.progress_pct)),
      eta: queueEntry.eta_seconds,
      timestamp: Date.now(),
    };
    if (fillRef.current) {
      fillRef.current.style.width = `${baselineRef.current.pct}%`;
      fillRef.current.setAttribute('aria-valuenow', String(Math.round(baselineRef.current.pct)));
    }
    if (pctTextRef.current) pctTextRef.current.textContent = `${baselineRef.current.pct.toFixed(0)}%`;
    if (etaTextRef.current) {
      const e = formatEta(queueEntry.eta_seconds);
      etaTextRef.current.textContent = e ? `· ${e}` : '';
      etaTextRef.current.style.display = e ? '' : 'none';
    }
  }, [queueEntry.progress_pct, queueEntry.eta_seconds]);

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
      if (Math.abs(extrapolated - lastWrittenPct) >= 0.1) {
        if (fillRef.current) {
          fillRef.current.style.width = `${extrapolated}%`;
          fillRef.current.setAttribute('aria-valuenow', String(Math.round(extrapolated)));
        }
        if (pctTextRef.current) pctTextRef.current.textContent = `${extrapolated.toFixed(0)}%`;
        lastWrittenPct = extrapolated;
      }
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

  let subText: string | null = null;
  if (queueEntry.error_message) subText = queueEntry.error_message;
  else if (queueEntry.release_title) subText = queueEntry.release_title;
  else subText = 'Waiting for Sonarr…';

  const initialEtaText = formatEta(queueEntry.eta_seconds);

  // Stuck-import retry — two-step preview-then-commit flow (the AoT
  // S01E05/E06 data-loss incident). Default mode is "Copy" so a failed
  // import never takes the user's source file with it.
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
      pushToast?.({ title: 'Cannot retry import', sub: "Sonarr didn't expose a download id for this entry.", kind: 'error' });
      return;
    }
    setPreviewState({ kind: 'loading' });
    try {
      const r = await api.sonarrPreviewImport(queueEntry.download_id);
      if (r.ok && r.candidates.length > 0) {
        setPreviewState({ kind: 'shown', candidates: r.candidates });
      } else {
        setPreviewState({ kind: 'idle' });
        pushToast?.({ title: "Sonarr has nothing to import", sub: r.detail ?? 'The queue entry is stale or files were moved.', kind: 'error' });
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
      const r = await api.sonarrRetryImport({ download_id: queueEntry.download_id, import_mode: importMode });
      if (r.ok) {
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
        pushToast?.({ title: "Sonarr couldn't import", sub: r.detail ?? 'Check Sonarr UI for the rejection reason.', kind: 'error' });
        if (isStaleQueue) window.dispatchEvent(new CustomEvent('kira:request-rescan'));
      }
    } catch (e) {
      pushToast?.({ title: 'Import failed', sub: (e as Error).message, kind: 'error' });
    } finally {
      setRetrying(false);
      setPreviewState({ kind: 'idle' });
    }
  }, [retrying, queueEntry.download_id, importMode, pushToast]);

  return (
    <div className={rowClass} style={staggerVar(staggerIndex)}>
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

      {/* Progress-fill band — width controlled inline + via rAF ref. Exposed as
          a progressbar so screen readers can read live download progress (the
          rAF loop keeps aria-valuenow in step with the width). */}
      <div
        ref={fillRef}
        className={`cx-row-dl-fill ${status === 'downloading' ? 'live' : ''}`}
        style={{ width: `${pct}%`, opacity: isLive ? 0.18 : showShimmer ? 0.12 : 0.10 }}
        role="progressbar"
        aria-valuenow={Math.round(pct)}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={`Download progress${episode ? ` — episode ${episode.episode}` : ''}`}
      />
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
      <div className="cx-pair-body">
        <div className="cx-pair-eptitle dl-title">
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
            style={{ color: 'var(--ink-3)', fontWeight: 500, marginLeft: 8, fontSize: 12, display: initialEtaText ? '' : 'none' }}
          >
            {initialEtaText ? `· ${initialEtaText}` : ''}
          </span>
        </div>
        <div className="cx-pair-folder mono" title={subText ?? undefined}>
          <span className="seg dl-sub">{subText}</span>
        </div>
        <div className="cx-pair-tags">
          {sizeText ? <span className="cx-row-tag">{sizeText}</span> : null}
          {queueEntry.protocol ? <span className="cx-row-tag">{queueEntry.protocol}</span> : null}
          {queueEntry.download_client ? <span className="cx-row-tag">{queueEntry.download_client}</span> : null}
          {queueEntry.needs_manual_import && queueEntry.download_id ? (
            <button
              onClick={handleRetryImport}
              disabled={retrying}
              className="cx-blank-btn dl-force-btn"
              title="Force Sonarr to import using the (series, episode) mapping it already computed during grab."
            >
              <IcDownload /> {retrying ? 'Importing…' : 'Force import'}
            </button>
          ) : null}
        </div>
      </div>
      <div className="cx-pair-aside">
        <span className={`cx-row-conf dl-pill dl-pill-${status}`}>
          {(status === 'failed' || status === 'warning' || queueEntry.needs_manual_import) ? <IcAlertTri /> : null}
          {queueEntry.needs_manual_import ? 'Stuck' : statusLabel(status)}
        </span>
      </div>
    </div>
  );
}
