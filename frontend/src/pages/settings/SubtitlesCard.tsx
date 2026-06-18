import { useState } from 'react';
import { IcTag, IcX, IcCheck, IcAlertTri, IcLink, IcRefresh } from '../../lib/icons';
import { Select } from '../../components/ui';
import { SectionCard, FieldRow, NestedBox } from '../../components/settings-blocks';
import { FfmpegStatusRow } from '../../components/FfmpegStatus';
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

// Subtitles are sidecar output (written next to each renamed file like .nfo and
// artwork), so the BEHAVIOR settings live under Naming. The OpenSubtitles
// CREDENTIALS live in Connections with every other provider key — this card
// only shows their connection status and links across.
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

  return (
    <SectionCard
      icon={<IcTag />}
      title="Subtitles"
      desc={<>Save subtitles as <span className="font-mono text-ink">.lang.srt</span> sidecars, cheapest-first: embedded tracks (free, offline) → OpenSubtitles → YIFY (movie scraper). Each source only fills languages the earlier ones missed. Provider credentials live in <span className="text-ink">Connections</span>.</>}
    >
      <div className="flex flex-col gap-3">
        {/* Connection status — credentials themselves live in Connections
            with every other provider key; this is just the health readout. */}
        <NestedBox className="flex items-center justify-between gap-3 px-3.5 py-3">
          <span className="inline-flex items-center gap-2 text-[13px] text-ink [&_svg]:size-3.5">
            {osKeySet
              ? <><span className="text-[var(--conf-high)]"><IcCheck /></span>OpenSubtitles connected
                  <span className="text-[11px] text-ink-soft">
                    {osLoginSet ? 'key + account login' : 'key only — downloads need the account login'}
                  </span></>
              : <><span className="text-[var(--conf-mid)]"><IcAlertTri /></span>OpenSubtitles not connected
                  <span className="text-[11px] text-ink-soft">embedded extraction still works without it</span></>}
          </span>
          {goToConnections ? (
            <Button color="secondary" size="sm" iconLeading={IcLink} onClick={goToConnections}>
              {osKeySet && !osLoginSet ? 'Add login' : osKeySet ? 'Manage' : 'Connect'}
            </Button>
          ) : null}
        </NestedBox>
        <FieldRow label="Languages" labelWidth="w-24">
          <div className="flex flex-1 flex-col gap-2">
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
        </FieldRow>
        {/* Sources + variants apply to EVERY fetch path — the per-title
            "Get subtitles" button, the library backfill, and the automations
            below — so they're always editable (no longer gated on auto-fetch). */}
        <NestedBox className="flex flex-col gap-3 px-3.5 py-3">
          <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-soft">Sources</div>
          <div className="flex items-center justify-between gap-3">
            <span className="text-[13px] text-ink">
              Embedded extraction
              <span className="ml-1.5 text-[11px] text-ink-soft">free · offline · needs ffmpeg</span>
            </span>
            <Toggle isSelected={subEmbedded} onChange={() => saveKey('subtitles.embedded')(!subEmbedded)} aria-label="Extract embedded subtitle tracks" />
          </div>
          {/* Live ffmpeg health + the one-click managed install. */}
          <FfmpegStatusRow />
          <div className="flex items-center justify-between gap-3">
            <span className="text-[13px] text-ink">
              SubDL
              <span className="ml-1.5 text-[11px] text-ink-soft">
                modern REST catalogue{subdlKeySet ? '' : ' · needs key in Connections'}
              </span>
            </span>
            <Toggle isSelected={subSubdl && subdlKeySet} isDisabled={!subdlKeySet} onChange={() => saveKey('subtitles.subdl')(!subSubdl)} aria-label="Fetch subtitles from SubDL" />
          </div>
          <div className="flex items-center justify-between gap-3">
            <span className="text-[13px] text-ink">
              Podnapisi
              <span className="ml-1.5 text-[11px] text-ink-soft">keyless · good EU-language coverage</span>
            </span>
            <Toggle isSelected={subPodnapisi} onChange={() => saveKey('subtitles.podnapisi')(!subPodnapisi)} aria-label="Fetch subtitles from Podnapisi" />
          </div>
          <div className="flex items-center justify-between gap-3">
            <span className="text-[13px] text-ink">
              SubSource
              <span className="ml-1.5 text-[11px] text-ink-soft">
                Subscene successor{subsourceKeySet ? '' : ' · needs key in Connections'}
              </span>
            </span>
            <Toggle isSelected={subSubsource && subsourceKeySet} isDisabled={!subsourceKeySet} onChange={() => saveKey('subtitles.subsource')(!subSubsource)} aria-label="Fetch subtitles from SubSource" />
          </div>
          <div className="flex items-center justify-between gap-3">
            <span className="text-[13px] text-ink">
              AnimeTosho
              <span className="ml-1.5 text-[11px] text-conf-mid">experimental · API exposes releases, not sub files yet</span>
            </span>
            <Toggle isSelected={subAnimetosho} onChange={() => saveKey('subtitles.animetosho')(!subAnimetosho)} aria-label="Fetch anime subtitles from AnimeTosho" />
          </div>
          <div className="flex items-center justify-between gap-3">
            <span className="text-[13px] text-ink">
              YIFY scraper
              <span className="ml-1.5 text-[11px] text-ink-soft">movies · best-effort, can break</span>
            </span>
            <Toggle isSelected={subYify} onChange={() => saveKey('subtitles.yifysubtitles')(!subYify)} aria-label="Fetch movie subtitles from YIFY" />
          </div>
          <div className="mt-1 text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-soft">Variants</div>
          <div className="flex items-center justify-between gap-3">
            <span className="text-[13px] text-ink">
              Hearing-impaired (SDH)
              <span className="ml-1.5 text-[11px] text-ink-soft">OpenSubtitles search filter</span>
            </span>
            <div className="w-[170px] shrink-0">
              <Select<string> value={subHi} onChange={v => saveKey('subtitles.hearing_impaired')(v)} options={variantOptions} aria-label="Hearing-impaired (SDH) subtitle preference" />
            </div>
          </div>
          <div className="flex items-center justify-between gap-3">
            <span className="text-[13px] text-ink">
              Forced / signs-only
              <span className="ml-1.5 text-[11px] text-ink-soft">foreign-parts-only tracks</span>
            </span>
            <div className="w-[170px] shrink-0">
              <Select<string> value={subForced} onChange={v => saveKey('subtitles.forced')(v)} options={variantOptions} aria-label="Forced / signs-only subtitle preference" />
            </div>
          </div>
        </NestedBox>

        {/* Automation — when Kira fetches WITHOUT a click. Both opt-in. */}
        <NestedBox className="flex flex-col gap-3 px-3.5 py-3">
          <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-soft">Automatic fetching</div>
          <div className="flex items-center justify-between gap-3">
            <span className="text-[13px] text-ink">
              After rename
              <span className="ml-1.5 text-[11px] text-ink-soft">grab subs for each freshly-renamed file</span>
            </span>
            <Toggle isSelected={subAutoFetch} onChange={() => saveKey('subtitles.auto_fetch')(!subAutoFetch)} aria-label="Auto-download subtitles after rename" />
          </div>
          <div className="flex items-center justify-between gap-3">
            <span className="text-[13px] text-ink">
              Backfill after scan
              <span className="ml-1.5 text-[11px] text-ink-soft">fill gaps once a scan reads file metadata</span>
            </span>
            <Toggle isSelected={subBackfill} onChange={() => saveKey('subtitles.backfill_after_scan')(!subBackfill)} aria-label="Backfill missing subtitles after a scan" />
          </div>
          <div className="text-[11px] leading-relaxed text-ink-soft">
            Both reuse the sources above. You can also fetch on demand from any
            title's <span className="text-ink">Get subtitles</span> button — progress
            shows live in the activity indicator.
          </div>
        </NestedBox>

        {/* Advanced — quality floor, per-type language overrides, and the
            upgrade-over-time loop. All optional; sensible defaults if left blank. */}
        <NestedBox className="flex flex-col gap-3 px-3.5 py-3">
          <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-soft">Advanced</div>

          {/* Minimum score floor — reject auto-picks below this confidence. */}
          <div className="flex items-center justify-between gap-3">
            <span className="text-[13px] text-ink">
              Minimum score
              <span className="ml-1.5 text-[11px] text-ink-soft">skip auto-picks below this (0–100, 0 = take best available)</span>
            </span>
            <div className="w-[88px] shrink-0">
              <Input
                type="number"
                value={String(subMinScore)}
                onChange={e => saveKey('subtitles.min_score')(Math.max(0, Math.min(100, +e.target.value || 0)))}
                aria-label="Minimum subtitle score"
              />
            </div>
          </div>

          {/* Per-type overrides — each defaults to the GLOBAL value set above
              (the Languages list, the Sources toggles, and the Minimum score).
              Leave a row empty to inherit; pick from the dropdown to override. */}
          <div className="mt-1 text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-soft">Per-type overrides</div>
          <div className="text-[11px] leading-relaxed text-ink-soft">
            Each media type uses the <span className="text-ink">Languages</span>, <span className="text-ink">Sources</span> and <span className="text-ink">Minimum score</span> you set above unless you override it here. Useful when anime wants Japanese + English from fansub sources while movies only need English from OpenSubtitles.
          </div>
          <div className="text-[12px] font-medium text-ink-muted">Languages</div>
          {(['movie', 'tv', 'anime'] as const).map(mt => (
            <div key={`lang-${mt}`} className="flex items-start justify-between gap-3">
              <span className="mt-2 w-12 shrink-0 text-[13px] capitalize text-ink">{mt === 'tv' ? 'TV' : mt}</span>
              <div className="w-[240px] shrink-0">
                <PerTypeChips
                  chosen={perTypeLangCodes(mt)}
                  options={SUBTITLE_LANGUAGES.map(l => ({ value: l.code, label: `${l.label} · ${l.code}` }))}
                  onChange={codes => setPerTypeLangs(mt, codes)}
                  placeholder={perTypeLangCodes(mt).length ? 'Add a language…' : 'Same as global — pick to override'}
                />
              </div>
            </div>
          ))}

          <div className="mt-1 text-[12px] font-medium text-ink-muted">Sources</div>
          <div className="text-[11px] leading-relaxed text-ink-soft">
            Pick from the dropdown to limit a type to specific sources — a source still needs its key / install to actually run.
          </div>
          {(['movie', 'tv', 'anime'] as const).map(mt => (
            <div key={`src-${mt}`} className="flex items-start justify-between gap-3">
              <span className="mt-2 w-12 shrink-0 text-[13px] capitalize text-ink">{mt === 'tv' ? 'TV' : mt}</span>
              <div className="w-[240px] shrink-0">
                <PerTypeChips
                  chosen={perTypeSources(mt)}
                  options={sourceOptions}
                  onChange={keys => setPerTypeSources(mt, keys)}
                  placeholder={perTypeSources(mt).length ? 'Add a source…' : 'Same as global — pick to override'}
                />
              </div>
            </div>
          ))}

          <div className="mt-1 text-[12px] font-medium text-ink-muted">Minimum score</div>
          <div className="text-[11px] leading-relaxed text-ink-soft">
            Blank uses the global floor. Anime fansubs often score lower than studio releases — a looser floor for anime keeps good subs a strict movie floor would reject.
          </div>
          {(['movie', 'tv', 'anime'] as const).map(mt => (
            <div key={`ms-${mt}`} className="flex items-center justify-between gap-3">
              <span className="w-12 shrink-0 text-[13px] capitalize text-ink">{mt === 'tv' ? 'TV' : mt}</span>
              <div className="w-[88px] shrink-0">
                <Input
                  type="number"
                  value={strSetting(rawSettings, `subtitles.min_score.${mt}`)}
                  onChange={e => saveKey(`subtitles.min_score.${mt}`)(e.target.value === '' ? '' : Math.max(0, Math.min(100, +e.target.value || 0)))}
                  placeholder="(global)"
                  aria-label={`Minimum subtitle score for ${mt}`}
                />
              </div>
            </div>
          ))}

          {/* Upgrade-over-time — re-check weak picks for a better release later. */}
          <div className="mt-1 flex items-center justify-between gap-3">
            <span className="text-[13px] text-ink">
              Upgrade over time
              <span className="ml-1.5 text-[11px] text-ink-soft">re-check low-scoring subs for a better release</span>
            </span>
            <Toggle isSelected={subUpgrade} onChange={() => saveKey('subtitles.upgrade')(!subUpgrade)} aria-label="Upgrade subtitles over time" />
          </div>
          {subUpgrade ? (
            <>
              <div className="flex items-center justify-between gap-3">
                <span className="text-[13px] text-ink">
                  Upgrade below
                  <span className="ml-1.5 text-[11px] text-ink-soft">only re-check subs scoring under this</span>
                </span>
                <div className="w-[88px] shrink-0">
                  <Input
                    type="number"
                    value={String(subUpgradeBelow)}
                    onChange={e => saveKey('subtitles.upgrade_below')(Math.max(1, Math.min(100, +e.target.value || 80)))}
                    aria-label="Upgrade-below threshold"
                  />
                </div>
              </div>
              <div className="flex items-center justify-between gap-3">
                <span className="text-[11px] leading-relaxed text-ink-soft">
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
        </NestedBox>
      </div>
    </SectionCard>
  );
}
