import { useQuery } from '@tanstack/react-query';
import { client, unwrap } from '../../api/client';
import type { PublicationSample } from './posting-model';

interface SamplePage {
  items: PublicationSample[];
  total: number;
}

export async function collectPublicationSamples(
  readPage: (offset: number) => Promise<SamplePage>,
) {
  const samples = new Map<string, PublicationSample>();
  let offset = 0;
  let total: number | undefined;
  let changed = false;
  while (offset < 10_000) {
    const page = await readPage(offset);
    validateTotal(page.total);
    total ??= page.total;
    changed ||= total !== page.total;
    for (const row of page.items.slice(0, 10_000 - offset)) samples.set(row.id, row);
    offset += page.items.length;
    if (!page.items.length || offset >= total) break;
  }
  return {
    samples: [...samples.values()],
    total: total ?? 0,
    capturedAt: Date.now(),
    complete: !changed && samples.size === total,
  };
}

function validateTotal(total: number) {
  if (!Number.isSafeInteger(total) || total < 0) throw new Error('素材总数无效。');
}

export function usePublicationSamples(sourceId: string) {
  return useQuery({
    queryKey: ['publication-hour-samples', sourceId],
    queryFn: ({ signal }) =>
      collectPublicationSamples(async (offset) => {
        const page = unwrap(
          await client.GET('/api/v1/videos', {
            params: { query: { source_id: sourceId, offset, limit: 100 } },
            signal,
          }),
        );
        return {
          total: page.total,
          items: page.items.map((row) => ({
            id: row.video_id,
            publishedAt: row.published_at ?? null,
          })),
        };
      }),
    staleTime: 60_000,
    refetchInterval: 60_000,
    refetchOnWindowFocus: false,
  });
}
