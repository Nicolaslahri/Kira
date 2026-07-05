import type { LibFile } from '../../lib/types';
import { IcCheck } from '../../lib/icons';
import { confTier } from '../LibraryGrid';

/**
 * Alternative match candidates for one file, rendered as a compact list with a
 * confidence bar + one-click "Use" per row. Wired to the existing select
 * endpoint via `onPick` (POST /files/{id}/select/{matchId}) so a wrong
 * auto-pick is correctable IN PLACE — no full Manual Search needed.
 *
 * Renders nothing when there's only one candidate (or none) — there's nothing
 * to switch to.
 */
export function CandidateList({ file, onPick }: {
  file: LibFile;
  onPick?: (fileId: string, candidate: { matchId?: number; title?: string; year?: number | null }) => void | Promise<void>;
}) {
  const candidates = file.candidates ?? [];
  if (!onPick || candidates.length < 2) return null;

  // The currently-selected candidate is the one whose matchId matches the
  // file's selected matchId (fallback: the top/highest-confidence one).
  const selectedId = file.matchId ?? candidates[0]?.matchId ?? null;

  return (
    <section className="cx-movie-section">
      <div className="cx-movie-section-label">
        Other matches <span style={{ color: 'var(--ink-4)', fontWeight: 500 }}>({candidates.length})</span>
      </div>
      <div className="flex flex-col gap-1.5">
        {candidates.map((c, i) => {
          const isCurrent = c.matchId != null && c.matchId === selectedId;
          const tier = confTier(c.confidence);
          const label = c.title
            ? `${c.title}${c.year ? ` (${c.year})` : ''}`
            : c.album
              ? `${c.artist ?? ''}${c.artist ? ' — ' : ''}${c.album}`
              : 'Untitled';
          return (
            <div
              key={c.matchId ?? i}
              className="flex items-center gap-3 rounded-lg px-2.5 py-2"
              style={{
                background: isCurrent ? 'var(--conf-high-bg)' : 'var(--panel-2, rgba(255,255,255,0.03))',
                boxShadow: isCurrent ? 'inset 0 0 0 1px var(--conf-high-32)' : 'inset 0 0 0 1px var(--line)',
              }}
            >
              <div className="min-w-0 flex-1">
                <div className="truncate text-[13px] font-medium text-ink">
                  {label}
                  {isCurrent ? (
                    <span className="badge badge-high" style={{ marginLeft: 8, padding: '1px 6px', fontSize: 10 }}>Current pick</span>
                  ) : null}
                </div>
                {(c.season != null || c.episode != null || c.absoluteEpisode != null) ? (
                  <div className="mt-0.5 text-[11px] text-ink-soft">
                    {c.season != null && c.episode != null ? `S${c.season}E${c.episode}`
                      : c.absoluteEpisode != null ? `Ep ${c.absoluteEpisode}` : ''}
                  </div>
                ) : null}
              </div>
              <div className="flex items-center gap-2">
                <div className="confidence-bar" style={{ width: 56 }}>
                  <div style={{ width: `${c.confidence}%`, background: `var(--conf-${tier})` }} />
                </div>
                <span className="text-xs font-medium tabular-nums" style={{ color: `var(--conf-${tier})`, minWidth: 30, textAlign: 'right' }}>
                  {c.confidence}%
                </span>
              </div>
              {isCurrent ? (
                <span style={{ padding: '5px 10px', color: 'var(--ink-3)', fontSize: 11 }}>In use</span>
              ) : (
                <button
                  className="btn btn-sm"
                  onClick={() => void onPick(file.id, { matchId: c.matchId, title: c.title, year: c.year })}
                >
                  <IcCheck /> Use
                </button>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}
