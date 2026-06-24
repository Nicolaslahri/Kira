import { useState, type CSSProperties } from 'react';
import type { LibraryItem } from '../../lib/types';
import { posterSrc } from '../../lib/api';

// ─────────────────────────────────────────────────────────────────────
// HeroCoverMosaic — MUSIC-ONLY cover wall for the CoverPopup hero slot.
//
// A "Singles" folder matches each track to its OWN release, so its tracks
// carry DISTINCT covers (per-recording Cover-Art-Archive art). Instead of
// one repeated album sleeve, the hero shows a contact-sheet mosaic of those
// distinct covers. A normal album (one cover repeated) has a single distinct
// cover → the caller (Hero) renders the existing one-big-cover branch instead;
// this component is only mounted for ≥2 distinct covers, so it never degrades
// into a 1×1 "mosaic".
//
// Static by design (Flow restraint + avoids the animated-content screenshot
// hang). Per-tile <img> onError falls back to a NEUTRAL poster tint so a slow
// / broken Cover-Art-Archive image never punches a hole in the wall.
// ─────────────────────────────────────────────────────────────────────

/** Distinct, order-preserved, proxied per-track covers (nulls excluded). */
export function distinctCovers(item: LibraryItem): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  for (const e of item.episodes) {
    const s = posterSrc(e.coverUrl);
    if (s && !seen.has(s)) {
      seen.add(s);
      out.push(s);
    }
  }
  return out;
}

type Cell =
  | { kind: 'img'; src: string; spanRows?: boolean }
  | { kind: 'overflow'; count: number };

/** Grid template + the cells to render for N distinct covers. The big Hero slot
 *  caps at a 3×3 wall (≈72px tiles at 220px — 4×4 would be mush); `compact` (the
 *  small review-page card) caps at a 2×2 quad and drops the overflow/count chrome
 *  so the tiles stay legible at ~90px. */
function layoutFor(covers: string[], compact: boolean): { cols: string; rows: string; cells: Cell[] } {
  const n = covers.length;
  if (n === 2) {
    return { cols: '1fr 1fr', rows: '1fr', cells: [
      { kind: 'img', src: covers[0] }, { kind: 'img', src: covers[1] },
    ] };
  }
  if (n === 3) {
    // Asymmetric trio — first cover spans both rows of column 1, the other two
    // stack in column 2 (no empty 4th cell).
    return { cols: '1fr 1fr', rows: '1fr 1fr', cells: [
      { kind: 'img', src: covers[0], spanRows: true },
      { kind: 'img', src: covers[1] }, { kind: 'img', src: covers[2] },
    ] };
  }
  if (n === 4 || compact) {
    // Quad — the compact card always tops out here (first 4 sleeves).
    return { cols: '1fr 1fr', rows: '1fr 1fr', cells: covers.slice(0, 4).map(s => ({ kind: 'img', src: s } as Cell)) };
  }
  // n ≥ 5 → 3×3 wall (9 cells).
  const cells: Cell[] = [];
  if (n >= 10) {
    for (let i = 0; i < 8; i++) cells.push({ kind: 'img', src: covers[i] });
    cells.push({ kind: 'overflow', count: n - 8 });
  } else {
    // 5..9 → fill all 9 cells; re-walk from the start for the trailing holes so
    // the wall reads balanced (never a gappy grid).
    for (let i = 0; i < 9; i++) cells.push({ kind: 'img', src: covers[i % n] });
  }
  return { cols: '1fr 1fr 1fr', rows: '1fr 1fr 1fr', cells };
}

function MosaicTile({ src, tint, spanRows }: { src: string; tint: [string, string]; spanRows?: boolean }) {
  const [failed, setFailed] = useState(false);
  const style: CSSProperties | undefined = spanRows ? { gridRow: '1 / span 2' } : undefined;
  if (failed) {
    return <div style={{ ...style, backgroundImage: `linear-gradient(135deg, ${tint[0]}, ${tint[1]})` }} />;
  }
  return (
    <img
      src={src}
      style={style}
      className="size-full object-cover"
      referrerPolicy="no-referrer"
      decoding="async"
      loading="lazy"
      alt=""
      onError={() => setFailed(true)}
    />
  );
}

interface MosaicProps {
  covers: string[];           // distinct, proxied (from distinctCovers)
  tint: [string, string];     // neutral fallback gradient
  title: string;              // for the a11y label
  /** Compact = the small review-page card: 2×2 cap, no overflow tile / count chip. */
  compact?: boolean;
}

export function HeroCoverMosaic({ covers, tint, title, compact = false }: MosaicProps) {
  const n = covers.length;
  const { cols, rows, cells } = layoutFor(covers, compact);
  return (
    <div
      role="img"
      aria-label={`Cover mosaic for ${title} — ${n} distinct covers`}
      style={{
        position: 'absolute', inset: 0, display: 'grid',
        gridTemplateColumns: cols, gridTemplateRows: rows,
        gap: '2px', borderRadius: 'inherit', overflow: 'hidden',
        background: 'var(--panel)',  // the 2px gaps read as thin dark seams
      }}
    >
      {cells.map((c, i) => c.kind === 'overflow' ? (
        <div key={i} style={{ background: 'var(--scrim-60)', display: 'grid', placeItems: 'center' }}>
          <span className="text-[15px] font-semibold tabular-nums text-white">+{c.count}</span>
        </div>
      ) : (
        <MosaicTile key={i} src={c.src} tint={tint} spanRows={c.spanRows} />
      ))}
      {/* Count chip — contact-sheet signature, canonical eyebrow recipe. Hidden on
          the compact card (its "N tracks" detail already carries the count). */}
      {!compact ? (
        <span className="absolute bottom-2 right-2 rounded-md bg-[var(--scrim-60)] px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.14em] tabular-nums text-white ring-1 ring-inset ring-white/15">
          {n} covers
        </span>
      ) : null}
    </div>
  );
}
