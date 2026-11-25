import { useRef } from 'react';
import { Play } from 'lucide-react';
import type { Schemas } from '../../api/client';
import { useAction } from '../../api/queries';
import { ErrorNotice } from '../../shared/Notice';
import { triggerBatch } from './batchPlanApi';

function bufferFull(plan: Schemas['BatchPlanState']) {
  return (
    (plan.buffer_used ?? 0) + plan.publications_per_batch > (plan.buffer_limit ?? 50)
  );
}

export function BatchTriggerButton({
  plan,
  dirty = false,
}: {
  plan: Schemas['BatchPlanState'];
  dirty?: boolean;
}) {
  const action = useAction(triggerBatch);
  const request = useRef<{ revision: number; id: string } | null>(null);
  const blocked =
    dirty ||
    plan.automatic_blocked ||
    bufferFull(plan) ||
    plan.eligible_count < plan.batch_size;
  function trigger() {
    const accepted = window.confirm(
      `处理 ${plan.batch_size} 条素材，按选题排名制作并投稿 ${plan.publications_per_batch} 条。\n` +
        `正常 ${2 + 6 * plan.publications_per_batch} 次模型调用（含字幕复审）；含每阶段2次格式纠正及每次请求2次网络恢复，理论上限 ${9 * (2 + 6 * plan.publications_per_batch)} 次请求。\n` +
        `确认具有转载使用权限；本批成片将自动上传 B 站，${plan.publication_mode === 'public_reprint' ? '公开可见' : '仅自己可见'}，中日双语字幕已烧录进画面，不上传原生字幕。继续？`,
    );
    if (!accepted) return;
    if (request.current?.revision !== plan.revision) {
      request.current = { revision: plan.revision, id: crypto.randomUUID() };
    }
    action.mutate(
      {
        request_id: request.current.id,
        revision: plan.revision,
        confirm_execution: true,
      },
      {
        onSuccess: () => {
          request.current = null;
        },
      },
    );
  }
  return (
    <div className="batch-trigger">
      <button
        className="button primary"
        disabled={blocked || action.isPending}
        onClick={trigger}
      >
        <Play size={15} />
        {action.isPending ? '正在创建批次…' : '手动触发一次'}
      </button>
      {dirty && <p className="inline-note">先保存配置，再触发。</p>}
      {action.isSuccess && (
        <p className="inline-note" role="status">
          批次已入队，可在「生产批次」查看执行进度。
        </p>
      )}
      <ErrorNotice error={action.error} />
    </div>
  );
}
