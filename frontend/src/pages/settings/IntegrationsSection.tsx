import { useState, useEffect, type ReactNode, type Dispatch, type SetStateAction } from 'react';
import { IcTv, IcRefresh, IcEyeOff, IcEye, IcCheck, IcAlertTri, IcLink, IcSettings, IcFilm } from '../../lib/icons';
import { Select } from '../../components/ui';
import { SettingsLayout, SettingsGrid, SectionCard, NestedBox, FieldRow, SETTINGS_NESTED } from '../../components/settings-blocks';
import { BadgeWithDot } from '../../components/base/badges/badges';
import { Button } from '../../components/base/buttons/button';
import { Input } from '../../components/base/input/input';
import { Toggle } from '../../components/base/toggle/toggle';
import { Alert } from '../../components/base/alert/alert';
import { FeaturedIcon } from '../../components/base/featured-icons/featured-icon';
import { api } from '../../lib/api';
import { strSetting, isValidHttpUrl, type SaveKeyFn, type PushToast } from './helpers';

export function IntegrationsSection({
  rawSettings,
  saveKey,
}: {
  rawSettings: Record<string, unknown>;
  setRawSettings: Dispatch<SetStateAction<Record<string, unknown>>>;
  saveKey: SaveKeyFn;
  pushToast: PushToast;
}) {
  const sonarrUrl = strSetting(rawSettings, 'integrations.sonarr.url');
  const sonarrApiKey = strSetting(rawSettings, 'integrations.sonarr.api_key');
  const sonarrUrlBase = strSetting(rawSettings, 'integrations.sonarr.url_base');
  // Show/hide for the API key field — `password` masks chars by
  // default; eye toggle flips to plain text for a quick visual check.
  const [showApiKey, setShowApiKey] = useState(false);

  // Pass 6 integrations — media-server refresh, inbound webhook, notification
  // fan-out. All optional; blank = that leg is off.
  const plexUrl = strSetting(rawSettings, 'integrations.plex.url');
  const plexToken = strSetting(rawSettings, 'integrations.plex.token');
  const jellyfinUrl = strSetting(rawSettings, 'integrations.jellyfin.url');
  const jellyfinKey = strSetting(rawSettings, 'integrations.jellyfin.api_key');
  const webhookToken = strSetting(rawSettings, 'integrations.webhook.token');
  const discordWebhook = strSetting(rawSettings, 'notifications.discord_webhook');
  const genericWebhook = strSetting(rawSettings, 'notifications.webhook_url');
  // One reveal toggle for all the secret-ish fields below (tokens / keys /
  // webhook URLs that embed a secret).
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
  // Origin for the copy-paste webhook URL hint (the user pastes this into
  // Sonarr/Radarr's Connect → Webhook). Guarded for SSR/tests.
  const webhookBase = typeof window !== 'undefined' ? window.location.origin : '';

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
      // first. BUT never send the MASKED placeholder ('•••• •••• •••• abcd')
      // back as the key: after a refresh the field shows that mask (the real
      // secret never leaves the server), and its non-ASCII bullets can't be
      // encoded into an HTTP header — sending it crashed the backend test with
      // an uncaught 500 that, cross-origin, surfaced as "Failed to fetch". When
      // the field still shows the mask (key not re-typed), omit it so the
      // backend tests with the SAVED key.
      const realKey = sonarrApiKey && !sonarrApiKey.startsWith('••••') ? sonarrApiKey : undefined;
      const r = await api.testSonarr({
        url: sonarrUrl || undefined,
        api_key: realKey,
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

  // Inline label + control row used throughout the integrations forms
  // (URL / token / key fields). Delegates to the shared FieldRow primitive
  // so the fixed-width label column matches every other section. Kept as a
  // plain render helper (not a wrapping component) so it doesn't remount the
  // wrapped <input> on every keystroke and drop focus.
  const fieldRow = (label: string, control: ReactNode, labelWidth = 'w-20') => (
    <FieldRow label={label} labelWidth={labelWidth}>{control}</FieldRow>
  );

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
    <SettingsLayout intro="Push actions to tools in your media stack. Sonarr first; Radarr arrives with movie-collection grouping.">
      {/* ── Sonarr block ─────────────────────────────────────────── */}
      <SectionCard
        icon={<IcTv />}
        title="Sonarr"
        desc={<>When configured, the cover popup gets a <span className="font-mono text-ink">Get missing → Sonarr</span> button that one-clicks a search for every episode Kira knows you don't have. URL + API key live in Sonarr's <span className="font-mono text-ink">Settings → General → Security</span>.</>}
        headerExtra={(
          <div className="flex shrink-0 items-center gap-2.5">
            <BadgeWithDot color={sonarrStatusColor}>{sonarrStatusLabel}</BadgeWithDot>
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
        )}
      >
        <div className="flex flex-col gap-3">
          {fieldRow('URL',
            <Input wrapperClassName="flex-1" mono value={sonarrUrl} placeholder="http://sonarr:8989" invalid={!isValidHttpUrl(sonarrUrl)} aria-invalid={!isValidHttpUrl(sonarrUrl)} title={!isValidHttpUrl(sonarrUrl) ? 'Enter a full http(s) URL, e.g. http://sonarr:8989' : undefined} onChange={e => saveKey('integrations.sonarr.url')(e.target.value)} />
          )}
          {fieldRow('URL base',
            <Input wrapperClassName="flex-1" mono value={sonarrUrlBase} placeholder="optional · e.g. /sonarr (reverse-proxy setups)" onChange={e => saveKey('integrations.sonarr.url_base')(e.target.value)} />
          )}
          {fieldRow('API key',
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
          )}
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
              {fieldRow('Monitor',
                <Select<string>
                  style={{ flex: 1, minWidth: 0 }}
                  value={monitorNewSeasons}
                  onChange={v => saveKey('integrations.sonarr.monitor_new_seasons')(v)}
                  options={[
                    { value: 'all', label: 'All future seasons' },
                    { value: 'future', label: 'Future seasons (no search)' },
                    { value: 'none', label: 'None (manual)' },
                  ]}
                />,
                'w-16',
              )}
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
      </SectionCard>

      {/* Media servers — full width (Subtitles moved to Naming → sidecars).
          Row 2: Inbound webhook + Notifications. Sonarr stays full width above. */}
      {/* ── Media servers (Plex / Jellyfin library refresh) ──────── */}
      <SectionCard
        icon={<IcRefresh />}
        title="Media servers"
        desc="After Kira renames a batch it nudges Plex / Jellyfin to re-scan, so the changes show up right away instead of at the next scheduled scan. Leave a server blank to skip it."
      >
        <div className="flex flex-col gap-3">
          <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-muted">Plex</div>
          {fieldRow('URL',
            <Input wrapperClassName="flex-1" mono value={plexUrl} placeholder="http://plex:32400" invalid={!isValidHttpUrl(plexUrl)} aria-invalid={!isValidHttpUrl(plexUrl)} title={!isValidHttpUrl(plexUrl) ? 'Enter a full http(s) URL, e.g. http://plex:32400' : undefined} onChange={e => saveKey('integrations.plex.url')(e.target.value)} />
          )}
          {fieldRow('Token',
            <Input wrapperClassName="flex-1" mono type={showSecrets ? 'text' : 'password'} value={plexToken} placeholder="X-Plex-Token" autoComplete="off" onChange={e => saveKey('integrations.plex.token')(e.target.value)} trailing={secretEye} />
          )}
          <div className="mt-1 text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-muted">Jellyfin</div>
          {fieldRow('URL',
            <Input wrapperClassName="flex-1" mono value={jellyfinUrl} placeholder="http://jellyfin:8096" invalid={!isValidHttpUrl(jellyfinUrl)} aria-invalid={!isValidHttpUrl(jellyfinUrl)} title={!isValidHttpUrl(jellyfinUrl) ? 'Enter a full http(s) URL, e.g. http://jellyfin:8096' : undefined} onChange={e => saveKey('integrations.jellyfin.url')(e.target.value)} />
          )}
          {fieldRow('API key',
            <Input wrapperClassName="flex-1" mono type={showSecrets ? 'text' : 'password'} value={jellyfinKey} placeholder="Dashboard → API Keys" autoComplete="off" onChange={e => saveKey('integrations.jellyfin.api_key')(e.target.value)} trailing={secretEye} />
          )}
        </div>
      </SectionCard>

      {/* Row 2: Inbound webhook + Notifications. */}
      <SettingsGrid>
      {/* ── Inbound webhook (Sonarr / Radarr post-import trigger) ─── */}
      <SectionCard
        icon={<IcLink />}
        title="Inbound webhook"
        desc={<>Let Sonarr / Radarr tell Kira to scan the moment a release imports. Set a token, then add a <span className="font-mono text-ink">Connect → Webhook</span> in *arr pointing at the URL below. Blank token = disabled.</>}
      >
        <div className="flex flex-col gap-3">
          {fieldRow('Token',
            <Input wrapperClassName="flex-1" mono type={showSecrets ? 'text' : 'password'} value={webhookToken} placeholder="a shared secret you choose" autoComplete="off" onChange={e => saveKey('integrations.webhook.token')(e.target.value)} trailing={secretEye} />
          )}
          {webhookToken ? (
            <NestedBox className="px-3 py-2.5">
              <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-muted">Webhook URL (for Sonarr)</div>
              <div className="mt-1 break-all font-mono text-[12px] text-ink-soft">{webhookBase}/api/v1/webhooks/sonarr?token={webhookToken}</div>
              <div className="mt-1.5 text-[11.5px] text-ink-soft">Radarr uses <span className="font-mono">/api/v1/webhooks/radarr</span> with the same token.</div>
            </NestedBox>
          ) : null}
        </div>
      </SectionCard>

      {/* ── Notifications fan-out (Discord / generic webhook) ─────── */}
      <SectionCard
        icon={<IcSettings />}
        title="Notifications"
        desc="Push scan + rename events to a Discord channel or any generic webhook (Apprise, n8n, a custom script), on top of the in-app bell. Leave blank to skip."
      >
        <div className="flex flex-col gap-3">
          {fieldRow('Discord',
            <Input wrapperClassName="flex-1" mono type={showSecrets ? 'text' : 'password'} value={discordWebhook} placeholder="https://discord.com/api/webhooks/…" autoComplete="off" onChange={e => saveKey('notifications.discord_webhook')(e.target.value)} trailing={secretEye} />,
            'w-24',
          )}
          {fieldRow('Webhook',
            <Input wrapperClassName="flex-1" mono value={genericWebhook} placeholder="https://example.com/hook (JSON POST)" invalid={!isValidHttpUrl(genericWebhook)} aria-invalid={!isValidHttpUrl(genericWebhook)} title={!isValidHttpUrl(genericWebhook) ? 'Enter a full http(s) URL, e.g. https://example.com/hook' : undefined} onChange={e => saveKey('notifications.webhook_url')(e.target.value)} />,
            'w-24',
          )}
        </div>
      </SectionCard>
      </SettingsGrid>

      {/* ── Radarr placeholder ───────────────────────────────────── */}
      <div className="rounded-2xl border border-dashed border-white/[0.14] bg-white/[0.02] p-4">
        <div className="flex items-start gap-3">
          <FeaturedIcon size="md" color="gray" icon={<IcFilm />} />
          <div className="min-w-0 flex-1">
            <div className="text-[15px] font-semibold text-ink-muted">Radarr (outbound)</div>
            <div className="mt-1 text-[12.5px] leading-relaxed text-ink-soft">
              Movies-side parallel of Sonarr's <span className="font-mono">Get missing</span> — activates once Kira can identify movie collections,
              so "MCU Phase 4: 8/12" can fire a search for the missing 4 in one click. (Radarr's inbound webhook already works above.)
            </div>
          </div>
          <BadgeWithDot color="gray">Coming soon</BadgeWithDot>
        </div>
      </div>
    </SettingsLayout>
  );
}
