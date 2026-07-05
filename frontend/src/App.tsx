import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { AnimatePresence, motion, MotionConfig } from 'motion/react';
import type { AppState, ModalState, Page, ToastData, MediaFile, SearchResult, TechProgress } from './lib/types';
import { api, ApiError, getBackendOnline, onBackendConnectivity, type ApiActivityJob } from './lib/api';
import { apiToMediaFile } from './lib/adapters';
import { cacheGet, cacheSet } from './lib/cache';
import { setConfBands, getConfBands } from './lib/confBands';
import { ScanProgress } from './components/ScanProgress';
import { Sidebar, Topbar } from './components/ui';
import { notify, NotificationToaster, type NotifyAction } from './components/base/notifications/notification';
import { useActivity, ActivityPill } from './components/ActivityIndicator';
import { SubtitleBrowseModal } from './components/SubtitleBrowseModal';
import { ManualSearchModal, RenamePreviewModal, KeyboardShortcutsModal } from './components/modals';
import { Onboarding, isOnboarded, setOnboarded } from './components/Onboarding';
import { LoginGate } from './components/LoginGate';
import { hasStoredAuth } from './lib/api';
import { DashboardPage } from './pages/DashboardPage';
import { ReviewPage } from './pages/ReviewPage';
import { HistoryPage } from './pages/HistoryPage';
import { SettingsPage } from './pages/SettingsPage';

// Settings sub-sections — now first-class routes (#/settings/<section>) so
// the sidebar's nested Settings nav drives them and refresh/back/forward work.
const SETTINGS_SECTIONS = ['connections', 'paths', 'integrations', 'packs', 'matching', 'naming', 'subtitles', 'cleanup', 'advanced'] as const;
export type SettingsSection = (typeof SETTINGS_SECTIONS)[number];

// A boolean setting may arrive as a bare `true`/`false` or wrapped as `{value}`
// depending on which write path produced it — read either shape truthily.
function settingIsOn(v: unknown): boolean {
  if (typeof v === 'boolean') return v;
  if (v && typeof v === 'object' && 'value' in v) return !!(v as { value?: unknown }).value;
  return !!v;
}

// Narrate the detached tech-tag (MediaInfo) pass as the scan popup's 3rd line.
// The scan tracker calls this right after matching completes and KEEPS the
// popup up (leaves `scanRunning` true) until it returns — so the read shows as
// part of the scan instead of a separate, easy-to-miss pill. The pass is
// spawned a beat after completion, so we wait briefly for its job to appear,
// then poll it to completion. Feature off → returns at once (no popup delay).
// Fully self-contained + swallows transient errors so it never breaks a scan.
async function narrateTechTail(
  setScanTech: (t: TechProgress | null) => void,
  featureOn: boolean,
  onDone?: (job: ApiActivityJob) => void,
  isCurrent?: () => boolean,
): Promise<void> {
  if (!featureOn) { setScanTech(null); return; }
  setScanTech({ active: false, done: 0, total: null, state: 'running', queued: true });
  const appearBy = Date.now() + 5000;   // grace for the spawn-at-tail latency
  // Hold the banner for at most ~5 minutes. The tail matches the enrich job by
  // NAME only, so an unrelated whole-library MediaInfo backfill could hold the
  // scan popup hostage for its entire (potentially hour-long) run; past the
  // cap we release the banner and let the ActivityPill narrate the rest.
  const holdUntil = Date.now() + 5 * 60_000;
  let seen = false;
  for (let i = 0; i < 5000; i++) {       // safety cap; a real pass ends well before
    // A newer scan/reparse loop took the banner — stop writing over its state.
    if (isCurrent && !isCurrent()) return;
    if (Date.now() > holdUntil) { setScanTech(null); return; }
    let job: ApiActivityJob | undefined;
    try { job = (await api.getActivity()).jobs.find(j => j.name === 'mediainfo_enrich'); }
    catch { /* transient backend blip — keep polling */ }
    if (job?.active) {
      seen = true;
      setScanTech({ active: true, done: job.done, total: job.total, state: 'running' });
    } else if (seen) {
      // Ran and finished — show a brief "done" beat (the caller clears it), and
      // hand the finished job to onDone so the ActivityPill doesn't ALSO surface
      // it as a separate "Done" pill once the scan popup closes (it lingers in
      // the /activity snapshot for ~15s). The Settings-toggle path never calls
      // narrateTechTail, so its own pill is left intact.
      if (job) { setScanTech({ active: false, done: job.done, total: job.total, state: 'done' }); onDone?.(job); }
      else setScanTech(null);
      return;
    } else if (Date.now() > appearBy) {
      setScanTech(null);                 // never started (nothing to read / lib missing)
      return;
    }
    await new Promise(r => setTimeout(r, 700));
  }
  setScanTech(null);
}

// Parse `#/<page>` or `#/settings/<section>` out of the URL hash. Falls back to
// review (the main work surface) / connections if missing or unknown.
function parseHash(): { page: Page; section: SettingsSection } {
  const h = window.location.hash.replace(/^#\/?/, '').trim().toLowerCase();
  const [p, sub] = h.split('/');
  const page: Page = (p === 'dashboard' || p === 'review' || p === 'history' || p === 'settings') ? p : 'review';
  const section = (SETTINGS_SECTIONS as readonly string[]).includes(sub) ? (sub as SettingsSection) : 'connections';
  return { page, section };
}

// Live backend connectivity, driven by the api request layer (any HTTP
// response = reachable; only a network failure = offline). Self-healing: the
// continuous /activity poll keeps it fresh, so a transient blip or a slow cold
// start can't leave the UI stuck on "Backend disconnected".
function useBackendOnline(): boolean | null {
  const [online, setOnline] = useState<boolean | null>(() => getBackendOnline());
  useEffect(() => {
    const unsub = onBackendConnectivity(setOnline);
    // Force an immediate, lightweight re-probe when the user returns to the tab
    // or the browser regains network — recovery shouldn't wait out the poll
    // interval. /health goes through request(), so it updates connectivity.
    const probe = () => { if (!document.hidden) void api.health().catch(() => {}); };
    const onVisible = () => { if (!document.hidden) probe(); };
    window.addEventListener('online', probe);
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      unsub();
      window.removeEventListener('online', probe);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, []);
  return online;
}

export default function App() {
  const [active, setActiveState] = useState<Page>(() => parseHash().page);
  const [settingsSection, setSettingsSectionState] = useState<SettingsSection>(() => parseHash().section);
  const [onboarded, setOnboardedState] = useState<boolean>(() => isOnboarded());
  // Auth state machine: 'setup' on first run (no account exists yet — the
  // sign-up screen creates it), 'login' when an account/env credentials
  // exist and this tab holds none, 'open' when usable. Starts 'unknown'
  // UNLESS this tab already holds credentials — and while unknown the app
  // tree does NOT render, so an unauthenticated refresh can never flash the
  // dashboard before the gate appears. Raised reactively by the api layer's
  // 401 handler ('kira:auth-required') too.
  const [authState, setAuthState] = useState<'unknown' | 'login' | 'setup' | 'open'>(
    () => (hasStoredAuth() ? 'open' : 'unknown'),
  );
  // Whether /auth/status has come back yet. Onboarding (a SERVER fact) must
  // wait for it: seeding `onboarded` from the localStorage hint means a tab
  // with a STALE `false` (e.g. cleared storage on an already-onboarded server)
  // would flash the wizard for a beat until the server corrected it.
  const [authResolved, setAuthResolved] = useState(false);
  useEffect(() => {
    let cancelled = false;
    void api.getAuthStatus()
      .then(s => {
        if (cancelled) return;
        if (s.setup) setAuthState('setup');
        else if (s.required && !hasStoredAuth()) setAuthState('login');
        else setAuthState('open');
        // Durable hint for the fail-closed catch above.
        try { localStorage.setItem('kira:auth-required', s.required ? '1' : '0'); } catch { /* */ }
        // Onboarding is a SERVER fact (set on completion; legacy instances
        // with a populated library count as onboarded). The localStorage
        // flag remains only as a fast first-paint hint.
        setOnboardedState(s.onboarded);
        setOnboarded(s.onboarded);
        setAuthResolved(true);
      })
      // Failure policy (§18 M): fall OPEN only when this browser has never
      // authenticated — there the connectivity UI covers "backend down" and
      // nothing sensitive hydrates. But when stored credentials exist, this
      // server has auth ON — a transient 500/timeout must NOT render the app
      // shell + cached library as if logged in; keep the stored-auth gate
      // ('open' with credentials attached) and let the 401 handler flip to
      // the login screen if the creds turn out stale. When auth was required
      // last session (localStorage flag) and no creds are stored, fail to
      // the LOGIN screen, not the open app.
      .catch(() => {
        if (cancelled) return;
        let requiredHint = false;
        try { requiredHint = localStorage.getItem('kira:auth-required') === '1'; } catch { /* */ }
        setAuthState(hasStoredAuth() ? 'open' : requiredHint ? 'login' : 'open');
        setAuthResolved(true);
      });
    const onAuthRequired = () => setAuthState('login');
    window.addEventListener('kira:auth-required', onAuthRequired);
    return () => { cancelled = true; window.removeEventListener('kira:auth-required', onAuthRequired); };
  }, []);
  // Post-onboarding "grand entrance": one-shot flag that amplifies the
  // dashboard's entry animations (deeper rise, blur-in hero, longer cascade)
  // for the very first landing, then expires so normal nav stays snappy.
  const [grandEntry, setGrandEntry] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  // Mobile nav drawer (hamburger). Ignored on lg+ where the sidebar is static.
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  // Settings now buffers edits until Save; this mirrors SettingsPage's dirty
  // state so leaving the page (sidebar/topbar nav) can confirm before the draft
  // is lost. Section switches stay on the page, so they're never guarded.
  const settingsDirtyRef = useRef(false);

  // Keep the hash in sync. Navigating to Settings preserves the last-open
  // section; everything else is a bare `#/<page>`.
  const setActive = useCallback((p: Page) => {
    if (active === 'settings' && p !== 'settings' && settingsDirtyRef.current) {
      if (!window.confirm('You have unsaved settings changes. Leave and discard them?')) return;
      settingsDirtyRef.current = false;
    }
    setActiveState(p);
    const hash = p === 'settings' ? `#/settings/${settingsSection}` : `#/${p}`;
    if (window.location.hash !== hash) window.location.hash = hash;
  }, [settingsSection, active]);

  // Select a Settings sub-section (from the nested sidebar nav). Also flips
  // the active page to settings.
  const setSettingsSection = useCallback((s: SettingsSection) => {
    setSettingsSectionState(s);
    setActiveState('settings');
    const hash = `#/settings/${s}`;
    if (window.location.hash !== hash) window.location.hash = hash;
  }, []);

  // The topbar search drives the Review queue's filter. If the user starts
  // typing from any other page, jump them to Review so the results are
  // actually visible — otherwise the box looks dead.
  const handleSearchChange = useCallback((q: string) => {
    setSearchQuery(q);
    if (q.trim() && active !== 'review') setActive('review');
  }, [active, setActive]);

  // Live refs so the mount-once hashchange listener sees CURRENT values.
  const activeForHashRef = useRef(active);
  const settingsSectionForHashRef = useRef(settingsSection);
  useEffect(() => { activeForHashRef.current = active; }, [active]);
  useEffect(() => { settingsSectionForHashRef.current = settingsSection; }, [settingsSection]);

  useEffect(() => {
    const onHashChange = () => {
      const { page, section } = parseHash();
      // Browser back/forward must respect the SAME unsaved-settings guard as
      // sidebar nav (§5 M) — it used to bypass it and destroy the draft. If
      // the user declines, restore the settings hash (this re-fires
      // hashchange, which no-ops: page === 'settings').
      if (
        activeForHashRef.current === 'settings' && page !== 'settings'
        && settingsDirtyRef.current
      ) {
        if (!window.confirm('You have unsaved settings changes. Leave and discard them?')) {
          window.location.hash = `#/settings/${settingsSectionForHashRef.current}`;
          return;
        }
        settingsDirtyRef.current = false;
      }
      setActiveState(page);
      setSettingsSectionState(section);
    };
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);

  // Stale-while-revalidate: hydrate from localStorage cache synchronously
  // so on second+ refresh the user sees the previous library INSTANTLY,
  // then the background fetch updates it silently. First-ever load (no
  // cache) starts empty + hydrated=false, so pages show skeletons.
  const cachedFiles = cacheGet<MediaFile[]>('files');
  const [state, setState] = useState<AppState>({
    files: cachedFiles ?? [],
    scanRunning: false,
    scanProgress: 0,
    scanFound: 0,
    scanMessage: 'Looking for media files…',
    scanPhase: 'idle',
    scanTech: null,
    // If we restored from cache, treat the page as hydrated for layout
    // purposes — the previous data is good enough to render. The
    // background fetch below will replace it with fresher data when it
    // lands.
    hydrated: cachedFiles !== null,
  });
  // Backend connectivity, derived from the live HTTP layer (see useBackendOnline)
  // so the "Disconnected" indicator self-heals instead of latching on a single
  // failed probe and pretending an empty library is real.
  const backendOk = useBackendOnline();

  // Pull real files from the backend on mount. Empty list is empty — never
  // fall back to mock data, since that confuses users about what's real.
  // `hydrated` flips in `.finally()` so success AND failure both unlock the
  // empty-state UIs — either way the loading window is over.
  //
  // Cache write: every successful response gets persisted so the next
  // refresh can hydrate the previous data instantly (stale-while-revalidate).
  useEffect(() => {
    // Gate on auth resolution (§18 m): firing before /auth/status returns
    // guaranteed a 401 burst on every unauthenticated visit. One cheap wait,
    // then the fetch runs exactly once post-resolution.
    if (!authResolved) return;
    api.listAllFiles()
      .then(rows => {
        const mapped = rows.map(apiToMediaFile);
        bumpFilesGen(); setState(s => ({ ...s, files: mapped }));
        cacheSet('files', mapped);
      })
      .catch(err => {
        // Connectivity is tracked centrally in the request layer now, so a
        // failure here doesn't latch "disconnected" — a /files-specific error
        // (e.g. a 500) keeps the app online while still logging the problem.
        console.warn('Kira initial /files load failed:', err);
      })
      .finally(() => {
        setState(s => ({ ...s, hydrated: true }));
      });
  }, [authResolved]);

  // Keep the stale-while-revalidate cache in lockstep with live state. Without
  // this, only the initial fetch wrote the cache, so after a mutation (manual
  // re-match, approve, rename) a page refresh hydrated a PRE-mutation snapshot
  // — the user saw the old poster/match flash until the background /files
  // fetch landed seconds later. Debounced so rapid scan-time updates coalesce.
  useEffect(() => {
    if (!state.hydrated) return;  // don't clobber the cache with the empty pre-fetch []
    // Large libraries: stringifying the WHOLE list burns hundreds of ms of
    // main thread per debounce fire (every 800ms during scans) only for
    // localStorage's ~5MB quota to reject it anyway. Cache a first-paint
    // slice instead — the live fetch replaces it seconds later regardless.
    const h = setTimeout(() => {
      const payload = state.files.length > 1500 ? state.files.slice(0, 500) : state.files;
      cacheSet('files', payload);
    }, 800);
    return () => clearTimeout(h);
  }, [state.files, state.hydrated]);

  const pendingCount = useMemo(() =>
    state.files.filter(f => f.status === 'pending').length,
  [state.files]);

  const [modal, setModal] = useState<ModalState>(null);
  const openModal = (kind: string, payload?: unknown) => setModal({ kind, payload } as ModalState);
  const closeModal = () => setModal(null);

  // Monotonic generation guard for writes to `state.files`. The two BACKGROUND
  // poll loops (trackScan + reparse) periodically replace the whole file list;
  // a user mutation (manual match, status change) that lands DURING a poll's
  // in-flight fetch would otherwise be clobbered when that stale fetch resolves
  // — the "manual match reverts a few seconds later" bug. Every user-initiated
  // write bumps this; each poll snapshots it before fetching and drops its
  // replace if a user write bumped in the meantime.
  const filesGenRef = useRef(0);
  const bumpFilesGen = useCallback(() => { filesGenRef.current += 1; return filesGenRef.current; }, []);

  // Toasts are now Untitled UI notifications on Sonner. pushToast keeps its
  // old signature so every call site is unchanged; the content-length duration
  // scaling (errors linger 50% longer) lives in notify().
  const pushToast = useCallback((t: Omit<ToastData, 'id'> & { action?: NotifyAction }) => { notify(t); }, []);

  // Background-activity poll (boot auto-heal, subtitle fetches, ffmpeg
  // install…) + the one-time "recovered after restart" toast. Always mounted
  // so polling survives page changes. Returns the running job OR the most
  // recent finished one (green summary / sticky red error) for the pill.
  const { job: activeJob, dismissJob } = useActivity(pushToast);
  // Toast stack must clear the activity panel (ScanProgress / ActivityPill) that
  // shares the bottom-right corner. The old fixed 112px offset was SHORTER than
  // ScanProgress → overlap. Measure the panel's REAL height instead and lift the
  // toasts to exactly clear it; the lift is CSS-transitioned (index.css,
  // [data-sonner-toaster]) so toasts glide up when the panel appears and settle
  // back when it goes — never overlapping, never snapping.
  const activityVisible = state.scanRunning || !!activeJob;
  const activityRef = useRef<HTMLDivElement | null>(null);
  const [activityH, setActivityH] = useState(0);
  // Watchdog baseline: bumped to Date.now() on real scan progress; the scan
  // health derivation below reads it to detect a stalled scan. Declared here
  // (before scanStatus) so it's initialized when scanStatus reads it.
  // Initialized to 0 so the very first Scan click always passes the staleness
  // check (no in-flight scan to defer to).
  const lastProgressAtRef = useRef<number>(0);
  // Scan-health heartbeat: while a scan runs, tick every 3s so the "stalled"
  // derivation re-evaluates even when the poll has gone quiet (a wedged scan
  // stops producing state updates, so nothing else would re-render).
  const [scanHealthTick, setScanHealthTick] = useState(0);
  useEffect(() => {
    if (!state.scanRunning) return;
    const t = setInterval(() => setScanHealthTick(n => n + 1), 3000);
    return () => clearInterval(t);
  }, [state.scanRunning]);
  // Frozen-state for the scan popup: offline when the backend is unreachable,
  // stalled when no real progress landed in 2 min (matches the poll watchdog).
  const scanStatus: 'live' | 'stalled' | 'offline' = !state.scanRunning ? 'live'
    : backendOk === false ? 'offline'
    : (Date.now() - lastProgressAtRef.current > 120_000) ? 'stalled'
    : 'live';
  void scanHealthTick;  // re-render dependency only
  useEffect(() => {
    const el = activityRef.current;
    if (!el) return;
    const measure = () => setActivityH(el.offsetHeight);
    measure();
    const ro = new ResizeObserver(measure);  // catches ScanProgress → ActivityPill height changes too
    ro.observe(el);
    return () => ro.disconnect();
  }, [activityVisible]);
  const toastOffset = activityVisible ? activityH + 36 : 24;   // panel height + bottom-6 + a gap

  const [focusedId, setFocusedId] = useState(state.files[0]?.id ?? '');

  // Rename defaults + library root pulled from settings so the rest of
  // the app stays consistent with what the user configured. Hardcoded
  // 'Z:\\media' was Windows-/this-user-specific and broke for anyone else.
  const [savedOp, setSavedOp] = useState<string>('move');
  const [savedProfile, setSavedProfile] = useState<string>('Plex');
  const [scanRoot, setScanRoot] = useState<string>('/media');
  // Whether "Read file metadata" (tech tags) is on — drives the scan popup's
  // 3rd line. Settings values come through as a bare value or `{value}`.
  const [techTagsOn, setTechTagsOn] = useState(false);
  // Mirror to a ref so the scan tracker (a useCallback) can read the live value
  // when it narrates the tech-tag tail, without churning its dependency list.
  const techTagsOnRef = useRef(techTagsOn);
  useEffect(() => { techTagsOnRef.current = techTagsOn; }, [techTagsOn]);
  useEffect(() => {
    const loadDefaults = async () => {
      try {
        const s = await api.getSettings();
        setTechTagsOn(settingIsOn(s['parsing.read_mediainfo']));
        if (typeof s['rename.default_op'] === 'string') setSavedOp(s['rename.default_op'] as string);
        if (typeof s['naming.profile'] === 'string') setSavedProfile(s['naming.profile'] as string);
        // Confidence badge cutoffs — feed the shared module so every badge
        // (Review, Library, popup) reflects the user's Confidence thresholds.
        setConfBands(
          typeof s['matching.high_threshold'] === 'number' ? s['matching.high_threshold'] as number : 85,
          typeof s['matching.mid_threshold'] === 'number' ? s['matching.mid_threshold'] as number : 50,
        );
        // library_root may be saved as a bare string OR as {value: "..."}
        // depending on which write path produced it. Handle both shapes.
        const lr = s['paths.library_root'];
        if (typeof lr === 'string' && lr) setScanRoot(lr);
        else if (lr && typeof lr === 'object' && 'value' in lr && typeof (lr as { value?: unknown }).value === 'string') {
          setScanRoot((lr as { value: string }).value);
        }
      } catch { /* defaults stay */ }
    };
    void loadDefaults();
    // Reload when Settings page saves
    const onChange = () => { void loadDefaults(); };
    window.addEventListener('kira:settings-saved', onChange);
    return () => window.removeEventListener('kira:settings-saved', onChange);
  }, []);

  // The path Kira will scan. Pulled from settings (`paths.library_root`)
  // with a sensible fallback. Falls through to '/media' if no setting is
  // saved — that's the canonical Docker mount point.
  const SCAN_ROOT = scanRoot;

  const refreshFiles = useCallback(async () => {
    // Claim a new generation BEFORE the fetch; commit the full-list replace only
    // if no newer reload/mutation bumped the gen while we were in flight. Without
    // this, two overlapping full-list refetches (e.g. a debounced rescan racing a
    // rename's refetch) let the older, slower-resolving one clobber the newer
    // state — the "my change reverted a second later" bug. Targeted single-file
    // patches transform current state synchronously and don't need this.
    const myGen = bumpFilesGen();
    try {
      const rows = await api.listAllFiles();
      const mapped = rows.map(apiToMediaFile);
      if (myGen === filesGenRef.current) {
        setState(s => ({ ...s, files: mapped }));
        cacheSet('files', mapped);
      }
      return mapped;  // let callers compare counts (import-landed detection)
    } catch (err) {
      // Connectivity is tracked centrally in the request layer; just log.
      console.warn('Failed to refresh files:', err);
      return null;
    }
  }, [bumpFilesGen]);

  // Poll a scan to completion, animating the progress banner + live file list.
  // Extracted so BOTH a freshly-started scan (runScan) and a re-attached
  // in-flight scan (the mount effect below, after a page refresh) drive the
  // exact same progress UI — no duplicated/drifting poll logic.
  const trackScan = useCallback(async (scanId: number) => {
    // Claim the banner for this loop; any earlier loop sees the token change and
    // bails on its next tick, so two loops can never write progress at once.
    const myToken = ++activePollRef.current;
    try {
      // PB-4: baseline for ETA math. Watchdog baseline too (bumped each cycle).
      scanStartedAtRef.current = Date.now();
      lastProgressAtRef.current = Date.now();
      let done = false;
      // Only refetch the (heavy) full file list when matched progress actually
      // advances. The /scans poll is cheap; /files?limit=500 re-serializes the
      // whole library, so firing it every 800ms — even while a slow cluster
      // makes no progress — floods the backend's event loop and competes with
      // the scan worker. Gating on matched_count kills that flood.
      let lastMatched = -1;
      let lastFilesRefetchAt = 0;
      let lastCount = -1;
      let lastPath: string | null = null;
      let stopped = false;
      let pollFailures = 0;
      while (!done) {
        await new Promise(r => setTimeout(r, 800));
        // Superseded by a newer scan/reparse loop — stop writing banner state.
        if (activePollRef.current !== myToken) return;
        let s: Awaited<ReturnType<typeof api.getScan>>;
        try {
          s = await api.getScan(scanId);
          pollFailures = 0;
        } catch (err) {
          // 404 = the scan row itself is gone (DB reset / container rebuilt
          // mid-scan) — no amount of polling brings it back. And transient
          // errors are tolerated but NOT forever: this loop used to swallow
          // every failure and leave the banner animating "running" against a
          // permanently dead backend.
          if (err instanceof ApiError && err.status === 404) {
            throw new Error('Scan record no longer exists — it may have been cleared by a backend reset.');
          }
          if (++pollFailures >= 75) { // ~60s of continuous failures
            throw new Error('Lost contact with the backend while tracking the scan.');
          }
          continue;
        }
        if (s.status.startsWith('failed')) {
          throw new Error(s.status);
        }
        // Refresh the file list only when something new resolved (or finished).
        const progressed = s.matched_count !== lastMatched || s.file_count !== lastCount;
        // The stall watchdog counts current_path movement as life too — the
        // backend deliberately updates it before slow rate-limited clusters,
        // so a legit no-new-matches stretch doesn't read as "wedged".
        const pathMoved = s.current_path !== lastPath;
        lastPath = s.current_path;
        const finishing = s.status === 'completed' || s.status === 'completed_partial';
        // Throttle (§10 m): with the parallel match lane, "progressed" is true
        // on almost every poll tick — refetching the ENTIRE library each time
        // hammered the backend and the adapter. At most one refetch per 5s
        // mid-scan; the terminal refetch always runs.
        const refetchDue = finishing || (Date.now() - lastFilesRefetchAt) > 5000;
        if ((progressed && refetchDue) || finishing) {
          lastFilesRefetchAt = Date.now();
          lastMatched = s.matched_count;
          lastCount = s.file_count;
          try {
            const gen = filesGenRef.current;
            const rows = await api.listAllFiles();
            // Drop this background replace if a user mutation bumped the gen
            // while we were fetching — don't clobber a fresh manual match.
            setState(st => (gen === filesGenRef.current ? { ...st, files: rows.map(apiToMediaFile) } : st));
          } catch { /* swallow */ }
        }

        let pct = 0;
        let msg = 'Looking for media files…';
        let phase: AppState['scanPhase'] = 'scanning';
        const total = s.estimated_total;
        if (s.status === 'scanning') {
          phase = 'scanning';
          pct = 0;
          msg = `Scanning… ${s.file_count} files found`;
        } else if (s.status === 'matching') {
          phase = 'matching';
          const denom = total ?? s.file_count;
          const matchPct = denom > 0 ? (s.matched_count / denom) : 0;
          pct = Math.min(100, Math.round(matchPct * 100));
          let etaSuffix = '';
          if (total && s.matched_count > 0 && scanStartedAtRef.current) {
            const elapsedMs = Date.now() - scanStartedAtRef.current;
            const ratePerMs = s.matched_count / Math.max(1, elapsedMs);
            const remaining = Math.max(0, total - s.matched_count);
            const etaMs = remaining / Math.max(0.001, ratePerMs);
            const etaMin = Math.round(etaMs / 60000);
            if (etaMin >= 1) etaSuffix = ` · ~${etaMin} min left`;
            else if (etaMs > 5000) etaSuffix = ` · <1 min left`;
          }
          // Current-file ticker: the backend maintains current_path exactly
          // for this — show WHICH title is resolving so slow rate-limited
          // clusters read as progress, not a hang.
          const curName = s.current_path ? (s.current_path.split(/[\/]/).pop() ?? '') : '';
          msg = `Matching ${s.matched_count} / ${denom}${etaSuffix}${curName ? ` · ${curName}` : ''}`;
        } else if (s.status === 'completed' || s.status === 'completed_partial') {
          phase = 'done';
          pct = 100;
          // Actionable terminal numbers (§3 M): say what needs the user next,
          // not just raw counts.
          const needsReview = Math.max(0, s.file_count - s.matched_count);
          const reviewHint = needsReview > 0 ? ` · ${needsReview} need review` : '';
          const partial = s.status === 'completed_partial'
            ? ' · some folders unreachable — see the bell' : '';
          msg = `${s.file_count} files · ${s.matched_count} matched${reviewHint}${partial}`;
          done = true;
        } else if (s.status === 'cancelled') {
          // Stop button (or a forced stale-lock release). The worker keeps what
          // it found — show that, then let the banner clear. MUST end the loop:
          // 'cancelled' matches no in-progress branch, so without this the loop
          // would spin forever re-asserting 'scanning' and flicker against the
          // next scan's loop (and `scanRunning` would never clear → stuck Stop).
          phase = 'done';
          pct = Math.min(100, pct);
          msg = `Stopped · ${s.file_count} files, ${s.matched_count} matched`;
          done = true;
          stopped = true;
        } else if (s.status !== 'scanning' && s.status !== 'matching' && s.status !== 'pending') {
          // Any other unrecognized terminal status — never keep polling blindly,
          // and never leave the banner frozen on "Looking for media files…".
          phase = 'done';
          msg = `Scan ended (${s.status}) · ${s.file_count} files, ${s.matched_count} matched`;
          done = true;
          stopped = true;
        }
        setState(st => ({ ...st, scanProgress: pct, scanFound: s.file_count, scanMessage: msg, scanPhase: phase }));
        // Watchdog baseline: bump ONLY on real movement (counts / current file /
        // terminal). It used to bump on every successful poll, which meant the
        // 90s stale check could never detect a wedged backend scan — a row
        // stuck at 'matching' with a dead worker froze the banner forever.
        if (progressed || pathMoved || done) lastProgressAtRef.current = Date.now();
      }

      // A superseded loop (a newer scan took over mid-iteration) must not
      // narrate the OLD scan's completion — that produced double "Scan
      // complete" toasts and a stale full-list refresh over the live scan.
      if (activePollRef.current !== myToken) return;
      await refreshFiles();
      // Final counts are cosmetic — a transient blip here must not convert a
      // completed scan into a "Scan failed" toast.
      let finalCounts = '';
      try {
        const final = await api.getScan(scanId);
        finalCounts = `${final.file_count} files · ${final.matched_count} matched`;
      } catch { finalCounts = 'finished'; }
      pushToast({
        title: stopped ? 'Scan stopped' : 'Scan complete',
        sub: finalCounts,
        kind: 'success',
      });
    } catch (err) {
      if (activePollRef.current !== myToken) return;
      pushToast({
        title: 'Scan failed',
        sub: (err as Error).message.includes('Failed to fetch')
          ? 'Backend not reachable — is uvicorn running on :8000?'
          : (err as Error).message,
        kind: 'error',
      });
    } finally {
      // Only tear down the banner if we're still the active loop. A superseded
      // loop (a newer scan took over) must NOT narrate or clear — its delayed
      // setState would hide the live scan's progress 1.6s from now.
      if (activePollRef.current === myToken) {
        // Narrate the detached tech-tag pass as the popup's 3rd line, holding the
        // popup open until it finishes (feature off → returns at once). Wrapped so
        // it can never block the banner from clearing.
        try { await narrateTechTail(t => setState(s => ({ ...s, scanTech: t })), techTagsOnRef.current, dismissJob, () => activePollRef.current === myToken); } catch { /* ignore */ }
        // Brief delay so the user sees the final state before the banner disappears.
        setTimeout(() => { if (activePollRef.current === myToken) setState(s => ({ ...s, scanRunning: false, scanTech: null })); }, 1600);
      }
    }
  }, [pushToast, refreshFiles]);

  // Re-attach to an in-flight scan after a page refresh. The scan runs as a
  // server-side background task, so a reload doesn't stop it — but the progress
  // banner is React state that resets to hidden. On mount, find a scan that's
  // still scanning/matching and resume the banner + polling so the popup
  // survives a refresh instead of vanishing.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      // The re-attach used to be one-shot: a brief backend unavailability at
      // page load (NAS cold start — this deployment's normal case) meant an
      // in-flight scan was never adopted and the banner just vanished. Retry
      // a few times before giving up; the visibility re-sync below is the
      // longer-term safety net.
      for (let attempt = 0; attempt < 3 && !cancelled; attempt++) {
        try {
          const scans = await api.listScans();
          const running = scans.find(
            s => (s.status === 'scanning' || s.status === 'matching') && !s.completed_at
          );
          if (running && !cancelled) {
            setState(s => ({
              ...s,
              scanRunning: true,
              scanProgress: 0,
              scanFound: running.file_count ?? 0,
              scanPhase: running.status === 'matching' ? 'matching' : 'scanning',
              scanMessage: running.source === 'reparse' ? 'Resuming re-parse in progress…' : 'Resuming scan in progress…',
            }));
            await trackScan(running.id);
          }
          return;
        } catch {
          await new Promise(r => setTimeout(r, 5000));
        }
      }
    })();
    return () => { cancelled = true; };
    // Runs once on mount; trackScan is stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Visibility re-sync: returning to a throttled/backgrounded tab, re-check
  // reality instead of trusting frozen React state. Two failure modes healed:
  // (a) banner says running but the backend has nothing running (poll loop
  // died / Stop landed while throttled) → clear it; (b) backend HAS a running
  // scan the banner doesn't know (watcher/auto scan, other tab) → adopt it.
  useEffect(() => {
    const onVisible = () => {
      if (document.hidden) return;
      void (async () => {
        try {
          const scans = await api.listScans();
          const running = scans.find(
            s => (s.status === 'scanning' || s.status === 'matching') && !s.completed_at
          );
          if (!running && scanRunningRef.current) {
            setState(s => (s.scanRunning ? { ...s, scanRunning: false, scanTech: null } : s));
          } else if (running && !scanRunningRef.current) {
            setState(s => ({
              ...s,
              scanRunning: true,
              scanProgress: 0,
              scanFound: running.file_count ?? 0,
              scanPhase: running.status === 'matching' ? 'matching' : 'scanning',
              scanMessage: running.source === 'reparse' ? 'Resuming re-parse in progress…' : 'Resuming scan in progress…',
            }));
            await trackScan(running.id);
          }
        } catch { /* backend unreachable — the connectivity banner covers it */ }
      })();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const runScan = useCallback(async (reason?: string) => {
    // Synchronous guard FIRST — set before any await so a double-click or a
    // second caller in the same tick can never both reach createScan.
    if (scanStartInFlightRef.current) return;
    scanStartInFlightRef.current = true;
    try {
      await runScanInner(reason);
    } finally {
      scanStartInFlightRef.current = false;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.scanRunning, pushToast, refreshFiles, trackScan]);

  // `reason` (optional) overrides the banner's opening line — used by the
  // automatic post-import retry chain so those self-started scans announce
  // themselves ("Checking for newly imported files…") instead of looking like
  // a scan the user never triggered.
  const runScanInner = useCallback(async (reason?: string) => {
    if (state.scanRunning) {
      // User-reported bug: Dashboard "Scan now" button silently doing
      // nothing. Likely cause: a previous scan crashed and left
      // scanRunning=true (the finally clears it normally, but if the
      // browser navigated away mid-scan or React unmounted during the
      // long-running poll, the setState never ran). The button is then
      // disabled visually, looking "broken".
      //
      // Watchdog escape hatch: if scanRunning is true but we haven't
      // seen a progress update in 90 seconds, the polling loop is dead
      // (the browser tab was backgrounded long enough for setTimeout
      // throttling to kill it, the websocket died, etc.). Just clear
      // the flag and let this click start a new scan — there's no
      // actual in-flight loop to collide with.
      const stale = Date.now() - lastProgressAtRef.current > 90_000;
      if (stale) {
        // eslint-disable-next-line no-console
        console.warn('[Kira] scanRunning stuck true with no progress in >90s — force-clearing and proceeding.');
        setState(s => ({ ...s, scanRunning: false, scanProgress: 0 }));
        // Fall through to start a new scan below. We don't `return` so
        // the same click that triggered the watchdog reset also kicks
        // off the scan it was trying to start — feels responsive.
      } else {
        pushToast({
          title: 'Scan already in progress',
          sub: 'Wait for the current scan to finish — or reload the page if it appears stuck.',
          kind: 'error',
        });
        return;
      }
    }
    // Bug A: fetch the freshest paths from the server right before
    // creating the scan. Reading from React state was a race trap —
    // if the user clicked "Scan now" before `loadDefaults` had finished
    // hydrating `scanRoot` from settings, we'd send the initial fallback
    // value ('/media') and get a 400 "Library folder doesn't exist".
    // That was the "first scan fails, second scan works" bug — by the
    // time the user retried, hydration had completed.
    //
    // Fetching synchronously here means the call is ALWAYS authoritative
    // regardless of what the React state thinks. The 10ms HTTP cost is
    // negligible compared to the scan itself.
    let effectiveRoot = SCAN_ROOT;
    let extraRoots: string[] = [];
    let techOn = false;
    try {
      const s = await api.getSettings();
      // Refresh the tech-tag flag at scan time so the 3rd line reflects a
      // toggle made since mount (the popup shows "queued…" only when it's on).
      techOn = settingIsOn(s['parsing.read_mediainfo']);
      setTechTagsOn(techOn);
      // library_root may be saved as a bare string OR as {value: "..."}
      // depending on which write path produced it — mirror the same
      // parsing as the loadDefaults effect.
      const lr = s['paths.library_root'];
      if (typeof lr === 'string' && lr) {
        effectiveRoot = lr;
      } else if (lr && typeof lr === 'object' && 'value' in lr && typeof (lr as { value?: unknown }).value === 'string') {
        effectiveRoot = (lr as { value: string }).value;
      }
      const wf = s['paths.watch_folders'];
      if (Array.isArray(wf)) {
        extraRoots = wf.filter((x): x is string => typeof x === 'string' && x.length > 0);
      }
    } catch {
      // Fail-soft: if the settings GET fails, fall back to React state.
      // Worse than authoritative server fetch but better than aborting
      // the user's scan attempt entirely.
    }
    // Dedup + drop empties. Library root goes first so it's the
    // "primary" display path for the Scan history row.
    const allRoots = Array.from(new Set([effectiveRoot, ...extraRoots])).filter(p => !!p);

    setState(s => ({
      ...s,
      scanRunning: true, scanProgress: 0, scanFound: 0, scanPhase: 'scanning',
      // Preview the tech-tag line as "queued…" from the start when the feature
      // is on, so the 3rd line is present throughout (the tail fills in counts).
      scanTech: techOn ? { active: false, done: 0, total: null, state: 'running', queued: true } : null,
      scanMessage: reason
        ?? (allRoots.length > 1
          ? `Looking through ${allRoots.length} folders…`
          : `Looking through ${effectiveRoot}…`),
    }));

    // Backend kicks off the work as a background task and returns the scan id.
    // Bug A: pass `allRoots` so the worker walks the library root PLUS every
    // configured watch folder in one scan. createScan failure clears the
    // banner immediately; otherwise trackScan drives it to completion.
    let scan: Awaited<ReturnType<typeof api.createScan>>;
    try {
      scan = await api.createScan(effectiveRoot, allRoots);
    } catch (err) {
      const msg = (err as Error).message;
      // Phantom "already scanning": a scan IS running (a watcher/auto scan, or one
      // started in another tab) but this UI never knew — so the backend 409'd and
      // we'd otherwise just toast an error with no progress shown. ADOPT the
      // running scan and show its progress instead (no refresh needed).
      if (/already running|\b409\b/i.test(msg)) {
        try {
          const scans = await api.listScans();
          const running = scans.find(s => s.status === 'scanning' || s.status === 'matching');
          if (running) { await trackScan(running.id); return; }
        } catch { /* fall through to the error toast */ }
      }
      pushToast({
        title: 'Scan failed',
        sub: msg.includes('Failed to fetch')
          ? 'Backend not reachable — is uvicorn running on :8000?'
          : msg,
        kind: 'error',
      });
      setState(s => ({ ...s, scanRunning: false }));
      return;
    }
    await trackScan(scan.id);
  }, [state.scanRunning, pushToast, refreshFiles, trackScan]);

  // Stop the running scan (the Sidebar button toggles to "Stop" while scanning).
  // Finds the live scan + cancels it; the worker stops at its next step, keeps
  // what it found, and frees the lock. Also unsticks a phantom/stale lock.
  const stopScan = useCallback(async () => {
    try {
      const scans = await api.listScans();
      const running = scans.find(s => s.status === 'scanning' || s.status === 'matching');
      if (!running) {
        // Nothing actually running — clear any stale local flag so the button frees up.
        setState(s => ({ ...s, scanRunning: false }));
        pushToast({ title: 'No scan running', sub: 'Cleared the stuck indicator.', kind: 'success' });
        return;
      }
      const r = await api.cancelScan(running.id);
      if (!r.forced) {
        // Reflect the stop in the BANNER immediately — the worker keeps
        // running until its next await, and with no visible change "Stop"
        // looked broken. trackScan's 'cancelled' terminal state closes it out.
        setState(s => (s.scanRunning
          ? { ...s, scanMessage: 'Stopping — finishing the current step…' }
          : s));
      }
      pushToast({
        title: r.forced ? 'Cleared a stuck scan' : 'Stopping scan…',
        sub: r.forced ? 'No active worker was behind it — freed the lock.' : 'Finishing the current step, then stopping.',
        kind: 'success',
      });
      if (r.forced) {
        // No live worker existed — nothing will drive the banner to 'done'.
        setState(s => ({ ...s, scanRunning: false, scanTech: null }));
        return;
      }
      // Stop used to be fire-and-forget: it relied entirely on the main poll
      // loop observing 'cancelled'. If that loop was dead (browser throttled a
      // backgrounded tab), the backend cancelled fine but the banner showed
      // "running" forever. Confirm with a short bounded re-poll and clear the
      // banner ourselves if the loop hasn't already done it.
      for (let i = 0; i < 20; i++) {
        await new Promise(res => setTimeout(res, 1000));
        try {
          const s = await api.getScan(running.id);
          if (s.status !== 'scanning' && s.status !== 'matching') {
            setState(st => (st.scanRunning
              ? { ...st, scanRunning: false, scanTech: null }
              : st));
            return;
          }
        } catch { /* transient — keep confirming */ }
      }
      // Cancellation still pending after ~20s (a slow provider call is
      // finishing). Leave the banner to the poll loop / visibility re-sync.
    } catch (e) {
      pushToast({ title: 'Couldn’t stop the scan', sub: (e as Error).message, kind: 'error' });
    }
  }, [pushToast]);

  // Re-parse the EXISTING library in place. A normal scan skips
  // already-indexed files, so parser + folder-lock improvements only reach
  // NEW files; this re-runs the parser on every stored file and re-matches
  // non-manual ones (manual pins + history preserved). Reuses the scan
  // banner — the backend returns a Scan row we poll exactly like a scan.
  const runReparse = useCallback(async (scope?: { media_type?: string; file_ids?: number[] }) => {
    // Same synchronous re-entry guard as runScan (shared ref: a scan start and
    // a re-parse start racing each other is the same double-run bug).
    if (scanStartInFlightRef.current) return;
    scanStartInFlightRef.current = true;
    try {
      await runReparseInner(scope);
    } finally {
      scanStartInFlightRef.current = false;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.scanRunning, pushToast, refreshFiles]);

  const runReparseInner = useCallback(async (scope?: { media_type?: string; file_ids?: number[] }) => {
    if (state.scanRunning) {
      pushToast({
        title: 'Busy',
        sub: 'A scan or re-parse is already running — wait for it to finish.',
        kind: 'error',
      });
      return;
    }
    setState(s => ({
      ...s, scanRunning: true, scanProgress: 0, scanFound: 0, scanPhase: 'scanning',
      scanMessage: scope?.media_type ? `Re-parsing ${scope.media_type}…`
        : scope?.file_ids ? 'Re-parsing selected…'
        : 'Re-parsing library…',
    }));
    const myToken = ++activePollRef.current;
    try {
      scanStartedAtRef.current = Date.now();
      lastProgressAtRef.current = Date.now();
      const scan = await api.reparseLibrary(scope);
      let done = false;
      let stopped = false;
      let pollFailures = 0;
      let lastMatched = -1;
      let lastFilesRefetchAt = 0;
      let lastCount = -1;
      while (!done) {
        await new Promise(r => setTimeout(r, 800));
        // Superseded by a newer scan/reparse loop — stop writing banner state.
        if (activePollRef.current !== myToken) return;
        let s: typeof scan;
        try {
          s = await api.getScan(scan.id);
          pollFailures = 0;
        } catch (err) {
          // Same failure semantics as trackScan: 404 = row gone, terminal;
          // anything else tolerated for ~60s, never forever.
          if (err instanceof ApiError && err.status === 404) {
            throw new Error('Re-parse record no longer exists — it may have been cleared by a backend reset.');
          }
          if (++pollFailures >= 75) {
            throw new Error('Lost contact with the backend while tracking the re-parse.');
          }
          continue;
        }
        // Refetch the (heavy) full list only when counts actually moved —
        // this used to re-serialize the entire library every 800ms tick.
        const progressed = s.matched_count !== lastMatched || s.file_count !== lastCount;
        const finishing = s.status === 'completed' || s.status === 'completed_partial';
        // Throttle (§10 m): with the parallel match lane, "progressed" is true
        // on almost every poll tick — refetching the ENTIRE library each time
        // hammered the backend and the adapter. At most one refetch per 5s
        // mid-scan; the terminal refetch always runs.
        const refetchDue = finishing || (Date.now() - lastFilesRefetchAt) > 5000;
        if ((progressed && refetchDue) || finishing) {
          lastFilesRefetchAt = Date.now();
          lastMatched = s.matched_count;
          lastCount = s.file_count;
          try {
            const gen = filesGenRef.current;
            const rows = await api.listAllFiles();
            // Drop this background replace if a user mutation bumped the gen
            // mid-fetch — don't revert a manual match made during reparse.
            setState(st => (gen === filesGenRef.current ? { ...st, files: rows.map(apiToMediaFile) } : st));
          } catch { /* swallow */ }
        }
        if (s.status.startsWith('failed')) throw new Error(s.status);

        let pct = 0;
        let msg = 'Re-parsing…';
        let phase: AppState['scanPhase'] = 'scanning';
        const total = s.estimated_total;
        if (s.status === 'scanning') {
          // Re-parse walks the existing library; total is known almost
          // immediately, but treat it as the indeterminate DISCOVERY phase
          // for a consistent two-bar UX with a normal scan.
          phase = 'scanning';
          pct = 0;
          msg = `Re-parsing… ${s.file_count}${total ? ` / ${total}` : ''} files`;
        } else if (s.status === 'matching') {
          phase = 'matching';
          const denom = total ?? s.file_count;
          const matchPct = denom > 0 ? (s.matched_count / denom) : 0;
          pct = Math.min(100, Math.round(matchPct * 100));
          msg = `Re-matching ${s.matched_count} / ${denom}`;
        } else if (s.status === 'completed' || s.status === 'completed_partial') {
          phase = 'done';
          pct = 100;
          msg = `Done · ${s.file_count} files, ${s.matched_count} matched`;
          done = true;
        } else if (s.status === 'cancelled') {
          // Stopped mid-reparse — end the loop (mirrors trackScan; without this
          // it spins forever on 'cancelled' and flickers against the next scan).
          phase = 'done';
          msg = `Stopped · ${s.file_count} files, ${s.matched_count} matched`;
          done = true;
          stopped = true;
        } else if (s.status !== 'scanning' && s.status !== 'matching' && s.status !== 'pending') {
          done = true;
          stopped = true;
        }
        setState(st => ({ ...st, scanProgress: pct, scanFound: s.file_count, scanMessage: msg, scanPhase: phase }));
        if (progressed || done) lastProgressAtRef.current = Date.now();
      }
      // Superseded loops must not narrate the old run's completion (double
      // toasts) — same guard as trackScan.
      if (activePollRef.current !== myToken) return;
      await refreshFiles();
      let finalCounts = '';
      try {
        const final = await api.getScan(scan.id);
        finalCounts = `${final.file_count} files · ${final.matched_count} matched`;
      } catch { finalCounts = 'finished'; }
      pushToast({
        title: stopped ? 'Re-parse stopped' : 'Re-parse complete',
        sub: finalCounts,
        kind: 'success',
      });
    } catch (err) {
      if (activePollRef.current !== myToken) return;
      pushToast({
        title: 'Re-parse failed',
        sub: (err as Error).message.includes('Failed to fetch')
          ? 'Backend not reachable — is uvicorn running on :8000?'
          : (err as Error).message,
        kind: 'error',
      });
    } finally {
      // Superseded loops must not tear down the banner — see trackScan.
      if (activePollRef.current === myToken) {
        // Re-parse also spawns the tech-tag pass over the whole library — narrate
        // it as the popup's 3rd line too, so the read isn't an invisible pill.
        try { await narrateTechTail(t => setState(s => ({ ...s, scanTech: t })), techTagsOnRef.current, dismissJob, () => activePollRef.current === myToken); } catch { /* ignore */ }
        setTimeout(() => { if (activePollRef.current === myToken) setState(s => ({ ...s, scanRunning: false, scanTech: null })); }, 1600);
      }
    }
  }, [state.scanRunning, pushToast, refreshFiles]);

  // Event-driven rescan trigger. CoverPopup dispatches this when it
  // detects Sonarr completions (queue entries that were `downloading`/
  // `importing` and have now vanished) AND when the user clicks
  // "Force import" on a stuck-import row. Debounced 2.5s so a burst
  // of completions (10 episodes finishing within seconds) fires
  // exactly ONE scan, but quick enough that an isolated Force Import
  // click feels responsive — the user sees the "Just imported" row
  // morph into a real file row within seconds, not after a yawn.
  //
  // Flow after the debounce fires:
  //   1. runScan() — finds the new files on disk, computes parser
  //      output, attempts a matcher pass
  //   2. sonarrHealUnmatched() — for any file the matcher couldn't
  //      confidently match (AniDB banned, wrong year in filename,
  //      atypical title), uses Sonarr's authoritative metadata to
  //      pin a high-confidence Match. Sonarr already knew exactly
  //      what each file was when it downloaded them.
  //   3. refreshFiles() — pulls the now-matched files back into the
  //      Review page. The popup's "Just imported · scanning…" rows
  //      transition into real file rows with correct episode titles.
  // Live refs so the debounced rescan listener can subscribe ONCE (on mount)
  // and still read current values. Previously `state.scanRunning` (plus
  // runScan/refreshFiles identity) sat in the effect's dep array, so every
  // scan-state flip tore down + re-created the listener — clearing a pending
  // debounce timer (a requested rescan silently dropped) and, via the stale
  // captured scanRunning, occasionally double-dispatching.
  const scanRunningRef = useRef(state.scanRunning);
  const runScanRef = useRef(runScan);
  const refreshFilesRef = useRef(refreshFiles);
  const filesCountRef = useRef(0);
  useEffect(() => {
    scanRunningRef.current = state.scanRunning;
    runScanRef.current = runScan;
    refreshFilesRef.current = refreshFiles;
    filesCountRef.current = state.files.length;
  });

  useEffect(() => {
    let debounce: ReturnType<typeof setTimeout> | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;
    // A completed Sonarr download's queue entry vanishes, but the actual IMPORT
    // (moving the file into the library) can land seconds-to-minutes later —
    // slow move, NAS propagation, post-processing. A single scan often races it
    // and the file is never indexed. So after the first scan, if no new files
    // appeared, RETRY a couple more times before giving up. Delays are AFTER the
    // initial debounced scan; the sequence stops early the moment files grow.
    const RETRY_DELAYS = [30_000, 90_000];

    const scanThenHeal = async (): Promise<boolean> => {
      // A scan already in flight is looking for the file too — don't stack one,
      // and don't treat it as "landed" (keep the retry sequence alive).
      if (scanRunningRef.current) return false;
      const before = filesCountRef.current;
      try {
        // Labelled so the banner explains WHY a scan started on its own (a
        // download just imported), instead of reading as spontaneous activity.
        await runScanRef.current('Checking for newly imported files…');
      } catch (e) {
        console.warn('Auto-rescan failed', e);
        return false;
      }
      // Heal-via-Sonarr step. Best-effort + silent: failures leave files in
      // their pre-heal state. The popup's transitional row morphing into a real
      // row IS the feedback.
      try {
        const r = await api.sonarrHealUnmatched();
        if (r.healed > 0) await refreshFilesRef.current();
      } catch (e) {
        console.debug('Sonarr heal skipped', e);
      }
      const after = await refreshFilesRef.current();
      return (after?.length ?? before) > before;  // did the import land?
    };

    const startSequence = () => {
      if (retry) { clearTimeout(retry); retry = null; }
      let i = 0;
      const step = async () => {
        const landed = await scanThenHeal();
        if (landed || i >= RETRY_DELAYS.length) return;  // found, or gave up
        retry = setTimeout(step, RETRY_DELAYS[i]);
        i += 1;
      };
      void step();
    };

    const onRequest = () => {
      // Coalesce a burst of completion signals into one debounced sequence.
      if (debounce) clearTimeout(debounce);
      debounce = setTimeout(() => { debounce = null; startSequence(); }, 2500);
    };
    window.addEventListener('kira:request-rescan', onRequest);

    // Lighter sibling: a file MUTATION (deleting duplicates from the cover
    // popup) only needs the files list re-pulled — NOT a full disk scan.
    // Without it the global cache keeps the deleted rows until the next poll,
    // so a reopened popup still shows the now-gone duplicate sign for a while.
    const onFilesChanged = () => { void refreshFilesRef.current(); };
    window.addEventListener('kira:files-changed', onFilesChanged);

    return () => {
      window.removeEventListener('kira:request-rescan', onRequest);
      window.removeEventListener('kira:files-changed', onFilesChanged);
      if (debounce) clearTimeout(debounce);
      if (retry) clearTimeout(retry);
    };
  }, []);  // subscribe ONCE — refs above keep the callback reading live values

  // Scoped reparse: the cover popup (per-album) and the Dashboard menu (per-type)
  // fire `kira:reparse` with an optional {media_type|file_ids} scope → run it
  // through the same handler so the scan banner + polling work identically.
  useEffect(() => {
    const onReparse = (e: Event) =>
      void runReparse((e as CustomEvent).detail as { media_type?: string; file_ids?: number[] } | undefined);
    window.addEventListener('kira:reparse', onReparse);
    return () => window.removeEventListener('kira:reparse', onReparse);
  }, [runReparse]);

  // ── Action handlers (backed by the API; local state mirrors the response) ──
  // Defined here, BEFORE the keyboard useEffect, so the effect's dependency
  // array can reference them without hitting a TDZ error at render time.
  const setFileStatus = useCallback(async (id: string, status: 'approved' | 'rejected' | 'pending') => {
    const backendId = Number(id);
    if (!Number.isFinite(backendId)) {
      bumpFilesGen(); setState(s => ({ ...s, files: s.files.map(f => f.id === id ? { ...f, status } : f) }));
      return;
    }
    try {
      const updated = await api.updateFileStatus(backendId, status);
      bumpFilesGen(); setState(s => ({ ...s, files: s.files.map(f => f.id === id ? apiToMediaFile(updated) : f) }));
    } catch (e) {
      pushToast({ title: 'Failed to update', sub: (e as Error).message, kind: 'error' });
    }
  }, [pushToast]);

  const setFileStatusBulk = useCallback(async (ids: string[], status: 'approved' | 'rejected' | 'pending') => {
    const backendIds = ids.map(Number).filter(Number.isFinite);
    if (backendIds.length === 0) {
      bumpFilesGen(); setState(s => ({ ...s, files: s.files.map(f => ids.includes(f.id) ? { ...f, status } : f) }));
      return;
    }
    // Snapshot previous statuses so we can revert on backend failure.
    // Previously this fired optimistically without a rollback path —
    // when the call failed, the toast said "Failed to update" but the
    // UI kept the wrong status until next refresh, gaslighting the user.
    let prevByid = new Map<string, string>();
    setState(s => {
      prevByid = new Map(s.files.filter(f => ids.includes(f.id)).map(f => [f.id, f.status]));
      return { ...s, files: s.files.map(f => ids.includes(f.id) ? { ...f, status } : f) };
    });
    try {
      await api.bulkStatus(backendIds, status);
    } catch (e) {
      // Revert local state — the backend rejected, so the UI must too.
      setState(s => ({
        ...s,
        files: s.files.map(f =>
          prevByid.has(f.id)
            ? { ...f, status: prevByid.get(f.id) as typeof f.status }
            : f
        ),
      }));
      pushToast({ title: 'Failed to update', sub: (e as Error).message, kind: 'error' });
    }
  }, [pushToast]);

  /** Direct rename for a list of file IDs — no modal, uses the saved
   *  default profile + op. Approve and rename should be one click for
   *  90% of cases; the modal is reserved for "I want to preview / pick
   *  non-default settings".
   *
   *  Declared here (BEFORE the keyboard useEffect) so the `a` key
   *  handler can reference it without hitting a temporal dead zone.
   *  The previous placement (after pickCandidate, ~line 480) broke the
   *  whole app — useEffect deps array evaluated `renameFilesDirectly`
   *  before it was initialized, blanking the page.
   *
   *  ── Serialization (H1) ───────────────────────────────────────────
   *  Multiple concurrent rename calls (rapid keyboard `a`, "Approve &
   *  rename" bulk-bar smashed twice, popup approve cascading through 8
   *  episodes) used to race each other AND race their own preceding
   *  setFileStatusBulk. Backend would see one rename targeting matches
   *  whose `is_selected` flag was still mid-commit from a sibling
   *  request — silent wrong-target renames.
   *
   *  We now serialize ALL rename calls behind a Promise chain held in
   *  `renameChainRef`. Each new call appends; the next one only runs
   *  after the previous resolves. The chain never blocks the UI thread
   *  — it just enforces "rename N completes before rename N+1 starts".
   *  Cost: a perceived <100ms queue for users mashing the button. */
  const renameChainRef = useRef<Promise<void>>(Promise.resolve());
  // PB-4: timestamp captured when a scan starts polling. Used to derive
  // an ETA in the scan banner — `(elapsed_ms / matched_count)` gives a
  // matched-per-ms rate that extrapolates over (estimated_total - matched).
  const scanStartedAtRef = useRef<number | null>(null);
  // Watchdog: bumped to `Date.now()` on every progress tick inside the
  // scan polling loop. The runScan early-return path checks this — if
  // scanRunning is true but the ref hasn't been touched in >90s, the
  // loop is dead (browser tab throttling, hot-reload, etc.) and the
  // next click force-clears scanRunning instead of silently no-op'ing.
  // Identity of the scan/reparse the progress banner currently belongs to. Each
  // poll loop stamps this when it starts and bails the moment it no longer
  // matches — so a stale loop (e.g. one left spinning on a cancelled scan, or a
  // re-attach that raced a fresh Scan-now) can't keep writing banner state and
  // flicker against the live loop. Bumped via a monotonic token, not the scan
  // id, so even adopting the SAME scan id from two paths resolves to one winner.
  const activePollRef = useRef(0);
  // Synchronous re-entry guard for scan/re-parse START. `state.scanRunning`
  // only flips after runScan's awaited settings fetch resolves (a full RTT to
  // a NAS backend), so a double-click — or the onboarding auto-scan racing a
  // user click — used to fire TWO createScan POSTs back-to-back: the 409-adopt
  // usually saved the first, but a fast first scan let the second start a
  // whole new run ("scan seems to run 2 times"). A ref flips before any await.
  const scanStartInFlightRef = useRef(false);
  const renameFilesDirectly = useCallback(async (fileIds: string[], opts?: { profile?: string; op?: string }): Promise<void> => {
    const backendIds = fileIds.map(Number).filter(Number.isFinite);
    if (backendIds.length === 0) return;
    // Chain onto whatever's already in-flight. We capture the previous
    // chain BEFORE assigning the new one so concurrent callers all see
    // distinct predecessors and run strictly serially.
    const prev = renameChainRef.current;
    const run = (async () => {
      // R2-M4: await the previous chain so this one starts after it
      // settles, but DON'T let its failure block our turn — independent
      // rename batches must not cascade-fail each other.
      try {
        await prev;
      } catch {
        // Previous batch threw; toast was already shown by that batch.
        // Continue with our work.
      }
      // R2-M4: re-throw on failure so subsequent callers see this chain
      // as rejected, not silently resolved. The old `.catch(() => undefined)`
      // converted rejected → resolved which meant the NEXT renameFilesDirectly
      // call had no idea the previous one failed — and on a partial-disk
      // failure (source moved, dest missing), the next call would phantom-
      // rename and silently double-record.
      try {
        // Kick the activity pill so the rename progress bar shows up immediately
        // — the backend emits begin/progress for the batch on /activity.
        try { window.dispatchEvent(new Event('kira:activity-refresh')); } catch { /* no window */ }
        const effProfile = opts?.profile ?? savedProfile;
        const effOp = opts?.op ?? savedOp;
        const res = await api.rename({ file_ids: backendIds, profile: effProfile, op: effOp });
        await refreshFiles();   // guarded full-list reload (gen-checked)
        if (res.failed === 0) {
          // Tier 1.2: the backend tags videos whose sidecar files were
          // moved along with the video via a "[SIDECARS] …" prefix on
          // an otherwise-successful item's error field. Count them so
          // the user knows subs / aux audio rode along — Plex/Jellyfin
          // users will appreciate the explicit confirmation.
          const withSubs = res.items.filter(
            i => i.ok && typeof i.error === 'string' && i.error.startsWith('[SIDECARS]'),
          ).length;
          const subNote = withSubs > 0
            ? ` · sidecars moved on ${withSubs} of ${res.succeeded}`
            : '';
          // Pre-warm History's cache so navigating to that tab paints
          // the fresh rows instantly instead of the "blank → 500ms gap
          // → everything pops in" pattern. Best-effort; failure here
          // just means History has to fetch on its own (which is fine).
          // We ALSO use the freshly-fetched rows to power an inline "Undo"
          // on the success toast (history is newest-first, so the top
          // `res.succeeded` rows are exactly what we just renamed).
          let undoIds: number[] = [];
          try {
            const [rows, counts] = await Promise.all([
              api.listHistory(),
              api.historyCounts(),
            ]);
            cacheSet('history.items', rows);
            cacheSet('history.counts', counts);
            undoIds = rows.slice(0, res.succeeded).map(r => r.id);
          } catch {
            // History prefetch failed; the History tab will still
            // refetch when the user navigates to it. (No Undo action then.)
          }
          pushToast({
            title: `${res.succeeded} file${res.succeeded === 1 ? '' : 's'} renamed`,
            sub: `${effOp} · ${effProfile}${subNote} — see Renamed filter or History.`,
            kind: 'success',
            action: undoIds.length ? {
              label: undoIds.length === 1 ? 'Undo' : 'Undo all',
              onClick: () => { void (async () => {
                try {
                  const r = await api.undoHistoryBulk(undoIds);
                  await refreshFiles();
                  window.dispatchEvent(new CustomEvent('kira:files-changed'));
                  pushToast({
                    title: `${r.succeeded} rename${r.succeeded === 1 ? '' : 's'} undone`,
                    kind: 'success',
                  });
                } catch (e) {
                  pushToast({ title: 'Undo failed', sub: (e as Error).message, kind: 'error' });
                }
              })(); },
            } : undefined,
          });
          window.dispatchEvent(new CustomEvent('kira:rename-success'));
          return;
        }
        if (res.succeeded > 0) {
          pushToast({
            title: `${res.succeeded} renamed, ${res.failed} failed`,
            sub: res.items.find(i => !i.ok)?.error ?? 'See History page for details.',
            kind: 'error',
          });
          // Partial failure — still throw so chain knows something was wrong
          throw new Error(`${res.failed} of ${res.succeeded + res.failed} files failed to rename`);
        }
        pushToast({
          title: `Rename failed`,
          sub: res.items.find(i => !i.ok)?.error ?? 'See History page for details.',
          kind: 'error',
        });
        throw new Error(res.items.find(i => !i.ok)?.error ?? 'All files failed to rename');
      } catch (e) {
        // Toast is already shown above for known failure shapes; for
        // surprise errors (network drop, JSON parse) toast here.
        if (!(e instanceof Error) || !e.message.includes('files failed to rename')) {
          pushToast({ title: 'Rename failed', sub: (e as Error).message, kind: 'error' });
        }
        throw e;
      }
    })();
    renameChainRef.current = run;
    return run;
  }, [savedOp, savedProfile, pushToast]);

  const pickCandidate = useCallback(async (fileId: string, candidate: { matchId?: number; title?: string; year?: number | null }) => {
    const backendFileId = Number(fileId);
    if (!candidate.matchId || !Number.isFinite(backendFileId)) {
      pushToast({ title: 'Cannot select', sub: 'This candidate has no backend record yet.', kind: 'error' });
      return;
    }
    try {
      const updated = await api.selectMatch(backendFileId, candidate.matchId);
      bumpFilesGen(); setState(s => ({ ...s, files: s.files.map(f => f.id === fileId ? apiToMediaFile(updated) : f) }));
      pushToast({ title: 'Match changed', sub: `${candidate.title}${candidate.year ? ' (' + candidate.year + ')' : ''}`, kind: 'success' });
    } catch (e) {
      pushToast({ title: 'Failed to select match', sub: (e as Error).message, kind: 'error' });
    }
  }, [pushToast]);

  // Content-hash identify (M5): hash the file's bytes, ask OpenSubtitles which
  // release it is, pin the resulting TMDB match. The only matching path that
  // works on a totally-garbage filename. Throws on failure so the calling
  // modal stays open (the error is already toasted).
  const handleIdentifyByHash = useCallback(async (file: MediaFile) => {
    const backendId = Number(file.id);
    if (!Number.isFinite(backendId)) {
      pushToast({ title: 'Cannot identify', sub: 'This file has no backend record yet.', kind: 'error' });
      throw new Error('no backend id');
    }
    try {
      const updated = await api.identifyByHash(backendId);
      const mapped = apiToMediaFile(updated);
      bumpFilesGen(); setState(s => ({ ...s, files: s.files.map(f => f.id === file.id ? mapped : f) }));
      pushToast({ title: 'Identified by content', sub: mapped.match?.title || mapped.filename, kind: 'success' });
    } catch (e) {
      const msg = (e as Error).message;
      pushToast({ title: 'No content match', sub: msg, kind: 'error' });
      throw e;  // keep the modal open on failure
    }
  }, [pushToast]);

// (handleFetchSubtitles removed with FileDetailsModal — per-file subtitle
// fetch lives in CoverPopup's missing-subs action now.)


  useEffect(() => {
    const isFormField = (el: Element | null) =>
      el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' || (el as HTMLElement).isContentEditable);

    let gMode = false;
    let gModeTimer: ReturnType<typeof setTimeout>;

    const handler = (e: KeyboardEvent) => {
      if (isFormField(e.target as Element) && e.key !== 'Escape') return;

      if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key.toLowerCase() === 'a') {
        e.preventDefault();
        const ids = state.files.filter(f => f.confidence >= getConfBands().high && f.status === 'pending').map(f => f.id);
        if (!ids.length) {
          pushToast({ title: 'No high-confidence matches to approve', kind: 'info' });
          return;
        }
        // Approve AND rename — every other approve path (card, bulk bar, popup)
        // renames on approve. This shortcut used to bare-approve, stranding the
        // files in "approved but not renamed" limbo that no other flow produces.
        void (async () => {
          await setFileStatusBulk(ids, 'approved');
          if (renameFilesDirectly) await renameFilesDirectly(ids);
        })();
        pushToast({ title: `${ids.length} high-confidence match${ids.length === 1 ? '' : 'es'} approved & renamed`, kind: 'success' });
        return;
      }
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
        e.preventDefault();
        openModal('renamePreview', state.files.filter(f => f.status === 'approved' || f.confidence >= getConfBands().high));
        return;
      }

      if (gMode) {
        clearTimeout(gModeTimer);
        gMode = false;
        if (e.key === 'd') { setActive('dashboard'); e.preventDefault(); return; }
        if (e.key === 'r') { setActive('review'); e.preventDefault(); return; }
        if (e.key === 'h') { setActive('history'); e.preventDefault(); return; }
        if (e.key === 's') { setActive('settings'); e.preventDefault(); return; }
      }

      if (e.key === '?' || (e.shiftKey && e.key === '/')) {
        e.preventDefault();
        openModal('shortcuts');
        return;
      }
      if (e.key === '/') {
        e.preventDefault();
        (document.querySelector('.sidebar-search input') as HTMLInputElement)?.focus();
        return;
      }
      if (e.key === 'g') {
        gMode = true;
        gModeTimer = setTimeout(() => { gMode = false; }, 700);
        return;
      }
      if (e.key === 'Escape') {
        if (modal) { closeModal(); return; }
      }

      // Review-item navigation (j/k/a/r/m/x/Enter) is owned by ReviewPage now
      // — it operates on the SAME LibraryItems the grid renders (matching the
      // `focused` highlight on item ids). The old handler here keyed off raw
      // file ids, which never matched the grid's `lib_…` item ids, so every
      // shortcut silently no-op'd. Global shortcuts (g-mode, ⌘⇧A, Esc) stay.
    };

    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [active, focusedId, state.files, modal, pushToast, setFileStatus, setFileStatusBulk, renameFilesDirectly]);

  const handleApply = useCallback(async (opts: { profile: string; op: string }) => {
    const target = (modal?.kind === 'renamePreview' ? modal.payload : []) as MediaFile[];
    // Only ship files with a REAL provider+providerId — synthesised
    // matches (built from parsed data for no_match cards) would otherwise
    // get sent and the backend would reject each one individually.
    const ids = target
      .filter(f => f.match?.provider && f.match?.providerId)
      .map(f => f.id);
    if (ids.length === 0) {
      pushToast({ title: 'Nothing to rename', sub: 'No files with matches selected.', kind: 'error' });
      closeModal();
      return;
    }
    // Route through the SAME serialized rename chain as every other path, with
    // this preview's per-batch profile/op override. A direct api.rename here
    // raced renameFilesDirectly (and its own setFileStatusBulk) — the exact
    // wrong-target / double-record race renameChainRef exists to prevent. The
    // chain owns the toast / files-refresh / "kira:rename-success" + history
    // pre-warm, so we just close the modal once it settles.
    try {
      await renameFilesDirectly(ids, { profile: opts.profile, op: opts.op });
    } catch {
      // renameFilesDirectly already surfaced the failure toast.
    }
    closeModal();
  }, [modal, pushToast, renameFilesDirectly]);

  const handleManualSelect = useCallback(async (selection: SearchResult & { _provider?: string; _providerId?: string; _posterUrl?: string | null }) => {
    if (modal?.kind !== 'manualSearch') return;
    const file = modal.payload;
    const backendId = Number(file.id);
    // Find ALL files in the same cluster — siblings sharing the user's
    // file's series_key. Previously this only applied the pick to the
    // single file the user opened Manual Search from, so a cluster of 8
    // wrongly-matched files saw 1 fix + 7 still-wrong. From the user's
    // perspective Manual Search "did nothing" because the cover card
    // still showed the bad match (driven by the highest-confidence file
    // in the cluster, which was usually one of the 7 untouched ones).
    //
    // Now: when the selected file has a series_key AND siblings, apply
    // the manual pick to ALL of them in one bulk call. Movies (and any
    // file with no series_key) fall through to the single-file path
    // since there's no cluster to bulk-apply to.
    // Prefer the EXPLICIT scope the opener attached (the card's actual file
    // set, or the single file for a per-file action). The parsed-seriesKey
    // expansion below is only the fallback for callers that don't pass one:
    // it mismatched the displayed card both ways — a merged cluster spans
    // multiple parsed keys (Re-identify re-pinned only a subset, so the cover
    // "didn't change"), and a per-file pick bulk-clobbered every sibling
    // sharing the key.
    const explicitIds = (file as { _clusterFileIds?: string[] })._clusterFileIds;
    const seriesKey = (file as { seriesKey?: string | null }).seriesKey ?? null;
    const cluster = explicitIds && explicitIds.length
      ? state.files.filter(f => explicitIds.includes(f.id))
      : seriesKey
      ? state.files.filter(f => (f as { seriesKey?: string | null }).seriesKey === seriesKey)
      : [file];
    const clusterIds = cluster
      .map(f => Number(f.id))
      .filter(Number.isFinite);
    try {
      if (clusterIds.length > 1) {
        // Bulk path — covers the cluster-of-files case (e.g. a TV/anime
        // series with N episode files all wrongly matched to the same
        // bad show; pinning the right show fixes everyone at once).
        const res = await api.bulkSelectManualMatch({
          file_ids: clusterIds,
          provider: (selection._provider ?? 'tvdb').toLowerCase(),
          provider_id: selection._providerId ?? '',
          title: selection.title ?? null,
          year: selection.year ?? null,
          // Forward the provider's poster_url from the search result.
          // Without this, the backend's poster_url guard kept the old
          // (wrong-match) poster, so the cover never visibly updated.
          poster_url: selection._posterUrl ?? null,
          overview: selection.overview ?? null,
          media_type: selection.mediaType ?? file.mediaType,
        });
        await refreshFiles();   // guarded full-list reload (gen-checked)
        pushToast({
          title: `Pinned ${res.updated} file${res.updated === 1 ? '' : 's'} to ${selection.title}`,
          sub: 'Future rescans will leave these alone.',
          kind: 'success',
        });
      } else {
        // Single-file path — movies, orphans, files without a cluster.
        const updated = await api.selectManualMatch(backendId, {
          provider: (selection._provider ?? 'tvdb').toLowerCase(),
          provider_id: selection._providerId ?? '',
          title: selection.title ?? null,
          year: selection.year ?? null,
          // Forward the provider's poster_url so the backend writes it
          // onto the (possibly-commandeered) Match row — otherwise the
          // cover stays on whatever the prior auto-match's poster was
          // (the "Match updated" toast appears but nothing visibly
          // changes on screen because the row's poster_url is unchanged).
          poster_url: selection._posterUrl ?? null,
          overview: selection.overview ?? null,
          media_type: selection.mediaType ?? file.mediaType,
        });
        bumpFilesGen(); setState(s => ({ ...s, files: s.files.map(f => f.id === file.id ? apiToMediaFile(updated) : f) }));
        pushToast({ title: 'Match updated', sub: `${selection.title}${selection.year ? ' (' + selection.year + ')' : ''}`, kind: 'success' });
      }
    } catch (e) {
      pushToast({ title: 'Failed to apply match', sub: (e as Error).message, kind: 'error' });
    }
  }, [modal, pushToast, state.files]);

  // `rematchCluster` was removed when the popup's "Re-match" button
  // was replaced by "Re-identify" (manual search → bulk-select-manual
  // with per-file cour routing). `api.rematchFile` still exists for
  // auto-heal + bulk-rematch-all on the backend; nothing in the frontend
  // calls it directly anymore.

  /** Bulk-pin one show across N files. Backend writes is_manual=true so
   *  the user's pick survives every subsequent heal/rematch. Used by the
   *  "Match all to..." flow in the Needs matching section. */
  const handleBulkManualMatch = useCallback(async (
    fileIds: string[],
    // Matches the `onBulkManualMatch` prop contract in ReviewPage: the picked
    // result's optional fields can be null (not just undefined). The body
    // normalizes with `?? null` before hitting the API, so null is fine.
    selection: {
      title?: string | null; year?: number | null; overview?: string | null;
      mediaType?: string; _provider?: string; _providerId?: string;
    },
    contextMediaType?: string,
  ) => {
    const backendIds = fileIds.map(id => Number(id)).filter(Number.isFinite);
    if (backendIds.length === 0) return;
    try {
      const res = await api.bulkSelectManualMatch({
        file_ids: backendIds,
        provider: (selection._provider ?? 'tvdb').toLowerCase(),
        provider_id: selection._providerId ?? '',
        title: selection.title ?? null,
        year: selection.year ?? null,
        overview: selection.overview ?? null,
        media_type: selection.mediaType ?? contextMediaType,
      });
      // Refetch all files so the new matches propagate everywhere
      // (Needs matching section recomputes, cards re-render with the
      // matched cover, the no_match counts drop).
      await refreshFiles();   // guarded full-list reload (gen-checked)
      pushToast({
        title: `Pinned ${res.updated} file${res.updated === 1 ? '' : 's'} to ${selection.title}`,
        sub: 'Future rescans will leave these alone.',
        kind: 'success',
      });
    } catch (e) {
      pushToast({ title: 'Bulk match failed', sub: (e as Error).message, kind: 'error' });
    }
  }, [pushToast]);

  // Until auth state is known, render NOTHING but the backdrop — otherwise a
  // refresh on an auth-protected server flashes the dashboard (with cached
  // library data) for the beat it takes /auth/status to answer. Tabs that
  // already hold credentials start at 'open', so the signed-in refresh path
  // renders instantly with no splash.
  if (authState !== 'open') {
    return (
      <MotionConfig reducedMotion="user">
        <div className="backdrop" />
        {(authState === 'login' || authState === 'setup') && <LoginGate mode={authState} />}
      </MotionConfig>
    );
  }

  return (
    <MotionConfig reducedMotion="user">
      <div className="backdrop" />
      {authResolved && !onboarded && (
        <Onboarding onComplete={() => {
          setOnboardedState(true);
          // Land on the dashboard with the one-shot entrance choreography,
          // then kick the first scan once the cascade has had its moment.
          setActive('dashboard');
          setGrandEntry(true);
          setTimeout(() => setGrandEntry(false), 3600);
          pushToast({ title: "You're all set", sub: 'Running your first scan now…', kind: 'success' });
          setTimeout(() => runScan(), 900);
        }} />
      )}
      <div className="relative z-[1] min-h-screen lg:grid lg:grid-cols-[var(--side-w)_1fr]">
        <Sidebar active={active} setActive={setActive} settingsSection={settingsSection} setSettingsSection={setSettingsSection} pendingCount={pendingCount} scanRunning={state.scanRunning} backendOk={backendOk} mobileOpen={mobileNavOpen} onClose={() => setMobileNavOpen(false)} searchQuery={searchQuery} onSearchChange={handleSearchChange} onScan={runScan} onStop={stopScan} onShortcuts={() => openModal('shortcuts')} />
        {/* Mobile drawer backdrop — tap to dismiss (hidden on lg+) */}
        {mobileNavOpen ? (
          <div className="fixed inset-0 z-40 bg-black/50 backdrop-blur-sm lg:hidden" onClick={() => setMobileNavOpen(false)} />
        ) : null}
        <main className="main relative min-w-0">
          <Topbar
            active={active}
            onMenuClick={() => setMobileNavOpen(true)}
          />

          {/* Brand sweep — a thin orange→magenta line wipes across the top of the
              content column each time the page changes, giving the transition a
              "loading into place" energy. Keyed by `active` so it replays per page
              switch. Absolutely positioned overlay (NOT an ancestor of page
              content) so it can use transform freely without creating a
              containing block for the pages' sticky headers. */}
          <AnimatePresence>
            <motion.span
              key={`sweep-${active}`}
              aria-hidden="true"
              className="pointer-events-none absolute inset-x-0 top-[62px] z-40 h-[2px] origin-left"
              style={{ background: 'var(--brand-grad)' }}
              initial={{ scaleX: 0, opacity: 0.9 }}
              animate={{ scaleX: 1, opacity: 0 }}
              transition={{ duration: 0.55, ease: [0.16, 1, 0.3, 1] }}
            />
          </AnimatePresence>

          {/* Page-change transition. Keyed by `active` so switching the top-level
              page remounts the stage and replays the CSS entrance (kFade here +
              the .page-stage-inner rise); switching Settings sub-sections (active
              stays 'settings') does NOT replay — the sidebar sub-nav handles that.
              Deliberately NO exit animation / AnimatePresence: the stage's CSS
              entrance (fill-mode: both) overrode motion's inline exit opacity, so
              under mode="wait" the OLD page froze at full opacity for the whole
              exit duration and then popped away — read as "the previous page
              flashes" when navigating. One animation owner (CSS) fixes it and
              makes navigation instant. Opacity-only on this wrapper by design: a
              transform here would become a containing block and break the sticky
              row-header / scan bar inside the pages. */}
          <div key={active} className={grandEntry ? "page-stage grand-entry" : "page-stage"}>
              {active === 'dashboard' && (
                <DashboardPage state={state} openModal={openModal} runScan={runScan} runReparse={runReparse} setActive={setActive} scanRoot={SCAN_ROOT} />
              )}
              {active === 'review' && (
                <ReviewPage
                  state={state} openModal={openModal}
                  focusedId={focusedId} setFocusedId={setFocusedId}
                  setFileStatus={setFileStatus}
                  setFileStatusBulk={setFileStatusBulk}
                  searchQuery={searchQuery}
                  onBulkManualMatch={handleBulkManualMatch}
                  renameFilesDirectly={renameFilesDirectly}
                  onPickCandidate={(id, candidate) => { void pickCandidate(id, candidate); }}
                  pushToast={pushToast}
                />
              )}
              {active === 'history' && (
                <HistoryPage pushToast={pushToast} />
              )}
              {active === 'settings' && (
                <SettingsPage state={state} pushToast={pushToast} section={settingsSection} setSection={setSettingsSection} onDirtyChange={d => { settingsDirtyRef.current = d; }} />
              )}
            </div>
        </main>
      </div>

      {modal?.kind === 'manualSearch' && (
        <ManualSearchModal file={modal.payload} onClose={closeModal} onSelect={handleManualSelect} onIdentifyByContent={handleIdentifyByHash} />
      )}
      {modal?.kind === 'renamePreview' && (
        <RenamePreviewModal
          files={modal.payload}
          onClose={closeModal}
          onApply={handleApply}
          defaultOp={savedOp}
          defaultProfile={savedProfile}
        />
      )}
      {modal?.kind === 'shortcuts' && (
        <KeyboardShortcutsModal onClose={closeModal} />
      )}

      {/* Persistent activity indicator (scan progress / background job) — fixed
          in the bottom-right corner; the Sonner toast stack lifts above it. */}
      {(state.scanRunning || activeJob) ? (
        <div ref={activityRef} className="fixed bottom-6 right-6 z-[9998] flex flex-col items-end gap-2">
          {/* Scan/re-parse popup AND background-job pill now STACK instead of
              the old either/or slot (a running scan used to HIDE every other
              job — subtitle backfill, poster warmup — until it finished). */}
          {state.scanRunning ? (
            <ScanProgress
              phase={state.scanPhase}
              progress={state.scanProgress}
              found={state.scanFound}
              message={state.scanMessage}
              tech={state.scanTech}
              status={scanStatus}
            />
          ) : null}
          {/* Show the background pill alongside a scan, EXCEPT the mediainfo
              job the scan popup already narrates as its 3rd line (avoid the
              same job appearing twice). */}
          {activeJob && !(state.scanRunning && activeJob.name === 'mediainfo_enrich') ? (
            <ActivityPill job={activeJob} onDismiss={dismissJob} />
          ) : null}
        </div>
      ) : null}

      {/* Untitled UI notifications (Sonner). Offset is the MEASURED activity-panel
          height so the stack always clears it (CSS-transitioned — see App above). */}
      <NotificationToaster offset={toastOffset} />

      {/* Manual subtitle browse-and-pick — opens on any "No EN" chip click. */}
      <SubtitleBrowseModal pushToast={pushToast} />
    </MotionConfig>
  );
}
