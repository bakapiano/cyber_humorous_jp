import { useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { useAction, useSources, syncSources, toggleSource } from '../../api/queries';
import { ErrorNotice } from '../../shared/Notice';
import { ChannelCard } from './ChannelCard';
import { ChannelPagination } from './ChannelPagination';
import { useSourceAvatars } from './channel-avatars';
import './channels.css';

export function ChannelSection({
  selected,
  choose,
}: {
  selected: string;
  choose: (id: string) => void;
}) {
  const [showAll, setShowAll] = useState(false);
  const [page, setPage] = useState(1);
  const avatars = useSourceAvatars();
  const sources = useSources();
  const sync = useAction(syncSources);
  const toggle = useAction(toggleSource);
  const allSources = sources.data ?? [];
  const visible = allSources.filter((item) => showAll || item.enabled);
  const pages = Math.max(1, Math.ceil(visible.length / 6));
  const current = Math.min(page, pages);
  function changeVisibility() {
    setShowAll(!showAll);
    setPage(1);
  }
  return (
    <>
      <div className="section-heading">
        <div>
          <h2>来源频道</h2>
        </div>
        <button
          className="button primary"
          disabled={sync.isPending}
          onClick={() => sync.mutate(undefined)}
        >
          <RefreshCw size={15} className={sync.isPending ? 'spin' : ''} />
          同步已启用频道
        </button>
      </div>
      <ErrorNotice error={sources.error ?? sync.error ?? toggle.error} />
      {sync.isSuccess && (
        <p className="inline-note" role="status">
          同步已排队，仅采集元数据，不下载视频。
        </p>
      )}
      <div className="channel-grid">
        {visible.slice((current - 1) * 6, current * 6).map((source) => (
          <ChannelCard
            key={source.id}
            source={source}
            avatar={avatars.data?.get(source.id)}
            selected={selected === source.id}
            onSelect={() => choose(source.id)}
            onToggle={() => toggle.mutate({ id: source.id, enabled: !source.enabled })}
            disabled={toggle.isPending}
          />
        ))}
      </div>
      <ChannelStatus pending={sources.isPending} count={visible.length} />
      <ChannelPagination
        page={current}
        pages={pages}
        total={visible.length}
        onChange={setPage}
      />
      <div className="channel-actions">
        <button className="text-button" onClick={changeVisibility}>
          {showAll ? '收起未启用来源' : `查看全部 ${allSources.length} 个已核实来源`}
        </button>
        <span>来源依据：幽默老日投稿简介引用</span>
      </div>
    </>
  );
}

function ChannelStatus({ pending, count }: { pending: boolean; count: number }) {
  if (pending) return <p className="inline-note">正在读取来源频道…</p>;
  if (!count) return <p className="inline-note">暂无已启用频道，可展开全部来源。</p>;
  return null;
}
