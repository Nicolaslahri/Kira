import { useEffect, useState } from 'react';
import { api } from '../../lib/api';
import type { SonarrQueueEntry } from './types';

// Popup-only hook. Polls /integrations/sonarr/queue?match_id=N every
// 1.5 seconds while the popup is mounted with a usable matchId. Stops
// polling on the first 400 (Sonarr-not-configured) so we don't hammer
// an endpoint that structurally can't help — the user opens Settings,
// configures, reopens popup, fresh poll begins.
//
// Returns null while we haven't fetched yet OR if Sonarr is
// unreachable. Returns [] for a configured Sonarr with no active
// downloads for this series. Both states render the same way in the
// popup (no progress rows shown), so the caller doesn't need to
// distinguish — the empty state matches the "no Sonarr" state.
export function useSonarrQueuePopup(matchId: number | null): SonarrQueueEntry[] | null {
  const [items, setItems] = useState<SonarrQueueEntry[] | null>(null);
  useEffect(() => {
    if (matchId == null) {
      setItems(null);
      return;
    }
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    // Backoff state — if the endpoint repeatedly errors we slow down
    // (4s → 12s → 30s → stop) so we don't burn a 4s tick forever on a
    // configuration that'll never succeed. Reset on first success.
    let errCount = 0;
    const tick = async () => {
      try {
        const r = await api.sonarrQueue({ match_id: matchId });
        if (cancelled) return;
        setItems(r.items);
        errCount = 0;
        // 1.5s while popup is open. The rAF extrapolation in
        // DownloadProgressRow interpolates smoothly between polls
        // using Sonarr's ETA, so the bar never looks stuck — fast
        // polling just means the extrapolated prediction gets
        // re-anchored against ground truth more often, reducing
        // any visible snap when reality diverges from prediction.
        timer = setTimeout(tick, 1500);
      } catch (e) {
        if (cancelled) return;
        errCount += 1;
        // 400 = Sonarr not configured. Don't keep polling forever —
        // surface the empty state to the caller and stop. The user
        // will reopen the popup after configuring.
        const msg = String(e ?? '');
        if (msg.includes('Sonarr URL') || msg.includes('Sonarr API key') || msg.includes('not configured')) {
          setItems(null);
          return; // intentionally NO further scheduling
        }
        // Transient (Sonarr down, network blip). Back off but keep
        // trying — Sonarr coming back online should auto-recover the
        // live progress without the user needing to reopen the popup.
        const delay = errCount <= 1 ? 4000 : errCount <= 3 ? 12000 : 30000;
        if (errCount > 8) return;   // give up entirely after ~6 minutes
        timer = setTimeout(tick, delay);
      }
    };
    void tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [matchId]);
  return items;
}
