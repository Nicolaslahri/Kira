import { useState, type ReactNode } from 'react';
import { IcX, IcCheck, IcAlertTri, IcLink, IcRefresh, IcCaption, IcDownload, IcSparkles, IcSearch, IcFilm, IcTv, IcAnime } from '../../lib/icons';
import { Select } from '../../components/ui';
import { SectionCard, SliderField } from '../../components/settings-blocks';
import { FfmpegStatusRow } from '../../components/FfmpegStatus';
import { FeaturedIcon } from '../../components/base/featured-icons/featured-icon';
import { Input } from '../../components/base/input/input';
import { Toggle } from '../../components/base/toggle/toggle';
import { Button } from '../../components/base/buttons/button';
import { api } from '../../lib/api';
import { strSetting, type SaveKeyFn } from './helpers';

// Common subtitle languages for the picker. Stored as a comma-separated code
// list under `subtitles.languages`; a code the user already had that isn't here
// still round-trips (its chip shows the raw code).
const SUBTITLE_LANGUAGES: { code: string; label: string }[] = [
  { code: 'en', label: 'English' },   { code: 'es', label: 'Spanish' },
  { code: 'fr', label: 'French' },    { code: 'de', label: 'German' },
  { code: 'it', label: 'Italian' },   { code: 'pt', label: 'Portuguese' },
  { code: 'nl', label: 'Dutch' },     { code: 'pl', label: 'Polish' },
  { code: 'ru', label: 'Russian' },   { code: 'ja', label: 'Japanese' },
  { code: 'zh', label: 'Chinese' },   { code: 'ko', label: 'Korean' },
  { code: 'ar', label: 'Arabic' },    { code: 'tr', label: 'Turkish' },
  { code: 'sv', label: 'Swedish' },   { code: 'hi', label: 'Hindi' },
];

// Subtitle sources with friendly labels for the per-type override picker. Keys
// MUST match the backend (_ALL_SOURCES in subtitles/prefs.py).
const SUBTITLE_SOURCES: { key: string; label: string }[] = [
  { key: 'embedded', label: 'Embedded' },
  { key: 'opensubtitles', label: 'OpenSubtitles' },
  { key: 'subdl', label: 'SubDL' },
  { key: 'podnapisi', label: 'Podnapisi' },
  { key: 'subsource', label: 'SubSource' },
  { key: 'animetosho', label: 'AnimeTosho' },
  { key: 'yifysubtitles', label: 'YIFY' },
];

// Discoverable multi-select: a dropdown of the remaining options + removable
// chips for the chosen ones. Empty list = "inherit the global value". Shared by
// the per-type language and per-type source overrides so neither is free-text.
/** Canonical removable chip (guidelines §6 count-badge grammar): neutral
 *  bg-tertiary + ring, secondary text; the ✕ turns error on hover. Replaces
 *  the legacy `bg-glass-2 text-ink` chips that sat outside the design system
 *  (flat white text, no hairline — they read unfinished next to UUI controls). */
function RemovableChip({ label, onRemove, removeLabel }: {
  label: ReactNode; onRemove: () => void; removeLabel: string;
}) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-md bg-tertiary py-1 pl-2 pr-1 text-[12px] font-medium text-secondary ring-1 ring-inset ring-secondary">
      {label}
      <button
        type="button"
        onClick={onRemove}
        aria-label={removeLabel}
        className="grid size-4 place-items-center rounded text-tertiary transition-colors hover:bg-error-secondary hover:text-error-primary [&_svg]:size-3"
      >
        <IcX />
      </button>
    </span>
  );
}

function PerTypeChips({ chosen, options, onChange, placeholder }: {
  chosen: string[];
  options: { value: string; label: string }[];
  onChange: (next: string[]) => void;
  placeholder: string;
}) {
  return (
    <div className="flex w-full flex-col gap-1.5">
      <Select
        options={options.filter(o => !chosen.includes(o.value))}
        value={null}
        onChange={(v: string) => { if (!chosen.includes(v)) onChange([...chosen, v]); }}
        placeholder={placeholder}
        style={{ flex: 1, minWidth: 0 }}
      />
      {chosen.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {chosen.map(v => (
            <RemovableChip
              key={v}
              label={options.find(o => o.value === v)?.label ?? v}
              onRemove={() => onChange(chosen.filter(x => x !== v))}
              removeLabel={`Remove ${v}`}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}

// A single source / control row in the cascade + automation cards. Pure chrome:
// the control is passed as children, so no behaviour lives here. `rail` paints
// the tier's left accent; `dim` greys a gated/off row.
function Row({ rail, name, hint, dim, children }: {
  rail?: string;
  name: ReactNode;
  hint?: ReactNode;
  dim?: boolean;
  children?: ReactNode;
}) {
  return (
    <div className={`relative flex items-center justify-between gap-3 overflow-hidden rounded-xl bg-tertiary px-3.5 py-3 ring-1 ring-inset ring-secondary${dim ? ' opacity-60' : ''}`}>
      {rail ? <span aria-hidden className="absolute inset-y-0 left-0 w-0.5" style={{ background: rail }} /> : null}
      <span className="min-w-0 text-[13px] text-primary">
        {name}
        {hint ? <span className="ml-1.5 text-[12px] text-tertiary">{hint}</span> : null}
      </span>
      <div className="flex shrink-0 items-center gap-2">{children}</div>
    </div>
  );
}

const EYEBROW = 'text-[10px] font-semibold uppercase tracking-[0.14em] text-quaternary';

// Subtitles are sidecar output (written next to each renamed file like .nfo and
// artwork). The OpenSubtitles CREDENTIALS live in Connections with every other
// provider key — this card only shows their connection status and links across.
// The page is a cheapest-first CASCADE: embedded (free/offline) → online
// providers → YIFY scraper, each filling the languages the tier above missed.
export function SubtitlesCard({
  rawSettings,
  saveKey,
  goToConnections,
}: {
  rawSettings: Record<string, unknown>;
  saveKey: SaveKeyFn;
  goToConnections?: () => void;
}) {
  // Masked-secret semantics: the server doesn't echo saved keys back, it
  // reports {set: true} — same convention as fanart.tv on the Connections tab.
  const keySet = (key: string) => {
    const raw = rawSettings[key];
    return (!!raw && typeof raw === 'object' && (raw as { set?: boolean }).set === true)
      || (typeof raw === 'string' && (raw as string).length > 0);
  };
  const osKeySet = keySet('providers.opensubtitles.api_key');
  const osLoginSet = keySet('providers.opensubtitles.username') && keySet('providers.opensubtitles.password');
  // NOT `|| 'en'`: an explicitly-emptied list must READ back empty (so the
  // "None selected" copy shows) instead of silently resurrecting English on
  // the next render. A never-set key (undefined) still defaults to 'en'.
  const _subLangRaw = rawSettings['subtitles.languages'];
  const subLanguages = typeof _subLangRaw === 'string' ? _subLangRaw : 'en';
  const subLangCodes = subLanguages.split(',').map(s => s.trim().toLowerCase()).filter(Boolean);
  const setSubLangs = (codes: string[]) => saveKey('subtitles.languages')(codes.join(', '));
  const subAutoFetch = rawSettings['subtitles.auto_fetch'] === true;
  const subBackfill = rawSettings['subtitles.backfill_after_scan'] === true;
  const subEmbedded = rawSettings['subtitles.embedded'] !== false;   // default ON
  const subYify = rawSettings['subtitles.yifysubtitles'] === true;   // default OFF
  // Additional providers (all opt-in, default OFF). SubDL + SubSource also need
  // their key in Connections to actually run.
  const subSubdl = rawSettings['subtitles.subdl'] === true;
  const subSubsource = rawSettings['subtitles.subsource'] === true;
  const subPodnapisi = rawSettings['subtitles.podnapisi'] === true;
  const subAnimetosho = rawSettings['subtitles.animetosho'] === true;
  const subdlKeySet = keySet('providers.subdl.api_key');
  const subsourceKeySet = keySet('providers.subsource.api_key');
  // Variant preferences (OpenSubtitles search filters). '' = the API default
  // (include alongside normal subs); 'exclude' / 'only' narrow the search.
  const subHi = strSetting(rawSettings, 'subtitles.hearing_impaired');
  const subForced = strSetting(rawSettings, 'subtitles.forced');
  // Phase 4 — per-type language overrides, min-score floor, upgrade-over-time.
  const numSetting = (key: string, dflt: number) => {
    const v = rawSettings[key];
    return typeof v === 'number' ? v : (typeof v === 'string' && v.trim() && !isNaN(+v) ? +v : dflt);
  };
  const subMinScore = numSetting('subtitles.min_score', 0);
  const subUpgrade = rawSettings['subtitles.upgrade'] === true;
  const subUpgradeBelow = numSetting('subtitles.upgrade_below', 80);
  // Fully wired in the backend (subcache.py) but never had UI — how many days a
  // downloaded-subtitle cache entry is kept before the janitor prunes it.
  const subCacheRetention = numSetting('subtitles.cache_retention_days', 30);
  // Thorough search defaults ON (matches the backend) — so it's enabled unless
  // the user has explicitly turned it off.
  const subThorough = rawSettings['subtitles.thorough_search'] !== false;
  // Per-type overrides stored as comma lists; empty = inherit the global value.
  // Read as code/key arrays for the chip pickers.
  const _csv = (key: string): string[] =>
    strSetting(rawSettings, key).split(',').map(s => s.trim().toLowerCase()).filter(Boolean);
  const perTypeLangCodes = (mt: string) => _csv(`subtitles.languages.${mt}`);
  const setPerTypeLangs = (mt: string, codes: string[]) => saveKey(`subtitles.languages.${mt}`)(codes.join(', '));
  const perTypeSources = (mt: string) => _csv(`subtitles.sources.${mt}`);
  const setPerTypeSources = (mt: string, keys: string[]) => saveKey(`subtitles.sources.${mt}`)(keys.join(', '));
  // Source availability for the per-type picker — mirror the global Sources
  // section's "needs key" hints. Picking an unavailable source is allowed (the
  // backend just won't run it), but the label flags it so the choice is honest.
  const sourceAvail: Record<string, boolean> = {
    embedded: true, opensubtitles: osKeySet, subdl: subdlKeySet,
    podnapisi: true, subsource: subsourceKeySet, animetosho: true, yifysubtitles: true,
  };
  const sourceOptions = SUBTITLE_SOURCES.map(s => ({
    value: s.key, label: sourceAvail[s.key] ? s.label : `${s.label} · needs key`,
  }));
  const [upgrading, setUpgrading] = useState(false);
  const runUpgrade = async () => {
    if (upgrading) return;
    setUpgrading(true);
    try {
      await api.upgradeSubtitles();
      window.dispatchEvent(new Event('kira:activity-refresh'));
    } catch { /* pill carries errors */ } finally { setUpgrading(false); }
  };
  const variantOptions = [
    { value: '', label: 'Include (default)' },
    { value: 'exclude', label: 'Exclude' },
    { value: 'only', label: 'Only' },
  ];

  // Honest, one-glance hero state — all derived from existing settings, no
  // invented coverage. `onlineCount` = online-tier sources that will actually
  // run (key-gated ones only count when their key is set).
  const onlineCount = [osKeySet, subSubdl && subdlKeySet, subPodnapisi, subSubsource && subsourceKeySet, subAnimetosho].filter(Boolean).length;
  const langN = subLangCodes.length;
  const autoLabel = subAutoFetch ? 'auto after rename' : subBackfill ? 'on scan' : 'on demand';

  // The three media types share one shape across the per-type override grid.
  const perTypeRows = [
    { mt: 'movie', label: 'Movies', color: '#4ec5b3', icon: <IcFilm /> },
    { mt: 'tv', label: 'TV', color: '#b3e5fc', icon: <IcTv /> },
    { mt: 'anime', label: 'Anime', color: 'var(--media-anime)', icon: <IcAnime /> },
  ] as const;

  return (
    <div className="flex flex-col gap-5">
      {/* ── FLOW HERO — the cheapest-first cascade: Embedded → Online → YIFY,
          ending in a stateful pill that reads the real output. Each node dims
          when its tier is fully off. ── */}
      <div className="overflow-hidden rounded-2xl bg-secondary px-5 py-4 shadow-xs ring-1 ring-inset ring-secondary">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-3">
          <div className={`flex shrink-0 items-center gap-2.5 rounded-xl bg-tertiary px-3 py-2 ring-1 ring-inset ring-secondary${subEmbedded ? '' : ' opacity-50'}`}>
            <FeaturedIcon size="sm" color="gray" icon={<IcFilm />} />
            <div className="min-w-0"><div className="text-[12.5px] font-semibold text-primary">Embedded</div><div className="text-[12px] text-tertiary">free · offline</div></div>
          </div>
          <div className="hidden h-px min-w-[20px] flex-1 sm:block" style={{ background: 'var(--line-strong)' }} />
          <div className={`flex shrink-0 items-center gap-2.5 rounded-xl bg-tertiary px-3 py-2 ring-1 ring-inset ring-secondary${onlineCount > 0 ? '' : ' opacity-50'}`}>
            <FeaturedIcon size="sm" color="gray" icon={<IcLink />} />
            <div className="min-w-0"><div className="text-[12.5px] font-semibold text-primary">Online</div><div className="text-[12px] text-tertiary">{onlineCount} source{onlineCount === 1 ? '' : 's'}</div></div>
          </div>
          <div className="hidden h-px min-w-[20px] flex-1 sm:block" style={{ background: 'var(--line-strong)' }} />
          <div className={`flex shrink-0 items-center gap-2.5 rounded-xl bg-tertiary px-3 py-2 ring-1 ring-inset ring-secondary${subYify ? '' : ' opacity-50'}`}>
            <FeaturedIcon size="sm" color="gray" icon={<IcSearch />} />
            <div className="min-w-0"><div className="text-[12.5px] font-semibold text-primary">YIFY</div><div className="text-[12px] text-tertiary">movies · scraper</div></div>
          </div>
          <div className="hidden h-px min-w-[20px] flex-1 sm:block" style={{ background: 'var(--line-strong)' }} />
          <div className="flex shrink-0 items-center gap-2 rounded-xl bg-[var(--accent-8)] px-3.5 py-2.5 ring-1 ring-inset ring-[var(--accent-line)]">
            <span className="text-[var(--accent-bright)] [&_svg]:size-[16px]"><IcCaption /></span>
            <span className="text-[12px] font-semibold text-[var(--accent-bright)] tabular-nums">{langN} sidecar{langN === 1 ? '' : 's'} · {autoLabel}</span>
          </div>
        </div>
      </div>

      {/* ── TWO-COLUMN LAYOUT (the Connections idiom): the tall Source
          cascade OWNS the left column; the lighter what/when cards stack on
          the right. Collapses to one column under lg. ── */}
      <div className="grid items-start gap-5 lg:grid-cols-2">
      <div className="order-1 flex min-w-0 flex-col gap-5 lg:order-none lg:col-start-2">
      {/* ── LANGUAGES — the global request ── */}
      <SectionCard
        tint="var(--accent-bright)"
        icon={<IcCaption />}
        title="Languages"
        desc={<>Which <span className="font-mono text-secondary">.lang.srt</span> sidecars Kira writes. Each source fills only the languages the cheaper tiers above it missed.</>}
      >
        <div className="flex flex-col gap-2">
          <Select
            options={SUBTITLE_LANGUAGES.filter(l => !subLangCodes.includes(l.code)).map(l => ({ value: l.code, label: `${l.label} · ${l.code}` }))}
            value={null}
            onChange={(code) => { if (!subLangCodes.includes(code)) setSubLangs([...subLangCodes, code]); }}
            placeholder="Add a language…"
            style={{ flex: 1, minWidth: 0 }}
          />
          {subLangCodes.length > 0 ? (
            <div className="flex flex-wrap gap-1.5">
              {subLangCodes.map(code => (
                <RemovableChip
                  key={code}
                  label={SUBTITLE_LANGUAGES.find(l => l.code === code)?.label ?? code}
                  onRemove={() => setSubLangs(subLangCodes.filter(c => c !== code))}
                  removeLabel={`Remove ${code}`}
                />
              ))}
            </div>
          ) : (
            <span className="text-[12px] text-tertiary">None selected — defaults to English.</span>
          )}
        </div>
      </SectionCard>

      </div>

      <div className="order-2 min-w-0 lg:order-none lg:col-start-1 lg:row-start-1">
      {/* ── SOURCE CASCADE (the wow) — three cost tiers, top-to-bottom. Every
          provider control is re-housed here unchanged; only the chrome is new. ── */}
      <SectionCard
        tint="var(--accent)"
        icon={<IcDownload />}
        title="Source cascade"
        desc="Tried cheapest-first — each source only fills languages the tier above left empty."
      >
        <div className="flex flex-col gap-2">
          {/* TIER 1 — free & offline */}
          <div className={EYEBROW}>Tier 1 · Free &amp; offline</div>
          <Row rail="var(--conf-high)" name="Embedded extraction" hint="free · offline · needs ffmpeg">
            <Toggle isSelected={subEmbedded} onChange={() => saveKey('subtitles.embedded')(!subEmbedded)} aria-label="Extract embedded subtitle tracks" />
          </Row>
          {/* Live ffmpeg health + one-click managed install, as embedded's status line. */}
          <div className="pl-3.5"><FfmpegStatusRow /></div>

          {/* TIER 2 — online providers */}
          <div className={`${EYEBROW} mt-2`}>Tier 2 · Online providers</div>
          {/* OpenSubtitles is connection-governed (key lives in Connections), never a toggle. */}
          <Row
            rail="var(--accent)"
            name={
              <span className="inline-flex items-center gap-1.5">
                <span className="[&_svg]:size-3.5" style={{ color: osKeySet ? 'var(--conf-high)' : 'var(--conf-mid)' }}>{osKeySet ? <IcCheck /> : <IcAlertTri />}</span>
                OpenSubtitles
              </span>
            }
            hint={osKeySet ? (osLoginSet ? 'connected · key + login' : 'key only — downloads need the account login') : 'embedded extraction still works without it'}
          >
            {goToConnections ? (
              <Button color="secondary" size="sm" iconLeading={IcLink} onClick={goToConnections}>
                {osKeySet && !osLoginSet ? 'Add login' : osKeySet ? 'Manage' : 'Connect'}
              </Button>
            ) : null}
          </Row>
          <Row rail="var(--accent)" dim={!subdlKeySet} name="SubDL" hint={subdlKeySet ? 'modern REST catalogue' : 'modern REST catalogue · needs key in Connections'}>
            {!subdlKeySet && goToConnections ? <Button color="link-color" size="sm" onClick={goToConnections}>Add key</Button> : null}
            <Toggle isSelected={subSubdl && subdlKeySet} isDisabled={!subdlKeySet} onChange={() => saveKey('subtitles.subdl')(!subSubdl)} aria-label="Fetch subtitles from SubDL" />
          </Row>
          <Row rail="var(--accent)" name="Podnapisi" hint="keyless · good EU-language coverage">
            <Toggle isSelected={subPodnapisi} onChange={() => saveKey('subtitles.podnapisi')(!subPodnapisi)} aria-label="Fetch subtitles from Podnapisi" />
          </Row>
          <Row rail="var(--accent)" dim={!subsourceKeySet} name="SubSource" hint={subsourceKeySet ? 'Subscene successor' : 'Subscene successor · needs key in Connections'}>
            {!subsourceKeySet && goToConnections ? <Button color="link-color" size="sm" onClick={goToConnections}>Add key</Button> : null}
            <Toggle isSelected={subSubsource && subsourceKeySet} isDisabled={!subsourceKeySet} onChange={() => saveKey('subtitles.subsource')(!subSubsource)} aria-label="Fetch subtitles from SubSource" />
          </Row>
          <Row rail="var(--accent)" name="AnimeTosho" hint={<span style={{ color: 'var(--conf-mid)' }}>experimental · API exposes releases, not sub files yet</span>}>
            <Toggle isSelected={subAnimetosho} onChange={() => saveKey('subtitles.animetosho')(!subAnimetosho)} aria-label="Fetch anime subtitles from AnimeTosho" />
          </Row>

          {/* TIER 3 — last resort */}
          <div className={`${EYEBROW} mt-2`}>Tier 3 · Last resort</div>
          <Row rail="var(--conf-mid)" name="YIFY scraper" hint="movies · best-effort, can break">
            <Toggle isSelected={subYify} onChange={() => saveKey('subtitles.yifysubtitles')(!subYify)} aria-label="Fetch movie subtitles from YIFY" />
          </Row>
        </div>

        {/* Variants — OpenSubtitles search filters, applied to every online search. */}
        <div className="mt-4 flex flex-col gap-2.5 border-t border-secondary pt-4">
          <div className={EYEBROW}>Variants</div>
          <Row name="Hearing-impaired (SDH)" hint="OpenSubtitles search filter">
            <div className="w-[170px] shrink-0">
              <Select<string> value={subHi} onChange={v => saveKey('subtitles.hearing_impaired')(v)} options={variantOptions} aria-label="Hearing-impaired (SDH) subtitle preference" />
            </div>
          </Row>
          <Row name="Forced / signs-only" hint="foreign-parts-only tracks">
            <div className="w-[170px] shrink-0">
              <Select<string> value={subForced} onChange={v => saveKey('subtitles.forced')(v)} options={variantOptions} aria-label="Forced / signs-only subtitle preference" />
            </div>
          </Row>
        </div>
      </SectionCard>

      {/* ── AUTOMATION & QUALITY — when Kira fetches, and how good it must be ── */}
      <SectionCard
        tint="var(--conf-high)"
        icon={<IcRefresh />}
        title="Automation &amp; quality"
        desc="When Kira fetches without a click, and the score bar an auto-pick has to clear."
      >
        <div className="flex flex-col gap-2.5">
          <div className={EYEBROW}>When</div>
          <Row name="After rename" hint="grab subs for each freshly-renamed file">
            <Toggle isSelected={subAutoFetch} onChange={() => saveKey('subtitles.auto_fetch')(!subAutoFetch)} aria-label="Auto-download subtitles after rename" />
          </Row>
          <Row name="Backfill after scan" hint="fill gaps once a scan reads file metadata">
            <Toggle isSelected={subBackfill} onChange={() => saveKey('subtitles.backfill_after_scan')(!subBackfill)} aria-label="Backfill missing subtitles after a scan" />
          </Row>
          {/* Thorough search — query every numbering we know, then merge/dedupe. */}
          <div className="flex flex-col gap-3">
            <Row name="Thorough search" hint="query providers by absolute + season/episode and merge — better recall, anime especially (searches only, no extra downloads)">
              <Toggle isSelected={subThorough} onChange={() => saveKey('subtitles.thorough_search')(!subThorough)} aria-label="Thorough subtitle search" />
            </Row>
          </div>
          {/* Upgrade-over-time — re-check weak picks for a better release later. */}
          <div className="flex flex-col gap-3">
            <Row name="Upgrade over time" hint="re-check low-scoring subs for a better release">
              <Toggle isSelected={subUpgrade} onChange={() => saveKey('subtitles.upgrade')(!subUpgrade)} aria-label="Upgrade subtitles over time" />
            </Row>
            {subUpgrade ? (
              <>
                <div className="flex flex-col gap-2.5 rounded-xl bg-tertiary px-3.5 py-3 ring-1 ring-inset ring-secondary">
                  <SliderField
                    label="Threshold"
                    min={1}
                    max={100}
                    value={subUpgradeBelow}
                    onChange={v => saveKey('subtitles.upgrade_below')(v)}
                    color="var(--conf-mid)"
                    valueLabel={`< ${subUpgradeBelow}`}
                  />
                  <div className="text-[12px] leading-relaxed text-tertiary">Only re-check subs scoring under this.</div>
                </div>
                <div className="flex items-center justify-between gap-3 px-1">
                  <span className="text-[12px] leading-relaxed text-tertiary">
                    Runs automatically after scans. You can also sweep the whole library now.
                  </span>
                  <Button
                    color="secondary"
                    size="sm"
                    iconLeading={IcRefresh}
                    isDisabled={upgrading}
                    onClick={runUpgrade}
                  >
                    {upgrading ? 'Starting…' : 'Upgrade now'}
                  </Button>
                </div>
              </>
            ) : null}
          </div>
          <div className={`${EYEBROW} mt-2`}>Quality</div>
          {/* Global minimum-score floor — a slider (0 = take the best available). */}
          <div className="flex flex-col gap-2.5 rounded-xl bg-tertiary px-3.5 py-3 ring-1 ring-inset ring-secondary">
            <SliderField
              label="Min score"
              min={0}
              max={100}
              value={subMinScore}
              onChange={v => saveKey('subtitles.min_score')(v)}
              color="var(--conf-high)"
              valueLabel={subMinScore === 0 ? 'Any' : `≥ ${subMinScore}`}
            />
            <div className="text-[12px] leading-relaxed text-tertiary">
              Skip auto-picks scoring below this. <span className="text-secondary">0</span> takes the best available.
            </div>
          </div>
          {/* Downloaded-subtitle cache retention — the janitor prunes cached
              entries older than this. Backend-wired all along; surfaced here. */}
          <div className="flex flex-col gap-2.5 rounded-xl bg-tertiary px-3.5 py-3 ring-1 ring-inset ring-secondary">
            <SliderField
              label="Cache retention"
              min={1}
              max={365}
              value={subCacheRetention}
              onChange={v => saveKey('subtitles.cache_retention_days')(v)}
              color="var(--info)"
              valueLabel={`${subCacheRetention} day${subCacheRetention === 1 ? '' : 's'}`}
            />
            <div className="text-[12px] leading-relaxed text-tertiary">
              How long downloaded-subtitle cache entries are kept before the cleanup pass removes them.
            </div>
          </div>
          <div className="text-[12px] leading-relaxed text-tertiary">
            You can also fetch on demand from any title's <span className="text-secondary">Get subtitles</span> button — progress shows live in the activity indicator.
          </div>
        </div>
      </SectionCard>
      </div>
      </div>

      {/* ── PER-TYPE OVERRIDES — full-width; three media-owned panels ── */}
      <SectionCard
        tint="var(--media-anime)"
        icon={<IcSparkles />}
        title="Per-type overrides"
        desc="Anime, movies and TV can each want different languages, sources and score floors. All optional — blank inherits the global settings above."
      >
        <div className="flex flex-col gap-4">
          <div>
            <div className="grid gap-4 md:grid-cols-3">
              {perTypeRows.map(row => (
                <div key={row.mt} className="relative flex flex-col gap-3 overflow-hidden rounded-xl bg-tertiary p-3 ring-1 ring-inset ring-secondary">
                  <span aria-hidden className="absolute inset-x-0 top-0 h-0.5" style={{ background: row.color }} />
                  <div className="flex items-center gap-1.5">
                    <span className="[&_svg]:size-3.5" style={{ color: row.color }}>{row.icon}</span>
                    <div className="text-[13px] font-semibold" style={{ color: row.color }}>{row.label}</div>
                  </div>
                  <div className="flex flex-col gap-1">
                    <div className="text-[12px] font-medium text-secondary">Languages</div>
                    <PerTypeChips
                      chosen={perTypeLangCodes(row.mt)}
                      options={SUBTITLE_LANGUAGES.map(l => ({ value: l.code, label: `${l.label} · ${l.code}` }))}
                      onChange={codes => setPerTypeLangs(row.mt, codes)}
                      placeholder={perTypeLangCodes(row.mt).length ? 'Add a language…' : 'Same as global'}
                    />
                  </div>
                  <div className="flex flex-col gap-1">
                    <div className="text-[12px] font-medium text-secondary">Sources</div>
                    <PerTypeChips
                      chosen={perTypeSources(row.mt)}
                      options={sourceOptions}
                      onChange={keys => setPerTypeSources(row.mt, keys)}
                      placeholder={perTypeSources(row.mt).length ? 'Add a source…' : 'Same as global'}
                    />
                  </div>
                  <div className="flex flex-col gap-1">
                    <div className="text-[12px] font-medium text-secondary">Min score</div>
                    <Input
                      type="number"
                      // NOT strSetting: onChange saves a NUMBER, and strSetting
                      // returns '' for anything non-string — so every keystroke
                      // was instantly wiped and saved values rendered blank.
                      value={(() => {
                        const v = rawSettings[`subtitles.min_score.${row.mt}`];
                        return typeof v === 'number' ? String(v) : (typeof v === 'string' ? v : '');
                      })()}
                      onChange={e => saveKey(`subtitles.min_score.${row.mt}`)(e.target.value === '' ? '' : Math.max(0, Math.min(100, +e.target.value || 0)))}
                      placeholder="(global)"
                      aria-label={`Minimum subtitle score for ${row.mt}`}
                    />
                  </div>
                </div>
              ))}
            </div>
          </div>

        </div>
      </SectionCard>
    </div>
  );
}
