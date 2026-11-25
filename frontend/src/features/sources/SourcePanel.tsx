import { useState } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { useVideos } from '../../api/queries';
import { ErrorNotice } from '../../shared/Notice';
import { ChannelSection } from './ChannelSection';
import { VideoGrid } from './VideoGrid';
import { SourcePostingChart } from './SourcePostingChart';
import { ScanSettings } from './ScanSettings';

export function SourcePanel() {
  const [selected, setSelected] = useState('');
  const [offset, setOffset] = useState(0);
  const videos = useVideos(selected, offset);
  function choose(id: string) {
    setSelected(id);
    setOffset(0);
  }
  return (
    <>
      <ChannelSection selected={selected} choose={choose} />
      <ScanSettings />
      <SourcePostingChart sourceId={selected} />
      <div className="section-heading feed-heading">
        <div>
          <h2>
            素材池 <span className="count">{videos.data?.total ?? 0}</span>
          </h2>
        </div>
        <button className="button subtle" onClick={() => choose('')}>
          {selected ? '清除频道筛选' : '全部已收录频道'}
        </button>
      </div>
      <ErrorNotice error={videos.error} />
      {videos.isPending ? (
        <div className="loading">正在读取素材池…</div>
      ) : (
        <VideoGrid items={videos.data?.items ?? []} />
      )}
      <div className="pagination">
        <span>入库不等于入选</span>
        <div>
          <button
            aria-label="上一页"
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - 12))}
          >
            <ChevronLeft size={16} />
          </button>
          <span>{Math.floor(offset / 12) + 1}</span>
          <button
            aria-label="下一页"
            disabled={offset + 12 >= (videos.data?.total ?? 0)}
            onClick={() => setOffset(offset + 12)}
          >
            <ChevronRight size={16} />
          </button>
        </div>
      </div>
    </>
  );
}
