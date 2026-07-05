import { useEffect, useMemo, useRef, useState } from 'react';
import { api, loginBasic, setupAccount } from '../lib/api';
import { IcLogoMark, IcAlertTri, IcSpin, IcArrowRight, IcShieldCheck, IcSparkles } from '../lib/icons';

/** Lock page scroll while a full-screen overlay is mounted — the app behind
 *  the blur must not scroll underneath (it was faintly visible doing so). */
export function useScrollLock() {
  useEffect(() => {
    const prevBody = document.body.style.overflow;
    const prevHtml = document.documentElement.style.overflow;
    document.body.style.overflow = 'hidden';
    document.documentElement.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = prevBody;
      document.documentElement.style.overflow = prevHtml;
    };
  }, []);
}

function shuffle<T>(arr: T[]): T[] {
  const a = [...arr];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

const RAIL_COUNT = 5;
const RAIL_LEN = 14;

// Bundled poster art served from public/backdrop — 20 PUBLIC-DOMAIN vintage
// film posters (silent-era / 1920s, from Wikimedia Commons; copyright expired,
// free to redistribute). On a FRESH boot the server backdrop is empty by
// construction — no library art yet, and no TMDB key to fetch "popular" — so
// the rails used to render nothing (the `pool.length < 6` bail). These
// fill/pad the pool so the login screen always has moving art, offline
// included. See public/backdrop/CREDITS.md for provenance.
const BUILTIN_POSTERS: string[] = Array.from(
  { length: 20 },
  (_, i) => `/backdrop/builtin-${String(i + 1).padStart(2, '0')}.jpg`,
);

/** Five tilted poster rails behind the auth card, sliding in alternating
 *  directions on a 3D plane. Posters are the library's own art topped up
 *  with TMDB popular titles (server-shuffled per request) and partitioned
 *  client-side WITHOUT repeats until the pool runs dry — plus randomized
 *  speeds, so every login looks different. An empty pool / failed fetch
 *  just leaves the plain backdrop. */
function PosterRails() {
  const [rows, setRows] = useState<string[][] | null>(null);

  useEffect(() => {
    let cancelled = false;
    // The bundled posters are TEMPORARY placeholders, shown only until the
    // user has scanned a library. `art` is the user's OWN cover art (backdrop
    // endpoint → Match.poster_url); once they have enough of it, we show THEIR
    // covers and drop the builtins entirely. Below the threshold their art
    // still leads and builtins just fill the empty rail slots (and are the
    // whole pool on a fresh, offline first boot).
    const build = (art: string[]) => {
      const real = shuffle([...new Set(art)]);
      const pool = real.length >= 6 ? real : [...real, ...shuffle(BUILTIN_POSTERS)];
      if (pool.length === 0) return; // truly nothing (shouldn't happen — builtins exist)
      const next = (() => { let i = 0; return () => pool[(i++) % pool.length]; })();
      setRows(Array.from({ length: RAIL_COUNT }, () =>
        Array.from({ length: Math.min(RAIL_LEN, pool.length) }, next)));
    };
    void api.getAuthBackdrop().then(b => {
      if (cancelled) return;
      build([...b.movies, ...b.anime, ...b.tv]);
    }).catch(() => {
      // Backend unreachable — still show the bundled rails so the login page
      // isn't a plain gradient offline.
      if (!cancelled) build([]);
    });
    return () => { cancelled = true; };
  }, []);

  // Randomized per-mount speeds + phase offsets so repeats never sync up.
  const motionSeeds = useMemo(
    () => Array.from({ length: RAIL_COUNT }, () => ({
      dur: 70 + Math.random() * 50,           // 70–120s per loop
      delay: -Math.random() * 60,             // start mid-flight
    })),
    [],
  );

  if (!rows) return null;
  return (
    <div className="login-bg" aria-hidden>
      <div className="login-bg-plane">
        {rows.map((urls, i) => (
          <div
            key={i}
            className={`login-bg-row ${i % 2 === 1 ? 'rev' : ''}`}
            style={{
              animationDuration: `${motionSeeds[i].dur}s`,
              animationDelay: `${motionSeeds[i].delay}s`,
            }}
          >
            {/* strip duplicated for a seamless marquee loop */}
            {[...urls, ...urls].map((u, j) => (
              <img key={j} src={u} alt="" loading="lazy" referrerPolicy="no-referrer" />
            ))}
          </div>
        ))}
      </div>
      <div className="login-bg-scrim" />
    </div>
  );
}

/** Full-screen auth page, two modes:
 *
 *  `setup` — first run, no account exists yet: a sign-up form that creates
 *  the server account (/auth/setup hashes + stores it), holds the
 *  credentials for this tab, and reloads into the app.
 *
 *  `login` — an account (or env credentials) exists: verify against
 *  /auth/check, store the header for the tab, reload. A clean mount re-runs
 *  every boot fetch with the Authorization header attached. */
export function LoginGate({ mode }: { mode: 'login' | 'setup' }) {
  const [user, setUser] = useState('');
  const [pass, setPass] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const userRef = useRef<HTMLInputElement>(null);
  useEffect(() => { userRef.current?.focus(); }, []);
  useScrollLock();

  const isSetup = mode === 'setup';
  const ready = isSetup
    ? user.trim().length > 0 && pass.length >= 6 && confirm === pass
    // Login also requires a non-empty password — submitting without one just
    // fired a doomed request instead of inline validation.
    : user.trim().length > 0 && pass.length > 0;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy || !ready) return;
    setBusy(true);
    setError(null);
    try {
      if (isSetup) {
        await setupAccount(user.trim(), pass);
        window.location.reload();
        return;
      }
      const ok = await loginBasic(user.trim(), pass);
      if (ok) {
        window.location.reload();
        return;
      }
      setError('Wrong username or password.');
      setPass('');
    } catch (err) {
      // Friendly mapping for the raw fetch failure ("Failed to fetch").
      const msg = (err as Error).message || '';
      setError(/failed to fetch|networkerror|load failed/i.test(msg)
        ? 'Can’t reach the Kira server — is it running?'
        : msg);
    }
    setBusy(false);
  };

  return (
    <div className="onboarding-root login-gate">
      <div className="backdrop" style={{ position: 'absolute' }} />
      <PosterRails />
      <form className="login-card" onSubmit={submit}>
        <div className="mark"><IcLogoMark /></div>
        <h1>{isSetup ? <>Create your <span className="grad">Kira</span> account</> : <>Sign in to <span className="grad">Kira</span></>}</h1>
        <div className="sub">
          {isSetup
            ? 'First run — choose the credentials this server will require from now on.'
            : 'This server requires credentials.'}
        </div>

        <label className="field">
          <span>Username</span>
          <input
            ref={userRef}
            className="input"
            autoComplete="username"
            value={user}
            onChange={e => setUser(e.target.value)}
            spellCheck={false}
          />
        </label>
        <label className="field">
          <span>Password{isSetup ? ' (6+ characters)' : ''}</span>
          <input
            className="input"
            type="password"
            autoComplete={isSetup ? 'new-password' : 'current-password'}
            value={pass}
            onChange={e => setPass(e.target.value)}
          />
        </label>
        {isSetup ? (
          <label className="field">
            <span>Confirm password</span>
            <input
              className="input"
              type="password"
              autoComplete="new-password"
              value={confirm}
              onChange={e => setConfirm(e.target.value)}
            />
          </label>
        ) : null}

        {isSetup && confirm && confirm !== pass ? (
          <div className="onb-state error"><IcAlertTri /><span>Passwords don't match.</span></div>
        ) : null}
        {error ? (
          <div className="onb-state error"><IcAlertTri /><span>{error}</span></div>
        ) : null}

        <button className="btn btn-primary submit" type="submit" disabled={busy || !ready}>
          {busy ? <IcSpin className="animate-spin" /> : isSetup ? <IcSparkles /> : <IcArrowRight />}
          {busy ? (isSetup ? 'Creating account…' : 'Signing in…') : (isSetup ? 'Create account' : 'Sign in')}
        </button>

        <div className="note">
          <IcShieldCheck />
          <span>
            {isSetup
              ? 'Stored hashed on your own server — nothing ever leaves it. Recover by clearing the auth rows in kira.db.'
              : 'Credentials are kept for this tab only and sent straight to your own server.'}
          </span>
        </div>
      </form>
    </div>
  );
}
