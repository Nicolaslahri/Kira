import { useRef, useState } from 'react';
import { IcSettings, IcFilm, IcAlertTri, IcDownload, IcRefresh } from '../../lib/icons';
import { Select } from '../../components/ui';
import { SettingsLayout, SectionCard, SettingRow, NumberField, NestedBox, SectionHeader, StatusPill, ProviderField } from '../../components/settings-blocks';
import { Button } from '../../components/base/buttons/button';
import { Input } from '../../components/base/input/input';
import { Toggle } from '../../components/base/toggle/toggle';
import { api } from '../../lib/api';
import { strSetting, type SaveKeyFn, type PushToast } from './helpers';

export function AdvancedSection({
  rawSettings,
  saveKey,
  pushToast,
}: {
  rawSettings: Record<string, unknown>;
  saveKey: SaveKeyFn;
  pushToast: PushToast;
}) {
  // Retention round-trip: settings stores `0` for "forever" (no upper
  // bound) and N for "N days". The select uses the string 'forever' for
  // the unbounded option. Map 0 → 'forever' on read so the select
  // doesn't fall back to its first option after a save.
  const retentionRaw = rawSettings['history.retention_days'];
  const retention =
    retentionRaw === 0 || retentionRaw === '0' ? 'forever' :
    typeof retentionRaw === 'number' ? String(retentionRaw) :
    typeof retentionRaw === 'string' && retentionRaw !== '' ? retentionRaw :
    '30';
  const concurrency = typeof rawSettings['rename.concurrency'] === 'number'
    ? rawSettings['rename.concurrency'] as number
    : 4;
  // MediaInfo (Phase 16) toggles — BOTH default OFF to match the backend, which
  // treats an unset key as False (`_read_mediainfo_setting` /
  // `_read_mediainfo_authoritative_setting`). Enrichment runs in the background
  // so it never slows a scan, but it's still opt-in: it does a per-file
  // container read (a NAS round-trip each).
  const readMediainfo = typeof rawSettings['parsing.read_mediainfo'] === 'boolean'
    ? rawSettings['parsing.read_mediainfo'] as boolean : false;
  const mediainfoAuthoritative = typeof rawSettings['parsing.mediainfo_authoritative'] === 'boolean'
    ? rawSettings['parsing.mediainfo_authoritative'] as boolean : false;
  // Stamp resolved provider IDs onto renamed files (xattr / ADS / Kira's
  // portable index) so a wiped database can re-identify the library instantly.
  // Default ON — some users object to ANY metadata being attached to their
  // files, so it's switchable.
  const stampIds = rawSettings['rename.stamp_ids'] !== false;
  // Relative symlink targets (Symlink op only) — portable across remounts /
  // different bind-mount paths. Default OFF (absolute, unchanged behavior).
  const symlinkRelative = rawSettings['rename.symlink_relative'] === true;
  // Post-rename ownership/mode (Docker/NAS). Master toggle reveals the octal
  // mode + uid/gid fields; all best-effort + Unix-only (chown), no-op elsewhere.
  const setPerms = rawSettings['rename.set_permissions'] === true;
  // Compare /health's version against the latest GitHub release and show a
  // small "vX.Y.Z out" link in the sidebar. The check is a single anonymous
  // GitHub API call from the BROWSER on app load — off = zero outbound calls.
  const updateCheck = rawSettings['advanced.update_check'] !== false;
  // Hidden file input for the settings-import flow — clicked via the Button.
  const importInputRef = useRef<HTMLInputElement>(null);

  // Settings backup: download everything except secrets (API keys leave the
  // server masked, and a backup that embeds them would undo that protection).
  const doExport = async () => {
    try {
      const all = await api.getSettings();
      // Drop secrets: plaintext bullet placeholders AND the server-masked
      // `{ masked: true, tail, set }` objects GET /settings now returns for
      // every secret. Keeps the "API keys are never included" promise literal.
      const clean = Object.fromEntries(
        Object.entries(all).filter(([, v]) =>
          !(typeof v === 'string' && v.startsWith('••••'))
          && !(!!v && typeof v === 'object' && 'masked' in (v as object)),
        ),
      );
      const blob = new Blob([JSON.stringify(clean, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'kira-settings.json';
      a.click();
      URL.revokeObjectURL(url);
      pushToast({ title: 'Settings exported', sub: 'API keys are not included — re-enter them after an import.', kind: 'success' });
    } catch (e) {
      pushToast({ title: 'Export failed', sub: (e as Error).message, kind: 'error' });
    }
  };

  const doImport = async (file: File) => {
    try {
      const parsed = JSON.parse(await file.text()) as Record<string, unknown>;
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error('Not a Kira settings export (expected a JSON object of settings keys).');
      }
      // Never import masked placeholders — they'd overwrite real saved keys.
      const clean = Object.fromEntries(
        Object.entries(parsed).filter(([, v]) => !(typeof v === 'string' && v.startsWith('••••'))),
      );
      await api.putSettings(clean);
      pushToast({ title: `Imported ${Object.keys(clean).length} settings`, sub: 'Reloading…', kind: 'success' });
      setTimeout(() => window.location.reload(), 600);
    } catch (e) {
      pushToast({ title: 'Import failed', sub: (e as Error).message, kind: 'error' });
    }
  };

  return (
    <SettingsLayout
      header={(
        <SectionHeader
          icon={<IcSettings />}
          title="Advanced"
          purpose="Power-user settings — history retention, performance, file metadata, and maintenance. The danger zone lives at the bottom, deliberately set apart."
          status={(
            <div className="flex flex-wrap items-center justify-end gap-1.5">
              <StatusPill tone={readMediainfo ? 'accent' : 'neutral'}>{readMediainfo ? 'MediaInfo on' : 'Defaults'}</StatusPill>
              <StatusPill tone="neutral">{retention === 'forever' ? 'History kept forever' : `History ${retention}d`}</StatusPill>
            </div>
          )}
        />
      )}
    >
      <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-2">
      {/* Library & performance (tall — owns column 1) */}
      <SectionCard
        tint="var(--accent)"
        icon={<IcSettings />}
        title={<>Library &amp; performance</>}
        desc="How long history is kept and how hard Kira hits your disk."
      >
        <div className="flex flex-col gap-4">
          <SettingRow
            settingKeys="history.retention_days"
            label="History retention"
            desc="How long to keep the rename log for undo. Older entries are pruned daily."
          >
            <div className="w-[160px]">
              <Select<string>
                value={retention}
                aria-label="History retention period"
                onChange={v => saveKey('history.retention_days')(v === 'forever' ? '0' : v)}
                options={[
                  { value: '30', label: '30 days' },
                  { value: '90', label: '90 days' },
                  { value: '365', label: '1 year' },
                  { value: 'forever', label: 'Forever' },
                ]}
              />
            </div>
          </SettingRow>
          <SettingRow
            settingKeys="rename.concurrency"
            label="Concurrent file reads"
            desc="How many parallel per-file operations run at once — post-rename subtitle downloads and the background media-info reads. The file moves themselves are deliberately one-at-a-time; that ordering is what makes a half-failed batch safely resumable."
          >
            <NumberField
              min={1}
              max={32}
              value={concurrency}
              onChange={v => saveKey('rename.concurrency')(String(v))}
            />
          </SettingRow>
          <SettingRow
            settingKeys="rename.stamp_ids"
            label="Remember matches on files"
            desc={<>After a rename, stamp the file with its resolved provider IDs (extended attributes where the volume supports them, Kira's local index otherwise) so a re-scan — even after a database reset — re-identifies it instantly with zero searches. Turn off if you don't want Kira attaching any metadata to your files.</>}
          >
            <Toggle isSelected={stampIds} onChange={() => saveKey('rename.stamp_ids')(!stampIds)} className="mt-0.5" aria-label="Remember matches on files" />
          </SettingRow>
          <SettingRow
            settingKeys="rename.on_conflict"
            label="When a file already exists at the target"
            desc={<>How Kira handles a rename whose destination is already occupied by a <em>different</em> file. (A re-run where the same file is already in place is always a safe no-op, regardless of this.)</>}
          >
            <div className="w-full max-w-[14rem]">
              <Select<string>
                value={strSetting(rawSettings, 'rename.on_conflict') || 'error'}
                aria-label="When a file already exists at the target"
                onChange={v => saveKey('rename.on_conflict')(v)}
                options={[
                  { value: 'error', label: 'Show an error (default)' },
                  { value: 'skip', label: 'Skip — keep the existing file' },
                  { value: 'overwrite', label: 'Overwrite the existing file' },
                ]}
              />
            </div>
          </SettingRow>
          <SettingRow
            settingKeys="rename.symlink_relative"
            label="Relative symlink targets"
            desc={<>Only applies when the rename op is <strong className="text-ink">Symlink</strong>. Point each link at a path <em>relative</em> to its own folder instead of an absolute one, so links survive the library being remounted or bind-mounted at a different path (common in Docker). Off = absolute targets.</>}
          >
            <Toggle isSelected={symlinkRelative} onChange={() => saveKey('rename.symlink_relative')(!symlinkRelative)} className="mt-0.5" aria-label="Relative symlink targets" />
          </SettingRow>
          <SettingRow
            settingKeys="rename.set_permissions"
            label="Set file ownership & permissions"
            desc={<>After each rename, apply a fixed mode and/or owner to the file and any folders Kira creates — so a media server running as a different user (common on Docker / NAS) can always read them. Best-effort: chown is Unix-only; on Windows this no-ops.</>}
          >
            <Toggle isSelected={setPerms} onChange={() => saveKey('rename.set_permissions')(!setPerms)} className="mt-0.5" aria-label="Set file ownership and permissions" />
          </SettingRow>
          {setPerms ? (
            <NestedBox>
              <div className="flex flex-col gap-3.5">
                <ProviderField kind="text" label="File mode (octal)" mono
                  value={strSetting(rawSettings, 'rename.file_mode')}
                  placeholder="e.g. 644 — blank to leave unchanged"
                  onSave={v => saveKey('rename.file_mode')(v as string)} />
                <ProviderField kind="text" label="Folder mode (octal)" mono
                  value={strSetting(rawSettings, 'rename.dir_mode')}
                  placeholder="e.g. 755 — blank to leave unchanged"
                  onSave={v => saveKey('rename.dir_mode')(v as string)} />
                <ProviderField kind="text" label="Owner UID" mono
                  value={strSetting(rawSettings, 'rename.owner_uid')}
                  placeholder="numeric uid — blank to leave unchanged"
                  onSave={v => saveKey('rename.owner_uid')(v as string)} />
                <ProviderField kind="text" label="Owner GID" mono
                  value={strSetting(rawSettings, 'rename.owner_gid')}
                  placeholder="numeric gid — blank to leave unchanged"
                  onSave={v => saveKey('rename.owner_gid')(v as string)} />
              </div>
            </NestedBox>
          ) : null}
          <SettingRow
            settingKeys="advanced.update_check"
            label="Check for updates"
            desc={<>On app load, compare this install against the latest GitHub release and show a small note in the sidebar when a newer version exists. One anonymous API call to github.com — off means zero outbound calls.</>}
          >
            <Toggle isSelected={updateCheck} onChange={() => saveKey('advanced.update_check')(!updateCheck)} className="mt-0.5" aria-label="Check for updates" />
          </SettingRow>
        </div>
      </SectionCard>

      {/* Column 2 — the two short cards stacked so the grid row stays balanced. */}
      <div className="flex flex-col gap-4">
      {/* File metadata */}
      <SectionCard
        tint="var(--accent-bright)"
        icon={<IcFilm />}
        title="File metadata (MediaInfo)"
        desc="Read real resolution / codec / HDR straight from the file container."
      >
        <div className="flex flex-col gap-4">
          <SettingRow
            settingKeys="parsing.read_mediainfo"
            label="Read file metadata"
            desc="Fill in resolution / codec / HDR from the file when the filename doesn't carry them. Runs in the background after a scan — never slows it. No-op if the MediaInfo library isn't installed."
          >
            <Toggle isSelected={readMediainfo} onChange={() => saveKey('parsing.read_mediainfo')(!readMediainfo)} className="mt-0.5" aria-label="Read file metadata" />
          </SettingRow>
          <NestedBox dimmed={!readMediainfo}>
            <SettingRow
              settingKeys="parsing.mediainfo_authoritative"
              label="Authoritative tech tags"
              desc={<>Let the file's real metadata <strong className="text-ink">override</strong> what the filename claims for <span className="font-mono text-ink">{'{{vc}}'}</span> <span className="font-mono text-ink">{'{{hdr}}'}</span> <span className="font-mono text-ink">{'{{channels}}'}</span> and quality. Reads every file (not just tag-less ones) so it's heavier — but it runs in the background, so the scan still finishes fast; the corrected tags fill in after. Gives true source-accurate tags.</>}
            >
              <Toggle isSelected={mediainfoAuthoritative} isDisabled={!readMediainfo} onChange={() => saveKey('parsing.mediainfo_authoritative')(!mediainfoAuthoritative)} className="mt-0.5" aria-label="Authoritative tech tags" />
            </SettingRow>
          </NestedBox>
        </div>
      </SectionCard>

      {/* Backup & restore — settings only (the database holds matches/history
          and has its own lifecycle; this covers configuration). */}
      <SectionCard
        tint="var(--conf-high)"
        icon={<IcDownload />}
        title="Backup &amp; restore"
        desc={<>Export every setting as a JSON file, or import one to restore a configuration. <strong className="text-ink">API keys are never included</strong> — they leave the server masked — so re-enter those after a restore.</>}
      >
        <div className="flex flex-wrap items-center gap-2.5">
          <Button color="secondary" size="sm" iconLeading={IcDownload} onClick={() => void doExport()}>
            Export settings
          </Button>
          <input
            ref={importInputRef}
            type="file"
            accept="application/json,.json"
            className="hidden"
            onChange={e => {
              const f = e.target.files?.[0];
              if (f) void doImport(f);
              e.target.value = '';
            }}
          />
          <Button color="secondary" size="sm" iconLeading={IcRefresh} onClick={() => importInputRef.current?.click()}>
            Import settings…
          </Button>
        </div>
      </SectionCard>
      </div>
      </div>

      {/* Danger zone — visually quarantined: its own labelled group + the
          red-tinted SectionCard tone. */}
      <div className="settings-danger-zone flex flex-col gap-2.5 pt-6">
      <div className="flex items-center gap-2.5">
        <IcAlertTri className="size-3.5 shrink-0 text-conf-low" />
        <span className="text-[11px] font-semibold uppercase tracking-[0.1em] text-conf-low">Danger zone</span>
        <span className="h-px flex-1 bg-[var(--conf-low-24)]" />
      </div>
      <SectionCard
        tone="danger"
        icon={<IcAlertTri />}
        title="Reset"
        desc="Four levels, lightest first — each shows exactly what survives and what is lost. Files already on disk are never touched."
      >
        {/* Blast-radius ladder — a gradient spine threads four rungs of
            strictly-growing destruction. Each rung carries its own cumulative
            radius bar; the arm / typed-confirm safety flow is unchanged. */}
        <div className="relative flex flex-col gap-2.5 pl-5">
          <span aria-hidden className="absolute left-[7px] top-3 bottom-3 w-0.5 rounded-full" style={{ background: 'linear-gradient(var(--conf-mid), color-mix(in srgb, var(--conf-mid) 45%, var(--conf-low)), var(--conf-low), var(--danger))' }} />
          <DangerRow
            level={1}
            badge="history"
            name="Clear rename history"
            survives="Files · matches · settings · account"
            lost="The rename log and undo."
            confirmWord={null}
            onRun={async () => {
              const r = await api.resetHistory();
              pushToast({ title: 'History cleared', sub: `${r.history_deleted} entries removed.`, kind: 'success' });
            }}
          />
          <DangerRow
            level={2}
            badge="matches"
            name="Forget all matches"
            survives="Files · history · settings · account"
            lost="Every identification — files flip back to pending for a fresh re-match."
            confirmWord={null}
            onRun={async () => {
              const r = await api.resetMatches();
              pushToast({ title: 'Matches reset', sub: `${r.matches_deleted} matches forgotten — run a scan to re-identify.`, kind: 'success' });
              setTimeout(() => window.location.reload(), 700);
            }}
          />
          <DangerRow
            level={3}
            badge="library data"
            name="Reset database"
            survives="Settings · your account"
            lost="All files, matches, history, and notifications. Renames on disk are NOT undone."
            confirmWord="DELETE"
            onRun={async () => {
              await api.resetDatabase();
              pushToast({ title: 'Database reset', sub: 'All scan data removed.', kind: 'success' });
              setTimeout(() => window.location.reload(), 700);
            }}
          />
          <DangerRow
            level={4}
            badge="everything"
            name="Factory reset"
            survives="Nothing but the files already on disk"
            lost="Everything above PLUS every setting, API key, and the account itself — back to first-run."
            confirmWord="FACTORY"
            onRun={async () => {
              await api.factoryReset();
              try { localStorage.clear(); sessionStorage.clear(); } catch { /* ignore */ }
              window.location.reload();
            }}
          />
        </div>
      </SectionCard>
      </div>
    </SettingsLayout>
  );
}


// ── Danger-zone row — one reset tier ──
// Severity escalates 1→4 (amber → deep red). Light tiers confirm with a
// second click; heavy tiers demand the confirm word typed out. Every row
// states exactly what is destroyed.

// Monotonic light→catastrophic ramp. The old [conf-mid, warn, conf-low, danger]
// did NOT escalate — --warn is grey and --danger === --conf-low, so it read
// amber → grey → red → red. color-mix gives a true amber → orange-red → red →
// deepest-red climb so the blast-radius ladder is honest.
const DANGER_COLORS = ['var(--conf-mid)', 'color-mix(in srgb, var(--conf-mid) 45%, var(--conf-low))', 'var(--conf-low)', 'var(--danger)'];

function DangerRow({ level, badge, name, survives, lost, confirmWord, onRun }: {
  level: 1 | 2 | 3 | 4;
  badge: string;
  name: string;
  /** What this tier spares — the green half of the survives/lost split. */
  survives: string;
  lost: string;
  /** null = two-click arm; string = must be typed to enable the button. */
  confirmWord: string | null;
  onRun: () => Promise<void>;
}) {
  const [armed, setArmed] = useState(false);
  const [typed, setTyped] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const color = DANGER_COLORS[level - 1];

  const run = async () => {
    setBusy(true);
    setErr(null);
    try {
      await onRun();
      setArmed(false);
      setTyped('');
    } catch (e) {
      setErr((e as Error).message);
    }
    setBusy(false);
  };

  return (
    <div
      className="flex flex-col gap-2 rounded-xl border px-3.5 py-3"
      style={{
        borderColor: `color-mix(in srgb, ${color} 30%, transparent)`,
        background: `color-mix(in srgb, ${color} ${level === 4 ? 8 : 5}%, transparent)`,
        ...(level === 4 ? { boxShadow: 'inset 0 0 0 1px color-mix(in srgb, var(--danger) 30%, transparent)' } : {}),
      }}
    >
      <div className="flex items-center gap-3">
        <span className="size-2 shrink-0 rounded-full" style={{ background: color, boxShadow: `0 0 8px ${color}` }} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-[13px] font-semibold text-ink">{name}</span>
            <span
              className="rounded-full px-2 py-px text-[9.5px] font-semibold uppercase tracking-[0.07em]"
              style={{
                color,
                background: `color-mix(in srgb, ${color} 12%, transparent)`,
                border: `1px solid color-mix(in srgb, ${color} 30%, transparent)`,
              }}
            >
              {badge}
            </span>
          </div>
          {/* Cumulative blast bar — lights one more segment per tier. */}
          <div className="mt-1.5 flex h-1.5 gap-px overflow-hidden rounded-full">
            {[0, 1, 2, 3].map(i => (
              <span
                key={i}
                className={`flex-1 ${i <= level - 1 ? '' : 'bg-tertiary opacity-30'}`}
                style={i <= level - 1 ? { background: DANGER_COLORS[i] } : undefined}
              />
            ))}
          </div>
          <div className="mt-1.5 text-[11.5px] leading-relaxed">
            <span className="font-semibold text-conf-low">Lost:</span> <span className="text-ink-muted">{lost}</span>
          </div>
          <div className="text-[11px] leading-relaxed">
            <span className="font-semibold" style={{ color: 'var(--conf-high)' }}>Survives:</span> <span className="text-tertiary">{survives}</span>
          </div>
        </div>
        {!armed ? (
          <Button color="secondary-destructive" size="sm" className="shrink-0" onClick={() => setArmed(true)}>
            {name}…
          </Button>
        ) : null}
      </div>
      {armed ? (
        <div className="flex flex-wrap items-center gap-2 pl-5">
          {confirmWord ? (
            <>
              <span className="shrink-0 text-[11.5px] text-conf-low">
                Type <span className="font-mono font-semibold text-ink">{confirmWord}</span>:
              </span>
              <Input wrapperClassName="w-40" mono value={typed} onChange={e => setTyped(e.target.value)} placeholder={confirmWord} autoFocus />
            </>
          ) : (
            <span className="flex-1 text-[11.5px] text-conf-low">Sure? This can't be undone.</span>
          )}
          <Button
            color="primary-destructive"
            size="sm"
            isDisabled={busy || (confirmWord !== null && typed !== confirmWord)}
            isLoading={busy}
            showTextWhileLoading
            onClick={() => void run()}
          >
            {busy ? 'Working…' : 'Confirm'}
          </Button>
          <Button color="tertiary" size="sm" isDisabled={busy} onClick={() => { setArmed(false); setTyped(''); setErr(null); }}>
            Cancel
          </Button>
          {err ? <span className="w-full text-[11.5px] text-conf-low">{err}</span> : null}
        </div>
      ) : null}
    </div>
  );
}
