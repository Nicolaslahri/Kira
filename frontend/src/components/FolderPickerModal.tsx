import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import { IcCheck, IcFolder, IcArrowRight, IcAlertTri, IcSpin } from '../lib/icons';
import { Modal } from './ui';

interface Entry {
  name: string;
  path: string;
  is_dir: boolean;
  file_count: number | null;
}

export function FolderPickerModal({
  initialPath = '',
  onPick,
  onClose,
}: {
  initialPath?: string;
  onPick: (path: string) => void;
  onClose: () => void;
}) {
  const [cwd, setCwd] = useState(initialPath);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [parent, setParent] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async (path: string) => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.listFolders(path);
      setCwd(res.path);
      setParent(res.parent);
      setEntries(res.entries);
    } catch (e) {
      setError((e as Error).message);
      setEntries([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(initialPath); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);

  return (
    <Modal
      title="Choose a folder"
      sub={cwd || 'Select a drive to begin'}
      onClose={onClose}
      size="lg"
      footer={
        <>
          <span className="text-mono text-sm" style={{ color: 'var(--ink-3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1, minWidth: 0 }}>
            {cwd || '—'}
          </span>
          <div className="right">
            <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
            <button className="btn btn-primary" disabled={!cwd} onClick={() => { onPick(cwd); onClose(); }}>
              <IcCheck /> Use this folder
            </button>
          </div>
        </>
      }
    >
      <div className="flex items-center gap-2" style={{ marginBottom: 12 }}>
        <button
          className="btn btn-sm"
          disabled={parent === null}
          onClick={() => parent !== null && void load(parent)}
        >
          ← Up
        </button>
        <input
          className="input mono"
          style={{ flex: 1 }}
          value={cwd}
          onChange={e => setCwd(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') void load(cwd); }}
          placeholder="Type a path and press Enter…"
        />
        <button className="btn btn-sm" onClick={() => void load(cwd)} disabled={loading}>
          {loading ? <IcSpin /> : <IcArrowRight />} Go
        </button>
      </div>

      {error ? (
        <div className="onboarding-state error" style={{ marginBottom: 12 }}>
          <IcAlertTri /><span>{error}</span>
        </div>
      ) : null}

      <div style={{
        border: '1px solid var(--line)', borderRadius: 10,
        maxHeight: 360, overflowY: 'auto', background: 'rgba(0,0,0,0.18)',
      }}>
        {entries.length === 0 && !loading ? (
          <div style={{ padding: 20, textAlign: 'center', color: 'var(--ink-3)', fontSize: 13 }}>
            {cwd ? 'No subfolders here.' : 'Loading drives...'}
          </div>
        ) : null}
        {entries.map(e => (
          <button
            key={e.path}
            className="nav-item"
            style={{ margin: 0, padding: '8px 12px', borderRadius: 0, width: '100%' }}
            onDoubleClick={() => void load(e.path)}
            onClick={() => setCwd(e.path)}
          >
            <IcFolder style={{ width: 14, height: 14 }} />
            <span className="text-mono text-sm" style={{ flex: 1, textAlign: 'left' }}>{e.name}</span>
            {e.file_count != null ? (
              <span className="text-xs text-muted">{e.file_count.toLocaleString()} items</span>
            ) : (
              <span className="text-xs" style={{ color: 'var(--conf-low)' }}>locked</span>
            )}
          </button>
        ))}
      </div>

      <div className="text-xs text-muted" style={{ marginTop: 8 }}>
        Tip: click to preview, double-click to enter. Hit "Use this folder" when you're in the right place.
      </div>
    </Modal>
  );
}
