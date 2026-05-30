import type { SVGProps } from 'react';

type IconProps = SVGProps<SVGSVGElement>;

const defaults: IconProps = {
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 2,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
};

function icon(children: React.ReactNode, overrides?: Partial<IconProps>) {
  return function Icon(props: IconProps) {
    return <svg {...defaults} {...overrides} {...props}>{children}</svg>;
  };
}

export const IcDashboard = icon(<>
  <rect x="3" y="3" width="7" height="9" rx="1.5"/>
  <rect x="14" y="3" width="7" height="5" rx="1.5"/>
  <rect x="14" y="12" width="7" height="9" rx="1.5"/>
  <rect x="3" y="16" width="7" height="5" rx="1.5"/>
</>);

export const IcReview = icon(<>
  <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/>
  <circle cx="9" cy="12" r="1"/><circle cx="13" cy="12" r="1"/><circle cx="17" cy="12" r="1"/>
</>);

export const IcHistory = icon(<>
  <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/>
  <path d="M3 3v5h5"/><path d="M12 7v5l4 2"/>
</>);

export const IcSettings = icon(<>
  <circle cx="12" cy="12" r="3"/>
  <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/>
</>);

export const IcSearch = icon(<>
  <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
</>);

export const IcFolder = icon(
  <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
);

export const IcFilm = icon(<>
  <rect x="2" y="2" width="20" height="20" rx="2.18" ry="2.18"/>
  <line x1="7" y1="2" x2="7" y2="22"/><line x1="17" y1="2" x2="17" y2="22"/>
  <line x1="2" y1="12" x2="22" y2="12"/>
  <line x1="2" y1="7" x2="7" y2="7"/><line x1="2" y1="17" x2="7" y2="17"/>
  <line x1="17" y1="17" x2="22" y2="17"/><line x1="17" y1="7" x2="22" y2="7"/>
</>);

export const IcTv = icon(<>
  <rect x="2" y="7" width="20" height="15" rx="2" ry="2"/>
  <polyline points="17 2 12 7 7 2"/>
</>);

export const IcCheck = icon(
  <polyline points="20 6 9 17 4 12"/>,
  { strokeWidth: 3 }
);

export const IcX = icon(<>
  <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
</>);

export const IcPlay = icon(
  <polygon points="6 3 20 12 6 21 6 3" fill="currentColor"/>
);

export const IcRefresh = icon(<>
  <polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/>
  <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>
</>);

export const IcArrowRight = icon(<>
  <line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/>
</>);

export const IcChevDown = icon(
  <polyline points="6 9 12 15 18 9"/>
);

export const IcMenu = icon(<>
  <line x1="3" y1="6" x2="21" y2="6"/>
  <line x1="3" y1="12" x2="21" y2="12"/>
  <line x1="3" y1="18" x2="21" y2="18"/>
</>);

export const IcScan = icon(<>
  <path d="M3 7V5a2 2 0 0 1 2-2h2"/><path d="M17 3h2a2 2 0 0 1 2 2v2"/>
  <path d="M21 17v2a2 2 0 0 1-2 2h-2"/><path d="M7 21H5a2 2 0 0 1-2-2v-2"/>
  <line x1="7" y1="12" x2="17" y2="12"/>
</>);

export const IcUndo = icon(<>
  <path d="M3 7v6h6"/><path d="M21 17a9 9 0 0 0-15-6.7L3 13"/>
</>);

export const IcBell = icon(<>
  <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/>
  <path d="M13.73 21a2 2 0 0 1-3.46 0"/>
</>);

export const IcKeyboard = icon(<>
  <rect x="2" y="6" width="20" height="12" rx="2"/>
  <path d="M6 10h0M10 10h0M14 10h0M18 10h0M6 14h0M18 14h0M10 14h4"/>
</>);

export const IcAlertTri = icon(<>
  <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
  <line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
</>);

export const IcSparkles = icon(<>
  <path d="M12 3l1.5 4.5L18 9l-4.5 1.5L12 15l-1.5-4.5L6 9l4.5-1.5L12 3z"/>
  <path d="M18 14l.75 2.25L21 17l-2.25.75L18 20l-.75-2.25L15 17l2.25-.75L18 14z"/>
</>);

export const IcTrash = icon(<>
  <polyline points="3 6 5 6 21 6"/>
  <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
  <path d="M10 11v6M14 11v6"/>
</>);

export const IcDownload = icon(<>
  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
  <polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>
</>);

// Eye icons for password-style input show/hide toggle. Used by the
// Sonarr API-key field — many users want to glance at the masked
// value to verify it before saving, without un-masking via devtools.
export const IcEye = icon(<>
  <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
  <circle cx="12" cy="12" r="3"/>
</>);
export const IcEyeOff = icon(<>
  <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/>
  <line x1="1" y1="1" x2="23" y2="23"/>
</>);

export const IcExternal = icon(<>
  <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>
  <polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/>
</>);

export const IcKey = icon(
  <path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"/>
);

export const IcLink = icon(<>
  <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/>
  <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>
</>);

export const IcShieldCheck = icon(<>
  <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
  <polyline points="9 12 11 14 15 10"/>
</>);

export const IcSpin = icon(
  <path d="M21 12a9 9 0 1 1-6.219-8.56"/>,
  { style: { animation: 'spin 1s linear infinite' } }
);

export const IcMusic = icon(<>
  <path d="M9 18V5l12-2v13"/>
  <circle cx="6" cy="18" r="3" fill="currentColor"/>
  <circle cx="18" cy="16" r="3" fill="currentColor"/>
</>);

export const IcAnime = icon(<>
  <path d="M12 2l2.5 6.5L21 11l-6.5 2.5L12 20l-2.5-6.5L3 11l6.5-2.5z" fill="currentColor" fillOpacity="0.18"/>
  <path d="M12 2l2.5 6.5L21 11l-6.5 2.5L12 20l-2.5-6.5L3 11l6.5-2.5z"/>
</>);

export const IcDisc = icon(<>
  <circle cx="12" cy="12" r="10"/>
  <circle cx="12" cy="12" r="2" fill="currentColor"/>
  <path d="M12 2a10 10 0 0 1 8 16" strokeOpacity="0.45"/>
</>);

export const IcWaveform = icon(
  <path d="M2 12h2M6 8v8M10 5v14M14 9v6M18 7v10M22 12h-2"/>
);

export const IcTag = icon(<>
  <path d="M20.59 13.41 13.42 20.58a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/>
  <line x1="7" y1="7" x2="7.01" y2="7"/>
</>);

export const IcPlus = icon(<>
  <line x1="12" y1="5" x2="12" y2="19"/>
  <line x1="5" y1="12" x2="19" y2="12"/>
</>);

// Reuses the public/favicon.svg so the browser tab icon and in-app logo
// stay visually identical. Sized to fill its container.
export const IcLogoMark = (props: { className?: string; style?: React.CSSProperties }) => (
  <img
    src="/favicon.svg"
    alt="Kira"
    width={28}
    height={27}
    draggable={false}
    {...props}
    style={{ display: 'block', width: '100%', height: 'auto', ...props.style }}
  />
);
