import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { client, unwrap } from './client';

export function useDashboard() {
  return useQuery({
    queryKey: ['dashboard'],
    queryFn: async () => unwrap(await client.GET('/api/v1/dashboard')),
    refetchInterval: 5000,
  });
}

export function useSources() {
  return useQuery({
    queryKey: ['sources'],
    queryFn: async () => unwrap(await client.GET('/api/v1/sources')),
    refetchInterval: 5000,
  });
}

export function useVideos(sourceId: string, offset: number) {
  return useQuery({
    queryKey: ['videos', sourceId, offset],
    queryFn: async () =>
      unwrap(
        await client.GET('/api/v1/videos', {
          params: { query: { source_id: sourceId, offset, limit: 12 } },
        }),
      ),
    refetchInterval: 5000,
  });
}

export function useBatches(offset = 0, limit = 10) {
  return useQuery({
    queryKey: ['batches', offset, limit],
    queryFn: async () =>
      unwrap(
        await client.GET('/api/v1/workspace/batches', {
          params: { query: { offset, limit } },
        }),
      ),
    placeholderData: keepPreviousData,
    refetchInterval: 3000,
  });
}

export function useTasks(offset = 0, limit = 10) {
  return useQuery({
    queryKey: ['tasks', offset, limit],
    queryFn: async () =>
      unwrap(
        await client.GET('/api/v1/workspace/tasks', {
          params: { query: { offset, limit } },
        }),
      ),
    placeholderData: keepPreviousData,
    refetchInterval: 3000,
  });
}

export function usePublications(offset = 0, limit = 10) {
  return useQuery({
    queryKey: ['publications', offset, limit],
    queryFn: async () =>
      unwrap(
        await client.GET('/api/v1/workspace/publications', {
          params: { query: { offset, limit } },
        }),
      ),
    placeholderData: keepPreviousData,
    refetchInterval: 3000,
  });
}

export function useBatchDetail(id: string) {
  return useQuery({
    queryKey: ['batch', id],
    enabled: Boolean(id),
    queryFn: async () =>
      unwrap(
        await client.GET('/api/v1/batches/{batch_id}', {
          params: { path: { batch_id: id } },
        }),
      ),
  });
}

export function useAction<T>(action: (input: T) => Promise<unknown>) {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: action,
    onSuccess: async () => {
      await cache.invalidateQueries();
    },
  });
}

export async function syncSources() {
  return unwrap(await client.POST('/api/v1/discovery-runs'));
}

export async function toggleSource(input: { id: string; enabled: boolean }) {
  return unwrap(
    await client.PATCH('/api/v1/sources/{source_id}', {
      params: { path: { source_id: input.id } },
      body: { enabled: input.enabled },
    }),
  );
}

export async function retryTask(id: string) {
  return unwrap(
    await client.POST('/api/v1/tasks/{task_id}/retry', {
      params: { path: { task_id: id } },
    }),
  );
}
