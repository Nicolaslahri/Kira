import { useEffect, useRef, useState, type ReactNode } from 'react';
import type { AppState, ToastData } from '../lib/types';
import type { SettingsSection } from '../App';
import { IcFolder, IcTrash, IcEye, IcEyeOff, IcFilm, IcTv, IcAnime, IcMusic, IcScan, IcX, IcPlus, IcCheck, IcAlertTri, IcRefresh, IcTag, IcArrowRight } from '../lib/icons';
import { Select } from '../components/ui';
import { SegmentedControl } from '../components/base/segmented/segmented-control';
import { ProviderCard, NamingTemplateTabs, SETTINGS_CARD, SETTINGS_NESTED, SETTINGS_DIVIDER } from '../components/settings-blocks';
import { BadgeWithDot } from '../components/base/badges/badges';
import { Button } from '../components/base/buttons/button';
import { Input } from '../components/base/input/input';
import { FeaturedIcon } from '../components/base/featured-icons/featured-icon';
import { Toggle } from '../components/base/toggle/toggle';
import { Alert } from '../components/base/alert/alert';
import { IcShieldCheck, IcChevDown, IcSettings, IcLink } from '../lib/icons';
import { FolderPickerModal } from '../components/FolderPickerModal';
import { api, type ApiProvider } from '../lib/api';

// F-05 / F-06: derive a ProviderCard status from the live /providers info.
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
  /** Active sub-section — now driven by the nested sidebar nav (App owns it). */
  section: SettingsSection;
  setSection: (s: SettingsSection) => void;
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

// A labelled settings field: title + optional description, then the control.
function SettingField({ label, desc, children }: { label: ReactNode; desc?: ReactNode; children: ReactNode }) {
  return (
    <div>
      <div className="text-[13.5px] font-medium text-ink">{label}</div>
      {desc ? <div className="mt-0.5 text-[12.5px] leading-relaxed text-ink-muted">{desc}</div> : null}
      <div className="mt-2.5">{children}</div>
    </div>
  );
}

export function SettingsPage({ pushToast, section, setSection }: Props) {
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
  //
  // Bug B fix: optimistic update FIRST. Path inputs (Media root,
  // per-type destinations) are controlled by `rawSettings`. Pre-fix we
  // only mirrored the new value into `rawSettings` AFTER the server PUT
  // resolved — so every keystroke re-rendered the input from the stale
  // persisted value and the typed character appeared not to stick. The
  // workaround the user discovered ("save twice") worked because the
  // second keystroke fired after the first PUT's `.then()` updated
  // `rawSettings`, by which point the input WAS in sync with the local
  // value. Optimistic update collapses the round-trip: typed characters
  // land in `rawSettings` synchronously, the PUT fires in the
  // background, and a failure toast is the only place the staleness
  // is visible (and even then we leave the optimistic value alone so
  // the user can retry without losing what they typed).
  const saveKey = (key: string) => (value: string | number | boolean) => {
    setRawSettings(s => ({ ...s, [key]: value }));
    void api.putSettings({ [key]: value })
      .catch(e => pushToast({ title: 'Save failed', sub: (e as Error).message, kind: 'error' }));
  };

  // Custom naming templates persist as a single JSON object under
  // `naming.custom.Custom` (the shape the backend's _resolve_profile reads at
  // rename time). Edits optimistically update rawSettings so the editor stays
  // in sync, then debounce the PUT so we don't hammer the backend per keystroke.
  const customSaveTimer = useRef<number | undefined>(undefined);
  const saveCustomTemplates = (dict: Record<string, string>) => {
    setRawSettings(s => ({ ...s, 'naming.custom.Custom': dict }));
    if (customSaveTimer.current) window.clearTimeout(customSaveTimer.current);
    customSaveTimer.current = window.setTimeout(() => {
      void api.putSettings({ 'naming.custom.Custom': dict })
        .then(() => window.dispatchEvent(new CustomEvent('kira:settings-saved')))
        .catch(e => pushToast({ title: 'Save failed', sub: (e as Error).message, kind: 'error' }));
    }, 500);
  };
  // Saved custom templates (if any) to seed the editor; undefined → defaults.
  const savedCustom = (() => {
    const v = rawSettings['naming.custom.Custom'];
    return v && typeof v === 'object' && !Array.isArray(v)
      ? (v as Record<string, string>)
      : undefined;
  })();

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

  // Settings deliberately renders even while !loaded — the defaults
  // (Profile=Plex, Op=hardlink, etc.) are the documented out-of-box
  // values, which IS what a brand-new user has. The snap-on-fetch is
  // only visible to users with non-default settings, and only for
  // ~200ms — not worth blocking the entire page. If this becomes a
  // recurring annoyance, the right fix is per-field skeletons; the
  // full-page spinner was too heavy-handed.

  // Connections summary — how many of the metadata providers are wired up.
  // (AniDB reports "Rate-limited" rather than "Connected", so it never counts.)
  const PROVIDER_KEYS = ['tmdb', 'tvdb', 'anidb', 'musicbrainz', 'acoustid'] as const;
  const connectedCount = PROVIDER_KEYS.filter(k => deriveProviderStatus(providers[k], k) === 'connected').length;

  return (
    <div className="page relative">
      {/* Plain page header — matches History/Dashboard (no boxed card). */}
      <div className="page-header">
        <div>
          <h1 className="page-title">Settings</h1>
          <p className="page-sub">Configure Kira · changes save automatically</p>
        </div>
      </div>

      {/* Section nav lives in the global sidebar (nested under Settings); the
          active section is already labelled there, so no in-page section
          header is needed. Content fills the full main column. */}
      <div>
          {/* Content fills the full main column. */}
          <div key={section} className="rounded-2xl border border-line bg-[rgba(255,255,255,0.02)]">
          {section === 'connections' && (
            <div className="flex flex-col gap-5 p-5">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <p className="max-w-xl text-[13px] leading-relaxed text-ink-muted">
                  Kira pulls metadata from these providers. Each is configured independently.
                </p>
                <BadgeWithDot color={connectedCount > 0 ? 'success' : 'gray'}>
                  {connectedCount} of {PROVIDER_KEYS.length} connected
                </BadgeWithDot>
              </div>

              <div className="grid grid-cols-1 items-start gap-3 lg:grid-cols-2">
              <ProviderCard
                providerKey="TMDB" status={deriveProviderStatus(providers['tmdb'], 'tmdb')}
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

              <ProviderCard
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

              <ProviderCard
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

              <ProviderCard
                providerKey="MusicBrainz" status={deriveProviderStatus(providers['musicbrainz'], 'musicbrainz')}
                fields={[
                  { kind: 'text', label: 'User-Agent string',
                    value: strSetting(rawSettings, 'providers.musicbrainz.user_agent') || 'Kira/0.5.0 (self-hosted)',
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

              <ProviderCard
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
              </div>
            </div>
          )}

          {section === 'paths' && (
            <PathsSection rawSettings={rawSettings} setRawSettings={setRawSettings} saveKey={saveKey} pushToast={pushToast} />
          )}

          {section === 'integrations' && (
            <IntegrationsSection rawSettings={rawSettings} setRawSettings={setRawSettings} saveKey={saveKey} pushToast={pushToast} />
          )}

          {section === 'naming' && (
            <div className="flex flex-col gap-4 p-5">
              <p className="text-[13px] leading-relaxed text-ink-muted">
                Choose how Kira names files and lays them out on disk. Changes apply to new scans.
              </p>

              {/* Naming profile + templates */}
              <div className={`p-4 ${SETTINGS_CARD}`}>
                <div className={`flex items-start gap-3 border-b pb-3.5 ${SETTINGS_DIVIDER}`}>
                  <FeaturedIcon size="md" color="gray" icon={<IcTag />} />
                  <div className="min-w-0">
                    <div className="text-[15px] font-semibold text-ink">Naming profile</div>
                    <div className="mt-1 text-[12.5px] leading-relaxed text-ink-muted">New scans use this profile unless overridden in the rename preview.</div>
                  </div>
                </div>
                <div className="mt-4 flex flex-col gap-4">
                  <SegmentedControl
                    value={profile}
                    onChange={setProfile}
                    fullWidth
                    options={[
                      { value: 'Plex', label: 'Plex' },
                      { value: 'Jellyfin', label: 'Jellyfin' },
                      { value: 'Kodi', label: 'Kodi' },
                      { value: 'Custom', label: 'Custom' },
                    ]}
                  />
                  <div className={`border-t pt-4 ${SETTINGS_DIVIDER}`}>
                    <div className="text-[13.5px] font-medium text-ink">Templates per media type</div>
                    <div className="mt-0.5 text-[12.5px] leading-relaxed text-ink-muted">
                      Each media type uses its own tokens. {profile === 'Custom' ? 'Editable — changes autosave.' : 'Pick the Custom profile to edit these.'}
                    </div>
                    <div className="mt-3">
                      <NamingTemplateTabs profile={profile} savedCustom={savedCustom} onSaveCustom={saveCustomTemplates} />
                    </div>
                  </div>
                </div>
              </div>

              {/* File handling */}
              <div className={`p-4 ${SETTINGS_CARD}`}>
                <div className={`flex items-start gap-3 border-b pb-3.5 ${SETTINGS_DIVIDER}`}>
                  <FeaturedIcon size="md" color="gray" icon={<IcRefresh />} />
                  <div className="min-w-0">
                    <div className="text-[15px] font-semibold text-ink">File handling</div>
                    <div className="mt-1 text-[12.5px] leading-relaxed text-ink-muted">How renamed files are placed on disk.</div>
                  </div>
                </div>
                <div className="mt-4 flex flex-col gap-5">
                  <SettingField
                    label="Rename mode"
                    desc={<><strong className="text-ink">In-place</strong> keeps each file in its current folder (only the file / show / season names change). <strong className="text-ink">Move to library</strong> builds a fresh tree under your Media root.</>}
                  >
                    <SegmentedControl
                      fullWidth
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
                  </SettingField>

                  <SettingField
                    label="Default file operation"
                    desc="What Kira does with the original file when it lands the renamed copy at the target path."
                  >
                    <div className="flex flex-col gap-3">
                      <SegmentedControl
                        fullWidth
                        value={defaultOp}
                        onChange={setDefaultOp}
                        options={[
                          { value: 'move', label: 'Move' },
                          { value: 'copy', label: 'Copy' },
                          { value: 'symlink', label: 'Symlink' },
                          { value: 'hardlink', label: 'Hardlink' },
                        ]}
                      />
                      <FileOpExplainer op={defaultOp} />
                    </div>
                  </SettingField>
                </div>
              </div>

              {/* Folder cleanup breadcrumb — full settings live in their own section. */}
              <div className={`p-4 ${SETTINGS_CARD}`}>
                <div className="flex items-center gap-3">
                  <FeaturedIcon size="md" color="gray" icon={<IcTrash />} />
                  <div className="min-w-0 flex-1">
                    <div className="text-[15px] font-semibold text-ink">Folder cleanup</div>
                    <div className="mt-1 text-[12.5px] leading-relaxed text-ink-muted">
                      Empty-folder removal, the Plex / Jellyfin / Kodi artifact sweep, and the deleted-pattern list live in their own section.
                    </div>
                  </div>
                  <Button color="secondary" size="sm" iconTrailing={<IcArrowRight className="size-3.5" />} onClick={() => setSection('cleanup')}>
                    Open
                  </Button>
                </div>
              </div>
            </div>
          )}

          {section === 'cleanup' && (() => {
            // Read both flags. The master toggle defaults TRUE in the UI
            // so first-time users see "yes I want clean folders" out of
            // the box. The artifact-sweep sub-toggle defaults TRUE for
            // the same reason — the whole point of cleanup is leaving
            // the source tidy.
            const masterOn = (() => {
              const v = rawSettings['rename.cleanup_empty_source_dirs'];
              if (typeof v === 'boolean') return v;
              if (v && typeof v === 'object' && 'value' in v) return Boolean((v as { value: boolean }).value);
              return true;
            })();
            const sweepOn = (() => {
              const v = rawSettings['rename.cleanup_media_server_artifacts'];
              if (typeof v === 'boolean') return v;
              if (v && typeof v === 'object' && 'value' in v) return Boolean((v as { value: boolean }).value);
              return true;
            })();
            return (
              <div className="flex flex-col gap-4 p-5">
                <p className="text-[13px] leading-relaxed text-ink-muted">
                  When Kira moves a file into your library the source folder is left behind — often with leftover{' '}
                  <span className="font-mono text-ink">poster.jpg</span> / <span className="font-mono text-ink">tvshow.nfo</span>{' '}
                  files that Plex / Jellyfin / Kodi wrote. Control whether Kira tidies those up.
                </p>

                {/* Cleanup toggles */}
                <div className={`p-4 ${SETTINGS_CARD}`}>
                  <div className={`flex items-start gap-3 border-b pb-3.5 ${SETTINGS_DIVIDER}`}>
                    <FeaturedIcon size="md" color="gray" icon={<IcTrash />} />
                    <div className="min-w-0">
                      <div className="text-[15px] font-semibold text-ink">Source folder cleanup</div>
                      <div className="mt-1 text-[12.5px] leading-relaxed text-ink-muted">Applies after a Move only — Copy / Hardlink / Symlink never empty the source.</div>
                    </div>
                  </div>
                  <div className="mt-4 flex flex-col gap-4">
                    {/* Master toggle */}
                    <div className="flex items-start justify-between gap-4">
                      <div className="min-w-0">
                        <div className="text-[13.5px] font-medium text-ink">Remove empty folders after Move</div>
                        <div className="mt-0.5 text-[12.5px] leading-relaxed text-ink-muted">
                          After a Move, walk up the source's folder chain and <span className="font-mono text-ink">rmdir</span> each level that's now empty. Stops at your Media root — never deletes the library root itself.
                        </div>
                      </div>
                      <Toggle isSelected={masterOn} onChange={() => saveKey('rename.cleanup_empty_source_dirs')(!masterOn)} className="mt-0.5" aria-label="Remove empty folders after Move" />
                    </div>

                    {/* Sub-toggle — artifact sweep, dimmed when master is off. */}
                    <div className={`p-3.5 ${SETTINGS_NESTED} ${masterOn ? '' : 'opacity-50'}`}>
                      <div className="flex items-start justify-between gap-4">
                        <div className="min-w-0">
                          <div className="text-[13.5px] font-medium text-ink">Also delete media-server metadata</div>
                          <div className="mt-0.5 text-[12.5px] leading-relaxed text-ink-muted">
                            Sweep known Plex / Jellyfin / Kodi cache files (posters, banners, NFOs, <span className="font-mono text-ink">.actors/</span>, per-episode thumbnails) so the folder can actually be removed. <strong className="text-ink">Disable</strong> for strict “only touch genuinely empty folders” behavior.
                          </div>
                        </div>
                        <Toggle isSelected={sweepOn} isDisabled={!masterOn} onChange={() => saveKey('rename.cleanup_media_server_artifacts')(!sweepOn)} className="mt-0.5" aria-label="Delete media-server cache files" />
                      </div>
                    </div>
                  </div>
                </div>

                {/* Transparency — exactly what gets swept. */}
                <details className={`group overflow-hidden ${SETTINGS_CARD}`}>
                  <summary className="flex cursor-pointer list-none items-center gap-3 p-4 [&::-webkit-details-marker]:hidden">
                    <FeaturedIcon size="md" color="gray" icon={<IcShieldCheck />} />
                    <div className="min-w-0 flex-1">
                      <div className="text-[15px] font-semibold text-ink">What gets deleted</div>
                      <div className="mt-1 text-[12.5px] leading-relaxed text-ink-muted">Exactly which files Kira sweeps — and what it never touches.</div>
                    </div>
                    <IcChevDown className="size-4 shrink-0 text-ink-soft transition-transform [details[open]_&]:rotate-180" />
                  </summary>
                  <div className={`flex flex-col gap-4 border-t px-4 py-4 ${SETTINGS_DIVIDER}`}>
                    <div>
                      <div className="mb-1.5 text-[12px] font-semibold text-ink">Deleted — exact filenames (case-insensitive)</div>
                      <div className="font-mono text-[11.5px] leading-relaxed text-ink-soft">
                        poster.jpg · banner.jpg · fanart.jpg · clearart.png · clearlogo.png · landscape.jpg · thumb.jpg · logo.jpg · disc.jpg · keyart.jpg · characterart.jpg · folder.jpg · cover.jpg · tvshow.nfo · season.nfo · movie.nfo · show.nfo · album.nfo · artist.nfo
                      </div>
                      <div className="mt-1 text-[11px] text-ink-faint">Both .jpg and .png variants are recognised.</div>
                    </div>
                    <div>
                      <div className="mb-1.5 text-[12px] font-semibold text-ink">Deleted — pattern matches</div>
                      <div className="font-mono text-[11.5px] leading-relaxed text-ink-soft">
                        season01-poster.jpg · season-specials-banner.jpg · Show.S01E01-thumb.jpg · Movie (2023)-poster.jpg · Album-fanart.png · Show.S01E01-fanart-2.jpg · *.tbn (Kodi binary thumbnails)
                      </div>
                    </div>
                    <div>
                      <div className="mb-1.5 text-[12px] font-semibold text-ink">Deleted — directories (recursive)</div>
                      <div className="font-mono text-[11.5px] leading-relaxed text-ink-soft">
                        .actors/ · .metadata/ · extrafanart/ · extrathumbs/ · backdrops/ · metadata/
                      </div>
                    </div>
                    <div>
                      <div className="mb-1.5 text-[12px] font-semibold text-conf-high">Never deleted</div>
                      <div className="text-[12.5px] leading-relaxed text-ink-muted">
                        Your own files (anything not on the lists above) — including <span className="font-mono text-ink">Subs/</span>, <span className="font-mono text-ink">Extras/</span>, <span className="font-mono text-ink">Featurettes/</span>, <span className="font-mono text-ink">Trailers/</span>, <span className="font-mono text-ink">Behind The Scenes/</span>, <span className="font-mono text-ink">Bonus/</span>, and any file not matching the recognised media-server naming conventions. If user content remains in a folder, the cleanup walk stops there — the folder stays.
                      </div>
                    </div>
                  </div>
                </details>
              </div>
            );
          })()}

          {section === 'confidence' && (
            <div className="flex flex-col gap-4 p-5">
              <p className="text-[13px] leading-relaxed text-ink-muted">
                Tune how confident a match must be before Kira trusts it.
              </p>

              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              {/* Auto-approve */}
              <div className={`p-4 ${SETTINGS_CARD}`}>
                <div className={`flex items-start gap-3 border-b pb-3.5 ${SETTINGS_DIVIDER}`}>
                  <FeaturedIcon size="md" color="gray" icon={<IcCheck />} />
                  <div className="min-w-0">
                    <div className="text-[15px] font-semibold text-ink">Auto-approve</div>
                    <div className="mt-1 text-[12.5px] leading-relaxed text-ink-muted">Matches scoring above the threshold are approved automatically, skipping review.</div>
                  </div>
                </div>
                <div className="mt-4 flex flex-col gap-4">
                  <div className="flex items-center justify-between gap-4">
                    <div className="text-[13.5px] font-medium text-ink">Enable auto-approve</div>
                    <Toggle isSelected={autoApprove} onChange={() => setAutoApprove(!autoApprove)} aria-label="Enable auto-approve" />
                  </div>
                  <div className={`p-3.5 ${SETTINGS_NESTED} ${autoApprove ? '' : 'opacity-50'}`}>
                    <div className="mb-2.5 flex items-center justify-between">
                      <span className="text-[13px] font-medium text-ink-muted">Threshold</span>
                      <span className="font-mono text-[13px] font-semibold text-conf-high">≥ {autoThreshold}%</span>
                    </div>
                    <input
                      type="range"
                      min={80}
                      max={100}
                      value={autoThreshold}
                      disabled={!autoApprove}
                      onChange={e => setAutoThreshold(+e.target.value)}
                      className="h-1.5 w-full cursor-pointer"
                      style={{ accentColor: 'var(--accent)' }}
                    />
                  </div>
                </div>
              </div>

              {/* Confidence thresholds — clamp High >= Med+5 and Med <= High-5
                  so the buckets can't invert and collapse the Mid range. */}
              <div className={`p-4 ${SETTINGS_CARD}`}>
                <div className={`flex items-start gap-3 border-b pb-3.5 ${SETTINGS_DIVIDER}`}>
                  <FeaturedIcon size="md" color="gray" icon={<IcShieldCheck />} />
                  <div className="min-w-0">
                    <div className="text-[15px] font-semibold text-ink">Confidence thresholds</div>
                    <div className="mt-1 text-[12.5px] leading-relaxed text-ink-muted">Where the green / amber / red cutoffs sit for the match badges.</div>
                  </div>
                </div>
                <div className="mt-4 flex flex-col gap-3.5">
                  <div className="flex items-center gap-3">
                    <span className="inline-flex w-16 shrink-0 items-center gap-2 text-[13px] font-medium text-ink">
                      <span className="size-2 rounded-full" style={{ background: 'var(--conf-high)' }} /> High
                    </span>
                    <input
                      type="range"
                      min={Math.max(60, midT + 5)}
                      max={100}
                      value={highT}
                      onChange={e => setHighT(Math.min(100, Math.max(midT + 5, +e.target.value)))}
                      className="h-1.5 flex-1 cursor-pointer"
                      style={{ accentColor: 'var(--conf-high)' }}
                    />
                    <span className="w-14 shrink-0 text-right font-mono text-[12px] text-conf-high">≥ {highT}%</span>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="inline-flex w-16 shrink-0 items-center gap-2 text-[13px] font-medium text-ink">
                      <span className="size-2 rounded-full" style={{ background: 'var(--conf-mid)' }} /> Med
                    </span>
                    <input
                      type="range"
                      min={20}
                      max={Math.min(80, highT - 5)}
                      value={midT}
                      onChange={e => setMidT(Math.max(20, Math.min(highT - 5, +e.target.value)))}
                      className="h-1.5 flex-1 cursor-pointer"
                      style={{ accentColor: 'var(--conf-mid)' }}
                    />
                    <span className="w-14 shrink-0 text-right font-mono text-[12px] text-conf-mid">≥ {midT}%</span>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="inline-flex w-16 shrink-0 items-center gap-2 text-[13px] font-medium text-ink">
                      <span className="size-2 rounded-full" style={{ background: 'var(--conf-low)' }} /> Low
                    </span>
                    <span className="flex-1 text-[12px] text-ink-soft">everything below the Med cutoff</span>
                    <span className="w-14 shrink-0 text-right font-mono text-[12px] text-conf-low">&lt; {midT}%</span>
                  </div>
                </div>
              </div>
              </div>
            </div>
          )}

          {section === 'advanced' && (
            <AdvancedSection rawSettings={rawSettings} setRawSettings={setRawSettings} saveKey={saveKey} pushToast={pushToast} />
          )}
          </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Paths section — folder picker + watch folder list
// ─────────────────────────────────────────────────────────────────────

// `number` is needed for numeric settings like the Sonarr quality-profile id;
// the value is JSON-serialized to the settings API, which accepts all three.
type SaveKeyFn = (key: string) => (value: string | number | boolean) => void;

// Reusable trailing icon-button style for path fields (browse / clear).
const PATH_ICON_BTN = 'grid size-7 shrink-0 place-items-center rounded-md text-ink-soft transition-colors hover:bg-glass-2 hover:text-ink [&_svg]:size-[14px]';

// Monospace path input with an in-field Browse button (and optional Clear).
// Declared at module scope so it keeps a stable identity across renders —
// defining it inside PathsSection would remount the <input> on every keystroke
// and drop focus.
function PathField({ value, placeholder, onChange, onBrowse, onClear, browseTitle }: {
  value: string;
  placeholder?: string;
  onChange: (v: string) => void;
  onBrowse: () => void;
  onClear?: () => void;
  browseTitle?: string;
}) {
  return (
    <Input
      mono
      value={value}
      placeholder={placeholder}
      spellCheck={false}
      onChange={e => onChange(e.target.value)}
      trailing={
        <div className="flex items-center gap-0.5">
          {onClear && value ? (
            <button type="button" className={PATH_ICON_BTN + ' hover:text-conf-low'} onClick={onClear} title="Clear override — use the default location">
              <IcX />
            </button>
          ) : null}
          <button type="button" className={PATH_ICON_BTN} onClick={onBrowse} title={browseTitle ?? 'Browse for folder'}>
            <IcFolder />
          </button>
        </div>
      }
    />
  );
}

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
    // Bug B: optimistic update FIRST — same fix as `saveKey` above.
    // Pre-fix, mirroring `rawSettings` only AFTER the PUT resolved
    // meant a re-open of the picker mid-flight could see the stale
    // list and produce a duplicate or missing entry on second add.
    // Optimistic update collapses the round-trip: the UI reflects
    // the new list immediately, and a failure toast is the only
    // signal of staleness (the user can retry; the optimistic state
    // is left in place so they don't lose their input).
    setRawSettings(s => ({ ...s, 'paths.watch_folders': next }));
    try {
      await api.putSettings({ 'paths.watch_folders': next });
      pushToast({ title: 'Watch folders saved', kind: 'success' });
    } catch (e) {
      pushToast({ title: 'Save failed', sub: (e as Error).message, kind: 'error' });
    }
  };

  // ── Watched-folders auto-scan config (single JSON blob under watch.config).
  // Backend re-arms the daemon on save (settings PUT → watcher.reconfigure()).
  type FolderCfg = { mode: 'scan' | 'auto_rename'; threshold: number };
  type WatchCfg = {
    auto_scan: boolean;
    debounce_seconds: number;
    poll_interval_seconds: number;
    folders: Record<string, FolderCfg>;
  };
  const watchCfg: WatchCfg = (() => {
    const raw = rawSettings['watch.config'];
    const base: WatchCfg = { auto_scan: false, debounce_seconds: 30, poll_interval_seconds: 900, folders: {} };
    if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
      const r = raw as Record<string, unknown>;
      if (typeof r.auto_scan === 'boolean') base.auto_scan = r.auto_scan;
      if (typeof r.debounce_seconds === 'number') base.debounce_seconds = r.debounce_seconds;
      if (typeof r.poll_interval_seconds === 'number') base.poll_interval_seconds = r.poll_interval_seconds;
      if (r.folders && typeof r.folders === 'object') base.folders = r.folders as Record<string, FolderCfg>;
    }
    return base;
  })();

  const saveWatchCfg = async (next: WatchCfg) => {
    setRawSettings(s => ({ ...s, 'watch.config': next as unknown as Record<string, unknown>[string] }));
    try {
      await api.putSettings({ 'watch.config': next });
      pushToast({ title: 'Auto-scan settings saved', kind: 'success' });
    } catch (e) {
      pushToast({ title: 'Save failed', sub: (e as Error).message, kind: 'error' });
    }
  };

  const folderModeFor = (path: string): FolderCfg =>
    watchCfg.folders[path] ?? { mode: 'scan', threshold: 0.9 };

  const typeRows = [
    { label: 'Movies', sub: 'Movies', value: targetMovie, key: 'paths.targets.movie' as const, pickerFor: 'target-movie' as const, icon: <IcFilm />, color: '#bdc1d0' },
    { label: 'TV',     sub: 'TV',     value: targetTv,    key: 'paths.targets.tv'    as const, pickerFor: 'target-tv'    as const, icon: <IcTv />,   color: '#49b8fe' },
    { label: 'Anime',  sub: 'Anime',  value: targetAnime, key: 'paths.targets.anime' as const, pickerFor: 'target-anime' as const, icon: <IcAnime />, color: '#c89bff' },
    { label: 'Music',  sub: 'Music',  value: targetMusic, key: 'paths.targets.music' as const, pickerFor: 'target-music' as const, icon: <IcMusic />, color: '#ffb14a' },
  ];

  return (
    <div className="flex flex-col gap-4 p-5">
      <p className="text-[13px] leading-relaxed text-ink-muted">
        Tell Kira where your media lives and where renamed files should land.
      </p>

      {/* Media root + Watch folders */}
      <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-2">
        {/* Media root */}
        <div className={`flex flex-col p-4 ${SETTINGS_CARD}`}>
          <div className={`flex items-start gap-3 border-b pb-3.5 ${SETTINGS_DIVIDER}`}>
            <FeaturedIcon size="md" color="gray" icon={<IcFolder />} />
            <div className="min-w-0">
              <div className="text-[15px] font-semibold text-ink">Media root</div>
              <div className="mt-1 text-[12.5px] leading-relaxed text-ink-muted">Where Kira renames into — the move / hardlink target for every file.</div>
            </div>
          </div>
          <div className="mt-4">
            <PathField
              value={libraryRoot}
              onChange={v => saveKey('paths.library_root')(v)}
              onBrowse={() => setPicker({ for: 'library', initial: libraryRoot })}
            />
          </div>
        </div>

        {/* Watch folders */}
        <div className={`flex flex-col p-4 ${SETTINGS_CARD}`}>
          <div className={`flex items-start gap-3 border-b pb-3.5 ${SETTINGS_DIVIDER}`}>
            <FeaturedIcon size="md" color="gray" icon={<IcScan />} />
            <div className="min-w-0">
              <div className="text-[15px] font-semibold text-ink">Watch folders</div>
              <div className="mt-1 text-[12.5px] leading-relaxed text-ink-muted">Folders Kira scans when you click “Scan now”.</div>
            </div>
          </div>
          <div className="mt-4 flex flex-1 flex-col gap-2">
            {watchFolders.length === 0 ? (
              <div className="rounded-xl border border-dashed border-white/[0.12] px-3 py-2.5 text-xs text-ink-muted">
                No watch folders yet — add one below.
              </div>
            ) : null}
            {watchFolders.map((p, i) => (
              <div key={i} className={`flex items-center gap-2.5 px-3 py-2.5 ${SETTINGS_NESTED}`}>
                <IcFolder style={{ width: 14, height: 14 }} className="shrink-0 text-ink-soft" />
                <span className="flex-1 truncate font-mono text-[12.5px] text-ink-muted">{p}</span>
                <button
                  type="button"
                  title="Remove"
                  onClick={() => void setWatch(watchFolders.filter((_, j) => j !== i))}
                  className="grid size-7 shrink-0 place-items-center rounded-md text-ink-soft transition-colors hover:bg-[var(--conf-low-bg)] hover:text-conf-low [&_svg]:size-[13px]"
                >
                  <IcTrash />
                </button>
              </div>
            ))}
          </div>
          <Button color="secondary" size="sm" iconLeading={IcPlus} className="mt-2.5 w-full" onClick={() => setPicker({ for: 'watch', initial: libraryRoot })}>
            Add watch folder
          </Button>
        </div>
      </div>

      {/* Auto-scan (watched folders) */}
      <div className={`p-4 ${SETTINGS_CARD}`}>
        <div className={`flex items-start gap-3 border-b pb-3.5 ${SETTINGS_DIVIDER}`}>
          <FeaturedIcon size="md" color="gray" icon={<IcScan />} />
          <div className="min-w-0 flex-1">
            <div className="text-[15px] font-semibold text-ink">Auto-scan</div>
            <div className="mt-1 text-[12.5px] leading-relaxed text-ink-muted">
              Watch your folders and scan automatically when new files appear — no need to click “Scan now”.
              Detected via filesystem events with a periodic poll fallback for network drives.
            </div>
          </div>
          <label className="flex shrink-0 cursor-pointer items-center gap-2 pt-0.5">
            <input
              type="checkbox"
              checked={watchCfg.auto_scan}
              onChange={e => void saveWatchCfg({ ...watchCfg, auto_scan: e.target.checked })}
              className="size-4 accent-[var(--brand-solid,#7c5cff)]"
            />
            <span className="text-[13px] font-medium text-ink-muted">{watchCfg.auto_scan ? 'On' : 'Off'}</span>
          </label>
        </div>

        {watchCfg.auto_scan ? (
          <div className="mt-4 flex flex-col gap-4">
            {/* timing */}
            <div className="flex flex-wrap gap-4">
              <label className="flex flex-col gap-1">
                <span className="text-[12px] font-medium text-ink-muted">Settle delay (seconds)</span>
                <input
                  type="number" min={5}
                  value={watchCfg.debounce_seconds}
                  onChange={e => void saveWatchCfg({ ...watchCfg, debounce_seconds: Math.max(5, Number(e.target.value) || 5) })}
                  className={`w-32 px-2.5 py-1.5 font-mono text-[13px] ${SETTINGS_NESTED}`}
                />
                <span className="text-[11px] text-ink-soft">Wait this long after the last change before scanning.</span>
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-[12px] font-medium text-ink-muted">Poll interval (seconds)</span>
                <input
                  type="number" min={60}
                  value={watchCfg.poll_interval_seconds}
                  onChange={e => void saveWatchCfg({ ...watchCfg, poll_interval_seconds: Math.max(60, Number(e.target.value) || 60) })}
                  className={`w-32 px-2.5 py-1.5 font-mono text-[13px] ${SETTINGS_NESTED}`}
                />
                <span className="text-[11px] text-ink-soft">Fallback re-check for network drives.</span>
              </label>
            </div>

            {/* per-folder mode */}
            <div className="flex flex-col gap-2">
              <div className="text-[12px] font-medium text-ink-muted">Per-folder behaviour</div>
              {Array.from(new Set([libraryRoot, ...watchFolders])).filter(Boolean).map(path => {
                const fc = folderModeFor(path);
                return (
                  <div key={path} className={`flex flex-wrap items-center gap-2.5 px-3 py-2.5 ${SETTINGS_NESTED}`}>
                    <IcFolder style={{ width: 14, height: 14 }} className="shrink-0 text-ink-soft" />
                    <span className="min-w-0 flex-1 truncate font-mono text-[12.5px] text-ink-muted">{path}</span>
                    <select
                      value={fc.mode}
                      onChange={e => {
                        const mode = e.target.value as FolderCfg['mode'];
                        void saveWatchCfg({ ...watchCfg, folders: { ...watchCfg.folders, [path]: { ...fc, mode } } });
                      }}
                      className={`px-2 py-1 text-[12.5px] ${SETTINGS_NESTED}`}
                    >
                      <option value="scan">Scan + match only</option>
                      <option value="auto_rename">Auto-rename high-confidence</option>
                    </select>
                    {fc.mode === 'auto_rename' ? (
                      <label className="flex items-center gap-1.5 text-[12px] text-ink-soft">
                        ≥
                        <input
                          type="number" min={0} max={100} step={1}
                          value={Math.round(fc.threshold * 100)}
                          onChange={e => {
                            const threshold = Math.min(1, Math.max(0, (Number(e.target.value) || 0) / 100));
                            void saveWatchCfg({ ...watchCfg, folders: { ...watchCfg.folders, [path]: { ...fc, threshold } } });
                          }}
                          className={`w-16 px-2 py-1 font-mono text-[12.5px] ${SETTINGS_NESTED}`}
                        />
                        % conf
                      </label>
                    ) : null}
                  </div>
                );
              })}
              <p className="text-[11px] leading-relaxed text-ink-soft">
                <span className="font-medium text-ink-muted">Scan + match only</span> surfaces new files in Review for you to approve.{' '}
                <span className="font-medium text-ink-muted">Auto-rename</span> additionally organizes matches above the confidence
                threshold automatically (coming soon — currently behaves as scan-only).
              </p>
            </div>
          </div>
        ) : null}
      </div>

      {/* Per-type destinations */}
      <div className={`p-4 ${SETTINGS_CARD}`}>
        <div className={`flex items-start gap-3 border-b pb-3.5 ${SETTINGS_DIVIDER}`}>
          <FeaturedIcon size="md" color="gray" icon={<IcFolder />} />
          <div className="min-w-0">
            <div className="text-[15px] font-semibold text-ink">Per-type destinations</div>
            <div className="mt-1 text-[12.5px] leading-relaxed text-ink-muted">
              Optional — route each media type to its own folder or drive. Blank lands at{' '}
              <span className="font-mono text-ink">Media root / Type</span>.
            </div>
          </div>
        </div>
        <div className="mt-4 flex flex-col gap-2.5">
          {typeRows.map(row => (
            <div key={row.key} className="flex items-center gap-3">
              <span className="inline-flex w-[88px] shrink-0 items-center gap-1.5 text-[13px] font-medium text-ink-muted">
                <span style={{ color: row.color }} className="inline-flex [&_svg]:size-[13px]">{row.icon}</span>{row.label}
              </span>
              <div className="flex-1">
                <PathField
                  value={row.value}
                  placeholder={defaultTarget(row.sub)}
                  onChange={v => saveKey(row.key)(v)}
                  onClear={() => saveKey(row.key)('')}
                  onBrowse={() => setPicker({ for: row.pickerFor, initial: row.value || libraryRoot })}
                  browseTitle={`Browse for ${row.label} destination`}
                />
              </div>
            </div>
          ))}
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
// Integrations section — outbound tools (Sonarr/Radarr/Plex/etc.)
//
// Distinct from Connections (which fetches metadata INTO Kira from
// TMDB/TVDB/AniDB). Integrations PUSH actions to user-owned tools.
// Phase 1 ships Sonarr — fill in URL + API key, click Test, and Kira
// will populate the quality-profile + root-folder dropdowns from the
// user's real Sonarr config. The Cover Popup gets a "Get missing →
// Sonarr" button once everything's valid.
// ─────────────────────────────────────────────────────────────────────

function IntegrationsSection({
  rawSettings,
  saveKey,
}: {
  rawSettings: Record<string, unknown>;
  setRawSettings: React.Dispatch<React.SetStateAction<Record<string, unknown>>>;
  saveKey: SaveKeyFn;
  pushToast: Props['pushToast'];
}) {
  const sonarrUrl = strSetting(rawSettings, 'integrations.sonarr.url');
  const sonarrApiKey = strSetting(rawSettings, 'integrations.sonarr.api_key');
  const sonarrUrlBase = strSetting(rawSettings, 'integrations.sonarr.url_base');
  // Show/hide for the API key field — `password` masks chars by
  // default; eye toggle flips to plain text for a quick visual check.
  const [showApiKey, setShowApiKey] = useState(false);

  // Per-flavor series-type. Sonarr accepts standard / anime / daily;
  // sensible defaults are seeded but the user can override either.
  // Same dropdown options as Sonarr's own UI.
  const readSeriesType = (sect: 'tv' | 'anime'): string => {
    const v = rawSettings[`integrations.sonarr.${sect}.series_type`];
    if (typeof v === 'string') return v;
    return sect === 'anime' ? 'anime' : 'standard';
  };
  const tvSeriesType = readSeriesType('tv');
  const animeSeriesType = readSeriesType('anime');

  // Per-flavor audio preference for grabs. On Sonarr v4 sub-vs-dub is decided
  // by Custom Formats in the quality profile; when the user picks sub/dub here
  // Kira instead does an interactive search and grabs the matching release,
  // skipping the opposite audio. Default 'any' = Sonarr's normal auto-search
  // (matches the backend default, so the UI never lies about behavior).
  const readAudio = (sect: 'tv' | 'anime'): string => {
    const v = rawSettings[`integrations.sonarr.${sect}.audio_preference`]
      ?? rawSettings['integrations.sonarr.audio_preference'];
    if (v === 'sub' || v === 'dub' || v === 'any') return v;
    return 'any';
  };
  const tvAudio = readAudio('tv');
  const animeAudio = readAudio('anime');

  // Global Sonarr behaviours (apply to every series we add).
  const seasonFolders = (() => {
    const v = rawSettings['integrations.sonarr.season_folders'];
    if (typeof v === 'boolean') return v;
    return true;  // Sonarr's own default is on
  })();
  const monitorNewSeasons = (() => {
    const v = rawSettings['integrations.sonarr.monitor_new_seasons'];
    if (v === 'all' || v === 'future' || v === 'none') return v;
    return 'all';
  })();
  // Per-series-type config — Sonarr keeps separate quality profiles +
  // root folders for TV vs Anime (typical setup: HD-1080p profile +
  // /data/media/tv for TV, "Anime" profile + /data/media/anime for
  // anime). Kira mirrors that split so the right Sonarr config gets
  // applied to each series. The backend's send-missing endpoint picks
  // the pair based on the matched series' provider (AniDB → anime,
  // TVDB → tv).
  //
  // Falls back to the legacy un-prefixed keys for users who configured
  // before the split (both fields can come from a single key without
  // needing to re-pick when they upgrade).
  const readId = (key: string): number | undefined => {
    const v = rawSettings[key];
    if (typeof v === 'number') return v;
    if (typeof v === 'string' && /^\d+$/.test(v)) return parseInt(v, 10);
    return undefined;
  };
  const legacyQpId = readId('integrations.sonarr.quality_profile_id');
  const legacyRoot = strSetting(rawSettings, 'integrations.sonarr.root_folder_path');
  const tvQpId = readId('integrations.sonarr.tv.quality_profile_id') ?? legacyQpId;
  const tvRoot = strSetting(rawSettings, 'integrations.sonarr.tv.root_folder_path') || legacyRoot;
  const animeQpId = readId('integrations.sonarr.anime.quality_profile_id') ?? legacyQpId;
  const animeRoot = strSetting(rawSettings, 'integrations.sonarr.anime.root_folder_path') || legacyRoot;

  // Test state — held locally because the result is transient (only
  // valid while the user's looking at the form). Profiles + folders
  // come back together from the test call and populate the dropdowns.
  const [testing, setTesting] = useState(false);
  const [testStatus, setTestStatus] = useState<'idle' | 'ok' | 'fail'>('idle');
  const [testDetail, setTestDetail] = useState<string | null>(null);
  const [version, setVersion] = useState<string | null>(null);
  const [profiles, setProfiles] = useState<Array<{ id: number; name: string }>>([]);
  const [roots, setRoots] = useState<Array<{ path: string; freeSpace?: number | null }>>([]);

  // On mount: if URL + key are already saved, fetch profiles + roots so
  // dropdowns aren't empty. Best-effort — silently skip on failure since
  // we don't want to nag the user with a toast on every page open.
  useEffect(() => {
    if (!sonarrUrl || !sonarrApiKey) return;
    let cancelled = false;
    void api.testSonarr().then(r => {
      if (cancelled) return;
      if (r.ok) {
        setTestStatus('ok');
        setVersion(r.version);
        setProfiles(r.quality_profiles ?? []);
        setRoots(r.root_folders ?? []);
      }
    }).catch(() => { /* silent */ });
    return () => { cancelled = true; };
    // We intentionally only run this once on mount — the user's "Test"
    // click handles refresh after credential edits.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const runTest = async () => {
    setTesting(true);
    setTestStatus('idle');
    setTestDetail(null);
    try {
      // Pass current form values inline so we can test BEFORE saving —
      // the user shouldn't have to commit a possibly-wrong config
      // first.
      const r = await api.testSonarr({
        url: sonarrUrl || undefined,
        api_key: sonarrApiKey || undefined,
      });
      if (r.ok) {
        setTestStatus('ok');
        setTestDetail(`Connected to Sonarr v${r.version ?? '?'}.`);
        setVersion(r.version);
        setProfiles(r.quality_profiles ?? []);
        setRoots(r.root_folders ?? []);
      } else {
        setTestStatus('fail');
        setTestDetail(r.detail ?? 'Sonarr test failed.');
        setProfiles([]);
        setRoots([]);
      }
    } catch (e) {
      setTestStatus('fail');
      setTestDetail((e as Error).message);
    } finally {
      setTesting(false);
    }
  };

  const formatBytes = (bytes: number | null | undefined): string => {
    if (!bytes || !Number.isFinite(bytes)) return '';
    const gb = bytes / (1024 ** 3);
    if (gb >= 1024) return `${(gb / 1024).toFixed(1)} TB free`;
    return `${Math.round(gb)} GB free`;
  };

  // Sonarr's series-type options. "anime" uses absolute-numbered
  // filenames + a separate folder root convention; "daily" is for
  // shows that release nightly (yyyy-mm-dd naming); "standard" is
  // the SxxExx default. Each flavor card gets its own pick so a
  // user with a daily TV show can route those without affecting
  // their main TV settings.
  const seriesTypeOptions = [
    { value: 'standard', label: 'Standard (SxxExx)' },
    { value: 'anime', label: 'Anime (absolute numbering)' },
    { value: 'daily', label: 'Daily (yyyy-mm-dd)' },
  ];

  // Audio-preference options for the per-flavor card. "any" defers to
  // Sonarr's normal auto-search; "sub"/"dub" make Kira run an interactive
  // release search and grab the matching audio, skipping the opposite.
  const audioOptions = [
    { value: 'any', label: 'Any (Sonarr decides)' },
    { value: 'sub', label: 'Prefer subbed (skip dubs)' },
    { value: 'dub', label: 'Prefer dubbed (skip subs)' },
  ];

  const sonarrConfigured = !!sonarrUrl && !!sonarrApiKey;
  const sonarrStatusColor: 'success' | 'error' | 'gray' =
    testStatus === 'ok' ? 'success' : testStatus === 'fail' ? 'error' : 'gray';
  const sonarrStatusLabel =
    testStatus === 'ok' ? 'Connected' : testStatus === 'fail' ? 'Failed' : sonarrConfigured ? 'Saved' : 'Not set up';

  const renderFlavor = (
    title: string,
    sub: string,
    qpId: number | undefined,
    folder: string,
    sType: string,
    audioPref: string,
    qpKey: string,
    folderKey: string,
    sTypeKey: string,
    audioKey: string,
  ) => (
    <div className={`p-3.5 ${SETTINGS_NESTED}`}>
      <div className="mb-3 flex items-baseline justify-between gap-2">
        <span className="text-[13px] font-semibold text-ink">{title}</span>
        <span className="text-[11px] text-ink-soft">{sub}</span>
      </div>
      <div className="flex flex-col gap-2.5">
        <div className="flex items-center gap-3">
          <span className="w-16 shrink-0 text-[12px] font-medium text-ink-muted">Type</span>
          <Select<string> style={{ flex: 1, minWidth: 0 }} value={sType} onChange={v => saveKey(sTypeKey)(v)} options={seriesTypeOptions} />
        </div>
        <div className="flex items-center gap-3">
          <span className="w-16 shrink-0 text-[12px] font-medium text-ink-muted">Quality</span>
          <Select<number> style={{ flex: 1, minWidth: 0 }} value={qpId} onChange={v => saveKey(qpKey)(v)} placeholder="— pick a quality profile —" options={profiles.map(p => ({ value: p.id, label: p.name }))} />
        </div>
        <div className="flex items-center gap-3">
          <span className="w-16 shrink-0 text-[12px] font-medium text-ink-muted">Audio</span>
          <Select<string> style={{ flex: 1, minWidth: 0 }} value={audioPref} onChange={v => saveKey(audioKey)(v)} options={audioOptions} />
        </div>
        <div className="flex items-center gap-3">
          <span className="w-16 shrink-0 text-[12px] font-medium text-ink-muted">Folder</span>
          <Select<string> style={{ flex: 1, minWidth: 0 }} buttonClassName="mono" value={folder || null} onChange={v => saveKey(folderKey)(v)} placeholder="— pick a root folder —" options={roots.map(r => ({ value: r.path, label: r.path, secondary: formatBytes(r.freeSpace) || undefined }))} />
        </div>
      </div>
    </div>
  );

  return (
    <div className="flex flex-col gap-4 p-5">
      <p className="text-[13px] leading-relaxed text-ink-muted">
        Push actions to tools in your media stack. Sonarr first; Radarr arrives with movie-collection grouping.
      </p>

      {/* ── Sonarr block ─────────────────────────────────────────── */}
      <div className={`p-4 ${SETTINGS_CARD}`}>
        <div className={`flex items-start gap-3 border-b pb-3.5 ${SETTINGS_DIVIDER}`}>
          <FeaturedIcon size="md" color="gray" icon={<IcTv />} />
          <div className="min-w-0 flex-1">
            <div className="text-[15px] font-semibold text-ink">Sonarr</div>
            <div className="mt-1 text-[12.5px] leading-relaxed text-ink-muted">
              When configured, the cover popup gets a <span className="font-mono text-ink">Get missing → Sonarr</span>{' '}
              button that one-clicks a search for every episode Kira knows you don't have. URL + API key live in
              Sonarr's <span className="font-mono text-ink">Settings → General → Security</span>.
            </div>
          </div>
          <BadgeWithDot color={sonarrStatusColor}>{sonarrStatusLabel}</BadgeWithDot>
        </div>

        <div className="mt-4 flex flex-col gap-3">
          <div className="flex items-center gap-3">
            <span className="w-20 shrink-0 text-[13px] font-medium text-ink-muted">URL</span>
            <Input wrapperClassName="flex-1" mono value={sonarrUrl} placeholder="http://sonarr:8989" onChange={e => saveKey('integrations.sonarr.url')(e.target.value)} />
          </div>
          <div className="flex items-center gap-3">
            <span className="w-20 shrink-0 text-[13px] font-medium text-ink-muted">URL base</span>
            <Input wrapperClassName="flex-1" mono value={sonarrUrlBase} placeholder="optional · e.g. /sonarr (reverse-proxy setups)" onChange={e => saveKey('integrations.sonarr.url_base')(e.target.value)} />
          </div>
          <div className="flex items-center gap-3">
            <span className="w-20 shrink-0 text-[13px] font-medium text-ink-muted">API key</span>
            <Input
              wrapperClassName="flex-1"
              mono
              type={showApiKey ? 'text' : 'password'}
              value={sonarrApiKey}
              placeholder="from Sonarr's General → Security page"
              autoComplete="off"
              onChange={e => saveKey('integrations.sonarr.api_key')(e.target.value)}
              trailing={
                <button
                  type="button"
                  onClick={() => setShowApiKey(s => !s)}
                  title={showApiKey ? 'Hide API key' : 'Show API key'}
                  aria-label={showApiKey ? 'Hide API key' : 'Show API key'}
                  className="grid size-6 shrink-0 place-items-center rounded-md text-ink-soft transition-colors hover:bg-glass-2 hover:text-ink [&_svg]:size-[14px]"
                >
                  {showApiKey ? <IcEyeOff /> : <IcEye />}
                </button>
              }
            />
          </div>
        </div>

        <div className="mt-3.5 flex justify-end">
          <Button
            color="secondary"
            size="sm"
            iconLeading={IcRefresh}
            isLoading={testing}
            isDisabled={!sonarrUrl || !sonarrApiKey}
            showTextWhileLoading
            onClick={() => void runTest()}
          >
            Test connection
          </Button>
        </div>

        {testStatus === 'ok' ? (
          <Alert color="success" icon={IcCheck} className="mt-3.5">
            {testDetail ?? `Connected to Sonarr${version ? ` v${version}` : ''}.`}
          </Alert>
        ) : null}
        {testStatus === 'fail' ? (
          <Alert color="error" icon={IcAlertTri} className="mt-3.5">{testDetail || 'Connection failed.'}</Alert>
        ) : null}

        {/* Per-series-type defaults — only render after a successful
            test has populated `profiles` + `roots`. The two flavor
            cards live in a 2-column grid that collapses to 1-col on
            narrow viewports (see CSS @media). The same profile list
            populates both cards; the backend picks the right pair at
            send-missing time based on the matched series' provider
            (TVDB → tv, AniDB → anime). */}
        {(profiles.length > 0 || roots.length > 0) && (
          <div className="mt-4 flex flex-col gap-3 border-t border-white/[0.1] pt-4">
            <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-muted">Per-series-type defaults</div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {renderFlavor(
                'TV Shows',
                'matched via TVDB',
                tvQpId, tvRoot, tvSeriesType, tvAudio,
                'integrations.sonarr.tv.quality_profile_id',
                'integrations.sonarr.tv.root_folder_path',
                'integrations.sonarr.tv.series_type',
                'integrations.sonarr.tv.audio_preference',
              )}
              {renderFlavor(
                'Anime',
                'matched via AniDB',
                animeQpId, animeRoot, animeSeriesType, animeAudio,
                'integrations.sonarr.anime.quality_profile_id',
                'integrations.sonarr.anime.root_folder_path',
                'integrations.sonarr.anime.series_type',
                'integrations.sonarr.anime.audio_preference',
              )}
            </div>

            {/* Global Sonarr behaviors — apply to every series Kira adds,
                regardless of TV vs Anime flavor. */}
            <div className="mt-1 text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-muted">Behavior on add</div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div className={`flex items-center justify-between gap-3 px-3.5 py-2.5 ${SETTINGS_NESTED}`}>
                <span className="text-[13px] text-ink">Use season folders <span className="font-mono text-ink-soft">(Season 01/)</span></span>
                <Toggle isSelected={seasonFolders} onChange={() => saveKey('integrations.sonarr.season_folders')(!seasonFolders)} aria-label="Use season folders" />
              </div>
              <div className="flex items-center gap-3">
                <span className="w-16 shrink-0 text-[12px] font-medium text-ink-muted">Monitor</span>
                <Select<string>
                  style={{ flex: 1, minWidth: 0 }}
                  value={monitorNewSeasons}
                  onChange={v => saveKey('integrations.sonarr.monitor_new_seasons')(v)}
                  options={[
                    { value: 'all', label: 'All future seasons' },
                    { value: 'future', label: 'Future seasons (no search)' },
                    { value: 'none', label: 'None (manual)' },
                  ]}
                />
              </div>
            </div>
          </div>
        )}
        {testStatus === 'ok' && (profiles.length === 0 || roots.length === 0) && (
          <Alert color="warning" icon={IcAlertTri} className="mt-3.5">
            Sonarr is reachable but has no{' '}
            {profiles.length === 0 ? 'quality profiles' : ''}
            {profiles.length === 0 && roots.length === 0 ? ' or ' : ''}
            {roots.length === 0 ? 'root folders' : ''} configured yet. Set them up in Sonarr, then Test again.
          </Alert>
        )}
      </div>

      {/* ── Radarr placeholder ───────────────────────────────────── */}
      <div className="rounded-2xl border border-dashed border-white/[0.14] bg-white/[0.02] p-4">
        <div className="flex items-start gap-3">
          <FeaturedIcon size="md" color="gray" icon={<IcFilm />} />
          <div className="min-w-0 flex-1">
            <div className="text-[15px] font-semibold text-ink-muted">Radarr</div>
            <div className="mt-1 text-[12.5px] leading-relaxed text-ink-soft">
              Movies-side parallel of Sonarr — activates once Kira can identify movie collections,
              so "MCU Phase 4: 8/12" can fire a search for the missing 4 in one click.
            </div>
          </div>
          <BadgeWithDot color="gray">Coming soon</BadgeWithDot>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Advanced section — retention, concurrency, danger zone
// ─────────────────────────────────────────────────────────────────────

function AdvancedSection({
  rawSettings,
  setRawSettings,
  saveKey,
  pushToast,
}: {
  rawSettings: Record<string, unknown>;
  setRawSettings: React.Dispatch<React.SetStateAction<Record<string, unknown>>>;
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
  // MediaInfo (Phase 16) toggles. read_mediainfo defaults ON (fills blanks
  // the filename lacked); authoritative defaults OFF (reads every file +
  // overrides — opt-in because of the per-file I/O cost).
  const readMediainfo = typeof rawSettings['parsing.read_mediainfo'] === 'boolean'
    ? rawSettings['parsing.read_mediainfo'] as boolean : true;
  const mediainfoAuthoritative = typeof rawSettings['parsing.mediainfo_authoritative'] === 'boolean'
    ? rawSettings['parsing.mediainfo_authoritative'] as boolean : false;
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
    <div className="flex flex-col gap-4 p-5">
      <p className="text-[13px] leading-relaxed text-ink-muted">
        Power-user settings — retention, performance, file metadata, and maintenance.
      </p>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      {/* Library & performance */}
      <div className={`p-4 ${SETTINGS_CARD}`}>
        <div className={`flex items-start gap-3 border-b pb-3.5 ${SETTINGS_DIVIDER}`}>
          <FeaturedIcon size="md" color="gray" icon={<IcSettings />} />
          <div className="min-w-0">
            <div className="text-[15px] font-semibold text-ink">Library &amp; performance</div>
            <div className="mt-1 text-[12.5px] leading-relaxed text-ink-muted">How long history is kept and how hard Kira hits your disk.</div>
          </div>
        </div>
        <div className="mt-4 flex flex-col gap-4">
          <div className="flex items-center justify-between gap-4">
            <div className="min-w-0">
              <div className="text-[13.5px] font-medium text-ink">History retention</div>
              <div className="mt-0.5 text-[12.5px] leading-relaxed text-ink-muted">How long to keep the rename log for undo. Older entries are pruned daily.</div>
            </div>
            <div className="w-[160px] shrink-0">
              <Select<string>
                value={retention}
                onChange={v => saveKey('history.retention_days')(v === 'forever' ? '0' : v)}
                options={[
                  { value: '30', label: '30 days' },
                  { value: '90', label: '90 days' },
                  { value: '365', label: '1 year' },
                  { value: 'forever', label: 'Forever' },
                ]}
              />
            </div>
          </div>
          <div className="flex items-center justify-between gap-4">
            <div className="min-w-0">
              <div className="text-[13.5px] font-medium text-ink">Concurrent file operations</div>
              <div className="mt-0.5 text-[12.5px] leading-relaxed text-ink-muted">More is faster but heavier on disk I/O.</div>
            </div>
            <Input
              wrapperClassName="w-24 shrink-0"
              type="number"
              min={1}
              max={32}
              value={concurrency}
              onChange={e => {
                const n = parseInt(e.target.value, 10);
                if (Number.isFinite(n) && n >= 1 && n <= 32) saveKey('rename.concurrency')(String(n));
              }}
            />
          </div>
        </div>
      </div>

      {/* File metadata */}
      <div className={`p-4 ${SETTINGS_CARD}`}>
        <div className={`flex items-start gap-3 border-b pb-3.5 ${SETTINGS_DIVIDER}`}>
          <FeaturedIcon size="md" color="gray" icon={<IcFilm />} />
          <div className="min-w-0">
            <div className="text-[15px] font-semibold text-ink">File metadata (MediaInfo)</div>
            <div className="mt-1 text-[12.5px] leading-relaxed text-ink-muted">Read real resolution / codec / HDR straight from the file container.</div>
          </div>
        </div>
        <div className="mt-4 flex flex-col gap-4">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <div className="text-[13.5px] font-medium text-ink">Read file metadata</div>
              <div className="mt-0.5 text-[12.5px] leading-relaxed text-ink-muted">
                Fill in resolution / codec / HDR from the file when the filename doesn't carry them. No-op if the MediaInfo library isn't installed.
              </div>
            </div>
            <Toggle isSelected={readMediainfo} onChange={() => saveKey('parsing.read_mediainfo')(!readMediainfo)} className="mt-0.5" aria-label="Read file metadata" />
          </div>
          <div className={`p-3.5 ${SETTINGS_NESTED} ${readMediainfo ? '' : 'opacity-50'}`}>
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <div className="text-[13.5px] font-medium text-ink">Authoritative tech tags</div>
                <div className="mt-0.5 text-[12.5px] leading-relaxed text-ink-muted">
                  Let the file's real metadata <strong className="text-ink">override</strong> what the filename claims for{' '}
                  <span className="font-mono text-ink">{'{{vc}}'}</span> <span className="font-mono text-ink">{'{{hdr}}'}</span> <span className="font-mono text-ink">{'{{channels}}'}</span> and quality. Reads every file on scan (slower) but gives true source-accurate tags.
                </div>
              </div>
              <Toggle isSelected={mediainfoAuthoritative} isDisabled={!readMediainfo} onChange={() => saveKey('parsing.mediainfo_authoritative')(!mediainfoAuthoritative)} className="mt-0.5" aria-label="Authoritative tech tags" />
            </div>
          </div>
        </div>
      </div>
      </div>

      {/* Webhook */}
      <div className={`p-4 ${SETTINGS_CARD}`}>
        <div className={`flex items-start gap-3 border-b pb-3.5 ${SETTINGS_DIVIDER}`}>
          <FeaturedIcon size="md" color="gray" icon={<IcLink />} />
          <div className="min-w-0">
            <div className="text-[15px] font-semibold text-ink">Webhook</div>
            <div className="mt-1 text-[12.5px] leading-relaxed text-ink-muted">Notify an external service after every successful rename. Optional.</div>
          </div>
        </div>
        <div className="mt-4">
          <Input
            mono
            placeholder="https://your-server.com/kira/webhook"
            defaultValue={strSetting(rawSettings, 'webhook.url')}
            onBlur={e => {
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

      {/* Danger zone */}
      <div className="rounded-2xl border border-[rgba(255,91,110,0.3)] bg-[var(--conf-low-bg)] p-4">
        <div className="flex items-start gap-3 border-b border-[rgba(255,91,110,0.2)] pb-3.5">
          <FeaturedIcon size="md" color="error" icon={<IcAlertTri />} />
          <div className="min-w-0">
            <div className="text-[15px] font-semibold text-conf-low">Danger zone</div>
            <div className="mt-1 text-[12.5px] leading-relaxed text-ink-muted">Reset Kira's database. Renames already on disk are NOT undone.</div>
          </div>
        </div>
        <div className="mt-4">
          {!confirming ? (
            <Button color="secondary-destructive" size="sm" iconLeading={IcTrash} onClick={() => setConfirming(true)}>
              Reset database…
            </Button>
          ) : (
            <div className="flex flex-col gap-2.5">
              <div className="text-[12.5px] text-conf-low">
                Type <span className="font-mono font-semibold text-ink">DELETE</span> to confirm. This cannot be undone.
              </div>
              <div className="flex items-center gap-2">
                <Input wrapperClassName="flex-1" mono value={confirmText} onChange={e => setConfirmText(e.target.value)} placeholder="DELETE" autoFocus />
                <Button color="primary-destructive" size="sm" isDisabled={confirmText !== 'DELETE'} onClick={() => void doReset()}>
                  Reset now
                </Button>
                <Button color="tertiary" size="sm" onClick={() => { setConfirming(false); setConfirmText(''); }}>
                  Cancel
                </Button>
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
    <div className={`flex flex-col gap-2.5 p-3.5 text-[12px] leading-relaxed ${SETTINGS_NESTED}`}>
      <div className="text-ink-muted"><strong className="text-ink">{info.title} — </strong>{info.what}</div>
      <div className="flex flex-col gap-1.5">
        <div className="flex items-start gap-2">
          <span className="mt-px shrink-0 rounded bg-[var(--conf-high-bg)] px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.04em] text-conf-high">Best for</span>
          <span className="text-ink-muted">{info.bestFor}</span>
        </div>
        <div className="flex items-start gap-2">
          <span className="mt-px shrink-0 rounded bg-[var(--conf-mid-bg)] px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.04em] text-conf-mid">Watch out</span>
          <span className="text-ink-muted">{info.caveat}</span>
        </div>
      </div>
    </div>
  );
}
