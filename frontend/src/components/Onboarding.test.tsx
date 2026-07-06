// Walks the full 8-step onboarding in jsdom and asserts the settings payload
// complete() persists — the wizard is the only writer of several keys
// (watch.config, scanning.scheduled, subtitles.auto_fetch…) so a silently
// dropped write here means a feature the user opted into never turns on.
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { Onboarding } from './Onboarding';

vi.mock('./FfmpegStatus', () => ({ FfmpegStatusRow: () => null }));
vi.mock('./FolderPickerModal', () => ({ FolderPickerModal: () => null }));
vi.mock('./LoginGate', () => ({ useScrollLock: () => {} }));
vi.mock('../lib/api', () => ({
  api: {
    health: vi.fn(),
    getSettings: vi.fn(),
    testProvider: vi.fn(),
    listFolders: vi.fn(),
    putSettings: vi.fn(),
    testSonarr: vi.fn(),
    testRadarr: vi.fn(),
  },
}));

import { api } from '../lib/api';

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  mocked.health.mockResolvedValue({ status: 'ok', version: '0.5.0' });
  mocked.getSettings.mockResolvedValue({});
  mocked.listFolders.mockResolvedValue({ folders: [] });
  mocked.putSettings.mockResolvedValue({});
});

/** Advance past the current step and wait for the next step's title. */
async function continueTo(title: RegExp) {
  fireEvent.click(screen.getByRole('button', { name: /continue/i }));
  await screen.findByText(title, undefined, { timeout: 3000 });
}

describe('Onboarding', () => {
  it('walks all 8 steps and persists every collected setting', async () => {
    const onComplete = vi.fn();
    render(<Onboarding onComplete={onComplete} />);

    // ── Welcome → Library ──
    fireEvent.click(await screen.findByRole('button', { name: /get started/i }));
    await screen.findByText(/What's in your library\?/, undefined, { timeout: 3000 });

    // Deselect Movies (makes TMDB optional), add Anime.
    fireEvent.click(screen.getByRole('checkbox', { name: /^Movies/ }));
    fireEvent.click(screen.getByRole('checkbox', { name: /^Anime/ }));

    // ── Library → Connect (TMDB not required without movies) ──
    await continueTo(/Connect your metadata/);

    // ── Connect → Folder ──
    await continueTo(/Where's your media\?/);

    // New scanning opt-ins live here.
    fireEvent.click(screen.getByRole('checkbox', { name: /full rescan every night/i }));
    expect(screen.getByLabelText(/nightly rescan time/i)).toHaveValue('03:00');
    fireEvent.click(screen.getByRole('checkbox', { name: /read technical metadata/i }));

    // ── Folder → Handling (validates the folder first) ──
    await continueTo(/How should files be placed\?/);
    expect(mocked.listFolders).toHaveBeenCalledWith('/media');

    fireEvent.click(screen.getByRole('checkbox', { name: /auto-approve confident matches/i }));

    // ── Handling → Naming ──
    await continueTo(/Pick a naming style/);
    fireEvent.click(screen.getByRole('checkbox', { name: /write \.nfo metadata files/i }));
    fireEvent.click(screen.getByRole('checkbox', { name: /download artwork/i }));

    // ── Naming → Subtitles ──
    await continueTo(/Subtitles, handled for you/);
    // Auto-fetch defaults ON for a fresh server.
    expect(screen.getByRole('checkbox', { name: /fetch missing subtitles automatically/i })).toBeChecked();
    // English preselected and un-removable as the last language; add Japanese.
    expect(screen.getByRole('checkbox', { name: 'English' })).toHaveAttribute('aria-checked', 'true');
    fireEvent.click(screen.getByRole('checkbox', { name: 'Japanese' }));

    // ── Subtitles → Integrations ──
    await continueTo(/Plug into your stack/);
    fireEvent.change(screen.getByLabelText('Sonarr URL'), { target: { value: 'http://sonarr:8989' } });
    fireEvent.change(screen.getByLabelText('Sonarr API key'), { target: { value: 'abc123' } });
    // Half-filled Radarr must be IGNORED by complete(), not saved as url-only.
    fireEvent.change(screen.getByLabelText('Radarr URL'), { target: { value: 'http://radarr:7878' } });

    // ── Integrations → Launch ──
    await continueTo(/You're all set\./);
    expect(screen.getByText(/Auto-fetch on · EN · JA/)).toBeInTheDocument();
    expect(screen.getByText('Sonarr')).toBeInTheDocument();
    expect(screen.getByText(/rescan 03:00/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /start first scan/i }));
    await waitFor(() => expect(onComplete).toHaveBeenCalled(), { timeout: 3000 });

    const payload = mocked.putSettings.mock.calls[0][0] as Record<string, unknown>;
    expect(payload['paths.library_root']).toBe('/media');
    expect(payload['onboarding.completed']).toBe(true);
    expect(payload['scanning.scheduled']).toBe(true);
    expect(payload['scanning.scheduled_time']).toBe('03:00');
    expect(payload['parsing.read_mediainfo']).toBe(true);
    expect(payload['matching.auto_approve']).toBe(true);
    // Threshold intentionally NOT written — backend default (95) governs.
    expect(payload).not.toHaveProperty('matching.auto_threshold');
    expect(payload['naming.write_nfo']).toBe(true);
    expect(payload['naming.download_artwork']).toBe(true);
    expect(payload['subtitles.auto_fetch']).toBe(true);
    expect(payload['subtitles.backfill_after_scan']).toBe(true);
    expect(payload['subtitles.languages']).toBe('en, ja');
    expect(payload['integrations.sonarr.url']).toBe('http://sonarr:8989');
    expect(payload['integrations.sonarr.api_key']).toBe('abc123');
    expect(payload).not.toHaveProperty('integrations.radarr.url');
    // Anime selected, music not — music.enabled must stay absent.
    expect(payload).not.toHaveProperty('music.enabled');
    expect(payload['watch.config']).toMatchObject({ auto_scan: true });
  }, 30000);

  it('re-run prefill adopts saved values instead of wizard defaults', async () => {
    mocked.getSettings.mockResolvedValue({
      'onboarding.completed': true,
      'paths.library_root': '/data',
      'subtitles.auto_fetch': false,
      'subtitles.languages': 'en, fr',
      'scanning.scheduled': true,
      'scanning.scheduled_time': '04:30',
      'integrations.sonarr.url': 'http://nas:8989',
      'integrations.sonarr.api_key': { set: true },
    });
    render(<Onboarding onComplete={vi.fn()} />);

    fireEvent.click(await screen.findByRole('button', { name: /get started/i }));
    await screen.findByText(/What's in your library\?/, undefined, { timeout: 3000 });
    fireEvent.click(screen.getByRole('checkbox', { name: /^Movies/ }));   // TMDB optional
    await continueTo(/Connect your metadata/);
    await continueTo(/Where's your media\?/);
    // Saved root + schedule prefilled — completing again can't clobber them.
    expect(screen.getByText('/data')).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: /full rescan every night/i })).toBeChecked();
    expect(screen.getByLabelText(/nightly rescan time/i)).toHaveValue('04:30');
    expect(mocked.listFolders).not.toHaveBeenCalled();

    await continueTo(/How should files be placed\?/);
    await continueTo(/Pick a naming style/);
    await continueTo(/Subtitles, handled for you/);
    // A server that deliberately turned auto-fetch OFF stays off on re-run…
    expect(screen.getByRole('checkbox', { name: /fetch missing subtitles automatically/i })).not.toBeChecked();
    // …and its saved languages are selected.
    expect(screen.getByRole('checkbox', { name: 'French' })).toHaveAttribute('aria-checked', 'true');

    await continueTo(/Plug into your stack/);
    expect(screen.getByLabelText('Sonarr URL')).toHaveValue('http://nas:8989');
    expect(screen.getByText(/already connected/i)).toBeInTheDocument();
  }, 30000);
});
