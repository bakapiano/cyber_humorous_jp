import { expect, test } from '@playwright/test';
import { setupPublishing } from './publishing-fixtures';

test('batch settings save, reload, and show only real receipt links', async ({
  page,
}) => {
  const state = await setupPublishing(page);
  await page.goto('/');
  await page.getByRole('button', { name: '发布工作台', exact: true }).click();
  await page.getByLabel('每批素材数（batch size）').fill('60');
  await page.getByLabel('每批投稿数', { exact: true }).fill('3');
  await expect(page.getByRole('button', { name: '手动触发一次' })).toBeDisabled();
  await page.getByRole('button', { name: '保存配置', exact: true }).click();
  await expect(page.getByText('配置已保存', { exact: true })).toBeVisible();
  expect(state.writes).toEqual([
    {
      path: '/api/v1/batch-plan',
      body: {
        batch_size: 60,
        publications_per_batch: 3,
        auto_enabled: false,
        publication_mode: 'private_reprint',
        revision: 0,
        confirm_execution: false,
      },
    },
  ]);
  await page.reload();
  await page.getByRole('button', { name: '发布工作台', exact: true }).click();
  await expect(page.getByLabel('每批素材数（batch size）')).toHaveValue('60');
  await expect(page.getByLabel('每批投稿数', { exact: true })).toHaveValue('3');
  const link = page.getByRole('link', { name: '查看投稿' });
  await expect(link).toHaveCount(1);
  await expect(link).toHaveAttribute(
    'href',
    'https://www.bilibili.com/video/BV1234567890/',
  );
  await expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
  ).toBe(true);
});

test('manual trigger needs full batch and cancellation sends nothing', async ({
  page,
}) => {
  const state = await setupPublishing(page);
  state.plan.eligible_count = 49;
  await page.goto('/');
  await page.getByRole('button', { name: '发布工作台', exact: true }).click();
  await expect(page.getByRole('button', { name: '手动触发一次' })).toBeDisabled();
  state.plan.eligible_count = 50;
  await expect(page.getByRole('button', { name: '手动触发一次' })).toBeEnabled();
  page.once('dialog', (dialog) => dialog.dismiss());
  await page.getByRole('button', { name: '手动触发一次' }).click();
  expect(state.writes).toEqual([]);
  page.once('dialog', (dialog) => dialog.accept());
  await page.getByRole('button', { name: '手动触发一次' }).click();
  await expect(page.getByText('批次已入队', { exact: false })).toBeVisible();
  await expect(page.getByRole('button', { name: '手动触发一次' })).toBeDisabled();
  expect(state.writes).toHaveLength(1);
  expect(state.writes[0]?.body).toMatchObject({ revision: 0, confirm_execution: true });
});

test('public visibility is saved and explicitly described before execution', async ({
  page,
}) => {
  const state = await setupPublishing(page);
  state.nextPlan.publication_mode = 'public_reprint';
  await page.goto('/');
  await page.getByRole('button', { name: '发布工作台', exact: true }).click();
  await page.getByLabel('投稿可见性').selectOption('public_reprint');
  await page.getByRole('button', { name: '保存配置', exact: true }).click();
  await expect(page.getByText('配置已保存', { exact: true })).toBeVisible();
  expect(state.writes[0]?.body).toMatchObject({ publication_mode: 'public_reprint' });
  await page.reload();
  await page.getByRole('button', { name: '发布工作台', exact: true }).click();
  await expect(page.getByLabel('投稿可见性')).toHaveValue('public_reprint');
  page.once('dialog', async (dialog) => {
    expect(dialog.message()).toContain('公开可见');
    await dialog.dismiss();
  });
  await page.getByRole('button', { name: '手动触发一次' }).click();
  expect(state.writes).toHaveLength(1);
});

test('another full batch can enqueue while the first batch is active', async ({
  page,
}) => {
  const state = await setupPublishing(page);
  state.plan.eligible_count = 100;
  await page.goto('/');
  await page.getByRole('button', { name: '发布工作台', exact: true }).click();
  page.on('dialog', (dialog) => dialog.accept());
  const trigger = page.getByRole('button', { name: '手动触发一次' });
  await trigger.click();
  await expect(page.getByText('批次已入队', { exact: false })).toBeVisible();
  expect(state.plan.active_batch_id).toBe('new-batch');
  expect(state.plan.eligible_count).toBe(50);
  await expect(trigger).toBeEnabled();
  await trigger.click();
  await expect(trigger).toBeDisabled();
  expect(state.writes).toHaveLength(2);
  expect(state.plan.eligible_count).toBe(0);
});

test('retries reuse the request identity and plan errors preserve edits', async ({
  page,
}) => {
  const state = await setupPublishing(page);
  state.failTrigger = true;
  await page.goto('/');
  await page.getByRole('button', { name: '发布工作台', exact: true }).click();
  page.on('dialog', (dialog) => dialog.accept());
  await page.getByRole('button', { name: '手动触发一次' }).click();
  await expect(page.getByText('隔离测试失败')).toBeVisible();
  state.failTrigger = false;
  await page.getByRole('button', { name: '手动触发一次' }).click();
  await expect(page.getByText('批次已入队', { exact: false })).toBeVisible();
  expect(state.writes[0]?.body).toEqual(state.writes[1]?.body);
  await page.getByLabel('每批素材数（batch size）').fill('80');
  state.plan.revision = 3;
  await expect(page.getByText('配置已在其他页面更新', { exact: false })).toBeVisible();
  await expect(page.getByLabel('每批素材数（batch size）')).toHaveValue('80');
  await expect(
    page.getByRole('button', { name: '保存配置', exact: true }),
  ).toBeDisabled();
});

test('scanner settings are independently editable without triggering production', async ({
  page,
}) => {
  const state = await setupPublishing(page);
  await page.goto('/');
  await page.getByLabel('扫描间隔（分钟）').fill('60');
  await page.getByRole('button', { name: '保存扫描设置' }).click();
  await expect(page.getByText('扫描设置已保存')).toBeVisible();
  expect(state.writes).toEqual([
    {
      path: '/api/v1/scan-plan',
      body: { enabled: true, interval_minutes: 60, revision: 0 },
    },
  ]);
  await page.reload();
  await expect(page.getByLabel('扫描间隔（分钟）')).toHaveValue('60');
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
  ).toBe(true);
});
