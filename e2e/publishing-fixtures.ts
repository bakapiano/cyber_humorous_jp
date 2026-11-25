import type { Page } from '@playwright/test';
import type { components } from '../packages/api-types/src/generated/api';

type Schemas = components['schemas'];
export const batchPlan: Schemas['BatchPlanState'] = {
  batch_size: 50,
  publications_per_batch: 10,
  auto_enabled: false,
  publication_mode: 'private_reprint',
  revision: 0,
  eligible_count: 70,
  active_batch_id: null,
  automatic_blocked: false,
  message: '素材已够一批，可手动触发。',
  publisher_ready: true,
  buffer_limit: 50,
  buffer_used: 0,
};
export const scanPlan: Schemas['ScanPlanState'] = {
  enabled: true,
  interval_minutes: 30,
  revision: 0,
  scanning: false,
  next_run_at: '2026-11-06T15:00:00Z',
};
export const published: Schemas['PublicationDTO'] = {
  id: 'published',
  content_id: 'synthetic',
  title: '已投稿的离线测试稿件',
  manifest_hash: 'a'.repeat(64),
  confirmed: true,
  status: 'completed',
  visibility: 'private',
  remote_id: 'BV1234567890',
  test_only: false,
  created_at: '2026-11-06T03:00:00Z',
};

export function fixturePage<T>(items: T[], url: URL) {
  const offset = Number(url.searchParams.get('offset') ?? 0);
  const limit = Number(url.searchParams.get('limit') ?? 10);
  return {
    items: items.slice(offset, offset + limit),
    total: items.length,
    offset,
    limit,
  };
}

function activityFixture(url: URL): Schemas['PublicationActivity'] {
  return {
    since: '2026-11-03T03:00:00Z',
    until: '2026-11-06T03:00:00Z',
    timezone: 'Asia/Shanghai',
    interval: Number(url.searchParams.get('hours')) > 72 ? 'day' : 'hour',
    total: 3,
    buckets: [
      { start: '2026-11-06T01:00:00Z', label: '11-06 09:00', count: 2 },
      { start: '2026-11-06T02:00:00Z', label: '11-06 10:00', count: 1 },
    ],
  };
}

export async function setupPublishing(page: Page) {
  const state = {
    plan: { ...batchPlan },
    scan: { ...scanPlan },
    nextPlan: { ...batchPlan, batch_size: 60, publications_per_batch: 3, revision: 1 },
    nextScan: { ...scanPlan, interval_minutes: 60, revision: 1 },
    failTrigger: false,
    publications: [
      published,
      {
        ...published,
        id: 'prepared',
        title: '尚未投稿',
        remote_id: null,
        status: 'prepared',
      },
    ],
    writes: [] as { path: string; body: unknown }[],
  };
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (path === '/api/v1/workspace/publications') {
      await route.fulfill({ json: fixturePage(state.publications, url) });
      return;
    }
    if (request.method() !== 'GET') {
      const body: unknown = request.postDataJSON();
      state.writes.push({ path, body });
      if (path === '/api/v1/batch-plan') state.plan = state.nextPlan;
      if (path === '/api/v1/scan-plan') state.scan = state.nextScan;
      if (path === '/api/v1/production-batches' && state.failTrigger) {
        await route.fulfill({
          status: 409,
          json: { code: 'TEMPORARY', message: '隔离测试失败' },
        });
        return;
      }
      if (path === '/api/v1/production-batches') {
        state.plan.active_batch_id = 'new-batch';
        state.plan.eligible_count -= state.plan.batch_size;
      }
    }
    const bodies: Record<string, unknown> = {
      '/api/v1/batch-plan': state.plan,
      '/api/v1/scan-plan': state.scan,
      '/api/v1/workspace/publication-activity': activityFixture(url),
      '/api/v1/production-batches': { id: 'new-batch' },
      '/api/v1/session': { csrf_token: 'offline-only' },
      '/api/v1/sources': [],
      '/api/v1/videos': { items: [], total: 0 },
      '/api/v1/workspace/tasks': { items: [], total: 0, offset: 0, limit: 10 },
      '/api/v1/workspace/batches': { items: [], total: 0, offset: 0, limit: 10 },
      '/api/v1/dashboard': {
        sources: 0,
        enabled_sources: 0,
        videos: 0,
        eligible_videos: 0,
        batches: 0,
        failed_tasks: 0,
        policy: { candidate_window_hours: 72 },
        automation_enabled: false,
        publication_enabled: false,
      },
    };
    await route.fulfill({ json: bodies[path] ?? {} });
  });
  return state;
}
