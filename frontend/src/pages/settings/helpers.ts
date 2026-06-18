import type { ToastData } from '../../lib/types';

/** Curried per-key optimistic save: `saveKey('a.b')(value)`. */
export type SaveKeyFn = (key: string) => (value: string | number | boolean) => void;

/** Toast emitter passed down from App. */
export type PushToast = (t: Omit<ToastData, 'id'>) => void;

/**
 * Read a string setting as an EDITABLE value.
 *
 * Secrets are server-masked: GET /settings returns them as
 * `{ masked: true, tail, set }` (never the plaintext). A masked secret has no
 * editable text — we return '' so the field renders an empty box. The bullet
 * placeholder is shown via {@link maskHint} as the input's *placeholder*, never
 * as its value. This is the crux of the "secret won't update" fix: bullets are
 * never part of the editable value, so the user always types into an empty box
 * and the backend's mask-guard (`_looks_like_mask`) never sees a `•` on save.
 *
 * A raw string IS returned verbatim — that's the just-typed (unsaved) value, or
 * a non-secret setting.
 */
export function strSetting(s: Record<string, unknown>, key: string): string {
  const v = s[key];
  if (typeof v === 'string') return v;
  // Masked-secret object: no plaintext to edit.
  return '';
}

/** True when the stored value is a server-masked secret (`{ masked, tail, set }`). */
export function isMasked(s: Record<string, unknown>, key: string): boolean {
  const v = s[key];
  return !!v && typeof v === 'object' && 'masked' in (v as object);
}

/**
 * True when a secret is configured server-side — either the masked shape with
 * `set: true`, OR a non-empty plaintext string (the value the user just typed
 * this session, before a reload re-masks it).
 */
export function secretSet(s: Record<string, unknown>, key: string): boolean {
  const v = s[key];
  if (typeof v === 'string') return v.length > 0;
  if (v && typeof v === 'object' && 'masked' in (v as object)) {
    return (v as { set?: boolean }).set === true;
  }
  return false;
}

/** Last 4 chars of a masked secret (the server's fingerprint), or '' if none. */
export function maskTail(s: Record<string, unknown>, key: string): string {
  const v = s[key];
  if (v && typeof v === 'object' && 'masked' in (v as object)) {
    return (v as { tail?: string }).tail ?? '';
  }
  return '';
}

/**
 * Placeholder text for a masked secret input: a few bullets + the tail so the
 * user can confirm a key is saved, plus a "type to replace" nudge. Returns
 * `undefined` when nothing is saved so the caller's own placeholder shows.
 */
export function maskHint(s: Record<string, unknown>, key: string): string | undefined {
  if (!isMasked(s, key)) return undefined;
  const tail = maskTail(s, key);
  return tail
    ? `•••• ${tail} — saved, type to replace`
    : '•••• saved — type to replace';
}

/**
 * True when `v` is a usable http(s) URL (or empty — an unset field isn't
 * "invalid", just blank). Used to flag malformed integration URLs inline.
 */
export function isValidHttpUrl(v: string): boolean {
  if (!v.trim()) return true;
  try {
    const u = new URL(v.trim());
    return u.protocol === 'http:' || u.protocol === 'https:';
  } catch {
    return false;
  }
}
