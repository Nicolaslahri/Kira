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
            <span key={v} className="inline-flex items-center gap-1 rounded-md bg-glass-2 px-2 py-1 text-[12px] text-ink">
              {options.find(o => o.value === v)?.label ?? v}
              <button
                type="button"
                onClick={() => onChange(chosen.filter(x => x !== v))}
                aria-label={`Remove ${v}`}
                className="grid place-items-center text-ink-soft transition-colors hover:text-ink [&_svg]:size-3"
              >
                <IcX />
              </button>
            </span>
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
        {hint ? <span className="ml-1.5 text-[11px] text-tertiary">{hint}</span> : null}
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
  const subLanguages = strSetting(rawSettings, 'subtitles.languages') || 'en';
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
            <div className="min-w-0"><div className="text-[12.5px] font-semibold text-primary">Embedded</div><div className="text-[11px] text-tertiary">free · offline</div></div>
          </div>
          <div className="hidden h-px min-w-[20px] flex-1 sm:block" style={{ background: 'var(--line-strong)' }} />
          <div className={`flex shrink-0 items-center gap-2.5 rounded-xl bg-tertiary px-3 py-2 ring-1 ring-inset ring-secondary${onlineCount > 0 ? '' : ' opacity-50'}`}>
            <FeaturedIcon size="sm" color="gray" icon={<IcLink />} />
            <div className="min-w-0"><div className="text-[12.5px] font-semibold text-primary">Online</div><div className="text-[11px] text-tertiary">{onlineCount} source{onlineCount === 1 ? '' : 's'}</div></div>
          </div>
          <div className="hidden h-px min-w-[20px] flex-1 sm:block" style={{ background: 'var(--line-strong)' }} />
          <div className={`flex shrink-0 items-center gap-2.5 rounded-xl bg-tertiary px-3 py-2 ring-1 ring-inset ring-secondary${subYify ? '' : ' opacity-50'}`}>
            <FeaturedIcon size="sm" color="gray" icon={<IcSearch />} />
            <div className="min-w-0"><div className="text-[12.5px] font-semibold text-primary">YIFY</div><div className="text-[11px] text-tertiary">movies · scraper</div></div>
          </div>
          <div className="hidden h-px min-w-[20px] flex-1 sm:block" style={{ background: 'var(--line-strong)' }} />
          <div className="flex shrink-0 items-center gap-2 rounded-xl px-3.5 py-2.5" style={{ background: 'var(--accent-deep)' }}>
            <span className="text-white [&_svg]:size-[16px]"><IcCaption /></span>
            <span className="text-[12px] font-semibold uppercase tracking-[0.06em] text-white">{langN} sidecar{langN === 1 ? '' : 's'} · {autoLabel}</span>
          </div>
        </div>
      </div>

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
                <span key={code} className="inline-flex items-center gap-1 rounded-md bg-glass-2 px-2 py-1 text-[12px] text-ink">
                  {SUBTITLE_LANGUAGES.find(l => l.code === code)?.label ?? code}
                  <button
                    type="button"
                    onClick={() => setSubLangs(subLangCodes.filter(c => c !== code))}
                    aria-label={`Remove ${code}`}
                    className="grid place-items-center text-ink-soft transition-colors hover:text-ink [&_svg]:size-3"
                  >
                    <IcX />
                  </button>
                </span>
              ))}
            </div>
          ) : (
            <span className="text-[11px] text-ink-soft">None selected — defaults to English.</span>
          )}
        </div>
      </SectionCard>

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

      {/* ── AUTOMATION — when Kira fetches without a click ── */}
      <SectionCard
        tint="var(--conf-high)"
        icon={<IcRefresh />}
        title="Automatic fetching"
        desc="When Kira fetches without a click. Both reuse the sources above."
      >
        <div className="flex flex-col gap-2.5">
          <Row name="After rename" hint="grab subs for each freshly-renamed file">
            <Toggle isSelected={subAutoFetch} onChange={() => saveKey('subtitles.auto_fetch')(!subAutoFetch)} aria-label="Auto-download subtitles after rename" />
          </Row>
          <Row name="Backfill after scan" hint="fill gaps once a scan reads file metadata">
            <Toggle isSelected={subBackfill} onChange={() => saveKey('subtitles.backfill_after_scan')(!subBackfill)} aria-label="Backfill missing subtitles after a scan" />
          </Row>
          <div className="text-[11px] leading-relaxed text-tertiary">
            You can also fetch on demand from any title's <span className="text-secondary">Get subtitles</span> button — progress shows live in the activity indicator.
          </div>
        </div>
      </SectionCard>

      {/* ── ADVANCED — quality floor, per-type overrides, upgrade-over-time ── */}
      <SectionCard
        tint="var(--media-anime)"
        icon={<IcSparkles />}
        title="Advanced"
        desc="Quality floor, per-type overrides, and the upgrade-over-time loop. All optional — sensible defaults if left blank."
      >
        <div className="flex flex-col gap-4">
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
            <div className="text-[11px] leading-relaxed text-tertiary">
              Skip auto-picks scoring below this. <span className="text-secondary">0</span> takes the best available.
            </div>
          </div>

          {/* Per-type overrides — three media-owned panels; each inherits the
              global Languages / Sources / Minimum score unless overridden. */}
          <div className="border-t border-secondary pt-4">
            <div className={`${EYEBROW} mb-2`}>Per-type overrides</div>
            <div className="mb-3 text-[11px] leading-relaxed text-tertiary">
              Each media type uses the <span className="text-secondary">Languages</span>, <span className="text-secondary">Sources</span> and <span className="text-secondary">Minimum score</span> above unless overridden here — useful when anime wants Japanese + English from fansub sources while movies only need English.
            </div>
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
                      value={strSetting(rawSettings, `subtitles.min_score.${row.mt}`)}
                      onChange={e => saveKey(`subtitles.min_score.${row.mt}`)(e.target.value === '' ? '' : Math.max(0, Math.min(100, +e.target.value || 0)))}
                      placeholder="(global)"
                      aria-label={`Minimum subtitle score for ${row.mt}`}
                    />
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Upgrade-over-time — re-check weak picks for a better release later. */}
          <div className="flex flex-col gap-3 border-t border-secondary pt-4">
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
                  <div className="text-[11px] leading-relaxed text-tertiary">Only re-check subs scoring under this.</div>
                </div>
                <div className="flex items-center justify-between gap-3 px-1">
                  <span className="text-[11px] leading-relaxed text-tertiary">
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
        </div>
      </SectionCard>
    </div>
  );
}
