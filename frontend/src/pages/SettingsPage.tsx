import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import type { AppState, ToastData } from '../lib/types';
import type { SettingsSection } from '../App';
import { IcTrash, IcCheck, IcRefresh, IcTag, IcArrowRight } from '../lib/icons';
import { SegmentedControl } from '../components/base/segmented/segmented-control';
import { ProviderCard, NamingTemplateTabs, SETTINGS_CARD, SETTINGS_NESTED, SETTINGS_DIVIDER, SettingsLayout, SectionCard, SettingRow, NestedBox, SliderField, SectionHeader, SettingsFilter, StatusPill } from '../components/settings-blocks';
import { Button } from '../components/base/buttons/button';
import { FeaturedIcon } from '../components/base/featured-icons/featured-icon';
import { Toggle } from '../components/base/toggle/toggle';
import { Input } from '../components/base/input/input';
import { IcShieldCheck, IcChevDown, IcLink, IcSearch, IcHistory, IcUndo, IcFolder, IcSpin, IcCaption } from '../lib/icons';
import { Select } from '../components/ui';
import { api, type ApiProvider } from '../lib/api';
import { strSetting } from './settings/helpers';
import { AdvancedSection } from './settings/AdvancedSection';
import { PathsSection } from './settings/PathsSection';
import { SubtitlesCard } from './settings/SubtitlesCard';
import { IntegrationsSection } from './settings/IntegrationsSection';
import { FolderPickerModal } from '../components/FolderPickerModal';

// Optional NFO fields the user can include/exclude (Settings → Naming).
// Keys MUST match the backend's NFO_TOGGLEABLE (kira/renamer/nfo.py) and the
// `naming.nfo_fields` setting dict. Structural identity — title, year,
// season/episode, provider <uniqueid> — is always written and not listed here.
// `targets` records which of the three .nfo files each field actually lands in
// (verified against backend kira/renamer/nfo.py builders). Presentation only —
// it drives the per-row M·S·E indicator so users can see at a glance that most
// fields are series/movie-level and never reach an episode file.
type NfoTarget = 'movie' | 'series' | 'episode';
const NFO_FIELDS: { key: string; label: string; hint?: string; targets: NfoTarget[] }[] = [
  { key: 'plot',          label: 'Plot / overview',                              targets: ['movie', 'series', 'episode'] },
  { key: 'genres',        label: 'Genres',                                       targets: ['movie', 'series'] },
  { key: 'cast',          label: 'Cast',                                         targets: ['movie', 'series'] },
  { key: 'director',      label: 'Director',                                     targets: ['movie'] },
  { key: 'studio',        label: 'Studio / network',                             targets: ['movie', 'series'] },
  { key: 'runtime',       label: 'Runtime',                                      targets: ['movie', 'episode'] },
  { key: 'country',       label: 'Country',                                      targets: ['movie', 'series'] },
  { key: 'originaltitle', label: 'Original title', hint: 'native / romaji',      targets: ['movie', 'series'] },
  { key: 'artwork',       label: 'Artwork URLs',   hint: 'poster + fanart',      targets: ['movie', 'series'] },
  { key: 'seasonposters', label: 'Season posters', hint: 'anime cours · Kodi',   targets: ['series'] },
  { key: 'collection',    label: 'Collection set', hint: 'movies',               targets: ['movie'] },
  { key: 'status',        label: 'Status',         hint: 'TV · Continuing / Ended', targets: ['series'] },
  { key: 'showtitle',     label: 'Show title',     hint: 'episodes',             targets: ['episode'] },
  { key: 'streamdetails', label: 'Stream details', hint: 'codec · HDR · audio',  targets: ['movie', 'episode'] },
];

// Renders the compact M·S·E indicator on each NFO field row. Filled (accent)
// dots = the field lands in that file; dim outline = it doesn't. The title
// attribute spells out the applicable files for hover/screen-reader users.
const NFO_TARGET_META: { t: NfoTarget; letter: string; name: string }[] = [
  { t: 'movie',   letter: 'M', name: 'Movie .nfo' },
  { t: 'series',  letter: 'S', name: 'Series tvshow.nfo' },
  { t: 'episode', letter: 'E', name: 'Episode .nfo' },
];
function NfoTargetDots({ targets, label }: { targets: NfoTarget[]; label: string }) {
  const applies = NFO_TARGET_META.filter(m => targets.includes(m.t)).map(m => m.name);
  const title = `${label} is written into: ${applies.join(', ')}`;
  return (
    <span
      className="flex shrink-0 items-center gap-1 font-mono text-[10px] font-semibold leading-none"
      title={title}
      aria-label={title}
    >
      {NFO_TARGET_META.map((m, i) => {
        const on = targets.includes(m.t);
        return (
          <span key={m.t} className="flex items-center gap-1" aria-hidden>
            {i > 0 && <span className="text-ink-faint">·</span>}
            <span
              className={
                on
                  ? 'grid size-[15px] place-items-center rounded-[4px] bg-accent-soft text-accent ring-1 ring-inset ring-accent-line'
                  : 'grid size-[15px] place-items-center rounded-[4px] text-ink-faint ring-1 ring-inset ring-line'
              }
            >
              {m.letter}
            </span>
          </span>
        );
      })}
    </span>
  );
}

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

// Stable deep-equality for the settings DRAFT vs the last-saved BASELINE — the
// basis for "which keys are unsaved". Sorts object keys so a rebuilt-but-
// identical dict (e.g. naming.nfo_fields) never falsely reads as changed.
function stableStringify(v: unknown): string {
  if (v === null || typeof v !== 'object') return JSON.stringify(v ?? null) ?? 'null';
  if (Array.isArray(v)) return '[' + v.map(stableStringify).join(',') + ']';
  const o = v as Record<string, unknown>;
  return '{' + Object.keys(o).sort().map(k => JSON.stringify(k) + ':' + stableStringify(o[k])).join(',') + '}';
}
const settingsEqual = (a: unknown, b: unknown) => stableStringify(a) === stableStringify(b);

interface Props {
  pushToast: (t: Omit<ToastData, 'id'>) => void;
  state: AppState;
  /** Active sub-section — now driven by the nested sidebar nav (App owns it). */
  section: SettingsSection;
  setSection: (s: SettingsSection) => void;
  /** Report unsaved-draft state up to App so it can guard navigation away. */
  onDirtyChange?: (dirty: boolean) => void;
}

// Real provider test handler — returns a callback that hits the backend.
// Resolves `true` on a verified connection so ProviderCard can fire its
// success pulse. The toast behaviour (success / error) is unchanged; the
// boolean is purely additive and ignored by any caller that doesn't need it.
function makeTester(slug: string, pushToast: Props['pushToast'], displayName: string) {
  return async (): Promise<boolean> => {
    try {
      const res = await api.testProvider(slug);
      if (res.ok) {
        pushToast({ title: `${displayName} verified`, sub: `${res.latency_ms ?? '—'} ms`, kind: 'success' });
        return true;
      }
      pushToast({ title: `${displayName} test failed`, sub: res.detail ?? undefined, kind: 'error' });
      return false;
    } catch (e) {
      pushToast({ title: `${displayName} test failed`, sub: (e as Error).message, kind: 'error' });
      return false;
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

export function SettingsPage({ pushToast, section, setSection, onDirtyChange }: Props) {
  const [profile, setProfile] = useState('Plex');
  const [defaultOp, setDefaultOp] = useState('hardlink');
  // Default OFF — must match the backend (`_read_auto_approve_setting`): a fresh
  // DB must not auto-approve a scanned library before the user reviews it.
  const [autoApprove, setAutoApprove] = useState(false);
  const [autoThreshold, setAutoThreshold] = useState(95);
  const [highT, setHighT] = useState(85);
  const [midT, setMidT] = useState(50);
  // All backend settings as a flat dict — read-through for provider keys etc.
  // This is now the editable DRAFT: every control writes here, nothing is
  // persisted until Save. `baseline` is the last-saved snapshot we diff against
  // to know what's unsaved (and revert to on Cancel).
  const [rawSettings, setRawSettings] = useState<Record<string, unknown>>({});
  const [baseline, setBaseline] = useState<Record<string, unknown>>({});
  const [loaded, setLoaded] = useState(false);
  // F-05 / F-06: live provider catalog from /providers, keyed by slug.
  // Drives Connected / Not configured / Coming soon labels per block.
  const [providers, setProviders] = useState<Record<string, ApiProvider>>({});

  // Hydrate from backend on mount.
  useEffect(() => {
    // The six control-backed keys live in their own state with these defaults.
    // Seed BOTH draft and baseline with their effective (loaded-or-default)
    // values so the mirror-into-draft effect below never reads as "unsaved" on
    // a fresh / partial load (a brand-new DB has none of them persisted yet).
    const SIX_DEFAULTS: Record<string, unknown> = {
      'naming.profile': 'Plex',
      'rename.default_op': 'hardlink',
      'matching.auto_approve': false,
      'matching.auto_threshold': 95,
      'matching.high_threshold': 85,
      'matching.mid_threshold': 50,
    };
    api.getSettings()
      .then(s => {
        const seeded = { ...SIX_DEFAULTS, ...s };
        setRawSettings(seeded);
        setBaseline(seeded);
        if (typeof s['naming.profile'] === 'string') setProfile(s['naming.profile'] as string);
        if (typeof s['rename.default_op'] === 'string') setDefaultOp(s['rename.default_op'] as string);
        if (typeof s['matching.auto_approve'] === 'boolean') setAutoApprove(s['matching.auto_approve'] as boolean);
        if (typeof s['matching.auto_threshold'] === 'number') setAutoThreshold(s['matching.auto_threshold'] as number);
        if (typeof s['matching.high_threshold'] === 'number') setHighT(s['matching.high_threshold'] as number);
        if (typeof s['matching.mid_threshold'] === 'number') setMidT(s['matching.mid_threshold'] as number);
      })
      .catch(() => { setRawSettings(SIX_DEFAULTS); setBaseline(SIX_DEFAULTS); })
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

  // DRAFT writer — every control routes here (directly or via the sub-sections).
  // It updates local state ONLY; the PUT happens once, on Save (commit), so
  // nothing persists without an explicit click — and browser autofill can no
  // longer silently overwrite a saved API key / token.
  const saveKey = (key: string) => (value: string | number | boolean) => {
    setRawSettings(s => ({ ...s, [key]: value }));
  };

  // Custom naming templates persist as a single JSON object under
  // `naming.custom.Custom` (the shape the backend's _resolve_profile reads at
  // rename time). Edits optimistically update rawSettings so the editor stays
  // in sync, then debounce the PUT so we don't hammer the backend per keystroke.
  const saveCustomTemplates = (dict: Record<string, string>) => {
    setRawSettings(s => ({ ...s, 'naming.custom.Custom': dict }));
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

  // OpenSubtitles — subtitle provider. Same masked-secret semantics as
  // fanart: the server doesn't echo saved keys back, so "set" is the signal.
  const keyIsSet = (settingKey: string) => {
    const raw = rawSettings[settingKey];
    return (!!raw && typeof raw === 'object' && (raw as { set?: boolean }).set === true)
      || (typeof raw === 'string' && raw.length > 0);
  };
  const osKeySet = keyIsSet('providers.opensubtitles.api_key');
  const subdlKeySet = keyIsSet('providers.subdl.api_key');
  const subsourceKeySet = keyIsSet('providers.subsource.api_key');

  // Music providers (MusicBrainz / AcoustID) are not implemented yet — NO
  // backend code reads `providers.musicbrainz.*` / `providers.acoustid.*`, so
  // their Connections cards collected credentials that did nothing (a setting
  // that lies). Hide them until music matching lands; flip to `true` to restore
  // both cards verbatim in one line.
  const MUSIC_PROVIDERS_ENABLED = false;

  // ── Provider preference (per media type) ─────────────────────────────────
  // Stored as `matching.provider_order.<type>` = the FULL ordered list of
  // provider keys. The backend's resolve_provider_order() tries them in this
  // order and SOFT-appends any omitted default as a trailing fallback, so a
  // reorder is a preference — never a hard exclude that could strand a title.
  const provName = (k: string) =>
    k === 'anidb' ? 'AniDB' : k === 'tvdb' ? 'TheTVDB' : k === 'tmdb' ? 'TMDB' : k;
  // Every candidate provider per type, in built-in default order. The reorder
  // UI shows ALL of them; the saved value just permutes this set.
  const PROVIDER_CANDS: Record<string, string[]> = {
    movie: ['tmdb', 'tvdb'],
    tv: ['tvdb', 'tmdb'],
    anime: ['anidb', 'tvdb', 'tmdb'],
  };
  // Current order: saved picks first (filtered to known cands), then any
  // omitted cands appended — mirrors the backend so the UI always lists every
  // provider exactly once even from a partial saved value.
  const providerOrder = (mt: string): string[] => {
    const cands = PROVIDER_CANDS[mt] ?? [];
    const v = rawSettings[`matching.provider_order.${mt}`];
    const saved = Array.isArray(v)
      ? v.filter((x): x is string => typeof x === 'string' && cands.includes(x))
      : [];
    return [...saved, ...cands.filter(c => !saved.includes(c))];
  };
  const setProviderOrder = (mt: string, order: string[]) => {
    const sk = `matching.provider_order.${mt}`;
    setRawSettings(s => ({ ...s, [sk]: order }));
  };
  const moveProvider = (mt: string, idx: number, dir: -1 | 1) => {
    const order = providerOrder(mt);
    const j = idx + dir;
    if (j < 0 || j >= order.length) return;
    [order[idx], order[j]] = [order[j], order[idx]];
    setProviderOrder(mt, order);
  };

  // Anime cross-ref enrichment source (episode titles + NFO cast/studio).
  // `matching.anime_crossref_order` — only TVDB/TMDB carry the Fribb cross-ref,
  // so the UI is a single primary pick; the backend soft-appends the other.
  const animeCrossref = (): string => {
    const v = rawSettings['matching.anime_crossref_order'];
    return Array.isArray(v) && v.length && typeof v[0] === 'string' ? (v[0] as string) : 'tvdb';
  };
  const setAnimeCrossref = (key: string) => {
    const sk = 'matching.anime_crossref_order';
    const order = key === 'tmdb' ? ['tmdb', 'tvdb'] : ['tvdb', 'tmdb'];
    setRawSettings(s => ({ ...s, [sk]: order }));
  };

  // These six live in their own state (bound to controls / clamps), so MIRROR
  // them into the draft whenever they change — no PUT. That way the unified
  // dirty-diff + Save below picks them up exactly like every saveKey field.
  useEffect(() => {
    if (!loaded) return;
    setRawSettings(s => ({
      ...s,
      'naming.profile': profile,
      'rename.default_op': defaultOp,
      'matching.auto_approve': autoApprove,
      'matching.auto_threshold': autoThreshold,
      'matching.high_threshold': highT,
      'matching.mid_threshold': midT,
    }));
  }, [loaded, profile, defaultOp, autoApprove, autoThreshold, highT, midT]);

  // ── Unsaved-changes model ────────────────────────────────────────────────
  // Dirty = keys whose draft value differs from the last-saved baseline. Save
  // PUTs only those; Cancel reverts the draft (and the mirrored controls).
  const dirtyKeys = useMemo(
    () => Object.keys(rawSettings).filter(k => !settingsEqual(rawSettings[k], baseline[k])),
    [rawSettings, baseline],
  );
  const dirty = dirtyKeys.length > 0;

  // Bumped on discard() to force-remount the subtrees that hold seed-once LOCAL
  // state (ProviderField API-key inputs, the NamingTemplateTabs editor), so
  // Cancel fully reverts them to the restored draft. Without it those inputs
  // keep stale typed text — which then silently re-commits on the next keystroke.
  const [discardNonce, setDiscardNonce] = useState(0);

  const commit = () => {
    if (!dirty) return;
    const patch: Record<string, unknown> = {};
    for (const k of dirtyKeys) patch[k] = rawSettings[k];
    trackSave(api.putSettings(patch))
      .then(() => {
        setBaseline(b => ({ ...b, ...patch }));
        // Let App reload its rename-modal defaults / confidence bands.
        window.dispatchEvent(new CustomEvent('kira:settings-saved'));
      })
      .catch(e => pushToast({ title: 'Save failed', sub: (e as Error).message, kind: 'error' }));
  };

  const discard = () => {
    setRawSettings(baseline);
    setProfile(typeof baseline['naming.profile'] === 'string' ? baseline['naming.profile'] as string : 'Plex');
    setDefaultOp(typeof baseline['rename.default_op'] === 'string' ? baseline['rename.default_op'] as string : 'hardlink');
    setAutoApprove(baseline['matching.auto_approve'] === true);
    setAutoThreshold(typeof baseline['matching.auto_threshold'] === 'number' ? baseline['matching.auto_threshold'] as number : 95);
    setHighT(typeof baseline['matching.high_threshold'] === 'number' ? baseline['matching.high_threshold'] as number : 85);
    setMidT(typeof baseline['matching.mid_threshold'] === 'number' ? baseline['matching.mid_threshold'] as number : 50);
    // Remount the seed-once subtrees (provider key inputs, template editor) so
    // their local state re-seeds from the just-restored draft instead of keeping
    // the user's now-cancelled edits.
    setDiscardNonce(n => n + 1);
  };

  // Surface dirty state to App (navigation guard) + warn on tab close / refresh.
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);
  useEffect(() => {
    if (!dirty) return;
    const h = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ''; };
    window.addEventListener('beforeunload', h);
    return () => window.removeEventListener('beforeunload', h);
  }, [dirty]);

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

  // Per-section settings filter. Local + cosmetic only — it stamps the
  // active section's `.settings-stage` with a query that CSS uses to dim/hide
  // non-matching SettingRows. Reset whenever the section changes so a stale
  // query never hides a freshly-opened section. NEVER touches save plumbing.
  const [filter, setFilter] = useState('');
  useEffect(() => { setFilter(''); }, [section]);
  const filterQ = filter.trim().toLowerCase();
  // The per-section filter input is wired into the Naming section header (the
  // densest section). Other sections keep their card rhythm; `filter` resets
  // on section change so a stale query never carries across.

  // Apply the filter by toggling a hidden/match class on each `.setting-row`
  // in the active section (rows stamp their searchable text in `data-search`).
  // A small DOM pass keyed on (section, query) — far simpler than threading a
  // query prop through every sub-page's SettingsLayout, and entirely
  // presentational. Cards with zero visible rows collapse via CSS.
  const sectionRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const root = sectionRef.current;
    if (!root) return;
    const rows = root.querySelectorAll<HTMLElement>('.setting-row[data-search]');
    rows.forEach(row => {
      const hay = row.dataset.search ?? '';
      const hit = !filterQ || hay.includes(filterQ);
      row.classList.toggle('setting-row-hidden', !hit);
      row.classList.toggle('setting-row-hit', !!filterQ && hit);
    });
    // Deps are ONLY the query + section: each row re-stamps its own `data-search`
    // on its own re-render, so we don't need to re-run this DOM pass on every
    // `rawSettings`/`providers` change (i.e. on every keystroke in any field).
  }, [filterQ, section]);

  return (
    <div className="page relative">
      {/* Plain page header — matches History/Dashboard (no boxed card). */}
      <div className="page-header">
        <div>
          <h1 className="page-title">Settings</h1>
          <p className="page-sub">Configure Kira · review your changes, then Save</p>
        </div>
        {saveStatus !== 'idle' ? (
          <div
            role="status"
            aria-live="polite"
            className={`save-indicator inline-flex shrink-0 items-center gap-2 self-center rounded-full border px-3 py-1.5 text-[12px] font-medium ${
              saveStatus === 'error' ? 'save-indicator-error border-[rgba(255,91,110,0.4)] text-conf-low'
                : saveStatus === 'saved' ? 'save-indicator-saved border-accent-line text-accent'
                : 'border-line text-ink-muted'
            }`}
          >
            <span className={`size-1.5 rounded-full ${
              saveStatus === 'saving' ? 'save-indicator-spin bg-ink-soft'
                : saveStatus === 'error' ? 'bg-conf-low' : saveStatus === 'saved' ? 'bg-accent' : 'bg-ink-soft'
            }`} />
            {saveStatus === 'saving' ? 'Saving…' : saveStatus === 'saved' ? 'Saved' : 'Save failed'}
          </div>
        ) : null}
      </div>

      {/* Section nav lives in the global sidebar (nested under Settings); the
          active section is already labelled there, so no in-page section
          header is needed. Each section width-constrains its own content via
          SettingsLayout so the forms don't stretch edge-to-edge. */}
      <div key={section} ref={sectionRef} className={filterQ ? 'settings-filtering' : undefined}>
          {section === 'connections' && (
            <SettingsLayout
              header={(
                <SectionHeader
                  icon={<IcLink />}
                  title="Connections"
                  purpose="Metadata sources Kira pulls FROM to identify your media. Each provider is configured independently."
                  status={(
                    <StatusPill tone={connectedCount > 0 ? 'connected' : 'neutral'} breathe={connectedCount > 0}>
                      {connectedCount} of {PROVIDER_KEYS.length} connected
                    </StatusPill>
                  )}
                />
              )}
            >
              {/* Preferred metadata source + anime cross-ref live in the
                  Matching section now (Identification band) — Connections is
                  credentials only. */}

              {/* The provider list — two INDEPENDENT columns on wide viewports
                  (identification sources left, subtitle/artwork sources right).
                  Flex columns, NOT a grid: expanding one card grows only its own
                  column instead of stretching a shared grid row and leaving dead
                  space beside its collapsed neighbour. */}
              <div key={`conn-${discardNonce}`} className="flex flex-col gap-2.5 xl:flex-row xl:items-start">
              <div className="flex min-w-0 flex-1 flex-col gap-2.5">
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
                  { kind: 'select', label: 'Search language', value: strSetting(rawSettings, 'providers.tvdb.language') || 'English',
                    options: ['English', 'Français', 'Deutsch', 'Español', 'Italiano', '日本語'],
                    desc: 'Language for TVDB search result names. Leave on English unless your library titles are in another language.',
                    onSave: saveKey('providers.tvdb.language') },
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

              {MUSIC_PROVIDERS_ENABLED && (<>
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
                    placeholder: keyIsSet('providers.acoustid.api_key')
                      ? '••••••••••••••••  (key saved — enter a new one to replace)'
                      : 'paste your acoustid.org API key', mono: true,
                    desc: 'Used for audio-fingerprint matching when filename metadata is missing. Get a free key at acoustid.org.',
                    onSave: saveKey('providers.acoustid.api_key') },
                  { kind: 'toggle', label: 'Auto-fingerprint untagged files',
                    value: rawSettings['providers.acoustid.auto_fingerprint'] === true ? 'true' : 'false',
                    desc: 'When enabled, music files without ID3 tags are fingerprinted and matched automatically.',
                    disabled: !keyIsSet('providers.acoustid.api_key'),
                    disabledReason: 'Add an AcoustID API key above to enable fingerprint matching.',
                    onSave: saveKey('providers.acoustid.auto_fingerprint') },
                ]}
                onTest={makeTester('acoustid', pushToast, 'AcoustID')}
              />
              </>)}
              </div>

              {/* Right column — artwork + subtitle sources. */}
              <div className="flex min-w-0 flex-1 flex-col gap-2.5">
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
                  { kind: 'text', label: 'Client key (optional)', value: strSetting(rawSettings, 'providers.fanarttv.client_key'),
                    placeholder: 'project client key — higher rate limits',
                    mono: true,
                    desc: 'A registered project key on top of the personal key lifts the fanart.tv rate limits and surfaces new artwork sooner. Leave blank to use the personal key alone.',
                    onSave: saveKey('providers.fanarttv.client_key') },
                ]}
                onTest={makeTester('fanarttv', pushToast, 'fanart.tv')}
              />

              <ProviderCard
                providerKey="OpenSubtitles"
                status={osKeySet ? 'connected' : 'not-configured'}
                fields={[
                  { kind: 'text', label: 'API key', value: strSetting(rawSettings, 'providers.opensubtitles.api_key'),
                    placeholder: osKeySet
                      ? '••••••••••••••••  (key saved — enter a new one to replace)'
                      : 'paste the 32-char key from opensubtitles.com → API consumers',
                    mono: true,
                    desc: 'Enables subtitle SEARCH. Free key: opensubtitles.com → log in → API consumers.',
                    onSave: saveKey('providers.opensubtitles.api_key') },
                  { kind: 'text', label: 'Username', value: strSetting(rawSettings, 'providers.opensubtitles.username'),
                    placeholder: 'your opensubtitles.com account',
                    desc: 'Downloads count against your account quota — without a login, search works but nothing can be saved.',
                    onSave: saveKey('providers.opensubtitles.username') },
                  { kind: 'password', label: 'Password', value: strSetting(rawSettings, 'providers.opensubtitles.password'),
                    placeholder: '••••••••',
                    onSave: saveKey('providers.opensubtitles.password') },
                ]}
                onTest={makeTester('opensubtitles', pushToast, 'OpenSubtitles')}
              />

              <ProviderCard
                providerKey="SubDL"
                status={subdlKeySet ? 'connected' : 'not-configured'}
                fields={[
                  { kind: 'text', label: 'API key', value: strSetting(rawSettings, 'providers.subdl.api_key'),
                    placeholder: subdlKeySet
                      ? '••••••••••••••••  (key saved — enter a new one to replace)'
                      : 'paste your SubDL API key',
                    mono: true,
                    desc: 'Subtitle source. Free key at subdl.com → panel → API. Enable it in Settings → Subtitles.',
                    onSave: saveKey('providers.subdl.api_key') },
                ]}
                onTest={makeTester('subdl', pushToast, 'SubDL')}
              />

              <ProviderCard
                providerKey="SubSource"
                status={subsourceKeySet ? 'connected' : 'not-configured'}
                fields={[
                  { kind: 'text', label: 'API key', value: strSetting(rawSettings, 'providers.subsource.api_key'),
                    placeholder: subsourceKeySet
                      ? '••••••••••••••••  (key saved — enter a new one to replace)'
                      : 'paste your SubSource API key',
                    mono: true,
                    desc: "Subtitle source (Subscene's successor). Free key from your subsource.net profile. Enable it in Settings → Subtitles.",
                    onSave: saveKey('providers.subsource.api_key') },
                ]}
                onTest={makeTester('subsource', pushToast, 'SubSource')}
              />
              </div>
              </div>
            </SettingsLayout>
          )}

          {section === 'paths' && (
            <PathsSection rawSettings={rawSettings} setRawSettings={setRawSettings} saveKey={saveKey} />
          )}

          {section === 'integrations' && (
            <IntegrationsSection rawSettings={rawSettings} setRawSettings={setRawSettings} saveKey={saveKey} pushToast={pushToast} />
          )}

          {section === 'naming' && (
            <SettingsLayout
              wide
              header={(
                <SectionHeader
                  accent
                  icon={<IcTag />}
                  title="Naming"
                  purpose="How Kira names files and lays them out on disk. Pick a profile, tune the template, watch the live preview, then choose the sidecars to write. Applies to new scans."
                  status={<StatusPill tone="accent">{profile} profile</StatusPill>}
                  filter={<SettingsFilter value={filter} onChange={setFilter} />}
                />
              )}
            >
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
                      <NamingTemplateTabs key={`naming-${discardNonce}`} profile={profile} savedCustom={savedCustom} onSaveCustom={saveCustomTemplates} />
                    </div>
                  </div>
                </div>
              </SectionCard>

              {/* Two height-packed columns: left = file ops (File handling +
                  cleanup breadcrumb), right = sidecar output (NFO / artwork +
                  subtitles). Independent flex columns so short cards never
                  leave a hole beside tall neighbours. */}
              <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-2">
              <div className="flex flex-col gap-4">
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
              </div>

              <div className="flex flex-col gap-4">
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
                      <div className="mb-2.5 text-[12.5px] leading-relaxed text-ink-muted">
                        Fields written into the movie <span className="font-mono text-ink">.nfo</span> and series <span className="font-mono text-ink">tvshow.nfo</span>
                        <span className="text-ink-soft"> — episode files stay lean by design (genres, cast, studio, artwork and the like live in the series file, where Plex/Jellyfin read them; episodes get title, plot, aired, runtime + stream details). Title, year, season/episode and provider IDs are always written, and an existing <span className="font-mono">tvshow.nfo</span> from your media server is never overwritten.</span>
                      </div>
                      {/* Legend: decodes the per-field M·S·E dots below. */}
                      <div className={`mb-3 flex flex-wrap items-center gap-x-3 gap-y-1 border-t pt-2.5 text-[11px] leading-none text-ink-soft ${SETTINGS_DIVIDER}`}>
                        <span className="text-ink-muted">Each field lands in:</span>
                        {NFO_TARGET_META.map(m => (
                          <span key={m.t} className="flex items-center gap-1.5">
                            <span className="grid size-[15px] shrink-0 place-items-center rounded-[4px] bg-accent-soft font-mono text-[10px] font-semibold leading-none text-accent ring-1 ring-inset ring-accent-line">{m.letter}</span>
                            {m.name}
                          </span>
                        ))}
                      </div>
                      <fieldset className="m-0 min-w-0 border-0 p-0">
                      <legend className="sr-only">Fields to include in each .nfo file</legend>
                      <div className="grid grid-cols-1 gap-x-6 gap-y-2.5 sm:grid-cols-2">
                        {NFO_FIELDS.map(f => (
                          <label key={f.key} className="flex cursor-pointer items-center justify-between gap-3">
                            <span className="flex min-w-0 items-center gap-2">
                              <span className="truncate text-[13px] text-ink">
                                {f.label}
                                {f.hint ? <span className="ml-1.5 text-[11px] text-ink-soft">{f.hint}</span> : null}
                              </span>
                              <NfoTargetDots targets={f.targets} label={f.label} />
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
                        {ARTWORK_KINDS.map(a => {
                          // A fanart.tv-only kind with no key can't produce anything,
                          // so show it OFF + disabled (not its default-on state — that
                          // read as "on but greyed", which looks active yet does nothing).
                          const blocked = !!a.fanartOnly && !fanartKeySet;
                          return (
                          <label key={a.key} className={`flex items-center justify-between gap-3 ${blocked ? 'cursor-default opacity-60' : 'cursor-pointer'}`}>
                            <span className="text-[13px] text-ink">
                              {a.label}
                              {a.hint ? <span className="ml-1.5 text-[11px] text-ink-soft">{a.hint}</span> : null}
                              {blocked ? <span className="ml-1.5 text-[11px] text-conf-mid">needs key</span> : null}
                            </span>
                            <Toggle
                              isSelected={artworkKindOn(a.key) && !blocked}
                              isDisabled={blocked}
                              onChange={() => toggleArtworkKind(a.key)}
                              aria-label={`Download ${a.label}`}
                            />
                          </label>
                          );
                        })}
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

              {/* Subtitles promoted to its own top-level section (Output band). */}
              </div>
              </div>
            </SettingsLayout>
          )}

          {section === 'subtitles' && (
            <SettingsLayout
              wide
              header={(
                <SectionHeader
                  accent
                  icon={<IcCaption />}
                  title="Subtitles"
                  purpose="Find and write subtitle sidecars next to each renamed file — sources, languages, scoring floors, and upgrades over time. Applies to new scans and backfill."
                  filter={<SettingsFilter value={filter} onChange={setFilter} />}
                />
              )}
            >
              <SubtitlesCard rawSettings={rawSettings} saveKey={saveKey} goToConnections={() => setSection('connections')} />
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
            // User-extendable sweep lists + the aggressive "delete non-video" mode.
            const asStrList = (v: unknown): string[] => Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [];
            const extraNames = asStrList(rawSettings['rename.cleanup_extra_filenames']);
            const extraExts = asStrList(rawSettings['rename.cleanup_extra_extensions']);
            const nonvideoMode = (() => {
              const v = rawSettings['rename.cleanup_nonvideo'];
              const s = typeof v === 'string' ? v : (v && typeof v === 'object' && 'value' in v ? String((v as { value: unknown }).value) : 'off');
              return (['off', 'keep_subs', 'all'] as const).includes(s as 'off') ? s : 'off';
            })();
            return (
              <SettingsLayout
                header={(
                  <SectionHeader
                    icon={<IcTrash />}
                    title="Folder cleanup"
                    purpose={(
                      <>When Kira moves a file into your library the source folder is left behind — often with leftover{' '}
                        <span className="font-mono text-ink">poster.jpg</span> / <span className="font-mono text-ink">tvshow.nfo</span>{' '}
                        files that Plex / Jellyfin / Kodi wrote. Control whether Kira tidies those up.</>
                    )}
                    status={<StatusPill tone={masterOn ? (trashOn ? 'connected' : 'warning') : 'neutral'}>{masterOn ? (trashOn ? 'On · recycle' : sweepOn ? 'On · delete' : 'Empty only') : 'Off'}</StatusPill>}
                  />
                )}
              >
                {/* Toggles left, transparency disclosure right — side by side
                    so the section's two cards share the width. */}
                <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-2">
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

                    {/* Sub-options of the artifact sweep: user-defined names /
                        extensions, and the aggressive "delete non-video" mode. */}
                    <NestedBox dimmed={!masterOn || !sweepOn}>
                      <div className="flex flex-col gap-4">
                        <div>
                          <div className="text-[13px] font-medium text-ink">Also delete these filenames</div>
                          <div className="mt-0.5 mb-2 text-[12px] leading-relaxed text-ink-muted">
                            Extra exact filenames to sweep on top of the built-in list — e.g. <span className="font-mono text-ink">backdrop.jpg</span>, <span className="font-mono text-ink">cover.jpg</span>, <span className="font-mono text-ink">.DS_Store</span>. Comma-separated, case-insensitive.
                          </div>
                          <CommaListField
                            value={extraNames}
                            placeholder="backdrop.jpg, theme.mp3, .DS_Store"
                            disabled={!masterOn || !sweepOn}
                            onSave={v => setRawSettings(s => ({ ...s, 'rename.cleanup_extra_filenames': v }))}
                          />
                        </div>
                        <div>
                          <div className="text-[13px] font-medium text-ink">Also delete these extensions</div>
                          <div className="mt-0.5 mb-2 text-[12px] leading-relaxed text-ink-muted">
                            Any file with one of these extensions is swept regardless of name — e.g. <span className="font-mono text-ink">.txt</span>, <span className="font-mono text-ink">.url</span>, <span className="font-mono text-ink">.nzb</span>. Comma-separated.
                          </div>
                          <CommaListField
                            value={extraExts}
                            placeholder=".txt, .url, .nzb"
                            disabled={!masterOn || !sweepOn}
                            onSave={v => setRawSettings(s => ({ ...s, 'rename.cleanup_extra_extensions': v }))}
                          />
                        </div>
                        <div className={`border-t pt-3.5 ${SETTINGS_DIVIDER}`}>
                          <div className="text-[13px] font-medium text-ink">Delete non-video leftovers</div>
                          <div className="mt-0.5 mb-2.5 text-[12px] leading-relaxed text-ink-muted">
                            When a source folder has <strong className="text-ink">no video files left</strong> after a Move, also remove the other files so the folder can go. Never touches a folder that still holds a video, and never deletes a video.
                          </div>
                          <SegmentedControl
                            fullWidth
                            value={nonvideoMode}
                            onChange={v => {
                              // Arm-gate the irreversible case: "Everything" deletes ALL
                              // non-video files (subtitles included) from any folder a Move
                              // empties of video. With Trash OFF that's a permanent mass
                              // delete of the user's own files — make them confirm before it
                              // even lands in the draft. (Controlled control reverts on cancel
                              // since we skip the saveKey.)
                              if (v === 'all' && !trashOn) {
                                const ok = window.confirm(
                                  'Delete EVERYTHING non-video?\n\n'
                                  + 'With "Move to trash" OFF, this PERMANENTLY deletes every non-video file '
                                  + '(subtitles included) from any folder a Move empties of video. It cannot be undone.\n\n'
                                  + 'Tip: turn on "Move to trash" below first to make it recoverable.\n\nProceed anyway?'
                                );
                                if (!ok) return;
                              }
                              saveKey('rename.cleanup_nonvideo')(v);
                            }}
                            options={[
                              { value: 'off', label: 'Off' },
                              { value: 'keep_subs', label: 'Keep subtitles' },
                              { value: 'all', label: 'Everything' },
                            ]}
                          />
                          {nonvideoMode !== 'off' ? (
                            <div className="mt-2 text-[11.5px] leading-relaxed text-conf-mid">
                              {nonvideoMode === 'all'
                                ? <>Deletes <strong className="text-ink">every</strong> non-video file — subtitles included.</>
                                : <>Deletes non-video files but <strong className="text-ink">keeps subtitle sidecars</strong> (.srt / .ass / …).</>}{' '}
                              {trashOn ? 'Recoverable from your Trash folder.' : <><strong className="text-ink">Permanent</strong> — turn on “Move to trash” below to make it recoverable.</>}
                            </div>
                          ) : null}
                        </div>
                      </div>
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
                          <TrashDirField
                            value={trashDir}
                            placeholder={`${libRoot}/.kira-trash  (default)`}
                            initialBrowse={libRoot}
                            onSave={v => saveKey('rename.trash_dir')(v)}
                          />
                          <div className="mt-1.5 text-[11px] leading-relaxed text-ink-soft">
                            Leave blank to use the default. Browse and restore items in the Trash bin below.
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
                        season01-poster.jpg · season-specials-banner.jpg · Show.S01E01-thumb.jpg · Movie (2023)-poster.jpg · Album-fanart.png · Show.S01E01-fanart-2.jpg · *.tbn (Kodi binary thumbnails) · *.nfo (only once every video has left the folder — an NFO without its video is orphaned metadata)
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

                {/* Trash bin — browse / restore what the sweep recycled. */}
                <TrashBinCard rawSettings={rawSettings} saveKey={saveKey} pushToast={pushToast} />
              </SettingsLayout>
            );
          })()}

          {section === 'matching' && (() => {
            // Experimental-boost flags folded in from the old Labs section.
            const mediaInfoOn = rawSettings['parsing.read_mediainfo'] === true;
            const boostOn = rawSettings['labs.episode_title_boost'] === true;
            const runtimeOn = rawSettings['labs.runtime_corroboration'] === true;
            return (
            <SettingsLayout
              header={(
                <SectionHeader
                  icon={<IcShieldCheck />}
                  title="Matching"
                  purpose="Which sources identify each kind of title, how confident a match must be before Kira trusts it, and the experimental boosts for close calls. Applies to new scans."
                  status={<StatusPill tone={autoApprove ? 'accent' : 'neutral'}>{autoApprove ? `Auto ≥ ${autoThreshold}%` : 'Manual review'}</StatusPill>}
                />
              )}
            >
              {/* Provider preference — moved here from Connections so the whole
                  of matching (sources + confidence + boosts) lives in one home. */}
              <SectionCard
                icon={<IcLink />}
                title="Preferred metadata source"
                desc="Which provider identifies each kind of title. Kira tries your pick first and only falls back to the others if it has no confident match. Applies to new scans — re-identify a show or rescan to change matches you already have."
              >
                <div className="grid gap-5 md:grid-cols-3">
                  {[
                    { mt: 'movie', label: 'Movies',
                      hint: 'TMDB carries the richest movie data; TheTVDB is the fallback. Reorder to taste.' },
                    { mt: 'tv', label: 'TV shows',
                      hint: 'TheTVDB leads for TV seasons; TMDB is the fallback. Reorder to taste.' },
                    { mt: 'anime', label: 'Anime',
                      hint: (<><strong className="text-ink">AniDB</strong> — richest anime metadata + original titles, but splits each cour into its own card. <strong className="text-ink">TheTVDB</strong> — one unified series with seasons, best Plex / Jellyfin match. <strong className="text-ink">TMDB</strong> — broad English coverage.</>) },
                  ].map(row => {
                    const order = providerOrder(row.mt);
                    const primary = order[0];
                    const primaryInfo = providers[primary];
                    const primaryNeedsKey = !!primaryInfo && !primaryInfo.configured && !primaryInfo.keyless;
                    return (
                      <div key={row.mt} className="flex flex-col gap-2">
                        <div className="text-[13px] font-semibold text-ink">{row.label}</div>
                        {/* Reorderable provider list — top = preferred. Kira
                            tries them in this order and keeps any provider you
                            push down as a trailing fallback, so it's a preference,
                            never a hard exclude. */}
                        <ol className="flex flex-col gap-1.5">
                          {order.map((k, i) => {
                            const info = providers[k];
                            const needsKey = !!info && !info.configured && !info.keyless;
                            return (
                              <li key={k} className="flex items-center gap-2 rounded-lg border border-line bg-surface-1 px-2.5 py-1.5">
                                <span className="grid size-5 shrink-0 place-items-center rounded-md bg-surface-2 text-[11px] font-semibold tabular-nums text-ink-muted">{i + 1}</span>
                                <span className="min-w-0 flex-1 truncate text-[13px] text-ink">
                                  {provName(k)}
                                  {i === 0 ? <span className="ml-1.5 text-[11px] font-medium text-accent">primary</span> : null}
                                  {needsKey ? <span className="ml-1.5 text-[11px] text-conf-mid">needs key</span> : null}
                                </span>
                                <span className="flex shrink-0 items-center gap-0.5">
                                  <button
                                    type="button" aria-label={`Move ${provName(k)} up`} disabled={i === 0}
                                    onClick={() => moveProvider(row.mt, i, -1)}
                                    className="grid size-6 place-items-center rounded-md text-ink-soft transition hover:bg-surface-2 hover:text-ink disabled:pointer-events-none disabled:opacity-25"
                                  ><IcChevDown className="size-3.5 rotate-180" /></button>
                                  <button
                                    type="button" aria-label={`Move ${provName(k)} down`} disabled={i === order.length - 1}
                                    onClick={() => moveProvider(row.mt, i, 1)}
                                    className="grid size-6 place-items-center rounded-md text-ink-soft transition hover:bg-surface-2 hover:text-ink disabled:pointer-events-none disabled:opacity-25"
                                  ><IcChevDown className="size-3.5" /></button>
                                </span>
                              </li>
                            );
                          })}
                        </ol>
                        <div className="text-[12px] leading-relaxed text-ink-muted">{row.hint}</div>
                        {primaryNeedsKey && (
                          <div className="text-[12px] leading-relaxed text-conf-mid">
                            {provName(primary)} isn't configured yet — add its API key in Connections, or Kira falls back to the next source automatically.
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>

                {/* Anime enrichment source — AniDB identifies anime but carries
                    sparse episode titles + no cast/studio data. Kira fills those
                    in from this cross-ref source; the other is tried if this one
                    has no Fribb mapping. */}
                <div className="mt-5 flex flex-col gap-2 border-t border-line pt-4">
                  <div className="text-[13px] font-semibold text-ink">Anime episode titles &amp; metadata</div>
                  <div className="max-w-xs">
                    <SegmentedControl
                      fullWidth
                      value={animeCrossref()}
                      onChange={setAnimeCrossref}
                      options={[
                        { value: 'tvdb', label: 'TheTVDB' },
                        { value: 'tmdb', label: 'TMDB' },
                      ]}
                    />
                  </div>
                  <div className="text-[12px] leading-relaxed text-ink-muted">
                    When a title is matched on AniDB, Kira pulls episode names and cast / studio data from this source via the cross-reference. The other is tried automatically if this one has no mapping.
                  </div>
                </div>
              </SectionCard>

              {/* Confidence — auto-approve + where the badge cutoffs sit. */}
              <div className="mt-5 mb-1 text-[13px] font-semibold text-ink">Confidence</div>
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

              {/* Experimental boosts — folded in from the old Labs section. All
                  off by default; each note says exactly what it trades. */}
              <div className="mt-5 border-t border-line pt-4">
                <div className="mb-3 flex items-center gap-2 text-[13px] font-semibold text-ink">
                  Experimental boosts <LabsChip>Off by default</LabsChip>
                </div>
                <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-2">
                  <SectionCard
                    icon={<IcSearch />}
                    title={<span className="flex items-center gap-2">Episode-title series boost <LabsChip>Experimental</LabsChip></span>}
                    desc="When two same-titled shows tie, prefer the one whose episode list contains the filename's episode title. Bounded and TVDB / TMDB-only (never the rate-limited AniDB), so it can't stall scans. Mainly helps western TV with name collisions."
                    headerExtra={<Toggle isSelected={boostOn} onChange={() => saveKey('labs.episode_title_boost')(!boostOn)} className="mt-0.5" aria-label="Episode-title series boost" />}
                  />

                  <SectionCard
                    icon={<IcHistory />}
                    title={<span className="flex items-center gap-2">Runtime corroboration <LabsChip>Needs MediaInfo</LabsChip></span>}
                    desc={<>Nudge confidence up when the file's real duration matches the episode / movie runtime. Small effect, and only does anything once <strong className="text-ink">Read file metadata</strong> is enabled in <button type="button" onClick={() => setSection('advanced')} className="font-medium text-info underline underline-offset-2 transition-colors hover:text-ink">Advanced</button> (it needs the file's duration).</>}
                    headerExtra={<Toggle isSelected={runtimeOn} isDisabled={!mediaInfoOn} onChange={() => saveKey('labs.runtime_corroboration')(!runtimeOn)} className="mt-0.5" aria-label="Runtime corroboration" />}
                  >
                    {!mediaInfoOn ? (
                      <div className="text-[12px] leading-relaxed text-conf-mid">
                        Needs <strong className="text-ink">Read file metadata</strong> (Advanced) — this boost reads the file's real duration. Enable it there to use this.
                      </div>
                    ) : null}
                  </SectionCard>
                </div>
              </div>
            </SettingsLayout>
            );
          })()}

          {section === 'advanced' && (
            <AdvancedSection rawSettings={rawSettings} saveKey={saveKey} pushToast={pushToast} />
          )}
      </div>

      {/* Unsaved-changes bar — nothing on this page persists until Save. Floats
          bottom-centre, above the content, out of the way of the sidebar. */}
      {dirty ? (
        <div className="pointer-events-none fixed inset-x-0 bottom-5 z-40 flex justify-center px-4">
          <div
            role="region"
            aria-label="Unsaved settings"
            className="anim-pop pointer-events-auto flex items-center gap-3 rounded-2xl border border-white/[0.12] bg-[rgba(12,13,17,0.92)] px-4 py-3 shadow-[0_18px_60px_rgba(0,0,0,0.55)] backdrop-blur-2xl"
          >
            <span className="text-[13px] text-ink-muted">
              <span className="font-semibold text-ink">{dirtyKeys.length}</span> unsaved {dirtyKeys.length === 1 ? 'change' : 'changes'}
            </span>
            <Button color="secondary" size="sm" onClick={discard} isDisabled={saveStatus === 'saving'}>
              Cancel
            </Button>
            <Button color="primary" size="sm" onClick={commit} isDisabled={saveStatus === 'saving'}>
              {saveStatus === 'saving' ? 'Saving…' : 'Save changes'}
            </Button>
          </div>
        </div>
      ) : null}
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


// ─────────────────────────────────────────────────────────────────────
// Trash bin — browse / restore / empty Kira's managed trash folder.
// Own component (not inline in the cleanup section) because that section
// renders via an IIFE, which can't hold hooks.
// ─────────────────────────────────────────────────────────────────────

type TrashItem = {
  name: string; is_dir: boolean; size_bytes: number;
  trashed_at: string | null; mtime: number; original: string | null;
};

function fmtBytes(b: number): string {
  if (b >= 1 << 30) return `${(b / (1 << 30)).toFixed(1)} GB`;
  if (b >= 1 << 20) return `${(b / (1 << 20)).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(b / 1024))} KB`;
}

function fmtAge(item: TrashItem): string {
  const t = item.trashed_at ? Date.parse(item.trashed_at) : item.mtime * 1000;
  if (!Number.isFinite(t) || t <= 0) return '';
  const d = Math.floor((Date.now() - t) / 86_400_000);
  if (d <= 0) return 'today';
  if (d === 1) return 'yesterday';
  return `${d}d ago`;
}

// Trash-folder path input with an in-field Browse button — the same
// affordance every other path field has. Own component because the cleanup
// section renders via an IIFE, which can't hold the picker's state.
// Comma-separated editor for an array setting (cleanup custom filenames /
// extensions). Holds an in-progress text buffer so typing commas doesn't fight
// the array round-trip; commits the parsed, de-duped list on blur / Enter.
function CommaListField({ value, placeholder, disabled, onSave }: {
  value: string[];
  placeholder?: string;
  disabled?: boolean;
  onSave: (next: string[]) => void;
}) {
  const [text, setText] = useState<string | null>(null);
  const commit = () => {
    if (text === null) return;
    onSave(Array.from(new Set(text.split(',').map(s => s.trim()).filter(Boolean))));
    setText(null);
  };
  return (
    <Input
      mono
      spellCheck={false}
      value={text ?? value.join(', ')}
      placeholder={placeholder}
      disabled={disabled}
      onChange={e => setText(e.target.value)}
      onBlur={commit}
      onKeyDown={e => { if (e.key === 'Enter') commit(); }}
    />
  );
}

function TrashDirField({ value, placeholder, initialBrowse, onSave }: {
  value: string;
  placeholder: string;
  initialBrowse: string;
  onSave: (v: string) => void;
}) {
  const [picking, setPicking] = useState(false);
  return (
    <>
      <Input
        mono
        spellCheck={false}
        value={value}
        placeholder={placeholder}
        onChange={e => onSave(e.target.value)}
        trailing={
          <button
            type="button"
            title="Browse for folder"
            onClick={() => setPicking(true)}
            className="grid size-7 shrink-0 place-items-center rounded-md text-ink-soft transition-colors hover:bg-glass-2 hover:text-ink [&_svg]:size-[14px]"
          >
            <IcFolder />
          </button>
        }
      />
      {picking ? (
        <FolderPickerModal
          initialPath={value || initialBrowse}
          onPick={path => onSave(path)}
          onClose={() => setPicking(false)}
        />
      ) : null}
    </>
  );
}

export function TrashBinCard({ rawSettings, saveKey, pushToast }: {
  rawSettings: Record<string, unknown>;
  saveKey: (key: string) => (value: string | number | boolean) => void;
  pushToast: (t: Omit<ToastData, 'id'>) => void;
}) {
  const [items, setItems] = useState<TrashItem[]>([]);
  const [totalBytes, setTotalBytes] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [armEmpty, setArmEmpty] = useState(false);
  const [emptying, setEmptying] = useState(false);

  const retentionRaw = rawSettings['rename.trash_retention_days'];
  const retention = typeof retentionRaw === 'number' ? String(retentionRaw)
    : typeof retentionRaw === 'string' && retentionRaw !== '' ? retentionRaw : '0';

  const load = async () => {
    try {
      const r = await api.listTrash();
      setItems(r.items);
      setTotalBytes(r.total_bytes);
    } catch { /* backend offline — card shows empty */ }
    setLoaded(true);
  };
  useEffect(() => { void load(); }, []);

  const doRestore = async (name: string) => {
    setBusy(name);
    try {
      const r = await api.restoreTrashItem(name);
      pushToast({ title: 'Restored', sub: r.to, kind: 'success' });
      await load();
    } catch (e) {
      pushToast({ title: 'Restore failed', sub: (e as Error).message, kind: 'error' });
    }
    setBusy(null);
  };

  const doDelete = async (name: string) => {
    setBusy(name);
    try {
      await api.deleteTrashItem(name);
      await load();
    } catch (e) {
      pushToast({ title: 'Delete failed', sub: (e as Error).message, kind: 'error' });
    }
    setBusy(null);
  };

  const doEmpty = async () => {
    if (!armEmpty) { setArmEmpty(true); setTimeout(() => setArmEmpty(false), 4000); return; }
    setArmEmpty(false);
    setEmptying(true);
    try {
      const r = await api.emptyTrash();
      pushToast({ title: `Trash emptied`, sub: `${r.deleted} item${r.deleted === 1 ? '' : 's'} permanently deleted.`, kind: 'success' });
      await load();
    } catch (e) {
      pushToast({ title: 'Empty failed', sub: (e as Error).message, kind: 'error' });
    }
    setEmptying(false);
  };

  return (
    <SectionCard
      icon={<IcTrash />}
      title="Trash bin"
      desc={items.length > 0
        ? <>{items.length} item{items.length === 1 ? '' : 's'} · {fmtBytes(totalBytes)} — swept artifacts wait here until you restore or remove them.</>
        : 'Items the cleanup sweep recycles land here, ready to restore.'}
      action={items.length > 0 ? (
        <Button
          color={armEmpty ? 'primary-destructive' : 'secondary-destructive'}
          size="sm"
          iconLeading={emptying ? undefined : IcTrash}
          isLoading={emptying}
          isDisabled={emptying}
          showTextWhileLoading
          onClick={() => void doEmpty()}
        >
          {emptying ? 'Emptying…' : armEmpty ? 'Click again to confirm' : 'Empty trash'}
        </Button>
      ) : undefined}
    >
      <div className="flex flex-col gap-3">
        {loaded && items.length === 0 ? (
          <div className="rounded-xl border border-dashed border-white/[0.12] px-3 py-2.5 text-xs text-ink-muted">
            Trash is empty.
          </div>
        ) : null}
        {items.length > 0 ? (
          <div className="flex max-h-[340px] flex-col gap-1.5 overflow-y-auto pr-1 [scrollbar-width:thin]">
            {/* Render cap — a big sweep can trash hundreds of artifacts; the
                full list lives on disk, the UI shows the newest slice. */}
            {items.slice(0, 200).map(it => (
              <div key={it.name} className={`flex items-center gap-2.5 px-3 py-2 ${SETTINGS_NESTED}`}>
                <IcFolder style={{ width: 14, height: 14 }} className={`shrink-0 ${it.is_dir ? 'text-ink-soft' : 'text-ink-faint'}`} />
                <div className="min-w-0 flex-1">
                  <div className="truncate font-mono text-[12px] text-ink-muted" title={it.name}>{it.name}</div>
                  <div className="truncate text-[10.5px] text-ink-faint" title={it.original ?? undefined}>
                    {fmtBytes(it.size_bytes)} · {fmtAge(it)}{it.original ? <> · from <span className="font-mono">{it.original}</span></> : ' · original location unknown'}
                  </div>
                </div>
                <button
                  type="button"
                  title={it.original ? `Restore to ${it.original}` : 'Original location unknown — restore by hand from the trash folder'}
                  disabled={!it.original || busy === it.name}
                  onClick={() => void doRestore(it.name)}
                  className="grid size-7 shrink-0 place-items-center rounded-md text-ink-soft transition-colors hover:bg-glass-2 hover:text-conf-high disabled:cursor-not-allowed disabled:opacity-35 [&_svg]:size-[13px]"
                >
                  {busy === it.name ? <IcSpin className="animate-spin" /> : <IcUndo />}
                </button>
                <button
                  type="button"
                  title="Delete permanently"
                  disabled={busy === it.name}
                  onClick={() => void doDelete(it.name)}
                  className="grid size-7 shrink-0 place-items-center rounded-md text-ink-soft transition-colors hover:bg-[var(--conf-low-bg)] hover:text-conf-low disabled:opacity-35 [&_svg]:size-[13px]"
                >
                  {busy === it.name ? <IcSpin className="animate-spin" /> : <IcTrash />}
                </button>
              </div>
            ))}
            {items.length > 200 ? (
              <div className="px-3 py-2 text-center text-[11px] text-ink-soft">
                …and {items.length - 200} more (newest 200 shown — Empty trash and Auto-purge apply to everything).
              </div>
            ) : null}
          </div>
        ) : null}
        <SettingRow
          label="Auto-purge"
          desc="Permanently remove trashed items after this long. Checked at startup."
        >
          <div className="w-[160px]">
            <Select<string>
              value={retention}
              onChange={v => saveKey('rename.trash_retention_days')(parseInt(v, 10) || 0)}
              options={[
                { value: '0', label: 'Keep forever' },
                { value: '7', label: '7 days' },
                { value: '30', label: '30 days' },
                { value: '90', label: '90 days' },
              ]}
            />
          </div>
        </SettingRow>
      </div>
    </SectionCard>
  );
}
