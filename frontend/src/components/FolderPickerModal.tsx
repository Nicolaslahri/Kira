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
  const [confirming, setConfirming] = useState(false);

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

  // Validate the current path exists before confirming. `cwd` can be a raw,
  // never-loaded value (typed without pressing Go, or an initialPath that
  // 404'd), so confirm it against the server rather than accepting it blindly.
  const confirm = async () => {
    setConfirming(true);
    setError(null);
    try {
      const res = await api.listFolders(cwd);
      onPick(res.path);   // canonical path the server resolved
      onClose();
    } catch (e) {
      setError((e as Error).message || 'That folder can’t be used — pick another.');
    } finally {
      setConfirming(false);
    }
  };

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
              style={{ background: 'linear-gradient(135deg, var(--brand-a), var(--brand-b))', boxShadow: '0 8px 22px -10px var(--brand-50)' }}
              disabled={!cwd || confirming}
              onClick={() => void confirm()}
            >
              {confirming ? <><IcSpin /> Checking…</> : <><IcCheck /> Use this folder</>}
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
        <div className="mb-3 flex items-start gap-2 rounded-xl border border-[var(--conf-low-24)] bg-[var(--conf-low-bg)] px-3 py-2.5 text-[12.5px] text-ink-muted [&_svg]:mt-0.5 [&_svg]:size-4 [&_svg]:shrink-0 [&_svg]:text-conf-low">
          <IcAlertTri /><span>{error}</span>
        </div>
      ) : null}

      {/* Folder list */}
      <div className="max-h-[360px] overflow-y-auto rounded-xl border border-line bg-black/20 [scrollbar-width:thin]">
        {entries.length === 0 && !loading ? (
          <div className="px-5 py-8 text-center text-[13px] text-ink-soft">{cwd ? 'No subfolders here.' : 'Loading drives…'}</div>
        ) : null}
        {entries.map((e) => {
          // file_count == null means Kira couldn't read the folder — it's
          // permission-locked, so selecting/entering it would only fail.
          // Disable it rather than letting the user pick a dead end.
          const locked = e.file_count == null;
          return (
            <button
              key={e.path}
              className="group flex w-full items-center gap-2.5 border-b border-line/60 px-3.5 py-2.5 text-left transition-colors last:border-b-0 hover:bg-glass-2 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-transparent"
              disabled={locked}
              aria-label={locked ? `${e.name} (locked — no access)` : `Open ${e.name}`}
              onDoubleClick={() => { if (!locked) void load(e.path); }}
              onClick={() => { if (!locked) setCwd(e.path); }}
            >
              <IcFolder style={{ width: 15, height: 15 }} className="shrink-0 text-accent" />
              <span className="flex-1 truncate font-mono text-[13px] text-ink-muted group-hover:text-ink">{e.name}</span>
              {!locked ? (
                <span className="shrink-0 text-[11px] text-ink-faint">{e.file_count!.toLocaleString()} items</span>
              ) : (
                <span className="shrink-0 text-[11px] text-conf-low">locked</span>
              )}
            </button>
          );
        })}
      </div>

      <div className="mt-2.5 text-[11.5px] text-ink-soft">
        Tip: click to preview, double-click to enter. Hit “Use this folder” when you’re in the right place.
      </div>
    </Modal>
  );
}
