import { expect, test } from '@playwright/test';
import type { components } from '../packages/api-types/src/generated/api';
import { batchPlan, scanPlan } from './publishing-fixtures';

const policy: components['schemas']['Policy'] = {
  initial_selection_version: 'legacy',
  timezone: 'Asia/Shanghai',
  daily_trigger: '10:00',
  channel_poll_minutes: 30,
  candidate_window_hours: 72,
  candidate_limit: 6,
  max_approved: 3,
  publish_slots: ['13:00', '16:00', '19:00'],
  max_new_publications_per_day: 3,
};
const dashboard: components['schemas']['Dashboard'] = {
  sources: 102,
  enabled_sources: 3,
  videos: 1,
  eligible_videos: 1,
  batches: 0,
  failed_tasks: 0,
  policy,
  automation_enabled: false,
  publication_enabled: false,
  selection_provider: 'not_implemented',
  rescreen_provider: 'passthrough',
  content_preparation_provider: 'not_implemented',
  packaging_provider: 'not_implemented',
  editing_provider: 'v003_ranges',
};
const source: components['schemas']['SourceDTO'] = {
  id: 'tbsnewsdig',
  name: 'TBS NEWS DIG',
  url: 'https://www.youtube.com/@tbsnewsdig',
  evidence_count: 120,
  enabled: true,
  video_count: 1,
};
const video: components['schemas']['VideoDTO'] = {
  video_id: 'abcdefghijk',
  title: '浏览器离线测试素材',
  source_id: 'tbsnewsdig',
  channel_title: 'TBS NEWS DIG',
  discovered_at: '2026-11-06T03:00:00Z',
  published_at: '2026-11-06T01:00:00Z',
  revision: 1,
  screened: false,
  origin: 'channel',
};

test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    const bodies: Record<string, unknown> = {
      '/api/v1/dashboard': dashboard,
      '/api/v1/sources': [source],
      '/api/v1/videos': { items: [video], total: 1 },
      '/api/v1/workspace/batches': { items: [], total: 0, offset: 0, limit: 10 },
      '/api/v1/workspace/tasks': { items: [], total: 0, offset: 0, limit: 10 },
      '/api/v1/workspace/publications': { items: [], total: 0, offset: 0, limit: 10 },
      '/api/v1/workspace/publication-activity': {
        since: '2026-11-03T03:00:00Z',
        until: '2026-11-06T03:00:00Z',
        timezone: 'Asia/Shanghai',
        interval: 'hour',
        total: 0,
        buckets: [],
      },
      '/api/v1/batch-plan': batchPlan,
      '/api/v1/scan-plan': scanPlan,
      '/api/v1/session': { csrf_token: 'offline-only' },
      '/api/v1/discovery-runs': [],
      '/api/v1/youtube/connection': {
        status: 'disconnected',
        paired: false,
        source: 'none',
        last_sync: null,
        last_verified: null,
        account_name: null,
        homepage_count: 0,
        message: '尚未连接 YouTube。',
      },
      '/api/v1/youtube/pairing': {
        code: 'a'.repeat(43),
        expires_at: '2099-01-01T10:00:00Z',
        extension_id: 'synthetic',
      },
    };
    await route.fulfill({
      json: bodies[path] ?? {},
      status: path === '/api/v1/discovery-runs' ? 202 : 200,
    });
  });
});

test('login page offers installation and a one-time pairing fallback without exposing cookies', async ({
  page,
}) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'YouTube 连接', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'YouTube 登录连接' })).toBeVisible();
  await expect(page.getByRole('link', { name: '下载自有扩展' })).toHaveAttribute(
    'href',
    '/api/v1/youtube/extension.zip',
  );
  await page.getByRole('button', { name: '连接 / 重新配对' }).click();
  await expect(page.getByLabel('一次性配对码')).toHaveValue('a'.repeat(43));
  await expect(page.getByText('请在装有扩展的 Chrome / Edge 中完成配对')).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
  ).toBe(true);
  await page.screenshot({
    path: 'test-results/youtube-connection-mobile.png',
    fullPage: true,
  });
});

test('workspace navigates without executing a blackbox', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: '来源与素材', level: 1 })).toBeVisible();
  await expect(page.getByRole('heading', { name: '浏览器离线测试素材' })).toBeVisible();
  await page.getByRole('button', { name: '同步已启用频道' }).click();
  await expect(page.getByRole('status')).toContainText('仅采集元数据');
  await page.getByRole('button', { name: '生产批次', exact: true }).click();
  await expect(page.getByText('还没有生产批次')).toBeVisible();
  await page.getByRole('button', { name: '发布工作台', exact: true }).click();
  await expect(page.getByText('转载 · 保留原始来源')).toBeVisible();
  await expect(page.getByText('还没有可发布的成片')).toBeVisible();
});

test('narrow screen has no horizontal overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: '浏览器离线测试素材' })).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
  ).toBe(true);
});

test('blue-white theme stays light and consistent across pages on a dark system', async ({
  page,
}) => {
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.goto('/');
  await expect(page.locator('html')).toHaveCSS('color-scheme', 'light');
  await expect(page.locator('html')).toHaveCSS('background-color', 'rgb(245, 248, 252)');
  await expect(page.locator('html')).toHaveCSS('color', 'rgb(30, 41, 59)');
  await expect(page.locator('.sidebar')).toHaveCSS(
    'background-color',
    'rgb(255, 255, 255)',
  );
  await expect(page.locator('.topbar')).toHaveCSS(
    'background-color',
    'rgb(255, 255, 255)',
  );
  await expect(page.locator('.channel-card')).toHaveCSS(
    'background-color',
    'rgb(255, 255, 255)',
  );
  await expect(page.locator('.video-card')).toHaveCSS(
    'background-color',
    'rgb(255, 255, 255)',
  );
  const sync = page.getByRole('button', { name: '同步已启用频道' });
  await expect(sync).toHaveCSS('background-color', 'rgb(37, 99, 235)');
  await expect(sync).toHaveCSS('color', 'rgb(255, 255, 255)');
  await sync.focus();
  await expect(sync).toHaveCSS('outline-color', 'rgb(37, 99, 235)');
  for (const name of ['来源与素材', '生产批次', '发布工作台', 'YouTube 连接']) {
    await page.getByRole('button', { name, exact: true }).click();
    await expect(page.locator('nav [aria-current="page"]')).toHaveCSS(
      'color',
      'rgb(37, 99, 235)',
    );
    await expect(page.locator('nav [aria-current="page"]')).toHaveCSS(
      'background-color',
      'rgb(239, 246, 255)',
    );
    await page.setViewportSize({ width: 390, height: 844 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
    await page.setViewportSize({ width: 1440, height: 1000 });
  }
  await expect(page.locator('.connection-card').first()).toHaveCSS(
    'background-color',
    'rgb(255, 255, 255)',
  );
});

test('pages show task content without brand slogans or repeated overview blocks', async ({
  page,
}) => {
  const writes: string[] = [];
  page.on('request', (request) => {
    if (request.method() !== 'GET') writes.push(request.method());
  });
  await page.goto('/');
  await expect(page.getByRole('region', { name: '来源概览' })).toBeVisible();
  for (const name of ['来源与素材', '生产批次', '发布工作台', 'YouTube 连接']) {
    await page.getByRole('button', { name, exact: true }).click();
    await expect(page.getByRole('heading', { level: 1, name })).toBeVisible();
    await expect(
      page.locator('.hero, .pipeline, .sidebar-note, .workspace-label, .eyebrow, footer'),
    ).toHaveCount(0);
    await expect(
      page.getByText(/REAL SOURCES|流程可靠|CONTENT OPERATIONS|让素材流动/),
    ).toHaveCount(0);
    if (name !== '来源与素材') await expect(page.locator('.metrics')).toHaveCount(0);
  }
  await expect(page.getByRole('button', { name: '断开并撤销授权' })).toBeVisible();
  await page.getByRole('button', { name: '生产批次', exact: true }).click();
  await expect(page.getByText('初筛正常2次调用', { exact: false })).toBeVisible();
  await page.getByRole('button', { name: '发布工作台', exact: true }).click();
  await expect(page.getByText('转载 · 保留原始来源')).toBeVisible();
  expect(writes).toEqual([]);
});
