import { useState, useEffect, useRef, type ReactNode, type FC } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import type { ProviderKey, MediaType } from '../lib/types';
import { PROVIDERS, TYPE_COLOR } from '../lib/data';
import { api } from '../lib/api';
import { IcChevDown, IcRefresh, IcAlertTri, IcFilm, IcTv, IcAnime, IcMusic, IcDisc, IcWaveform, IcEye, IcEyeOff } from '../lib/icons';
import { cn } from '../lib/utils';
import { Button } from './base/buttons/button';
import { FeaturedIcon } from './base/featured-icons/featured-icon';
import { BadgeWithDot, Badge } from './base/badges/badges';
import { Input } from './base/input/input';
import { Alert } from './base/alert/alert';
import { Toggle } from './base/toggle/toggle';
import { Select } from './ui';

// ── Shared settings surface styles ──────────────────────────────────
// One source of truth so every Settings section (Connections, Paths,
// Integrations, …) uses the exact same card + nested-box treatment.
export const SETTINGS_CARD = 'rounded-2xl border border-white/[0.12] bg-white/[0.045] shadow-[0_1px_3px_rgba(0,0,0,0.35)]';
export const SETTINGS_NESTED = 'rounded-xl border border-white/[0.1] bg-white/[0.07]';
export const SETTINGS_DIVIDER = 'border-white/[0.1]';

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
  const [show, setShow] = useState(false);

  // Re-sync local `text` when the upstream `value` arrives late (the field
  // mounts before rawSettings hydrates). Only adopt the server value while the
  // field is empty so we never clobber an in-progress edit.
  useEffect(() => {
    if (text === '' && value) setText(value);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  const labelBlock = (
    <div>
      <div className="text-[13px] font-medium text-ink">{label}</div>
      {desc ? <div className="mt-0.5 text-[11.5px] leading-relaxed text-ink-soft">{desc}</div> : null}
    </div>
  );

  if (kind === 'toggle') {
    return (
      <div className="flex items-center justify-between gap-4">
        {labelBlock}
        <Toggle isSelected={on} onChange={() => { const next = !on; setOn(next); onSave?.(next); }} aria-label={label} />
      </div>
    );
  }

  if (kind === 'select') {
    return (
      <div>
        <div className="mb-1.5">{labelBlock}</div>
        <Select<string>
          value={value ?? null}
          onChange={v => onSave?.(v)}
          options={(options ?? []).map(o => ({ value: o, label: o }))}
          placeholder={placeholder}
        />
      </div>
    );
  }

  const isPassword = kind === 'password';
  return (
    <div>
      <div className="mb-1.5">{labelBlock}</div>
      <Input
        mono={mono}
        type={isPassword && !show ? 'password' : 'text'}
        value={text}
        onChange={e => setText(e.target.value)}
        onBlur={() => { if (text !== value) onSave?.(text); }}
        placeholder={placeholder}
        autoComplete={isPassword ? 'off' : undefined}
        trailing={isPassword ? (
          <button
            type="button"
            onClick={() => setShow(s => !s)}
            title={show ? 'Hide' : 'Show'}
            aria-label={show ? 'Hide value' : 'Show value'}
            className="grid size-6 shrink-0 place-items-center rounded-md text-ink-soft transition-colors hover:bg-glass-2 hover:text-ink [&_svg]:size-[14px]"
          >
            {show ? <IcEyeOff /> : <IcEye />}
          </button>
        ) : undefined}
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
  onTest?: () => void | Promise<void>;
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
    <Alert
      color="warning"
      icon={IcAlertTri}
      title={`Rate-limited — unbans in ${remaining} (at ${atStr})`}
    >
      {fallback}
    </Alert>
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

// Map a provider's icon slug (from PROVIDERS metadata) to an icon component.
const PROVIDER_ICON: Record<string, FC<{ className?: string }>> = {
  film: IcFilm, tv: IcTv, anime: IcAnime, disc: IcDisc, waveform: IcWaveform,
};

// Provider avatar: tries the official logo at /providers/<slug>.svg and falls
// back to the tinted media-type icon when that file is missing. Drop brand
// SVGs into frontend/public/providers/ to light these up.
function ProviderLogo({ slug, color, icon: Icon }: { slug: string; color: string; icon: FC<{ className?: string }> }) {
  const [failed, setFailed] = useState(false);
  if (failed) return <FeaturedIcon size="md" tint={color} icon={<Icon />} />;
  return (
    <span className="size-9 shrink-0 overflow-hidden rounded-lg bg-white/[0.06]">
      <img
        src={`/providers/${slug}.svg`}
        alt=""
        draggable={false}
        onError={() => setFailed(true)}
        className="size-full object-contain"
      />
    </span>
  );
}

export function ProviderCard({ providerKey, fields = [], defaultOpen = false, status = 'connected', warning, onTest, bannedUntil, fallbackChain }: ProviderBlockProps) {
  const [open, setOpen] = useState(defaultOpen);
  const [testing, setTesting] = useState(false);
  const p = PROVIDERS[providerKey];
  if (!p) return null;

  const Icon = PROVIDER_ICON[p.icon] ?? IcFilm;
  const slug = providerKey.toLowerCase();

  // F-06: clearer labels for the discovered states. "Coming soon"
  // distinguishes "we haven't built this yet" from "you need a key";
  // "Not configured" is for implemented providers awaiting credentials.
  const statusLabel =
    status === 'connected' ? 'Connected' :
    status === 'warning' ? 'Rate-limited' :
    status === 'error' ? 'Error' :
    status === 'coming-soon' ? 'Coming soon' :
    status === 'not-configured' ? 'Not configured' : 'Disabled';
  const statusColor: 'success' | 'warning' | 'error' | 'gray' =
    status === 'connected' ? 'success' :
    status === 'warning' ? 'warning' :
    status === 'error' ? 'error' : 'gray';

  const handleTest = async () => {
    if (!onTest) return;
    setTesting(true);
    try { await onTest(); } finally { setTesting(false); }
  };

  return (
    <div className={cn('overflow-hidden transition-colors', SETTINGS_CARD)}>
      <button className="flex w-full items-center gap-3 px-4 py-3.5 text-left" onClick={() => setOpen(o => !o)}>
        <ProviderLogo slug={slug} color={p.color} icon={Icon} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[14px] font-semibold text-ink">{p.name}</span>
            {p.for.map(t => <Badge key={t}>{t}</Badge>)}
          </div>
          <div className="mt-0.5 truncate text-[12px] text-ink-muted">{p.desc}</div>
        </div>
        <BadgeWithDot color={statusColor}>{statusLabel}</BadgeWithDot>
        <IcChevDown className={cn('size-4 shrink-0 text-ink-soft transition-transform duration-200', open && 'rotate-180')} />
      </button>

      <AnimatePresence initial={false}>
        {open ? (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.22, ease: 'easeOut' }}
            className="overflow-hidden"
          >
            <div className={cn('flex flex-col gap-3.5 border-t px-4 py-4', SETTINGS_DIVIDER)}>
              {/* Ban countdown — only renders while bannedUntil is set + future. */}
              {bannedUntil ? <BanCountdownBanner unixSec={bannedUntil} fallbackChain={fallbackChain} /> : null}
              {warning ? <Alert color="warning" icon={IcAlertTri}>{warning}</Alert> : null}
              {fallbackChain && fallbackChain.length > 0 ? (
                <Alert color="info">
                  <strong className="text-ink-muted">Fallback chain:</strong> if unavailable, Kira tries{' '}
                  {fallbackChain.map((k, i) => (
                    <span key={k}>{i > 0 ? ' → ' : ''}<span className="text-ink-muted">{k.toUpperCase()}</span></span>
                  ))}{' '}in order.
                </Alert>
              ) : null}

              {fields.length > 0 ? (
                <div className="flex flex-col gap-3.5">
                  {fields.map((f, i) => <ProviderField key={i} {...f} />)}
                </div>
              ) : null}

              <div className="flex justify-end">
                <Button color="secondary" size="sm" iconLeading={IcRefresh} isLoading={testing} showTextWhileLoading onClick={handleTest}>
                  Test connection
                </Button>
              </div>
            </div>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
}

// Jinja2 ({{ }}) versions of the built-in profiles — mirror backend
// DEFAULT_PROFILES. Kept LOCAL (not the shared NAMING_PROFILES, which stays
// {token} so the rename-modal preview's formatPath keeps working) and used
// as editor seeds + the input to the real backend live preview.
const JINJA_PROFILES: Record<string, Record<MediaType, string>> = {
  Plex: {
    movie: '{{n}} ({{y}})/{{n}} ({{y}}){{variant}} [{{q}}].{{x}}',
    tv:    '{{n}} ({{y}})/Season {{s2}}/{{n}} - S{{s2}}E{{e2}}{{variant}} - {{t}} [{{q}}].{{x}}',
    anime: '{{n}}/Season {{s2}}/{{n}} - S{{s2}}E{{e2}}{{variant}} - {{t}} [{{rg}}].{{x}}',
    music: '{{artist}}/{{album}} ({{y}})/{{tn}}{{variant}} - {{title}}.{{x}}',
  },
  Jellyfin: {
    movie: '{{n}} ({{y}})/{{n}} ({{y}}){{variant}}.{{x}}',
    tv:    '{{n}} ({{y}})/Season {{s2}}/{{n}} ({{y}}) - S{{s2}}E{{e2}}{{variant}} - {{t}}.{{x}}',
    anime: '{{n}} ({{y}})/Season {{s2}}/{{n}} - S{{s2}}E{{e2}}{{variant}} - {{t}}.{{x}}',
    music: '{{artist}}/{{album}}/{{tn}}{{variant}} {{title}}.{{x}}',
  },
  Kodi: {
    movie: '{{n}} ({{y}})/{{n}} ({{y}}){{variant}} - {{q}}.{{x}}',
    tv:    '{{n}}/Season {{s2}}/{{n}}.S{{s2}}E{{e2}}{{variant}}.{{t}}.{{x}}',
    anime: '{{n}}/S{{s2}}/{{n}} - {{abs}}{{variant}} - {{t}}.{{x}}',
    music: '{{artist}} - {{album}}/{{tn}}{{variant}}. {{title}}.{{x}}',
  },
  Custom: {
    movie: '{{n}} ({{y}})/{{n}} ({{y}}).{{x}}',
    tv:    '{{n}}/Season {{s2}}/{{n}} - S{{s2}}E{{e2}}.{{x}}',
    anime: '{{n}}/{{n}} - {{abs}} [{{rg}}].{{x}}',
    music: '{{artist}} - {{album}}/{{tn}}. {{title}}.{{x}}',
  },
};

// Complete token reference per media type ({{ }} syntax). Mirrors every
// token the backend _build_ctx provides so the palette is a true reference,
// not a subset. Filters (| pad, | ascii, | roman, | clean, | sortName,
// | upperInitial, | acronym, plus Jinja's | upper/lower/replace/default)
// are documented in TOKEN_FILTERS below.
const TOKEN_CHIPS: Record<MediaType, { k: string; d: string }[]> = {
  movie: [
    { k: '{{n}}', d: 'Title' }, { k: '{{y}}', d: 'Year' }, { k: '{{ny}}', d: 'Title (Year)' },
    { k: '{{decade}}', d: '1990s' }, { k: '{{x}}', d: 'Ext' }, { k: '{{q}}', d: 'Quality' },
    { k: '{{resolution}}', d: '1080p' }, { k: '{{source}}', d: 'BluRay/WEB' }, { k: '{{vc}}', d: 'Video codec' },
    { k: '{{ac}}', d: 'Audio codec' }, { k: '{{channels}}', d: 'Audio ch (5.1)' }, { k: '{{hdr}}', d: 'HDR' },
    { k: '{{bitdepth}}', d: '10bit' }, { k: '{{edition}}', d: 'Edition' }, { k: '{{variant}}', d: 'Variant suffix' },
    { k: '{{director}}', d: 'Director' }, { k: '{{cast}}', d: 'Cast' }, { k: '{{genres}}', d: 'Genres' },
    { k: '{{genre}}', d: 'First genre' }, { k: '{{studio}}', d: 'Studio' }, { k: '{{country}}', d: 'Country' },
    { k: '{{runtime}}', d: 'Minutes' }, { k: '{{gigabytes}}', d: 'Size (GB)' },
    { k: '{{tmdbid}}', d: 'TMDB id' }, { k: '{{imdbid}}', d: 'IMDb id' }, { k: '{{plex}}', d: 'Full Plex path' },
  ],
  tv: [
    { k: '{{n}}', d: 'Series' }, { k: '{{y}}', d: 'Year' }, { k: '{{s2}}', d: 'Season (00)' },
    { k: '{{e2}}', d: 'Episode (00)' }, { k: '{{e2end}}', d: 'End ep (ranges)' }, { k: '{{s00e00}}', d: 'S01E05' },
    { k: '{{sxe}}', d: '1x05' }, { k: '{{t}}', d: 'Ep title' }, { k: '{{airdate}}', d: 'Air date' },
    { k: '{{q}}', d: 'Quality' }, { k: '{{resolution}}', d: '1080p' }, { k: '{{vc}}', d: 'Video codec' },
    { k: '{{channels}}', d: 'Audio ch (5.1)' }, { k: '{{hdr}}', d: 'HDR' }, { k: '{{variant}}', d: 'Variant suffix' },
    { k: '{{network}}', d: 'Network' }, { k: '{{studio}}', d: 'Studio' }, { k: '{{genres}}', d: 'Genres' },
    { k: '{{yearrange}}', d: '2022 – 2024' }, { k: '{{tvdbid}}', d: 'TVDB id' }, { k: '{{plex}}', d: 'Full Plex path' },
  ],
  anime: [
    { k: '{{n}}', d: 'Series' }, { k: '{{s2}}', d: 'Season' }, { k: '{{e2}}', d: 'Episode' },
    { k: '{{abs}}', d: 'Absolute (000)' }, { k: '{{s00e00}}', d: 'S01E05' }, { k: '{{t}}', d: 'Ep title' },
    { k: '{{rg}}', d: 'Group' }, { k: '{{group}}', d: 'Group (blank)' }, { k: '{{cour}}', d: 'Cour #' },
    { k: '{{vc}}', d: 'Video codec' }, { k: '{{channels}}', d: 'Audio ch (5.1)' }, { k: '{{hdr}}', d: 'HDR' },
    { k: '{{bitdepth}}', d: '10bit' }, { k: '{{variant}}', d: 'Audio/edition' }, { k: '{{studio}}', d: 'Studio' },
    { k: '{{genres}}', d: 'Genres' }, { k: '{{anidbid}}', d: 'AniDB id' }, { k: '{{plex}}', d: 'Full Plex path' },
  ],
  music: [
    { k: '{{artist}}', d: 'Artist' }, { k: '{{album}}', d: 'Album' }, { k: '{{y}}', d: 'Year' },
    { k: '{{decade}}', d: '1990s' }, { k: '{{tn}}', d: 'Track # (02)' }, { k: '{{title}}', d: 'Track title' },
    { k: '{{label}}', d: 'Label' }, { k: '{{genres}}', d: 'Genres' }, { k: '{{x}}', d: 'Ext' },
  ],
};

// Reusable filters (advanced string helpers + Jinja built-ins). Shown as a hint under
// the token palette so users can discover them. e.g. `{{ n | upper }}`,
// `{{ episode | pad(3) }}`, `{{ n | acronym }}`.
const TOKEN_FILTERS = 'pad(n) · ascii · roman · clean · sortName · upperInitial · acronym · upper · lower · replace(a,b) · default(x)';

// Seed the 4-type template set for a profile. For Custom, layer any saved
// custom templates over the built-in Custom defaults so unset types still
// render sensibly.
function seedTemplates(profile: string, savedCustom?: Record<string, string>): Record<MediaType, string> {
  if (profile === 'Custom') {
    return { ...JINJA_PROFILES.Custom, ...(savedCustom ?? {}) } as Record<MediaType, string>;
  }
  return { ...(JINJA_PROFILES[profile] ?? JINJA_PROFILES.Plex) };
}

// 4-tab naming template editor with a REAL live preview (rendered by the
// backend engine against the user's own files — see LiveTemplatePreview).
// Layout is a 2-pane editor | preview grid so the wide Settings column gets
// used: the editor (tabs + template + token palette) sits on the left, the
// live-rendered paths (which are long) get the full right pane.
//
// Custom-profile edits persist via onSaveCustom → backend `naming.custom.Custom`
// (the same JSON dict the rename engine reads at rename time). Built-in
// profiles are read-only.
export function NamingTemplateTabs({ profile, savedCustom, onSaveCustom }: {
  profile: string;
  savedCustom?: Record<string, string>;
  onSaveCustom?: (dict: Record<string, string>) => void;
}) {
  const tabs: { key: MediaType; label: string; icon: ReactNode }[] = [
    { key: 'movie', label: 'Movies', icon: <IcFilm style={{ width: 13, height: 13 }} /> },
    { key: 'tv',    label: 'TV',     icon: <IcTv style={{ width: 13, height: 13 }} /> },
    { key: 'anime', label: 'Anime',  icon: <IcAnime style={{ width: 13, height: 13 }} /> },
    { key: 'music', label: 'Music',  icon: <IcMusic style={{ width: 13, height: 13 }} /> },
  ];
  const [tab, setTab] = useState<MediaType>('movie');
  const [templates, setTemplates] = useState<Record<MediaType, string>>(() => seedTemplates(profile, savedCustom));

  // Keep the latest saved-custom values in a ref so a profile switch can
  // re-seed from them WITHOUT savedCustom being a seed-effect dependency —
  // otherwise every debounced save (which updates savedCustom) would re-seed
  // and clobber the user's in-progress edits / jump their caret.
  const savedCustomRef = useRef(savedCustom);
  useEffect(() => { savedCustomRef.current = savedCustom; }, [savedCustom]);

  // Re-seed only when the profile changes. Declared AFTER the ref-sync effect
  // so that when a settings load lands both a new profile AND savedCustom in
  // the same commit, the ref is already current before this reads it.
  useEffect(() => {
    setTemplates(seedTemplates(profile, savedCustomRef.current));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profile]);

  const editable = profile === 'Custom';
  const tpl = templates[tab];
  const tokens = TOKEN_CHIPS[tab] || [];
  const inputRef = useRef<HTMLInputElement>(null);

  // Apply an edit to the active tab. Updates local state and, for the Custom
  // profile, persists the whole 4-type dict through onSaveCustom (debounced
  // upstream).
  function applyEdit(nextForTab: string) {
    // Compute the next dict from the current closure value and call both
    // setters OUTSIDE any updater. (Calling the parent's onSaveCustom from
    // inside a setTemplates updater triggers React's "setState while
    // rendering another component" warning.)
    const next = { ...templates, [tab]: nextForTab };
    setTemplates(next);
    if (editable) onSaveCustom?.(next);
  }

  // Click a token chip → insert it at the caret (Custom profile only).
  function insertToken(k: string) {
    if (!editable) return;
    const el = inputRef.current;
    const cur = templates[tab] ?? '';
    if (el && typeof el.selectionStart === 'number') {
      const a = el.selectionStart, b = el.selectionEnd ?? a;
      applyEdit(cur.slice(0, a) + k + cur.slice(b));
      // restore caret just after the inserted token
      requestAnimationFrame(() => {
        el.focus();
        const pos = a + k.length;
        el.setSelectionRange(pos, pos);
      });
    } else {
      applyEdit(cur + k);
    }
  }

  return (
    <div>
      <div className="provider-tabs" style={{ marginBottom: 16 }}>
        {tabs.map(t => (
          <button key={t.key} className={`provider-tab ${tab === t.key ? 'on' : ''}`} onClick={() => setTab(t.key)}>
            <span style={{ display: 'inline-flex', alignItems: 'center', width: 13, height: 13, color: tab === t.key ? TYPE_COLOR[t.key] : 'var(--ink-3)' }}>
              {t.icon}
            </span>
            {t.label}
          </button>
        ))}
      </div>

      <div className="naming-editor">
        {/* ── LEFT: template + token palette ───────────────── */}
        <div className="naming-pane">
          <div className="naming-pane-head">
            <span>{tabs.find(t => t.key === tab)!.label} template</span>
            {!editable
              ? <span className="naming-lock">{profile} preset · pick Custom to edit</span>
              : <span className="naming-lock" style={{ color: 'var(--accent)' }}>editable · autosaved</span>}
          </div>
          <input
            ref={inputRef}
            className="input mono"
            value={tpl}
            readOnly={!editable}
            spellCheck={false}
            onChange={e => applyEdit(e.target.value)}
          />
          <div className="naming-hint">
            Pipe filters like <code>{'{{ n | upper }}'}</code> and conditionals
            like <code>{'{% if hdr %}…{% endif %}'}</code> work too.
          </div>

          <div className="naming-pane-head" style={{ marginTop: 18 }}>
            <span>Tokens for {tab}</span>
            {editable ? <span className="naming-lock">click to insert</span> : null}
          </div>
          <div className="naming-tokens">
            {tokens.map(t => (
              <button
                key={t.k}
                type="button"
                className={`token-chip ${editable ? 'clickable' : ''}`}
                disabled={!editable}
                onClick={() => insertToken(t.k)}
                title={editable ? `Insert ${t.k}` : t.d}
              >
                <span className="kbd" style={{ margin: 0 }}>{t.k}</span>
                <span style={{ color: 'var(--ink-3)', fontSize: 11 }}>{t.d}</span>
              </button>
            ))}
          </div>
          <div style={{ marginTop: 8, fontSize: 11, color: 'var(--ink-3)', lineHeight: 1.5 }}>
            <span style={{ color: 'var(--ink-4)' }}>Filters (pipe with </span>
            <code style={{ color: 'var(--ink-2)' }}>|</code>
            <span style={{ color: 'var(--ink-4)' }}>): </span>
            {TOKEN_FILTERS}
          </div>
        </div>

        {/* ── RIGHT: live preview against the real library ─── */}
        <div className="naming-pane naming-pane-preview">
          <div className="naming-pane-head">
            <span>Live preview · your library</span>
          </div>
          <LiveTemplatePreview tab={tab} template={tpl} />
        </div>
      </div>
    </div>
  );
}

// Real live preview: debounces edits, calls the backend's /rename/preview-
// template (the SAME engine a real rename uses) against the user's recent
// matched files, and shows the actual paths it would produce.
function LiveTemplatePreview({ tab, template }: { tab: MediaType; template: string }) {
  const [samples, setSamples] = useState<{ filename: string; rendered: string; error: string | null }[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setErr(null);
    const handle = window.setTimeout(async () => {
      try {
        const body: { movie?: string; tv?: string; anime?: string; music?: string; samples_per_type?: number } = { samples_per_type: 3 };
        body[tab] = template;
        const resp = await api.previewTemplate(body);
        if (cancelled) return;
        setSamples(resp.samples.filter(s => s.media_type === tab));
      } catch (e) {
        if (!cancelled) { setErr((e as Error).message); setSamples(null); }
      }
    }, 350);
    return () => { cancelled = true; window.clearTimeout(handle); };
  }, [tab, template]);

  if (err) {
    return <div className="naming-preview-empty" style={{ color: 'var(--conf-low)' }}>Preview unavailable: {err}</div>;
  }
  if (samples === null) {
    return <div className="naming-preview-empty">Rendering…</div>;
  }
  if (samples.length === 0) {
    return (
      <div className="naming-preview-empty">
        No matched {tab} files yet — scan &amp; match some to see a live preview.
      </div>
    );
  }
  return (
    <div className="naming-preview-list">
      {samples.map((s, i) => (
        <div key={i} className="naming-preview-row">
          {s.error ? (
            <span style={{ color: 'var(--conf-low)', fontSize: 12 }}>{s.filename}: {s.error}</span>
          ) : (
            <>
              <div className="naming-preview-src">{s.filename}</div>
              <div className="preview-path"><span className="seg-new">{s.rendered}</span></div>
            </>
          )}
        </div>
      ))}
    </div>
  );
}
