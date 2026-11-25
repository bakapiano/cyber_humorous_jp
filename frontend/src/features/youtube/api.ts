import { useQuery } from '@tanstack/react-query';
import { EXTENSION_ID, isRecord } from '@youmo/browser-bridge';
import { client, unwrap } from '../../api/client';

export function useYoutubeConnection() {
  return useQuery({
    queryKey: ['youtube-connection'],
    queryFn: async () => unwrap(await client.GET('/api/v1/youtube/connection')),
    refetchInterval: 3000,
  });
}
export async function beginPairing() {
  const pairing = unwrap(await client.POST('/api/v1/youtube/pairing'));
  let opened = false;
  try {
    if (typeof chrome !== 'undefined' && chrome.runtime?.sendMessage) {
      const result: unknown = await chrome.runtime.sendMessage(EXTENSION_ID, {
        action: 'pair',
        code: pairing.code,
      });
      opened = isRecord(result) && result.ok === true;
    }
  } catch {
    /* The same pairing can be completed manually in the installed extension. */
  }
  return { pairing, opened };
}
export async function verifyConnection() {
  return unwrap(await client.POST('/api/v1/youtube/verify'));
}
export async function disconnectYoutube() {
  return unwrap(await client.DELETE('/api/v1/youtube/connection'));
}
