import { useState, useEffect, type ReactNode } from 'react';
import type { ProviderKey, MediaType } from '../lib/types';
import { PROVIDERS, NAMING_PROFILES, NAMING_TOKENS, TYPE_COLOR } from '../lib/data';
import { IcChevDown, IcRefresh, IcAlertTri, IcFilm, IcTv, IcAnime, IcMusic } from '../lib/icons';

type FieldKind = 'text' | 'password' | 'select' | 'toggle';

export interface ProviderFieldProps {
  kind?: FieldKind;
  label: string;
  value?: string;
  placeholder?: string;
  options?: string[];
  mono?: boolean;
  desc?: string;
  onSave?: (next: string | boolean) => void;
}

export function ProviderField({ kind = 'text', label, value, placeholder, options, mono, desc, onSave }: ProviderFieldProps) {
  const [text, setText] = useState(value ?? '');
  const [on, setOn] = useState(value !== 'false');

  // Sync local `text` state when the upstream `value` prop changes.
  // Without this, the API key field stays empty after a fresh page
  // load: the field mounts with rawSettings=[] (the initial empty
  // state), `text` initializes to '', then rawSettings resolves with
  // the saved key but `text` never re-syncs because useState's
  // initializer only fires once. Result: input visually empty while
  // the backend reports "Connected" because the registry CAN see the
  // saved key.
  //
  // Re-sync on every value change UNLESS the user has typed
  // something that differs from value (= they're mid-edit, don't
  // clobber). The "user is editing" heuristic: text !== value AND
  // text !== '' AND there was a previous non-empty value. This
  // preserves typing-in-progress while still picking up server
  // updates.
  useEffect(() => {
    // Always re-sync if the field is currently empty (mount + value
    // arriving late case). If text differs from value and user is
    // actively typing, leave their local state alone.
    if (text === '' && value) {
      setText(value);
    } else if (text === value) {
      // No-op (already in sync).
    }
    // Intentionally ignoring `text` in deps — we only want to react
    // to upstream value changes, not loop on local edits.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  if (kind === 'toggle') {
    return (
      <div className="provider-field">
        <div className="provider-field-label">
          <span>{label}</span>
          {desc ? <span className="provider-field-desc">{desc}</span> : null}
        </div>
        <label className="flex items-center gap-2" style={{ cursor: 'pointer' }}>
          <input
            type="checkbox"
            checked={on}
            onChange={() => { const next = !on; setOn(next); onSave?.(next); }}
            style={{ accentColor: 'var(--accent)', width: 16, height: 16 }}
          />
          <span className="text-sm">{on ? 'Enabled' : 'Disabled'}</span>
        </label>
      </div>
    );
  }
  if (kind === 'select') {
    return (
      <div className="provider-field">
        <div className="provider-field-label"><span>{label}</span></div>
        <select
          className="input"
          defaultValue={value}
          onChange={e => onSave?.(e.target.value)}
        >
          {options?.map(o => <option key={o}>{o}</option>)}
        </select>
      </div>
    );
  }
  return (
    <div className="provider-field">
      <div className="provider-field-label">
        <span>{label}</span>
        {desc ? <span className="provider-field-desc">{desc}</span> : null}
      </div>
      <input
        className={`input ${mono ? 'mono' : ''}`}
        type={kind === 'password' ? 'password' : 'text'}
        value={text}
        onChange={e => setText(e.target.value)}
        onBlur={() => { if (text !== value) onSave?.(text); }}
        placeholder={placeholder}
      />
    </div>
  );
}

interface ProviderBlockProps {
  providerKey: ProviderKey;
  fields?: ProviderFieldProps[];
  defaultOpen?: boolean;
  status?: 'connected' | 'warning' | 'error' | 'disabled' | 'coming-soon' | 'not-configured';
  rateLimit?: string;
  warning?: string;
  onTest?: () => void;
  /** Unix timestamp (seconds) when the provider's ban expires. When set
   *  AND > now, the block renders a live countdown banner. AniDB only. */
  bannedUntil?: number | null;
  /** Provider keys this one falls back to when unavailable. Surfaced as
   *  a tooltip-style hint so the user understands "if AniDB is banned,
   *  TVDB takes over." */
  fallbackChain?: string[] | null;
}

/**
 * Live ban-countdown banner. Renders inside a provider block when the
 * provider is throttled / banned (currently only AniDB has this state).
 * Returns null when the ban has expired so the banner auto-dismisses
 * without a manual refresh.
 */
function BanCountdownBanner({
  unixSec,
  fallbackChain,
}: { unixSec: number; fallbackChain?: string[] | null }) {
  const remaining = useCountdown(unixSec);
  if (!remaining) return null;
  // Reconstruct the local time of expiry for the secondary "at HH:MM"
  // hint — useful when the wait is hours so the user can plan around it.
  const at = new Date(unixSec * 1000);
  const atStr = at.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  const fallback = fallbackChain && fallbackChain.length > 0
    ? `Kira is using ${fallbackChain.map(k => k.toUpperCase()).join(' → ')} in the meantime.`
    : 'New matches against this provider will fail until then.';
  return (
    <div
      role="status"
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        gap: 10,
        padding: '10px 12px',
        marginBottom: 14,
        background: 'rgba(255, 201, 74, 0.10)',
        border: '1px solid rgba(255, 201, 74, 0.32)',
        borderRadius: 8,
        color: 'var(--ink-1)',
        fontSize: 13,
        lineHeight: 1.45,
      }}
    >
      <IcAlertTri style={{ width: 16, height: 16, color: 'var(--conf-mid)', flex: '0 0 auto', marginTop: 2 }} />
      <div style={{ flex: 1 }}>
        <div style={{ fontWeight: 600, marginBottom: 2 }}>
          Rate-limited — unbans in {remaining} (at {atStr})
        </div>
        <div style={{ color: 'var(--ink-3)', fontSize: 12 }}>
          {fallback}
        </div>
      </div>
    </div>
  );
}

function useCountdown(unixSec: number | null | undefined): string | null {
  // Re-renders every 30s while the deadline is in the future.
  const [, tick] = useState(0);
  useEffect(() => {
    if (!unixSec) return;
    const remaining = unixSec * 1000 - Date.now();
    if (remaining <= 0) return;
    const t = setInterval(() => tick(t => t + 1), 30_000);
    return () => clearInterval(t);
  }, [unixSec]);
  if (!unixSec) return null;
  const remainingMs = unixSec * 1000 - Date.now();
  if (remainingMs <= 0) return null;
  const totalMin = Math.ceil(remainingMs / 60_000);
  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

export function ProviderBlock({ providerKey, fields = [], defaultOpen = false, status = 'connected', rateLimit, warning, onTest, bannedUntil, fallbackChain }: ProviderBlockProps) {
  const [open, setOpen] = useState(defaultOpen);
  const p = PROVIDERS[providerKey];
  if (!p) return null;
  // F-06: clearer labels for the discovered states. "Coming soon"
  // distinguishes "we haven't built this yet" from "you need a key";
  // "Not configured" is for implemented providers awaiting credentials.
  const statusLabel =
    status === 'connected' ? 'Connected' :
    status === 'warning' ? 'Rate-limited' :
    status === 'error' ? 'Error' :
    status === 'coming-soon' ? 'Coming soon' :
    status === 'not-configured' ? 'Not configured' : 'Disabled';
  const statusColor =
    status === 'connected' ? 'var(--conf-high)' :
    status === 'warning' ? 'var(--conf-mid)' :
    status === 'error' ? 'var(--conf-low)' :
    status === 'coming-soon' ? 'var(--ink-3)' :
    status === 'not-configured' ? 'var(--ink-3)' : 'var(--ink-3)';

  return (
    <div className={`provider-block ${open ? 'open' : ''}`}>
      <button className="provider-block-head" onClick={() => setOpen(!open)}>
        <span className="provider-dot" style={{ background: p.color }} />
        <div style={{ flex: 1, minWidth: 0, textAlign: 'left' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span className="provider-block-name">{p.name}</span>
            <span className="provider-block-for">for {p.for.join(' · ')}</span>
          </div>
          <div className="provider-block-desc">{p.desc}</div>
        </div>
        <span className="status-pill"><span className="swatch" style={{ background: statusColor }} />{statusLabel}</span>
        <span className={`provider-chev ${open ? 'open' : ''}`}><IcChevDown /></span>
      </button>
      {open ? (
        <div className="provider-block-body">
          {/* Ban countdown banner — only renders when bannedUntil is
              set AND in the future. Auto-disappears when the ban
              expires (countdown component returns null). */}
          {bannedUntil ? <BanCountdownBanner unixSec={bannedUntil} fallbackChain={fallbackChain} /> : null}
          {warning ? (
            <div className="onboarding-state error" style={{ marginBottom: 14, lineHeight: 1.5 }}>
              <IcAlertTri /><span>{warning}</span>
            </div>
          ) : null}
          {/* Fallback chain hint — always shown when defined, even
              when the provider is up. Lets the user see "if AniDB
              breaks, we try TVDB then TMDB" without hunting through
              docs. */}
          {fallbackChain && fallbackChain.length > 0 ? (
            <div style={{
              fontSize: 12, color: 'var(--ink-3)', marginBottom: 14,
              padding: '8px 10px', background: 'rgba(255,255,255,0.03)',
              borderRadius: 6, border: '1px solid var(--line)',
            }}>
              <strong style={{ color: 'var(--ink-2)' }}>Fallback chain:</strong>{' '}
              if this provider is unavailable, Kira tries{' '}
              {fallbackChain.map((k, i) => (
                <span key={k}>
                  {i > 0 ? ' → ' : ''}
                  <span style={{ color: 'var(--ink-2)' }}>{k.toUpperCase()}</span>
                </span>
              ))}
              {' '}in order.
            </div>
          ) : null}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            {fields.map((f, i) => <ProviderField key={i} {...f} />)}
          </div>
          <div className="provider-block-foot">
            {rateLimit ? <span className="text-xs text-muted">{rateLimit}</span> : <span />}
            <button className="btn btn-sm" onClick={onTest}><IcRefresh /> Test connection</button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

// 4-tab naming template editor (Movie / TV / Anime / Music)
export function NamingTemplateTabs({ profile }: { profile: string }) {
  const tabs: { key: MediaType; label: string; icon: ReactNode }[] = [
    { key: 'movie', label: 'Movies', icon: <IcFilm style={{ width: 13, height: 13 }} /> },
    { key: 'tv',    label: 'TV',     icon: <IcTv style={{ width: 13, height: 13 }} /> },
    { key: 'anime', label: 'Anime',  icon: <IcAnime style={{ width: 13, height: 13 }} /> },
    { key: 'music', label: 'Music',  icon: <IcMusic style={{ width: 13, height: 13 }} /> },
  ];
  const [tab, setTab] = useState<MediaType>('movie');
  const tpl = NAMING_PROFILES[profile][tab];
  const tokens = NAMING_TOKENS[tab] || [];
  return (
    <div>
      <div className="provider-tabs" style={{ marginBottom: 14 }}>
        {tabs.map(t => (
          <button key={t.key} className={`provider-tab ${tab === t.key ? 'on' : ''}`} onClick={() => setTab(t.key)}>
            <span style={{ display: 'inline-flex', alignItems: 'center', width: 13, height: 13, color: tab === t.key ? TYPE_COLOR[t.key] : 'var(--ink-3)' }}>
              {t.icon}
            </span>
            {t.label}
          </button>
        ))}
      </div>

      <div className="text-xs text-muted font-medium" style={{ textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 6 }}>
        {tabs.find(t => t.key === tab)!.label} template
      </div>
      <input className="input mono" value={tpl} readOnly={profile !== 'Custom'} onChange={() => { /* prototype */ }} />

      <div className="text-xs text-muted" style={{ marginTop: 14, marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.06em', fontWeight: 600 }}>
        Tokens available for {tab}
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {tokens.map(t => (
          <span key={t.k} className="token-chip">
            <span className="kbd" style={{ margin: 0 }}>{t.k}</span>
            <span style={{ color: 'var(--ink-3)', fontSize: 11 }}>{t.d}</span>
          </span>
        ))}
      </div>

      <div style={{ marginTop: 14 }}>
        <div className="text-xs text-muted font-medium" style={{ textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 6 }}>
          Preview
        </div>
        <div className="preview-side new" style={{ padding: '10px 12px' }}>
          <div className="preview-path">{namingPreview(tab, tpl)}</div>
        </div>
      </div>
    </div>
  );
}

function namingPreview(type: MediaType, tpl: string) {
  const data: Record<MediaType, Record<string, string | number>> = {
    movie: { n: 'Dune: Part Two', y: 2024, q: '2160p WEB-DL', x: 'mkv' },
    tv:    { n: 'Severance', y: 2022, s2: '02', e2: '07', t: 'Chikhai Bardo', q: '2160p WEB-DL', x: 'mkv' },
    anime: { n: "Frieren: Beyond Journey's End", y: 2023, s2: '01', e2: '28', abs: '028', t: 'A Bird Cage of Silver', rg: 'SubsPlease', x: 'mkv' },
    music: { artist: 'Radiohead', album: 'OK Computer', y: 1997, tn: '03', title: 'Subterranean Homesick Alien', x: 'flac' },
  };
  let out = tpl;
  for (const [k, v] of Object.entries(data[type] || {})) {
    out = out.replace(new RegExp('\\{' + k + '\\}', 'g'), String(v));
  }
  const root = '/media/library/' + ({ movie: 'Movies', tv: 'TV', anime: 'Anime', music: 'Music' } as const)[type];
  const parts = out.split('/');
  const last = parts.pop();
  const dir = root + (parts.length ? '/' + parts.join('/') : '');
  return (
    <>
      <span className="seg-dir">{dir}/</span>
      <span className="seg-new">{last}</span>
    </>
  );
}
