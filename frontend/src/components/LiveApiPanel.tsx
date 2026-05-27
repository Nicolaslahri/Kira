import { useEffect, useState } from 'react';
import { api, type ApiMediaFile } from '../lib/api';
import { IcCheck, IcAlertTri, IcScan, IcSpin, IcRefresh } from '../lib/icons';

type Health = { ok: boolean; version?: string; error?: string };

function confColor(c: number): string {
  if (c >= 0.85) return 'var(--conf-high)';
  if (c >= 0.50) return 'var(--conf-mid)';
  return 'var(--conf-low)';
}

function typeColor(t: string | null): string {
  if (t === 'music') return '#ffb14a';
  if (t === 'anime') return '#c89bff';
  if (t === 'tv')    return 'var(--info)';
  return 'var(--ink-3)';
}

export function LiveApiPanel() {
  const [health, setHealth] = useState<Health | null>(null);
  const [files, setFiles] = useState<ApiMediaFile[]>([]);
  const [path, setPath] = useState('Z:\\media');
  const [busy, setBusy] = useState(false);
  const [matchBusy, setMatchBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = async () => {
    try {
      const h = await api.health();
      setHealth({ ok: true, version: h.version });
      setFiles(await api.listFiles({ limit: 500 }));
    } catch (e) {
      setHealth({ ok: false, error: (e as Error).message });
    }
  };

  useEffect(() => { void refresh(); }, []);

  const scan = async () => {
    setBusy(true); setError(null);
    try {
      await api.createScan(path);
      setFiles(await api.listFiles({ limit: 500 }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const rematchAll = async () => {
    setMatchBusy(true); setError(null);
    try {
      await api.rematchAll({ limit: 500 });
      setFiles(await api.listFiles({ limit: 500 }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setMatchBusy(false);
    }
  };

  const matched = files.filter(f => f.matches.length > 0).length;
  const stats = {
    movie: files.filter(f => f.media_type === 'movie').length,
    tv:    files.filter(f => f.media_type === 'tv').length,
    anime: files.filter(f => f.media_type === 'anime').length,
    music: files.filter(f => f.media_type === 'music').length,
  };

  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div className="card-head">
        <div>
          <div className="card-title">Live backend</div>
          <div className="card-sub">
            {health == null ? 'Checking…'
              : health.ok
                ? <>
                    <IcCheck style={{ width: 11, height: 11, color: 'var(--conf-high)', display: 'inline', verticalAlign: 'middle' }} />
                    {' '}Connected · v{health.version} · {files.length} files, {matched} matched
                    {' · '}
                    <span style={{ color: 'var(--ink-3)' }}>
                      Movies {stats.movie} · TV {stats.tv} · Anime {stats.anime} · Music {stats.music}
                    </span>
                  </>
                : <>
                    <IcAlertTri style={{ width: 11, height: 11, color: 'var(--conf-low)', display: 'inline', verticalAlign: 'middle' }} />
                    {' '}Backend offline · is uvicorn running on :8000?
                  </>}
          </div>
        </div>
        <div className="flex gap-2">
          <button className="btn btn-sm" onClick={() => void refresh()}>Refresh</button>
          <button className="btn btn-sm" onClick={() => void rematchAll()} disabled={matchBusy || files.length === 0}>
            {matchBusy ? <IcSpin /> : <IcRefresh />} {matchBusy ? 'Matching…' : 'Rematch all'}
          </button>
        </div>
      </div>
      <div className="card-pad" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div className="flex gap-2 items-center">
          <input
            className="input mono"
            style={{ flex: 1 }}
            value={path}
            onChange={e => setPath(e.target.value)}
            placeholder="/media or C:\path\to\media"
          />
          <button className="btn btn-primary" onClick={scan} disabled={busy || !path}>
            {busy ? <IcSpin /> : <IcScan />} {busy ? 'Scanning…' : 'Scan this folder'}
          </button>
        </div>
        {error ? (
          <div className="onboarding-state error"><IcAlertTri /><span>{error}</span></div>
        ) : null}
        {files.length > 0 ? (
          <div style={{
            background: 'rgba(0,0,0,0.22)', border: '1px solid var(--line)',
            borderRadius: 10, padding: '8px 12px',
            fontSize: 11,
            maxHeight: 320, overflowY: 'auto',
          }}>
            {files.slice(0, 100).map(f => {
              const top = f.matches[0];
              const filename = f.file_path.split(/[\\/]/).pop() ?? f.file_path;
              return (
                <div key={f.id} style={{
                  display: 'grid',
                  gridTemplateColumns: 'minmax(0, 1fr) auto auto auto',
                  gap: 12,
                  alignItems: 'center',
                  padding: '4px 0',
                  borderBottom: '1px solid rgba(255,255,255,0.04)',
                }}>
                  <span className="text-mono" style={{
                    color: 'var(--ink-2)',
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  }}>{filename}</span>
                  <span style={{ color: typeColor(f.media_type), whiteSpace: 'nowrap', fontWeight: 500 }}>
                    {f.media_type ?? '—'}
                  </span>
                  <span style={{
                    color: top ? 'var(--ink)' : 'var(--ink-4)',
                    fontStyle: top ? 'normal' : 'italic',
                    whiteSpace: 'nowrap',
                    maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis',
                  }}>
                    {top ? `${top.title}${top.year ? ` (${top.year})` : ''}` : 'no match'}
                  </span>
                  <span style={{
                    color: top ? confColor(top.confidence) : 'var(--ink-4)',
                    fontFamily: 'var(--font-mono)',
                    fontWeight: 600,
                    minWidth: 44,
                    textAlign: 'right',
                  }}>
                    {top ? `${Math.round(top.confidence * 100)}%` : '—'}
                  </span>
                </div>
              );
            })}
            {files.length > 100 ? (
              <div style={{ padding: '6px 0 0', textAlign: 'center', color: 'var(--ink-4)' }}>
                + {files.length - 100} more rows
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}
