import type { Schemas } from '../../api/client';

export type Totals = Schemas['UsageTotals'];
export type UsageRow = Schemas['UsageRow'];
export const tokens = (value: number) => value.toLocaleString('zh-CN');
export const money = (nano: number) =>
  (nano / 1e9).toLocaleString('zh-CN', {
    style: 'currency',
    currency: 'CNY',
    minimumFractionDigits: 4,
    maximumFractionDigits: 6,
  });

export function cost(totals: Totals) {
  return money(totals.cost_nano_cny);
}

export function stageLabel(stage: string) {
  if (stage.startsWith('translation')) return '字幕翻译';
  if (stage.startsWith('title')) return '标题生成';
  if (stage.startsWith('subtitle')) return '字幕自审';
  if (stage.startsWith('editing')) return '剪辑区间';
  if (stage.startsWith('cover')) return '封面生成';
  if (stage.startsWith('packaging')) return '标题与封面';
  return stage;
}
