import { expect, test } from '@playwright/test';
import { setupPublishing } from './publishing-fixtures';
import { usageFixture } from './usage-fixtures';

test('usage dashboard shows per-video and workflow costs, details, pagination and official prices', async ({
  page,
}) => {
  const state = await setupPublishing(page);
  let refreshed = false;
  await page.route('**/api/v1/model-usage?*', (route) => {
    refreshed ||= new URL(route.request().url()).searchParams.get('refresh') === 'true';
    return route.fulfill({ json: usageFixture });
  });
  await page.goto('/');
  await page.getByRole('button', { name: '模型用量', exact: true }).click();
  await expect(page.getByRole('region', { name: '模型用量概览' })).toContainText(
    '39,600',
  );
  await expect(page.getByText('已计价调用', { exact: true })).toBeVisible();
  await expect(page.getByRole('region', { name: '模型用量概览' })).toContainText('14');
  await expect(
    page.getByText(/部分|未计价|金额不完整|无用量回执|缓存明细缺失/),
  ).toHaveCount(0);
  const videoRows = page.getByRole('table', { name: '视频消耗列表' }).locator('tbody tr');
  await expect(videoRows).toHaveCount(10);
  await page.getByRole('button', { name: '视频消耗下一页' }).click();
  await expect(videoRows).toHaveCount(2);
  await page.getByLabel('筛选视频消耗').fill('视频 1');
  await expect(videoRows).toHaveCount(4);
  await videoRows.first().getByRole('button', { name: '查看分项' }).click();
  await expect(page.getByRole('region', { name: '用量明细' })).toContainText('缓存命中');
  await page.getByRole('button', { name: '按工作流批次', exact: true }).click();
  const workflow = page.getByRole('table', { name: '工作流消耗列表' });
  await expect(workflow).not.toContainText('金额不完整');
  await expect(workflow).toContainText('12 / 8');
  await page.getByText('官方单价与统计口径', { exact: true }).click();
  await expect(page.getByRole('link', { name: 'DeepSeek 官方计价规则' })).toHaveAttribute(
    'href',
    usageFixture.pricing.source_url,
  );
  await page.getByRole('button', { name: '刷新统计' }).click();
  await expect.poll(() => refreshed).toBe(true);
  await page.getByRole('button', { name: '按阶段', exact: true }).click();
  await expect(
    page.getByText(/部分|未计价|金额不完整|无用量回执|缓存明细缺失/),
  ).toHaveCount(0);
  await page.getByRole('button', { name: '按工作流批次', exact: true }).click();
  await page.screenshot({ path: '.tmp/usage-dashboard/desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
  ).toBe(true);
  await page.screenshot({ path: '.tmp/usage-dashboard/mobile.png', fullPage: true });
  expect(state.writes).toEqual([]);
});

test('usage read errors remain visible and do not look like zero spending', async ({
  page,
}) => {
  await setupPublishing(page);
  await page.route('**/api/v1/model-usage?*', (route) =>
    route.fulfill({
      status: 409,
      json: { code: 'UNAVAILABLE', message: '用量暂不可读取' },
    }),
  );
  await page.goto('/');
  await page.getByRole('button', { name: '模型用量', exact: true }).click();
  await expect(page.getByText('用量暂不可读取')).toBeVisible();
  await expect(page.getByRole('region', { name: '模型用量概览' })).toHaveCount(0);
});
