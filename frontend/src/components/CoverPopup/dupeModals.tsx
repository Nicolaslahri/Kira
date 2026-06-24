import { useState, useEffect, type ReactNode } from 'react';
import type { LibraryItem, LibEpisode, LibFile } from '../../lib/types';
import { IcX, IcTrash, IcAlertTri } from '../../lib/icons';
import { inferQuality, inferSource, audioLangChip, subLangChip } from './quality';
import { Chip } from './format';

// ─────────────────────────────────────────────────────────────────────
// Duplicate-resolver + delete-confirm modals. Extracted from CoverPopup —
// a self-contained cluster (the dedupe picker + its single / bulk delete
// confirmations). Rendered by CoverPopup based on its dupe / delete state.
// ─────────────────────────────────────────────────────────────────────

interface DupesResolverModalProps {
  item: LibraryItem;
  episode: LibEpisode;
  files: LibFile[];
  onClose: () => void;
  /** Delete ONE specific file (the per-row trash → single confirm). */
  onRequestDelete: (file: LibFile) => void;
  /** Delete the loser copies in ONE confirmation (keep the chosen one). */
  onBulkDelete: (losers: LibFile[]) => void;
}

export function DupesResolverModal({ item, episode, files, onClose, onRequestDelete, onBulkDelete }: DupesResolverModalProps) {
  void item;
  // Which copy the user explicitly picked to keep (null = use the default).
  // `keptId` is DERIVED, not synced via an effect: if the pick is absent (file
  // deleted, or nothing picked yet) it falls back to the top-ranked best copy.
  const [picked, setPicked] = useState<string | null>(null);
  const keptId = picked && files.some(f => f.id === picked) ? picked : (files[0]?.id ?? '');
  const setKeptId = setPicked;
  // Auto-close when no more duplicates — the parent has already filtered
  // out deletedIds, so files.length===1 means the user resolved this group.
  useEffect(() => {
    if (files.length <= 1) onClose();
  }, [files.length, onClose]);

  // Escape closes the resolver (parity with the popup + backdrop click).
  // Capture phase + stopPropagation so this sub-modal's Escape intercepts
  // BEFORE the parent popup's window-level Escape handler — otherwise one
  // press would close both the resolver and the whole popup.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      e.stopPropagation();
      onClose();
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [onClose]);

  const losers = files.filter(f => f.id !== keptId);

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0,
        background: 'var(--panel-75)',
        backdropFilter: 'blur(6px)',
        WebkitBackdropFilter: 'blur(6px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        zIndex: 9000,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: 'var(--panel)',
          color: 'var(--ink)',
          borderRadius: 'var(--r-lg)',
          padding: 24,
          maxWidth: 760,
          width: '92%',
          maxHeight: '82vh',
          overflow: 'hidden',
          display: 'flex', flexDirection: 'column',
          border: '1px solid var(--line-strong)',
          boxShadow: '0 24px 60px var(--scrim-60)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 16, marginBottom: 18 }}>
          <div
            style={{
              flexShrink: 0,
              width: 44, height: 44, borderRadius: 8,
              background: 'var(--conf-mid-16)',
              color: 'var(--conf-mid)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontWeight: 700, fontSize: 18,
            }}
          >
            {files.length}
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <h3 style={{ margin: '0 0 4px 0', fontSize: 17, fontWeight: 600, display: 'flex', alignItems: 'baseline', gap: 10 }}>
              Duplicate files for {item.kind === 'album'
                ? `Track ${episode.track ?? episode.episode}`
                : (episode.season ? `S${String(episode.season).padStart(2, '0')}E${String(episode.episode).padStart(2, '0')}` : `Episode ${episode.episode}`)}
            </h3>
            <div style={{ fontSize: 13, color: 'var(--ink-2)' }}>
              {episode.title || (item.kind === 'album' ? `Track ${episode.track ?? episode.episode}` : `Episode ${episode.episode}`)}
              <span style={{ color: 'var(--ink-3)', marginLeft: 8 }}>
                · Keep one copy, delete the other {losers.length}
              </span>
            </div>
          </div>
          <button
            onClick={onClose}
            title="Close (Esc)"
            aria-label="Close"
            style={{
              appearance: 'none', border: 'none', background: 'transparent',
              color: 'var(--ink-3)', cursor: 'pointer', padding: 4,
            }}
          >
            <IcX />
          </button>
        </div>

        <div style={{ overflowY: 'auto', flex: 1, margin: '0 -24px', padding: '0 24px' }}>
          {files.map((f, i) => (
            <DupeFileCard
              key={f.id}
              file={f}
              isKept={f.id === keptId}
              suggested={i === 0}
              onKeep={() => setKeptId(f.id)}
              onDelete={() => onRequestDelete(f)}
            />
          ))}
        </div>

        <div
          style={{
            marginTop: 18, paddingTop: 14,
            borderTop: '1px solid var(--line)',
            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
            gap: 12,
            fontSize: 12, color: 'var(--ink-3)',
          }}
        >
          <span style={{ flex: 1, minWidth: 0 }}>Ranked by quality, then source. Click “Keep” on a different copy to override.</span>
          <button
            onClick={onClose}
            style={{
              padding: '9px 14px', borderRadius: 8,
              background: 'var(--glass-2)', color: 'var(--ink)',
              border: '1px solid var(--line)',
              fontSize: 13, fontWeight: 500, cursor: 'pointer', flexShrink: 0,
            }}
          >
            Cancel
          </button>
          <button
            onClick={() => onBulkDelete(losers)}
            disabled={losers.length === 0}
            title="Delete every copy except the one marked Keep — in a single confirmation."
            style={{
              padding: '9px 16px', borderRadius: 8,
              background: losers.length ? 'var(--conf-low)' : 'var(--conf-low-24)',
              color: 'var(--ink)', border: 'none',
              fontSize: 13, fontWeight: 600,
              cursor: losers.length ? 'pointer' : 'not-allowed',
              opacity: losers.length ? 1 : 0.55,
              display: 'inline-flex', alignItems: 'center', gap: 6, flexShrink: 0,
            }}
          >
            <IcTrash /> Delete other {losers.length}
          </button>
        </div>
      </div>
    </div>
  );
}

interface DupeFileCardProps {
  file: LibFile;
  /** This is the copy currently chosen to keep (default = the suggested one). */
  isKept: boolean;
  /** This is the top-ranked copy (shown as "Suggested" even when not kept). */
  suggested: boolean;
  onKeep: () => void;
  onDelete: () => void;
}

function DupeFileCard({ file, isKept, suggested, onKeep, onDelete }: DupeFileCardProps) {
  return (
    <div
      style={{
        padding: '12px 14px',
        borderRadius: 'var(--r-md)',
        background: isKept ? 'var(--accent-soft)' : 'var(--glass)',
        border: isKept
          ? '1px solid var(--accent-line)'
          : '1px solid var(--line)',
        marginBottom: 10,
        display: 'flex', gap: 12, alignItems: 'flex-start',
      }}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
          {isKept ? (
            <span
              style={{
                fontSize: 10, fontWeight: 700, letterSpacing: '0.05em',
                textTransform: 'uppercase', padding: '3px 7px',
                borderRadius: 4,
                background: 'var(--accent-soft)', color: 'var(--conf-high)',
              }}
            >
              Keeping
            </span>
          ) : (
            <span
              style={{
                fontSize: 10, fontWeight: 600, letterSpacing: '0.05em',
                textTransform: 'uppercase', padding: '3px 7px',
                borderRadius: 4,
                background: 'var(--glass-2)', color: 'var(--ink-3)',
              }}
            >
              Will delete
            </span>
          )}
          {suggested && !isKept ? (
            <span style={{ fontSize: 10, fontWeight: 600, color: 'var(--conf-high)' }}>
              · best quality
            </span>
          ) : null}
        </div>
        <div
          className="mono"
          style={{
            fontSize: 13, color: 'var(--ink)', wordBreak: 'break-all',
            marginBottom: 4, lineHeight: 1.4,
          }}
        >
          {file.filename}
        </div>
        <div
          className="mono"
          style={{ fontSize: 11, color: 'var(--ink-3)', marginBottom: 8, wordBreak: 'break-all' }}
        >
          {file.folder}
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
          {(() => { const q = inferQuality(file); return q ? <Chip>{q}</Chip> : null; })()}
          {(() => { const s = inferSource(file); return s ? <Chip>{s}</Chip> : null; })()}
          {file.codec ? <Chip>{file.codec}</Chip> : null}
          {file.hdr ? <Chip>{file.hdr}</Chip> : null}
          {file.channels ? <Chip>{file.channels}</Chip> : null}
          {file.audio?.[0] ? <Chip>{file.audio[0]}</Chip> : null}
          {(() => { const a = audioLangChip(file); return a ? <Chip>{a}</Chip> : null; })()}
          {(() => { const s = subLangChip(file); return s ? <Chip>{s}</Chip> : null; })()}
          {file.releaseGroup ? <Chip accent>[{file.releaseGroup}]</Chip> : null}
        </div>
      </div>
      <div style={{ flexShrink: 0, display: 'flex', flexDirection: 'column', gap: 8, alignItems: 'stretch' }}>
        {isKept ? (
          <span
            style={{
              padding: '8px 12px', borderRadius: 8,
              background: 'var(--accent-soft)',
              color: 'var(--conf-high)',
              border: '1px solid var(--accent-line)',
              fontSize: 12, fontWeight: 600,
              display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 6,
            }}
          >
            ✓ Kept
          </span>
        ) : (
          <>
            <button
              onClick={onKeep}
              title="Keep this copy instead (the others will be deleted)"
              style={{
                appearance: 'none',
                padding: '8px 12px', borderRadius: 8,
                background: 'var(--glass-2)',
                color: 'var(--ink)',
                border: '1px solid var(--line)',
                fontSize: 12, fontWeight: 600, cursor: 'pointer',
                display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 6,
              }}
            >
              Keep this
            </button>
            <button
              onClick={onDelete}
              title="Delete just this one file from disk (irreversible)"
              style={{
                appearance: 'none',
                padding: '8px 12px', borderRadius: 8,
                background: 'var(--conf-low-16)',
                color: 'var(--conf-low)',
                border: '1px solid var(--conf-low-32)',
                fontSize: 12, fontWeight: 600, cursor: 'pointer',
                display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 6,
              }}
            >
              <IcTrash /> Delete
            </button>
          </>
        )}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Delete-confirm modal — second guard against the irreversible action.
// Backend ALSO requires ?confirm=true so a curl can't bypass this.
// ─────────────────────────────────────────────────────────────────────

interface DeleteConfirmModalProps {
  file: LibFile;
  onCancel: () => void;
  onConfirm: () => void;
}

export function DeleteConfirmModal({ file, onCancel, onConfirm }: DeleteConfirmModalProps) {
  const [acknowledged, setAcknowledged] = useState(false);
  // Escape cancels — destructive prompts must be dismissible by keyboard.
  // Capture + stopPropagation so it dismisses THIS modal only, not the
  // parent popup behind it.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      e.stopPropagation();
      onCancel();
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [onCancel]);
  return (
    <div
      onClick={onCancel}
      style={{
        position: 'fixed', inset: 0,
        // Solid dark overlay with blur so the popup behind doesn't bleed through.
        background: 'var(--panel-75)',
        backdropFilter: 'blur(6px)',
        WebkitBackdropFilter: 'blur(6px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        zIndex: 11000,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          // Opaque card, not glassy — this is a destructive prompt, it
          // needs to dominate. Solid var(--panel) reads above the popup behind.
          background: 'var(--panel)',
          color: 'var(--ink)',
          borderRadius: 'var(--r-lg)',
          padding: 28,
          maxWidth: 540,
          width: '90%',
          border: '1px solid var(--conf-low-32)',
          boxShadow: '0 24px 60px var(--scrim-60), 0 0 0 1px var(--conf-low-16)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 14 }}>
          <span
            style={{
              display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
              width: 36, height: 36, borderRadius: 8,
              background: 'var(--conf-low-bg)', color: 'var(--conf-low)',
            }}
          >
            <IcAlertTri />
          </span>
          <h3 style={{ margin: 0, fontSize: 17, color: 'var(--ink)', fontWeight: 600 }}>
            Delete this file from disk?
          </h3>
        </div>
        <p style={{ color: 'var(--ink-2)', fontSize: 13, margin: '0 0 12px 0' }}>
          The .mkv will be permanently removed from your filesystem. This action
          cannot be undone.
        </p>
        <div
          className="mono"
          style={{
            fontSize: 12,
            padding: '10px 12px',
            borderRadius: 8,
            background: 'var(--scrim-30)',
            border: '1px solid var(--line)',
            color: 'var(--ink-2)',
            wordBreak: 'break-all',
            marginBottom: 18,
            lineHeight: 1.5,
          }}
        >
          {file.folder ? <span style={{ color: 'var(--ink-3)' }}>{file.folder}\</span> : null}
          <span style={{ color: 'var(--ink)', fontWeight: 600 }}>{file.filename}</span>
        </div>
        <label
          style={{
            display: 'flex', alignItems: 'center', gap: 10,
            fontSize: 13, color: 'var(--ink-2)',
            marginBottom: 20, cursor: 'pointer',
            padding: '8px 10px', borderRadius: 6,
            background: 'var(--surface-1)',
          }}
        >
          <input
            type="checkbox"
            checked={acknowledged}
            onChange={(e) => setAcknowledged(e.target.checked)}
            style={{ accentColor: 'var(--conf-low)', width: 16, height: 16 }}
          />
          <span>I understand this is irreversible</span>
        </label>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
          <button
            onClick={onCancel}
            style={{
              padding: '9px 16px', borderRadius: 8,
              background: 'var(--glass-2)', color: 'var(--ink)',
              border: '1px solid var(--line)',
              fontSize: 13, fontWeight: 500, cursor: 'pointer',
            }}
          >
            Cancel
          </button>
          <button
            disabled={!acknowledged}
            onClick={onConfirm}
            style={{
              padding: '9px 16px', borderRadius: 8,
              background: acknowledged ? 'var(--conf-low)' : 'var(--conf-low-24)',
              color: 'var(--ink)',
              border: 'none',
              fontSize: 13, fontWeight: 600,
              cursor: acknowledged ? 'pointer' : 'not-allowed',
              opacity: acknowledged ? 1 : 0.55,
              display: 'inline-flex', alignItems: 'center', gap: 6,
            }}
          >
            <IcTrash /> Delete from disk
          </button>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Bulk delete-confirm — the "keep best, delete the rest" path. Shows the
// exact files that will be removed (so it's never a blind mass-delete) and
// requires one acknowledgement, replacing N separate delete+confirm cycles.
// ─────────────────────────────────────────────────────────────────────

interface BulkDeleteConfirmModalProps {
  files: LibFile[];
  /** How many copies are being KEPT (one best per duplicated episode). */
  keepCount: number;
  /** How many episodes (or tracks, for music) this spans — summary line. */
  epCount: number;
  /** Noun for the spanned unit — "episode" for TV/anime, "track" for music. */
  noun?: string;
  /** Override the default "Delete N duplicate files?" headline (e.g. the music
   *  cross-album "Delete N duplicate singles?" flow). */
  headline?: string;
  /** Override the default "keeping the best copy …" body when the kept copies
   *  live elsewhere (cross-album dupes keep the album versions in other cards). */
  detail?: ReactNode;
  onCancel: () => void;
  onConfirm: () => void;
}

export function BulkDeleteConfirmModal({ files, keepCount, epCount, noun = 'episode', headline, detail, onCancel, onConfirm }: BulkDeleteConfirmModalProps) {
  const [acknowledged, setAcknowledged] = useState(false);
  const n = files.length;
  // Escape cancels — destructive prompts must be dismissible by keyboard.
  // Capture + stopPropagation so it dismisses THIS modal only, not the
  // parent popup behind it.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      e.stopPropagation();
      onCancel();
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [onCancel]);
  return (
    <div
      onClick={onCancel}
      style={{
        position: 'fixed', inset: 0,
        background: 'var(--panel-75)',
        backdropFilter: 'blur(6px)', WebkitBackdropFilter: 'blur(6px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        zIndex: 11000,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: 'var(--panel)', color: 'var(--ink)',
          borderRadius: 'var(--r-lg)', padding: 28, maxWidth: 620, width: '92%',
          maxHeight: '82vh', display: 'flex', flexDirection: 'column',
          border: '1px solid var(--conf-low-32)',
          boxShadow: '0 24px 60px var(--scrim-60), 0 0 0 1px var(--conf-low-16)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
          <span
            style={{
              display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
              width: 36, height: 36, borderRadius: 8,
              background: 'var(--conf-low-bg)', color: 'var(--conf-low)',
            }}
          >
            <IcTrash />
          </span>
          <h3 style={{ margin: 0, fontSize: 17, color: 'var(--ink)', fontWeight: 600 }}>
            {headline ?? <>Delete {n} duplicate file{n === 1 ? '' : 's'}?</>}
          </h3>
        </div>
        <p style={{ color: 'var(--ink-2)', fontSize: 13, margin: '0 0 14px 0', lineHeight: 1.5 }}>
          {detail ?? <>
            {epCount > 1
              ? <>Keeping the best copy of each of <strong>{keepCount}</strong> {noun}{keepCount === 1 ? '' : 's'} and removing the other <strong>{n}</strong> file{n === 1 ? '' : 's'} from disk. </>
              : <>Keeping the copy you chose and removing the other <strong>{n}</strong> file{n === 1 ? '' : 's'} from disk. </>}
            This cannot be undone.
          </>}
        </p>
        <div
          style={{
            overflowY: 'auto', flex: '0 1 auto', maxHeight: '34vh',
            margin: '0 0 16px 0', padding: '4px 0',
            borderTop: '1px solid var(--line)', borderBottom: '1px solid var(--line)',
          }}
        >
          {files.map(f => (
            <div
              key={f.id}
              className="mono"
              style={{
                fontSize: 12, padding: '7px 2px', lineHeight: 1.4,
                wordBreak: 'break-all', borderBottom: '1px solid var(--glass)',
              }}
            >
              <span style={{ color: 'var(--ink)', fontWeight: 600 }}>{f.filename}</span>
              {(() => { const q = inferQuality(f); return q ? <span style={{ color: 'var(--ink-3)', marginLeft: 8 }}>{q}</span> : null; })()}
              {f.releaseGroup ? <span style={{ color: 'var(--brand-a)', marginLeft: 6 }}>[{f.releaseGroup}]</span> : null}
            </div>
          ))}
        </div>
        <label
          style={{
            display: 'flex', alignItems: 'center', gap: 10,
            fontSize: 13, color: 'var(--ink-2)', marginBottom: 18, cursor: 'pointer',
            padding: '8px 10px', borderRadius: 6, background: 'var(--surface-1)',
          }}
        >
          <input
            type="checkbox"
            checked={acknowledged}
            onChange={(e) => setAcknowledged(e.target.checked)}
            style={{ accentColor: 'var(--conf-low)', width: 16, height: 16 }}
          />
          <span>I understand this permanently deletes {n} file{n === 1 ? '' : 's'}</span>
        </label>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
          <button
            onClick={onCancel}
            style={{
              padding: '9px 16px', borderRadius: 8,
              background: 'var(--glass-2)', color: 'var(--ink)',
              border: '1px solid var(--line)', fontSize: 13, fontWeight: 500, cursor: 'pointer',
            }}
          >
            Cancel
          </button>
          <button
            disabled={!acknowledged}
            onClick={onConfirm}
            style={{
              padding: '9px 16px', borderRadius: 8,
              background: acknowledged ? 'var(--conf-low)' : 'var(--conf-low-24)',
              color: 'var(--ink)', border: 'none', fontSize: 13, fontWeight: 600,
              cursor: acknowledged ? 'pointer' : 'not-allowed',
              opacity: acknowledged ? 1 : 0.55,
              display: 'inline-flex', alignItems: 'center', gap: 6,
            }}
          >
            <IcTrash /> Delete {n} file{n === 1 ? '' : 's'}
          </button>
        </div>
      </div>
    </div>
  );
}
