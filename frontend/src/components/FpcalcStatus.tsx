import { useEffect, useState } from 'react';
import { api, type ApiFpcalcStatus } from '../lib/api';
import { IcCheck, IcDownload, IcAlertTri } from '../lib/icons';
import { Button } from './base/buttons/button';

/**
 * fpcalc (Chromaprint) health row — Settings → AcoustID.
 *
 * fpcalc computes the audio fingerprint that AcoustID needs to identify UNTAGGED /
 * badly-named music. Like ffmpeg, this row shows the live status and offers the
 * ONE-CLICK managed install: Kira downloads the official Chromaprint build into its
 * own tools dir — no PATH edits, nothing asked of the user. Progress narrates in the
 * activity pill; the row re-checks itself when the install finishes
 * (`kira:fpcalc-changed`).
 */
export function FpcalcStatusRow({ compact = false }: { compact?: boolean }) {
  const [status, setStatus] = useState<ApiFpcalcStatus | null>(null);
  const [requesting, setRequesting] = useState(false);

  const refresh = () => { void api.fpcalcStatus().then(setStatus).catch(() => {}); };
  useEffect(() => {
    let attempts = 0;
    let timer: number | undefined;
    // Retry the initial fetch — a blip must not hide the row forever.
    const tryLoad = () => {
      void api.fpcalcStatus().then(setStatus).catch(() => {
        if (++attempts < 4) timer = window.setTimeout(tryLoad, 1500);
      });
    };
    tryLoad();
    window.addEventListener('kira:fpcalc-changed', refresh);
    return () => { window.removeEventListener('kira:fpcalc-changed', refresh); if (timer) clearTimeout(timer); };
  }, []);

  if (!status) return null;

  const install = async () => {
    if (requesting) return;
    setRequesting(true);
    try {
      const s = await api.installFpcalc();
      setStatus(s);
      window.dispatchEvent(new Event('kira:activity-refresh')); // pill narrates
    } catch {
      /* the pill/bell carry the error */
    } finally {
      setRequesting(false);
    }
  };

  return (
    <div className="flex items-center justify-between gap-3">
      <span className={compact ? 'text-[12.5px] text-ink' : 'text-[13px] text-ink'}>
        fpcalc
        <span className="ml-1.5 text-[11px] text-ink-soft">
          {status.available
            ? (status.source === 'managed' ? 'installed by Kira' : 'found on this system')
            : 'powers AcoustID fingerprint matching'}
        </span>
      </span>
      {status.available ? (
        <span className="inline-flex items-center gap-1.5 text-[12px] font-medium text-[var(--conf-high)] [&_svg]:size-3.5">
          <IcCheck /> Ready
        </span>
      ) : status.installing ? (
        <span className="inline-flex items-center gap-1.5 text-[12px] text-ink-muted">
          Installing… <span className="text-ink-soft">(see activity)</span>
        </span>
      ) : status.installable ? (
        <Button color="secondary" size="sm" iconLeading={IcDownload} isLoading={requesting} onClick={() => void install()}>
          Install for me
        </Button>
      ) : (
        <span className="inline-flex items-center gap-1.5 text-[12px] text-[var(--conf-mid)] [&_svg]:size-3.5">
          <IcAlertTri /> Install chromaprint
        </span>
      )}
    </div>
  );
}
