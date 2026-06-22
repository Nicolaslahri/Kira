import { describe, it, expect, beforeEach } from 'vitest';
import { setConfBands, getConfBands, confLevel } from './confBands';

// Module-level mutable state (App hydrates it once, badges read it at render).
// Reset to the shipped defaults before each test so order can't leak state.
beforeEach(() => setConfBands(85, 50));

describe('confBands', () => {
  it('defaults to 85 / 50 and buckets accordingly', () => {
    expect(getConfBands()).toEqual({ high: 85, mid: 50 });
    expect(confLevel(90)).toBe('high');
    expect(confLevel(60)).toBe('mid');
    expect(confLevel(30)).toBe('low');
  });

  it('treats the cutoffs as inclusive lower bounds', () => {
    expect(confLevel(85)).toBe('high');  // >= high
    expect(confLevel(50)).toBe('mid');   // >= mid
    expect(confLevel(49)).toBe('low');
  });

  it('re-buckets after the user retunes the thresholds', () => {
    // This is the whole point of the module — the Confidence sliders must
    // actually move where the badges fall (the bug was they wrote settings
    // nothing read).
    setConfBands(70, 40);
    expect(getConfBands()).toEqual({ high: 70, mid: 40 });
    expect(confLevel(75)).toBe('high');
    expect(confLevel(65)).toBe('mid');
    expect(confLevel(35)).toBe('low');
  });

  it('ignores non-finite values so garbage settings keep the prior band', () => {
    setConfBands(NaN, Infinity);
    expect(getConfBands()).toEqual({ high: 85, mid: 50 });
  });

  it('updates each band independently when only one is valid', () => {
    setConfBands(90, NaN);
    expect(getConfBands()).toEqual({ high: 90, mid: 50 });
  });
});
