import { useState, useEffect, useRef, type CSSProperties, type ReactNode } from 'react';
import { AnimatePresence, MotionConfig, motion } from 'motion/react';
import type { ContentTypes } from '../lib/types';
import { api } from '../lib/api';
import {
  IcLogoMark, IcScan, IcSparkles, IcShieldCheck, IcLink,
  IcCheck, IcX, IcAlertTri, IcSpin, IcFolder, IcKey, IcArrowRight,
  IcFilm, IcTv, IcAnime, IcMusic, IcTag, IcUndo,
} from '../lib/icons';
import { FolderPickerModal } from './FolderPickerModal';
import { useScrollLock } from './LoginGate';
import { FfmpegStatusRow } from './FfmpegStatus';

const ONBOARDING_KEY = 'kira.onboarded.v1';

export function isOnboarded(): boolean {
  try { return localStorage.getItem(ONBOARDING_KEY) === 'true'; } catch { return false; }
}
export function setOnboarded(v: boolean) {
  try { localStorage.setItem(ONBOARDING_KEY, v ? 'true' : 'false'); } catch { /* noop */ }
}

type ValidationState =
  | { state: 'idle' }
  | { state: 'incomplete' }        // key entered but too short to be valid yet
  | { state: 'checking' }
  | { state: 'success'; latencyMs?: number | null; existing?: boolean }
  | { state: 'error'; error: string };

/** Validate a provider key WITHOUT persisting it first. The test endpoint
 *  accepts a candidate key in its body, so we only save (at complete()) after
 *  the user finishes — a wrong key can no longer clobber a working/bundled
 *  key that was already saved, and a failed test leaves storage untouched.
 *  Only called once the key LOOKS complete (length gate at the call site). */
async function validateProviderKey(
  provider: 'tmdb' | 'tvdb', _settingKey: string, key: string,
): Promise<{ ok: true; latencyMs: number | null } | { ok: false; error: string }> {
  try {
    const res = await api.testProvider(provider, { api_key: key });
    if (res.ok) return { ok: true, latencyMs: res.latency_ms ?? null };
    return { ok: false, error: res.detail || `${provider.toUpperCase()} rejected the key.` };
  } catch (e) {
    return { ok: false, error: friendlyError((e as Error).message) };
  }
}

/** Turn a raw fetch/HTTP error into something a first-time user can act on. */
function friendlyError(msg: string): string {
  const m = msg || '';
  if (/failed to fetch|networkerror|load failed/i.test(m)) return 'Can’t reach the Kira server — check your connection and try again.';
  if (/\b5\d\d\b/.test(m)) return 'The server hit an error validating the key. Try again in a moment.';
  return m;
}

const idx = (i: number): CSSProperties => ({ ['--i' as never]: i });

interface OnboardingProps {
  onComplete: (data: { apiKey: string; folder: string; profile: string; contentTypes: ContentTypes }) => void;
}

// ─── Step 0 · Welcome ────────────────────────────────────────────────

// Looping "messy → clean" rename demo — the product's whole pitch in one
// animated card. Cycles real-looking examples; the clean line wipes in.
const RENAME_DEMO: Array<[string, string]> = [
  ['Breaking.Bad.S05E14.1080p.BluRay.x264-ROVERS.mkv', 'Breaking Bad/Season 05/Breaking Bad - S05E14 - Ozymandias.mkv'],
  ['[SubsPlease] Sousou no Frieren - 28 (1080p) [F02B9CEE].mkv', 'Frieren/Season 02/Frieren - S02E28 - Petit Frieren.mkv'],
  ['The.Matrix.1999.2160p.UHD.REMUX.HDR-FraMeSToR.mkv', 'The Matrix (1999)/The Matrix (1999) [2160p].mkv'],
];

function RenameDemo() {
  const [i, setI] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setI(x => (x + 1) % RENAME_DEMO.length), 3800);
    return () => clearInterval(t);
  }, []);
  const [messy, clean] = RENAME_DEMO[i];
  return (
    <div className="onb-demo" aria-hidden>
      <AnimatePresence mode="wait" initial={false}>
        <motion.div
          key={i}
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -12 }}
          transition={{ duration: 0.32, ease: [0.16, 1, 0.3, 1] }}
        >
          <div className="dl"><span className="messy">{messy}</span></div>
          <div className="dl">
            <span className="arrow"><IcArrowRight /></span>
            <motion.span
              className="clean"
              initial={{ clipPath: 'inset(0 100% 0 0)' }}
              animate={{ clipPath: 'inset(0 0% 0 0)' }}
              transition={{ delay: 0.3, duration: 0.75, ease: 'easeOut' }}
            >{clean}</motion.span>
          </div>
        </motion.div>
      </AnimatePresence>
    </div>
  );
}

function WelcomeStep() {
  return (
    <div className="onb-hero">
      <div className="mark" style={idx(0)}><IcLogoMark /></div>
      <h1 style={idx(1)}>Welcome to <span className="grad text-shimmer">Kira</span></h1>
      <div className="tag" style={idx(2)}>Rename, organize, done.</div>
      <div style={idx(3)}><RenameDemo /></div>
      <div className="bullets" style={idx(4)}>
        <div><IcScan /><span>Scans your library for messy filenames</span></div>
        <div><IcSparkles /><span>Matches against TMDB, TVDB &amp; AniDB</span></div>
        <div><IcShieldCheck /><span>Nothing renamed without your approval</span></div>
        <div><IcUndo /><span>Every rename is one click to undo</span></div>
        <div><IcLink /><span>Hardlinks keep disk usage flat</span></div>
      </div>
    </div>
  );
}

// ─── Step 1 · Library (content types) ────────────────────────────────

function ContentTypeStep({ types, setTypes }: { types: ContentTypes; setTypes: (t: ContentTypes) => void }) {
  const items: { key: keyof ContentTypes; icon: ReactNode; label: string; desc: string; ex: string; color: string; soon?: boolean }[] = [
    { key: 'movies', icon: <IcFilm />,  label: 'Movies',
      desc: 'Film files in any common format.',
      ex: 'Dune · Past Lives · Oppenheimer', color: 'var(--ink-2)' },
    { key: 'tv',     icon: <IcTv />,    label: 'TV Shows',
      desc: 'Series with seasons and episodes.',
      ex: 'Severance · The Bear · Shōgun', color: 'var(--info)' },
    { key: 'anime',  icon: <IcAnime />, label: 'Anime',
      desc: 'Cour-aware matching, absolute numbering, franchises.',
      ex: 'Frieren · Jujutsu Kaisen · Spy × Family', color: 'var(--media-anime)' },
    { key: 'music',  icon: <IcMusic />, label: 'Music',
      desc: 'Albums & tracks via MusicBrainz — tags, cover art, tidy folders.',
      ex: 'Radiohead · Pink Floyd · Kendrick Lamar', color: 'var(--media-music)' },
  ];
  return (
    <>
      <div className="onb-eyebrow" style={idx(0)}>
        <span className="step-n">Step 1 of 6</span>
        <span>Required</span>
      </div>
      <div className="onb-title" style={idx(1)}>What's in your library?</div>
      <div className="onb-sub" style={idx(2)}>
        Pick all that apply — it tunes the matching defaults and the first-scan summary.
      </div>

      <div className="ct-grid" style={idx(3)} role="group" aria-label="Content types">
        {items.map(it => {
          const on = !it.soon && !!types[it.key];
          return (
            <button key={it.key}
                 type="button"
                 role="checkbox"
                 aria-checked={on}
                 aria-disabled={it.soon || undefined}
                 aria-label={`${it.label} — ${it.desc}`}
                 className={`ct-card ${on ? 'selected' : ''} ${it.soon ? 'soon' : ''}`}
                 onClick={() => { if (!it.soon) setTypes({ ...types, [it.key]: !on }); }}>
              <div className="ct-card-head">
                <div className="ct-icon" style={{ color: it.color }}>
                  <span style={{ display: 'inline-flex', width: 18, height: 18 }}>{it.icon}</span>
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="ct-name">
                    {it.label}
                    {it.soon ? <span className="ct-soon">Coming soon</span> : null}
                  </div>
                  <div className="ct-desc">{it.desc}</div>
                </div>
                <div className={`ct-check ${on ? 'on' : ''}`}>{on ? <IcCheck /> : null}</div>
              </div>
              <div className="ct-ex">{it.ex}</div>
            </button>
          );
        })}
      </div>

      {Object.values(types).some(Boolean) ? (
        <div className="onb-hint" style={idx(4)}>
          Movies + TV covers most libraries — everything stays switchable in Settings.
        </div>
      ) : (
        <div className="onb-state error" style={idx(4)} role="alert">
          <IcAlertTri /><span>Pick at least one content type to continue.</span>
        </div>
      )}
    </>
  );
}

// ─── Step 2 · Connect (TMDB required, TVDB optional) ─────────────────

function KeyField({ label, required, placeholder, value, setValue, validation, helpHref, helpLabel }: {
  label: string;
  required: boolean;
  placeholder: string;
  value: string;
  setValue: (v: string) => void;
  validation: ValidationState;
  helpHref: string;
  helpLabel: string;
}) {
  const state = validation.state;
  const errId = `onb-key-err-${label.replace(/\W+/g, '-').toLowerCase()}`;
  return (
    <div className="onb-keyfield">
      <div className="head">
        <span className="lbl">{label}</span>
        <span className={`req ${required ? '' : 'opt'}`}>{required ? 'Required' : 'Optional'}</span>
        <a href={helpHref} target="_blank" rel="noreferrer">{helpLabel} →</a>
      </div>
      <div className="onb-input-wrap">
        <input
          className="input mono"
          placeholder={placeholder}
          value={value}
          onChange={e => setValue(e.target.value)}
          spellCheck={false}
          aria-label={`${label} API key`}
          aria-invalid={state === 'error'}
          aria-describedby={state === 'error' ? errId : undefined}
        />
        <div className="state">
          {state === 'checking' && <span style={{ color: 'var(--ink-3)' }}><IcSpin /></span>}
          {state === 'success' && <span style={{ color: 'var(--accent)' }}><IcCheck /></span>}
          {state === 'error' && <span style={{ color: 'var(--conf-low)' }}><IcX /></span>}
        </div>
      </div>
      {state === 'success' && (
        <div className="onb-state success">
          <IcCheck />
          <span>
            {validation.state === 'success' && validation.existing
              ? <><b>Already connected</b> — a working key is on file. Paste a new one to replace it.</>
              : <><b>Key verified.</b>{validation.state === 'success' && typeof validation.latencyMs === 'number' ? <> Responded in {validation.latencyMs}&nbsp;ms.</> : null}</>}
          </span>
        </div>
      )}
      {state === 'error' && validation.state === 'error' && (
        <div className="onb-state error" id={errId} role="alert"><IcAlertTri /><span>{validation.error}</span></div>
      )}
      {state === 'checking' && (
        <div className="onb-state checking"><IcSpin /><span>Verifying…</span></div>
      )}
      {state === 'incomplete' && (
        <div className="onb-state checking"><IcKey /><span>Keep typing — a full key is at least 26 characters.</span></div>
      )}
    </div>
  );
}

function ConnectStep({ types, tmdbKey, setTmdbKey, tmdbVal, setTmdbVal, tvdbKey, setTvdbKey, tvdbVal, setTvdbVal }: {
  types: ContentTypes;
  tmdbKey: string; setTmdbKey: (v: string) => void;
  tmdbVal: ValidationState; setTmdbVal: (v: ValidationState) => void;
  tvdbKey: string; setTvdbKey: (v: string) => void;
  tvdbVal: ValidationState; setTvdbVal: (v: ValidationState) => void;
}) {
  const wantsTvdb = !!types.tv || !!types.anime;

  // Whether the BACKEND already has each key (stable across edits) — the
  // basis for restoring the "Already connected" state when the field is
  // cleared. Derived-from-val-state was the M2 dead-end: typing one char
  // dropped val to idle, which recomputed `existing` to false, so deleting
  // back to empty stuck on idle with Continue disabled and no way back.
  const tmdbServerKey = useRef(false);
  const tvdbServerKey = useRef(false);

  // Detect keys that are ALREADY configured on the backend (a wiped
  // localStorage re-runs onboarding, but the server still has the keys —
  // forcing the user to dig them out again was hostile).
  useEffect(() => {
    let cancelled = false;
    void api.getSettings().then(s => {
      if (cancelled) return;
      const has = (k: string) => {
        const v = s[k];
        return (typeof v === 'object' && v !== null && (v as { set?: boolean }).set === true)
          || (typeof v === 'string' && v.length > 0);
      };
      tmdbServerKey.current = has('providers.tmdb.api_key');
      tvdbServerKey.current = has('providers.tvdb.api_key');
      if (tmdbVal.state === 'idle' && !tmdbKey && tmdbServerKey.current) {
        setTmdbVal({ state: 'success', existing: true });
      }
      if (tvdbVal.state === 'idle' && !tvdbKey && tvdbServerKey.current) {
        setTvdbVal({ state: 'success', existing: true });
      }
    }).catch(() => { /* backend offline — typing still validates */ });
    return () => { cancelled = true; };
    // mount-only by design
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Debounced validation — only fires once the key LOOKS complete (≥ 26
  // chars). The candidate key is TESTED, never persisted here (see
  // validateProviderKey), so a bad key can't clobber a working/bundled one.
  const useKeyValidation = (key: string, provider: 'tmdb' | 'tvdb',
                            setVal: (v: ValidationState) => void, hasServerKey: () => boolean) => {
    useEffect(() => {
      let cancelled = false;              // M4: ignore a stale in-flight result
      const k = (key || '').trim();
      if (!k) {
        // Empty field: restore the "Already connected" state if the server
        // has a key (M2 recovery), else plain idle.
        setVal(hasServerKey() ? { state: 'success', existing: true } : { state: 'idle' });
        return;
      }
      if (k.length < 26) { setVal({ state: 'incomplete' }); return; }  // M3: real feedback
      setVal({ state: 'checking' });
      const handle = setTimeout(async () => {
        const result = await validateProviderKey(provider, provider === 'tmdb' ? 'providers.tmdb.api_key' : 'providers.tvdb.api_key', k);
        if (cancelled) return;            // a newer edit superseded this run
        setVal(result.ok
          ? { state: 'success', latencyMs: result.latencyMs }
          : { state: 'error', error: result.error });
      }, 400);
      return () => { cancelled = true; clearTimeout(handle); };
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [key]);
  };
  useKeyValidation(tmdbKey, 'tmdb', setTmdbVal, () => tmdbServerKey.current);
  useKeyValidation(tvdbKey, 'tvdb', setTvdbVal, () => tvdbServerKey.current);

  return (
    <>
      <div className="onb-eyebrow" style={idx(0)}>
        <span className="step-n">Step 2 of 6</span>
        <span>{types.movies ? 'Required' : 'Optional'}</span>
      </div>
      <div className="onb-title" style={idx(1)}>Connect your metadata</div>
      <div className="onb-sub" style={idx(2)}>
        Free API keys power the matching — titles, posters, episode lists.
        Keys never leave this server.
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 18, marginTop: 4, ...idx(3) }}>
        <KeyField
          label="TMDB"
          // Only required when Movies is selected — TV/anime are covered by the
          // bundled TVDB key + keyless AniDB.
          required={!!types.movies}
          placeholder="Paste your TMDB API key…"
          value={tmdbKey}
          setValue={setTmdbKey}
          validation={tmdbVal}
          helpHref="https://www.themoviedb.org/settings/api"
          helpLabel="Get a key"
        />
        {wantsTvdb ? (
          <KeyField
            label="TVDB (optional override)"
            required={false}
            placeholder="Preconfigured — paste a personal v4 key to override…"
            value={tvdbKey}
            setValue={setTvdbKey}
            validation={tvdbVal}
            helpHref="https://thetvdb.com/api-information"
            helpLabel="Get a key"
          />
        ) : null}
      </div>

      {!types.movies && (
        <div className="onb-hint" style={idx(4)}>
          TMDB is only needed for movies — you can skip it for a TV/anime-only library and add it later.
        </div>
      )}
      {wantsTvdb ? (
        <div className="onb-hint" style={idx(4)}>
          TV &amp; anime work out of the box — Kira ships a TVDB key{types.anime ? ' and AniDB needs no key' : ''}. Paste your own TVDB key only to use your personal quota.
        </div>
      ) : null}

      {/* ffmpeg check — extras like embedded-subtitle extraction need it.
          Docker ships it (the row just shows Ready); on bare installs the
          one-click button has Kira install its own copy, nothing else needed. */}
      <div style={{ marginTop: 14, ...idx(5) }}>
        <FfmpegStatusRow compact framed />
      </div>
    </>
  );
}

// ─── Step 3 · Folder ─────────────────────────────────────────────────

function MediaFolderStep({ folder, setFolder, watchFolder, setWatchFolder, folderErr, clearFolderErr }: {
  folder: string; setFolder: (s: string) => void;
  watchFolder: boolean; setWatchFolder: (v: boolean) => void;
  folderErr: string | null; clearFolderErr: () => void;
}) {
  const [pickerOpen, setPickerOpen] = useState(false);
  return (
    <>
      <div className="onb-eyebrow" style={idx(0)}>
        <span className="step-n">Step 3 of 6</span>
        <span>Required</span>
      </div>
      <div className="onb-title" style={idx(1)}>Where's your media?</div>
      <div className="onb-sub" style={idx(2)}>
        Kira scans this folder for video files. The first scan tells you exactly
        how many movies / TV / anime it found — nothing is touched yet.
      </div>

      <div className="onb-folder-card" style={idx(3)}>
        <div className="icon"><IcFolder /></div>
        <div className="info">
          <div className="path">{folder || '(no folder selected)'}</div>
          <div className="meta">Click Browse to pick a different folder</div>
        </div>
        <button className="btn btn-sm" onClick={() => setPickerOpen(true)}>
          <IcFolder /> Browse…
        </button>
      </div>

      <label className="onb-checkrow" style={idx(4)}>
        <input
          type="checkbox"
          checked={watchFolder}
          onChange={e => setWatchFolder(e.target.checked)}
          style={{ accentColor: 'var(--accent)' }}
        />
        <span>
          Watch this folder and scan automatically when new files appear
          <span className="sub">Turns on the auto-scan daemon — new downloads land in Review without clicking Scan.</span>
        </span>
      </label>

      {folderErr ? (
        <div className="onb-state error" style={idx(5)} role="alert"><IcAlertTri /><span>{folderErr}</span></div>
      ) : null}

      {pickerOpen ? (
        <FolderPickerModal
          initialPath={folder}
          onPick={(p) => { setFolder(p); clearFolderErr(); setPickerOpen(false); }}
          onClose={() => setPickerOpen(false)}
        />
      ) : null}
    </>
  );
}

// ─── Step 4 · Handling (file operation + rename mode) ────────────────

function HandlingStep({ op, setOp, mode, setMode }: {
  op: string; setOp: (v: string) => void;
  mode: string; setMode: (v: string) => void;
}) {
  const ops = [
    { key: 'hardlink', label: 'Hardlink', rec: true,
      desc: 'Renamed copy appears in the library; the original stays put. Zero extra disk — seeding keeps working.' },
    { key: 'move', label: 'Move',
      desc: 'Relocate the file itself. The source folder is cleaned up afterwards.' },
    { key: 'copy', label: 'Copy',
      desc: 'Duplicate into the library. Safest, but uses double the space.' },
    { key: 'symlink', label: 'Symlink',
      desc: 'A pointer at the original. Light, but breaks if the source moves.' },
  ];
  const modes = [
    { key: 'in-place', label: 'In place',
      desc: 'Files stay in their current folders — only the names change.' },
    { key: 'move-to-library', label: 'Into the library',
      desc: 'Build a fresh, fully organized tree under your media root.' },
  ];
  return (
    <>
      <div className="onb-eyebrow" style={idx(0)}>
        <span className="step-n">Step 4 of 6</span>
        <span>Optional</span>
      </div>
      <div className="onb-title" style={idx(1)}>How should files be placed?</div>
      <div className="onb-sub" style={idx(2)}>
        What happens to the original when Kira lands the renamed copy. Hardlink is
        the safe default — switchable per-batch later.
      </div>

      <div className="ct-grid" style={idx(3)} role="radiogroup" aria-label="File operation">
        {ops.map(o => (
          <button key={o.key} type="button" role="radio" aria-checked={op === o.key}
                  className={`ct-card ${op === o.key ? 'selected' : ''}`}
                  onClick={() => setOp(o.key)}>
            <div className="ct-card-head">
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="ct-name">
                  {o.label}
                  {o.rec ? <span className="ct-soon rec">Recommended</span> : null}
                </div>
                <div className="ct-desc">{o.desc}</div>
              </div>
              <div className={`ct-check ${op === o.key ? 'on' : ''}`}>{op === o.key ? <IcCheck /> : null}</div>
            </div>
          </button>
        ))}
      </div>

      <div className="onb-naming" style={idx(4)} role="radiogroup" aria-label="Rename mode">
        {modes.map(m => (
          <button key={m.key} type="button" role="radio" aria-checked={mode === m.key}
                  className={`naming-card ${mode === m.key ? 'selected' : ''}`}
                  onClick={() => setMode(m.key)}>
            <div className="naming-card-head">
              <div>
                <div className="naming-card-name">{m.label}</div>
                <div className="text-xs text-muted">{m.desc}</div>
              </div>
              <div className="naming-card-check">{mode === m.key ? <IcCheck /> : null}</div>
            </div>
          </button>
        ))}
      </div>
    </>
  );
}

// ─── Step 5 · Naming ─────────────────────────────────────────────────

function NamingStep({ profile, setProfile }: { profile: string; setProfile: (p: string) => void }) {
  const profiles = [
    {
      key: 'Plex',
      name: 'Plex',
      desc: 'The de-facto standard for most setups.',
      tree: (
        <>
          <div className="dir">Movies/</div>
          <div className="dir">  Oppenheimer (2023)/</div>
          <div className="accent-line">    Oppenheimer (2023) [2160p UHD].mkv</div>
          <div className="dir">TV/</div>
          <div className="dir">  The Bear (2022)/</div>
          <div className="dir">    Season 03/</div>
          <div className="accent-line">      The Bear - S03E01 - Tomorrow.mkv</div>
        </>
      ),
    },
    {
      key: 'Jellyfin',
      name: 'Jellyfin',
      desc: 'Cleaner names, lighter on tags.',
      tree: (
        <>
          <div className="dir">Movies/</div>
          <div className="dir">  Oppenheimer (2023)/</div>
          <div className="accent-line">    Oppenheimer (2023).mkv</div>
          <div className="dir">TV/</div>
          <div className="dir">  The Bear (2022)/</div>
          <div className="dir">    Season 03/</div>
          <div className="accent-line">      The Bear (2022) - S03E01 - Tomorrow.mkv</div>
        </>
      ),
    },
  ];

  return (
    <>
      <div className="onb-eyebrow" style={idx(0)}>
        <span className="step-n">Step 5 of 6</span>
        <span>Optional</span>
      </div>
      <div className="onb-title" style={idx(1)}>Pick a naming style</div>
      <div className="onb-sub" style={idx(2)}>
        Files get renamed into folders your media server understands. Change it anytime —
        including fully custom templates.
      </div>

      <div className="onb-naming" style={idx(3)} role="radiogroup" aria-label="Naming style">
        {profiles.map(p => (
          <button
            key={p.key}
            type="button"
            role="radio"
            aria-checked={profile === p.key}
            aria-label={`${p.name} — ${p.desc}`}
            className={`naming-card ${profile === p.key ? 'selected' : ''}`}
            onClick={() => setProfile(p.key)}
          >
            <div className="naming-card-head">
              <div>
                <div className="naming-card-name">{p.name}</div>
                <div className="text-xs text-muted">{p.desc}</div>
              </div>
              <div className="naming-card-check">{profile === p.key ? <IcCheck /> : null}</div>
            </div>
            <div className="naming-card-tree">{p.tree}</div>
          </button>
        ))}
      </div>
    </>
  );
}

// ─── Step 5 · Launch ─────────────────────────────────────────────────

function ReadyStep({ data, gotoStep }: {
  data: { apiKey: string; tmdbExisting: boolean; folder: string; profile: string; contentTypes: ContentTypes; watchFolder: boolean; fileOp: string; renameMode: string };
  gotoStep: (n: number) => void;
}) {
  return (
    <>
      <div className="onb-eyebrow" style={idx(0)}>
        <span className="step-n">Step 6 of 6</span>
        <span>Ready</span>
      </div>
      <div className="onb-title" style={idx(1)}>You're all set.</div>
      <div className="onb-sub" style={idx(2)}>
        Kira runs its first scan when you hit start — every match waits for your
        approval before anything is renamed.
      </div>

      <div className="onb-summary" style={idx(3)}>
        <div className="row">
          <div className="icon"><IcSparkles /></div>
          <div>
            <div className="lbl">Library</div>
            <div className="val" style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {Object.entries(data.contentTypes).filter(([, v]) => v).map(([k]) => (
                <span key={k} className="chip">
                  {k === 'tv' ? 'TV Shows' : k.charAt(0).toUpperCase() + k.slice(1)}
                </span>
              ))}
            </div>
          </div>
          <button className="edit" onClick={() => gotoStep(1)}>Edit</button>
        </div>
        <div className="row">
          <div className="icon"><IcKey /></div>
          <div>
            <div className="lbl">Metadata</div>
            <div className="val" style={{ color: 'var(--accent)' }}>
              {data.tmdbExisting && !data.apiKey
                ? 'TMDB connected · existing key'
                : <>TMDB connected · key ending {data.apiKey.slice(-4)}</>}
            </div>
          </div>
          <button className="edit" onClick={() => gotoStep(2)}>Edit</button>
        </div>
        <div className="row">
          <div className="icon"><IcFolder /></div>
          <div>
            <div className="lbl">Media folder</div>
            <div className="val">
              {data.folder}
              {data.watchFolder ? <span className="chip" style={{ marginLeft: 8 }}>auto-scan on</span> : null}
            </div>
          </div>
          <button className="edit" onClick={() => gotoStep(3)}>Edit</button>
        </div>
        <div className="row">
          <div className="icon"><IcLink /></div>
          <div>
            <div className="lbl">File handling</div>
            <div className="val" style={{ textTransform: 'capitalize' }}>
              {data.fileOp} · {data.renameMode === 'in-place' ? 'in place' : 'into the library'}
            </div>
          </div>
          <button className="edit" onClick={() => gotoStep(4)}>Edit</button>
        </div>
        <div className="row">
          <div className="icon"><IcTag /></div>
          <div>
            <div className="lbl">Naming profile</div>
            <div className="val">{data.profile || 'Plex (default) — customize later'}</div>
          </div>
          <button className="edit" onClick={() => gotoStep(5)}>Edit</button>
        </div>
      </div>
    </>
  );
}

// ─── Shell ───────────────────────────────────────────────────────────

const RAIL_STEPS = [
  { n: 1, label: 'Library',  hint: 'What you have' },
  { n: 2, label: 'Connect',  hint: 'Metadata keys' },
  { n: 3, label: 'Folder',   hint: 'Where it lives' },
  { n: 4, label: 'Handling', hint: 'Move or link' },
  { n: 5, label: 'Naming',   hint: 'How it looks' },
  { n: 6, label: 'Launch',   hint: 'First scan' },
];

export function Onboarding({ onComplete }: OnboardingProps) {
  // 0=Welcome · 1=Library · 2=Connect · 3=Folder · 4=Handling · 5=Naming · 6=Launch
  const [step, setStep] = useState(0);
  const [contentTypes, setContentTypes] = useState<ContentTypes>({ movies: true, tv: true, anime: false, music: false });
  const [tmdbKey, setTmdbKey] = useState('');
  const [tmdbVal, setTmdbVal] = useState<ValidationState>({ state: 'idle' });
  const [tvdbKey, setTvdbKey] = useState('');
  const [tvdbVal, setTvdbVal] = useState<ValidationState>({ state: 'idle' });
  const [folder, setFolder] = useState('/media');
  const [watchFolder, setWatchFolder] = useState(true);
  const [profile, setProfile] = useState('Plex');
  const [fileOp, setFileOp] = useState('hardlink');
  const [renameMode, setRenameMode] = useState('in-place');
  const [version, setVersion] = useState<string | null>(null);
  // Final-save state: without this, a failed complete() used to be swallowed
  // and the wizard dismissed anyway — the first scan then ran against an
  // unsaved root and (since onboarding.completed never persisted) the whole
  // wizard reappeared on refresh with every choice lost.
  const [completing, setCompleting] = useState(false);
  const [completeError, setCompleteError] = useState<string | null>(null);
  // When the user clicks "Edit" on the Launch summary, remember to bounce them
  // straight BACK to the summary after they change that one thing — instead of
  // force-walking every subsequent step again.
  const [returnToSummary, setReturnToSummary] = useState(false);
  // Step-3 folder existence check (async, on Continue).
  const [folderErr, setFolderErr] = useState<string | null>(null);
  const [checkingFolder, setCheckingFolder] = useState(false);
  // Existing per-folder watch config, preserved across a re-run so completing
  // onboarding doesn't wipe it (we only rewrite the top-level auto_scan flag).
  const [existingWatchFolders, setExistingWatchFolders] = useState<Record<string, unknown>>({});
  // The app renders (blurred) behind this overlay — it must not scroll under it.
  useScrollLock();

  useEffect(() => {
    let cancelled = false;
    void api.health().then(h => { if (!cancelled && h.version) setVersion(h.version); }).catch(() => {});
    // Re-run protection: a wiped localStorage restarts onboarding while the
    // BACKEND still has real settings. Prefill folder + profile from the
    // saved values so completing the flow can't clobber a configured library
    // root with the '/media' default.
    void api.getSettings().then(st => {
      if (cancelled) return;
      // Settings can be stored bare OR wrapped as {value: "..."} depending on
      // which write path produced them — unwrap both, or a wrapped library
      // root prefilled as nothing and complete() overwrote it with '/media'.
      const unwrap = (v: unknown): string => {
        if (typeof v === 'string') return v;
        if (v && typeof v === 'object' && 'value' in v && typeof (v as { value?: unknown }).value === 'string') {
          return (v as { value: string }).value;
        }
        return '';
      };
      const root = unwrap(st['paths.library_root']);
      if (root.trim()) setFolder(root);
      const prof = unwrap(st['naming.profile']);
      if (prof.trim()) setProfile(prof);
      const op = unwrap(st['rename.default_op']);
      if (op.trim()) setFileOp(op);
      const mode = unwrap(st['rename.mode']);
      if (mode.trim()) setRenameMode(mode);
      // Preserve existing per-folder watch config + reflect its auto_scan
      // state in the checkbox on a re-run.
      const wc = st['watch.config'];
      if (wc && typeof wc === 'object') {
        const w = wc as { auto_scan?: unknown; folders?: unknown };
        if (typeof w.auto_scan === 'boolean') setWatchFolder(w.auto_scan);
        if (w.folders && typeof w.folders === 'object') setExistingWatchFolders(w.folders as Record<string, unknown>);
      }
    }).catch(() => {});
    return () => { cancelled = true; };
  }, []);

  const anyContentSelected = Object.values(contentTypes).some(Boolean);

  const canContinue = () => {
    if (step === 1) return anyContentSelected;
    // TMDB is only REQUIRED for movies — TV runs on the bundled TVDB key and
    // anime on keyless AniDB, so a TV/anime-only library shouldn't be blocked
    // waiting on a TMDB key it doesn't need. (TMDB has no bundled key, so it's
    // still required when Movies is selected.)
    if (step === 2) return contentTypes.movies ? tmdbVal.state === 'success' : true;
    if (step === 3) return folder.length > 0;
    return true;
  };
  const isOptional = step === 4 || step === 5;

  // Validate the chosen media folder exists before leaving step 3 — the
  // default '/media' (and any typed path) can point at nothing on bare metal,
  // which used to sail through and make the first scan find zero files.
  const validateFolder = async (): Promise<boolean> => {
    setFolderErr(null);
    if (!folder.trim()) { setFolderErr('Pick a media folder first.'); return false; }
    setCheckingFolder(true);
    try {
      await api.listFolders(folder);   // 404s / errors if the path doesn't exist
      return true;
    } catch (e) {
      const msg = (e as Error).message || '';
      setFolderErr(/not exist|no such|404/i.test(msg)
        ? `That folder doesn’t exist on the server: ${folder}`
        : `Couldn’t reach that folder: ${msg}`);
      return false;
    } finally {
      setCheckingFolder(false);
    }
  };

  // Direction of travel for the step transition (1 = forward, -1 = back) —
  // read by the AnimatePresence variants so content slides the way you're going.
  const dirRef = useRef(1);

  const advance = () => {
    // Honor an in-progress "Edit from summary": one change, straight back to
    // the Launch summary (step 6).
    if (returnToSummary) { setReturnToSummary(false); setStep(6); return; }
    if (step < 6) setStep(step + 1); else void complete();
  };
  const next = () => {
    dirRef.current = 1;
    if (step === 3) { void validateFolder().then(ok => { if (ok) advance(); }); return; }
    advance();
  };
  const skip = () => {
    dirRef.current = 1;
    // "Skip — set up later" must NOT reset choices to defaults: on a re-run
    // over a configured server the prefill loaded the real values, and
    // clobbering them (profile→'', op→hardlink) rewrote e.g. a Jellyfin
    // server to Plex. Just advance; complete() persists whatever's current.
    if (returnToSummary) { setReturnToSummary(false); setStep(6); return; }
    if (step === 4) setStep(5);
    else if (step === 5) setStep(6);
  };
  const back = () => { if (step > 0) { dirRef.current = -1; setStep(step - 1); } };
  // Edit-from-summary: jump to the step AND arm the return so Continue/Skip
  // brings the user back to the summary rather than re-walking the rest.
  const editFromSummary = (n: number) => { dirRef.current = -1; setReturnToSummary(true); setStep(n); };

  const complete = async () => {
    if (completing) return;              // guard double-fire (the launch button)
    setCompleting(true);
    setCompleteError(null);
    // Persist everything the flow collected. The watch checkbox writes the
    // REAL auto-scan config (watch.config — the settings PUT re-arms the
    // watcher daemon); a previous version wrote a `paths.watch_enabled` key
    // that nothing read, so the checkbox silently did nothing.
    const values: Record<string, unknown> = {
      'paths.library_root': folder,
      'naming.profile': profile || 'Plex',
      'rename.default_op': fileOp,
      'rename.mode': renameMode,
      // Server-side completion flag — a fresh browser on a configured
      // server skips onboarding; a factory-reset server re-enters it.
      'onboarding.completed': true,
    };
    // Provider keys are validated (not saved) during the flow now, so persist
    // the confirmed keys HERE — only the ones the user actually entered, so a
    // blank field can't clobber the bundled/existing key.
    if (tmdbKey.trim()) values['providers.tmdb.api_key'] = tmdbKey.trim();
    if (tvdbKey.trim()) values['providers.tvdb.api_key'] = tvdbKey.trim();
    // Music matching is gated by `music.enabled` (default off), so picking
    // Music in step 1 has to actually turn it on — otherwise the first scan
    // would skip every music file the user just said they have.
    if (contentTypes.music) values['music.enabled'] = true;
    // ALWAYS write watch.config so UNCHECKING the box on a re-run actually
    // turns auto-scan OFF (the watcher re-arms from this). Previously it was
    // only written when checked, so an unchecked box left a prior auto_scan:true
    // config running.
    values['watch.config'] = {
      auto_scan: watchFolder,
      debounce_seconds: 30, poll_interval_seconds: 900,
      folders: existingWatchFolders,   // preserve per-folder config on re-run
    };
    try {
      await api.putSettings(values);
    } catch (e) {
      // Do NOT dismiss the wizard on failure — surface the error and let the
      // user retry. Dismissing here lost every choice and re-showed the
      // wizard on the next refresh (onboarding.completed never landed).
      setCompleteError(friendlyError((e as Error).message) || 'Could not save your setup. Check the connection and try again.');
      setCompleting(false);
      return;
    }
    setOnboarded(true);
    onComplete({ apiKey: tmdbKey, folder, profile: profile || 'Plex', contentTypes });
  };

  const tmdbExisting = tmdbVal.state === 'success' && !!tmdbVal.existing;
  const progress = step === 0 ? 0 : (step / 6) * 100;

  const shellRef = useRef<HTMLDivElement>(null);
  // Focus containment + Enter-to-advance for the modal overlay. The app renders
  // (blurred) behind it, so without a trap Tab could reach invisible controls;
  // Enter on a field should move the wizard forward like a normal form.
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      const tag = (e.target as HTMLElement).tagName;
      // Don't hijack Enter on the actual buttons (they have their own onClick)
      // or textareas; do advance from text inputs / the shell itself.
      if (tag !== 'BUTTON' && tag !== 'TEXTAREA' && step > 0) {
        if (step === 6) { if (!completing) void complete(); }
        else if (canContinue() && !checkingFolder) { e.preventDefault(); next(); }
      }
      return;
    }
    if (e.key !== 'Tab' || !shellRef.current) return;
    const f = shellRef.current.querySelectorAll<HTMLElement>(
      'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])');
    if (f.length === 0) return;
    const first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  };

  return (
    <MotionConfig reducedMotion="user">
    <div className="onboarding-root" role="dialog" aria-modal="true" aria-label="Kira setup"
         ref={shellRef} onKeyDown={onKeyDown}>
      <div className="backdrop" style={{ position: 'absolute' }} />
      <div className="onb-shell">
        {/* ── Left rail: brand + step map ── */}
        <aside className="onb-rail">
          <div className="brand">
            <div className="mark"><IcLogoMark /></div>
            <div>
              <div className="name">Kira</div>
              <div className="sub">Rename, organize, done.</div>
            </div>
          </div>
          <nav className="steps" aria-label="Setup steps">
            {RAIL_STEPS.map(s => {
              const state = step === 0 ? '' : s.n < step ? 'done' : s.n === step ? 'active' : '';
              return (
                <button
                  key={s.n}
                  type="button"
                  className={`step ${state}`}
                  disabled={step === 0 || s.n > step}
                  onClick={() => { if (s.n < step) { dirRef.current = -1; setStep(s.n); } }}
                >
                  {state === 'active' ? (
                    <motion.span
                      layoutId="onb-step-glow"
                      className="onb-step-glow"
                      transition={{ type: 'spring', stiffness: 420, damping: 36 }}
                    />
                  ) : null}
                  <span className="dot">{state === 'done' ? <IcCheck /> : s.n}</span>
                  <span className="txt">
                    <span className="l">{s.label}</span>
                    <span className="h">{s.hint}</span>
                  </span>
                </button>
              );
            })}
          </nav>
          <div className="foot">Self-hosted{version ? ` · v${version}` : ''}</div>
        </aside>

        {/* ── Right pane: step content ── */}
        <section className="onb-pane">
          <div className="onb-progress" aria-hidden>
            <div className="bar" style={{ width: `${progress}%` }} />
          </div>
          <div className="onb-body">
            <AnimatePresence mode="wait" custom={dirRef.current} initial={false}>
            <motion.div
              key={step}
              className="onb-step"
              custom={dirRef.current}
              initial="enter"
              animate="center"
              exit="exit"
              variants={{
                enter: (d: number) => ({ opacity: 0, x: 36 * d, scale: 0.985, filter: 'blur(5px)' }),
                center: {
                  opacity: 1, x: 0, scale: 1, filter: 'blur(0px)',
                  transition: { type: 'spring', stiffness: 320, damping: 30 },
                },
                exit: (d: number) => ({
                  opacity: 0, x: -30 * d, scale: 0.985, filter: 'blur(5px)',
                  transition: { duration: 0.16, ease: 'easeIn' },
                }),
              }}
            >
            {step === 0 && <WelcomeStep />}
            {step === 1 && <ContentTypeStep types={contentTypes} setTypes={setContentTypes} />}
            {step === 2 && (
              <ConnectStep
                types={contentTypes}
                tmdbKey={tmdbKey} setTmdbKey={setTmdbKey} tmdbVal={tmdbVal} setTmdbVal={setTmdbVal}
                tvdbKey={tvdbKey} setTvdbKey={setTvdbKey} tvdbVal={tvdbVal} setTvdbVal={setTvdbVal}
              />
            )}
            {step === 3 && (
              <MediaFolderStep folder={folder} setFolder={setFolder} watchFolder={watchFolder} setWatchFolder={setWatchFolder} folderErr={folderErr} clearFolderErr={() => setFolderErr(null)} />
            )}
            {step === 4 && <HandlingStep op={fileOp} setOp={setFileOp} mode={renameMode} setMode={setRenameMode} />}
            {step === 5 && <NamingStep profile={profile} setProfile={setProfile} />}
            {step === 6 && (
              <ReadyStep
                data={{ apiKey: tmdbKey, tmdbExisting, folder, profile, contentTypes, watchFolder, fileOp, renameMode }}
                gotoStep={editFromSummary}
              />
            )}
            </motion.div>
            </AnimatePresence>
          </div>

          <div className="onb-foot">
            {step === 0 ? (
              <>
                <div className="hint">Takes about two minutes</div>
                <button className="btn btn-brand" style={{ padding: '12px 24px', fontSize: 14 }} onClick={next}>
                  Get started <IcArrowRight />
                </button>
              </>
            ) : (
              <>
                <button className="btn btn-ghost" onClick={back} disabled={completing}>← Back</button>
                <div className="right">
                  {completeError && step === 6 && (
                    <span className="onb-save-error" role="alert" style={{ color: 'var(--conf-low, #f87171)', fontSize: 12, marginRight: 8 }}>
                      {completeError}
                    </span>
                  )}
                  {isOptional && <button className="onb-skip" onClick={skip}>Skip — set up later</button>}
                  {step === 6 ? (
                    <button className="btn btn-primary launch" style={{ padding: '11px 22px' }} onClick={() => void complete()} disabled={completing}>
                      {completing ? <><IcSpin /> Saving…</> : completeError ? <><IcScan /> Retry</> : <><IcScan /> Start first scan</>}
                    </button>
                  ) : (
                    <button className="btn btn-primary" disabled={!canContinue() || checkingFolder} onClick={next}>
                      {checkingFolder ? <><IcSpin /> Checking…</> : returnToSummary ? <>Save &amp; back <IcArrowRight /></> : <>Continue <IcArrowRight /></>}
                    </button>
                  )}
                </div>
              </>
            )}
          </div>
        </section>
      </div>
    </div>
    </MotionConfig>
  );
}
