import type {
  MediaFile, NamingProfile, PosterData, MediaType,
  ProviderKey, ProviderMeta,
} from './types';

// Monochrome app: poster tints are neutral dark-grey gradients (was a set of
// vivid per-show colour pairs). A few tonal variations keep episode badges /
// poster glows from being dead-identical while staying strictly black+white.
const TINTS: [string, string][] = [
  ['#3a3a3a', '#1a1a1a'],
  ['#333333', '#161616'],
  ['#2c2c2c', '#141414'],
  ['#404040', '#1e1e1e'],
  ['#2f2f2f', '#171717'],
  ['#373737', '#1b1b1b'],
  ['#282828', '#131313'],
  ['#444444', '#202020'],
];

function tintFor(seed: string): [string, string] {
  let h = 0;
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) | 0;
  return TINTS[Math.abs(h) % TINTS.length];
}

export function poster(title: string, year: number | null): PosterData {
  const stop = new Set(['the', 'of', 'a', 'an', 'and', 'to', 'part']);
  const words = title.split(/[\s:.-]+/).filter(w => w && !stop.has(w.toLowerCase()));
  const initials = (words.slice(0, 2).map(w => w[0]).join('') || title.slice(0, 2)).toUpperCase();
  return { init: initials, tint: tintFor(title), year };
}

export const NAMING_PROFILES: Record<string, NamingProfile> = {
  Plex: {
    movie: '{n} ({y})/{n} ({y}) [{q}].{x}',
    tv:    '{n} ({y})/Season {s2}/{n} - S{s2}E{e2} - {t} [{q}].{x}',
    anime: '{n}/Season {s2}/{n} - S{s2}E{e2} - {t} [{rg}].{x}',
    music: '{artist}/{album} ({y})/{tn} - {title}.{x}',
  },
  Jellyfin: {
    movie: '{n} ({y})/{n} ({y}).{x}',
    tv:    '{n} ({y})/Season {s2}/{n} ({y}) - S{s2}E{e2} - {t}.{x}',
    anime: '{n} ({y})/Season {s2}/{n} - S{s2}E{e2} - {t}.{x}',
    music: '{artist}/{album}/{tn} {title}.{x}',
  },
  Kodi: {
    movie: '{n} ({y})/{n} ({y}) - {q}.{x}',
    tv:    '{n}/Season {s2}/{n}.S{s2}E{e2}.{t}.{x}',
    anime: '{n}/S{s2}/{n} - {abs} - {t}.{x}',
    music: '{artist} - {album}/{tn}. {title}.{x}',
  },
  Custom: {
    movie: 'Movies/{n} ({y})/{n} ({y}).{x}',
    tv:    'TV/{n}/S{s2}/{n} - S{s2}E{e2}.{x}',
    anime: 'Anime/{n}/{n} - {abs} [{rg}].{x}',
    music: 'Music/{artist}/{album}/{tn} - {title}.{x}',
  },
};

export const PROVIDERS: Record<ProviderKey, ProviderMeta> = {
  TMDB:        { name: 'TMDB',        for: ['movie', 'tv'],    color: '#90cea1', icon: 'film',  logo: '/providers/tmdb.svg',
                 desc: 'Movies and TV series · the gold standard for English-language libraries' },
  TVDB:        { name: 'TheTVDB',     for: ['tv', 'anime'],    color: '#6ec1ff', icon: 'tv',    logo: '/providers/tvdb.svg',
                 desc: 'Deep TV metadata with strong support for absolute episode numbering' },
  AniDB:       { name: 'AniDB',       for: ['anime'],          color: '#c89bff', icon: 'anime', logo: '/providers/anidb.svg',
                 desc: 'The canonical source for anime — episodes, groups, alternate titles' },
  MusicBrainz: { name: 'MusicBrainz', for: ['music'],          color: '#ffb14a', icon: 'disc',  logo: '/providers/musicbrainz.svg',
                 desc: 'Open music encyclopedia · artists, releases, recordings' },
  AcoustID:    { name: 'AcoustID',    for: ['music'],          color: '#28d9a0', icon: 'waveform',
                 desc: 'Audio fingerprint matching for music files with missing or wrong tags' },
  'fanart.tv': { name: 'fanart.tv',   for: ['movie', 'tv', 'anime'], color: '#ff7575', icon: 'film', logo: '/providers/fanart.tv.png',
                 desc: 'Artwork only — clear logos, clear art, banners, disc & character art (used by Download artwork)' },
  OpenSubtitles: { name: 'OpenSubtitles', for: ['movie', 'tv', 'anime'], color: '#ff9a4d', icon: 'caption', logo: '/providers/opensubtitles.svg',
                 desc: 'Subtitles only — community subtitle downloads. Key enables search; the account login is what downloads need.' },
  SubDL:       { name: 'SubDL',        for: ['movie', 'tv', 'anime'], color: '#5ac8d7', icon: 'caption', logo: '/providers/subdl.svg',
                 desc: 'Subtitles only — modern REST catalogue. Free key at subdl.com → panel → API.' },
  SubSource:   { name: 'SubSource',    for: ['movie', 'tv', 'anime'], color: '#b48cff', icon: 'caption', logo: '/providers/subsource.svg',
                 desc: "Subtitles only — Subscene's successor. Free key from your subsource.net profile." },
};

export const TYPE_COLOR: Record<MediaType, string> = {
  movie: '#4ec5b3',
  tv:    '#b3e5fc',
  anime: '#c89bff',
  music: '#ffb14a',
};

export function formatPath(file: MediaFile, profile: string = 'Plex'): string {
  if (!file.match) return file.filename;
  const m = file.match;
  const root = '/media/library';
  const x = file.filename.split('.').pop()!.toLowerCase();
  const qMatch = file.filename.match(/(2160p|1080p|720p|480p)/i);
  const sMatch = file.filename.match(/(WEB-DL|BluRay|HDR|WEB|HDTV|REMUX)/i);
  const q = [qMatch?.[1], sMatch?.[1]].filter(Boolean).join(' ');
  const tpl = NAMING_PROFILES[profile][file.mediaType] || NAMING_PROFILES[profile].movie;
  const s2  = m.season != null ? String(m.season).padStart(2, '0') : '';
  const e2  = m.episode != null ? String(m.episode).padStart(2, '0') : '';
  const tn  = m.track != null ? String(m.track).padStart(2, '0') : '';
  const abs = m.absoluteEpisode != null ? String(m.absoluteEpisode).padStart(3, '0') : e2;
  const filled = tpl
    .replace(/\{n\}/g,       m.title || '')
    .replace(/\{y\}/g,       String(m.year ?? m.albumYear ?? ''))
    .replace(/\{q\}/g,       q || '1080p')
    .replace(/\{x\}/g,       x)
    .replace(/\{s2\}/g,      s2)
    .replace(/\{e2\}/g,      e2)
    .replace(/\{abs\}/g,     abs)
    .replace(/\{t\}/g,       m.episodeTitle ?? '')
    .replace(/\{rg\}/g,      file.releaseGroup ?? '')
    .replace(/\{artist\}/g,  m.artist ?? '')
    .replace(/\{album\}/g,   m.album ?? '')
    .replace(/\{tn\}/g,      tn)
    .replace(/\{title\}/g,   m.trackTitle ?? '');
  const subfolder = ({ movie: 'Movies', tv: 'TV', anime: 'Anime', music: 'Music' } as const)[file.mediaType] || 'Movies';
  return root + '/' + subfolder + '/' + filled;
}
