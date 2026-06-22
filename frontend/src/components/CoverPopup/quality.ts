import type { LibFile } from '../../lib/types';
import { confLevel } from '../../lib/confBands';

// ─────────────────────────────────────────────────────────────────────
// File-quality inference + duplicate ranking + language chips.
// Pure leaf helpers extracted from CoverPopup so the row cells, the
// dedupe resolver, and the hero can share them without the 4k-line file.
// ─────────────────────────────────────────────────────────────────────

/** CSS color var for a match-confidence percent. Delegates to the tunable
 *  bands (Settings → Confidence) via confLevel, so the hero's avg-confidence
 *  swatch tracks the user's thresholds AND agrees with the confTier() class on
 *  the same chip — instead of the old hardcoded 85/50 that ignored the slider. */
export function confColorP(v: number): string {
  return `var(--conf-${confLevel(v)})`;
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
// Per-track language chips (MediaInfo). Codes are ISO-639-2/B (jpn/eng/…); we
// just uppercase + cap the display so a many-track file doesn't blow up the row.
function _fmtLangs(langs: string[]): string {
  const clean = langs.filter(Boolean);
  const shown = clean.slice(0, 3).map(l => l.toUpperCase());
  const extra = clean.length - shown.length;
  return shown.join('+') + (extra > 0 ? `+${extra}` : '');
}
// Dual/multi-audio is the notable signal ([JPN+ENG]); a single audio language
// isn't worth a chip. Subtitles show whenever present.
export function audioLangChip(file: { audio_langs?: string[] }): string | null {
  const langs = file.audio_langs?.filter(Boolean) ?? [];
  return langs.length >= 2 ? _fmtLangs(langs) : null;
}
export function subLangChip(file: { sub_langs?: string[] }): string | null {
  const langs = file.sub_langs?.filter(Boolean) ?? [];
  return langs.length >= 1 ? 'SUB ' + _fmtLangs(langs) : null;
}
// Wanted languages this file is MISSING (backend-computed coverage gap).
// "No EN" / "No EN+ES" — drives the amber warning chip + the bulk fetch count.
export function missingSubChip(file: { missingSubs?: string[] }): string | null {
  const langs = file.missingSubs?.filter(Boolean) ?? [];
  return langs.length >= 1 ? 'No ' + langs.map(l => l.toUpperCase()).join('+') : null;
}

// MediaInfo dupe signals (lower = better). HDR flavor: DV > HDR10+ > HDR10 > HLG;
// a file with ANY HDR beats SDR (no tag → 9). Channels: more speakers win.
const _HDR_RANK: Record<string, number> = { dv: 0, 'hdr10+': 1, hdr10: 2, hlg: 3 };
const _CHAN_RANK: Record<string, number> = {
  '9.1': 0, '7.1': 1, '6.1': 2, '5.1': 3, '4.1': 4, '4.0': 5, '2.1': 6, '2.0': 7, '1.0': 8,
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

export function rankFile(a: LibFile, b: LibFile): number {
  // 1. Resolution
  const qa = _Q_RANK[inferQuality(a) ?? ''] ?? 9;
  const qb = _Q_RANK[inferQuality(b) ?? ''] ?? 9;
  if (qa !== qb) return qa - qb;
  // 1b. HDR — at equal resolution an HDR grade beats SDR (and DV / HDR10+ beat
  //     plain HDR10). No tag → SDR → ranks last. MediaInfo-derived.
  const ha = _HDR_RANK[(a.hdr ?? '').toLowerCase()] ?? 9;
  const hb = _HDR_RANK[(b.hdr ?? '').toLowerCase()] ?? 9;
  if (ha !== hb) return ha - hb;
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
  // 4b. Audio channels — more speakers win (7.1 > 5.1 > 2.0). MediaInfo-derived;
  //     unknown layout ranks last so a tagged file beats an untagged one.
  const cha = _CHAN_RANK[(a.channels ?? '').toLowerCase()] ?? 9;
  const chb = _CHAN_RANK[(b.channels ?? '').toLowerCase()] ?? 9;
  if (cha !== chb) return cha - chb;
  // 5. File size — larger usually = higher bitrate = less compression.
  //    Only kicks in when 1-4 all tie (e.g. byte-identical copies that
  //    differ only by name).
  if (a.sizeBytes != null && b.sizeBytes != null && a.sizeBytes !== b.sizeBytes) {
    return b.sizeBytes - a.sizeBytes; // descending
  }
  // 6. Stable alphabetical fallback for true ties (or missing size data).
  return a.filename.localeCompare(b.filename);
}
