import { useQuery } from '@tanstack/react-query';
import { client, unwrap } from '../../api/client';
import type { Schemas } from '../../api/client';

export function useBatchPlan() {
  return useQuery({
    queryKey: ['batch-plan'],
    queryFn: async () => unwrap(await client.GET('/api/v1/batch-plan')),
    refetchInterval: 3000,
  });
}

export async function saveBatchPlan(body: Schemas['BatchPlanUpdate']) {
  return unwrap(await client.PUT('/api/v1/batch-plan', { body }));
}

export async function triggerBatch(body: Schemas['BatchTrigger']) {
  return unwrap(await client.POST('/api/v1/production-batches', { body }));
}
