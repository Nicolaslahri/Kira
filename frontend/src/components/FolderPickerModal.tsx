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

  const CTL_BTN = 'inline-flex shrink-0 items-center gap-1.5 rounded-xl border border-line bg-glass px-3 py-2 text-[13px] font-medium text-ink-muted transition-colors hover:bg-glass-2 hover:text-ink disabled:opacity-40 [&_svg]:size-[14px]';

  return (
    <Modal
      title="Choose a folder"
      sub={cwd || 'Select a drive to begin'}
      onClose={onClose}
      size="lg"
      footer={
        <>
          <span className="min-w-0 flex-1 truncate font-mono text-[12.5px] text-ink-soft">{cwd || '—'}</span>
          <div className="flex gap-2">
            <button className="rounded-xl border border-line bg-glass px-4 py-2 text-[13px] font-medium text-ink-muted transition-colors hover:bg-glass-2 hover:text-ink" onClick={onClose}>Cancel</button>
            <button
              className="inline-flex items-center gap-1.5 rounded-xl px-4 py-2 text-[13px] font-semibold text-white transition-transform active:translate-y-px disabled:opacity-40 [&_svg]:size-[14px]"
              style={{ background: 'linear-gradient(135deg, var(--brand-a), var(--brand-b))', boxShadow: '0 8px 22px -10px rgba(229,75,186,0.6)' }}
              disabled={!cwd}
              onClick={() => { onPick(cwd); onClose(); }}
            >
              <IcCheck /> Use this folder
            </button>
          </div>
        </>
      }
    >
      {/* Path bar */}
      <div className="mb-3 flex items-center gap-2">
        <button className={CTL_BTN} disabled={parent === null} onClick={() => parent !== null && void load(parent)}>← Up</button>
        <input
          className="min-w-0 flex-1 rounded-xl border border-line bg-glass px-3.5 py-2 font-mono text-[12.5px] text-ink outline-none transition-colors placeholder:text-ink-faint focus:border-accent-line focus:bg-glass-2"
          value={cwd}
          onChange={e => setCwd(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') void load(cwd); }}
          placeholder="Type a path and press Enter…"
          spellCheck={false}
        />
        <button className={CTL_BTN} onClick={() => void load(cwd)} disabled={loading}>
          {loading ? <IcSpin /> : <IcArrowRight />} Go
        </button>
      </div>

      {error ? (
        <div className="mb-3 flex items-start gap-2 rounded-xl border border-[rgba(255,91,110,0.25)] bg-[var(--conf-low-bg)] px-3 py-2.5 text-[12.5px] text-ink-muted [&_svg]:mt-0.5 [&_svg]:size-4 [&_svg]:shrink-0 [&_svg]:text-conf-low">
          <IcAlertTri /><span>{error}</span>
        </div>
      ) : null}

      {/* Folder list */}
      <div className="max-h-[360px] overflow-y-auto rounded-xl border border-line bg-black/20 [scrollbar-width:thin]">
        {entries.length === 0 && !loading ? (
          <div className="px-5 py-8 text-center text-[13px] text-ink-soft">{cwd ? 'No subfolders here.' : 'Loading drives…'}</div>
        ) : null}
        {entries.map((e) => (
          <button
            key={e.path}
            className="group flex w-full items-center gap-2.5 border-b border-line/60 px-3.5 py-2.5 text-left transition-colors last:border-b-0 hover:bg-glass-2"
            onDoubleClick={() => void load(e.path)}
            onClick={() => setCwd(e.path)}
          >
            <IcFolder style={{ width: 15, height: 15 }} className="shrink-0 text-accent" />
            <span className="flex-1 truncate font-mono text-[13px] text-ink-muted group-hover:text-ink">{e.name}</span>
            {e.file_count != null ? (
              <span className="shrink-0 text-[11px] text-ink-faint">{e.file_count.toLocaleString()} items</span>
            ) : (
              <span className="shrink-0 text-[11px] text-conf-low">locked</span>
            )}
          </button>
        ))}
      </div>

      <div className="mt-2.5 text-[11.5px] text-ink-soft">
        Tip: click to preview, double-click to enter. Hit “Use this folder” when you’re in the right place.
      </div>
    </Modal>
  );
}
