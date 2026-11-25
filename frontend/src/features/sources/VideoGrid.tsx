import { ArrowUpRight } from 'lucide-react';
import type { Schemas } from '../../api/client';
import { localTime } from '../../shared/format';
import { EmptyState } from '../../shared/Notice';

export function VideoGrid({ items }: { items: Schemas['VideoDTO'][] }) {
  if (!items.length)
    return (
      <EmptyState title="暂无素材">同步频道后显示视频元数据，不自动下载。</EmptyState>
    );
  return (
    <div className="video-grid">
      {items.map((video) => (
        <article className="video-card" key={video.video_id}>
          <a
            className="thumbnail"
            href={`https://www.youtube.com/watch?v=${video.video_id}`}
            target="_blank"
            rel="noreferrer"
          >
            {video.thumbnail_url && (
              <img src={video.thumbnail_url} alt="" loading="lazy" />
            )}
            <span className="thumbnail-label">
              YOUTUBE <ArrowUpRight size={12} />
            </span>
          </a>
          <div className="video-copy">
            <div className="video-meta">
              <span>{video.channel_title ?? video.source_id}</span>
              <time>{localTime(video.published_at)}</time>
            </div>
            <h3>{video.title}</h3>
            <div className="video-footer">
              <span className={`badge ${video.screened ? 'neutral' : 'green'}`}>
                {video.screened ? '已筛选' : '待首筛'}
              </span>
              <span className="mono">{video.video_id}</span>
            </div>
          </div>
        </article>
      ))}
    </div>
  );
}
