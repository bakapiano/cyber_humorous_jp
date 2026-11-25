import { describe, expect, it } from 'vitest';
import { hourLabel, publicationHours, publicationRange } from './posting-model';

const now = Date.parse('2026-11-06T12:00:00Z');

describe('publication hour aggregation', () => {
  it('uses Beijing hours with explicit offsets and midnight rollover', () => {
    const stats = publicationHours(
      [
        { id: 'a', publishedAt: '2026-11-05T16:30:00Z' },
        { id: 'b', publishedAt: '2026-11-06T01:30:00+09:00' },
        { id: 'c', publishedAt: '2026-11-06T09:00:00+08:00' },
      ],
      24,
      now,
    );
    expect(stats.total).toBe(3);
    expect(stats.buckets[0]?.count).toBe(2);
    expect(stats.buckets[9]?.count).toBe(1);
    expect(stats.peaks).toEqual([{ hour: 0, count: 2 }]);
  });
  it('deduplicates IDs, filters dates and never guesses missing timezones', () => {
    const stats = publicationHours(
      [
        { id: 'a', publishedAt: '2026-11-05T12:00:00Z' },
        { id: 'a', publishedAt: '2026-11-05T12:00:00Z' },
        { id: 'old', publishedAt: '2026-11-05T11:59:59Z' },
        { id: 'future', publishedAt: '2026-11-06T12:00:01Z' },
        { id: 'now', publishedAt: '2026-11-06T12:00:00Z' },
        { id: 'missing', publishedAt: null },
        { id: 'invalid', publishedAt: 'invalidZ' },
        { id: 'naive', publishedAt: '2026-11-06T01:00:00' },
      ],
      24,
      now,
    );
    expect(stats.total).toBe(2);
    expect(stats.missing).toBe(3);
    expect(stats.buckets[20]?.count).toBe(2);
  });
  it('handles empty windows and tied peaks without a fabricated busiest hour', () => {
    expect(publicationHours([], 72, now).peaks).toEqual([]);
    const stats = publicationHours(
      [
        { id: 'a', publishedAt: '2026-11-04T00:00:00Z' },
        { id: 'b', publishedAt: '2026-11-06T01:00:00Z' },
      ],
      72,
      now,
    );
    expect(stats.peaks.map((row) => row.hour)).toEqual([8, 9]);
    expect(stats.buckets).toHaveLength(24);
    expect(hourLabel(0)).toBe('00:00');
    expect(publicationRange(now)).toContain('20:00');
  });
});
