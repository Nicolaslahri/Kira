import { useState } from 'react';
import { IcTag, IcX, IcEye, IcEyeOff } from '../../lib/icons';
import { Select } from '../../components/ui';
import { SectionCard, FieldRow, NestedBox } from '../../components/settings-blocks';
import { Input } from '../../components/base/input/input';
import { Toggle } from '../../components/base/toggle/toggle';
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

// Subtitles are sidecar output (written next to each renamed file like .nfo and
// artwork), so they live under Naming. OpenSubtitles credentials sit here rather
// than Connections because they're only used by this sidecar feature.
export function SubtitlesCard({
  rawSettings,
  saveKey,
}: {
  rawSettings: Record<string, unknown>;
  saveKey: SaveKeyFn;
}) {
  const osApiKey = strSetting(rawSettings, 'providers.opensubtitles.api_key');
  const osUsername = strSetting(rawSettings, 'providers.opensubtitles.username');
  const osPassword = strSetting(rawSettings, 'providers.opensubtitles.password');
  const subLanguages = strSetting(rawSettings, 'subtitles.languages') || 'en';
  const subLangCodes = subLanguages.split(',').map(s => s.trim().toLowerCase()).filter(Boolean);
  const setSubLangs = (codes: string[]) => saveKey('subtitles.languages')(codes.join(', '));
  const subAutoFetch = rawSettings['subtitles.auto_fetch'] === true;
  const subEmbedded = rawSettings['subtitles.embedded'] !== false;   // default ON
  const subYify = rawSettings['subtitles.yifysubtitles'] === true;   // default OFF

  const [showSecrets, setShowSecrets] = useState(false);
  const secretEye = (
    <button
      type="button"
      onClick={() => setShowSecrets(s => !s)}
      title={showSecrets ? 'Hide' : 'Show'}
      aria-label={showSecrets ? 'Hide secrets' : 'Show secrets'}
      className="grid size-6 shrink-0 place-items-center rounded-md text-ink-soft transition-colors hover:bg-glass-2 hover:text-ink [&_svg]:size-[14px]"
    >
      {showSecrets ? <IcEyeOff /> : <IcEye />}
    </button>
  );

  return (
    <SectionCard
      icon={<IcTag />}
      title="Subtitles"
      desc={<>Save subtitles as <span className="font-mono text-ink">.lang.srt</span> sidecars on rename, cheapest-first: embedded tracks (free, offline) → OpenSubtitles (needs the key + login below) → YIFY (movie scraper). Each source only fills languages the earlier ones missed.</>}
    >
      <div className="flex flex-col gap-3">
        <FieldRow label="API key" labelWidth="w-24">
          <Input wrapperClassName="flex-1" mono type={showSecrets ? 'text' : 'password'} value={osApiKey} placeholder="OpenSubtitles → Consumers" autoComplete="off" onChange={e => saveKey('providers.opensubtitles.api_key')(e.target.value)} trailing={secretEye} />
        </FieldRow>
        <FieldRow label="Username" labelWidth="w-24">
          <Input wrapperClassName="flex-1" mono value={osUsername} placeholder="for downloads (optional)" autoComplete="off" onChange={e => saveKey('providers.opensubtitles.username')(e.target.value)} />
        </FieldRow>
        <FieldRow label="Password" labelWidth="w-24">
          <Input wrapperClassName="flex-1" mono type={showSecrets ? 'text' : 'password'} value={osPassword} placeholder="for downloads (optional)" autoComplete="off" onChange={e => saveKey('providers.opensubtitles.password')(e.target.value)} trailing={secretEye} />
        </FieldRow>
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
        <NestedBox className="flex flex-col gap-3 px-3.5 py-3">
          <div className="flex items-center justify-between gap-3">
            <span className="text-[13px] font-medium text-ink">Auto-download after rename</span>
            <Toggle isSelected={subAutoFetch} onChange={() => saveKey('subtitles.auto_fetch')(!subAutoFetch)} aria-label="Auto-download subtitles after rename" />
          </div>
          <div className="flex items-center justify-between gap-3">
            <span className="text-[13px] text-ink">
              Embedded extraction
              <span className="ml-1.5 text-[11px] text-ink-soft">free · offline · needs ffmpeg</span>
            </span>
            <Toggle isSelected={subEmbedded} isDisabled={!subAutoFetch} onChange={() => saveKey('subtitles.embedded')(!subEmbedded)} aria-label="Extract embedded subtitle tracks" />
          </div>
          <div className="flex items-center justify-between gap-3">
            <span className="text-[13px] text-ink">
              YIFY scraper
              <span className="ml-1.5 text-[11px] text-ink-soft">movies · best-effort, can break</span>
            </span>
            <Toggle isSelected={subYify} isDisabled={!subAutoFetch} onChange={() => saveKey('subtitles.yifysubtitles')(!subYify)} aria-label="Fetch movie subtitles from YIFY" />
          </div>
          {!subAutoFetch ? (
            <div className="text-[11px] leading-relaxed text-ink-soft">
              Turn on <span className="text-ink">Auto-download after rename</span> to choose sources.
            </div>
          ) : null}
        </NestedBox>
      </div>
    </SectionCard>
  );
}
