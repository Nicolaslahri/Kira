// Tiny formatting helpers — copy / numeric display.
// Kept in its own file so it can be imported anywhere without dragging
// the rest of the lib in.

/**
 * F-03: Singular/plural switch. Returns the count followed by the singular
 * form (n=1) or plural form (everything else, including 0). Plural defaults
 * to `${singular}s` for the regular case; pass an explicit plural for
 * irregular words ("episode"/"episodes" works automatically; "tax"/"taxes"
 * needs the explicit form).
 *
 * Examples:
 *   pluralize(1, 'episode')  → "1 episode"
 *   pluralize(2, 'episode')  → "2 episodes"
 *   pluralize(0, 'episode')  → "0 episodes"
 *   pluralize(3, 'tax', 'taxes') → "3 taxes"
 */
export function pluralize(n: number, singular: string, plural?: string): string {
  const word = n === 1 ? singular : (plural ?? `${singular}s`);
  return `${n} ${word}`;
}

/**
 * Map ISO 639-2/B codes (or other short forms) to display names. Used in
 * CoverPopup metadata block where TVDB/TMDB returns 3-letter codes that
 * read as opaque jargon to end users. Falls back to the original code
 * (uppercased) when unknown so nothing disappears.
 */
const _LANG_NAMES: Record<string, string> = {
  eng: 'English', en: 'English',
  jpn: 'Japanese', ja: 'Japanese',
  fre: 'French', fra: 'French', fr: 'French',
  ger: 'German', deu: 'German', de: 'German',
  spa: 'Spanish', es: 'Spanish',
  ita: 'Italian', it: 'Italian',
  kor: 'Korean', ko: 'Korean',
  chi: 'Chinese', zho: 'Chinese', zh: 'Chinese',
  rus: 'Russian', ru: 'Russian',
  por: 'Portuguese', pt: 'Portuguese',
  ara: 'Arabic', ar: 'Arabic',
  hin: 'Hindi', hi: 'Hindi',
  dut: 'Dutch', nld: 'Dutch', nl: 'Dutch',
  swe: 'Swedish', sv: 'Swedish',
  nor: 'Norwegian', no: 'Norwegian',
  dan: 'Danish', da: 'Danish',
  fin: 'Finnish', fi: 'Finnish',
  pol: 'Polish', pl: 'Polish',
  tur: 'Turkish', tr: 'Turkish',
};

/**
 * Map ISO 3166-1 alpha-2 / alpha-3 country codes to display names. Same
 * principle as language — TVDB returns codes like "usa" / "us" / "jpn"
 * which read as jargon. Falls back to uppercased code when unknown.
 */
const _COUNTRY_NAMES: Record<string, string> = {
  usa: 'United States', us: 'United States',
  jpn: 'Japan', jp: 'Japan',
  gbr: 'United Kingdom', uk: 'United Kingdom', gb: 'United Kingdom',
  can: 'Canada', ca: 'Canada',
  aus: 'Australia', au: 'Australia',
  fra: 'France', fr: 'France',
  deu: 'Germany', de: 'Germany',
  esp: 'Spain', es: 'Spain',
  ita: 'Italy', it: 'Italy',
  kor: 'South Korea', kr: 'South Korea',
  chn: 'China', cn: 'China',
  mex: 'Mexico', mx: 'Mexico',
  bra: 'Brazil', br: 'Brazil',
  ind: 'India', in: 'India',
  rus: 'Russia', ru: 'Russia',
  nld: 'Netherlands', nl: 'Netherlands',
  swe: 'Sweden', se: 'Sweden',
  nor: 'Norway', no: 'Norway',
  dnk: 'Denmark', dk: 'Denmark',
  fin: 'Finland', fi: 'Finland',
  pol: 'Poland', pl: 'Poland',
  tur: 'Turkey', tr: 'Turkey',
  arg: 'Argentina', ar: 'Argentina',
  irl: 'Ireland', ie: 'Ireland',
  nzl: 'New Zealand', nz: 'New Zealand',
  che: 'Switzerland', ch: 'Switzerland',
  bel: 'Belgium', be: 'Belgium',
  aut: 'Austria', at: 'Austria',
};

export function prettyLanguage(code: string | null | undefined): string {
  if (!code) return '';
  const key = code.trim().toLowerCase();
  return _LANG_NAMES[key] ?? code.toUpperCase();
}

export function prettyCountry(code: string | null | undefined): string {
  if (!code) return '';
  const key = code.trim().toLowerCase();
  return _COUNTRY_NAMES[key] ?? code.toUpperCase();
}
