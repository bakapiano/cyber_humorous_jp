import { useState } from 'react';
import { ExternalLink, Radio } from 'lucide-react';
import type { Schemas } from '../../api/client';
import { localTime } from '../../shared/format';

export function ChannelCard({
  source,
  avatar,
  selected,
  onSelect,
  onToggle,
  disabled,
}: {
  source: Schemas['SourceDTO'];
  avatar: string | undefined;
  selected: boolean;
  onSelect: () => void;
  onToggle: () => void;
  disabled: boolean;
}) {
  return (
    <article className={`channel-card ${selected ? 'selected' : ''}`}>
      <button
        className="channel-main"
        onClick={onSelect}
        aria-pressed={selected}
        title={`筛选 ${source.name} 的素材`}
      >
        <ChannelAvatar key={avatar} url={avatar} name={source.name} />
        <div>
          <h3>{source.name}</h3>
          <span>@{source.id}</span>
        </div>
      </button>
      <a
        className="channel-youtube"
        href={source.url}
        target="_blank"
        rel="noopener noreferrer"
        aria-label={`在 YouTube 打开 ${source.name}`}
      >
        YouTube <ExternalLink size={12} />
      </a>
      <div className="channel-bottom">
        <span>
          {source.evidence_count} 次引用 · {source.video_count} 条入库
        </span>
        <button
          className={`toggle ${source.enabled ? 'on' : ''}`}
          aria-label={`${source.enabled ? '暂停' : '启用'} ${source.name}`}
          onClick={onToggle}
          disabled={disabled}
        >
          <span />
        </button>
      </div>
      <p className={source.last_error ? 'channel-error' : 'channel-time'}>
        {source.last_error ?? `最近同步 ${localTime(source.last_checked)}`}
      </p>
    </article>
  );
}

function ChannelAvatar({ url, name }: { url: string | undefined; name: string }) {
  const [failed, setFailed] = useState(false);
  return (
    <div className="channel-icon channel-avatar">
      {url && !failed ? (
        <img
          src={url}
          alt={`${name} 头像`}
          width={35}
          height={35}
          loading="lazy"
          onError={() => setFailed(true)}
        />
      ) : (
        <Radio size={20} aria-label="暂无频道头像" />
      )}
    </div>
  );
}
