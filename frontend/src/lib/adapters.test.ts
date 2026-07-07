import { describe, it, expect } from 'vitest';
import { apiToMediaFile, buildLibraryItems, mergeDuplicateClusters } from './adapters';
import type { ApiMediaFile, ApiMatch } from './api';
import type { MediaFile } from './types';

// ── Fixture builders ──────────────────────────────────────────────────
// Minimal valid ApiMatch / ApiMediaFile with per-test overrides. Keeping
// these here (not in a shared helper) so each spec reads top-to-bottom.

function mkMatch(over: Partial<ApiMatch> = {}): ApiMatch {
  return {
    id: 1, provider: 'tmdb', provider_id: '100', match_type: 'movie',
    confidence: 1, title: 'A Movie', year: 2020, season_number: null,
    episode_number: null, episode_title: null, poster_url: null,
    overview: null, is_selected: true, is_manual: false,
    series_group_id: null, metadata: null, ...over,
  };
}

function mkFile(over: Partial<ApiMediaFile> = {}): ApiMediaFile {
  return {
    id: 1, file_path: '/media/A Movie (2020)/A Movie (2020).mkv',
    file_size: null, media_type: 'movie', status: 'pending',
    parsed_data: null, series_key: null, variant_key: null,
    missing_subs: null, created_at: '', updated_at: '', matches: [], ...over,
  };
}

/** Build a matched, non-movie MediaFile (the common grid-clustering input). */
function epFile(over: Partial<ApiMediaFile>, match: Partial<ApiMatch>): MediaFile {
  return apiToMediaFile(mkFile({
    media_type: 'tv',
    matches: [mkMatch({ match_type: 'tv_episode', ...match })],
    ...over,
  }));
}

describe('apiToMediaFile', () => {
  it('maps the basic identity fields (id→string, filename/folder split)', () => {
    const f = apiToMediaFile(mkFile({ id: 42, file_path: '/x/y/A Movie (2020)/A Movie (2020).mkv' }));
    expect(f.id).toBe('42');           // ids are strings in the UI model
    expect(f.filename).toBe('A Movie (2020).mkv');
    expect(f.folder).toBe('/x/y/A Movie (2020)');
    expect(f.mediaType).toBe('movie'); // null media_type defaults to movie
  });

  it('picks the is_selected match over array order (manual-pin clobber bug)', () => {
    // Backend sorts by confidence DESC; an old auto-match and a fresh manual
    // pick can BOTH be 1.0, leaving the manual one at index 1+. Reading
    // matches[0] showed the stale row even though the pick succeeded.
    const f = apiToMediaFile(mkFile({
      matches: [
        mkMatch({ id: 1, confidence: 1, is_selected: false, title: 'Stale Top' }),
        mkMatch({ id: 2, confidence: 1, is_selected: true, title: 'Manual Pick' }),
      ],
    }));
    expect(f.match?.title).toBe('Manual Pick');
    expect(f.match?.matchId).toBe(2);
  });

  it('falls back to matches[0] when nothing is is_selected', () => {
    const f = apiToMediaFile(mkFile({
      matches: [
        mkMatch({ id: 7, is_selected: false, title: 'First' }),
        mkMatch({ id: 8, is_selected: false, title: 'Second' }),
      ],
    }));
    expect(f.match?.matchId).toBe(7);
  });

  it('strips a trailing (YYYY) so the year is not double-printed', () => {
    const f = apiToMediaFile(mkFile({
      matches: [mkMatch({ title: 'Kanojo, Okarishimasu (2022)', year: 2022 })],
    }));
    expect(f.match?.title).toBe('Kanojo, Okarishimasu');
    expect(f.match?.year).toBe(2022);
  });

  it("passes 'renamed' status through (renamed-collapsed-to-pending bug)", () => {
    // 'renamed' once collapsed to 'pending', so renamed files reappeared in
    // the queue forever and the Renamed tab stayed empty.
    expect(apiToMediaFile(mkFile({ status: 'renamed' })).status).toBe('renamed');
  });

  it('collapses unknown statuses to pending', () => {
    expect(apiToMediaFile(mkFile({ status: 'totally_made_up' })).status).toBe('pending');
  });

  it('trusts the matcher canonical season over the filename-parsed season', () => {
    // `[ToonsHub] BLEACH TYBW - S01E01` parses as S1 but Fribb pins the AID
    // to S17. Trusting parsed.season split the franchise card across clusters.
    const f = apiToMediaFile(mkFile({
      media_type: 'anime',
      parsed_data: { season: 1, episode: 1 },
      matches: [mkMatch({ provider: 'anidb', provider_id: '18671', season_number: 17, episode_number: 1 })],
    }));
    expect(f.match?.season).toBe(17);
  });

  it('keeps missing_subs only when the array is non-empty', () => {
    // The chip/button must render exactly when there is a real gap.
    expect(apiToMediaFile(mkFile({ missing_subs: ['en'] })).missingSubs).toEqual(['en']);
    expect(apiToMediaFile(mkFile({ missing_subs: [] })).missingSubs).toBeUndefined();
    expect(apiToMediaFile(mkFile({ missing_subs: null })).missingSubs).toBeUndefined();
  });

  it('yields a null match when there is neither a provider hit nor a parsed title', () => {
    expect(apiToMediaFile(mkFile({ matches: [], parsed_data: null })).match).toBeNull();
  });

  it('synthesizes a placeholder match from parsed title alone (no provider hit)', () => {
    const f = apiToMediaFile(mkFile({ matches: [], parsed_data: { title: 'Some Show' } }));
    expect(f.match).not.toBeNull();
    expect(f.match?.provider).toBeUndefined();   // placeholder — no real provider
    expect(f.match?.matchId).toBeUndefined();
  });

  it('surfaces matched_via for music (the "via …" transparency chip source)', () => {
    const f = apiToMediaFile(mkFile({
      media_type: 'music',
      matches: [mkMatch({ metadata: { music: true, matched_via: 'acoustid' } })],
    }));
    expect(f.matchedVia).toBe('acoustid');
    // Non-music never carries it (the field is music-gated).
    const g = apiToMediaFile(mkFile({
      media_type: 'movie',
      matches: [mkMatch({ metadata: { matched_via: 'acoustid' } })],
    }));
    expect(g.matchedVia).toBeUndefined();
  });
});

describe('buildLibraryItems — grid clustering', () => {
  it('keeps each movie solo even when matched (no franchise collapse)', () => {
    // Otherwise every Marvel movie would merge into one card.
    const items = buildLibraryItems([
      apiToMediaFile(mkFile({ id: 1, media_type: 'movie', matches: [mkMatch({ provider: 'tmdb', provider_id: '1' })] })),
      apiToMediaFile(mkFile({ id: 2, media_type: 'movie', matches: [mkMatch({ provider: 'tmdb', provider_id: '2' })] })),
    ]);
    expect(items).toHaveLength(2);
    expect(items.every(i => i.kind === 'movie')).toBe(true);
  });

  it('clusters duplicate COPIES of the same movie into one card', () => {
    // Two files matched to the SAME tmdb id are copies of one film. Split,
    // they rendered as two identical cards the collection band mislabeled
    // "Part 1 / Part 2"; clustered, the duplicates tooling sees them.
    const items = buildLibraryItems([
      apiToMediaFile(mkFile({ id: 1, media_type: 'movie', matches: [mkMatch({ provider: 'tmdb', provider_id: '1266127' })] })),
      apiToMediaFile(mkFile({ id: 2, media_type: 'movie', matches: [mkMatch({ provider: 'tmdb', provider_id: '1266127' })] })),
    ]);
    expect(items).toHaveLength(1);
    expect(items[0].kind).toBe('movie');
    expect(items[0].files).toHaveLength(2);
  });

  it('keeps unmatched movies solo (no id to cluster on)', () => {
    const items = buildLibraryItems([
      apiToMediaFile(mkFile({ id: 1, media_type: 'movie', status: 'no_match', matches: [] })),
      apiToMediaFile(mkFile({ id: 2, media_type: 'movie', status: 'no_match', matches: [] })),
    ]);
    expect(items).toHaveLength(2);
  });

  it('clusters episodes sharing (provider, id, season) into one series card', () => {
    const items = buildLibraryItems([
      epFile({ id: 1 }, { provider: 'anidb', provider_id: '69', season_number: 1, episode_number: 1, title: 'One Piece' }),
      epFile({ id: 2 }, { provider: 'anidb', provider_id: '69', season_number: 1, episode_number: 2, title: 'One Piece' }),
    ]);
    expect(items).toHaveLength(1);
    expect(items[0].kind).toBe('series');
    expect(items[0].files).toHaveLength(2);
    expect(items[0].episodes).toHaveLength(2);
  });

  it('splits the SAME provider id across seasons into separate cards (Euphoria S1/S2)', () => {
    // Without the |s{season} suffix the popup collapses every season to the
    // first and the rest render as orphan rows.
    const items = buildLibraryItems([
      epFile({ id: 1 }, { provider: 'tvdb', provider_id: '360261', season_number: 1, episode_number: 1, title: 'Euphoria' }),
      epFile({ id: 2 }, { provider: 'tvdb', provider_id: '360261', season_number: 2, episode_number: 1, title: 'Euphoria' }),
    ]);
    expect(items).toHaveLength(2);
  });

  it('groups unmatched files by series_key', () => {
    const items = buildLibraryItems([
      apiToMediaFile(mkFile({ id: 1, media_type: 'tv', status: 'no_match', series_key: 'tv|the show|1|the show' })),
      apiToMediaFile(mkFile({ id: 2, media_type: 'tv', status: 'no_match', series_key: 'tv|the show|1|the show' })),
    ]);
    expect(items).toHaveLength(1);
    expect(items[0].files).toHaveLength(2);
  });

  it('drops fully-unidentified files into solo buckets', () => {
    const items = buildLibraryItems([
      apiToMediaFile(mkFile({ id: 1, media_type: 'tv', status: 'no_match', series_key: null })),
      apiToMediaFile(mkFile({ id: 2, media_type: 'tv', status: 'no_match', series_key: null })),
    ]);
    expect(items).toHaveLength(2);
  });

  it('dedups two release groups of the same episode into one entry, two files', () => {
    // VARYG (S01E16, no absolute) + Moozzi2 (abs=16) are the same episode;
    // keying by season-episode keeps one entry but both files.
    const items = buildLibraryItems([
      epFile({ id: 1 }, { provider: 'anidb', provider_id: '999', season_number: 1, episode_number: 16, title: 'Nana' }),
      apiToMediaFile(mkFile({
        id: 2, media_type: 'anime', parsed_data: { absolute_episode: 16 },
        matches: [mkMatch({ match_type: 'tv_episode', provider: 'anidb', provider_id: '999', season_number: 1, episode_number: 16, title: 'Nana' })],
      })),
    ]);
    expect(items).toHaveLength(1);
    expect(items[0].episodes).toHaveLength(1);
    expect(items[0].files).toHaveLength(2);
  });

  it('gives same-series_key / different-provider-id clusters distinct ids (React key collision)', () => {
    // Multi-cour Bleach S17 spans AIDs 15449/17849 under one series_key;
    // a naive lib_<seriesKey> id collided and React mis-attributed state.
    const items = buildLibraryItems([
      epFile({ id: 1, series_key: 'anime|bleach|17|bleach' }, { provider: 'anidb', provider_id: '15449', season_number: 17, episode_number: 1, title: 'Bleach' }),
      epFile({ id: 2, series_key: 'anime|bleach|17|bleach' }, { provider: 'anidb', provider_id: '17849', season_number: 17, episode_number: 14, title: 'Bleach' }),
    ]);
    expect(items).toHaveLength(2);
    const ids = items.map(i => i.id);
    expect(new Set(ids).size).toBe(2);                 // unique
    expect(ids.every(id => id.startsWith('lib_anime|bleach'))).toBe(true);
  });

  it('keeps a single AniDB AID as ONE card even when its season_number disagrees file-to-file (One Piece AID 69 phantom-duplicate)', () => {
    // One Piece is a single AniDB entry (AID 69) spanning 1100+ episodes. The
    // matcher derives season_number per file from a TVDB cross-ref that can
    // disagree across adjacent episodes (ep 1166 → season 1, ep 1167 → season
    // 23). Splitting the grid cluster on that derived season manufactured a
    // phantom second card AND collided the React key (both clusters reduced to
    // `lib_anime|one piece|23|1999_anidb_69`). The AID alone must define the card.
    const items = buildLibraryItems([
      epFile({ id: 1, series_key: 'anime|one piece|23|one piece' },
        { provider: 'anidb', provider_id: '69', season_number: 23, episode_number: 1156, title: 'One Piece' }),
      epFile({ id: 2, series_key: 'anime|one piece|23|1999' },
        { provider: 'anidb', provider_id: '69', season_number: 23, episode_number: 12, title: 'One Piece' }),
      epFile({ id: 3, series_key: 'anime|one piece|23|1999' },
        { provider: 'anidb', provider_id: '69', season_number: 1, episode_number: 1166, title: 'One Piece' }),
    ]);
    expect(items).toHaveLength(1);
    expect(items[0].files).toHaveLength(3);
    expect(new Set(items.map(i => i.id)).size).toBe(1);
  });

  it('gives same-series_key TVDB clusters that differ only by canonical season distinct ids', () => {
    // The season split is KEPT for TVDB/TMDB (one id legitimately spans
    // seasons — Euphoria). Guard the id against the One Piece failure mode for
    // those providers: parsed season agrees (same series_key) but the matcher's
    // canonical season disagrees → the cluster splits, so the id must too.
    const items = buildLibraryItems([
      epFile({ id: 1, series_key: 'tv|show|1|2020' }, { provider: 'tvdb', provider_id: '500', season_number: 1, episode_number: 1, title: 'Show' }),
      epFile({ id: 2, series_key: 'tv|show|1|2020' }, { provider: 'tvdb', provider_id: '500', season_number: 2, episode_number: 1, title: 'Show' }),
    ]);
    expect(items).toHaveLength(2);
    expect(new Set(items.map(i => i.id)).size).toBe(2);
  });

  it('flags a cluster as noMatch when no file has a real provider id', () => {
    const items = buildLibraryItems([
      apiToMediaFile(mkFile({ id: 1, media_type: 'tv', status: 'no_match', series_key: 'tv|mystery|1|mystery' })),
    ]);
    expect(items[0].noMatch).toBe(true);
  });

  it('does NOT flag noMatch while a file is still matching', () => {
    const items = buildLibraryItems([
      apiToMediaFile(mkFile({ id: 1, media_type: 'tv', status: 'matching', series_key: 'tv|mystery|1|mystery' })),
    ]);
    expect(items[0].noMatch).toBe(false);
    expect(items[0].matchingState).toBe(true);
  });

  it('carries missingSubs through to the per-file LibFile', () => {
    const items = buildLibraryItems([
      epFile({ id: 1, missing_subs: ['en'] }, { provider: 'anidb', provider_id: '69', season_number: 1, episode_number: 1, title: 'One Piece' }),
    ]);
    expect(items[0].files[0].missingSubs).toEqual(['en']);
  });
});

describe('buildLibraryItems — pack clustering (One Pace)', () => {
  const packEp = (id: number, season: number, ep: number) => epFile(
    { id, media_type: 'anime', file_path: `/m/One Pace/s${season}e${ep}.mp4` },
    { provider: 'pack', provider_id: `one-pace:${season}:${ep}`,   // per-EPISODE provider_id
      series_group_id: 'pack:one-pace:abc', season_number: season, episode_number: ep, title: 'One Pace' },
  );

  it('groups one arc into ONE card despite the per-episode provider_id', () => {
    // Romance Dawn = 4 files, same series_group_id + season → ONE card with 4
    // episodes (the bug made 4 "One Pace Part N" cards).
    const items = buildLibraryItems([1, 2, 3, 4].map(ep => packEp(ep, 1, ep)));
    expect(items).toHaveLength(1);
    expect(items[0].episodes).toHaveLength(4);
    expect(items[0].season).toBe(1);
  });

  it('still gives each distinct arc its own card', () => {
    const items = buildLibraryItems([packEp(1, 1, 1), packEp(2, 1, 2), packEp(3, 2, 1)]);
    expect(items).toHaveLength(2);                                  // S1 (2 eps) + S2 (1 ep)
    expect(items.map(i => i.episodes.length).sort()).toEqual([1, 2]);
  });
});

describe('mergeDuplicateClusters — collapse cross-provider duplicate copies', () => {
  // One file's worth of identity — only the fields the merge reads.
  const f = (provider: string, gid: string | null, season: number, episode: number): MediaFile =>
    ({ match: { provider, seriesGroupId: gid, season, episode } } as unknown as MediaFile);
  const cluster = (provider: string, gid: string, eps: number[]): MediaFile[] =>
    eps.map(e => f(provider, gid, 1, e));

  it('merges an AniDB copy + a Sonarr {tvdb-…} copy of the same episodes into ONE cluster', () => {
    const anidb = cluster('anidb', 'g', [1, 2, 3, 4, 5, 6, 7, 8]);
    const tvdb = cluster('tvdb', 'g', [1, 2, 3, 4, 5, 6, 7, 8]);
    const out = mergeDuplicateClusters([anidb, tvdb]);
    expect(out).toHaveLength(1);          // one cover, not two
    expect(out[0]).toHaveLength(16);      // both copies' files → 2 per episode → dedupe procedure
  });

  it('does NOT merge same-provider clusters — AoT seasons / One Piece movies stay separate', () => {
    const aot = [cluster('anidb', 'g', [1, 2]), cluster('anidb', 'g', [1, 2]), cluster('anidb', 'g', [1, 2])];
    const movies = [[f('tmdb', 'c', 1, 1)], [f('tmdb', 'c', 1, 1)]];  // both fabricate S1E1
    expect(mergeDuplicateClusters([...aot, ...movies])).toHaveLength(5);
  });

  it('does NOT merge different franchises or non-overlapping seasons', () => {
    const a = cluster('anidb', 'g1', [1]);
    const b = cluster('tvdb', 'g2', [1]);                  // different franchise
    const c = [f('tvdb', 'g1', 2, 1)];                     // same franchise as a, season 2 → no overlap
    expect(mergeDuplicateClusters([a, b, c])).toHaveLength(3);
  });

  it('merges three copies (anidb + tvdb + tmdb) of one episode into one cluster', () => {
    const out = mergeDuplicateClusters([
      [f('anidb', 'g', 1, 1)], [f('tvdb', 'g', 1, 1)], [f('tmdb', 'g', 1, 1)],
    ]);
    expect(out).toHaveLength(1);
    expect(out[0]).toHaveLength(3);
  });
});
