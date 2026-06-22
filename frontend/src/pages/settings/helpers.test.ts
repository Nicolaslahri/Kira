import { describe, it, expect } from 'vitest';
import {
  strSetting, isMasked, secretSet, maskTail, maskHint, isValidHttpUrl, humanizeSettingKey,
} from './helpers';

// The masked-secret wire shape returned by GET /settings.
const masked = (over: Record<string, unknown> = {}) => ({ masked: true, set: true, tail: 'cdef', ...over });

describe('strSetting — editable value', () => {
  it('returns a plain string verbatim (the just-typed / non-secret value)', () => {
    expect(strSetting({ 'a.b': 'hello' }, 'a.b')).toBe('hello');
  });
  it('returns empty for a masked secret so the field renders an empty box', () => {
    // Crux of the "secret won't update" fix: bullets are never the editable
    // value, so the backend mask-guard never sees a • on save.
    expect(strSetting({ 'api.key': masked() }, 'api.key')).toBe('');
  });
  it('returns empty for a missing key', () => {
    expect(strSetting({}, 'nope')).toBe('');
  });
});

describe('isMasked', () => {
  it('detects the masked-secret object', () => {
    expect(isMasked({ k: masked() }, 'k')).toBe(true);
  });
  it('is false for plain strings and missing keys', () => {
    expect(isMasked({ k: 'plain' }, 'k')).toBe(false);
    expect(isMasked({}, 'k')).toBe(false);
  });
});

describe('secretSet — is a secret configured', () => {
  it('true for a non-empty just-typed string', () => {
    expect(secretSet({ k: 'sk-123' }, 'k')).toBe(true);
  });
  it('false for an empty string', () => {
    expect(secretSet({ k: '' }, 'k')).toBe(false);
  });
  it('reflects the masked object set flag', () => {
    expect(secretSet({ k: masked({ set: true }) }, 'k')).toBe(true);
    expect(secretSet({ k: masked({ set: false }) }, 'k')).toBe(false);
  });
  it('false for a missing key', () => {
    expect(secretSet({}, 'k')).toBe(false);
  });
});

describe('maskTail / maskHint — placeholder fingerprint', () => {
  it('extracts the tail from a masked object, empty otherwise', () => {
    expect(maskTail({ k: masked({ tail: 'wxyz' }) }, 'k')).toBe('wxyz');
    expect(maskTail({ k: 'plain' }, 'k')).toBe('');
  });
  it('builds a "saved, type to replace" hint with the tail', () => {
    expect(maskHint({ k: masked({ tail: 'cdef' }) }, 'k')).toBe('•••• cdef — saved, type to replace');
  });
  it('builds a tail-less hint when no fingerprint is present', () => {
    expect(maskHint({ k: masked({ tail: undefined }) }, 'k')).toBe('•••• saved — type to replace');
  });
  it('returns undefined for a non-masked field so the caller placeholder shows', () => {
    expect(maskHint({ k: 'plain' }, 'k')).toBeUndefined();
  });
});

describe('humanizeSettingKey — save-bar fallback label', () => {
  it('takes the leaf segment, unslugs underscores, and sentence-cases it', () => {
    expect(humanizeSettingKey('matching.auto_threshold')).toBe('Auto threshold');
    expect(humanizeSettingKey('rename.cleanup_extra_filenames')).toBe('Cleanup extra filenames');
  });
  it('handles a key with no dot', () => {
    expect(humanizeSettingKey('profile')).toBe('Profile');
  });
  it('returns the key untouched when there is no leaf to humanize', () => {
    expect(humanizeSettingKey('')).toBe('');
  });
});

describe('isValidHttpUrl — integration URL guard', () => {
  it('accepts http(s) URLs', () => {
    expect(isValidHttpUrl('http://localhost:8989')).toBe(true);
    expect(isValidHttpUrl('https://sonarr.example.com')).toBe(true);
  });
  it('treats blank as valid (unset is not invalid, just empty)', () => {
    expect(isValidHttpUrl('')).toBe(true);
    expect(isValidHttpUrl('   ')).toBe(true);
  });
  it('rejects non-http(s) schemes and junk', () => {
    expect(isValidHttpUrl('ftp://host/file')).toBe(false);
    expect(isValidHttpUrl('javascript:alert(1)')).toBe(false);
    expect(isValidHttpUrl('not a url')).toBe(false);
  });
});
