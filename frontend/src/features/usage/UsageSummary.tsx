import type { Schemas } from '../../api/client';
import { cost, money, tokens } from './usage-format';

export function UsageSummary({ report }: { report: Schemas['UsageReport'] }) {
  const total = report.summary;
  return (
    <section className="metrics usage-metrics" aria-label="模型用量概览">
      <div className="metric">
        <span>费用估算</span>
        <div>{cost(total)}</div>
        <p>人民币 · 非账单实扣</p>
      </div>
      <div className="metric">
        <span>总 Token</span>
        <div>{tokens(total.total_tokens)}</div>
        <p>
          输入 {tokens(total.input_tokens)} / 输出 {tokens(total.output_tokens)}
        </p>
      </div>
      <div className="metric">
        <span>单视频均摊估算</span>
        <div>{videoAverage(report)}</div>
        <p>{report.videos.length} 个制作视频 · 含已分配初筛费用</p>
      </div>
      <div className="metric">
        <span>已计价调用</span>
        <div>{tokens(total.calls)}</div>
        <p>其中重试 {total.retries} 次</p>
      </div>
    </section>
  );
}

function videoAverage(report: Schemas['UsageReport']) {
  if (!report.videos.length) return '—';
  const assigned = report.videos.reduce((sum, row) => sum + row.total.cost_nano_cny, 0);
  return money(assigned / report.videos.length);
}
