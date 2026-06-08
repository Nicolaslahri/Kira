import { useState } from 'react';
import { IcSettings, IcFilm, IcAlertTri, IcTrash } from '../../lib/icons';
import { Select } from '../../components/ui';
import { SettingsLayout, SectionCard, SettingRow, NumberField, NestedBox } from '../../components/settings-blocks';
import { Button } from '../../components/base/buttons/button';
import { Input } from '../../components/base/input/input';
import { Toggle } from '../../components/base/toggle/toggle';
import { api } from '../../lib/api';
import { type SaveKeyFn, type PushToast } from './helpers';

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
  const [confirming, setConfirming] = useState(false);
  const [confirmText, setConfirmText] = useState('');

  const doReset = async () => {
    try {
      await api.resetDatabase();
      pushToast({ title: 'Database reset', sub: 'All files, matches, and history removed.', kind: 'success' });
      setConfirming(false);
      // Force a reload so the rest of the UI resets too.
      setTimeout(() => window.location.reload(), 600);
    } catch (e) {
      pushToast({ title: 'Reset failed', sub: (e as Error).message, kind: 'error' });
    }
  };

  return (
    <SettingsLayout intro="Power-user settings — retention, performance, file metadata, and maintenance.">
      <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-2">
      {/* Library & performance */}
      <SectionCard
        icon={<IcSettings />}
        title={<>Library &amp; performance</>}
        desc="How long history is kept and how hard Kira hits your disk."
      >
        <div className="flex flex-col gap-4">
          <SettingRow
            label="History retention"
            desc="How long to keep the rename log for undo. Older entries are pruned daily."
          >
            <div className="w-[160px]">
              <Select<string>
                value={retention}
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
            label="Concurrent file operations"
            desc="More is faster but heavier on disk I/O."
          >
            <NumberField
              min={1}
              max={32}
              value={concurrency}
              onChange={v => saveKey('rename.concurrency')(String(v))}
            />
          </SettingRow>
        </div>
      </SectionCard>

      {/* File metadata */}
      <SectionCard
        icon={<IcFilm />}
        title="File metadata (MediaInfo)"
        desc="Read real resolution / codec / HDR straight from the file container."
      >
        <div className="flex flex-col gap-4">
          <SettingRow
            label="Read file metadata"
            desc="Fill in resolution / codec / HDR from the file when the filename doesn't carry them. Runs in the background after a scan — never slows it. No-op if the MediaInfo library isn't installed."
          >
            <Toggle isSelected={readMediainfo} onChange={() => saveKey('parsing.read_mediainfo')(!readMediainfo)} className="mt-0.5" aria-label="Read file metadata" />
          </SettingRow>
          <NestedBox dimmed={!readMediainfo}>
            <SettingRow
              label="Authoritative tech tags"
              desc={<>Let the file's real metadata <strong className="text-ink">override</strong> what the filename claims for <span className="font-mono text-ink">{'{{vc}}'}</span> <span className="font-mono text-ink">{'{{hdr}}'}</span> <span className="font-mono text-ink">{'{{channels}}'}</span> and quality. Reads every file (not just tag-less ones) so it's heavier — but it runs in the background, so the scan still finishes fast; the corrected tags fill in after. Gives true source-accurate tags.</>}
            >
              <Toggle isSelected={mediainfoAuthoritative} isDisabled={!readMediainfo} onChange={() => saveKey('parsing.mediainfo_authoritative')(!mediainfoAuthoritative)} className="mt-0.5" aria-label="Authoritative tech tags" />
            </SettingRow>
          </NestedBox>
        </div>
      </SectionCard>
      </div>

      {/* Danger zone */}
      <SectionCard
        tone="danger"
        icon={<IcAlertTri />}
        title="Danger zone"
        desc="Reset Kira's database. Renames already on disk are NOT undone."
      >
        <div>
          {!confirming ? (
            <Button color="secondary-destructive" size="sm" iconLeading={IcTrash} onClick={() => setConfirming(true)}>
              Reset database…
            </Button>
          ) : (
            <div className="flex flex-col gap-2.5">
              <div className="text-[12.5px] text-conf-low">
                Type <span className="font-mono font-semibold text-ink">DELETE</span> to confirm. This cannot be undone.
              </div>
              <div className="flex items-center gap-2">
                <Input wrapperClassName="flex-1" mono value={confirmText} onChange={e => setConfirmText(e.target.value)} placeholder="DELETE" autoFocus />
                <Button color="primary-destructive" size="sm" isDisabled={confirmText !== 'DELETE'} onClick={() => void doReset()}>
                  Reset now
                </Button>
                <Button color="tertiary" size="sm" onClick={() => { setConfirming(false); setConfirmText(''); }}>
                  Cancel
                </Button>
              </div>
            </div>
          )}
        </div>
      </SectionCard>
    </SettingsLayout>
  );
}
