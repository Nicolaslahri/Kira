import { useEffect, useState, type ReactNode } from 'react';
import { IcSparkles, IcRefresh, IcTrash, IcCheck, IcAlertTri, IcFolder, IcPlus, IcX, IcCaption, IcShieldCheck, IcSpin } from '../../lib/icons';
import { Select } from '../../components/ui';
import { SettingsLayout, SectionHeader, NestedBox } from '../../components/settings-blocks';
import { BadgeWithDot } from '../../components/base/badges/badges';
import { Button } from '../../components/base/buttons/button';
import { Input } from '../../components/base/input/input';
import { Toggle } from '../../components/base/toggle/toggle';
import { Alert } from '../../components/base/alert/alert';
import { FeaturedIcon } from '../../components/base/featured-icons/featured-icon';
import { FolderPickerModal } from '../../components/FolderPickerModal';
import { api, posterSrc, type ApiPack, type ApiPackValidate } from '../../lib/api';
import { isValidHttpUrl, type PushToast } from './helpers';

const ACCENT = 'var(--accent)';

function PackCard({ icon, tint, title, desc, headerExtra, connected, children }: {
  icon: ReactNode; tint: string; title: ReactNode; desc?: ReactNode;
  headerExtra?: ReactNode; connected?: boolean; children?: ReactNode;
}) {
  return (
    <div
      className="overflow-hidden rounded-2xl bg-secondary transition-shadow"
      style={{ boxShadow: connected ? 'inset 0 0 0 1px color-mix(in srgb, var(--conf-high) 38%, transparent)' : 'inset 0 0 0 1px var(--color-border-secondary)' }}
    >
      <div className="flex items-start gap-3 p-4">
        <FeaturedIcon size="md" tint={tint} icon={icon} />
        <div className="min-w-0 flex-1">
          <div className="text-[14px] font-semibold text-primary">{title}</div>
          {desc ? <div className="mt-1 text-[12px] leading-relaxed text-tertiary">{desc}</div> : null}
        </div>
        {headerExtra ? <div className="shrink-0">{headerExtra}</div> : null}
      </div>
      {children ? <div className="border-t border-secondary p-4">{children}</div> : null}
    </div>
  );
}

export function PacksSection({ pushToast }: { pushToast: PushToast }) {
  const [packs, setPacks] = useState<ApiPack[]>([]);
  const [loading, setLoading] = useState(true);

  // Add-a-pack flow.
  const [url, setUrl] = useState('');
  const [validating, setValidating] = useState(false);
  const [preview, setPreview] = useState<ApiPackValidate | null>(null);
  const [adding, setAdding] = useState(false);

  // Per-pack busy + the folder picker target.
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [picker, setPicker] = useState<{ key: string; enableOverride: boolean } | null>(null);
  const [rescanning, setRescanning] = useState(false);

  const reload = async () => {
    try {
      const r = await api.listPacks();
      setPacks(r.packs);
    } catch (e) {
      pushToast({ title: 'Could not load packs', sub: (e as Error).message, kind: 'error' });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void reload(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);

  const runValidate = async () => {
    if (!isValidHttpUrl(url)) {
      pushToast({ title: 'Enter a valid http(s) URL', kind: 'error' });
      return;
    }
    setValidating(true);
    setPreview(null);
    try {
      const r = await api.validatePack(url);
      setPreview(r);
      if (!r.ok) pushToast({ title: 'Pack is not valid', sub: r.error ?? undefined, kind: 'error' });
    } catch (e) {
      pushToast({ title: 'Validation failed', sub: (e as Error).message, kind: 'error' });
    } finally {
      setValidating(false);
    }
  };

  const runAdd = async () => {
    setAdding(true);
    try {
      const r = await api.addPack({ url });
      const rescued = r.rescued ?? 0;
      pushToast({
        title: 'Pack added',
        sub: rescued > 0
          ? `${preview?.name ?? url} — rescued ${rescued} unmatched file${rescued === 1 ? '' : 's'}`
          : (preview?.name ?? url),
        kind: 'success',
      });
      // The pack auto-applied to the no_match backlog → refresh Review so the
      // newly-matched files show up without a manual rescan.
      if (rescued > 0) { try { window.dispatchEvent(new Event('kira:files-changed')); } catch { /* no window */ } }
      setUrl('');
      setPreview(null);
      await reload();
    } catch (e) {
      pushToast({ title: 'Could not add pack', sub: (e as Error).message, kind: 'error' });
    } finally {
      setAdding(false);
    }
  };

  /** PUT a patch for one pack; surface the backend's message (e.g. the
   *  override⇒scope rule) and re-sync from the server (single source of truth). */
  const patch = async (key: string, body: Parameters<typeof api.updatePack>[1]) => {
    setBusyKey(key);
    try {
      await api.updatePack(key, body);
      await reload();
    } catch (e) {
      pushToast({ title: 'Update rejected', sub: (e as Error).message, kind: 'error' });
      await reload();
    } finally {
      setBusyKey(null);
    }
  };

  const onAuthorityChange = (p: ApiPack, next: 'fallback' | 'override') => {
    if (next === 'override' && p.scope_paths.length === 0) {
      // Can't grant override without a folder — guide the user straight to
      // picking one (and apply override + scope together).
      setPicker({ key: p.key, enableOverride: true });
      return;
    }
    void patch(p.key, { authority: next });
  };

  const onPickFolder = (path: string) => {
    const target = picker;
    setPicker(null);
    if (!target) return;
    const p = packs.find(x => x.key === target.key);
    if (!p) return;
    const scope = Array.from(new Set([...p.scope_paths, path]));
    void patch(p.key, target.enableOverride ? { authority: 'override', scope_paths: scope } : { scope_paths: scope });
  };

  const removeScope = (p: ApiPack, path: string) => {
    const scope = p.scope_paths.filter(x => x !== path);
    if (p.authority === 'override' && scope.length === 0) {
      pushToast({ title: 'Override needs a folder', sub: 'Switch to Fallback first, or add another folder.', kind: 'error' });
      return;
    }
    void patch(p.key, { scope_paths: scope });
  };

  const refresh = async (key: string) => {
    setBusyKey(key);
    try {
      const r = await api.refreshPack(key);
      const rescued = r.rescued ?? 0;
      pushToast(r.last_error
        ? { title: 'Refresh failed', sub: r.last_error, kind: 'error' }
        : { title: 'Pack refreshed', sub: rescued > 0 ? `Rescued ${rescued} unmatched file${rescued === 1 ? '' : 's'}` : undefined, kind: 'success' });
      if (rescued > 0) { try { window.dispatchEvent(new Event('kira:files-changed')); } catch { /* no window */ } }
      await reload();
    } catch (e) {
      pushToast({ title: 'Refresh failed', sub: (e as Error).message, kind: 'error' });
    } finally {
      setBusyKey(null);
    }
  };

  const remove = async (p: ApiPack) => {
    setBusyKey(p.key);
    try {
      await api.deletePack(p.key);
      pushToast({ title: 'Pack removed', sub: p.name, kind: 'success' });
      await reload();
    } catch (e) {
      pushToast({ title: 'Could not remove pack', sub: (e as Error).message, kind: 'error' });
    } finally {
      setBusyKey(null);
    }
  };

  const runRescan = async () => {
    setRescanning(true);
    try {
      const r = await api.rescanPacks();
      if (r.rescued > 0) {
        // Reflect the freshly-matched files in Review immediately — App.tsx
        // listens for this to re-fetch. Without it the rescue landed in the DB
        // but Review stayed stale until a separate Scan forced a refresh.
        window.dispatchEvent(new Event('kira:files-changed'));
      }
      pushToast({
        title: r.rescued > 0 ? `Rescued ${r.rescued} file${r.rescued === 1 ? '' : 's'}` : 'Nothing to rescue',
        sub: r.rescued > 0 ? 'They now show as matched in Review.' : 'No unmatched files were claimed by a pack.',
        kind: r.rescued > 0 ? 'success' : 'info',
      });
    } catch (e) {
      pushToast({ title: 'Re-run failed', sub: (e as Error).message, kind: 'error' });
    } finally {
      setRescanning(false);
    }
  };

  return (
    <SettingsLayout
      header={(
        <SectionHeader
          accent
          icon={<IcSparkles />}
          title="Packs"
          purpose="Teach Kira about fan-edits the metadata providers can't match — One Pace, custom cuts, re-numbered releases. Paste a pack URL and Kira rescues, names, posters, and subtitles those files. Packs only ever touch files Kira couldn't match, so your other titles are never affected."
          status={<BadgeWithDot color={packs.length ? 'brand' : 'gray'}>{packs.length ? `${packs.length} installed` : 'None'}</BadgeWithDot>}
        />
      )}
    >
      <div className="flex flex-col gap-5">
        {/* Safety explainer */}
        <Alert color="info" icon={IcShieldCheck}>
          A pack only rescues files Kira marked <span className="font-mono text-ink">No match</span>. It can never change a title Kira already matched.
          {' '}<a href="https://github.com/Nicolaslahri/Kira/blob/main/docs/KIRA_PACKS.md" target="_blank" rel="noreferrer" className="font-medium underline underline-offset-2 hover:text-primary">How to author a pack →</a>
        </Alert>

        {/* ── Add a pack ───────────────────────────────────────────── */}
        <PackCard
          tint={ACCENT}
          icon={<IcPlus />}
          title="Add a pack"
          desc={<>Paste the URL of a Kira pack JSON file (a GitHub raw link, a gist, any static URL). Kira validates it and shows what it would do before you install.</>}
        >
          <div className="flex flex-col gap-3">
            <div className="flex flex-wrap items-center gap-2">
              <Input
                wrapperClassName="flex-1 min-w-[260px]"
                mono
                value={url}
                placeholder="https://raw.githubusercontent.com/…/one-pace.json"
                invalid={!!url && !isValidHttpUrl(url)}
                aria-invalid={!!url && !isValidHttpUrl(url)}
                onChange={e => { setUrl(e.target.value); setPreview(null); }}
              />
              <Button color="secondary" size="md" iconLeading={IcCheck} isLoading={validating} showTextWhileLoading isDisabled={!url} onClick={() => void runValidate()}>
                Validate
              </Button>
            </div>

            {preview && preview.ok ? (
              <NestedBox className="flex items-start gap-3 p-3.5">
                {preview.poster_url ? (
                  <img src={posterSrc(preview.poster_url) ?? undefined} alt="" className="h-20 w-[3.5rem] shrink-0 rounded-md object-cover ring-1 ring-inset ring-secondary" />
                ) : (
                  <FeaturedIcon size="lg" tint={ACCENT} icon={<IcSparkles />} />
                )}
                <div className="min-w-0 flex-1">
                  <div className="text-[14px] font-semibold text-primary">{preview.title ?? preview.name}{preview.year ? <span className="ml-1.5 font-normal text-tertiary">({preview.year})</span> : null}</div>
                  <div className="mt-1 flex flex-wrap items-center gap-1.5">
                    <BadgeWithDot color="gray">{preview.episode_count ?? 0} episodes</BadgeWithDot>
                    {preview.season_count ? <BadgeWithDot color="gray">{preview.season_count} season{preview.season_count === 1 ? '' : 's'}</BadgeWithDot> : null}
                    {preview.sub_count ? <BadgeWithDot color="gray">{preview.sub_count} subtitles</BadgeWithDot> : null}
                  </div>
                  <div className="mt-2 text-[12px] text-tertiary">
                    {preview.would_rescue && preview.would_rescue > 0 ? (
                      <span className="font-medium text-[var(--conf-high)]">Would rescue {preview.would_rescue} of your unmatched file{preview.would_rescue === 1 ? '' : 's'}.</span>
                    ) : (
                      <span>No current unmatched files match this pack yet — it'll apply on your next scan.</span>
                    )}
                    {preview.sample_files && preview.sample_files.length ? (
                      <span className="mt-0.5 block truncate font-mono text-[11px] text-quaternary">e.g. {preview.sample_files.join(', ')}</span>
                    ) : null}
                  </div>
                </div>
                <Button color="primary" size="sm" iconLeading={IcPlus} isLoading={adding} showTextWhileLoading onClick={() => void runAdd()}>
                  Add pack
                </Button>
              </NestedBox>
            ) : null}
          </div>
        </PackCard>

        {/* ── Installed packs ──────────────────────────────────────── */}
        {loading ? (
          <div className="flex items-center gap-2 px-1 text-[13px] text-tertiary"><span className="[&_svg]:size-4"><IcSpin /></span> Loading packs…</div>
        ) : packs.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-white/[0.14] bg-white/[0.02] p-6 text-center text-[13px] text-tertiary">
            No packs installed yet. Add one above to organize fan-edits like One Pace.
          </div>
        ) : (
          <>
            <div className="flex items-center justify-between gap-3 px-1">
              <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-quaternary">Installed</span>
              <Button color="secondary" size="sm" iconLeading={IcRefresh} isLoading={rescanning} showTextWhileLoading onClick={() => void runRescan()}>
                Re-run on unmatched files
              </Button>
            </div>

            {packs.map(p => {
              const busy = busyKey === p.key;
              return (
                <PackCard
                  key={p.key}
                  tint={ACCENT}
                  connected={p.enabled && p.resolved}
                  icon={p.poster_url
                    ? <img src={posterSrc(p.poster_url) ?? undefined} alt="" className="size-full rounded-[inherit] object-cover" />
                    : <IcSparkles />}
                  title={<span className="flex items-center gap-2">{p.title ?? p.name}{!p.resolved ? <BadgeWithDot color="error">Unreachable</BadgeWithDot> : null}</span>}
                  desc={p.resolved
                    ? <span className="flex flex-wrap items-center gap-x-3 gap-y-0.5"><span>{p.episode_count ?? 0} episodes</span>{p.sub_count ? <span className="inline-flex items-center gap-1"><span className="[&_svg]:size-3.5"><IcCaption /></span>{p.sub_count} subtitles</span> : null}<span className="truncate font-mono text-[11px] text-quaternary">{p.url}</span></span>
                    : <span className="text-[var(--conf-low)]">{p.last_error ?? 'Could not fetch this pack.'}</span>}
                  headerExtra={
                    <div className="flex items-center gap-2">
                      <span className="text-[11px] text-quaternary">{p.enabled ? 'On' : 'Off'}</span>
                      <Toggle isSelected={p.enabled} isDisabled={busy} onChange={() => void patch(p.key, { enabled: !p.enabled })} aria-label="Enable pack" />
                    </div>
                  }
                >
                  <div className="flex flex-col gap-3">
                    {/* Authority + subtitles */}
                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                      <div className="flex items-center gap-3">
                        <span className="w-20 shrink-0 text-[12px] font-medium text-ink-muted">Authority</span>
                        <Select<string>
                          style={{ flex: 1, minWidth: 0 }}
                          value={p.authority}
                          onChange={v => onAuthorityChange(p, v as 'fallback' | 'override')}
                          options={[
                            { value: 'fallback', label: 'Fallback — rescue only' },
                            { value: 'override', label: 'Override providers (scoped)' },
                          ]}
                          aria-label="Pack authority"
                        />
                      </div>
                      <div className={`flex items-center justify-between gap-3 px-3.5 py-2.5 rounded-xl bg-tertiary ring-1 ring-inset ring-secondary`}>
                        <span className="inline-flex items-center gap-1.5 text-[13px] text-ink"><span className="[&_svg]:size-4"><IcCaption /></span>Fetch pack subtitles</span>
                        <Toggle isSelected={p.subtitles} isDisabled={busy} onChange={() => void patch(p.key, { subtitles: !p.subtitles })} aria-label="Fetch pack subtitles" />
                      </div>
                    </div>

                    {p.authority === 'override' ? (
                      <Alert color="warning" icon={IcAlertTri}>
                        Override lets this pack replace correct provider matches, so it's restricted to the folders below. It only ever acts inside them.
                      </Alert>
                    ) : null}

                    {/* Scope folders */}
                    <div>
                      <div className="mb-1.5 flex items-center justify-between gap-2">
                        <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-muted">
                          Folders {p.authority === 'override' ? '(required)' : '(optional — empty = whole library)'}
                        </span>
                        <Button color="link-color" size="sm" iconLeading={IcFolder} onClick={() => setPicker({ key: p.key, enableOverride: false })}>
                          Add folder
                        </Button>
                      </div>
                      {p.scope_paths.length ? (
                        <div className="flex flex-wrap gap-1.5">
                          {p.scope_paths.map(path => (
                            <span key={path} className="inline-flex items-center gap-1.5 rounded-lg bg-tertiary px-2.5 py-1 font-mono text-[11.5px] text-ink ring-1 ring-inset ring-secondary">
                              {path}
                              <button type="button" aria-label={`Remove ${path}`} className="grid size-4 place-items-center rounded text-ink-soft transition-colors hover:bg-glass-2 hover:text-ink [&_svg]:size-3" onClick={() => removeScope(p, path)}>
                                <IcX />
                              </button>
                            </span>
                          ))}
                        </div>
                      ) : (
                        <span className="text-[12px] text-quaternary">Applies anywhere in your library (still no-match-only).</span>
                      )}
                    </div>

                    {/* Footer actions */}
                    <div className="flex flex-wrap items-center justify-between gap-2 border-t border-white/[0.08] pt-3">
                      <span className="text-[11px] text-quaternary">{p.last_fetched ? `Refreshed ${new Date(p.last_fetched).toLocaleString()}` : 'Not yet refreshed'}</span>
                      <div className="flex items-center gap-2">
                        <Button color="secondary" size="sm" iconLeading={IcRefresh} isDisabled={busy} onClick={() => void refresh(p.key)}>Refresh</Button>
                        <Button color="secondary-destructive" size="sm" iconLeading={IcTrash} isDisabled={busy} onClick={() => void remove(p)}>Remove</Button>
                      </div>
                    </div>
                  </div>
                </PackCard>
              );
            })}
          </>
        )}
      </div>

      {picker ? (
        <FolderPickerModal onPick={onPickFolder} onClose={() => setPicker(null)} />
      ) : null}
    </SettingsLayout>
  );
}
