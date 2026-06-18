import { useEffect, useState, type Dispatch, type SetStateAction } from 'react';
import { IcX, IcFolder, IcScan, IcPlus, IcTrash, IcFilm, IcTv, IcAnime, IcMusic } from '../../lib/icons';
import { Select } from '../../components/ui';
import { SettingsLayout, SectionCard, NestedBox, NumberField, SETTINGS_NESTED, SectionHeader, StatusPill } from '../../components/settings-blocks';
import { Button } from '../../components/base/buttons/button';
import { Input } from '../../components/base/input/input';
import { Toggle } from '../../components/base/toggle/toggle';
import { FolderPickerModal } from '../../components/FolderPickerModal';
import { api } from '../../lib/api';
import { strSetting, type SaveKeyFn } from './helpers';

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

  const typeRows = [
    { label: 'Movies', sub: 'Movies', value: targetMovie, key: 'paths.targets.movie' as const, pickerFor: 'target-movie' as const, icon: <IcFilm />, color: '#bdc1d0' },
    { label: 'TV',     sub: 'TV',     value: targetTv,    key: 'paths.targets.tv'    as const, pickerFor: 'target-tv'    as const, icon: <IcTv />,   color: '#49b8fe' },
    { label: 'Anime',  sub: 'Anime',  value: targetAnime, key: 'paths.targets.anime' as const, pickerFor: 'target-anime' as const, icon: <IcAnime />, color: 'var(--media-anime)' },
    { label: 'Music',  sub: 'Music',  value: targetMusic, key: 'paths.targets.music' as const, pickerFor: 'target-music' as const, icon: <IcMusic />, color: 'var(--media-music)' },
  ];

  const overrideCount = [targetMovie, targetTv, targetAnime, targetMusic].filter(Boolean).length;
  return (
    <SettingsLayout
      header={(
        <SectionHeader
          icon={<IcFolder />}
          title="Paths"
          purpose="Tell Kira where your media lives and where renamed files should land — your media root, the folders it watches, and optional per-type destinations."
          status={(
            <StatusPill tone={watchFolders.length > 0 || watchCfg.auto_scan ? 'connected' : 'neutral'} breathe={watchCfg.auto_scan}>
              {watchFolders.length} watched{overrideCount > 0 ? ` · ${overrideCount} routed` : ''}{watchCfg.auto_scan ? ' · auto' : ''}
            </StatusPill>
          )}
        />
      )}
    >
      {/* Two independent columns (not a row-aligned grid): cards pack by
          height within each column, so a short card never leaves a hole
          beside a tall neighbour. */}
      <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-2">
        <div className="flex flex-col gap-4">
        {/* Media root */}
        <SectionCard
          icon={<IcFolder />}
          title="Media root"
          desc="Where Kira renames into — the move / hardlink target for every file."
        >
          <PathField
            value={libraryRoot}
            onChange={v => saveKey('paths.library_root')(v)}
            onBrowse={() => setPicker({ for: 'library', initial: libraryRoot })}
          />
          {persistence !== null && (
            <div className="mt-2.5 flex items-center gap-2 text-[11.5px] leading-relaxed text-ink-soft">
              <span className={`size-1.5 shrink-0 rounded-full ${persistence === 'native' ? 'bg-conf-high' : 'bg-info'}`} />
              {persistence === 'native'
                ? <>Match memory: stored on the files themselves (xattr) — survives database resets and manual moves.</>
                : <>Match memory: this volume can't hold file metadata, so renamed files are remembered in Kira's local index — survives database resets, but not files you move by hand.</>}
            </div>
          )}
        </SectionCard>

        {/* Per-type destinations */}
        <SectionCard
          icon={<IcFolder />}
          title="Per-type destinations"
          desc={<>Optional — route each media type to its own folder or drive. Blank lands at <span className="font-mono text-ink">Media root / Type</span>.</>}
        >
          <div className="flex flex-col gap-2.5">
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
            <div className={`px-3 py-2.5 ${SETTINGS_NESTED}`}>
              <div className="text-[12px] font-medium text-ink-muted">Ignore patterns</div>
              <div className="mt-1.5">
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
              <div className="mt-1.5 text-[11px] leading-relaxed text-ink-soft">
                Comma-separated globs, matched against file and folder names. Samples, trailers and extras are always skipped regardless.
              </div>
            </div>
          </div>
        </SectionCard>
        </div>

        {/* Watch folders + Auto-scan */}
        <div className="flex flex-col gap-4">
        <SectionCard
          icon={<IcScan />}
          title="Watch folders"
          desc="Folders Kira scans when you click “Scan now”."
          action={(
            <Button color="secondary" size="sm" iconLeading={IcPlus} className="shrink-0" onClick={() => setPicker({ for: 'watch', initial: libraryRoot })}>
              Add folder
            </Button>
          )}
        >
          <div className="flex flex-1 flex-col gap-2">
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
        </SectionCard>

      {/* Auto-scan (watched folders) — grows when expanded */}
      <SectionCard
        icon={<IcScan />}
        title="Auto-scan"
        desc="Watch your folders and scan automatically when new files appear — no need to click “Scan now”. Detected via filesystem events with a periodic poll fallback for network drives."
        headerExtra={(
          <Toggle
            isSelected={watchCfg.auto_scan}
            onChange={() => void saveWatchCfg({ ...watchCfg, auto_scan: !watchCfg.auto_scan })}
            className="mt-0.5"
            aria-label="Enable auto-scan"
          />
        )}
      >
        {watchCfg.auto_scan ? (
          <div className="flex flex-col gap-4">
            {/* timing */}
            <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
              <NestedBox className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="text-[13px] font-medium text-ink">Settle delay</div>
                  <div className="mt-0.5 text-[12px] leading-relaxed text-ink-muted">Wait after the last change before scanning.</div>
                </div>
                <NumberField
                  className="w-28 shrink-0"
                  suffix="sec"
                  min={5}
                  value={watchCfg.debounce_seconds}
                  onChange={v => void saveWatchCfg({ ...watchCfg, debounce_seconds: Math.max(5, v) })}
                />
              </NestedBox>
              <NestedBox className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="text-[13px] font-medium text-ink">Poll interval</div>
                  <div className="mt-0.5 text-[12px] leading-relaxed text-ink-muted">Fallback re-check for network drives.</div>
                </div>
                <NumberField
                  className="w-28 shrink-0"
                  suffix="sec"
                  min={60}
                  value={watchCfg.poll_interval_seconds}
                  onChange={v => void saveWatchCfg({ ...watchCfg, poll_interval_seconds: Math.max(60, v) })}
                />
              </NestedBox>
            </div>

            {/* per-folder mode */}
            <div className="flex flex-col gap-2">
              <div className="text-[12px] font-medium text-ink-muted">Per-folder behaviour</div>
              {Array.from(new Set([libraryRoot, ...watchFolders])).filter(Boolean).map(path => {
                const fc = folderModeFor(path);
                return (
                  <NestedBox key={path} className="flex flex-wrap items-center gap-2.5 px-3 py-2.5">
                    <IcFolder style={{ width: 14, height: 14 }} className="shrink-0 text-ink-soft" />
                    <span className="min-w-0 flex-1 truncate font-mono text-[12.5px] text-ink-muted">{path}</span>
                    <Select<FolderCfg['mode']>
                      style={{ width: 220 }}
                      value={fc.mode}
                      onChange={mode => void saveWatchCfg({ ...watchCfg, folders: { ...watchCfg.folders, [path]: { ...fc, mode } } })}
                      options={[
                        { value: 'scan', label: 'Scan + match only' },
                        { value: 'auto_rename', label: 'Auto-rename high-confidence' },
                      ]}
                    />
                    {fc.mode === 'auto_rename' ? (
                      <label className="flex items-center gap-1.5 text-[12px] text-ink-soft">
                        ≥
                        <NumberField
                          className="w-20"
                          min={0} max={100} step={1}
                          value={Math.round(fc.threshold * 100)}
                          onChange={v => {
                            const threshold = Math.min(1, Math.max(0, v / 100));
                            void saveWatchCfg({ ...watchCfg, folders: { ...watchCfg.folders, [path]: { ...fc, threshold } } });
                          }}
                        />
                        % conf
                      </label>
                    ) : null}
                  </NestedBox>
                );
              })}
              <p className="text-[11px] leading-relaxed text-ink-soft">
                <span className="font-medium text-ink-muted">Scan + match only</span> surfaces new files in Review for you to approve.{' '}
                <span className="font-medium text-ink-muted">Auto-rename</span> additionally organizes matches at or above the confidence
                threshold automatically, using your default file operation (hardlink by default — non-destructive, the original stays put). Sub-threshold files still wait in Review.
              </p>
            </div>
          </div>
        ) : null}
      </SectionCard>
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
    </SettingsLayout>
  );
}
