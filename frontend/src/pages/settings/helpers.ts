import type { ToastData } from '../../lib/types';

/** Curried per-key optimistic save: `saveKey('a.b')(value)`. */
export type SaveKeyFn = (key: string) => (value: string | number | boolean) => void;

/** Toast emitter passed down from App. */
export type PushToast = (t: Omit<ToastData, 'id'>) => void;

/**
 * Read a string setting, handling the masked-secret shape produced when a
 * value is bootstrapped from `.env` (`{ masked: true, tail }`).
 */
export function strSetting(s: Record<string, unknown>, key: string): string {
  const v = s[key];
  if (typeof v === 'string') return v;
  if (v && typeof v === 'object' && 'masked' in v) {
    const tail = (v as { tail?: string }).tail ?? '';
    return tail ? `•••• •••• •••• ${tail}` : '••••';
  }
  return '';
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
