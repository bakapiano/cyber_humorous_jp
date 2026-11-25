import type { Schemas } from '../../api/client';

export function UsagePricing({ pricing }: { pricing: Schemas['UsagePriceInfo'] }) {
  return (
    <details className="usage-pricing">
      <summary>官方单价与统计口径</summary>
      <p>
        价表核对日期：{pricing.verified_at} ·{' '}
        <a href={pricing.source_url} target="_blank" rel="noopener noreferrer">
          DeepSeek 官方计价规则
        </a>
      </p>
      <div className="table-wrap">
        <table aria-label="DeepSeek人民币单价">
          <thead>
            <tr>
              <th>模型</th>
              <th>时段</th>
              <th>输入·缓存命中</th>
              <th>输入·未命中</th>
              <th>输出</th>
            </tr>
          </thead>
          <tbody>
            {pricing.rates.map((rate) => (
              <tr key={`${rate.name}-${rate.period}`}>
                <td>{rate.name}</td>
                <td>{rate.period === 'peak' ? '高峰' : '低谷'}</td>
                <td>¥{rate.cache_hit}</td>
                <td>¥{rate.cache_miss}</td>
                <td>¥{rate.output}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p>
        单位：人民币 / 百万
        Token。高峰为周一至周五北京时间09:00–12:00、14:00–18:00（中国节假日除外）；其他时段为低谷。
      </p>
      <p>
        按响应创建时间、缓存命中/未命中输入和输出用量换算。推理Token已经包含在输出中，不重复收费。日历覆盖{' '}
        {pricing.calendar_years.join('、')} 年。
      </p>
      <p>
        只统计用量与计价信息齐全的选题和制作调用，按响应ID去重；重试、格式纠正及模型拒绝的调用遵循相同口径。本地复用不重复计数，API缓存命中按命中单价计费。
      </p>
      <p>
        工作流指生产批次；初筛费用按本批实际分发的制作视频数均摊（包括失败/跳过项），同一视频的历史重做版本合并计算。未分发视频的失败初筛费用保留在批次与全局总额中。
      </p>
      <p>
        历史用量统一按本次官方价表估算，不是账户账单实扣，也不覆盖项目外调用；未绑定批次的调用保留在全局总额中。
      </p>
      <p>
        单视频调用数只列实际制作调用；选题调用数保留在批次/阶段统计中。进行中的批次费用还会继续增长。
      </p>
    </details>
  );
}
