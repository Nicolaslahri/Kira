import { createPortal } from 'react-dom';
import { IcAlertTri, IcDownload } from '../../lib/icons';

// ─────────────────────────────────────────────────────────────────────
// ForceImportConfirmModal — preview-then-commit confirmation for the
// Force Import button on a stuck Sonarr queue entry. Shows EXACTLY where
// Sonarr plans to write each file + lets the user pick Copy (safer, source
// stays) or Move (Sonarr's default, source gone). Portaled to body so it
// escapes the popup's transformed stacking context.
// ─────────────────────────────────────────────────────────────────────

interface ForceImportConfirmModalProps {
  candidates: Array<{
    source_path: string;
    destination_root: string;
    series_title: string;
    series_id: number;
    episode_labels: string[];
    episode_ids: number[];
    quality_name: string | null;
    release_group: string | null;
    rejection_reasons: string[];
  }>;
  importMode: 'Copy' | 'Move';
  onChangeMode: (m: 'Copy' | 'Move') => void;
  onCancel: () => void;
  onConfirm: () => void;
  confirming: boolean;
}

export function ForceImportConfirmModal({
  candidates, importMode, onChangeMode, onCancel, onConfirm, confirming,
}: ForceImportConfirmModalProps) {
  const importableCount = candidates.filter(c => c.rejection_reasons.length === 0).length;
  const blockedCount = candidates.length - importableCount;

  // Portal to document.body so the modal escapes the DownloadProgressRow's
  // stacking context. The row sits deep inside cx-shell → cx-main →
  // cx-body → cx-col → cx-row; the popup's transform on cx-shell creates
  // a stacking context that traps any descendant regardless of z-index.
  // Portaling to body lets the modal stack above the entire popup like
  // the Dupes / Delete modals do (those are rendered at the popup root
  // and so naturally escape — same goal, different mechanism).
  return createPortal(
    <div
      onClick={onCancel}
      style={{
        position: 'fixed', inset: 0,
        background: 'rgba(7, 6, 12, 0.78)',
        backdropFilter: 'blur(6px)',
        WebkitBackdropFilter: 'blur(6px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        zIndex: 12000,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: '#14121b',
          color: 'var(--ink)',
          borderRadius: 14,
          padding: 24,
          maxWidth: 760,
          width: '92%',
          maxHeight: '86vh',
          overflow: 'hidden',
          display: 'flex', flexDirection: 'column',
          border: '1px solid var(--line-strong)',
          boxShadow: '0 24px 60px rgba(0, 0, 0, 0.6)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 14, marginBottom: 16 }}>
          <span style={{
            display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
            width: 40, height: 40, borderRadius: 8,
            background: 'rgba(255, 201, 74, 0.15)',
            color: 'var(--conf-mid)',
            flexShrink: 0,
          }}>
            <IcAlertTri />
          </span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <h3 style={{ margin: 0, fontSize: 17, fontWeight: 600 }}>
              Confirm manual import
            </h3>
            <div style={{ fontSize: 13, color: 'var(--ink-2)', marginTop: 4, lineHeight: 1.45 }}>
              Sonarr will write {importableCount} file
              {importableCount === 1 ? '' : 's'} to your library using
              the mapping below.
              {blockedCount > 0 ? (
                <span style={{ color: 'var(--conf-low)', marginLeft: 6 }}>
                  {blockedCount} file{blockedCount === 1 ? '' : 's'} blocked by Sonarr rejections.
                </span>
              ) : null}
            </div>
          </div>
        </div>

        <div style={{ overflowY: 'auto', flex: 1, margin: '0 -8px', padding: '0 8px' }}>
          {candidates.map((c, i) => (
            <div
              key={i}
              style={{
                marginBottom: 12,
                padding: '12px 14px',
                borderRadius: 10,
                background: c.rejection_reasons.length > 0
                  ? 'rgba(255, 91, 110, 0.06)'
                  : 'rgba(40, 217, 160, 0.04)',
                border: '1px solid ' + (c.rejection_reasons.length > 0
                  ? 'rgba(255, 91, 110, 0.30)'
                  : 'rgba(40, 217, 160, 0.24)'),
              }}
            >
              <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink-1)', marginBottom: 8 }}>
                {c.series_title}
                {c.episode_labels.length > 0 ? (
                  <span style={{ color: 'var(--ink-3)', fontWeight: 500, marginLeft: 8 }}>
                    · {c.episode_labels.join(', ')}
                  </span>
                ) : null}
                {c.quality_name ? (
                  <span style={{
                    fontSize: 11, padding: '2px 8px', borderRadius: 4,
                    background: 'var(--glass-2)', color: 'var(--ink-2)',
                    marginLeft: 8, fontWeight: 500,
                  }}>{c.quality_name}</span>
                ) : null}
              </div>

              <div style={{ fontSize: 11.5, color: 'var(--ink-3)', marginBottom: 6 }}>
                <strong style={{ color: 'var(--ink-2)' }}>From:</strong>
                <code style={{
                  marginLeft: 6, color: 'var(--ink-2)', wordBreak: 'break-all',
                }}>{c.source_path}</code>
              </div>
              <div style={{ fontSize: 11.5, color: 'var(--ink-3)' }}>
                <strong style={{ color: 'var(--ink-2)' }}>To:</strong>
                <code style={{
                  marginLeft: 6,
                  color: c.rejection_reasons.length > 0 ? 'var(--ink-4)' : 'var(--conf-high)',
                  wordBreak: 'break-all',
                }}>{c.destination_root}</code>
                <span style={{ color: 'var(--ink-4)', marginLeft: 6, fontSize: 11 }}>
                  (under Sonarr's series folder; exact filename via Sonarr's template)
                </span>
              </div>

              {c.rejection_reasons.length > 0 ? (
                <div style={{ marginTop: 8, fontSize: 11, color: 'var(--conf-low)' }}>
                  <strong>Sonarr rejected:</strong>{' '}
                  {c.rejection_reasons.join(' · ')}
                </div>
              ) : null}
            </div>
          ))}
        </div>

        {/* Import-mode selector — defaults to Copy (safer). Move
            cleans up the source but if the move partially fails the
            source can vanish. We document the trade-off inline so
            the user makes an informed choice. */}
        <div
          style={{
            marginTop: 16,
            padding: '12px 14px',
            borderRadius: 8,
            background: 'var(--glass-2)',
            border: '1px solid var(--line)',
            fontSize: 12.5,
            lineHeight: 1.5,
          }}
        >
          <div style={{ fontWeight: 600, marginBottom: 8, color: 'var(--ink-1)' }}>
            Import mode
          </div>
          <label style={{ display: 'flex', alignItems: 'flex-start', gap: 8, marginBottom: 8, cursor: 'pointer' }}>
            <input
              type="radio"
              name="import-mode"
              checked={importMode === 'Copy'}
              onChange={() => onChangeMode('Copy')}
              style={{ accentColor: 'var(--conf-high)', marginTop: 3 }}
            />
            <div>
              <div style={{ color: 'var(--ink-1)', fontWeight: 500 }}>
                Copy <span style={{ color: 'var(--conf-high)', fontSize: 11 }}>(recommended)</span>
              </div>
              <div style={{ color: 'var(--ink-3)', fontSize: 11.5 }}>
                Source file stays in the download client's folder. Safer:
                if the import fails for any reason, the source survives.
                Costs disk space until your download client's retention rule
                cleans it up.
              </div>
            </div>
          </label>
          <label style={{ display: 'flex', alignItems: 'flex-start', gap: 8, cursor: 'pointer' }}>
            <input
              type="radio"
              name="import-mode"
              checked={importMode === 'Move'}
              onChange={() => onChangeMode('Move')}
              style={{ accentColor: 'var(--conf-mid)', marginTop: 3 }}
            />
            <div>
              <div style={{ color: 'var(--ink-1)', fontWeight: 500 }}>
                Move <span style={{ color: 'var(--conf-mid)', fontSize: 11 }}>(deletes source)</span>
              </div>
              <div style={{ color: 'var(--ink-3)', fontSize: 11.5 }}>
                Sonarr deletes the source after the move. Saves disk space
                but a partial-move failure on cross-device transfers can lose
                the source while leaving the destination incomplete. The
                AoT S01E05/E06 incident happened with this mode.
              </div>
            </div>
          </label>
        </div>

        <div
          style={{
            marginTop: 18, paddingTop: 14,
            borderTop: '1px solid var(--line)',
            display: 'flex', justifyContent: 'flex-end', gap: 10,
          }}
        >
          <button
            onClick={onCancel}
            disabled={confirming}
            style={{
              padding: '9px 16px', borderRadius: 8,
              background: 'var(--glass-2)', color: 'var(--ink)',
              border: '1px solid var(--line)',
              fontSize: 13, fontWeight: 500,
              cursor: confirming ? 'wait' : 'pointer',
            }}
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={confirming || importableCount === 0}
            style={{
              padding: '9px 18px', borderRadius: 8,
              background: importableCount > 0 ? 'var(--conf-high)' : 'rgba(40, 217, 160, 0.25)',
              color: importableCount > 0 ? '#022b1c' : 'var(--ink-3)',
              border: 'none',
              fontSize: 13, fontWeight: 600,
              cursor: confirming
                ? 'wait'
                : (importableCount === 0 ? 'not-allowed' : 'pointer'),
              opacity: importableCount === 0 ? 0.55 : 1,
              display: 'inline-flex', alignItems: 'center', gap: 6,
            }}
          >
            <IcDownload />
            {confirming
              ? 'Importing…'
              : importableCount === 0
                ? 'Nothing to import'
                : `Import ${importableCount} file${importableCount === 1 ? '' : 's'} (${importMode})`}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
