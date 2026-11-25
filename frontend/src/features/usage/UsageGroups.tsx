import { useState } from 'react';
import { Pagination } from '../../shared/Pagination';
import { usePagination } from '../../shared/usePagination';
import { localTime, statusLabel } from '../../shared/format';
import { cost, money, tokens } from './usage-format';
import type { UsageRow } from './usage-format';
import { TokenBreakdown, UsageStages } from './UsageStages';

export function UsageGroups({
  items,
  workflow,
}: {
  items: UsageRow[];
  workflow: boolean;
}) {
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState('');
  const matches = items.filter((row) =>
    `${row.title} ${row.id}`.toLowerCase().includes(search.toLowerCase()),
  );
  const paging = usePagination(matches);
  const detail = matches.find((row) => row.id === selected);
  const label = workflow ? '工作流消耗' : '视频消耗';
  return (
    <section aria-label={label}>
      <div className="usage-list-heading">
        <h2>{label}</h2>
        <input
          aria-label={`筛选${label}`}
          placeholder="搜索标题或 ID"
          value={search}
          onChange={(event) => {
            setSearch(event.target.value);
            setSelected('');
            paging.onPageChange(1);
          }}
        />
      </div>
      <div className="table-wrap">
        <table aria-label={`${label}列表`}>
          <thead>
            <tr>
              <th>{workflow ? '工作流批次' : '视频'}</th>
              <th>调用</th>
              <th>总 Token</th>
              <th>{workflow ? '本批总费用' : '直接费用'}</th>
              <th>{workflow ? '每制作视频均摊' : '初筛分摊'}</th>
              <th>{workflow ? '制作 / 已投递' : '含分摊合计'}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {paging.items.map((row) => (
              <GroupRow
                key={row.id}
                row={row}
                workflow={workflow}
                onSelect={() => setSelected(row.id)}
              />
            ))}
          </tbody>
        </table>
      </div>
      <p className="usage-scroll-hint">左右滑动查看 Token 和费用列</p>
      {!matches.length && <p className="inline-note">暂无匹配的用量记录。</p>}
      <Pagination {...paging} label={label} />
      {detail && (
        <UsageDetail detail={detail} workflow={workflow} close={() => setSelected('')} />
      )}
    </section>
  );
}

function UsageDetail({
  detail,
  workflow,
  close,
}: {
  detail: UsageRow;
  workflow: boolean;
  close: () => void;
}) {
  return (
    <section className="snapshot usage-detail" aria-label="用量明细">
      <div className="section-heading">
        <h3>{detail.title}</h3>
        <button className="text-button" onClick={close}>
          关闭明细
        </button>
      </div>
      <p className="inline-note">
        直接费用 {cost(detail.direct)} · 初筛费用/分摊 {cost(detail.shared)} · 合计{' '}
        {cost(detail.total)}
      </p>
      <TokenBreakdown totals={detail.total} />
      <UsageStages key={detail.id} items={detail.stages} />
      {!workflow && (
        <p className="inline-note">
          阶段表列本视频实际制作调用；上方Token与合计包含选题均摊。工作流：
          {detail.batch_ids.join(' / ') || '未归属'}
        </p>
      )}
    </section>
  );
}

function GroupRow({
  row,
  workflow,
  onSelect,
}: {
  row: UsageRow;
  workflow: boolean;
  onSelect: () => void;
}) {
  return (
    <tr>
      <td className="usage-title">
        <strong title={row.title}>{row.title}</strong>
        <small className="mono">{row.id}</small>
        <small className="usage-mobile-cost">合计 {cost(row.total)}</small>
        <small>
          {row.status ? statusLabel(row.status) : localTime(row.last_call_at)}
        </small>
      </td>
      <td>
        {row.total.calls}
        <small>重试 {row.total.retries}</small>
      </td>
      <td>{tokens(row.total.total_tokens)}</td>
      <td>{cost(workflow ? row.total : row.direct)}</td>
      <td>{workflow ? averageCost(row) : cost(row.shared)}</td>
      <td>
        {workflow ? `${row.video_count} / ${row.delivered_count}` : cost(row.total)}
      </td>
      <td>
        <button className="text-button" onClick={onSelect}>
          查看分项
        </button>
      </td>
    </tr>
  );
}

function averageCost(row: UsageRow) {
  if (!row.video_count) return '—';
  return money(row.total.cost_nano_cny / row.video_count);
}
