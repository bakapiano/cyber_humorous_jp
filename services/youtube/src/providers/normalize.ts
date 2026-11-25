import { YTNodes } from 'youtubei.js';
import type { DiscoveryVideo } from '../contracts.js';

export function validVideo(id: string, title: string): DiscoveryVideo | null {
  if (!/^[\w-]{11}$/.test(id) || !title.trim()) return null;
  return { video_id: id, title, origin: 'home', published_at: null };
}

export function normalizeVideo(node: YTNodes.Video): DiscoveryVideo | null {
  const result = validVideo(node.video_id, node.title.toString());
  if (!result) return null;
  return {
    ...result,
    channel_id: node.author.id || null,
    channel_title: node.author.name || null,
    thumbnail_url: node.thumbnails[0]?.url ?? null,
    duration_seconds: node.duration.seconds || null,
  };
}

export function normalizeLockup(node: YTNodes.LockupView): DiscoveryVideo | null {
  if (node.content_type !== 'VIDEO') return null;
  const result = validVideo(node.content_id ?? '', lockupTitle(node));
  if (!result) return null;
  const image = node.content_image?.is(YTNodes.ThumbnailView)
    ? node.content_image.image[0]?.url
    : null;
  return {
    ...result,
    thumbnail_url: image ?? null,
    duration_seconds: node.duration.seconds || null,
  };
}

function lockupTitle(node: YTNodes.LockupView): string {
  return node.metadata?.title?.toString() ?? '';
}
