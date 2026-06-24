import { memo, useState, type CSSProperties, type ReactNode } from 'react';
import type { LibraryItem, LibEpisode, LibFile } from '../../lib/types';
import type { PairedRowShape } from './types';
import { posterSrc } from '../../lib/api';
import { confTier } from '../LibraryGrid';
import { cn } from '../../lib/utils';
import { MarqueeText } from './MarqueeText';
import { ButtonGroup, ButtonGroupItem } from '../base/button-group/button-group';
import { IcCheck, IcX, IcSearch, IcAlertTri } from '../../lib/icons';

// ─────────────────────────────────────────────────────────────────────
// MusicRow — the MUSIC-ONLY pairing row for the CoverPopup. A redesign of
// the shared PairRowCell for music: the leading slot is the track's OWN
// release cover, FULL-BLEED edge-to-edge (replacing the gradient "TRACK NN"
// badge), with the track number moved onto the art as a frosted corner tag.
// The body carries the max useful per-track detail — title · duration, the
// collab artist (when it differs from the album artist), and a quiet
// music-native spec rail (codec/lossless · channels · size) — then the
// filename, demoted. The aside keeps PairRowCell's grammar verbatim:
// status pill → confidence chip → approve/reject ButtonGroup.
//
// Self-contained on purpose (Chip, confChipClass, staggerVar are LOCAL copies)
// so the shared TV/movie/anime row code stays untouched.
// ─────────────────────────────────────────────────────────────────────

// Lossless containers tint the format chip in the music media colour (meaning =
// lossless); lossy (MP3/AAC/OGG) stays neutral grey, so a glance down the rail
// flags the lossless tracks.
const LOSSLESS = /^(FLAC|ALAC|WAV|AIFF|APE|WV|WAVPACK|PCM|DSD|DSF|DFF)$/i;
const AUDIO_EXT = /\.(flac|alac|wav|aiff|ape|wv|dsf|dff|mp3|m4a|aac|ogg|opus|wma)$/i;

/** Audio format for the spec chip — the container codec when known, else derived
 *  from the file extension (music files carry no `f.codec`, but the ".flac"
 *  extension IS the format and reliably flags lossless). */
/** Hz → kHz string for the tech chip: 44100 → "44.1", 96000 → "96", 48000 → "48". */
function khz(hz: number): string {
  const k = hz / 1000;
  return Number.isInteger(k) ? String(k) : k.toFixed(1).replace(/\.0$/, '');
}

function audioFormat(f: LibFile): string | null {
  if (f.codec) return f.codec;
  const m = f.filename.match(AUDIO_EXT);
  return m ? m[1].toUpperCase() : null;
}

function Chip({ children, className, title }: { children: ReactNode; className?: string; title?: string }) {
  return (
    <span
      title={title}
      className={cn(
        'inline-flex items-center whitespace-nowrap rounded-md bg-white/[0.05] px-1.5 py-[3px] text-[10.5px] font-medium leading-none text-tertiary ring-1 ring-inset ring-white/[0.07]',
        className,
      )}
    >
      {children}
    </span>
  );
}

function confChipClass(tier: 'high' | 'mid' | 'low'): string {
  return tier === 'high'
    ? 'bg-[var(--conf-high-bg)] text-[var(--conf-high)] ring-[var(--accent-line)]'
    : tier === 'mid'
      ? 'bg-[var(--conf-mid-bg)] text-[var(--conf-mid)] ring-[var(--conf-mid-32)]'
      : 'bg-[var(--conf-low-bg)] text-[var(--conf-low)] ring-[var(--conf-low-32)]';
}

function staggerVar(i?: number): CSSProperties | undefined {
  return i != null && i >= 0 && i < 24 ? ({ ['--pair-i' as never]: i } as CSSProperties) : undefined;
}

// Shared outer-row recipe — ring hairlines (NOT border), full-bleed SQUARE cover
// via flex + items-stretch (the cover is aspect-square self-stretch → a square
// equal to the row height) + overflow-hidden + gap-0 + a zero-padding cover cell.
const ROW_BASE =
  'anim-pair group/mrow relative my-[7px] flex h-[88px] items-stretch gap-0 overflow-hidden rounded-xl bg-secondary ring-1 ring-inset ring-secondary shadow-xs transition-[background-color,box-shadow] hover:bg-tertiary hover:ring-primary';

// ── The edge-to-edge cover cell (Zone 1). The cover fills the 64px-wide cell
//    to full row height; the track number sits as a frosted corner tag over the
//    art and fades on hover so the sleeve reads unobstructed. Null / broken
//    cover → neutral poster tint with the number CENTERED (never amber, never a
//    broken-image box).
function MusicCover({ ep, tint, dim }: { ep: LibEpisode; tint: [string, string]; dim?: boolean }) {
  const src = posterSrc(ep.coverUrl);
  const [failed, setFailed] = useState(false);
  const num = String(ep.track ?? ep.episode ?? 0).padStart(2, '0');
  const seam = <span className="pointer-events-none absolute inset-y-0 right-0 w-px bg-[var(--line)]" />;
  if (!src || failed) {
    return (
      <div className={cn('relative aspect-square h-full shrink-0 overflow-hidden', dim && 'opacity-45')}>
        <div
          className="absolute inset-0 grid place-items-center"
          style={{ backgroundImage: `linear-gradient(135deg, ${tint[0]}, ${tint[1]})` }}
        >
          <span className="font-mono text-[17px] font-bold tabular-nums leading-none text-white/90 drop-shadow">{num}</span>
        </div>
        {seam}
      </div>
    );
  }
  return (
    <div className={cn('relative aspect-square h-full shrink-0 overflow-hidden', dim && 'opacity-45')}>
      <img
        src={src}
        className="absolute inset-0 size-full object-cover"
        referrerPolicy="no-referrer"
        decoding="async"
        loading="lazy"
        alt=""
        onError={() => setFailed(true)}
      />
      <span className="absolute left-1 top-1 rounded bg-[var(--scrim-60)] px-1 py-px font-mono text-[10px] font-bold tabular-nums leading-none text-white/90 transition-opacity group-hover/mrow:opacity-0">
        {num}
      </span>
      {seam}
    </div>
  );
}

// Match transparency: how a music track resolved → a short label + explainer.
// The fingerprint / ID methods are trustworthy; the positional/fuzzy ones
// (track number, title) are flagged so a wrong match on a low-confidence row
// is easy to spot.
const MATCH_VIA: Record<string, { label: string; title: string; warn?: boolean }> = {
  acoustid:  { label: 'AcoustID',    title: 'Matched by audio fingerprint (AcoustID) — identifies the actual recording regardless of tags' },
  mbid:      { label: 'MusicBrainz', title: 'Matched directly by MusicBrainz release ID — a confident album match' },
  recording: { label: 'recording',   title: 'Matched to a specific MusicBrainz recording' },
  tracknum:  { label: 'track no.',    title: 'Matched by track number / position on the album — a positional guess; verify if the title looks off', warn: true },
  title:     { label: 'title',        title: 'Matched by track-title similarity — verify if the confidence is low', warn: true },
};

interface MusicRowProps {
  row: PairedRowShape;
  item: LibraryItem;
  updateFile: (idx: number, patch: Partial<LibFile>) => void;
  onManualSearch: (item: LibraryItem, episodeIdx?: number | null, fileIdx?: number | null) => void;
  onOpenDupeModal: (episode: LibEpisode, files: LibFile[]) => void;
  staggerIndex?: number;
}

function MusicRowImpl({ row, item, updateFile, onManualSearch, onOpenDupeModal, staggerIndex }: MusicRowProps) {
  const { episode: ep, file } = row;
  const fileIdx = file ? item.files.indexOf(file) : -1;
  const tint = item.poster.tint;

  // ── Missing — a track with no file (rare for music; no Sonarr/Radarr path). ──
  if (!file && ep) {
    return (
      <div
        className={cn(ROW_BASE, 'bg-secondary/40 hover:bg-tertiary')}
        style={staggerVar(staggerIndex)}
      >
        <MusicCover ep={ep} tint={tint} dim />
        <div className="flex min-w-0 flex-1 flex-col justify-center gap-0.5 px-3 py-2 overflow-hidden">
          <MarqueeText className="text-[13.5px] font-semibold leading-tight text-tertiary">
            <span title={ep.title || undefined}>{ep.title || `Track ${ep.track}`}</span>
          </MarqueeText>
          <div className="text-[11px] text-quaternary">
            No file for this track{ep.duration ? <span className="ml-2 font-mono tabular-nums">· {ep.duration}</span> : null}
          </div>
        </div>
        <div className="flex shrink-0 items-center justify-center pl-2 pr-3">
          <span className="inline-flex items-center rounded-md bg-white/[0.04] px-2 py-0.5 text-[11px] font-medium text-quaternary ring-1 ring-inset ring-secondary">—</span>
        </div>
      </div>
    );
  }

  // ── Orphan — a file with no matching track. ──
  if (!ep && file) {
    const rejected = file.status === 'rejected';
    return (
      <div
        className={cn(ROW_BASE, rejected && 'opacity-55 hover:opacity-100')}
        style={{ ...staggerVar(staggerIndex), animation: rejected ? 'none' : undefined }}
      >
        <div className="relative aspect-square h-full shrink-0 overflow-hidden">
          <div
            className="absolute inset-0 grid place-items-center"
            style={{ backgroundImage: `linear-gradient(135deg, ${tint[0]}, ${tint[1]})` }}
          >
            <span className="font-mono text-[20px] font-bold leading-none text-white/70 drop-shadow">—</span>
          </div>
          <span className="pointer-events-none absolute inset-y-0 right-0 w-px bg-[var(--line)]" />
        </div>
        <div className="flex min-w-0 flex-1 flex-col justify-center gap-1.5 px-3 py-2.5">
          <MarqueeText className="font-mono text-[12px] text-secondary">
            <span title={file.filename}>{file.filename}</span>
          </MarqueeText>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[11px] font-medium text-[var(--conf-low)]">Orphaned · no matching track</span>
            <button
              onClick={() => onManualSearch(item, null, fileIdx)}
              className="inline-flex items-center gap-1 rounded-md bg-tertiary px-2 py-1 text-[11px] font-semibold text-secondary ring-1 ring-inset ring-secondary transition-colors hover:bg-primary_hover hover:text-primary [&_svg]:size-3"
            >
              <IcSearch /> Search this file
            </button>
          </div>
        </div>
        <div className="flex shrink-0 items-center justify-center pl-2 pr-3" onClick={(e) => e.stopPropagation()}>
          <span className="inline-flex items-center rounded-md bg-[var(--conf-low-bg)] px-2 py-0.5 text-[11px] font-semibold text-[var(--conf-low)] ring-1 ring-inset ring-[var(--conf-low-32)]">
            No track
          </span>
        </div>
      </div>
    );
  }

  // ── Paired — the common case: a track + the file filling it. ──
  const f = file!;
  const e = ep!;
  const conf = f.confidence ?? 0;
  const confT = confTier(conf);
  const fmt = audioFormat(f);
  // Prefer MediaInfo's authoritative lossless flag (correct for ambiguous containers
  // like M4A = ALAC vs AAC); fall back to the format name when not container-read yet.
  const lossless = f.lossless ?? !!(fmt && LOSSLESS.test(fmt.trim()));
  // Quality spec — bit-depth/sample-rate for lossless, bitrate for lossy (one chip).
  const techSpec = (f.audioBitDepth && f.sampleRate)
    ? `${f.audioBitDepth}-bit/${khz(f.sampleRate)}kHz`
    : (f.audioBitrate ? `${f.audioBitrate} kbps` : null);
  const isDupePrimary = row.kind === 'dupe-primary' && !!row.dupeAll && row.dupeAll.length > 1;

  return (
    <div
      className={cn(
        ROW_BASE,
        (f.status === 'approved' || f.status === 'renamed') && '!bg-[var(--conf-high-bg)] !ring-[var(--conf-high-32)]',
        f.status === 'rejected' && 'opacity-55 hover:opacity-100',
      )}
      style={{ ...staggerVar(staggerIndex), animation: f.status === 'rejected' ? 'none' : undefined }}
    >
      <MusicCover ep={e} tint={tint} />

      {/* Body — title · duration, collab artist, spec rail, filename. */}
      <div className="flex min-w-0 flex-1 flex-col justify-center gap-0.5 px-3 py-2 overflow-hidden">
        <MarqueeText className="text-[13.5px] font-semibold leading-tight text-primary">
          <span title={e.title || undefined}>
            {e.title || `Track ${e.track}`}
            {e.duration ? <span className="ml-2 font-mono text-[11px] font-medium tabular-nums text-quaternary">· {e.duration}</span> : null}
          </span>
        </MarqueeText>

        {/* Collab artist — only when it differs from the album artist. */}
        {e.artist && e.artist !== item.artist ? (
          <span className="truncate text-[11px] font-medium text-secondary">{e.artist}</span>
        ) : null}

        {/* Spec rail — lossless codec tints the music colour as MEANING. */}
        <div className="flex flex-wrap items-center gap-1">
          {/* Cross-album duplicate: this single is also on a real album you have. */}
          {e.dupOf ? (
            <span
              title={`This song is also on your album “${e.dupOf}” — likely a duplicate of that track`}
              className="inline-flex items-center gap-1 whitespace-nowrap rounded-md bg-[var(--conf-mid-bg)] px-1.5 py-[3px] text-[10.5px] font-semibold leading-none text-[var(--conf-mid)] ring-1 ring-inset ring-[var(--conf-mid-32)] [&_svg]:size-3 [&_svg]:shrink-0"
            >
              {/* Strip the edition suffix ("(triple chucks deluxe)") so the pill stays
                  short; the full album name lives in the tooltip. */}
              <IcAlertTri /> Also on {e.dupOf.replace(/\s*\([^)]*\)\s*$/, '').trim() || e.dupOf}
            </span>
          ) : null}
          {fmt ? (
            <Chip
              title={lossless ? 'Lossless' : undefined}
              className={lossless ? 'bg-[var(--media-music-12)] text-[var(--media-music)] ring-[var(--media-music-32)]' : undefined}
            >
              {fmt}
            </Chip>
          ) : null}
          {techSpec ? <Chip title="Audio quality" className="tabular-nums">{techSpec}</Chip> : null}
          {f.channels ? <Chip>{f.channels}</Chip> : null}
          {f.size ? <Chip className="tabular-nums">{f.size}</Chip> : null}
          {/* Match transparency — HOW this track matched. Fuzzy methods
              (track number / title) tint amber as a "verify me" cue. */}
          {(() => {
            const via = f.matchedVia ? MATCH_VIA[f.matchedVia] : undefined;
            return via ? (
              <Chip
                title={via.title}
                className={via.warn
                  ? 'bg-[var(--conf-mid-bg)] text-[var(--conf-mid)] ring-[var(--conf-mid-32)]'
                  : 'text-tertiary'}
              >
                via {via.label}
              </Chip>
            ) : null;
          })()}
          {isDupePrimary ? (
            <button
              onClick={(ev) => { ev.stopPropagation(); if (row.episode && row.dupeAll) onOpenDupeModal(row.episode, row.dupeAll); }}
              title={`${row.dupeAll!.length} files claim this song — click to pick which to keep`}
              className="inline-flex items-center gap-1 whitespace-nowrap rounded-md bg-[var(--conf-mid-bg)] px-1.5 py-[3px] text-[10.5px] font-semibold leading-none text-[var(--conf-mid)] ring-1 ring-inset ring-[var(--conf-mid-32)] transition-colors hover:bg-[var(--conf-mid-24)] [&_svg]:size-3"
            >
              <IcAlertTri /> +{row.dupeAll!.length - 1}
            </button>
          ) : null}
        </div>

        <MarqueeText className="font-mono text-[10.5px] text-quaternary">
          <span title={f.filename}>{f.filename}</span>
        </MarqueeText>
      </div>

      {/* Aside — status pill → confidence chip → approve/reject. */}
      <div className="flex shrink-0 flex-col items-end justify-center gap-2 py-2.5 pl-2 pr-3" onClick={(ev) => ev.stopPropagation()}>
        {f.status === 'renamed' ? (
          <span className="inline-flex items-center gap-1 rounded-md bg-[var(--conf-high-bg)] px-2 py-0.5 text-[11px] font-semibold text-[var(--conf-high)] ring-1 ring-inset ring-[var(--conf-high-32)] [&_svg]:size-3" title="File has been renamed"><IcCheck /> Renamed</span>
        ) : f.status === 'approved' ? (
          <span className="inline-flex items-center gap-1 rounded-md bg-[var(--conf-high-bg)] px-2 py-0.5 text-[11px] font-semibold text-[var(--conf-high)] ring-1 ring-inset ring-[var(--conf-high-32)] [&_svg]:size-3" title="Approved — queued for rename"><IcCheck /> Approved</span>
        ) : f.status === 'rejected' ? (
          <span className="inline-flex items-center gap-1 rounded-md bg-[var(--conf-low-bg)] px-2 py-0.5 text-[11px] font-semibold text-[var(--conf-low)] ring-1 ring-inset ring-[var(--conf-low-32)] [&_svg]:size-3" title="Rejected"><IcX /> Rejected</span>
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

const musicRowsEqual = (a: MusicRowProps, b: MusicRowProps): boolean => {
  if (a.row.key !== b.row.key) return false;
  if (a.row.kind !== b.row.kind) return false;
  if (a.row.file?.id !== b.row.file?.id) return false;
  if (a.row.file?.status !== b.row.file?.status) return false;
  if (a.row.file?.confidence !== b.row.file?.confidence) return false;
  if ((a.row.dupeAll?.length ?? 0) !== (b.row.dupeAll?.length ?? 0)) return false;
  const ea = a.row.episode, eb = b.row.episode;
  if ((ea == null) !== (eb == null)) return false;
  if (ea && eb) {
    if (ea.title !== eb.title) return false;
    if (ea.track !== eb.track) return false;
    if (ea.duration !== eb.duration) return false;
    if (ea.artist !== eb.artist) return false;
    if (ea.coverUrl !== eb.coverUrl) return false;
    if (ea.dupOf !== eb.dupOf) return false;
  }
  return true;
};

export const MusicRow = memo(MusicRowImpl, musicRowsEqual);
