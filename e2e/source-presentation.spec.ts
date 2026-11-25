import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import type { components } from '../packages/api-types/src/generated/api';

const sources: components['schemas']['SourceDTO'][] = Array.from(
  { length: 14 },
  (_, index) => ({
    id: `channel${index}`,
    name: `测试频道 ${index}`,
    url: `https://www.youtube.com/@channel${index}`,
    enabled: true,
    evidence_count: 2,
    video_count: 5,
  }),
);
const videos: components['schemas']['VideoDTO'][] = [0, 1, 2, 3].map((index) => ({
  video_id: `test-video-${index}`,
  source_id: `channel${index % 2}`,
  title: `测试素材 ${index}`,
  published_at:
    ['2026-11-06T01:00:00Z', '2026-11-05T16:30:00Z', '2026-11-04T01:00:00Z', null][
      index
    ] ?? null,
  discovered_at: '2026-11-06T04:00:00Z',
  revision: 1,
  screened: false,
  origin: 'channel',
}));

async function mockSources(page: Page) {
  await page.clock.setFixedTime(new Date('2026-11-06T12:00:00Z'));
  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/v1/sources') return route.fulfill({ json: sources });
    if (url.pathname === '/api/v1/videos') {
      const source = url.searchParams.get('source_id');
      const items = videos.filter((row) => !source || row.source_id === source);
      const offset = Number(url.searchParams.get('offset'));
      const limit = Number(url.searchParams.get('limit'));
      return route.fulfill({
        json: { items: items.slice(offset, offset + limit), total: items.length },
      });
    }
    return route.fulfill({ json: null });
  });
  await page.route('**/source-avatars.json', (route) =>
    route.fulfill({
      json: {
        avatars: { channel0: '/source-avatars/UC6AG81pAkf6Lbi_1VC5NmPA.png' },
      },
    }),
  );
}

test('source pagination, local avatars, source filtering and hourly windows', async ({
  page,
}) => {
  const writes: string[] = [];
  page.on('request', (request) => {
    if (request.method() !== 'GET') writes.push(request.method());
  });
  await mockSources(page);
  await page.goto('/');
  await expect(page.locator('.channel-card')).toHaveCount(6);
  const avatar = page.getByAltText('测试频道 0 头像');
  await expect(avatar).toBeVisible();
  await expect
    .poll(() =>
      avatar.evaluate(
        (image) => image instanceof HTMLImageElement && image.naturalWidth > 0,
      ),
    )
    .toBe(true);
  await expect(
    page.getByRole('link', { name: '在 YouTube 打开 测试频道 0' }),
  ).toHaveAttribute('href', 'https://www.youtube.com/@channel0');
  await page.getByRole('button', { name: '频道下一页' }).click();
  await expect(page.getByRole('navigation', { name: '来源频道分页' })).toContainText(
    '2 / 3',
  );
  await page.getByRole('button', { name: '频道下一页' }).click();
  await expect(page.locator('.channel-card')).toHaveCount(2);
  await expect(page.getByRole('button', { name: '频道下一页' })).toBeDisabled();
  await page.getByRole('button', { name: '频道上一页' }).click();
  await page.getByRole('button', { name: '频道上一页' }).click();
  const chart = page.getByRole('region', { name: '来源发布时间走势' });
  await expect(chart.locator('[data-posting-hour="9"]')).toHaveAttribute(
    'data-count',
    '2',
  );
  await expect(chart.locator('[data-posting-hour="0"]')).toHaveAttribute(
    'data-count',
    '1',
  );
  await expect(chart).toContainText('另有 1 条缺少有效发布时间');
  await page.getByRole('button', { name: '近24小时', exact: true }).click();
  await expect(chart.locator('[data-posting-hour="9"]')).toHaveAttribute(
    'data-count',
    '1',
  );
  await page.getByTitle('筛选 测试频道 0 的素材').click();
  await expect(chart).toContainText('@channel0');
  await expect(chart.locator('[data-posting-hour="0"]')).toHaveAttribute(
    'data-count',
    '0',
  );
  await page.getByRole('button', { name: '近7天', exact: true }).click();
  await expect(chart.locator('[data-posting-hour="9"]')).toHaveAttribute(
    'data-count',
    '2',
  );
  await page.getByRole('button', { name: '清除频道筛选' }).click();
  await expect(chart.locator('[data-posting-hour="0"]')).toHaveAttribute(
    'data-count',
    '1',
  );
  await page.screenshot({ path: '.tmp/source-presentation/desktop.png', fullPage: true });
  expect(writes).toEqual([]);
});

test('source chart fits mobile and keeps the material feed reachable', async ({
  page,
}) => {
  await mockSources(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await expect(page.locator('[data-posting-hour="9"]')).toHaveAttribute(
    'data-count',
    '2',
  );
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
  ).toBe(true);
  await page.getByRole('button', { name: '频道下一页' }).click();
  await expect(page.locator('.channel-card')).toHaveCount(6);
  await page.getByRole('heading', { name: /素材池/ }).scrollIntoViewIfNeeded();
  await expect(page.getByRole('heading', { name: /素材池/ })).toBeVisible();
  await page.screenshot({ path: '.tmp/source-presentation/mobile.png', fullPage: true });
});
