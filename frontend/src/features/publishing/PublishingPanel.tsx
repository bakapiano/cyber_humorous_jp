import { ShieldCheck } from 'lucide-react';
import { client, unwrap } from '../../api/client';
import { useAction, usePublications } from '../../api/queries';
import { ErrorNotice } from '../../shared/Notice';
import { Pagination } from '../../shared/Pagination';
import { usePageState } from '../../shared/usePagination';
import { BatchPlanEditor } from './BatchPlanEditor';
import { PublicationList } from './PublicationList';
import { PublicationActivityChart } from './PublicationActivityChart';

export function PublishingPanel() {
  const paging = usePageState();
  const query = usePublications(paging.offset, paging.pageSize);
  const confirm = useAction(async (input: { id: string; hash: string }) =>
    unwrap(
      await client.POST('/api/v1/publications/{publication_id}/confirm', {
        params: { path: { publication_id: input.id } },
        body: { manifest_hash: input.hash },
      }),
    ),
  );
  return (
    <>
      <BatchPlanEditor />
      <PublicationActivityChart />
      <div className="section-heading">
        <div>
          <h2>投稿记录</h2>
        </div>
        <span className="badge amber">
          <ShieldCheck size={12} />
          转载 · 保留原始来源
        </span>
      </div>
      <ErrorNotice error={query.error ?? confirm.error} />
      {query.isPending ? (
        <div className="loading">正在读取投稿记录…</div>
      ) : (
        <PublicationList
          items={query.data?.items ?? []}
          busy={confirm.isPending}
          onConfirm={(item) => confirm.mutate({ id: item.id, hash: item.manifest_hash })}
        />
      )}
      <Pagination
        {...paging}
        label="投稿记录"
        total={query.data?.total ?? 0}
        offset={query.data?.offset ?? paging.offset}
        disabled={query.isFetching}
      />
    </>
  );
}
