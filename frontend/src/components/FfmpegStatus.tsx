import { useEffect, useState } from 'react';
import { api, type ApiFfmpegStatus } from '../lib/api';
import { IcCheck, IcDownload, IcAlertTri } from '../lib/icons';
import { Button } from './base/buttons/button';

/**
 * ffmpeg health row — shared by Settings → Subtitles and Onboarding.
 *
 * ffmpeg powers embedded subtitle extraction (the best subtitle source for
 * anime: free, offline, no quota). Docker ships it; on bare installs this row
 * shows the live status and offers the ONE-CLICK managed install: Kira
 * downloads a static build into its own tools dir — no PATH edits, nothing
 * asked of the user. Progress narrates in the activity pill; the row
 * re-checks itself when the install job finishes (`kira:ffmpeg-changed`).
 */
export function FfmpegStatusRow({ compact = false, framed = false }: { compact?: boolean; framed?: boolean }) {
  const [status, setStatus] = useState<ApiFfmpegStatus | null>(null);
  const [requesting, setRequesting] = useState(false);

  const refresh = () => { void api.ffmpegStatus().then(setStatus).catch(() => {}); };
  useEffect(() => {
    let attempts = 0;
    let timer: number | undefined;
    // Retry the initial fetch a few times — a single blip during Settings load
    // used to leave `status` null forever, hiding the whole row (return null
    // below) with no way to recover short of a remount.
    const tryLoad = () => {
      void api.ffmpegStatus().then(setStatus).catch(() => {
        if (++attempts < 4) timer = window.setTimeout(tryLoad, 1500);
      });
    };
    tryLoad();
    window.addEventListener('kira:ffmpeg-changed', refresh);
    return () => { window.removeEventListener('kira:ffmpeg-changed', refresh); if (timer) clearTimeout(timer); };
  }, []);

  // While an install runs, poll for the live progress label ("downloading
  // 12 / 90 MB"). The row is the ONLY visible surface during onboarding —
  // the activity pill it used to point at sits behind the wizard overlay.
  const installing = !!status?.installing;
  useEffect(() => {
    if (!installing) return;
    const t = window.setInterval(refresh, 1500);
    return () => clearInterval(t);
  }, [installing]);

  if (!status) return null;   // framed too — no empty card shell while loading

  // "Installing ffmpeg · downloading 12 / 90 MB" → the row already says
  // "ffmpeg" on the left, so show just the part after the "·".
  const progressText = status.progress?.split('·').pop()?.trim() || 'Installing…';

  const install = async () => {
    if (requesting) return;
    setRequesting(true);
    try {
      const s = await api.installFfmpeg();
      setStatus(s);
      window.dispatchEvent(new Event('kira:activity-refresh')); // pill narrates
    } catch {
      /* the pill/bell carry the error */
    } finally {
      setRequesting(false);
    }
  };

  const body = (
    <div className="flex items-center justify-between gap-3">
      <span className={compact ? 'text-[12.5px] text-ink' : 'text-[13px] text-ink'}>
        ffmpeg
        <span className="ml-1.5 text-[11px] text-ink-soft">
          {status.available
            ? (status.source === 'managed' ? 'installed by Kira' : 'found on this system')
            : 'powers embedded subtitle extraction'}
        </span>
      </span>
      {status.available ? (
        <span className="inline-flex items-center gap-1.5 text-[12px] font-medium text-[var(--conf-high)] [&_svg]:size-3.5">
          <IcCheck /> Ready
        </span>
      ) : status.installing ? (
        <span className="inline-flex items-center gap-1.5 text-[12px] text-ink-muted">
          <span className="size-1.5 shrink-0 animate-pulse rounded-full bg-[var(--accent)]" aria-hidden />
          {progressText}
        </span>
      ) : status.installable ? (
        <Button color="secondary" size="sm" iconLeading={IcDownload} isLoading={requesting} onClick={() => void install()}>
          Install for me
        </Button>
      ) : (
        <span className="inline-flex items-center gap-1.5 text-[12px] text-[var(--conf-mid)] [&_svg]:size-3.5">
          <IcAlertTri /> Install from ffmpeg.org
        </span>
      )}
    </div>
  );
  return framed ? <div className="onb-folder-card">{body}</div> : body;
}
