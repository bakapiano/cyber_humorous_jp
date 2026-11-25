import { useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { ErrorNotice } from '../../shared/Notice';
import { hourLabel, publicationHours, publicationRange } from './posting-model';
import { usePublicationSamples } from './posting-query';
import { PostingPlot } from './PostingPlot';
import '../../styles/activity.css';

const periods = [
  { hours: 24, label: '近24小时' },
  { hours: 72, label: '近72小时' },
  { hours: 168, label: '近7天' },
];

export function SourcePostingChart({ sourceId }: { sourceId: string }) {
  const [hours, setHours] = useState(72);
  const query = usePublicationSamples(sourceId);
  const stats =
    query.data && publicationHours(query.data.samples, hours, query.data.capturedAt);
  return (
    <section className="source-posting" aria-label="来源发布时间走势">
      <div className="posting-heading">
        <div>
          <h2>发布时间走势</h2>
          <p>北京时间 · {sourceId ? `@${sourceId}` : '全部已收录频道'}</p>
        </div>
        <div className="posting-controls">
          <div className="posting-periods" role="group" aria-label="发布时间范围">
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
            aria-label="刷新发布时间走势"
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
      {query.isPending && <p className="posting-empty">正在统计已入库素材…</p>}
      {stats && <PostingBody stats={stats} />}
      {query.data && !query.data.complete && (
        <p className="inline-note" role="status">
          当前仅统计已读取的 {query.data.samples.length} / {query.data.total}{' '}
          条素材；采集期间数据有变化或达到读取上限，请刷新后查看。
        </p>
      )}
      <p className="posting-footnote">
        按原频道发布时间统计，不是入库时间；只包含已收录素材，不代表频道的全部历史投稿。
      </p>
    </section>
  );
}

function PostingBody({ stats }: { stats: ReturnType<typeof publicationHours> }) {
  const peak = stats.peaks.map((row) => hourLabel(row.hour));
  return (
    <>
      <div className="posting-summary">
        <span>
          窗口内 <strong>{stats.total}</strong> 条
        </span>
        {stats.total > 0 && (
          <span>
            高峰{' '}
            <strong>
              {peak.slice(0, 3).join(' / ')}
              {peak.length > 3 ? ' 等' : ''}
            </strong>{' '}
            · {stats.maximum} 条/小时桶
          </span>
        )}
      </div>
      {stats.total ? (
        <>
          <PostingPlot buckets={stats.buckets} />
          <p className="posting-scroll-hint">左右滑动查看全天时段</p>
        </>
      ) : (
        <p className="posting-empty">这个范围内暂无带有效发布时间的素材。</p>
      )}
      <p className="posting-footnote">
        {publicationRange(stats.since)} — {publicationRange(stats.until)}
        ；多天相同小时合并统计。
        {stats.missing > 0 && `另有 ${stats.missing} 条缺少有效发布时间，未计入。`}
      </p>
      {stats.total > 0 && (
        <details className="posting-hour-details">
          <summary>逐时数据</summary>
          <dl>
            {stats.buckets.map((row) => (
              <div key={row.hour}>
                <dt>{hourLabel(row.hour)}</dt>
                <dd>{row.count} 条</dd>
              </div>
            ))}
          </dl>
        </details>
      )}
    </>
  );
}
