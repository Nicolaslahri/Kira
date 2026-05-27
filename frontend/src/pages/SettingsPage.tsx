import { useEffect, useState } from 'react';
import type { AppState, ToastData } from '../lib/types';
import { IcFolder, IcTrash } from '../lib/icons';
import { Segmented } from '../components/ui';
import { ProviderBlock, NamingTemplateTabs } from '../components/settings-blocks';
import { FolderPickerModal } from '../components/FolderPickerModal';
import { api, type ApiProvider } from '../lib/api';

// F-05 / F-06: derive a ProviderBlock status from the live /providers info.
// Encodes the three real states we care about:
//   - not implemented yet   → "Coming soon" (grey)
//   - implemented + no key  → "Not configured" (grey)
//   - implemented + has key → "Connected" (green)
// Special-cased: AniDB always reports "Rate-limited" once implemented
// because we want the warning surfaced even when keys are set.
type BlockStatus = 'connected' | 'warning' | 'error' | 'disabled' | 'coming-soon' | 'not-configured';
function deriveProviderStatus(info: ApiProvider | undefined, key: string): BlockStatus {
  if (!info) return 'not-configured';
  if (!info.implemented) return 'coming-soon';
  if (key === 'anidb') return 'warning'; // rate-limit caveat always visible
  if (!info.configured) return 'not-configured';
  return 'connected';
}

interface Props {
  pushToast: (t: Omit<ToastData, 'id'>) => void;
  state: AppState;
}

// Real provider test handler — returns a callback that hits the backend.
function makeTester(slug: string, pushToast: Props['pushToast'], displayName: string) {
  return async () => {
    try {
      const res = await api.testProvider(slug);
      if (res.ok) {
        pushToast({ title: `${displayName} verified`, sub: `${res.latency_ms ?? '—'} ms`, kind: 'success' });
      } else {
        pushToast({ title: `${displayName} test failed`, sub: res.detail ?? undefined, kind: 'error' });
      }
    } catch (e) {
      pushToast({ title: `${displayName} test failed`, sub: (e as Error).message, kind: 'error' });
    }
  };
}

// Pull a string out of the loosely-typed settings dict, falling back to ''.
function strSetting(s: Record<string, unknown>, key: string): string {
  const v = s[key];
  if (typeof v === 'string') return v;
  if (v && typeof v === 'object' && 'masked' in v) {
    // Bootstrapped from .env — show a masked placeholder.
    const tail = (v as { tail?: string }).tail ?? '';
    return tail ? `•••• •••• •••• ${tail}` : '••••';
  }
  return '';
}

export function SettingsPage({ pushToast }: Props) {
  const [section, setSection] = useState('connections');
  const [profile, setProfile] = useState('Plex');
  const [defaultOp, setDefaultOp] = useState('hardlink');
  const [autoApprove, setAutoApprove] = useState(true);
  const [autoThreshold, setAutoThreshold] = useState(95);
  const [highT, setHighT] = useState(85);
  const [midT, setMidT] = useState(50);
  // All backend settings as a flat dict — read-through for provider keys etc.
  const [rawSettings, setRawSettings] = useState<Record<string, unknown>>({});
  const [loaded, setLoaded] = useState(false);
  // F-05 / F-06: live provider catalog from /providers, keyed by slug.
  // Drives Connected / Not configured / Coming soon labels per block.
  const [providers, setProviders] = useState<Record<string, ApiProvider>>({});

  // Hydrate from backend on mount.
  useEffect(() => {
    api.getSettings()
      .then(s => {
        setRawSettings(s);
        if (typeof s['naming.profile'] === 'string') setProfile(s['naming.profile'] as string);
        if (typeof s['rename.default_op'] === 'string') setDefaultOp(s['rename.default_op'] as string);
        if (typeof s['matching.auto_approve'] === 'boolean') setAutoApprove(s['matching.auto_approve'] as boolean);
        if (typeof s['matching.auto_threshold'] === 'number') setAutoThreshold(s['matching.auto_threshold'] as number);
        if (typeof s['matching.high_threshold'] === 'number') setHighT(s['matching.high_threshold'] as number);
        if (typeof s['matching.mid_threshold'] === 'number') setMidT(s['matching.mid_threshold'] as number);
      })
      .catch(() => { /* backend down — keep defaults */ })
      .finally(() => setLoaded(true));
    // Pull live provider catalog in parallel so block statuses are correct.
    api.getProviders()
      .then(list => {
        const map: Record<string, ApiProvider> = {};
        for (const p of list) map[p.key] = p;
        setProviders(map);
      })
      .catch(() => { /* keep empty — blocks fall back to "Not configured" */ });
  }, []);

  // Single-key save that pushes a toast on failure but stays quiet on success.
  const saveKey = (key: string) => (value: string | boolean) => {
    void api.putSettings({ [key]: value })
      .then(() => setRawSettings(s => ({ ...s, [key]: value })))
      .catch(e => pushToast({ title: 'Save failed', sub: (e as Error).message, kind: 'error' }));
  };

  // Auto-save whenever any persisted setting changes (after initial hydrate).
  useEffect(() => {
    if (!loaded) return;
    const handle = setTimeout(() => {
      void api.putSettings({
        'naming.profile': profile,
        'rename.default_op': defaultOp,
        'matching.auto_approve': autoApprove,
        'matching.auto_threshold': autoThreshold,
        'matching.high_threshold': highT,
        'matching.mid_threshold': midT,
      }).then(() => {
        // Let App reload its rename-modal defaults from the saved values.
        window.dispatchEvent(new CustomEvent('kira:settings-saved'));
      }).catch(() => { /* swallow — user can retry by changing again */ });
    }, 500);
    return () => clearTimeout(handle);
  }, [loaded, profile, defaultOp, autoApprove, autoThreshold, highT, midT]);

  const sections = [
    { key: 'connections', label: 'Connections' },
    { key: 'paths', label: 'Paths & folders' },
    { key: 'naming', label: 'Naming & format' },
    { key: 'confidence', label: 'Confidence thresholds' },
    { key: 'advanced', label: 'Advanced' },
  ];

  // Settings deliberately renders even while !loaded — the defaults
  // (Profile=Plex, Op=hardlink, etc.) are the documented out-of-box
  // values, which IS what a brand-new user has. The snap-on-fetch is
  // only visible to users with non-default settings, and only for
  // ~200ms — not worth blocking the entire page. If this becomes a
  // recurring annoyance, the right fix is per-field skeletons; the
  // full-page spinner was too heavy-handed.

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Settings</h1>
          <p className="page-sub">Configure Kira · changes save automatically</p>
        </div>
      </div>

      <div className="settings-grid">
        <div className="settings-nav">
          {sections.map(s => (
            <button key={s.key} className={`settings-nav-item ${section === s.key ? 'active' : ''}`} onClick={() => setSection(s.key)}>{s.label}</button>
          ))}
        </div>

        <div className="card">
          {section === 'connections' && (
            <div className="card-pad" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div className="text-xs text-muted" style={{ padding: '0 2px' }}>
                Kira uses several providers for metadata. Each is configured independently.
              </div>

              <ProviderBlock
                providerKey="TMDB" defaultOpen status={deriveProviderStatus(providers['tmdb'], 'tmdb')}
                fields={[
                  // F-05: when /providers says TMDB is configured but the
                  // raw settings field is empty (server doesn't echo the
                  // key back for security), swap the placeholder to a
                  // "key already saved" indicator so the user knows not
                  // to retype.
                  { kind: 'text', label: 'API key', value: strSetting(rawSettings, 'providers.tmdb.api_key'),
                    placeholder: providers['tmdb']?.configured && !strSetting(rawSettings, 'providers.tmdb.api_key')
                      ? '••••••••••••••••  (key saved — enter a new one to replace)'
                      : 'paste 32-char key from themoviedb.org',
                    mono: true,
                    desc: 'Get a free key at themoviedb.org → Settings → API.',
                    onSave: saveKey('providers.tmdb.api_key') },
                  { kind: 'select', label: 'Language', value: strSetting(rawSettings, 'providers.tmdb.language') || 'English (US)',
                    options: ['English (US)', 'English (UK)', 'Français', 'Deutsch', '日本語'],
                    onSave: saveKey('providers.tmdb.language') },
                ]}
                onTest={makeTester('tmdb', pushToast, 'TMDB')}
              />

              <ProviderBlock
                providerKey="TVDB" status={deriveProviderStatus(providers['tvdb'], 'tvdb')}
                fields={[
                  { kind: 'text', label: 'API key (v4)', value: strSetting(rawSettings, 'providers.tvdb.api_key'),
                    placeholder: providers['tvdb']?.configured && !strSetting(rawSettings, 'providers.tvdb.api_key')
                      ? '••••••••••••••••  (key saved — enter a new one to replace)'
                      : 'paste your TVDB v4 API key',
                    mono: true,
                    desc: 'Required for TV and as a secondary anime source. Sign up at thetvdb.com/api-information.',
                    onSave: saveKey('providers.tvdb.api_key') },
                  { kind: 'select', label: 'Order', value: strSetting(rawSettings, 'providers.tvdb.order') || 'Aired order',
                    options: ['Aired order', 'DVD order', 'Absolute order'],
                    onSave: saveKey('providers.tvdb.order') },
                ]}
                onTest={makeTester('tvdb', pushToast, 'TVDB')}
              />

              <ProviderBlock
                providerKey="AniDB" status={deriveProviderStatus(providers['anidb'], 'anidb')}
                fields={[
                  { kind: 'text', label: 'Client name', value: strSetting(rawSettings, 'providers.anidb.client') || 'kira', mono: true,
                    desc: 'Your registered AniDB HTTP-API client name (see anidb.net/software/add_program). Title-only search works without registration; cover art requires it.',
                    onSave: saveKey('providers.anidb.client') },
                  { kind: 'text', label: 'Client version', value: strSetting(rawSettings, 'providers.anidb.clientver') || '1', mono: true,
                    desc: 'The numeric version AniDB approved for your client. Pictures stay blank until this matches a real registration.',
                    onSave: saveKey('providers.anidb.clientver') },
                  { kind: 'text', label: 'Username (optional)', value: strSetting(rawSettings, 'providers.anidb.username'),
                    placeholder: 'only needed for mylist features (future)',
                    onSave: saveKey('providers.anidb.username') },
                  { kind: 'password', label: 'Password (optional)', value: strSetting(rawSettings, 'providers.anidb.password'),
                    placeholder: '••••••••',
                    onSave: saveKey('providers.anidb.password') },
                ]}
                warning="AniDB strictly rate-limits to ~1 request per 4 seconds. Title-only search (the matcher) works out-of-the-box; cover art requires a registered AniDB client name + version."
                onTest={makeTester('anidb', pushToast, 'AniDB')}
                bannedUntil={providers['anidb']?.banned_until}
                fallbackChain={providers['anidb']?.fallback_chain ?? ['tvdb', 'tmdb']}
              />

              <ProviderBlock
                providerKey="MusicBrainz" status={deriveProviderStatus(providers['musicbrainz'], 'musicbrainz')}
                fields={[
                  { kind: 'text', label: 'User-Agent string',
                    value: strSetting(rawSettings, 'providers.musicbrainz.user_agent') || 'Kira/0.4.1 (self-hosted)',
                    mono: true,
                    desc: 'MusicBrainz requires a unique User-Agent identifying your application and contact info.',
                    onSave: saveKey('providers.musicbrainz.user_agent') },
                  { kind: 'text', label: 'Contact (URL or email)',
                    value: strSetting(rawSettings, 'providers.musicbrainz.contact'),
                    placeholder: 'your-email@example.com', mono: true,
                    onSave: saveKey('providers.musicbrainz.contact') },
                ]}
                onTest={makeTester('musicbrainz', pushToast, 'MusicBrainz')}
              />

              <ProviderBlock
                providerKey="AcoustID" status={deriveProviderStatus(providers['acoustid'], 'acoustid')}
                fields={[
                  { kind: 'text', label: 'API key', value: strSetting(rawSettings, 'providers.acoustid.api_key'),
                    placeholder: 'paste your acoustid.org API key', mono: true,
                    desc: 'Used for audio-fingerprint matching when filename metadata is missing. Get a free key at acoustid.org.',
                    onSave: saveKey('providers.acoustid.api_key') },
                  { kind: 'toggle', label: 'Auto-fingerprint untagged files',
                    desc: 'When enabled, music files without ID3 tags are fingerprinted and matched automatically.',
                    onSave: saveKey('providers.acoustid.auto_fingerprint') },
                ]}
                onTest={makeTester('acoustid', pushToast, 'AcoustID')}
              />

              <div className="field-row" style={{ padding: '20px 0 4px' }}>
                <div className="field-label-block">
                  <div className="label">Webhook URL</div>
                  <div className="desc">Notify an external service after every successful rename. Optional.</div>
                </div>
                <div className="field-control">
                  <input
                    className="input mono"
                    placeholder="https://your-server.com/kira/webhook"
                    defaultValue={strSetting(rawSettings, 'webhook.url')}
                    onBlur={e => {
                      // Toast on save so the user knows the value persisted.
                      // Previously this saved silently — user had no idea if
                      // the value stuck.
                      const v = e.target.value;
                      void api.putSettings({ 'webhook.url': v })
                        .then(() => {
                          setRawSettings(s => ({ ...s, 'webhook.url': v }));
                          pushToast({ title: v ? 'Webhook saved' : 'Webhook cleared', kind: 'success' });
                        })
                        .catch(err => pushToast({ title: 'Save failed', sub: (err as Error).message, kind: 'error' }));
                    }}
                  />
                </div>
              </div>
            </div>
          )}

          {section === 'paths' && (
            <PathsSection rawSettings={rawSettings} setRawSettings={setRawSettings} saveKey={saveKey} pushToast={pushToast} />
          )}

          {section === 'naming' && (
            <div className="card-pad">
              <div className="field-row">
                <div className="field-label-block">
                  <div className="label">Default naming profile</div>
                  <div className="desc">New scans will use this profile unless overridden in the rename preview.</div>
                </div>
                <div className="field-control">
                  <Segmented value={profile} onChange={setProfile} options={[
                    { value: 'Plex', label: 'Plex' },
                    { value: 'Jellyfin', label: 'Jellyfin' },
                    { value: 'Kodi', label: 'Kodi' },
                    { value: 'Custom', label: 'Custom' },
                  ]} />
                </div>
              </div>

              <div className="field-row" style={{ display: 'block', padding: '20px 0' }}>
                <div className="field-label-block" style={{ marginBottom: 14 }}>
                  <div className="label">Templates per media type</div>
                  <div className="desc">Each media type uses a different set of tokens. Click a tab to edit.</div>
                </div>
                <NamingTemplateTabs profile={profile} />
              </div>

              <div className="field-row">
                <div className="field-label-block">
                  <div className="label">Rename mode</div>
                  <div className="desc">
                    <strong>In-place</strong> keeps each file inside its current containing folder (only the file
                    + show + season folder names change to match the template). <strong>Move to library</strong>
                    builds a fresh tree under your Media root from scratch.
                  </div>
                </div>
                <div className="field-control" style={{ display: 'flex', flexDirection: 'column', gap: 10, alignItems: 'stretch' }}>
                  <Segmented
                    value={(() => {
                      const v = rawSettings['rename.mode'];
                      if (typeof v === 'string') return v;
                      if (v && typeof v === 'object' && 'value' in v) return String((v as { value: string }).value);
                      return 'in-place';
                    })()}
                    onChange={v => saveKey('rename.mode')(v)}
                    options={[
                      { value: 'in-place', label: 'In-place rename' },
                      { value: 'move-to-library', label: 'Move to library' },
                    ]}
                  />
                </div>
              </div>

              <div className="field-row">
                <div className="field-label-block">
                  <div className="label">Default file operation</div>
                  <div className="desc">What Kira does with the original file when it lands the renamed copy at the target path.</div>
                </div>
                <div className="field-control" style={{ display: 'flex', flexDirection: 'column', gap: 10, alignItems: 'stretch' }}>
                  <Segmented value={defaultOp} onChange={setDefaultOp} options={[
                    { value: 'move', label: 'Move' },
                    { value: 'copy', label: 'Copy' },
                    { value: 'symlink', label: 'Symlink' },
                    { value: 'hardlink', label: 'Hardlink' },
                  ]} />
                  {/* Per-option explainer — keeps the four options uniform
                      in the segmented control while still teaching the
                      user what each one DOES. The text swaps based on the
                      current selection so the user sees the relevant
                      explanation without hovering or guessing. */}
                  <FileOpExplainer op={defaultOp} />
                </div>
              </div>

              {/* Move-specific: clean up empty source folders. Only
                  relevant when Default operation is Move (the other ops
                  don't remove the source file, so the source folder is
                  never empty to begin with). Show always for discoverability
                  but mention the gating. */}
              <div className="field-row">
                <div className="field-label-block">
                  <div className="label">Clean up empty source folders</div>
                  <div className="desc">
                    After a Move, also rmdir any source-side folders that are now empty.
                    Walks up the chain (Season folder, Show folder) and removes each empty
                    directory it finds. Stops at your Media root. Safe: rmdir refuses
                    non-empty directories. Has no effect on Copy / Hardlink / Symlink
                    (those don't empty the source).
                  </div>
                </div>
                <div className="field-control">
                  <label className="flex items-center gap-2" style={{ cursor: 'pointer' }}>
                    <input
                      type="checkbox"
                      checked={(() => {
                        const v = rawSettings['rename.cleanup_empty_source_dirs'];
                        if (typeof v === 'boolean') return v;
                        if (v && typeof v === 'object' && 'value' in v) return Boolean((v as { value: boolean }).value);
                        return true; // default-on
                      })()}
                      onChange={e => saveKey('rename.cleanup_empty_source_dirs')(e.target.checked)}
                    />
                    <span style={{ fontSize: 13 }}>Auto-remove empty folders after Move</span>
                  </label>
                </div>
              </div>
            </div>
          )}

          {section === 'confidence' && (
            <div className="card-pad">
              <div className="field-row">
                <div className="field-label-block">
                  <div className="label">Auto-approve threshold</div>
                  <div className="desc">When enabled, matches scoring above this will be approved automatically without review.</div>
                </div>
                <div className="field-control" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                  <label className="flex items-center gap-2" style={{ cursor: 'pointer' }}>
                    <input type="checkbox" checked={autoApprove} onChange={() => setAutoApprove(!autoApprove)} style={{ accentColor: 'var(--accent)' }} />
                    <span className="text-sm">Enable auto-approve</span>
                  </label>
                  <div className="threshold">
                    <span className="text-sm text-muted">Threshold</span>
                    <input type="range" min={80} max={100} value={autoThreshold} onChange={e => setAutoThreshold(+e.target.value)} style={{ accentColor: 'var(--accent)' }} />
                    <span className="text-sm font-medium" style={{ color: 'var(--conf-high)' }}>≥ {autoThreshold}%</span>
                  </div>
                </div>
              </div>
              <div className="field-row">
                <div className="field-label-block">
                  <div className="label">Confidence colors</div>
                  <div className="desc">Where the cutoffs for the green / yellow / red badges sit.</div>
                </div>
                <div className="field-control" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                  {/* Clamp High slider to >= Med + 5 so the user can't
                      invert the thresholds. Similarly Med <= High - 5.
                      Without this, dragging High below Med collapses the
                      Mid bucket entirely (anything between would be both
                      high and not-mid) → meaningless colour assignment. */}
                  <div className="threshold">
                    <span className="badge badge-high"><span className="dot" />High</span>
                    <input
                      type="range"
                      min={Math.max(60, midT + 5)}
                      max={100}
                      value={highT}
                      onChange={e => {
                        const v = Math.max(midT + 5, +e.target.value);
                        setHighT(Math.min(100, v));
                      }}
                      style={{ accentColor: 'var(--accent)' }}
                    />
                    <span className="text-sm font-mono" style={{ color: 'var(--conf-high)' }}>≥ {highT}%</span>
                  </div>
                  <div className="threshold">
                    <span className="badge badge-mid"><span className="dot" />Med</span>
                    <input
                      type="range"
                      min={20}
                      max={Math.min(80, highT - 5)}
                      value={midT}
                      onChange={e => {
                        const v = Math.min(highT - 5, +e.target.value);
                        setMidT(Math.max(20, v));
                      }}
                      style={{ accentColor: 'var(--conf-mid)' }}
                    />
                    <span className="text-sm font-mono" style={{ color: 'var(--conf-mid)' }}>≥ {midT}%</span>
                  </div>
                  <div className="threshold">
                    <span className="badge badge-low"><span className="dot" />Low</span>
                    <div />
                    <span className="text-sm font-mono" style={{ color: 'var(--conf-low)' }}>&lt; {midT}%</span>
                  </div>
                </div>
              </div>
            </div>
          )}

          {section === 'advanced' && (
            <AdvancedSection rawSettings={rawSettings} saveKey={saveKey} pushToast={pushToast} />
          )}
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Paths section — folder picker + watch folder list
// ─────────────────────────────────────────────────────────────────────

type SaveKeyFn = (key: string) => (value: string | boolean) => void;

function PathsSection({
  rawSettings,
  setRawSettings,
  saveKey,
  pushToast,
}: {
  rawSettings: Record<string, unknown>;
  /** Direct setter — needed for multi-value writes (like the
   *  watch-folders array) that don't fit `saveKey`'s single-value shape.
   *  Without it, save succeeds on the backend but the local list never
   *  updates and the UI looks broken ("watch folder added" toast but
   *  empty list). */
  setRawSettings: React.Dispatch<React.SetStateAction<Record<string, unknown>>>;
  saveKey: SaveKeyFn;
  pushToast: Props['pushToast'];
}) {
  // Picker state — 'library' for Media root, 'watch' for watch folders,
  // and one entry per media-type destination override (target-{type}).
  type PickerFor = 'library' | 'watch' | 'target-movie' | 'target-tv' | 'target-anime' | 'target-music';
  const [picker, setPicker] = useState<{ for: PickerFor; initial: string } | null>(null);
  // Default to '/media' — canonical Docker mount point. Previous default
  // was 'Z:\\media' which was author-machine-specific.
  const libraryRoot = strSetting(rawSettings, 'paths.library_root') || '/media';

  // Per-media-type destination overrides. When set, rename targets for
  // that type land there directly (NOT `library_root / Type-subfolder`).
  // Empty string = "fall back to library_root / SUBFOLDER[type]".
  const targetMovie = strSetting(rawSettings, 'paths.targets.movie');
  const targetTv = strSetting(rawSettings, 'paths.targets.tv');
  const targetAnime = strSetting(rawSettings, 'paths.targets.anime');
  const targetMusic = strSetting(rawSettings, 'paths.targets.music');
  // For the placeholder, show what the path WOULD be if the user didn't
  // override — keeps the inheritance explicit instead of "field is blank,
  // good luck guessing".
  const defaultTarget = (sub: string) => `${libraryRoot.replace(/[\\/]+$/, '')}/${sub}  (default)`;

  // watch_folders stored as JSON array; fall back to single root.
  const watchFolders: string[] = (() => {
    const v = rawSettings['paths.watch_folders'];
    if (Array.isArray(v)) return v.filter((x): x is string => typeof x === 'string');
    return [];
  })();

  const setWatch = async (next: string[]) => {
    try {
      await api.putSettings({ 'paths.watch_folders': next });
      // CRUCIAL: mirror the save into local rawSettings so the next
      // render reflects the new list. Without this, the toast says
      // "saved" but the displayed list re-derives from stale state
      // and shows the empty placeholder — looking like nothing happened.
      setRawSettings(s => ({ ...s, 'paths.watch_folders': next }));
      pushToast({ title: 'Watch folders saved', kind: 'success' });
    } catch (e) {
      pushToast({ title: 'Save failed', sub: (e as Error).message, kind: 'error' });
    }
  };

  return (
    <div className="card-pad">
      <div className="field-row">
        <div className="field-label-block">
          <div className="label">Media root</div>
          <div className="desc">Where Kira renames into. (Move/Hardlink target.)</div>
        </div>
        <div className="field-control">
          <div className="flex gap-2">
            <input
              className="input mono"
              value={libraryRoot}
              onChange={e => saveKey('paths.library_root')(e.target.value)}
              style={{ flex: 1 }}
            />
            <button
              className="btn"
              onClick={() => setPicker({ for: 'library', initial: libraryRoot })}
            >
              <IcFolder style={{ width: 14, height: 14 }} /> Browse
            </button>
          </div>
        </div>
      </div>

      <div className="field-row">
        <div className="field-label-block">
          <div className="label">Per-type destinations</div>
          <div className="desc">
            Optional. Override the destination folder for each media type independently.
            When blank, renames land at <span className="text-mono">Media root / Movies</span>,{' '}
            <span className="text-mono">Media root / TV</span>, <span className="text-mono">Media root / Anime</span>,{' '}
            or <span className="text-mono">Media root / Music</span> respectively (the legacy default).
            Set per-type to route each kind to its own drive or pre-existing library folder.
          </div>
        </div>
        <div className="field-control" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {([
            { label: 'Movies', sub: 'Movies', value: targetMovie, key: 'paths.targets.movie' as const, pickerFor: 'target-movie' as const },
            { label: 'TV',     sub: 'TV',     value: targetTv,    key: 'paths.targets.tv'    as const, pickerFor: 'target-tv'    as const },
            { label: 'Anime',  sub: 'Anime',  value: targetAnime, key: 'paths.targets.anime' as const, pickerFor: 'target-anime' as const },
            { label: 'Music',  sub: 'Music',  value: targetMusic, key: 'paths.targets.music' as const, pickerFor: 'target-music' as const },
          ]).map(row => (
            <div key={row.key} className="flex items-center gap-2">
              <span className="text-xs" style={{ width: 64, color: 'var(--ink-2)', fontWeight: 500 }}>{row.label}</span>
              <input
                className="input mono"
                value={row.value}
                placeholder={defaultTarget(row.sub)}
                onChange={e => saveKey(row.key)(e.target.value)}
                style={{ flex: 1 }}
              />
              <button
                className="btn"
                onClick={() => setPicker({ for: row.pickerFor, initial: row.value || libraryRoot })}
                title={`Browse for ${row.label} destination`}
              >
                <IcFolder style={{ width: 14, height: 14 }} /> Browse
              </button>
              {row.value ? (
                <button
                  className="btn"
                  onClick={() => saveKey(row.key)('')}
                  title="Clear override — fall back to default location"
                >
                  Clear
                </button>
              ) : null}
            </div>
          ))}
        </div>
      </div>

      <div className="field-row">
        <div className="field-label-block">
          <div className="label">Watch folders</div>
          <div className="desc">Folders Kira will scan when you click "Scan now".</div>
        </div>
        <div className="field-control">
          {watchFolders.length === 0 ? (
            <div className="text-xs text-muted" style={{ marginBottom: 8 }}>
              No watch folders yet — add one below.
            </div>
          ) : null}
          {watchFolders.map((p, i) => (
            <div key={i} className="flex items-center gap-2" style={{ marginBottom: 8, background: 'var(--glass)', border: '1px solid var(--line)', padding: '8px 12px', borderRadius: 10 }}>
              <IcFolder style={{ width: 14, height: 14 }} />
              <span className="text-mono text-sm">{p}</span>
              <button
                className="act ml-auto"
                title="Remove"
                onClick={() => void setWatch(watchFolders.filter((_, j) => j !== i))}
              >
                <IcTrash />
              </button>
            </div>
          ))}
          <button
            className="btn btn-sm"
            onClick={() => setPicker({ for: 'watch', initial: libraryRoot })}
          >
            <IcFolder style={{ width: 14, height: 14 }} /> Add watch folder
          </button>
        </div>
      </div>

      {picker ? (
        <FolderPickerModal
          initialPath={picker.initial}
          onPick={path => {
            if (picker.for === 'library') {
              saveKey('paths.library_root')(path);
            } else if (picker.for === 'watch') {
              void setWatch(Array.from(new Set([...watchFolders, path])));
            } else if (picker.for === 'target-movie') {
              saveKey('paths.targets.movie')(path);
            } else if (picker.for === 'target-tv') {
              saveKey('paths.targets.tv')(path);
            } else if (picker.for === 'target-anime') {
              saveKey('paths.targets.anime')(path);
            } else if (picker.for === 'target-music') {
              saveKey('paths.targets.music')(path);
            }
          }}
          onClose={() => setPicker(null)}
        />
      ) : null}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Advanced section — retention, concurrency, danger zone
// ─────────────────────────────────────────────────────────────────────

function AdvancedSection({
  rawSettings,
  saveKey,
  pushToast,
}: {
  rawSettings: Record<string, unknown>;
  saveKey: SaveKeyFn;
  pushToast: Props['pushToast'];
}) {
  // Retention round-trip: settings stores `0` for "forever" (no upper
  // bound) and N for "N days". The select uses the string 'forever' for
  // the unbounded option. Map 0 → 'forever' on read so the select
  // doesn't fall back to its first option after a save.
  const retentionRaw = rawSettings['history.retention_days'];
  const retention =
    retentionRaw === 0 || retentionRaw === '0' ? 'forever' :
    typeof retentionRaw === 'number' ? String(retentionRaw) :
    typeof retentionRaw === 'string' && retentionRaw !== '' ? retentionRaw :
    '30';
  const concurrency = typeof rawSettings['rename.concurrency'] === 'number'
    ? rawSettings['rename.concurrency'] as number
    : 4;
  const [confirming, setConfirming] = useState(false);
  const [confirmText, setConfirmText] = useState('');

  const doReset = async () => {
    try {
      await api.resetDatabase();
      pushToast({ title: 'Database reset', sub: 'All files, matches, and history removed.', kind: 'success' });
      setConfirming(false);
      // Force a reload so the rest of the UI resets too.
      setTimeout(() => window.location.reload(), 600);
    } catch (e) {
      pushToast({ title: 'Reset failed', sub: (e as Error).message, kind: 'error' });
    }
  };

  return (
    <div className="card-pad">
      <div className="field-row">
        <div className="field-label-block">
          <div className="label">History retention</div>
          <div className="desc">How long to keep the rename log for undo. Older entries are pruned daily.</div>
        </div>
        <div className="field-control">
          <select
            className="input"
            value={retention}
            onChange={e => saveKey('history.retention_days')(e.target.value === 'forever' ? '0' : e.target.value)}
          >
            <option value="30">30 days</option>
            <option value="90">90 days</option>
            <option value="365">1 year</option>
            <option value="forever">Forever</option>
          </select>
        </div>
      </div>
      <div className="field-row">
        <div className="field-label-block">
          <div className="label">Concurrent file operations</div>
          <div className="desc">More is faster but heavier on disk I/O.</div>
        </div>
        <div className="field-control">
          {/* Controlled input — uses `value` not `defaultValue` so the
              displayed number reflects the persisted setting (especially
              important if the save fails and we need to roll back). */}
          <input
            className="input"
            type="number"
            min={1}
            max={32}
            value={concurrency}
            onChange={e => {
              const n = parseInt(e.target.value, 10);
              if (Number.isFinite(n) && n >= 1 && n <= 32) {
                saveKey('rename.concurrency')(String(n));
              }
            }}
            style={{ width: 100 }}
          />
        </div>
      </div>
      <div className="field-row">
        <div className="field-label-block">
          <div className="label" style={{ color: 'var(--conf-low)' }}>Danger zone</div>
          <div className="desc">Reset Kira's database. Renames already on disk are NOT undone.</div>
        </div>
        <div className="field-control">
          {!confirming ? (
            <button className="btn btn-danger" onClick={() => setConfirming(true)}>
              <IcTrash /> Reset database...
            </button>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <div className="text-sm" style={{ color: 'var(--conf-low)' }}>
                Type <span className="kbd">DELETE</span> to confirm. This cannot be undone.
              </div>
              <div className="flex gap-2">
                <input
                  className="input mono"
                  style={{ flex: 1 }}
                  value={confirmText}
                  onChange={e => setConfirmText(e.target.value)}
                  placeholder="DELETE"
                  autoFocus
                />
                <button
                  className="btn btn-danger"
                  disabled={confirmText !== 'DELETE'}
                  onClick={() => void doReset()}
                >
                  Reset now
                </button>
                <button className="btn btn-ghost" onClick={() => { setConfirming(false); setConfirmText(''); }}>
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// FileOpExplainer — per-option help text under the file-op segmented
// control. The four options are functionally distinct on disk; this
// block teaches the user the trade-off they're picking. Color-coded
// "Best for" / "Watch out for" tags so the impact is scannable.
// ─────────────────────────────────────────────────────────────────────
const FILE_OP_INFO: Record<string, {
  title: string; what: string; bestFor: string; caveat: string;
}> = {
  move: {
    title: 'Move',
    what: 'The original file is RELOCATED to the new path. Nothing remains at the source.',
    bestFor: 'You want files renamed in place and don\'t care about the old folder layout.',
    caveat: 'If the target is on a DIFFERENT drive, the OS has to copy then delete — slower than a hardlink and uses extra disk during the copy.',
  },
  copy: {
    title: 'Copy',
    what: 'A full byte-for-byte duplicate is written to the new path. The original stays put.',
    bestFor: 'You want a separate, independent copy at the destination (e.g. burning to external drive).',
    caveat: 'Doubles disk usage. Almost always you want Hardlink instead — same end result, no extra space.',
  },
  symlink: {
    title: 'Symlink',
    what: 'A small "shortcut" file is written at the new path pointing back to the original.',
    bestFor: 'Cross-drive setups where Hardlink isn\'t possible (hardlinks can\'t span filesystems).',
    caveat: 'Some media servers (Plex on Windows in particular) can\'t follow symlinks reliably. If the source file moves or gets deleted, the symlink breaks.',
  },
  hardlink: {
    title: 'Hardlink',
    what: 'A SECOND filesystem entry pointing at the same disk bytes. The original ALSO stays. Looks like a duplicate in Explorer but takes zero extra disk space.',
    bestFor: 'Most users. Plex / Jellyfin libraries especially — gives you an organized layer at the rename target without disrupting the source folder layout (e.g. so your torrent client can keep seeding).',
    caveat: 'Hardlinks can\'t cross drives. If your media root is on a different drive than the source files, this falls back to Move at run time. Both filesystem entries must be on the same partition.',
  },
};

function FileOpExplainer({ op }: { op: string }) {
  const info = FILE_OP_INFO[op] ?? FILE_OP_INFO.hardlink;
  return (
    <div style={{
      padding: '12px 14px',
      borderRadius: 8,
      background: 'rgba(255,255,255,0.025)',
      border: '1px solid var(--line)',
      fontSize: 12,
      lineHeight: 1.5,
      display: 'flex',
      flexDirection: 'column',
      gap: 8,
    }}>
      <div style={{ color: 'var(--ink-1)' }}>
        <strong style={{ color: 'var(--ink)' }}>{info.title} — </strong>{info.what}
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        <div>
          <span style={{
            display: 'inline-block', padding: '1px 6px', borderRadius: 4,
            fontSize: 10, fontWeight: 600, letterSpacing: '0.04em', textTransform: 'uppercase',
            background: 'rgba(40,217,160,0.14)', color: 'var(--conf-high)', marginRight: 8,
          }}>Best for</span>
          <span style={{ color: 'var(--ink-2)' }}>{info.bestFor}</span>
        </div>
        <div>
          <span style={{
            display: 'inline-block', padding: '1px 6px', borderRadius: 4,
            fontSize: 10, fontWeight: 600, letterSpacing: '0.04em', textTransform: 'uppercase',
            background: 'rgba(248,191,96,0.14)', color: '#ffd591', marginRight: 8,
          }}>Watch out</span>
          <span style={{ color: 'var(--ink-2)' }}>{info.caveat}</span>
        </div>
      </div>
    </div>
  );
}
