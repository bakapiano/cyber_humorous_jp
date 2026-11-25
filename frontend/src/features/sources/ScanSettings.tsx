import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { client, unwrap } from '../../api/client';
import type { Schemas } from '../../api/client';
import { useAction } from '../../api/queries';
import { localTime } from '../../shared/format';
import { ErrorNotice } from '../../shared/Notice';

type Plan = Schemas['ScanPlanState'];
type Draft = { minutes: string; enabled: boolean; revision: number };

export function ScanSettings() {
  const query = useQuery({
    queryKey: ['scan-plan'],
    queryFn: async () => unwrap(await client.GET('/api/v1/scan-plan')),
    refetchInterval: 5000,
  });
  return (
    <section aria-label="频道扫描设置" className="scan-settings">
      <ErrorNotice error={query.error} />
      {query.data && <ScanForm plan={query.data} />}
    </section>
  );
}

function useScanForm(plan: Plan) {
  const [draft, setDraft] = useState<Draft | null>(null);
  const save = useAction(async (body: Schemas['ScanPlanUpdate']) =>
    unwrap(await client.PUT('/api/v1/scan-plan', { body })),
  );
  const fields = draft ?? {
    minutes: String(plan.interval_minutes),
    enabled: plan.enabled,
    revision: plan.revision,
  };
  const minutes = Number(fields.minutes);
  const invalid = !Number.isInteger(minutes) || minutes < 5 || minutes > 1440;
  function change(patch: Partial<Draft>) {
    setDraft({ ...fields, ...patch });
  }
  function submit() {
    if (invalid || fields.revision !== plan.revision) return;
    save.mutate(
      { interval_minutes: minutes, enabled: fields.enabled, revision: fields.revision },
      { onSuccess: () => setDraft(null) },
    );
  }
  return {
    fields,
    save,
    change,
    submit,
    invalid,
    dirty: Boolean(draft),
    stale: fields.revision !== plan.revision,
    reset: () => setDraft(null),
  };
}

function ScanForm({ plan }: { plan: Plan }) {
  const form = useScanForm(plan);
  return (
    <>
      <form
        className="scan-controls"
        onSubmit={(event) => {
          event.preventDefault();
          form.submit();
        }}
      >
        <label>
          <input
            type="checkbox"
            checked={form.fields.enabled}
            disabled={form.save.isPending}
            onChange={(event) => form.change({ enabled: event.target.checked })}
          />
          定期扫描频道
        </label>
        <label>
          扫描间隔（分钟）
          <input
            type="number"
            min="5"
            max="1440"
            step="1"
            required
            value={form.fields.minutes}
            disabled={form.save.isPending}
            onChange={(event) => form.change({ minutes: event.target.value })}
          />
        </label>
        <button
          className="button"
          disabled={!form.dirty || form.invalid || form.stale || form.save.isPending}
        >
          保存扫描设置
        </button>
        {form.dirty && (
          <button className="text-button" type="button" onClick={form.reset}>
            加载最新扫描设置
          </button>
        )}
      </form>
      <p className="inline-note">
        {scanStatus(plan)}
        。仅更新素材库，与批量处理独立；「同步已启用频道」可手动扫描。
      </p>
      {form.stale && (
        <p role="alert" className="notice error">
          扫描配置已更新，请加载最新设置。
        </p>
      )}
      {form.save.isSuccess && !form.dirty && (
        <p role="status" className="inline-note">
          扫描设置已保存
        </p>
      )}
      <ErrorNotice error={form.save.error} />
    </>
  );
}

function scanStatus(plan: Plan) {
  if (plan.scanning) return '正在扫描';
  if (!plan.enabled) return '定期扫描已关闭';
  return `下次扫描：${plan.next_run_at ? localTime(plan.next_run_at) : '等待扫描器启动'}`;
}
