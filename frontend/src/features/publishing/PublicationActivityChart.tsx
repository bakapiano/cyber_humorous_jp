import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { RefreshCw } from 'lucide-react';
import { client, unwrap } from '../../api/client';
import type { Schemas } from '../../api/client';
import { ActivityPlot } from '../../shared/ActivityPlot';
import { ErrorNotice } from '../../shared/Notice';
import { localTime } from '../../shared/format';
import '../../styles/activity.css';

const periods = [
  { hours: 24, label: '近24小时' },
  { hours: 72, label: '近72小时' },
  { hours: 168, label: '近7天' },
  { hours: 720, label: '近30天' },
];

export function PublicationActivityChart() {
  const [hours, setHours] = useState(72);
  const query = useQuery({
    queryKey: ['publication-activity', hours],
    queryFn: async ({ signal }) =>
      unwrap(
        await client.GET('/api/v1/workspace/publication-activity', {
          params: { query: { hours } },
          signal,
        }),
      ),
    refetchInterval: 30_000,
  });
  return (
    <section
      className="source-posting"
      aria-label="投稿数量走势"
      aria-busy={query.isFetching}
    >
      <div className="posting-heading">
        <div>
          <h2>投稿数量走势</h2>
          <p>北京时间 · 成功投递</p>
        </div>
        <div className="posting-controls">
          <div className="posting-periods" role="group" aria-label="投稿统计范围">
            {periods.map((period) => (
              <button
                key={period.hours}
                aria-pressed={hours === period.hours}
                onClick={() => setHours(period.hours)}
              >
                {period.label}
              </button>
            ))}
          </div>
          <button
            className="posting-refresh"
            aria-label="刷新投稿数量走势"
            disabled={query.isFetching}
            onClick={() => {
              void query.refetch();
            }}
          >
            <RefreshCw size={14} className={query.isFetching ? 'spin' : ''} />
          </button>
        </div>
      </div>
      <ErrorNotice error={query.error} />
      {query.isPending && <p className="posting-empty">正在统计投递记录…</p>}
      {query.data && <ActivityBody data={query.data} />}
      <p className="posting-footnote">
        按本地成功投递任务的回执记录时间统计，重复BV只计一次；不含测试及结果不明，不代表审核通过。
      </p>
    </section>
  );
}

function ActivityBody({ data }: { data: Schemas['PublicationActivity'] }) {
  return (
    <>
      <div className="posting-summary">
        <span>
          窗口内已投递 <strong>{data.total}</strong> 条
        </span>
        <span>{data.interval === 'hour' ? '按小时' : '按天'}统计</span>
      </div>
      {data.total > 0 ? (
        <ActivityPlot
          title="按北京时间统计的成功投递数量"
          buckets={data.buckets.map((row) => ({
            key: row.start,
            label: row.label,
            count: row.count,
          }))}
        />
      ) : (
        <p className="posting-empty">这个范围内暂无成功投递记录。</p>
      )}
      <p className="posting-scroll-hint">左右滑动查看时间走势</p>
      <p className="posting-footnote">
        {localTime(data.since)} — {localTime(data.until)}
      </p>
    </>
  );
}
