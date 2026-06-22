import { describe, it, expect, beforeEach } from 'vitest';
import {
  inferQuality, inferSource, rankFile, confColorP,
  audioLangChip, subLangChip, missingSubChip,
} from './quality';
import { setConfBands } from '../../lib/confBands';
import type { LibFile } from '../../lib/types';

/** Minimal LibFile; `as LibFile` fills the optional fields rankFile ignores. */
function f(over: Partial<LibFile> = {}): LibFile {
  return {
    id: '1', filename: 'file.mkv', folder: '', status: 'pending',
    confidence: 100, matchedToEpisode: null, matchedWrong: false,
    matchId: null, releaseGroup: null,
    ...over,
  } as LibFile;
}

describe('inferQuality — parsed value, else scan the filename', () => {
  it('prefers the parsed quality', () => {
    expect(inferQuality(f({ quality: '720p' }))).toBe('720p');
  });
  it('falls back to a resolution token in the filename', () => {
    expect(inferQuality(f({ filename: 'Show.S01E01.2160p.mkv' }))).toBe('2160p');
  });
  it('maps a WxH dimension to a p-resolution', () => {
    expect(inferQuality(f({ filename: 'Show.1920x1080.mkv' }))).toBe('1080p');
  });
  it('treats a resolution-less BluRay as 1080p', () => {
    expect(inferQuality(f({ filename: 'Show.BluRay.x264.mkv' }))).toBe('1080p');
  });
  it('returns undefined when nothing is inferable', () => {
    expect(inferQuality(f({ filename: 'Show.mkv' }))).toBeUndefined();
  });
});

describe('inferSource', () => {
  it('prefers the parsed source', () => {
    expect(inferSource(f({ source: 'WEB-DL' }))).toBe('WEB-DL');
  });
  it('reads a source token from the filename', () => {
    expect(inferSource(f({ filename: 'Show.S01E01.WEBRip.x264.mkv' }))).toBe('WEBRip');
  });
  it('normalizes a bare BD token to BluRay', () => {
    expect(inferSource(f({ filename: 'Show.BD.1080p.mkv' }))).toBe('BluRay');
  });
  it('returns undefined when no source token is present', () => {
    expect(inferSource(f({ filename: 'Show.mkv' }))).toBeUndefined();
  });
});

describe('rankFile — duplicate "keep best" resolver (negative ⇒ keep a)', () => {
  // This decides which copy survives and which gets removed, so the ordering
  // is genuinely high-stakes — a wrong sign deletes the better file.
  it('keeps higher resolution first', () => {
    expect(rankFile(f({ quality: '2160p' }), f({ quality: '1080p' }))).toBeLessThan(0);
  });
  it('at equal resolution, HDR beats SDR', () => {
    expect(rankFile(f({ quality: '1080p', hdr: 'DV' }), f({ quality: '1080p' }))).toBeLessThan(0);
  });
  it('then BluRay beats WEBRip', () => {
    expect(rankFile(f({ quality: '1080p', source: 'BluRay' }), f({ quality: '1080p', source: 'WEBRip' }))).toBeLessThan(0);
  });
  it('then x265 beats x264', () => {
    expect(rankFile(f({ quality: '1080p', codec: 'x265' }), f({ quality: '1080p', codec: 'x264' }))).toBeLessThan(0);
  });
  it('then 10-bit beats 8-bit', () => {
    expect(rankFile(f({ quality: '1080p', bitDepth: '10bit' }), f({ quality: '1080p', bitDepth: '8bit' }))).toBeLessThan(0);
  });
  it('breaks remaining ties by larger file size (not alphabetical-by-name)', () => {
    // The old alphabetical tie-break preferred space-named Kira outputs over
    // their richer original-source counterparts; size now wins first.
    expect(rankFile(f({ quality: '1080p', sizeBytes: 2_000 }), f({ quality: '1080p', sizeBytes: 1_000 }))).toBeLessThan(0);
  });
  it('sorts a duplicate group best-first', () => {
    const group = [
      f({ filename: 'web.mkv', quality: '720p', source: 'WEBRip' }),
      f({ filename: 'bd.mkv', quality: '1080p', source: 'BluRay' }),
    ];
    const best = [...group].sort(rankFile)[0];
    expect(best.filename).toBe('bd.mkv');
  });
});

describe('language chips', () => {
  it('shows an audio chip only for dual/multi-audio', () => {
    expect(audioLangChip({ audio_langs: ['jpn', 'eng'] })).toBe('JPN+ENG');
    expect(audioLangChip({ audio_langs: ['jpn'] })).toBeNull();   // single audio: no chip
  });
  it('shows a SUB chip whenever subs are present', () => {
    expect(subLangChip({ sub_langs: ['eng'] })).toBe('SUB ENG');
    expect(subLangChip({ sub_langs: [] })).toBeNull();
  });
  it('formats the missing-subtitle gap as "No EN+ES"', () => {
    expect(missingSubChip({ missingSubs: ['en', 'es'] })).toBe('No EN+ES');
    expect(missingSubChip({ missingSubs: [] })).toBeNull();
  });
});

describe('confColorP — swatch color follows the tunable confidence bands', () => {
  // The fix: this used to hardcode 85/50, so the hero avg-confidence swatch
  // ignored the Confidence sliders and could disagree with its own confTier().
  beforeEach(() => setConfBands(85, 50));

  it('maps to high/mid/low CSS vars at the default thresholds', () => {
    expect(confColorP(90)).toBe('var(--conf-high)');
    expect(confColorP(60)).toBe('var(--conf-mid)');
    expect(confColorP(30)).toBe('var(--conf-low)');
  });

  it('shifts when the user retunes the thresholds', () => {
    setConfBands(70, 40);
    expect(confColorP(75)).toBe('var(--conf-high)');  // was mid under 85/50
    expect(confColorP(45)).toBe('var(--conf-mid)');   // was low under 85/50
    expect(confColorP(30)).toBe('var(--conf-low)');
  });
});
