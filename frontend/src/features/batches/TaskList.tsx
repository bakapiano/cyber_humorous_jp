import { RotateCcw } from 'lucide-react';
import { retryTask, useAction, useTasks } from '../../api/queries';
import { localTime, statusLabel } from '../../shared/format';
import { ErrorNotice } from '../../shared/Notice';
import { Pagination } from '../../shared/Pagination';
import { usePageState } from '../../shared/usePagination';

export function TaskList() {
  const paging = usePageState();
  const tasks = useTasks(paging.offset, paging.pageSize);
  const retry = useAction(retryTask);
  return (
    <>
      <div className="section-heading feed-heading">
        <div>
          <h2>任务执行</h2>
        </div>
      </div>
      <ErrorNotice error={tasks.error ?? retry.error} />
      {tasks.isPending && <div className="loading">正在读取任务…</div>}
      <div className="task-list">
        {(tasks.data?.items ?? []).map((task) => (
          <article className="task-row" key={task.id}>
            <span className={`status-dot ${task.status}`} />
            <div className="task-copy">
              <strong>{taskName(task.kind)}</strong>
              <span>{task.error_message ?? statusLabel(task.status)}</span>
              {task.error_code && <code>{task.error_code}</code>}
            </div>
            <div className="task-end">
              <span>{localTime(task.updated_at)}</span>
              {task.status === 'failed' && task.kind !== 'publish' && (
                <button
                  className="button subtle"
                  disabled={retry.isPending}
                  onClick={() => retry.mutate(task.id)}
                >
                  <RotateCcw size={13} />
                  重试
                </button>
              )}
            </div>
          </article>
        ))}
      </div>
      <Pagination
        {...paging}
        label="任务执行"
        total={tasks.data?.total ?? 0}
        offset={tasks.data?.offset ?? paging.offset}
        disabled={tasks.isFetching}
      />
    </>
  );
}

function taskName(kind: string) {
  const names: Record<string, string> = {
    sync_source: '真实频道同步',
    selection: '首筛 → 内容准备 → 二筛',
    initial_selection: '标题初筛 · 按任务冻结版本执行',
    rescreen: '复筛 · 原样输出',
    prepare_content: '原片下载／本地识别／翻译',
    prepare_video: '单条制作 · 原片下载／识别／翻译',
    cleanup_batch: '投递完成后清理视频 · 保留 LLM 输入输出',
    dispatch_production: '按本批投稿数分发制作',
    publish: '批次转载投稿',
    packaging: '标题与封面生成',
    editing: '标题对齐剪辑 · 快速回看＋正文',
    prepare_publication: '媒体质检与发布包',
    finalize_private: '投递回执收尾',
  };
  return names[kind] ?? kind;
}
