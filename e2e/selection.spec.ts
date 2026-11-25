import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import type { components } from '../packages/api-types/src/generated/api';

type Schemas = components['schemas'];
const policy: Schemas['Policy'] = {
  timezone: 'Asia/Shanghai',
  daily_trigger: '10:00',
  channel_poll_minutes: 30,
  candidate_window_hours: 72,
  candidate_limit: 30,
  max_approved: 30,
  publish_slots: ['13:00', '16:00', '19:00'],
  max_new_publications_per_day: 3,
  initial_selection_version: 'v008',
};
const batch: Schemas['BatchDetail'] = {
  trigger_mode: 'legacy',
  id: 'synthetic-batch',
  local_date: '2026-11-06',
  status: 'failed',
  snapshot_at: '2026-11-06T03:00:00Z',
  candidate_count: 55,
  policy,
  error_code: 'CONTENT_PREPARATION_NOT_IMPLEMENTED',
  error_message: '下载、识别及翻译产物尚未接入。',
  items: [],
};
const ranked: Schemas['RankedCandidate'][] = Array.from({ length: 50 }, (_, index) => ({
  candidate_id: `C${index + 1}`,
  rank: index + 1,
  angle: '未核实的标题线索',
  evidence_field: 'title',
  evidence: '隔离测试原标题',
  verify: '后续内容是否支持',
  evidence_matches_title: true,
  video: {
    video_id: `test${index.toString().padStart(7, '0')}`,
    title: `隔离测试原标题 ${index + 1}`,
    source_id: 'tbsnewsdig',
    discovered_at: '2026-11-06T03:00:00Z',
    revision: 1,
    screened: false,
    origin: 'channel',
  },
}));
const result: Schemas['InitialOutput'] = {
  batch_id: batch.id,
  version: 'v008',
  composition: 'anchor_rescue',
  component_hashes: {},
  input_sha256: 'a'.repeat(64),
  prompt_sha256: 'b'.repeat(64),
  rank_count: 50,
  shortlist_limit: 30,
  ranked,
  shortlisted_ids: ranked.slice(0, 30).map((row) => row.video.video_id),
  not_shortlisted_ids: ranked.slice(30).map((row) => row.video.video_id),
  content_verified: false,
};

async function openSelection(page: Page, output: Schemas['InitialOutput']) {
  await page.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    const bodies: Record<string, unknown> = {
      '/api/v1/dashboard': {
        sources: 102,
        enabled_sources: 3,
        videos: 55,
        eligible_videos: 0,
        batches: 1,
        failed_tasks: 1,
        policy,
        automation_enabled: false,
        publication_enabled: false,
      },
      '/api/v1/sources': [],
      '/api/v1/videos': { items: [], total: 0 },
      '/api/v1/workspace/tasks': { items: [], total: 0, offset: 0, limit: 10 },
      '/api/v1/workspace/batches': { items: [batch], total: 1, offset: 0, limit: 10 },
      [`/api/v1/batches/${batch.id}`]: {
        ...batch,
        items: ranked.map((row) => row.video),
      },
      [`/api/v1/batches/${batch.id}/selection-results`]: {
        initial: output,
        rescreen: output,
        active_version: 'v008',
        history: [],
        rescreen_mode: 'passthrough',
        content_preparation: 'not_implemented',
      },
    };
    await route.fulfill({ json: bodies[path] ?? {} });
  });
  await page.goto('/');
  await page.getByRole('button', { name: '生产批次', exact: true }).click();
  await page.getByRole('button', { name: '查看快照' }).click();
}

test('batch view shows thirty unchanged selections and keeps reserve ranks separate', async ({
  page,
}) => {
  await openSelection(page, result);
  await expect(
    page.getByText('复筛已原样输出：没有删减、重排或改写。', { exact: false }),
  ).toBeVisible();
  const ranking = page.locator('.selection-ranking').first();
  await expect(ranking.locator('li')).toHaveCount(10);
  await page.getByRole('button', { name: '入围结果下一页' }).click();
  await expect(ranking.locator('li').first()).toHaveAttribute('value', '11');
  await page.getByRole('button', { name: '入围结果末页' }).click();
  await expect(ranking.locator('li').first()).toHaveAttribute('value', '21');
  await expect(
    page.getByRole('button', { name: '按 v008 启动初筛', exact: true }),
  ).toHaveCount(0);
  await page.getByText('候补排序（本轮不处理）').click();
  await expect(page.locator('details .selection-ranking li')).toHaveCount(10);
  await page.getByRole('button', { name: '候补结果下一页' }).click();
  await expect(page.locator('details .selection-ranking li').first()).toHaveAttribute(
    'value',
    '41',
  );
  const snapshot = page.getByRole('region', { name: '冻结清单' });
  await page.getByRole('button', { name: '冻结清单末页' }).click();
  await expect(snapshot.locator('li').first()).toContainText('隔离测试原标题 41');
  await page.screenshot({
    path: '.tmp/ui-pagination/batches-desktop.png',
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
  ).toBe(true);
  await page.screenshot({
    path: '.tmp/ui-pagination/batches-mobile.png',
    fullPage: true,
  });
});

test('filtered rescue is labeled as fallback, not successful supplementation', async ({
  page,
}) => {
  await openSelection(page, {
    ...result,
    composition: 'baseline_fallback',
    fallback_reason: 'provider_content_filter',
  });
  await expect(
    page.getByText(
      '补漏被内容过滤，已明确回退到有效 v004 基础排序；本次没有采用补漏结果。',
    ),
  ).toBeVisible();
});

test('an old v004 result does not hide the explicit v008 upgrade action', async ({
  page,
}) => {
  await openSelection(page, {
    ...result,
    version: 'v004',
    composition: 'baseline',
    component_hashes: {},
  });
  await expect(
    page.getByRole('button', { name: '按 v008 启动初筛', exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText('初筛正常两次调用，含格式纠正及网络恢复最多十八次', { exact: false }),
  ).toBeVisible();
});
