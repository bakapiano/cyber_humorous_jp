import { useQuery } from '@tanstack/react-query';

export function parseAvatarIndex(value: unknown): Map<string, string> {
  const result = new Map<string, string>();
  if (!value || typeof value !== 'object' || !('avatars' in value)) return result;
  const entries = value.avatars;
  if (!entries || typeof entries !== 'object' || Array.isArray(entries)) return result;
  for (const [id, url] of Object.entries(entries)) {
    if (typeof url === 'string' && /^\/source-avatars\/UC[\w-]{22}\.png$/.test(url))
      result.set(id, url);
  }
  return result;
}

export function useSourceAvatars() {
  return useQuery({
    queryKey: ['source-avatars'],
    queryFn: async ({ signal }) => {
      const response = await fetch('/source-avatars.json', { signal });
      if (!response.ok) throw new Error('频道头像暂不可用');
      const data: unknown = await response.json();
      return parseAvatarIndex(data);
    },
    staleTime: 3_600_000,
    retry: 1,
  });
}
