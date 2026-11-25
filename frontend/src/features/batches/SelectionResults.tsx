import { useQuery } from '@tanstack/react-query';
import { client, unwrap } from '../../api/client';
import type { Schemas } from '../../api/client';
import { useAction } from '../../api/queries';
import { ErrorNotice } from '../../shared/Notice';
import { Pagination } from '../../shared/Pagination';
import { usePagination } from '../../shared/usePagination';

export function SelectionResults({ batchId }: { batchId: string }) {
  const query = useQuery({
    queryKey: ['selection-results', batchId],
    queryFn: async () =>
      unwrap(
        await client.GET('/api/v1/batches/{batch_id}/selection-results', {
          params: { path: { batch_id: batchId } },
        }),
      ),
    refetchInterval: 3000,
  });
  const start = useAction(async () =>
    unwrap(
      await client.POST('/api/v1/batches/{batch_id}/initial-selection', {
        params: { path: { batch_id: batchId } },
      }),
    ),
  );
  const result = query.data?.initial;
  const startDisabled = [start.isPending, query.isLoading, query.isError].some(Boolean);
  return (
    <section className="snapshot selection-results">
      <div className="section-heading">
        <h3>初筛结果 · v008</h3>
      </div>
      <ErrorNotice error={query.error ?? start.error} />
      <StartSelection
        current={result?.version === 'v008'}
        disabled={startDisabled}
        started={start.isSuccess}
        start={() => start.mutate()}
      />
      {result && <Ranking result={result} rescreened={Boolean(query.data?.rescreen)} />}
      <ArchivedRankings items={query.data?.history ?? []} />
    </section>
  );
}
function StartSelection({
  current,
  disabled,
  started,
  start,
}: {
  current: boolean;
  disabled: boolean;
  started: boolean;
  start: () => void;
}) {
  if (current) return null;
  return (
    <>
      <p className="inline-note">
        初筛正常两次调用，含格式纠正及网络恢复最多十八次；入围后每条正常五次，含全部恢复最多四十五次。保留旧结果，重试复用缓存。
      </p>
      <button className="button primary" disabled={disabled} onClick={start}>
        按 v008 启动初筛
      </button>
      {started && <p className="inline-note">已排队，请查看下方任务状态。</p>}
    </>
  );
}
function ArchivedRankings({ items }: { items: Schemas['InitialOutput'][] }) {
  return (
    <>
      {items.map((item) => (
        <details key={item.version}>
          <summary>历史 {item.version} 结果</summary>
          <Ranking result={item} rescreened={false} archived />
        </details>
      ))}
    </>
  );
}
function Ranking({
  result,
  rescreened,
  archived = false,
}: {
  result: Schemas['InitialOutput'];
  rescreened: boolean;
  archived?: boolean;
}) {
  const shortlist = new Set(result.shortlisted_ids);
  return (
    <>
      <p className="inline-note">
        {result.version} · 排序 {result.rank_count} 条，入围 {shortlist.size} 条。
        {archived
          ? '历史输出已保留。'
          : rescreened
            ? '复筛已原样输出：没有删减、重排或改写。'
            : '等待复筛直通任务。'}
        这些是标题线索，尚未验证视频内容。
      </p>
      {result.fallback_reason === 'provider_content_filter' && (
        <p className="notice">
          补漏被内容过滤，已明确回退到有效 v004 基础排序；本次没有采用补漏结果。
        </p>
      )}
      <RankingList
        label="入围结果"
        items={result.ranked.filter((row) => shortlist.has(row.video.video_id))}
      />
      <details>
        <summary>候补排序（本轮不处理）</summary>
        <RankingList
          label="候补结果"
          items={result.ranked.filter((row) => !shortlist.has(row.video.video_id))}
        />
      </details>
    </>
  );
}
function RankingList({
  label,
  items,
}: {
  label: string;
  items: Schemas['RankedCandidate'][];
}) {
  const paging = usePagination(items);
  return (
    <div aria-label={label}>
      <ol className="selection-ranking">
        {paging.items.map((row) => (
          <Candidate key={row.candidate_id} row={row} />
        ))}
      </ol>
      <Pagination {...paging} label={label} />
    </div>
  );
}
function Candidate({ row }: { row: Schemas['RankedCandidate'] }) {
  return (
    <li value={row.rank}>
      <strong>{row.video.title}</strong>
      <p>潜在角度：{row.angle}</p>
      <p>
        原标题依据：{row.evidence}
        {!row.evidence_matches_title && '（引用不匹配，未进入入围清单）'}
      </p>
      <p>待核实：{row.verify}</p>
    </li>
  );
}
