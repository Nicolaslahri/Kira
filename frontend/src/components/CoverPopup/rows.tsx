import { memo, useState, useEffect, useRef, useCallback, type CSSProperties, type ReactNode } from 'react';
import type { LibraryItem, LibEpisode, LibFile } from '../../lib/types';
import { api } from '../../lib/api';
import { IcCheck, IcX, IcSearch, IcAlertTri, IcDownload, IcCaption } from '../../lib/icons';
import { ButtonGroup, ButtonGroupItem } from '../base/button-group/button-group';
import { cn } from '../../lib/utils';
import { confTier } from '../LibraryGrid';
import { audioLangChip, subLangChip, missingSubChip, inferQuality, inferSource } from './quality';
import { TechBadges } from '../TechBadge';
import { CandidateList } from './CandidateList';
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

// ── UUI tech-spec chip. Neutral glassy pill + inset ring; `className`
//    tints specific specs (HDR amber, audio-language emerald, release
//    group monospace). Shared by every paired/orphan/download row so the
//    tag rail reads consistently.
function RowTag({ children, className, title }: { children: ReactNode; className?: string; title?: string }) {
  return (
    <span
      title={title}
      className={cn(
        'inline-flex items-center gap-1 whitespace-nowrap rounded-md bg-white/[0.05] px-1.5 py-[3px] text-[10.5px] font-medium leading-none text-tertiary ring-1 ring-white/[0.07] ring-inset',
        className,
      )}
    >
      {children}
    </span>
  );
}

// Confidence chip color ramp (paired-row aside). Mirrors the old
// .cx-row-conf tiers — emerald / amber / red glass.
function confChipClass(tier: 'high' | 'mid' | 'low'): string {
  return tier === 'high'
    ? 'bg-[var(--conf-high-bg)] text-[var(--conf-high)] ring-[var(--accent-line)]'
    : tier === 'mid'
      ? 'bg-[var(--conf-mid-bg)] text-[var(--conf-mid)] ring-[var(--conf-mid-32)]'
      : 'bg-[var(--conf-low-bg)] text-[var(--conf-low)] ring-[var(--conf-low-32)]';
}

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
      onClick={browse}
      title={`Missing preferred subtitles (${(file.missingSubs ?? []).map(l => l.toUpperCase()).join(', ')}) — click to browse & pick`}
      className="inline-flex items-center gap-1 whitespace-nowrap rounded-md bg-[var(--warn-bg)] px-1.5 py-[3px] text-[10.5px] font-semibold leading-none text-[var(--warn)] ring-1 ring-[var(--warn-line)] ring-inset transition-colors hover:bg-[var(--warn-line)] hover:ring-[var(--warn)] disabled:cursor-progress disabled:opacity-60 [&_svg]:size-[11px]"
    >
      {label}
      <IcDownload />
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
  /** Request THIS missing episode from Sonarr (background search). Set by the
   *  parent only for Sonarr-eligible series; absent → the row shows the plain
   *  "—" placeholder instead of a per-episode request button. */
  onRequestEpisode?: (episode: number) => void;
  /** First-paint stagger index — drives the CSS entrance delay. Only the
   *  first ~24 rows stagger; the rest mount flat (cheap). */
  staggerIndex?: number;
  /** Switch this row's file to a different match candidate — same handler the
   *  movie body's CandidateList uses (POST /files/{id}/select/{matchId}).
   *  Present ⇒ episodes with 2+ candidates grow an "N matches" expander. */
  onPickCandidate?: (fileId: string, candidate: { matchId?: number; title?: string; year?: number | null }) => void | Promise<void>;
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
  queueEntry, justImported, pushToast, onRequestEpisode, staggerIndex, onPickCandidate,
}: RowCellProps) {
  // Per-episode alternatives expander (audit §10 / M1 follow-through): movies
  // got one-click candidate switching; episodes now get the same, inline.
  const [altsOpen, setAltsOpen] = useState(false);

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
      <div
        className="anim-pair relative my-[7px] grid grid-cols-[56px_minmax(0,1fr)_auto] items-center gap-3.5 rounded-xl border border-dashed border-secondary bg-secondary/40 p-3 transition-colors hover:bg-tertiary"
        style={staggerVar(staggerIndex)}
      >
        <div
          className="relative flex size-14 shrink-0 flex-col items-center justify-center overflow-hidden rounded-xl border border-white/10 text-white opacity-45"
          style={{ backgroundImage: `linear-gradient(135deg, ${epColor[0]}, ${epColor[1]})` }}
        >
          <span className="pointer-events-none absolute inset-0 bg-gradient-to-b from-white/10 via-transparent to-black/35" />
          {prefix ? <span className="relative z-10 font-mono text-[9px] font-bold uppercase leading-none tracking-[0.08em] opacity-90">{prefix}</span> : null}
          <span className="relative z-10 mt-0.5 font-mono text-[17px] font-bold leading-none tabular-nums drop-shadow">{num}</span>
        </div>
        <div className="flex min-w-0 flex-col gap-1">
          <MarqueeText className="text-[13.5px] font-semibold leading-tight text-tertiary">
            <span title={ep.title || undefined}>
              {ep.title || (isAlbum ? `Track ${ep.track}` : `Episode ${ep.episode}`)}
            </span>
          </MarqueeText>
          <div className="text-[11px] text-quaternary">No file for this {isAlbum ? 'track' : 'episode'}</div>
        </div>
        <div className="flex shrink-0 items-center">
          {onRequestEpisode && typeof ep.episode === 'number' ? (
            <button
              type="button"
              onClick={() => onRequestEpisode(ep.episode)}
              title="Search Sonarr for just this episode"
              className="press inline-flex items-center gap-1 rounded-md bg-[var(--accent-8)] px-2 py-1 text-[11px] font-medium text-[var(--accent-bright)] ring-1 ring-inset ring-[var(--accent-line)] transition-colors hover:bg-[var(--accent-12)] [&_svg]:size-3"
            >
              <IcDownload />
              Sonarr
            </button>
          ) : (
            <span className="inline-flex items-center rounded-md bg-white/[0.04] px-2 py-0.5 text-[11px] font-medium text-quaternary ring-1 ring-secondary ring-inset">—</span>
          )}
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
      <div
        className={cn(
          'anim-pair relative my-[7px] grid grid-cols-[56px_minmax(0,1fr)_auto] items-center gap-3.5 rounded-xl border p-3 shadow-xs transition-colors',
          'border-secondary bg-secondary hover:border-primary hover:bg-tertiary',
          statusClass(file) === 'rejected' && 'opacity-55 hover:opacity-100',
        )}
        style={{ ...staggerVar(staggerIndex), animation: statusClass(file) === 'rejected' ? 'none' : undefined }}
      >
        <div className="flex size-14 shrink-0 items-center justify-center rounded-xl border border-dashed border-[var(--border-2)] bg-white/[0.02] font-mono text-[22px] font-bold leading-none text-quaternary">
          —
        </div>
        <div className="flex min-w-0 flex-col gap-1.5">
          <MarqueeText className="font-mono text-[12px] text-secondary">
            <span title={file.filename}>{file.filename}</span>
          </MarqueeText>
          <MarqueeText className="font-mono text-[10.5px] text-quaternary">
            <span title={file.folder}>{file.folder}</span>
          </MarqueeText>
          <div className="flex flex-wrap items-center gap-2 pt-0.5">
            <span className="text-[11px] font-medium text-[var(--conf-low)]">Orphaned · no matching {isAlbum ? 'track' : 'episode'}</span>
            <button
              onClick={() => onManualSearch(item, null, fileIdx)}
              className="inline-flex items-center gap-1 rounded-md bg-tertiary px-2 py-1 text-[11px] font-semibold text-secondary ring-1 ring-secondary ring-inset transition-colors hover:bg-primary_hover hover:text-primary [&_svg]:size-3"
            >
              <IcSearch /> Search this file
            </button>
          </div>
        </div>
        <div className="flex shrink-0 items-center" onClick={(e) => e.stopPropagation()}>
          <span
            className="inline-flex items-center rounded-md bg-[var(--conf-low-bg)] px-2 py-0.5 text-[11px] font-semibold text-[var(--conf-low)] ring-1 ring-[var(--conf-low-32)] ring-inset"
            title={
              file.matchId != null
                ? `Series matched but no episode in it matches this file's number. Use "Search this file" to fix.`
                : 'No match at all — use "Search this file" to find one.'
            }
          >
            No episode
          </span>
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
    <div
      className={cn(
        'anim-pair group/row relative my-[7px] grid grid-cols-[56px_minmax(0,1fr)_auto] items-center gap-3.5 rounded-xl border p-3 shadow-xs transition-colors',
        'border-secondary bg-secondary hover:border-primary hover:bg-tertiary',
        (f.status === 'approved' || f.status === 'renamed') && '!border-[var(--conf-high-32)] !bg-[var(--conf-high-bg)]',
        f.status === 'rejected' && 'opacity-55 hover:opacity-100',
        wrong && f.status !== 'approved' && f.status !== 'renamed' && '!border-[var(--conf-mid-32)]',
      )}
      // anim-pair's cxPairIn `both` fill pins opacity:1; kill it for rejected
      // rows so the opacity-55 dim actually takes (they don't need entrance).
      style={{ ...staggerVar(staggerIndex), animation: f.status === 'rejected' ? 'none' : undefined }}
    >
      {/* Leading episode badge — poster-tinted square, the "this is episode N" anchor. */}
      <div
        className="relative flex size-14 shrink-0 flex-col items-center justify-center overflow-hidden rounded-xl border border-white/10 text-white shadow-sm"
        style={{ backgroundImage: `linear-gradient(135deg, ${epColor[0]}, ${epColor[1]})` }}
      >
        <span className="pointer-events-none absolute inset-0 bg-gradient-to-b from-white/10 via-transparent to-black/35" />
        {prefix ? <span className="relative z-10 font-mono text-[9px] font-bold uppercase leading-none tracking-[0.08em] opacity-90">{prefix}</span> : null}
        <span className="relative z-10 mt-0.5 font-mono text-[17px] font-bold leading-none tabular-nums drop-shadow">{num}</span>
      </div>

      {/* Body — episode identity on top, the file that fills it beneath. */}
      <div className="flex min-w-0 flex-col gap-2">
        <div className="min-w-0">
          <MarqueeText className="text-[13.5px] font-semibold leading-tight text-primary">
            <span title={e.title || undefined}>
              {e.title || (isAlbum ? `Track ${e.track}` : `Episode ${e.episode}`)}
              {isAlbum && e.duration ? <span className="ml-2 text-xs font-medium text-quaternary">· {e.duration}</span> : null}
            </span>
          </MarqueeText>
          <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-tertiary">
            <span className="font-mono font-medium text-secondary">{fullTag}</span>
            {/* Music: surface the track's OWN artist when it's a collab (differs
                from the album artist) — e.g. "Stuck With U · Ariana Grande & …". */}
            {isAlbum && e.artist && e.artist !== item.artist
              ? <><span className="dot-sep" /><span className="font-medium text-secondary">{e.artist}</span></> : null}
            {e.airDate && !isAlbum ? <><span className="dot-sep" /><span>{e.airDate}</span></> : null}
            {e.runtime && !isAlbum ? <><span className="dot-sep" /><span>{e.runtime} min</span></> : null}
          </div>
        </div>

        {/* The file that fills the slot — name + tech chips, divided from the
            episode identity above so the pairing reads top-down. */}
        <div className="flex min-w-0 flex-col gap-1.5 border-t border-secondary pt-2">
          <MarqueeText className="font-mono text-[11px] text-quaternary">
            <span title={f.filename}>{f.filename}</span>
          </MarqueeText>
          <div className="flex flex-wrap items-center gap-1">
            {f.size ? <RowTag>{f.size}</RowTag> : null}
            {/* Tech specs — one unified Apple-TV-style white badge rail
                (4K · DOLBY VISION · HEVC · DOLBY ATMOS · 7.1). Replaces the
                old mix of gold 4K art + amber HDR chip + grey codec chips. */}
            <TechBadges file={{ ...f, quality: inferQuality(f) ?? f.quality }} />
            {(() => { const s = inferSource(f); return s ? <RowTag>{s}</RowTag> : null; })()}
            {(() => { const a = audioLangChip(f); return a ? <RowTag className="text-[var(--accent)] ring-[var(--accent-line)]">{a}</RowTag> : null; })()}
            {(() => { const s = subLangChip(f); return s ? <RowTag><IcCaption className="mr-1 inline-block size-3 align-[-1px]" />{s.replace(/^SUB /, '')}</RowTag> : null; })()}
            <MissingSubAction file={f} />
            {f.releaseGroup ? <RowTag className="font-mono text-quaternary" title={f.releaseGroup}>[{f.releaseGroup}]</RowTag> : null}
            {onPickCandidate && f.candidates && f.candidates.length > 1 ? (
              <button
                type="button"
                onClick={() => setAltsOpen(v => !v)}
                aria-expanded={altsOpen}
                title="This episode matched more than one candidate — click to compare and switch"
                className="inline-flex items-center gap-1 whitespace-nowrap rounded-md bg-[var(--accent-8)] px-1.5 py-[3px] text-[10.5px] font-medium leading-none text-[var(--accent-bright)] ring-1 ring-inset ring-[var(--accent-line)] transition-colors hover:bg-[var(--accent-12)]"
              >
                {f.candidates.length} matches {altsOpen ? '▴' : '▾'}
              </button>
            ) : null}
            {altsOpen && onPickCandidate ? (
              <div className="w-full pt-1">
                <CandidateList file={f} onPick={onPickCandidate} />
              </div>
            ) : null}
            {row.kind === 'dupe-primary' && row.dupeAll && row.dupeAll.length > 1 ? (
              <button
                onClick={(ev) => {
                  ev.stopPropagation();
                  if (row.episode && row.dupeAll) onOpenDupeModal(row.episode, row.dupeAll);
                }}
                title={`${row.dupeAll.length} files claim this episode — click to pick which to keep`}
                className="inline-flex items-center gap-1 whitespace-nowrap rounded-md bg-[var(--conf-mid-bg)] px-1.5 py-[3px] text-[10.5px] font-semibold leading-none text-[var(--conf-mid)] ring-1 ring-[var(--conf-mid-32)] ring-inset transition-colors hover:bg-[var(--conf-mid-24)] [&_svg]:size-3"
              >
                <IcAlertTri /> +{row.dupeAll.length - 1}
              </button>
            ) : null}
          </div>
          {wrong ? (
            <div className="flex flex-wrap items-center gap-2 rounded-lg bg-[var(--conf-mid-8)] px-2 py-1.5 ring-1 ring-[var(--conf-mid-24)] ring-inset">
              <span className="inline-flex items-center gap-1.5 text-[11px] font-medium text-[var(--conf-mid)] [&_svg]:size-3.5">
                <IcAlertTri /> Filename suggests a different {isAlbum ? 'track' : 'episode'}
              </span>
              <button
                onClick={() => onManualSearch(item, row.episodeIdx, fileIdx)}
                className="inline-flex items-center gap-1 rounded-md bg-tertiary px-2 py-1 text-[11px] font-semibold text-secondary ring-1 ring-secondary ring-inset transition-colors hover:bg-primary_hover hover:text-primary [&_svg]:size-3"
              >
                <IcSearch /> Find correct
              </button>
            </div>
          ) : null}
        </div>
      </div>

      {/* Aside — status badge (when set) + confidence chip + the joined
          approve/reject ButtonGroup. stopPropagation so clicks here never
          bubble to a future row click. */}
      <div className="flex shrink-0 flex-col items-end gap-2" onClick={(ev) => ev.stopPropagation()}>
        {f.status === 'renamed' ? (
          <span className="inline-flex items-center gap-1 rounded-md bg-[var(--conf-high-bg)] px-2 py-0.5 text-[11px] font-semibold text-[var(--conf-high)] ring-1 ring-[var(--conf-high-32)] ring-inset [&_svg]:size-3" title="File has been renamed"><IcCheck /> Renamed</span>
        ) : f.status === 'approved' ? (
          <span className="inline-flex items-center gap-1 rounded-md bg-[var(--conf-high-bg)] px-2 py-0.5 text-[11px] font-semibold text-[var(--conf-high)] ring-1 ring-[var(--conf-high-32)] ring-inset [&_svg]:size-3" title="Approved — queued for rename"><IcCheck /> Approved</span>
        ) : f.status === 'rejected' ? (
          <span className="inline-flex items-center gap-1 rounded-md bg-[var(--conf-low-bg)] px-2 py-0.5 text-[11px] font-semibold text-[var(--conf-low)] ring-1 ring-[var(--conf-low-32)] ring-inset [&_svg]:size-3" title="Rejected"><IcX /> Rejected</span>
        ) : null}
        <span className={cn('inline-flex items-center rounded-md px-2 py-0.5 text-[11px] font-bold tabular-nums ring-1 ring-inset', confChipClass(confT))}>{conf}%</span>
        <ButtonGroup size="sm">
          <ButtonGroupItem
            color="success"
            iconLeading={IcCheck}
            aria-label="Approve this file"
            title="Approve this file"
            isDisabled={fileIdx < 0}
            onClick={() => fileIdx >= 0 ? updateFile(fileIdx, { status: 'approved' }) : null}
          />
          <ButtonGroupItem
            color="destructive"
            iconLeading={IcX}
            aria-label="Reject this file"
            title="Reject this file"
            isDisabled={fileIdx < 0}
            onClick={() => fileIdx >= 0 ? updateFile(fileIdx, { status: 'rejected' }) : null}
          />
        </ButtonGroup>
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
