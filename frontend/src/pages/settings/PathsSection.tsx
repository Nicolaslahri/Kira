import { useEffect, useState, type Dispatch, type SetStateAction, type ReactNode } from 'react';
import { IcFilm, IcTv, IcAnime, IcMusic } from '../../lib/icons';
import { Folder, Scan, Plus, Trash01, XClose } from '@untitledui/icons';
import { Select } from '../../components/ui';
import { SettingsLayout, SectionHeader } from '../../components/settings-blocks';
import { Button } from '../../components/base/buttons/button';
import { Input } from '../../components/base/input/input';
import { InputNumber } from '../../components/base/input/input-number';
import { Toggle } from '../../components/base/toggle/toggle';
import { FeaturedIcon } from '../../components/base/featured-icons/featured-icon';
import { BadgeWithDot } from '../../components/base/badges/badges';
import { FolderPickerModal } from '../../components/FolderPickerModal';
import { cn } from '../../lib/utils';
import { api } from '../../lib/api';
import { strSetting, type SaveKeyFn } from './helpers';

const PATH_ICON_BTN = 'grid size-7 shrink-0 place-items-center rounded-md text-tertiary transition-colors hover:bg-white/[0.07] hover:text-primary [&_svg]:size-[14px]';

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
      editGate
      value={value}
      placeholder={placeholder}
      spellCheck={false}
      onChange={e => onChange(e.target.value)}
      trailing={
        <div className="flex items-center gap-0.5">
          {onClear && value ? (
            <button type="button" className={PATH_ICON_BTN + ' hover:text-error-primary'} onClick={onClear} title="Clear override — use the default location">
              <XClose />
            </button>
          ) : null}
          <button type="button" className={PATH_ICON_BTN} onClick={onBrowse} title={browseTitle ?? 'Browse for folder'}>
            <Folder />
          </button>
        </div>
      }
    />
  );
}

// Flow-rail end-cap node (Sources / Destinations). The centre "Kira" core is
// rendered inline since it's the only filled-indigo chip on the rail.
function FlowNode({ icon, label, caption, accent }: { icon: ReactNode; label: string; caption: ReactNode; accent?: boolean }) {
  return (
    <div className="flex min-w-0 shrink-0 items-center gap-2.5 rounded-xl bg-tertiary px-3 py-2 ring-1 ring-inset ring-secondary">
      <FeaturedIcon size="sm" icon={icon} tint={accent ? 'var(--accent)' : undefined} color="gray" />
      <div className="min-w-0">
        <div className="text-[12.5px] font-semibold text-primary">{label}</div>
        <div className="truncate text-[11px] text-tertiary">{caption}</div>
      </div>
    </div>
  );
}

export function PathsSection({
  rawSettings,
  setRawSettings,
  saveKey,
}: {
  rawSettings: Record<string, unknown>;
  /** Direct setter — needed for multi-value writes (like the watch-folders
   *  array / watch.config blob) that don't fit `saveKey`'s single-value shape.
   *  Writes the DRAFT only; the parent's Save bar persists it. */
  setRawSettings: Dispatch<SetStateAction<Record<string, unknown>>>;
  saveKey: SaveKeyFn;
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

  // Match-persistence probe: can ID stamps live ON the files (xattr / NTFS
  // ADS) or does this volume force the Kira-side index? Purely informational —
  // both modes work — but "stamping silently no-ops here" used to be invisible.
  const [persistence, setPersistence] = useState<'native' | 'index' | null>(null);
  useEffect(() => {
    let cancelled = false;
    void api.getPersistence()
      .then(p => { if (!cancelled) setPersistence(p.mode); })
      .catch(() => { /* informational only — stay silent on failure */ });
    return () => { cancelled = true; };
  }, [libraryRoot]);

  // watch_folders stored as JSON array; fall back to single root.
  const watchFolders: string[] = (() => {
    const v = rawSettings['paths.watch_folders'];
    if (Array.isArray(v)) return v.filter((x): x is string => typeof x === 'string');
    return [];
  })();

  // User scan-ignore globs — stored as an array, edited as comma-separated
  // text. Matched case-insensitively against filenames AND folder names
  // (e.g. `*.partial.mkv`, `Anime Music Videos`, `*[OPED]*`). Applied on the
  // next scan; built-in sample/trailer/extras filtering always stays on.
  const ignorePatterns: string[] = (() => {
    const v = rawSettings['scanning.ignore_patterns'];
    return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [];
  })();
  const [ignoreText, setIgnoreText] = useState<string | null>(null);
  const commitIgnores = () => {
    if (ignoreText === null) return;
    const arr = ignoreText.split(',').map(s => s.trim()).filter(Boolean);
    // Array value — outside saveKey's scalar shape. Draft only; Save persists.
    setRawSettings(s => ({ ...s, 'scanning.ignore_patterns': arr }));
    setIgnoreText(null);
  };

  // Draft only — add/remove a watch folder updates the local list immediately;
  // the parent's Save bar writes it. (No per-action PUT/toast anymore.)
  const setWatch = (next: string[]) => {
    setRawSettings(s => ({ ...s, 'paths.watch_folders': next }));
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

  const saveWatchCfg = (next: WatchCfg) => {
    // Draft only — the parent's Save bar persists watch.config (the backend
    // re-arms the watcher daemon on that PUT, exactly as before).
    setRawSettings(s => ({ ...s, 'watch.config': next as unknown as Record<string, unknown>[string] }));
  };

  const folderModeFor = (path: string): FolderCfg =>
    watchCfg.folders[path] ?? { mode: 'scan', threshold: 0.9 };

  // Per-type routing lanes — coloured in each media type's own hue (Movies
  // teal, TV sky, Anime purple, Music amber) so the lane colour carries real
  // meaning: dim when inheriting the root, full-saturation when routed away.
  const typeRows = [
    { label: 'Movies', sub: 'Movies', value: targetMovie, key: 'paths.targets.movie' as const, pickerFor: 'target-movie' as const, icon: <IcFilm />,  color: '#4ec5b3' },
    { label: 'TV',     sub: 'TV',     value: targetTv,    key: 'paths.targets.tv'    as const, pickerFor: 'target-tv'    as const, icon: <IcTv />,    color: '#b3e5fc' },
    { label: 'Anime',  sub: 'Anime',  value: targetAnime, key: 'paths.targets.anime' as const, pickerFor: 'target-anime' as const, icon: <IcAnime />, color: 'var(--media-anime)' },
    { label: 'Music',  sub: 'Music',  value: targetMusic, key: 'paths.targets.music' as const, pickerFor: 'target-music' as const, icon: <IcMusic />, color: 'var(--media-music)' },
  ];

  const overrideCount = [targetMovie, targetTv, targetAnime, targetMusic].filter(Boolean).length;

  return (
    <SettingsLayout
      header={(
        <SectionHeader
          icon={<Folder />}
          title="Library & paths"
          purpose="Where your media lives, where renamed files land, and how Kira watches for new files — read it like a pipeline: sources → Kira → destinations."
          status={(
            <BadgeWithDot color={watchFolders.length > 0 || watchCfg.auto_scan ? 'success' : 'gray'} pulse={watchCfg.auto_scan}>
              {watchFolders.length} watched{overrideCount > 0 ? ` · ${overrideCount} routed` : ''}{watchCfg.auto_scan ? ' · auto' : ''}
            </BadgeWithDot>
          )}
        />
      )}
    >
      <div className="flex flex-col gap-5">

        {/* ── FLOW RAIL — the pipeline at a glance: sources → Kira → destinations.
              The trunk wires + Kira core glow/pulse only while auto-scan is armed. */}
        <div className="overflow-hidden rounded-2xl bg-secondary px-5 py-4 shadow-xs ring-1 ring-inset ring-secondary">
          <div className="flex items-center gap-3">
            <FlowNode icon={<Scan />} label="Sources" caption={`${watchFolders.length} folder${watchFolders.length === 1 ? '' : 's'} watched`} />

            <div className="relative hidden h-px min-w-[20px] flex-1 sm:block" style={{ background: watchCfg.auto_scan ? 'var(--accent-50)' : 'var(--line-strong)' }}>
              {watchCfg.auto_scan ? <span className="absolute left-1/2 top-1/2 size-1.5 -translate-x-1/2 -translate-y-1/2 animate-pulse rounded-full" style={{ background: 'var(--accent)' }} /> : null}
            </div>

            {/* Kira core — the only filled-indigo chip; glows when armed */}
            <div className="flex shrink-0 items-center gap-2 rounded-xl px-3.5 py-2.5" style={{ background: 'var(--accent-deep)', boxShadow: watchCfg.auto_scan ? '0 0 30px -8px var(--accent)' : undefined }}>
              <span className="text-white [&_svg]:size-[18px]"><Scan /></span>
              <span className="text-[12px] font-semibold uppercase tracking-[0.08em] text-white">Kira</span>
            </div>

            <div className="relative hidden h-px min-w-[20px] flex-1 sm:block" style={{ background: watchCfg.auto_scan ? 'var(--accent-50)' : 'var(--line-strong)' }}>
              {watchCfg.auto_scan ? <span className="absolute left-1/2 top-1/2 size-1.5 -translate-x-1/2 -translate-y-1/2 animate-pulse rounded-full" style={{ background: 'var(--accent)', animationDelay: '0.6s' }} /> : null}
            </div>

            <div className="flex shrink-0 items-center gap-2.5">
              <FlowNode icon={<Folder />} label="Destinations" caption={overrideCount > 0 ? `media root · +${overrideCount} routed` : 'media root'} accent />
              {/* per-type routing dots — lit when that type is routed off the trunk */}
              <div className="hidden flex-col gap-1 md:flex">
                {typeRows.map(r => (
                  <span key={r.key} className="size-1.5 rounded-full" title={r.value ? `${r.label}: routed` : `${r.label}: inherits root`} style={{ background: r.value ? r.color : `color-mix(in srgb, ${r.color} 30%, transparent)` }} />
                ))}
              </div>
            </div>
          </div>
        </div>

        {/* ── MEDIA ROOT — the hero destination + match-memory verdict ── */}
        <section className="overflow-hidden rounded-2xl p-5 shadow-xs" style={{ background: 'radial-gradient(130% 130% at 92% -25%, var(--accent-8), transparent 55%), var(--color-bg-secondary)', boxShadow: 'inset 0 0 0 1px var(--accent-line)' }}>
          <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-quaternary">
            <span className="text-[var(--accent)] [&_svg]:size-3.5"><Folder /></span>
            Media root · renames into
          </div>
          <div className="mt-3">
            <PathField
              value={libraryRoot}
              onChange={v => saveKey('paths.library_root')(v)}
              onBrowse={() => setPicker({ for: 'library', initial: libraryRoot })}
            />
          </div>
          {persistence !== null && (
            <div className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-tertiary">
              <span className="text-secondary">Match memory:</span>
              <span
                className="inline-flex items-center gap-2 rounded-full px-2.5 py-1 text-[11.5px] font-semibold"
                style={persistence === 'native'
                  ? { color: 'var(--conf-high)', background: 'var(--conf-high-bg)', boxShadow: 'inset 0 0 0 1px color-mix(in srgb, var(--conf-high) 30%, transparent)' }
                  : { color: 'var(--info-bright)', background: 'var(--info-bg)', boxShadow: 'inset 0 0 0 1px color-mix(in srgb, var(--info) 30%, transparent)' }}
              >
                <span className="settings-dot-live size-1.5 rounded-full" style={{ background: persistence === 'native' ? 'var(--conf-high)' : 'var(--info-bright)' }} />
                {persistence === 'native' ? 'On the files (xattr)' : "In Kira's index"}
              </span>
              <span>{persistence === 'native' ? '— survives database resets and manual moves.' : '— survives resets, but not files you move by hand.'}</span>
            </div>
          )}
        </section>

        {/* ── PER-TYPE DESTINATIONS — colour-owned routing lanes ── */}
        <section>
          <div className="mb-2.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-quaternary">Per-type destinations · optional</div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {typeRows.map(row => {
              const routed = !!row.value;
              return (
                <div
                  key={row.key}
                  className="relative overflow-hidden rounded-xl bg-secondary p-3.5 shadow-xs transition-shadow"
                  style={{ boxShadow: routed ? `inset 0 0 0 1px color-mix(in srgb, ${row.color} 38%, transparent)` : 'inset 0 0 0 1px var(--color-border-secondary)' }}
                >
                  {/* colour rail — dim when inheriting, full media-colour when routed */}
                  <span aria-hidden className="absolute inset-y-0 left-0 w-[3px] transition-colors" style={{ background: routed ? row.color : `color-mix(in srgb, ${row.color} 16%, transparent)` }} />
                  <div className="flex items-center gap-2.5">
                    <FeaturedIcon size="md" tint={row.color} icon={row.icon} />
                    <div className="min-w-0">
                      <div className="text-[13px] font-semibold text-primary">{row.label}</div>
                      <div className="mt-0.5 inline-flex items-center gap-1.5 text-[11px] font-medium" style={{ color: routed ? row.color : 'var(--color-text-tertiary)' }}>
                        <span className="size-1.5 rounded-full" style={{ background: routed ? row.color : 'rgba(255,255,255,0.22)' }} />
                        {routed ? 'Routed here' : 'Inherits root'}
                      </div>
                    </div>
                  </div>
                  <div className="mt-3">
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
              );
            })}
          </div>
        </section>

        {/* ── SOURCES — folders Kira watches + the ignore filter on intake ── */}
        <section className="rounded-2xl bg-secondary p-5 shadow-xs ring-1 ring-inset ring-secondary">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2.5">
              <FeaturedIcon size="md" icon={<Scan />} tint="var(--accent)" />
              <div>
                <div className="text-[14px] font-semibold text-primary">Folders Kira watches</div>
                <div className="text-[12px] text-tertiary">Scanned when you click &ldquo;Scan now&rdquo;.</div>
              </div>
            </div>
            <Button color="secondary" size="sm" iconLeading={Plus} className="shrink-0" onClick={() => setPicker({ for: 'watch', initial: libraryRoot })}>Add folder</Button>
          </div>
          <div className="mt-4 flex flex-col gap-2">
            {watchFolders.length === 0 ? (
              <div className="flex flex-col items-center gap-1.5 rounded-xl border border-dashed border-[var(--line-strong)] px-4 py-7 text-center">
                <span className="text-tertiary [&_svg]:size-5"><Scan /></span>
                <div className="text-[12.5px] font-medium text-secondary">Nothing watched yet</div>
                <div className="text-[11.5px] text-tertiary">Point Kira at a folder and it&rsquo;ll scan it for new media.</div>
              </div>
            ) : null}
            {watchFolders.map((p, i) => (
              <div key={i} className="group flex items-center gap-2.5 rounded-xl bg-tertiary px-3 py-2.5 ring-1 ring-inset ring-secondary">
                <Folder style={{ width: 14, height: 14 }} className="shrink-0 text-tertiary" />
                <span className="min-w-0 flex-1 truncate font-mono text-[12.5px] text-secondary">{p}</span>
                <button
                  type="button"
                  title="Remove"
                  onClick={() => void setWatch(watchFolders.filter((_, j) => j !== i))}
                  className="grid size-7 shrink-0 place-items-center rounded-md text-tertiary opacity-0 transition-all hover:bg-error-secondary hover:text-error-primary group-hover:opacity-100 [&_svg]:size-[13px]"
                >
                  <Trash01 />
                </button>
              </div>
            ))}
          </div>

          {/* Ignore filter — acts on the intake side, before files reach Kira */}
          <div className="mt-4 border-t border-secondary pt-4">
            <div className="flex items-center gap-2 text-[12px] font-medium text-secondary">
              <span className="text-tertiary [&_svg]:size-3.5"><XClose /></span>
              Ignore patterns
            </div>
            <div className="mt-2">
              <Input
                mono
                spellCheck={false}
                value={ignoreText ?? ignorePatterns.join(', ')}
                placeholder="*.partial.mkv, Anime Music Videos, *NCOP*"
                onChange={e => setIgnoreText(e.target.value)}
                onBlur={commitIgnores}
                onKeyDown={e => { if (e.key === 'Enter') commitIgnores(); }}
              />
            </div>
            <div className="mt-1.5 text-[11px] leading-relaxed text-tertiary">
              Comma-separated globs, matched against file and folder names. Samples, trailers and extras are always skipped regardless.
            </div>
          </div>
        </section>

        {/* ── AUTO-SCAN — the engine: watch + act automatically ── */}
        <section className="overflow-hidden rounded-2xl bg-secondary shadow-xs ring-1 ring-inset ring-secondary" style={watchCfg.auto_scan ? { boxShadow: 'inset 0 0 0 1px var(--accent-line)' } : undefined}>
          <div className="flex items-center justify-between gap-3 p-5">
            <div className="flex items-center gap-2.5">
              <FeaturedIcon size="md" icon={<Scan />} tint="var(--accent)" />
              <div>
                <div className="flex items-center gap-2 text-[14px] font-semibold text-primary">
                  Auto-scan
                  {watchCfg.auto_scan ? <BadgeWithDot color="success" pulse>armed</BadgeWithDot> : null}
                </div>
                <div className="text-[12px] leading-relaxed text-tertiary">Watch your folders and scan automatically when new files appear — no need to click &ldquo;Scan now&rdquo;.</div>
              </div>
            </div>
            <Toggle isSelected={watchCfg.auto_scan} onChange={() => void saveWatchCfg({ ...watchCfg, auto_scan: !watchCfg.auto_scan })} aria-label="Enable auto-scan" />
          </div>

          {/* CSS-only collapse via grid-rows 0fr→1fr (same trick as the provider cards). */}
          <div className={cn('grid transition-[grid-template-rows] duration-200 ease-out motion-reduce:transition-none', watchCfg.auto_scan ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]')}>
            <div className="overflow-hidden">
              <div className="flex flex-col gap-4 border-t border-secondary p-5">
                {/* timing */}
                <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
                  <div className="rounded-xl bg-tertiary p-3.5 ring-1 ring-inset ring-secondary">
                    <div className="text-[13px] font-medium text-primary">Settle delay</div>
                    <div className="mt-0.5 text-[11.5px] leading-relaxed text-tertiary">Wait after the last change before scanning.</div>
                    <InputNumber wrapperClassName="mt-2.5 w-full" minValue={5} value={watchCfg.debounce_seconds} onChange={v => { if (Number.isFinite(v)) void saveWatchCfg({ ...watchCfg, debounce_seconds: Math.max(5, v) }); }} />
                  </div>
                  <div className="rounded-xl bg-tertiary p-3.5 ring-1 ring-inset ring-secondary">
                    <div className="text-[13px] font-medium text-primary">Poll interval</div>
                    <div className="mt-0.5 text-[11.5px] leading-relaxed text-tertiary">Fallback re-check for network drives.</div>
                    <InputNumber wrapperClassName="mt-2.5 w-full" minValue={60} value={watchCfg.poll_interval_seconds} onChange={v => { if (Number.isFinite(v)) void saveWatchCfg({ ...watchCfg, poll_interval_seconds: Math.max(60, v) }); }} />
                  </div>
                </div>

                {/* per-folder behaviour — a status board; armed rows carry an indigo rail + ping */}
                <div className="flex flex-col gap-1.5">
                  <div className="text-[12px] font-medium text-secondary">Per-folder behaviour</div>
                  <div className="overflow-hidden rounded-xl ring-1 ring-inset ring-secondary">
                    {Array.from(new Set([libraryRoot, ...watchFolders])).filter(Boolean).map(path => {
                      const fc = folderModeFor(path);
                      const armed = fc.mode === 'auto_rename';
                      return (
                        <div
                          key={path}
                          className="flex flex-wrap items-center gap-2.5 border-t border-secondary px-3.5 py-2.5 first:border-t-0"
                          style={armed ? { boxShadow: 'inset 3px 0 0 0 var(--accent)' } : undefined}
                        >
                          <span className="relative flex size-1.5 shrink-0">
                            {armed ? <span className="absolute inline-flex size-full animate-ping rounded-full opacity-60" style={{ background: 'var(--accent)' }} /> : null}
                            <span className="relative inline-flex size-1.5 rounded-full" style={{ background: armed ? 'var(--accent)' : 'var(--ink-3)' }} />
                          </span>
                          <span className="min-w-0 flex-1 truncate font-mono text-[12.5px] text-secondary">{path}</span>
                          <Select<FolderCfg['mode']>
                            aria-label="Folder behaviour"
                            style={{ width: 210 }}
                            value={fc.mode}
                            onChange={mode => void saveWatchCfg({ ...watchCfg, folders: { ...watchCfg.folders, [path]: { ...fc, mode } } })}
                            options={[
                              { value: 'scan', label: 'Scan + match only' },
                              { value: 'auto_rename', label: 'Auto-rename high-confidence' },
                            ]}
                          />
                          {armed ? (
                            <label className="flex items-center gap-1.5 text-[12px] text-tertiary">
                              ≥
                              <InputNumber
                                wrapperClassName="w-24"
                                minValue={0} maxValue={100} step={1}
                                value={Math.round(fc.threshold * 100)}
                                onChange={v => {
                                  if (!Number.isFinite(v)) return;
                                  const threshold = Math.min(1, Math.max(0, v / 100));
                                  void saveWatchCfg({ ...watchCfg, folders: { ...watchCfg.folders, [path]: { ...fc, threshold } } });
                                }}
                              />
                              % conf
                            </label>
                          ) : null}
                        </div>
                      );
                    })}
                  </div>
                  <p className="text-[11px] leading-relaxed text-tertiary">
                    <span className="font-medium text-secondary">Scan + match only</span> surfaces new files in Review for you to approve.{' '}
                    <span className="font-medium text-secondary">Auto-rename</span> additionally organizes matches at or above the confidence threshold automatically, using your default file operation (hardlink by default — non-destructive, the original stays put). Sub-threshold files still wait in Review.
                  </p>
                </div>
              </div>
            </div>
          </div>
        </section>

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
    </SettingsLayout>
  );
}
