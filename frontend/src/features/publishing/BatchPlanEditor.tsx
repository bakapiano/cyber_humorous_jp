import { useState } from 'react';
import type { Schemas } from '../../api/client';
import { useAction } from '../../api/queries';
import { ErrorNotice } from '../../shared/Notice';
import { BatchTriggerButton } from './BatchTriggerButton';
import { saveBatchPlan, useBatchPlan } from './batchPlanApi';

type Plan = Schemas['BatchPlanState'];
type Draft = {
  size: string;
  count: string;
  auto: boolean;
  revision: number;
  mode: 'private_reprint' | 'public_reprint';
};

function fromPlan(plan: Plan): Draft {
  return {
    size: String(plan.batch_size),
    count: String(plan.publications_per_batch),
    auto: plan.auto_enabled,
    revision: plan.revision,
    mode: plan.publication_mode ?? 'private_reprint',
  };
}

function usePlanForm(plan: Plan) {
  const [draft, setDraft] = useState<Draft | null>(null);
  const save = useAction(saveBatchPlan);
  const fields = draft ?? fromPlan(plan);
  const changed = Boolean(draft);
  const stale = fields.revision !== plan.revision;
  const size = Number(fields.size);
  const count = Number(fields.count);
  const valid =
    Number.isInteger(size) &&
    size >= 50 &&
    size <= 1000 &&
    Number.isInteger(count) &&
    count >= 1 &&
    count <= 30;
  function change(patch: Partial<Draft>) {
    setDraft({ ...fields, ...patch });
  }
  function submit() {
    if (!valid || stale) return;
    if (
      fields.auto &&
      !window.confirm(
        `开启后每攒够 ${size} 条素材，自动选题、制作并投稿 ${count} 条，${fields.mode === 'public_reprint' ? '公开可见' : '仅自己可见'}。\n正常 ${2 + 6 * count} 次模型调用（含字幕复审）；每阶段最多2次格式纠正，每次请求最多2次网络恢复，理论上限 ${9 * (2 + 6 * count)} 次请求。确认转载使用权限并授权持续执行？`,
      )
    )
      return;
    save.mutate(
      {
        batch_size: size,
        publications_per_batch: count,
        auto_enabled: fields.auto,
        publication_mode: fields.mode,
        revision: fields.revision,
        confirm_execution: fields.auto,
      },
      { onSuccess: () => setDraft(null) },
    );
  }
  return {
    fields,
    changed,
    stale,
    valid,
    change,
    submit,
    save,
    reset: () => setDraft(null),
  };
}

export function BatchPlanEditor() {
  const query = useBatchPlan();
  return (
    <section aria-label="批量处理计划">
      <div className="section-heading">
        <h2>批量处理</h2>
        <span className="badge neutral">按数量触发 · 不定时</span>
      </div>
      <ErrorNotice error={query.error} />
      {query.data ? (
        <PlanForm plan={query.data} />
      ) : (
        <p className="inline-note">正在读取配置…</p>
      )}
    </section>
  );
}

function PlanForm({ plan }: { plan: Plan }) {
  const form = usePlanForm(plan);
  return (
    <>
      <form
        className="batch-plan"
        onSubmit={(event) => {
          event.preventDefault();
          form.submit();
        }}
      >
        <PlanFields form={form} />
        <PlanActions form={form} />
      </form>
      {form.stale && (
        <p className="notice error" role="alert">
          配置已在其他页面更新，请加载最新配置后再编辑。
        </p>
      )}
      <ErrorNotice error={form.save.error} />
      <p className="inline-note">
        当前有效未处理素材：{plan.eligible_count} / {plan.batch_size} 条 · 自动触发
        {plan.auto_enabled ? '已开启' : '未开启'}
      </p>
      <p className="inline-note">{plan.message}</p>
      <p className="inline-note">
        本地制作 / 待投名额：{plan.buffer_used ?? 0} / {plan.buffer_limit ?? 50} ·
        投稿独立消费，间隔至少 30 分 30 秒
      </p>
      {plan.next_submit_at && (
        <p className="inline-note">
          最早下次投递：
          {new Date(plan.next_submit_at).toLocaleString('zh-CN', {
            timeZone: 'Asia/Shanghai',
          })}
          （北京时间）
        </p>
      )}
      <p className="inline-note">
        v008 保留完整 Top30，复筛直通，再取前 N 条制作。每批至少 50 条素材、投稿 1–30
        条。手动与自动均需满批；明确限流按 5 / 10 / 20 / 30 / 60 分钟退避，
        之后每小时重试；结果不明不重投。
      </p>
      <BatchTriggerButton plan={plan} dirty={form.changed} />
    </>
  );
}

type PlanFormState = ReturnType<typeof usePlanForm>;

function PlanFields({ form }: { form: PlanFormState }) {
  return (
    <fieldset disabled={form.save.isPending}>
      <label>
        投稿可见性
        <select
          value={form.fields.mode}
          onChange={(event) =>
            form.change({
              mode:
                event.target.value === 'public_reprint'
                  ? 'public_reprint'
                  : 'private_reprint',
            })
          }
        >
          <option value="public_reprint">公开可见 · 转载</option>
          <option value="private_reprint">仅自己可见 · 转载</option>
        </select>
      </label>
      <label>
        每批素材数（batch size）
        <input
          type="number"
          min="50"
          max="1000"
          step="1"
          required
          value={form.fields.size}
          onChange={(event) => form.change({ size: event.target.value })}
        />
      </label>
      <label>
        每批投稿数
        <input
          type="number"
          min="1"
          max="30"
          step="1"
          required
          value={form.fields.count}
          onChange={(event) => form.change({ count: event.target.value })}
        />
      </label>
      <label className="batch-auto">
        <input
          type="checkbox"
          checked={form.fields.auto}
          onChange={(event) => form.change({ auto: event.target.checked })}
        />
        攒够一批自动触发
      </label>
    </fieldset>
  );
}

function PlanActions({ form }: { form: PlanFormState }) {
  return (
    <div className="plan-actions">
      <button
        type="submit"
        className="button"
        disabled={!form.changed || !form.valid || form.stale || form.save.isPending}
      >
        保存配置
      </button>
      {form.changed && (
        <button type="button" className="text-button" onClick={form.reset}>
          放弃修改 / 加载最新配置
        </button>
      )}
      {form.save.isSuccess && !form.changed && <span role="status">配置已保存</span>}
    </div>
  );
}
