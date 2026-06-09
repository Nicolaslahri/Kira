import { useEffect, useRef, useState, type ReactNode } from 'react';
import type { AppState, ToastData } from '../lib/types';
import type { SettingsSection } from '../App';
import { IcTrash, IcCheck, IcRefresh, IcTag, IcArrowRight } from '../lib/icons';
import { SegmentedControl } from '../components/base/segmented/segmented-control';
import { ProviderCard, NamingTemplateTabs, SETTINGS_CARD, SETTINGS_NESTED, SETTINGS_DIVIDER, SettingsLayout, SettingsGrid, SectionCard, SettingRow, NestedBox, SliderField } from '../components/settings-blocks';
import { BadgeWithDot } from '../components/base/badges/badges';
import { Button } from '../components/base/buttons/button';
import { FeaturedIcon } from '../components/base/featured-icons/featured-icon';
import { Toggle } from '../components/base/toggle/toggle';
import { Input } from '../components/base/input/input';
import { IcShieldCheck, IcChevDown, IcSettings, IcLink } from '../lib/icons';
import { api, type ApiProvider } from '../lib/api';
import { strSetting } from './settings/helpers';
import { AdvancedSection } from './settings/AdvancedSection';
import { PathsSection } from './settings/PathsSection';
import { SubtitlesCard } from './settings/SubtitlesCard';
import { IntegrationsSection } from './settings/IntegrationsSection';

// Optional NFO fields the user can include/exclude (Settings → Naming).
// Keys MUST match the backend's NFO_TOGGLEABLE (kira/renamer/nfo.py) and the
// `naming.nfo_fields` setting dict. Structural identity — title, year,
// season/episode, provider <uniqueid> — is always written and not listed here.
const NFO_FIELDS: { key: string; label: string; hint?: string }[] = [
  { key: 'plot',          label: 'Plot / overview' },
  { key: 'genres',        label: 'Genres' },
  { key: 'cast',          label: 'Cast' },
  { key: 'director',      label: 'Director' },
  { key: 'studio',        label: 'Studio / network' },
  { key: 'runtime',       label: 'Runtime' },
  { key: 'country',       label: 'Country' },
  { key: 'originaltitle', label: 'Original title', hint: 'native / romaji' },
  { key: 'artwork',       label: 'Artwork URLs',   hint: 'poster + fanart' },
  { key: 'collection',    label: 'Collection set', hint: 'movies' },
  { key: 'status',        label: 'Status',         hint: 'TV · Continuing / Ended' },
  { key: 'showtitle',     label: 'Show title',     hint: 'episodes' },
  { key: 'streamdetails', label: 'Stream details', hint: 'codec · HDR · audio' },
];

// Artwork kinds Kira can save beside each renamed file (Settings → Naming).
// Keys + defaults MUST match the backend's fanart.tv ALL_KINDS + _ARTWORK_DEFAULTS
// (kira/providers/fanarttv.py, kira/api/rename.py). `fanartOnly` flags the kinds
// that come ONLY from fanart.tv (need a key); poster + fanart also fall back to
// the matched provider's own images, so they work with no key.
const ARTWORK_KINDS: { key: string; label: string; dflt: boolean; fanartOnly?: boolean; hint?: string }[] = [
  { key: 'poster',       label: 'Poster',              dflt: true },
  { key: 'fanart',       label: 'Background',          dflt: true,  hint: 'fanart' },
  { key: 'clearlogo',    label: 'Clear logo',          dflt: true,  fanartOnly: true },
  { key: 'clearart',     label: 'Clear art',           dflt: false, fanartOnly: true },
  { key: 'banner',       label: 'Banner',              dflt: false, fanartOnly: true },
  { key: 'landscape',    label: 'Landscape',           dflt: false, fanartOnly: true, hint: 'thumb' },
  { key: 'disc',         label: 'Disc art',            dflt: false, fanartOnly: true, hint: 'movies' },
  { key: 'characterart', label: 'Character art',       dflt: false, fanartOnly: true, hint: 'anime / TV' },
];

// Common subtitle languages for the picker. Stored as a comma-separated code
// list under `subtitles.languages`; a code the user already had that isn't here
// still round-trips (its chip shows the raw code).
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
// Small status chip for Labs toggles — conveys maturity / cost at a glance.
function LabsChip({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex items-center rounded-full border border-white/[0.14] bg-white/[0.06] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.06em] text-ink-soft">
      {children}
    </span>
  );
}

export function SettingsPage({ pushToast, section, setSection }: Props) {
  const [profile, setProfile] = useState('Plex');
  const [defaultOp, setDefaultOp] = useState('hardlink');
  // Default OFF — must match the backend (`_read_auto_approve_setting`): a fresh
  // DB must not auto-approve a scanned library before the user reviews it.
  const [autoApprove, setAutoApprove] = useState(false);
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
  // Global save indicator — the page header claims "changes save
  // automatically", so surface the actual state. Every save path runs its PUT
  // through trackSave to flip saving → saved → idle (or error). Failures still
  // toast (below); this is the ambient "it stuck" confirmation.
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const savedTimerRef = useRef<number | undefined>(undefined);
  const trackSave = <T,>(p: Promise<T>): Promise<T> => {
    setSaveStatus('saving');
    p.then(
      () => {
        setSaveStatus('saved');
        window.clearTimeout(savedTimerRef.current);
        savedTimerRef.current = window.setTimeout(() => setSaveStatus('idle'), 1600);
      },
      () => setSaveStatus('error'),
    );
    return p;
  };

  const saveKey = (key: string) => (value: string | number | boolean) => {
    setRawSettings(s => ({ ...s, [key]: value }));
    trackSave(api.putSettings({ [key]: value }))
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

  // Which optional NFO fields to write — a dict under `naming.nfo_fields`
  // ({plot:true,...}). A field absent from the dict defaults ON, matching the
  // backend reader, so an unconfigured library writes everything.
  const nfoFields = (() => {
    const v = rawSettings['naming.nfo_fields'];
    return v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, boolean>) : {};
  })();
  const nfoFieldOn = (key: string) => nfoFields[key] !== false;
  const toggleNfoField = (key: string) => {
    const next = { ...nfoFields, [key]: !nfoFieldOn(key) };
    setRawSettings(s => ({ ...s, 'naming.nfo_fields': next }));
    void api.putSettings({ 'naming.nfo_fields': next })
      .catch(e => pushToast({ title: 'Save failed', sub: (e as Error).message, kind: 'error' }));
  };

  // Artwork-type picker — same shape as the NFO picker. Stored under
  // `naming.artwork_types` as `{kind: bool}`; a kind absent uses its default
  // (mirrors the backend's _ARTWORK_DEFAULTS).
  const artworkTypes = (() => {
    const v = rawSettings['naming.artwork_types'];
    return v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, boolean>) : {};
  })();
  const artworkKindOn = (key: string) => {
    const dflt = ARTWORK_KINDS.find(a => a.key === key)?.dflt ?? false;
    return artworkTypes[key] ?? dflt;
  };
  const toggleArtworkKind = (key: string) => {
    const next = { ...artworkTypes, [key]: !artworkKindOn(key) };
    setRawSettings(s => ({ ...s, 'naming.artwork_types': next }));
    void api.putSettings({ 'naming.artwork_types': next })
      .catch(e => pushToast({ title: 'Save failed', sub: (e as Error).message, kind: 'error' }));
  };
  // fanart.tv key is masked in GET /settings ({masked,set,tail}); the raw value
  // is a string only right after the user types it. `fanartKeySet` drives the
  // Connections-card status, its placeholder, and the "needs key" hints on the
  // fanart-only artwork types in Naming. The key INPUT itself lives in the
  // Connections tab (with the other provider credentials).
  const fanartKeyRaw = rawSettings['providers.fanarttv.api_key'];
  const fanartKeySet =
    (!!fanartKeyRaw && typeof fanartKeyRaw === 'object' && (fanartKeyRaw as { set?: boolean }).set === true)
    || (typeof fanartKeyRaw === 'string' && fanartKeyRaw.length > 0);

  // ── Preferred metadata source (provider preference per media type) ───────
  // Stored as `matching.provider_order.<type> = [preferredKey]`. The backend's
  // resolve_provider_order() SOFT-appends the remaining defaults as fallbacks,
  // so picking one provider never strands a title the others could still match.
  const provName = (k: string) =>
    k === 'anidb' ? 'AniDB' : k === 'tvdb' ? 'TheTVDB' : k === 'tmdb' ? 'TMDB' : k;
  const preferredSource = (mt: string, fallback: string): string => {
    const v = rawSettings[`matching.provider_order.${mt}`];
    return Array.isArray(v) && v.length && typeof v[0] === 'string' ? (v[0] as string) : fallback;
  };
  const setPreferredSource = (mt: string, key: string) => {
    const sk = `matching.provider_order.${mt}`;
    setRawSettings(s => ({ ...s, [sk]: [key] }));
    trackSave(api.putSettings({ [sk]: [key] }))
      .catch(e => pushToast({ title: 'Save failed', sub: (e as Error).message, kind: 'error' }));
  };

  // Auto-save whenever any persisted setting changes (after initial hydrate).
  useEffect(() => {
    if (!loaded) return;
    const handle = setTimeout(() => {
      trackSave(api.putSettings({
        'naming.profile': profile,
        'rename.default_op': defaultOp,
        'matching.auto_approve': autoApprove,
        'matching.auto_threshold': autoThreshold,
        'matching.high_threshold': highT,
        'matching.mid_threshold': midT,
      })).then(() => {
        // Let App reload its rename-modal defaults from the saved values.
        window.dispatchEvent(new CustomEvent('kira:settings-saved'));
      }).catch((e) => {
        // Previously swallowed — a failed threshold/profile save was invisible
        // while every other field toasts on failure, so the user thought the
        // change stuck. Surface it consistently.
        pushToast({ title: 'Save failed', sub: (e as Error).message, kind: 'error' });
      });
    }, 500);
    return () => clearTimeout(handle);
  }, [loaded, profile, defaultOp, autoApprove, autoThreshold, highT, midT, pushToast]);

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
        {saveStatus !== 'idle' ? (
          <div
            role="status"
            aria-live="polite"
            className={`inline-flex shrink-0 items-center gap-2 self-center rounded-full border px-3 py-1.5 text-[12px] font-medium ${
              saveStatus === 'error' ? 'border-[rgba(255,91,110,0.4)] text-conf-low'
                : saveStatus === 'saved' ? 'border-accent-line text-accent'
                : 'border-line text-ink-muted'
            }`}
          >
            <span className={`size-1.5 rounded-full ${
              saveStatus === 'error' ? 'bg-conf-low' : saveStatus === 'saved' ? 'bg-accent' : 'bg-ink-soft'
            }`} />
            {saveStatus === 'saving' ? 'Saving…' : saveStatus === 'saved' ? 'Saved' : 'Save failed'}
          </div>
        ) : null}
      </div>

      {/* Section nav lives in the global sidebar (nested under Settings); the
          active section is already labelled there, so no in-page section
          header is needed. Each section width-constrains its own content via
          SettingsLayout so the forms don't stretch edge-to-edge. */}
      <div key={section}>
          {section === 'connections' && (
            <SettingsLayout
              intro="Kira pulls metadata from these providers. Each is configured independently."
              actions={(
                <BadgeWithDot color={connectedCount > 0 ? 'success' : 'gray'}>
                  {connectedCount} of {PROVIDER_KEYS.length} connected
                </BadgeWithDot>
              )}
            >
              {/* Preferred metadata source — which provider IDENTIFIES each
                  media type. Soft preference: the rest stay as fallbacks, so a
                  pick never strands a title the other sources could match. */}
              {/* Two columns: the per-type source preference on the left, the
                  provider connection cards stacked on the right. */}
              <SettingsGrid>
              <SectionCard
                icon={<IcLink />}
                title="Preferred metadata source"
                desc="Which provider identifies each kind of title. Kira tries your pick first and only falls back to the others if it has no confident match. Applies to new scans — re-identify a show or rescan to change matches you already have."
              >
                <div className="flex flex-col gap-5">
                  {[
                    { mt: 'movie', label: 'Movies', cands: ['tmdb', 'tvdb'],
                      hint: 'TMDB carries the richest movie data; TheTVDB is the fallback.' },
                    { mt: 'tv', label: 'TV shows', cands: ['tvdb', 'tmdb'],
                      hint: 'TheTVDB leads for TV seasons; TMDB is the fallback.' },
                    { mt: 'anime', label: 'Anime', cands: ['anidb', 'tvdb'],
                      hint: (<><strong className="text-ink">AniDB</strong> — richest anime metadata + original titles, but splits each cour into its own card. <strong className="text-ink">TheTVDB</strong> — one unified series with seasons + absolute numbering, best Plex / Jellyfin match.</>) },
                  ].map(row => {
                    const cur = preferredSource(row.mt, row.cands[0]);
                    const curInfo = providers[cur];
                    const curNeedsKey = !!curInfo && !curInfo.configured && !curInfo.keyless;
                    return (
                      <SettingRow key={row.mt} layout="stacked" label={row.label} desc={row.hint}>
                        <div className="flex flex-col gap-2">
                          <SegmentedControl
                            fullWidth
                            value={cur}
                            onChange={v => setPreferredSource(row.mt, v)}
                            options={row.cands.map(k => {
                              const info = providers[k];
                              const needsKey = !!info && !info.configured && !info.keyless;
                              return { value: k, label: provName(k) + (needsKey ? ' · needs key' : '') };
                            })}
                          />
                          {curNeedsKey && (
                            <div className="text-[12px] leading-relaxed text-conf-mid">
                              {provName(cur)} isn't configured yet — add its API key below, or Kira falls back to the next source automatically.
                            </div>
                          )}
                        </div>
                      </SettingRow>
                    );
                  })}
                </div>
              </SectionCard>

              {/* Right column: the provider connection cards, stacked. */}
              <div className="flex flex-col gap-3">
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

              <ProviderCard
                providerKey="fanart.tv"
                status={fanartKeySet ? 'connected' : 'not-configured'}
                fields={[
                  { kind: 'text', label: 'API key', value: strSetting(rawSettings, 'providers.fanarttv.api_key'),
                    placeholder: fanartKeySet
                      ? '••••••••••••••••  (key saved — enter a new one to replace)'
                      : 'paste your personal API key from fanart.tv',
                    mono: true,
                    desc: 'Artwork only — clear logos, clear art, banners, disc & character art for the “Download artwork” option (Naming). Free personal key at fanart.tv → log in → API. Anime resolves its artwork via the TheTVDB cross-reference.',
                    onSave: saveKey('providers.fanarttv.api_key') },
                ]}
                onTest={makeTester('fanarttv', pushToast, 'fanart.tv')}
              />
              </div>
              </SettingsGrid>
            </SettingsLayout>
          )}

          {section === 'paths' && (
            <PathsSection rawSettings={rawSettings} setRawSettings={setRawSettings} saveKey={saveKey} pushToast={pushToast} />
          )}

          {section === 'integrations' && (
            <IntegrationsSection rawSettings={rawSettings} setRawSettings={setRawSettings} saveKey={saveKey} pushToast={pushToast} />
          )}

          {section === 'naming' && (
            <SettingsLayout wide intro="Choose how Kira names files and lays them out on disk. Changes apply to new scans.">
              {/* Naming profile + templates */}
              <SectionCard
                icon={<IcTag />}
                title="Naming profile"
                desc="New scans use this profile unless overridden in the rename preview."
              >
                <div className="flex flex-col gap-4">
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
              </SectionCard>

              {/* File handling + Sidecar files — two columns side by side so
                  the wide naming section uses its horizontal space. */}
              <SettingsGrid>
              <SectionCard
                icon={<IcRefresh />}
                title="File handling"
                desc="How renamed files are placed on disk."
              >
                <div className="flex flex-col gap-5">
                  <SettingRow
                    layout="stacked"
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
                  </SettingRow>

                  <SettingRow
                    layout="stacked"
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
                  </SettingRow>

                  <SettingRow
                    layout="stacked"
                    label="Anime episode numbering"
                    desc={<>How anime episodes are numbered in the output. <strong className="text-ink">Seasonal</strong> → <strong className="text-ink">S04E05</strong> inside Season folders. <strong className="text-ink">Absolute</strong> → the series-wide number in a flat folder (e.g. <strong className="text-ink">One Piece - 1156</strong>). Falls back to SxxExx when a show has no absolute number. Applies to new scans / re-renames.</>}
                  >
                    <SegmentedControl
                      fullWidth
                      value={(() => {
                        const v = rawSettings['naming.anime_numbering'];
                        if (typeof v === 'string') return v;
                        if (v && typeof v === 'object' && 'value' in v) return String((v as { value: string }).value);
                        return 'seasonal';
                      })()}
                      onChange={v => saveKey('naming.anime_numbering')(v)}
                      options={[
                        { value: 'seasonal', label: 'Seasonal · S04E05' },
                        { value: 'absolute', label: 'Absolute · 1156' },
                      ]}
                    />
                  </SettingRow>
                </div>
              </SectionCard>

              {/* Sidecar files — NFO + artwork output (right column). */}
              <SectionCard
                icon={<IcTag />}
                title="Sidecar files"
                desc="Optional metadata + artwork written next to each renamed file."
              >
                <div className="flex flex-col gap-5">
                  <SettingRow
                    label="Write .nfo files"
                    desc="Save Kodi / Emby-style metadata sidecars (movie / episode / tvshow .nfo) next to each renamed file. Pure output from the matched metadata — off by default."
                  >
                    <Toggle
                      isSelected={rawSettings['naming.write_nfo'] === true}
                      onChange={() => saveKey('naming.write_nfo')(!(rawSettings['naming.write_nfo'] === true))}
                      aria-label="Write NFO files"
                    />
                  </SettingRow>

                  {/* Per-field NFO picker — only relevant when NFOs are on. */}
                  {rawSettings['naming.write_nfo'] === true && (
                    <NestedBox className="px-3.5 py-3">
                      <div className="mb-3 text-[12.5px] leading-relaxed text-ink-muted">
                        Fields to include in each <span className="font-mono text-ink">.nfo</span>
                        <span className="text-ink-soft"> — title, year, season/episode and provider IDs are always written.</span>
                      </div>
                      <fieldset className="m-0 min-w-0 border-0 p-0">
                      <legend className="sr-only">Fields to include in each .nfo file</legend>
                      <div className="grid grid-cols-1 gap-x-6 gap-y-2.5 sm:grid-cols-2">
                        {NFO_FIELDS.map(f => (
                          <label key={f.key} className="flex cursor-pointer items-center justify-between gap-3">
                            <span className="text-[13px] text-ink">
                              {f.label}
                              {f.hint ? <span className="ml-1.5 text-[11px] text-ink-soft">{f.hint}</span> : null}
                            </span>
                            <Toggle
                              isSelected={nfoFieldOn(f.key)}
                              onChange={() => toggleNfoField(f.key)}
                              aria-label={`Include ${f.label} in NFO`}
                            />
                          </label>
                        ))}
                      </div>
                      </fieldset>
                    </NestedBox>
                  )}

                  <SettingRow
                    label="Download artwork"
                    desc="Save artwork beside each renamed file (Plex / Kodi local-asset convention, e.g. `<name>-poster.jpg`, `<name>-clearlogo.png`). Best-effort, off by default."
                  >
                    <Toggle
                      isSelected={rawSettings['naming.download_artwork'] === true}
                      onChange={() => saveKey('naming.download_artwork')(!(rawSettings['naming.download_artwork'] === true))}
                      aria-label="Download artwork"
                    />
                  </SettingRow>

                  {/* Artwork-type picker + fanart.tv key — only when artwork is on. */}
                  {rawSettings['naming.download_artwork'] === true && (
                    <NestedBox className="px-3.5 py-3">
                      <div className="mb-3 text-[12.5px] leading-relaxed text-ink-muted">
                        Which artwork to save. <span className="text-ink">Poster</span> and{' '}
                        <span className="text-ink">background</span> come from your matched metadata
                        provider; logos, clear art, banners, disc &amp; character art come from{' '}
                        <span className="font-mono text-ink">fanart.tv</span>.
                      </div>
                      <fieldset className="m-0 min-w-0 border-0 p-0">
                      <legend className="sr-only">Artwork types to download</legend>
                      <div className="grid grid-cols-1 gap-x-6 gap-y-2.5 sm:grid-cols-2">
                        {ARTWORK_KINDS.map(a => (
                          <label key={a.key} className="flex cursor-pointer items-center justify-between gap-3">
                            <span className="text-[13px] text-ink">
                              {a.label}
                              {a.hint ? <span className="ml-1.5 text-[11px] text-ink-soft">{a.hint}</span> : null}
                              {a.fanartOnly && !fanartKeySet ? <span className="ml-1.5 text-[11px] text-conf-mid">needs key</span> : null}
                            </span>
                            <Toggle
                              isSelected={artworkKindOn(a.key)}
                              onChange={() => toggleArtworkKind(a.key)}
                              aria-label={`Download ${a.label}`}
                            />
                          </label>
                        ))}
                      </div>
                      </fieldset>
                      <div className={`mt-3 border-t pt-3 text-[11px] leading-relaxed text-ink-soft ${SETTINGS_DIVIDER}`}>
                        Logos, clear art, banners, disc &amp; character art need a free{' '}
                        <span className="font-mono text-ink">fanart.tv</span> API key — set it in{' '}
                        <button type="button" onClick={() => setSection('connections')} className="font-medium text-info underline underline-offset-2 transition-colors hover:text-ink">Connections</button>.
                      </div>
                    </NestedBox>
                  )}
                </div>
              </SectionCard>
              </SettingsGrid>

              {/* Subtitles — sidecar output written next to each renamed file,
                  so it belongs with the other sidecars (NFO / artwork) rather
                  than under Integrations. */}
              <SubtitlesCard rawSettings={rawSettings} saveKey={saveKey} />

              {/* Folder cleanup breadcrumb — full settings live in their own section. */}
              <SectionCard
                icon={<IcTrash />}
                title="Folder cleanup"
                desc="Empty-folder removal, the Plex / Jellyfin / Kodi artifact sweep, and the deleted-pattern list live in their own section."
                action={(
                  <Button color="secondary" size="sm" iconTrailing={<IcArrowRight className="size-3.5" />} onClick={() => setSection('cleanup')}>
                    Open
                  </Button>
                )}
              />
            </SettingsLayout>
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
            // Recycle/trash instead of permanent delete — defaults OFF
            // (preserves prior hard-delete behavior; opt in for recoverability).
            const trashOn = rawSettings['rename.cleanup_trash'] === true;
            const trashDir = strSetting(rawSettings, 'rename.trash_dir');
            const libRoot = (strSetting(rawSettings, 'paths.library_root') || '/media').replace(/[\\/]+$/, '');
            return (
              <SettingsLayout intro={(
                <>When Kira moves a file into your library the source folder is left behind — often with leftover{' '}
                  <span className="font-mono text-ink">poster.jpg</span> / <span className="font-mono text-ink">tvshow.nfo</span>{' '}
                  files that Plex / Jellyfin / Kodi wrote. Control whether Kira tidies those up.</>
              )}>
                {/* Cleanup toggles */}
                <SectionCard
                  icon={<IcTrash />}
                  title="Source folder cleanup"
                  desc="Applies after a Move only — Copy / Hardlink / Symlink never empty the source."
                >
                  <div className="flex flex-col gap-4">
                    {/* Master toggle */}
                    <SettingRow
                      label="Remove empty folders after Move"
                      desc={<>After a Move, walk up the source's folder chain and <span className="font-mono text-ink">rmdir</span> each level that's now empty. Stops at your Media root — never deletes the library root itself.</>}
                    >
                      <Toggle isSelected={masterOn} onChange={() => saveKey('rename.cleanup_empty_source_dirs')(!masterOn)} className="mt-0.5" aria-label="Remove empty folders after Move" />
                    </SettingRow>

                    {/* Sub-toggle — artifact sweep, dimmed when master is off. */}
                    <NestedBox dimmed={!masterOn}>
                      <SettingRow
                        label="Also delete media-server metadata"
                        desc={<>Sweep known Plex / Jellyfin / Kodi cache files (posters, banners, NFOs, <span className="font-mono text-ink">.actors/</span>, per-episode thumbnails) so the folder can actually be removed. <strong className="text-ink">Disable</strong> for strict “only touch genuinely empty folders” behavior.</>}
                      >
                        <Toggle isSelected={sweepOn} isDisabled={!masterOn} onChange={() => saveKey('rename.cleanup_media_server_artifacts')(!sweepOn)} className="mt-0.5" aria-label="Delete media-server cache files" />
                      </SettingRow>
                    </NestedBox>

                    {/* Sub-toggle — recycle to a trash folder instead of hard
                        delete. Only meaningful when the artifact sweep is on
                        (emptying a folder via rmdir is never data loss). */}
                    <NestedBox dimmed={!masterOn || !sweepOn}>
                      <SettingRow
                        label="Move removed items to a trash folder"
                        desc={<>Instead of permanently deleting swept artifacts, <strong className="text-ink">move them to a trash folder</strong> so a mistaken sweep is recoverable from your file browser. Off → permanent delete. (Kira keeps its own trash because a container has no OS recycle bin.)</>}
                      >
                        <Toggle isSelected={trashOn} isDisabled={!masterOn || !sweepOn} onChange={() => saveKey('rename.cleanup_trash')(!trashOn)} className="mt-0.5" aria-label="Move removed items to a trash folder" />
                      </SettingRow>
                      {trashOn ? (
                        <div className="mt-3">
                          <Input
                            mono
                            spellCheck={false}
                            value={trashDir}
                            placeholder={`${libRoot}/.kira-trash  (default)`}
                            onChange={e => saveKey('rename.trash_dir')(e.target.value)}
                          />
                          <div className="mt-1.5 text-[11px] leading-relaxed text-ink-soft">
                            Leave blank to use the default. Sweep old items yourself whenever you're sure you don't need them.
                          </div>
                        </div>
                      ) : null}
                    </NestedBox>
                  </div>
                </SectionCard>

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
              </SettingsLayout>
            );
          })()}

          {section === 'confidence' && (
            <SettingsLayout intro="Tune how confident a match must be before Kira trusts it.">
              <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-2">
              {/* Auto-approve */}
              <SectionCard
                icon={<IcCheck />}
                title="Auto-approve"
                desc="Matches scoring above the threshold are approved automatically, skipping review."
              >
                <div className="flex flex-col gap-4">
                  <SettingRow label="Enable auto-approve">
                    <Toggle isSelected={autoApprove} onChange={() => setAutoApprove(!autoApprove)} aria-label="Enable auto-approve" />
                  </SettingRow>
                  <NestedBox dimmed={!autoApprove}>
                    <SliderField
                      label="Threshold"
                      min={80}
                      max={100}
                      value={autoThreshold}
                      disabled={!autoApprove}
                      onChange={setAutoThreshold}
                      color="var(--conf-high)"
                      valueLabel={`≥ ${autoThreshold}%`}
                    />
                  </NestedBox>
                </div>
              </SectionCard>

              {/* Confidence thresholds — clamp High >= Med+5 and Med <= High-5
                  so the buckets can't invert and collapse the Mid range. */}
              <SectionCard
                icon={<IcShieldCheck />}
                title="Confidence thresholds"
                desc="Where the green / amber / red cutoffs sit for the match badges."
              >
                <div className="flex flex-col gap-3.5">
                  <SliderField
                    label="High"
                    dot="var(--conf-high)"
                    min={Math.max(60, midT + 5)}
                    max={100}
                    value={highT}
                    onChange={v => setHighT(Math.min(100, Math.max(midT + 5, v)))}
                    color="var(--conf-high)"
                    valueLabel={`≥ ${highT}%`}
                  />
                  <SliderField
                    label="Med"
                    dot="var(--conf-mid)"
                    min={20}
                    max={Math.min(80, highT - 5)}
                    value={midT}
                    onChange={v => setMidT(Math.max(20, Math.min(highT - 5, v)))}
                    color="var(--conf-mid)"
                    valueLabel={`≥ ${midT}%`}
                  />
                  <div className="flex items-center gap-3">
                    <span className="inline-flex w-20 shrink-0 items-center gap-2 text-[13px] font-medium text-ink">
                      <span className="size-2 rounded-full" style={{ background: 'var(--conf-low)' }} /> Low
                    </span>
                    <span className="flex-1 text-[12px] text-ink-soft">everything below the Med cutoff</span>
                    <span className="w-16 shrink-0 text-right font-mono text-[12.5px] font-semibold text-conf-low">&lt; {midT}%</span>
                  </div>
                </div>
              </SectionCard>
              </div>
            </SettingsLayout>
          )}

          {section === 'labs' && (() => {
            const mediaInfoOn = rawSettings['parsing.read_mediainfo'] === true;
            const boostOn = rawSettings['labs.episode_title_boost'] === true;
            const runtimeOn = rawSettings['labs.runtime_corroboration'] === true;
            return (
              <SettingsLayout intro="Experimental and cost-bearing options — all off by default. Each note says exactly what it trades, so you can turn on only what's worth it for your setup.">
                <SectionCard
                  icon={<IcSettings />}
                  title="Labs"
                  desc="These touch scan speed or matching accuracy. Changes apply to the next scan / match."
                >
                  <div className="flex flex-col gap-5">
                    <SettingRow
                      label={<span className="flex items-center gap-2">Episode-title series boost <LabsChip>Experimental</LabsChip></span>}
                      desc="When two same-titled shows tie, prefer the one whose episode list contains the filename's episode title. Bounded and TVDB / TMDB-only (never the rate-limited AniDB), so it can't stall scans. Mainly helps western TV with name collisions."
                    >
                      <Toggle isSelected={boostOn} onChange={() => saveKey('labs.episode_title_boost')(!boostOn)} aria-label="Episode-title series boost" />
                    </SettingRow>

                    <SettingRow
                      label={<span className="flex items-center gap-2">Runtime corroboration <LabsChip>Needs MediaInfo</LabsChip></span>}
                      desc={<>Nudge confidence up when the file's real duration matches the episode / movie runtime. Small effect, and only does anything once <strong className="text-ink">Read file metadata</strong> is enabled in <button type="button" onClick={() => setSection('advanced')} className="font-medium text-info underline underline-offset-2 transition-colors hover:text-ink">Advanced</button> (it needs the file's duration).</>}
                    >
                      <Toggle isSelected={runtimeOn} onChange={() => saveKey('labs.runtime_corroboration')(!runtimeOn)} aria-label="Runtime corroboration" />
                    </SettingRow>

                    {!mediaInfoOn && runtimeOn ? (
                      <div className="text-[12px] leading-relaxed text-conf-mid">
                        Runtime corroboration is on but file metadata isn't being read — enable <strong className="text-ink">Read file metadata</strong> in Advanced for this to have any effect.
                      </div>
                    ) : null}
                  </div>
                </SectionCard>
              </SettingsLayout>
            );
          })()}

          {section === 'advanced' && (
            <AdvancedSection rawSettings={rawSettings} saveKey={saveKey} pushToast={pushToast} />
          )}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Paths section — folder picker + watch folder list
// ─────────────────────────────────────────────────────────────────────

// `number` is needed for numeric settings like the Sonarr quality-profile id;
// the value is JSON-serialized to the settings API, which accepts all three.

// Reusable trailing icon-button style for path fields (browse / clear).
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


// ─────────────────────────────────────────────────────────────────────
// Advanced section — retention, concurrency, danger zone
// ─────────────────────────────────────────────────────────────────────

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

