import { useState } from 'react';
import { LockKeyhole } from 'lucide-react';
import { useBatchDetail, useBatches } from '../../api/queries';
import type { Schemas } from '../../api/client';
import { localTime, statusLabel } from '../../shared/format';
import { EmptyState, ErrorNotice } from '../../shared/Notice';
import { TaskList } from './TaskList';
import { SelectionResults } from './SelectionResults';
import { Pagination } from '../../shared/Pagination';
import { usePageState, usePagination } from '../../shared/usePagination';

export function BatchPanel() {
  const [selected, setSelected] = useState('');
  const detail = useBatchDetail(selected);
  return (
    <>
      <div className="section-heading">
        <div>
          <h2>生产批次</h2>
        </div>
      </div>
      <div className="notice">
        <LockKeyhole size={18} />
        <span>
          在「发布工作台」设置批量并触发。v008 初筛正常2次调用，复筛直通；按每批投稿数取前
          N
          条制作，每条正常6次调用（含字幕LLM复审）；格式纠正和网络恢复分别有上限并留档，按发布计划的可见性转载上传。
        </span>
      </div>
      <ErrorNotice error={detail.error} />
      <BatchCollection select={setSelected} />
      {detail.data && (
        <Snapshot
          key={`snapshot-${detail.data.id}`}
          detail={detail.data}
          close={() => setSelected('')}
        />
      )}
      {detail.data && (
        <SelectionResults key={`selection-${detail.data.id}`} batchId={detail.data.id} />
      )}
      <TaskList />
    </>
  );
}

function BatchCollection({ select }: { select: (id: string) => void }) {
  const paging = usePageState();
  const batches = useBatches(paging.offset, paging.pageSize);
  return (
    <>
      <ErrorNotice error={batches.error} />
      {batches.isPending ? (
        <div className="loading">正在读取批次…</div>
      ) : (
        <BatchTable items={batches.data?.items ?? []} select={select} />
      )}
      <Pagination
        {...paging}
        label="生产批次"
        total={batches.data?.total ?? 0}
        offset={batches.data?.offset ?? paging.offset}
        disabled={batches.isFetching}
      />
    </>
  );
}

function BatchTable({
  items,
  select,
}: {
  items: Schemas['BatchDTO'][];
  select: (id: string) => void;
}) {
  if (!items.length)
    return (
      <EmptyState title="还没有生产批次">
        先同步素材，在发布工作台设置每批素材数与投稿数；满批后可手动或自动触发。
      </EmptyState>
    );
  return (
    <div className="table-wrap">
      <table aria-label="生产批次列表">
        <thead>
          <tr>
            <th>批次</th>
            <th>候选快照</th>
            <th>素材数量</th>
            <th>状态</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {items.map((batch) => (
            <tr key={batch.id}>
              <td>
                <strong>{batch.local_date}</strong>
                <small className="mono">{batch.id.slice(0, 10)}</small>
              </td>
              <td>{localTime(batch.snapshot_at)}</td>
              <td>
                {batch.candidate_count} 条
                {batch.publications_per_batch && (
                  <small>计划投稿 {batch.publications_per_batch} 条</small>
                )}
              </td>
              <td>
                <span
                  className={`badge ${batch.status === 'failed' ? 'amber' : 'neutral'}`}
                >
                  {statusLabel(batch.status)}
                </span>
              </td>
              <td>
                <button className="text-button" onClick={() => select(batch.id)}>
                  查看快照
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Snapshot({
  detail,
  close,
}: {
  detail: Schemas['BatchDetail'];
  close: () => void;
}) {
  const paging = usePagination(detail.items);
  return (
    <section className="snapshot" aria-label="冻结清单">
      <div className="section-heading">
        <h3>{detail.local_date} · 冻结清单</h3>
        <button className="text-button" onClick={close}>
          关闭
        </button>
      </div>
      <p className="inline-note">
        {detail.error_message ?? '快照独立于后续来源更新，重试不更换输入。'}
      </p>
      <ul>
        {paging.items.map((item) => (
          <li key={item.video_id}>
            <span>{item.channel_title}</span>
            {item.title}
          </li>
        ))}
      </ul>
      <Pagination {...paging} label="冻结清单" />
    </section>
  );
}
