import { expect, test } from '@playwright/test';
import { fixturePage, published, setupPublishing } from './publishing-fixtures';

test('publication records paginate independently of activity totals and period', async ({
  page,
}) => {
  const state = await setupPublishing(page);
  state.publications = Array.from({ length: 25 }, (_, i) => ({
    ...published,
    id: `p${i}`,
    title: `分页稿件 ${i + 1}`,
  }));
  await page.goto('/');
  await page.getByRole('button', { name: '发布工作台', exact: true }).click();
  const chart = page.getByRole('region', { name: '投稿数量走势' });
  await expect(chart.getByRole('img')).toBeVisible();
  await expect(chart.locator('.posting-summary')).toContainText('3');
  await expect(page.locator('.task-list article')).toHaveCount(10);
  await page.getByRole('button', { name: '投稿记录下一页' }).click();
  await expect(page.locator('.task-list article').first()).toContainText('分页稿件 11');
  await page.getByRole('button', { name: '投稿记录末页' }).click();
  await expect(page.locator('.task-list article')).toHaveCount(5);
  await page.getByLabel('投稿记录每页条数').selectOption('20');
  await expect(page.locator('.task-list article')).toHaveCount(20);
  await chart.getByRole('button', { name: '近7天', exact: true }).click();
  await expect(chart.getByText('按天统计', { exact: true })).toBeVisible();
  await expect(chart.locator('.posting-summary')).toContainText('3');
  await page.getByLabel('投稿记录每页条数').selectOption('10');
  await page.screenshot({
    path: '.tmp/ui-pagination/publishing-desktop.png',
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
  ).toBe(true);
  await page.screenshot({
    path: '.tmp/ui-pagination/publishing-mobile.png',
    fullPage: true,
  });
  expect(state.writes).toEqual([]);
});

test('batch and task tables have separate pages and page sizes', async ({ page }) => {
  const state = await setupPublishing(page);
  const batches = Array.from({ length: 31 }, (_, i) => ({
    id: `batch-${String(i + 1).padStart(3, '0')}`,
    local_date: '2026-11-06',
    snapshot_at: '2026-11-06T03:00:00Z',
    candidate_count: 50,
    status: 'completed',
    publications_per_batch: 10,
  }));
  const tasks = Array.from({ length: 125 }, (_, i) => ({
    id: `task-${i}`,
    kind: 'publish',
    status: 'succeeded',
    error_message: `任务记录 ${i + 1}`,
    updated_at: '2026-11-06T03:00:00Z',
  }));
  await page.route('**/api/v1/workspace/batches?*', (route) =>
    route.fulfill({
      json: fixturePage(batches, new URL(route.request().url())),
    }),
  );
  await page.route('**/api/v1/workspace/tasks?*', (route) =>
    route.fulfill({
      json: fixturePage(tasks, new URL(route.request().url())),
    }),
  );
  await page.goto('/');
  await page.getByRole('button', { name: '生产批次', exact: true }).click();
  const rows = page.getByRole('table', { name: '生产批次列表' }).locator('tbody tr');
  await expect(rows).toHaveCount(10);
  await expect(page.locator('.task-row')).toHaveCount(10);
  await page.getByRole('button', { name: '生产批次下一页' }).click();
  await expect(rows.first()).toContainText('batch-011');
  await expect(page.locator('.task-row').first()).toContainText('任务记录 1');
  await page.getByRole('button', { name: '任务执行末页' }).click();
  await expect(page.locator('.task-row')).toHaveCount(5);
  await expect(page.locator('.task-row').first()).toContainText('任务记录 121');
  await page.getByLabel('生产批次每页条数').selectOption('20');
  await expect(rows).toHaveCount(20);
  await expect(rows.first()).toContainText('batch-001');
  await expect(page.locator('.task-row')).toHaveCount(5);
  expect(state.writes).toEqual([]);
});
