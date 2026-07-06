import type { LibFile } from '../lib/types';

/** Apple-TV-style tech-spec badges — thin white-bordered rounded rectangles
 *  with white wordmark text, all one height so the rail reads as one family.
 *  Raw MediaInfo strings are normalized to the marks people recognise
 *  ("DV" → DOLBY VISION, "EAC3" → DD+, 2160p → 4K). Styled wordmarks stand in
 *  for brand logos (Dolby/DTS marks are trademarks — the bordered-wordmark
 *  treatment is exactly how Apple TV renders them at this size anyway). */

export function TechBadge({ label, title }: { label: string; title?: string }) {
  return (
    <span className="tech-badge" title={title ?? label}>
      {label}
    </span>
  );
}

/* ── normalizers ─────────────────────────────────────────────────────────── */

function resolutionMark(q: string | undefined): string | null {
  if (!q) return null;
  const s = q.toLowerCase();
  if (s.includes('2160') || s.includes('4k') || s.includes('uhd')) return '4K';
  if (s.includes('1080')) return 'HD';
  if (s.includes('720')) return '720P';
  if (s.includes('480') || s.includes('576')) return 'SD';
  return q.toUpperCase();
}

function hdrMark(hdr: string | undefined): string | null {
  if (!hdr) return null;
  const s = hdr.toLowerCase();
  if (s.includes('dolby') || /\bdv\b/.test(s) || s.includes('vision')) return 'DOLBY VISION';
  if (s.includes('hdr10+') || s.includes('hdr10plus')) return 'HDR10+';
  if (s.includes('hdr')) return 'HDR';
  if (s.includes('hlg')) return 'HLG';
  return hdr.toUpperCase();
}

function codecMark(codec: string | undefined): string | null {
  if (!codec) return null;
  const s = codec.toLowerCase();
  if (s.includes('hevc') || s.includes('265')) return 'HEVC';
  if (s.includes('av1')) return 'AV1';
  if (s.includes('264') || s.includes('avc')) return 'H.264';
  if (s.includes('vp9')) return 'VP9';
  if (s.includes('mpeg2')) return 'MPEG-2';
  return codec.toUpperCase();
}

function audioMark(audio: string | undefined): string | null {
  if (!audio) return null;
  const s = audio.toLowerCase();
  if (s.includes('atmos')) return 'DOLBY ATMOS';
  if (s.includes('truehd')) return 'DOLBY TRUEHD';
  if (s.includes('dts:x') || s.includes('dts-x') || s.includes('dtsx')) return 'DTS:X';
  if (s.includes('dts-hd') || s.includes('dts hd')) return 'DTS-HD MA';
  if (s.includes('dts')) return 'DTS';
  if (s.includes('eac3') || s.includes('e-ac3') || s.includes('ddp') || s.includes('dd+')) return 'DD+';
  if (s.includes('ac3') || /\bdd\b/.test(s)) return 'DOLBY DIGITAL';
  if (s.includes('aac')) return 'AAC';
  if (s.includes('flac')) return 'FLAC';
  if (s.includes('opus')) return 'OPUS';
  if (s.includes('pcm')) return 'PCM';
  return audio.toUpperCase();
}

/** Ordered white-badge rail for a file's tech specs: resolution → HDR →
 *  video codec → audio format → channel layout. Anything missing is skipped;
 *  duplicate marks (e.g. Atmos implying TrueHD) are deduped. */
export function techBadgesFor(f: Pick<LibFile, 'quality' | 'hdr' | 'codec' | 'channels'> & { audio?: string[] }): string[] {
  const out: string[] = [];
  const push = (v: string | null) => { if (v && !out.includes(v)) out.push(v); };
  push(resolutionMark(f.quality));
  push(hdrMark(f.hdr));
  push(codecMark(f.codec));
  push(audioMark(f.audio?.[0]));
  if (f.channels) push(f.channels.toUpperCase());
  return out;
}

export function TechBadges({ file }: { file: Pick<LibFile, 'quality' | 'hdr' | 'codec' | 'channels'> & { audio?: string[] } }) {
  const marks = techBadgesFor(file);
  if (!marks.length) return null;
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      {marks.map(m => <TechBadge key={m} label={m} />)}
    </span>
  );
}
