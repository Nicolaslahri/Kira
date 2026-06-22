import type { ReactNode } from 'react';
import type { LibraryItem } from '../../lib/types';
import { IcExternal } from '../../lib/icons';

// ─────────────────────────────────────────────────────────────────────
// Pure formatting + tiny presentational leaves shared across the popup
// (hero, rows, modals). Extracted from CoverPopup so the component files
// can import them without the 4k-line parent.
// ─────────────────────────────────────────────────────────────────────

export function mediaTypeLong(item: LibraryItem): string {
  if (item.mediaType === 'tv') return 'TV Series';
  if (item.mediaType === 'anime') return 'Anime';
  if (item.mediaType === 'movie') return 'Movie';
  return 'Album';
}

export function detectFromFilename(filename: string, item: LibraryItem): string | null {
  if (item.kind === 'album') {
    const m = filename.match(/^(\d{1,2})\b/) || filename.match(/[-_\s]+(\d{1,2})\b/);
    return m ? String(+m[1]).padStart(2, '0') : null;
  }
  if (item.mediaType === 'anime') {
    const m = filename.match(/-\s*(\d{1,3})\s*[\[\(]/);
    if (m) return m[1].padStart(2, '0');
  }
  const m = filename.match(/[Ss](\d{1,2})[Ee](\d{1,2})/);
  if (m) return `S${m[1].padStart(2, '0')}E${m[2].padStart(2, '0')}`;
  return null;
}

export function formatEta(seconds: number | null): string | null {
  if (seconds == null || seconds <= 0) return null;
  if (seconds < 60) return `${seconds}s left`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min left`;
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  return m > 0 ? `${h}h ${m}m left` : `${h}h left`;
}

export function formatBytes(n: number | null): string | null {
  if (n == null || n <= 0) return null;
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(0)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

export function statusLabel(status: string): string {
  switch (status) {
    case 'queued':       return 'Queued';
    case 'searching':    return 'Searching';
    case 'downloading':  return 'Downloading';
    case 'importing':    return 'Importing';
    case 'completed':    return 'Imported';
    case 'failed':       return 'Failed';
    case 'warning':      return 'Warning';
    default:             return status;
  }
}

const _WEEKDAYS = [
  'Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday',
];
const _MONTHS = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
];

export function formatUpcomingAirDate(iso: string): string | null {
  // Provider dates are typically `YYYY-MM-DD` (date only, no time).
  // We compare against today's local-midnight so an episode airing
  // "today" is correctly treated as today even though its parsed
  // Date object lands at 00:00 UTC. Off-by-one timezone bugs in
  // this comparison would either show today's episode as "Airs
  // today" the day before it actually airs (annoying) or miss
  // calling out today's episode at all (worse).
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return null;

  // Truncate both sides to midnight of their respective local day.
  const air = new Date(parsed.getFullYear(), parsed.getMonth(), parsed.getDate());
  const today = new Date();
  today.setHours(0, 0, 0, 0);

  const dayMs = 86_400_000;
  const daysAhead = Math.round((air.getTime() - today.getTime()) / dayMs);

  if (daysAhead < 0) return null;     // already aired — caller uses "No file"
  if (daysAhead === 0) return 'Airs today';
  if (daysAhead === 1) return 'Airs tomorrow';
  if (daysAhead <= 7) return `Airs ${_WEEKDAYS[air.getDay()]}`;
  if (daysAhead <= 30) return `Airs in ${daysAhead} days`;
  const monthName = _MONTHS[air.getMonth()];
  const day = air.getDate();
  const includeYear = air.getFullYear() !== today.getFullYear();
  return includeYear
    ? `Airs ${monthName} ${day}, ${air.getFullYear()}`
    : `Airs ${monthName} ${day}`;
}

export function ProviderLink({ label, href }: { label: string; href: string }) {
  return (
    <a className="cx-prov-link" href={href} target="_blank" rel="noreferrer">
      {label} <IcExternal />
    </a>
  );
}

export function Chip({ children, accent }: { children: ReactNode; accent?: boolean }) {
  return (
    <span
      style={{
        fontSize: 11, padding: '3px 8px', borderRadius: 4,
        background: accent ? 'var(--warn-16)' : 'var(--glass-2)',
        color: accent ? 'var(--brand-a)' : 'var(--ink-2)',
        border: '1px solid ' + (accent ? 'var(--warn-32)' : 'var(--line)'),
        fontWeight: 500,
      }}
    >
      {children}
    </span>
  );
}
