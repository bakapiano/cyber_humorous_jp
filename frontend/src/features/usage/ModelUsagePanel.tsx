import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { RefreshCw } from 'lucide-react';
import { client, unwrap } from '../../api/client';
import { ErrorNotice } from '../../shared/Notice';
import { localTime } from '../../shared/format';
import { UsageSummary } from './UsageSummary';
import { UsageGroups } from './UsageGroups';
import { TokenBreakdown, UsageStages } from './UsageStages';
import { UsagePricing } from './UsagePricing';
import '../../styles/usage.css';

const tabs = [
  { id: 'videos', label: '按视频' },
  { id: 'workflows', label: '按工作流批次' },
  { id: 'stages', label: '按阶段' },
] as const;
type UsageTab = (typeof tabs)[number]['id'];
const readUsage = async (refresh = false) =>
  unwrap(await client.GET('/api/v1/model-usage', { params: { query: { refresh } } }));

export function ModelUsagePanel() {
  const [tab, setTab] = useState<UsageTab>('videos');
  const cache = useQueryClient();
  const query = useQuery({
    queryKey: ['model-usage'],
    queryFn: () => readUsage(),
    refetchInterval: 30_000,
  });
  const refresh = useMutation({
    mutationFn: () => readUsage(true),
    onSuccess: (data) => cache.setQueryData(['model-usage'], data),
  });
  const report = query.data;
  return (
    <>
      <div className="section-heading">
        <h2>模型用量与费用</h2>
        <button
          className="button subtle"
          disabled={query.isFetching || refresh.isPending}
          onClick={() => refresh.mutate()}
        >
          <RefreshCw size={14} />
          刷新统计
        </button>
      </div>
      <ErrorNotice error={query.error ?? refresh.error} />
      {query.isPending && <div className="loading">正在读取历史用量留档…</div>}
      {report && (
        <>
          <UsageSummary report={report} />
          <p className="inline-note">
            仅统计用量与计价信息齐全的调用 · 更新于 {localTime(report.captured_at)}
          </p>
          <div className="usage-tabs" role="group" aria-label="用量统计维度">
            {tabs.map((item) => (
              <button
                key={item.id}
                aria-pressed={tab === item.id}
                onClick={() => setTab(item.id)}
              >
                {item.label}
              </button>
            ))}
          </div>
          {tab !== 'stages' ? (
            <UsageGroups
              key={tab}
              workflow={tab === 'workflows'}
              items={tab === 'videos' ? report.videos : report.workflows}
            />
          ) : (
            <>
              <TokenBreakdown totals={report.summary} />
              <UsageStages items={report.stages} label="全部阶段" />
            </>
          )}
          <UsagePricing pricing={report.pricing} />
        </>
      )}
    </>
  );
}
