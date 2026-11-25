import { ExternalLink, ShieldCheck } from 'lucide-react';
import type { Schemas } from '../../api/client';
import { EmptyState } from '../../shared/Notice';
import { statusLabel } from '../../shared/format';

export function PublicationList({
  items,
  busy,
  onConfirm,
}: {
  items: Schemas['PublicationDTO'][];
  busy: boolean;
  onConfirm: (item: Schemas['PublicationDTO']) => void;
}) {
  if (!items.length)
    return (
      <EmptyState title="还没有可发布的成片">
        素材、字幕和封面齐备并通过校验后显示。
      </EmptyState>
    );
  return (
    <div className="task-list">
      {items.map((item) => (
        <article className="task-row" key={item.id}>
          <ShieldCheck size={20} />
          <div className="task-copy">
            <strong>{item.title}</strong>
            <span>
              {statusLabel(item.status)} ·{' '}
              {item.visibility === 'private' ? '仅自己可见' : '公开'}
              {item.test_only ? ' · 测试夹具，禁止真实发布' : ''}
            </span>
            <code>{item.manifest_hash.slice(0, 16)}</code>
          </div>
          {item.remote_id && /^BV[A-Za-z0-9]{10}$/.test(item.remote_id) && (
            <a
              className="button subtle"
              href={`https://www.bilibili.com/video/${item.remote_id}/`}
              target="_blank"
              rel="noopener noreferrer"
            >
              <ExternalLink size={14} />
              查看投稿
            </a>
          )}
          {!item.confirmed && (
            <button
              className="button subtle"
              disabled={busy}
              onClick={() => onConfirm(item)}
            >
              确认此版本
            </button>
          )}
        </article>
      ))}
    </div>
  );
}
