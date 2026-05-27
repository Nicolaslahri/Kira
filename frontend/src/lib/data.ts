import type {
  MediaFile, HistoryEntry, NamingProfile, PosterData, MediaType,
  SearchResult, ProviderKey, ProviderMeta, NamingToken,
} from './types';

const TINTS: [string, string][] = [
  ['#e54bba', '#ff974b'],
  ['#7200e4', '#e54bba'],
  ['#125dff', '#49b8fe'],
  ['#28d9a0', '#125dff'],
  ['#ff974b', '#db413c'],
  ['#9b18a6', '#7200e4'],
  ['#ffc94a', '#ff974b'],
  ['#0a5d3f', '#28d9a0'],
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

export const FILES: MediaFile[] = [
  {
    id: 'f01',
    filename: 'oppenheimer.2023.imax.2160p.uhd.bluray.x265-pmp.mkv',
    folder: '/media/downloads/Oppenheimer.2023.IMAX.2160p.UHD.BluRay.x265-PMP',
    mediaType: 'movie', status: 'pending', confidence: 97,
    match: {
      title: 'Oppenheimer', year: 2023, tmdbId: 872585, runtime: 180,
      poster: poster('Oppenheimer', 2023),
      overview: 'The story of American scientist J. Robert Oppenheimer and his role in the development of the atomic bomb.',
    },
    candidates: [
      { title: 'Oppenheimer', year: 2023, confidence: 97, poster: poster('Oppenheimer', 2023) },
      { title: 'Oppenheimer: After Trinity', year: 2023, confidence: 41, poster: poster('Oppenheimer After', 2023) },
    ],
  },
  {
    id: 'f02',
    filename: 'Dune.Part.Two.2024.2160p.WEB-DL.DDP5.1.Atmos.HDR.HEVC-CMRG.mkv',
    folder: '/media/downloads',
    mediaType: 'movie', status: 'pending', confidence: 96,
    match: {
      title: 'Dune: Part Two', year: 2024, tmdbId: 693134, runtime: 167,
      poster: poster('Dune Part Two', 2024),
      overview: 'Paul Atreides unites with Chani and the Fremen while seeking revenge against the conspirators.',
    },
    candidates: [
      { title: 'Dune: Part Two', year: 2024, confidence: 96, poster: poster('Dune Part Two', 2024) },
      { title: 'Dune', year: 2021, confidence: 58, poster: poster('Dune', 2021) },
      { title: 'Dune', year: 1984, confidence: 22, poster: poster('Dune Old', 1984) },
    ],
  },
  {
    id: 'f03',
    filename: 'the.bear.s03e01.napkins.1080p.web-dl.h264-flux.mkv',
    folder: '/media/downloads/The.Bear.S03',
    mediaType: 'tv', status: 'pending', confidence: 94,
    match: {
      title: 'The Bear', year: 2022, tmdbId: 136315, season: 3, episode: 1, episodeTitle: 'Tomorrow',
      poster: poster('The Bear', 2022),
      overview: 'Carmen "Carmy" Berzatto returns to Chicago to run his deceased brother\'s sandwich shop.',
    },
    candidates: [
      { title: 'The Bear', year: 2022, season: 3, episode: 1, confidence: 94, poster: poster('The Bear', 2022) },
      { title: 'The Bear', year: 2022, season: 2, episode: 1, confidence: 38, poster: poster('The Bear', 2022) },
    ],
  },
  {
    id: 'f04',
    filename: 'succession.s04e10.with.open.eyes.1080p.amzn.web-dl.mkv',
    folder: '/media/downloads/Succession.S04',
    mediaType: 'tv', status: 'pending', confidence: 99,
    match: {
      title: 'Succession', year: 2018, tmdbId: 76331, season: 4, episode: 10, episodeTitle: 'With Open Eyes',
      poster: poster('Succession', 2018),
      overview: 'The Roy family fights for control of the global media empire.',
    },
    candidates: [
      { title: 'Succession', year: 2018, season: 4, episode: 10, confidence: 99, poster: poster('Succession', 2018) },
    ],
  },
  {
    id: 'f05',
    filename: 'Barbie_2023_1080p_BluRay_x264-RARBG.mp4',
    folder: '/media/downloads',
    mediaType: 'movie', status: 'pending', confidence: 92,
    match: {
      title: 'Barbie', year: 2023, tmdbId: 346698, runtime: 114,
      poster: poster('Barbie', 2023),
      overview: 'Barbie suffers a crisis that leads her to question her world and her existence.',
    },
    candidates: [
      { title: 'Barbie', year: 2023, confidence: 92, poster: poster('Barbie', 2023) },
      { title: 'Barbie: Princess Charm School', year: 2011, confidence: 31, poster: poster('Barbie Princess', 2011) },
    ],
  },
  {
    id: 'f06',
    filename: 'severance.s02e07.chikhai.bardo.2160p.atvp.web-dl.mkv',
    folder: '/media/downloads/Severance.S02',
    mediaType: 'tv', status: 'pending', confidence: 95,
    match: {
      title: 'Severance', year: 2022, tmdbId: 95396, season: 2, episode: 7, episodeTitle: 'Chikhai Bardo',
      poster: poster('Severance', 2022),
      overview: 'Mark leads a team whose memories have been surgically divided between work and personal lives.',
    },
    candidates: [
      { title: 'Severance', year: 2022, season: 2, episode: 7, confidence: 95, poster: poster('Severance', 2022) },
      { title: 'Severance', year: 2022, season: 1, episode: 7, confidence: 44, poster: poster('Severance', 2022) },
    ],
  },
  {
    id: 'f07',
    filename: 'shogun.2024.s01e10.a.dream.of.a.dream.1080p.dsnp.mkv',
    folder: '/media/downloads/Shogun.S01',
    mediaType: 'tv', status: 'pending', confidence: 91,
    match: {
      title: 'Shōgun', year: 2024, tmdbId: 202555, season: 1, episode: 10, episodeTitle: 'A Dream of a Dream',
      poster: poster('Shogun', 2024),
      overview: 'In the year 1600, on the eve of a Japanese civil war, a Western navigator finds himself entangled.',
    },
    candidates: [
      { title: 'Shōgun', year: 2024, season: 1, episode: 10, confidence: 91, poster: poster('Shogun', 2024) },
      { title: 'Shōgun', year: 1980, season: 1, episode: 5, confidence: 47, poster: poster('Shogun Old', 1980) },
    ],
  },
  {
    id: 'f08',
    filename: 'fallout.s01e01.the.end.2160p.amzn.web-dl.mkv',
    folder: '/media/downloads/Fallout.S01',
    mediaType: 'tv', status: 'approved', confidence: 98,
    match: {
      title: 'Fallout', year: 2024, tmdbId: 106379, season: 1, episode: 1, episodeTitle: 'The End',
      poster: poster('Fallout', 2024),
      overview: 'A peaceful denizen of a fallout shelter is forced onto the surface 200 years after the apocalypse.',
    },
    candidates: [
      { title: 'Fallout', year: 2024, season: 1, episode: 1, confidence: 98, poster: poster('Fallout', 2024) },
    ],
  },
  {
    id: 'f09',
    filename: 'the.last.of.us.s02e01.future.days.hdr.2160p.web.mkv',
    folder: '/media/downloads/TLOU.S02',
    mediaType: 'tv', status: 'pending', confidence: 88,
    match: {
      title: 'The Last of Us', year: 2023, tmdbId: 100088, season: 2, episode: 1, episodeTitle: 'Future Days',
      poster: poster('The Last of Us', 2023),
      overview: 'Twenty years after a modern civilization has been destroyed, Joel and Ellie must survive.',
    },
    candidates: [
      { title: 'The Last of Us', year: 2023, season: 2, episode: 1, confidence: 88, poster: poster('The Last of Us', 2023) },
      { title: 'The Last of Us', year: 2023, season: 1, episode: 1, confidence: 51, poster: poster('The Last of Us', 2023) },
    ],
  },
  {
    id: 'f10',
    filename: 'Avatar.The.Way.of.Water.2022.UHD.BluRay.2160p.HDR.x265.mkv',
    folder: '/media/downloads',
    mediaType: 'movie', status: 'pending', confidence: 96,
    match: {
      title: 'Avatar: The Way of Water', year: 2022, tmdbId: 76600, runtime: 192,
      poster: poster('Avatar Way of Water', 2022),
      overview: 'Jake Sully lives with his newfound family formed on the planet of Pandora.',
    },
    candidates: [
      { title: 'Avatar: The Way of Water', year: 2022, confidence: 96, poster: poster('Avatar Way of Water', 2022) },
      { title: 'Avatar', year: 2009, confidence: 64, poster: poster('Avatar', 2009) },
    ],
  },
  {
    id: 'f11',
    filename: 'andor.s02e01.welcome.to.the.rebellion.1080p.mkv',
    folder: '/media/downloads/Andor.S02',
    mediaType: 'tv', status: 'pending', confidence: 89,
    match: {
      title: 'Andor', year: 2022, tmdbId: 83867, season: 2, episode: 1, episodeTitle: 'Welcome to the Rebellion',
      poster: poster('Andor', 2022),
      overview: 'The tale of the burgeoning rebellion against the Empire and how people and planets became involved.',
    },
    candidates: [
      { title: 'Andor', year: 2022, season: 2, episode: 1, confidence: 89, poster: poster('Andor', 2022) },
      { title: 'Andor', year: 2022, season: 1, episode: 1, confidence: 36, poster: poster('Andor', 2022) },
    ],
  },
  {
    id: 'f12',
    filename: 'past.lives.2023.LIMITED.1080p.BluRay.X264-AMIABLE.mkv',
    folder: '/media/downloads',
    mediaType: 'movie', status: 'pending', confidence: 90,
    match: {
      title: 'Past Lives', year: 2023, tmdbId: 666277, runtime: 105,
      poster: poster('Past Lives', 2023),
      overview: 'Nora and Hae Sung, two deeply connected childhood friends, are wrested apart after Nora\'s family emigrates.',
    },
    candidates: [
      { title: 'Past Lives', year: 2023, confidence: 90, poster: poster('Past Lives', 2023) },
      { title: 'Past Life', year: 2016, confidence: 33, poster: poster('Past Life', 2016) },
    ],
  },
  {
    id: 'f13',
    filename: 'Wicked_Part_One_2024.mkv',
    folder: '/media/downloads',
    mediaType: 'movie', status: 'pending', confidence: 71,
    match: {
      title: 'Wicked', year: 2024, tmdbId: 402431, runtime: 161,
      poster: poster('Wicked', 2024),
      overview: 'Elphaba, an ostracized but defiant girl born with green skin, and Galinda, a privileged girl, become unlikely friends.',
    },
    candidates: [
      { title: 'Wicked', year: 2024, confidence: 71, poster: poster('Wicked', 2024) },
      { title: 'Wicked Little Letters', year: 2023, confidence: 52, poster: poster('Wicked Letters', 2023) },
      { title: 'Wicked', year: 2013, confidence: 28, poster: poster('Wicked Old', 2013) },
    ],
  },
  {
    id: 'f14',
    filename: 'perfect.days.2023.mkv',
    folder: '/media/downloads',
    mediaType: 'movie', status: 'pending', confidence: 68,
    match: {
      title: 'Perfect Days', year: 2023, tmdbId: 1066262, runtime: 124,
      poster: poster('Perfect Days', 2023),
      overview: 'Hirayama is a toilet cleaner in Tokyo who is content with his quiet, structured life.',
    },
    candidates: [
      { title: 'Perfect Days', year: 2023, confidence: 68, poster: poster('Perfect Days', 2023) },
      { title: 'These Days', year: 2023, confidence: 41, poster: poster('These Days', 2023) },
    ],
  },
  {
    id: 'f15',
    filename: 'house.of.dragon.s02e01.1080p.mkv',
    folder: '/media/downloads/HOTD.S02',
    mediaType: 'tv', status: 'pending', confidence: 76,
    match: {
      title: 'House of the Dragon', year: 2022, tmdbId: 94997, season: 2, episode: 1, episodeTitle: 'A Son for a Son',
      poster: poster('House of the Dragon', 2022),
      overview: 'The Targaryen dynasty is at the absolute apex of its power, 200 years before Game of Thrones.',
    },
    candidates: [
      { title: 'House of the Dragon', year: 2022, season: 2, episode: 1, confidence: 76, poster: poster('House of the Dragon', 2022) },
      { title: 'House of the Dragon', year: 2022, season: 1, episode: 1, confidence: 60, poster: poster('House of the Dragon', 2022) },
    ],
  },
  {
    id: 'f16',
    filename: 'Anora.2024.HDR.HEVC-RELEASE.mkv',
    folder: '/media/downloads',
    mediaType: 'movie', status: 'pending', confidence: 64,
    match: {
      title: 'Anora', year: 2024, tmdbId: 1064213, runtime: 139,
      poster: poster('Anora', 2024),
      overview: 'A young sex worker from Brooklyn gets her chance at a Cinderella story when she meets the son of an oligarch.',
    },
    candidates: [
      { title: 'Anora', year: 2024, confidence: 64, poster: poster('Anora', 2024) },
      { title: 'Aurora', year: 2022, confidence: 49, poster: poster('Aurora', 2022) },
    ],
  },
  {
    id: 'f17',
    filename: 'movie_final_v3 (1).mkv',
    folder: '/media/downloads/random',
    mediaType: 'movie', status: 'pending', confidence: 0,
    match: null,
    candidates: [],
  },
  {
    id: 'f18',
    filename: 'untitled.s01e04.mkv',
    folder: '/media/downloads/random',
    mediaType: 'tv', status: 'pending', confidence: 18,
    match: { title: 'Untitled Pilot Project', year: null, tmdbId: null, poster: poster('Untitled', null), overview: '' },
    candidates: [
      { title: 'Untitled Pilot Project', year: null, season: 1, episode: 4, confidence: 18, poster: poster('Untitled', null) },
      { title: 'The Untitled', year: 2009, confidence: 14, poster: poster('Untitled', 2009) },
    ],
  },
  {
    id: 'f19',
    filename: 'IMG_9482.mov',
    folder: '/media/downloads/random',
    mediaType: 'movie', status: 'pending', confidence: 0,
    match: null,
    candidates: [],
  },
  {
    id: 'f20',
    filename: 'inception_2010_bluray_1080p_dts-hd_ma-amiable.mkv',
    folder: '/media/downloads',
    mediaType: 'movie', status: 'approved', confidence: 99,
    match: {
      title: 'Inception', year: 2010, tmdbId: 27205, runtime: 148,
      poster: poster('Inception', 2010),
      overview: 'Cobb steals secrets from within the subconscious during the dream state.',
    },
    candidates: [
      { title: 'Inception', year: 2010, confidence: 99, poster: poster('Inception', 2010) },
    ],
  },
  {
    id: 'f21',
    filename: 'mad.max.fury.road.2015.uhd.bluray.x265.mkv',
    folder: '/media/downloads',
    mediaType: 'movie', status: 'pending', confidence: 93,
    match: {
      title: 'Mad Max: Fury Road', year: 2015, tmdbId: 76341, runtime: 120,
      poster: poster('Mad Max Fury Road', 2015),
      overview: 'In a post-apocalyptic wasteland, Max teams up with a rebel to flee from a tyrannical warlord.',
    },
    candidates: [
      { title: 'Mad Max: Fury Road', year: 2015, confidence: 93, poster: poster('Mad Max Fury Road', 2015) },
      { title: 'Furiosa: A Mad Max Saga', year: 2024, confidence: 51, poster: poster('Furiosa', 2024) },
    ],
  },

  // ANIME — absolute episode numbers + release-group tags
  {
    id: 'a01',
    filename: "[SubsPlease] Frieren - Beyond Journey's End - 28 (1080p) [F2A7B3D9].mkv",
    folder: '/media/downloads/anime',
    mediaType: 'anime', status: 'pending', confidence: 96,
    releaseGroup: 'SubsPlease',
    match: {
      title: "Frieren: Beyond Journey's End",
      titleRomaji: 'Sousou no Frieren',
      year: 2023, anidbId: 17075,
      season: 1, episode: 28, absoluteEpisode: 28,
      episodeTitle: 'A Bird Cage of Silver',
      poster: poster('Frieren', 2023),
      overview: 'An elven mage who outlived her party reflects on her journey decades after defeating the Demon King.',
    },
    candidates: [
      { title: "Frieren: Beyond Journey's End", year: 2023, confidence: 96, season: 1, episode: 28, poster: poster('Frieren', 2023) },
      { title: 'Frieren', year: 2023, confidence: 71, season: 1, episode: 28, poster: poster('Frieren', 2023) },
    ],
  },
  {
    id: 'a02',
    filename: '[Erai-raws] Jujutsu Kaisen - 47 [1080p][HEVC][Multiple Subtitle].mkv',
    folder: '/media/downloads/anime',
    mediaType: 'anime', status: 'pending', confidence: 92,
    releaseGroup: 'Erai-raws',
    match: {
      title: 'Jujutsu Kaisen',
      titleRomaji: 'Jujutsu Kaisen',
      year: 2020, anidbId: 15291,
      season: 2, episode: 23, absoluteEpisode: 47,
      episodeTitle: 'Hidden Inventory, Premature Death',
      poster: poster('Jujutsu Kaisen', 2020),
      overview: 'Yuji Itadori swallows a cursed object and becomes a vessel for a powerful curse.',
    },
    candidates: [
      { title: 'Jujutsu Kaisen', year: 2020, confidence: 92, season: 2, episode: 23, absoluteEpisode: 47, poster: poster('Jujutsu Kaisen', 2020) },
      { title: 'Jujutsu Kaisen 0', year: 2021, confidence: 38, poster: poster('Jujutsu Kaisen 0', 2021) },
    ],
  },
  {
    id: 'a03',
    filename: '[ASW] Spy x Family - 25 [1080p HEVC][C9D2F1A4].mkv',
    folder: '/media/downloads/anime',
    mediaType: 'anime', status: 'pending', confidence: 89,
    releaseGroup: 'ASW',
    match: {
      title: 'Spy × Family',
      titleRomaji: 'Spy x Family',
      year: 2022, anidbId: 16429,
      season: 2, episode: 13, absoluteEpisode: 25,
      episodeTitle: 'A New Family Member',
      poster: poster('Spy Family', 2022),
      overview: 'A spy, an assassin, and a telepath form a fake family to maintain world peace.',
    },
    candidates: [
      { title: 'Spy × Family', year: 2022, confidence: 89, season: 2, episode: 13, absoluteEpisode: 25, poster: poster('Spy Family', 2022) },
    ],
  },
  {
    id: 'a04',
    filename: '[HorribleSubs] Demon Slayer - 11 [1080p][hardsub].mkv',
    folder: '/media/downloads/anime',
    mediaType: 'anime', status: 'pending', confidence: 64,
    releaseGroup: 'HorribleSubs',
    match: {
      title: 'Demon Slayer: Kimetsu no Yaiba',
      titleRomaji: 'Kimetsu no Yaiba',
      year: 2019, anidbId: 14397,
      season: 1, episode: 11, absoluteEpisode: 11,
      episodeTitle: 'Tsuzumi Mansion',
      poster: poster('Demon Slayer', 2019),
      overview: 'A young boy becomes a demon slayer to save his sister, who has been turned into a demon.',
    },
    candidates: [
      { title: 'Demon Slayer: Kimetsu no Yaiba', year: 2019, confidence: 64, poster: poster('Demon Slayer', 2019) },
      { title: 'Demon Slayer: Mugen Train', year: 2020, confidence: 41, poster: poster('Mugen Train', 2020) },
      { title: 'Demon Slayer: Swordsmith Village Arc', year: 2023, confidence: 28, poster: poster('Swordsmith', 2023) },
    ],
  },

  // MUSIC — Artist/Album/Track structure, square album art
  {
    id: 'm01',
    filename: '03 - Black Star.flac',
    folder: '/media/downloads/music/Radiohead - OK Computer',
    mediaType: 'music', status: 'pending', confidence: 97,
    match: {
      artist: 'Radiohead', album: 'OK Computer',
      track: 3, trackTitle: 'Subterranean Homesick Alien',
      year: 1997, albumYear: 1997, mbid: '0b6b3c7a-2c1a-4f8b-9d4d-b7f1f2a3e4d5',
      acoustidMatch: true, acoustidConfidence: 99,
      duration: '4:27', totalTracks: 12,
      art: poster('Radiohead OK Computer', 1997),
      genre: 'Alternative Rock',
    },
    candidates: [
      { artist: 'Radiohead', album: 'OK Computer', track: 3, trackTitle: 'Subterranean Homesick Alien', year: 1997, confidence: 97, art: poster('Radiohead OK Computer', 1997) },
      { artist: 'Radiohead', album: 'OK Computer OKNOTOK', track: 3, trackTitle: 'Subterranean Homesick Alien', year: 2017, confidence: 71, art: poster('OKNOTOK', 2017) },
    ],
  },
  {
    id: 'm02',
    filename: 'fleetwood_mac_-_rumours_-_05_-_go_your_own_way.mp3',
    folder: '/media/downloads/music',
    mediaType: 'music', status: 'pending', confidence: 94,
    match: {
      artist: 'Fleetwood Mac', album: 'Rumours',
      track: 5, trackTitle: 'Go Your Own Way',
      year: 1977, albumYear: 1977, mbid: '8a0f5e2b-3c7d-4a1f-9e8c-1d2b3f4e5a6b',
      acoustidMatch: true, acoustidConfidence: 96,
      duration: '3:38', totalTracks: 11,
      art: poster('Fleetwood Mac Rumours', 1977),
      genre: 'Rock',
    },
    candidates: [
      { artist: 'Fleetwood Mac', album: 'Rumours', track: 5, trackTitle: 'Go Your Own Way', year: 1977, confidence: 94, art: poster('Fleetwood Mac Rumours', 1977) },
      { artist: 'Fleetwood Mac', album: 'Greatest Hits', track: 4, trackTitle: 'Go Your Own Way', year: 1988, confidence: 68, art: poster('Fleetwood Hits', 1988) },
    ],
  },
  {
    id: 'm03',
    filename: 'Track 07.m4a',
    folder: '/media/downloads/music/_unknown_/the_dark_side_of_the_moon',
    mediaType: 'music', status: 'pending', confidence: 88,
    match: {
      artist: 'Pink Floyd', album: 'The Dark Side of the Moon',
      track: 7, trackTitle: 'Money',
      year: 1973, albumYear: 1973, mbid: 'a1b2c3d4-5e6f-7a8b-9c0d-1e2f3a4b5c6d',
      acoustidMatch: true, acoustidConfidence: 91,
      duration: '6:23', totalTracks: 10,
      art: poster('Pink Floyd Dark Side', 1973),
      genre: 'Progressive Rock',
    },
    candidates: [
      { artist: 'Pink Floyd', album: 'The Dark Side of the Moon', track: 7, trackTitle: 'Money', year: 1973, confidence: 88, art: poster('Pink Floyd Dark Side', 1973) },
      { artist: 'Pink Floyd', album: 'Echoes: The Best of Pink Floyd', track: 9, trackTitle: 'Money', year: 2001, confidence: 52, art: poster('Pink Floyd Echoes', 2001) },
      { artist: 'Various Artists', album: 'Rock Classics', track: 4, trackTitle: 'Money', year: 2010, confidence: 23, art: poster('Rock Classics', 2010) },
    ],
  },
  {
    id: 'm04',
    filename: 'kendrick_lamar-good_kid_maad_city-09-money_trees_feat_jay_rock.flac',
    folder: '/media/downloads/music',
    mediaType: 'music', status: 'pending', confidence: 95,
    match: {
      artist: 'Kendrick Lamar', album: 'good kid, m.A.A.d city',
      track: 9, trackTitle: 'Money Trees (feat. Jay Rock)',
      year: 2012, albumYear: 2012, mbid: 'b3c4d5e6-7f8a-9b0c-1d2e-3f4a5b6c7d8e',
      acoustidMatch: true, acoustidConfidence: 98,
      duration: '6:26', totalTracks: 12,
      art: poster('Kendrick GKMC', 2012),
      genre: 'Hip-Hop',
    },
    candidates: [
      { artist: 'Kendrick Lamar', album: 'good kid, m.A.A.d city', track: 9, trackTitle: 'Money Trees', year: 2012, confidence: 95, art: poster('Kendrick GKMC', 2012) },
    ],
  },
  {
    id: 'm05',
    filename: '01_intro.mp3',
    folder: '/media/downloads/music/random',
    mediaType: 'music', status: 'pending', confidence: 22,
    match: null,
    candidates: [],
  },
];

export const HISTORY: HistoryEntry[] = [
  { id: 'h1', when: '2 min ago', mediaType: 'tv', poster: poster('Fallout', 2024),
    title: 'Fallout S01E01 — The End', op: 'Hardlink',
    from: '/media/downloads/Fallout.S01/fallout.s01e01.the.end.2160p.amzn.web-dl.mkv',
    to: '/media/library/TV/Fallout (2024)/Season 01/Fallout - S01E01 - The End [2160p WEB-DL].mkv' },
  { id: 'h2', when: '8 min ago', mediaType: 'movie', poster: poster('Inception', 2010),
    title: 'Inception (2010)', op: 'Move',
    from: '/media/downloads/inception_2010_bluray_1080p_dts-hd_ma-amiable.mkv',
    to: '/media/library/Movies/Inception (2010)/Inception (2010) [1080p BluRay].mkv' },
  { id: 'h3', when: '14 min ago', mediaType: 'tv', poster: poster('Severance', 2022),
    title: 'Severance S02E06 — Attila', op: 'Hardlink',
    from: '/media/downloads/Severance.S02/severance.s02e06.2160p.atvp.web-dl.mkv',
    to: '/media/library/TV/Severance (2022)/Season 02/Severance - S02E06 - Attila [2160p WEB-DL].mkv' },
  { id: 'h4', when: '22 min ago', mediaType: 'tv', poster: poster('Severance', 2022),
    title: "Severance S02E05 — Trojan's Horse", op: 'Hardlink',
    from: '/media/downloads/Severance.S02/severance.s02e05.2160p.atvp.web-dl.mkv',
    to: "/media/library/TV/Severance (2022)/Season 02/Severance - S02E05 - Trojan's Horse [2160p WEB-DL].mkv" },
  { id: 'h5', when: '1 hr ago', mediaType: 'movie', poster: poster('Mad Max Fury Road', 2015),
    title: 'Mad Max: Fury Road (2015)', op: 'Symlink',
    from: '/media/downloads/mad.max.fury.road.2015.bluray.x265.mkv',
    to: '/media/library/Movies/Mad Max Fury Road (2015)/Mad Max Fury Road (2015) [1080p BluRay].mkv' },
  { id: 'h6', when: '1 hr ago', mediaType: 'tv', poster: poster('The Bear', 2022),
    title: 'The Bear S02E10 — The Bear', op: 'Hardlink',
    from: '/media/downloads/The.Bear.S02/the.bear.s02e10.the.bear.1080p.mkv',
    to: '/media/library/TV/The Bear (2022)/Season 02/The Bear - S02E10 - The Bear [1080p WEB-DL].mkv' },
  { id: 'h7', when: '3 hrs ago', mediaType: 'movie', poster: poster('Past Lives', 2023),
    title: 'Past Lives (2023)', op: 'Move',
    from: '/media/downloads/past.lives.2023.bluray.x264.mkv',
    to: '/media/library/Movies/Past Lives (2023)/Past Lives (2023) [1080p BluRay].mkv' },
  { id: 'h8', when: 'Yesterday, 21:14', mediaType: 'tv', poster: poster('Shogun', 2024),
    title: 'Shōgun S01E09 — Crimson Sky', op: 'Hardlink',
    from: '/media/downloads/Shogun.S01/shogun.s01e09.crimson.sky.1080p.dsnp.mkv',
    to: '/media/library/TV/Shogun (2024)/Season 01/Shogun - S01E09 - Crimson Sky [1080p WEB-DL].mkv' },
];

export const SEARCH_BY_PROVIDER: Record<ProviderKey, SearchResult[]> = {
  TMDB: [
    { title: 'Dune: Part Two', year: 2024, mediaType: 'movie', poster: poster('Dune Part Two', 2024), overview: 'Paul Atreides unites with the Fremen.', votes: 8.3, tmdbId: 693134 },
    { title: 'Oppenheimer', year: 2023, mediaType: 'movie', poster: poster('Oppenheimer', 2023), overview: 'The story of J. Robert Oppenheimer.', votes: 8.1, tmdbId: 872585 },
    { title: 'The Bear', year: 2022, mediaType: 'tv', poster: poster('The Bear', 2022), overview: 'A young chef returns to Chicago.', votes: 8.6, tmdbId: 136315 },
    { title: 'Severance', year: 2022, mediaType: 'tv', poster: poster('Severance', 2022), overview: 'Memories surgically divided.', votes: 8.4, tmdbId: 95396 },
    { title: 'Shōgun', year: 2024, mediaType: 'tv', poster: poster('Shogun', 2024), overview: '1600 Japan, a Western navigator.', votes: 8.5, tmdbId: 202555 },
    { title: 'Fallout', year: 2024, mediaType: 'tv', poster: poster('Fallout', 2024), overview: 'A vault dweller surfaces.', votes: 8.4, tmdbId: 106379 },
    { title: 'Past Lives', year: 2023, mediaType: 'movie', poster: poster('Past Lives', 2023), overview: 'Childhood friends, decades apart.', votes: 7.8, tmdbId: 666277 },
    { title: 'Anora', year: 2024, mediaType: 'movie', poster: poster('Anora', 2024), overview: 'Cinderella with an oligarch.', votes: 7.5, tmdbId: 1064213 },
  ],
  TVDB: [
    { title: 'Severance', year: 2022, mediaType: 'tv', poster: poster('Severance', 2022), overview: 'TVDB-verified · 2 seasons · 19 episodes', tvdbId: 371980, eps: 19 },
    { title: 'Shōgun', year: 2024, mediaType: 'tv', poster: poster('Shogun', 2024), overview: 'TVDB-verified · 1 season · 10 episodes', tvdbId: 412115, eps: 10 },
    { title: 'The Last of Us', year: 2023, mediaType: 'tv', poster: poster('The Last of Us', 2023), overview: 'TVDB-verified · 2 seasons · 16 episodes', tvdbId: 392256, eps: 16 },
    { title: 'House of the Dragon', year: 2022, mediaType: 'tv', poster: poster('House of the Dragon', 2022), overview: 'TVDB-verified · 2 seasons · 18 episodes', tvdbId: 371572, eps: 18 },
    { title: 'Fallout', year: 2024, mediaType: 'tv', poster: poster('Fallout', 2024), overview: 'TVDB-verified · 1 season · 8 episodes', tvdbId: 416488, eps: 8 },
    { title: 'Andor', year: 2022, mediaType: 'tv', poster: poster('Andor', 2022), overview: 'TVDB-verified · 2 seasons · 24 episodes', tvdbId: 389236, eps: 24 },
  ],
  AniDB: [
    { title: "Frieren: Beyond Journey's End", titleRomaji: 'Sousou no Frieren', year: 2023, mediaType: 'anime',
      poster: poster('Frieren', 2023), overview: 'TV series · 28 episodes · Madhouse', anidbId: 17075, eps: 28, studio: 'Madhouse' },
    { title: 'Jujutsu Kaisen', titleRomaji: 'Jujutsu Kaisen', year: 2020, mediaType: 'anime',
      poster: poster('Jujutsu Kaisen', 2020), overview: 'TV series · 47 episodes · MAPPA', anidbId: 15291, eps: 47, studio: 'MAPPA' },
    { title: 'Demon Slayer: Kimetsu no Yaiba', titleRomaji: 'Kimetsu no Yaiba', year: 2019, mediaType: 'anime',
      poster: poster('Demon Slayer', 2019), overview: 'TV series · 55 episodes · ufotable', anidbId: 14397, eps: 55, studio: 'ufotable' },
    { title: 'Spy × Family', titleRomaji: 'Spy x Family', year: 2022, mediaType: 'anime',
      poster: poster('Spy Family', 2022), overview: 'TV series · 25 episodes · WIT/CloverWorks', anidbId: 16429, eps: 25, studio: 'WIT × CloverWorks' },
    { title: 'Attack on Titan: The Final Season', titleRomaji: 'Shingeki no Kyojin', year: 2020, mediaType: 'anime',
      poster: poster('Attack on Titan', 2020), overview: 'TV series · 87 episodes · MAPPA', anidbId: 15441, eps: 87, studio: 'MAPPA' },
    { title: 'Chainsaw Man', titleRomaji: 'Chainsaw Man', year: 2022, mediaType: 'anime',
      poster: poster('Chainsaw Man', 2022), overview: 'TV series · 12 episodes · MAPPA', anidbId: 16782, eps: 12, studio: 'MAPPA' },
  ],
  MusicBrainz: [
    { artist: 'Radiohead', album: 'OK Computer', year: 1997, mediaType: 'music',
      art: poster('Radiohead OK Computer', 1997), overview: '12 tracks · Alternative Rock · 53:21', mbid: '0b6b3c7a', tracks: 12 },
    { artist: 'Fleetwood Mac', album: 'Rumours', year: 1977, mediaType: 'music',
      art: poster('Fleetwood Mac Rumours', 1977), overview: '11 tracks · Rock · 39:37', mbid: '8a0f5e2b', tracks: 11 },
    { artist: 'Pink Floyd', album: 'The Dark Side of the Moon', year: 1973, mediaType: 'music',
      art: poster('Pink Floyd Dark Side', 1973), overview: '10 tracks · Progressive Rock · 42:49', mbid: 'a1b2c3d4', tracks: 10 },
    { artist: 'Kendrick Lamar', album: 'good kid, m.A.A.d city', year: 2012, mediaType: 'music',
      art: poster('Kendrick GKMC', 2012), overview: '12 tracks · Hip-Hop · 68:30', mbid: 'b3c4d5e6', tracks: 12 },
    { artist: 'Frank Ocean', album: 'Blonde', year: 2016, mediaType: 'music',
      art: poster('Frank Ocean Blonde', 2016), overview: '17 tracks · R&B · 60:08', mbid: 'c4d5e6f7', tracks: 17 },
    { artist: 'Daft Punk', album: 'Discovery', year: 2001, mediaType: 'music',
      art: poster('Daft Punk Discovery', 2001), overview: '14 tracks · Electronic · 60:46', mbid: 'd5e6f7a8', tracks: 14 },
  ],
  AcoustID: [],
};

export const SEARCH_DEMO: Record<string, SearchResult[]> = {
  '': SEARCH_BY_PROVIDER.TMDB,
  'dune': SEARCH_BY_PROVIDER.TMDB,
};

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

export const NAMING_TOKENS: Record<MediaType, NamingToken[]> = {
  movie: [
    { k: '{n}', d: 'Title' }, { k: '{y}', d: 'Year' }, { k: '{q}', d: 'Quality tag' }, { k: '{x}', d: 'Extension' },
  ],
  tv: [
    { k: '{n}', d: 'Series' }, { k: '{y}', d: 'First-air year' }, { k: '{s2}', d: 'Season (00)' },
    { k: '{e2}', d: 'Episode (00)' }, { k: '{t}', d: 'Episode title' }, { k: '{q}', d: 'Quality' },
  ],
  anime: [
    { k: '{n}', d: 'Series' }, { k: '{s2}', d: 'Season (00)' }, { k: '{e2}', d: 'Episode (00)' },
    { k: '{abs}', d: 'Absolute ep' }, { k: '{t}', d: 'Episode title' }, { k: '{rg}', d: 'Release group' },
  ],
  music: [
    { k: '{artist}', d: 'Artist' }, { k: '{album}', d: 'Album' }, { k: '{y}', d: 'Album year' },
    { k: '{tn}', d: 'Track # (02)' }, { k: '{title}', d: 'Track title' },
  ],
};

export const PROVIDERS: Record<ProviderKey, ProviderMeta> = {
  TMDB:        { name: 'TMDB',        for: ['movie', 'tv'],    color: '#90cea1', icon: 'film',
                 desc: 'Movies and TV series · the gold standard for English-language libraries' },
  TVDB:        { name: 'TheTVDB',     for: ['tv', 'anime'],    color: '#6ec1ff', icon: 'tv',
                 desc: 'Deep TV metadata with strong support for absolute episode numbering' },
  AniDB:       { name: 'AniDB',       for: ['anime'],          color: '#c89bff', icon: 'anime',
                 desc: 'The canonical source for anime — episodes, groups, alternate titles' },
  MusicBrainz: { name: 'MusicBrainz', for: ['music'],          color: '#ffb14a', icon: 'disc',
                 desc: 'Open music encyclopedia · artists, releases, recordings' },
  AcoustID:    { name: 'AcoustID',    for: ['music'],          color: '#28d9a0', icon: 'waveform',
                 desc: 'Audio fingerprint matching for music files with missing or wrong tags' },
};

export const TYPE_COLOR: Record<MediaType, string> = {
  movie: 'var(--ink-3)',
  tv:    'var(--info)',
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
