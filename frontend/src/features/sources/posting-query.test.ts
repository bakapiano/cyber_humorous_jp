import { expect, it } from 'vitest';
import { collectPublicationSamples } from './posting-query';

it('reads every page and does not limit statistics to the displayed feed page', async () => {
  const offsets: number[] = [];
  const result = await collectPublicationSamples((offset) => {
    offsets.push(offset);
    return Promise.resolve({
      total: 3,
      items: offset
        ? [{ id: 'c', publishedAt: null }]
        : [
            { id: 'a', publishedAt: null },
            { id: 'b', publishedAt: null },
          ],
    });
  });
  expect(offsets).toEqual([0, 2]);
  expect(result.complete).toBe(true);
  expect(result.samples).toHaveLength(3);
});

it('marks duplicate or changing pagination incomplete', async () => {
  const result = await collectPublicationSamples((offset) =>
    Promise.resolve({
      total: offset ? 3 : 2,
      items: [{ id: 'same', publishedAt: null }],
    }),
  );
  expect(result.complete).toBe(false);
  expect(result.samples).toHaveLength(1);
});

it('stops at empty pages and the safety limit', async () => {
  const empty = await collectPublicationSamples(() =>
    Promise.resolve({ total: 1, items: [] }),
  );
  expect(empty.complete).toBe(false);
  const huge = await collectPublicationSamples((offset) =>
    Promise.resolve({
      total: 12_000,
      items: Array.from({ length: 100 }, (_, index) => ({
        id: String(offset + index),
        publishedAt: null,
      })),
    }),
  );
  expect(huge.samples).toHaveLength(10_000);
  expect(huge.complete).toBe(false);
});

it('accepts an empty catalogue and rejects invalid totals or request failures', async () => {
  expect(
    (await collectPublicationSamples(() => Promise.resolve({ total: 0, items: [] })))
      .complete,
  ).toBe(true);
  await expect(
    collectPublicationSamples(() => Promise.resolve({ total: -1, items: [] })),
  ).rejects.toThrow('素材总数无效');
  await expect(
    collectPublicationSamples(() => Promise.reject(new Error('offline'))),
  ).rejects.toThrow('offline');
});
