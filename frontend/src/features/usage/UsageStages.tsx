import type { Schemas } from '../../api/client';
import { Pagination } from '../../shared/Pagination';
import { usePagination } from '../../shared/usePagination';
import { cost, stageLabel, tokens } from './usage-format';
import type { Totals } from './usage-format';

export function UsageStages({
  items,
  label = '阶段明细',
}: {
  items: Schemas['UsageStage'][];
  label?: string;
}) {
  const paging = usePagination(items);
  return (
    <>
      <div className="table-wrap">
        <table aria-label={label}>
          <thead>
            <tr>
              <th>阶段</th>
              <th>调用 / 重试</th>
              <th>输入 Token</th>
              <th>输出 Token</th>
              <th>估算费用</th>
            </tr>
          </thead>
          <tbody>
            {paging.items.map(({ stage, totals }) => (
              <tr key={stage}>
                <td>
                  <strong>{stageLabel(stage)}</strong>
                  <small>{stage}</small>
                </td>
                <td>
                  {totals.calls} / {totals.retries}
                </td>
                <td>{tokens(totals.input_tokens)}</td>
                <td>{tokens(totals.output_tokens)}</td>
                <td>{cost(totals)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Pagination {...paging} label={label} />
    </>
  );
}

export function TokenBreakdown({ totals }: { totals: Totals }) {
  return (
    <dl className="usage-token-details">
      <div>
        <dt>输入 Token</dt>
        <dd>{tokens(totals.input_tokens)}</dd>
      </div>
      <div>
        <dt>缓存命中</dt>
        <dd>{tokens(totals.cache_hit_tokens)}</dd>
      </div>
      <div>
        <dt>缓存未命中</dt>
        <dd>{tokens(totals.cache_miss_tokens)}</dd>
      </div>
      <div>
        <dt>输出 Token</dt>
        <dd>{tokens(totals.output_tokens)}</dd>
      </div>
      <div>
        <dt>其中已知推理 Token</dt>
        <dd>{tokens(totals.reasoning_tokens)}</dd>
      </div>
    </dl>
  );
}
