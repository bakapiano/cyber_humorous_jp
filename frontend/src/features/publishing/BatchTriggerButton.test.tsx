import { afterEach, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { Schemas } from '../../api/client';
import { BatchTriggerButton } from './BatchTriggerButton';

afterEach(cleanup);

const plan: Schemas['BatchPlanState'] = {
  batch_size: 50,
  publications_per_batch: 2,
  auto_enabled: true,
  publication_mode: 'public_reprint',
  revision: 1,
  eligible_count: 100,
  active_batch_id: 'other-video-is-rendering',
  automatic_blocked: false,
  publisher_ready: false,
  buffer_used: 10,
  buffer_limit: 50,
  message: 'Independent queues',
};

function show(patch: Partial<Schemas['BatchPlanState']> = {}) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <BatchTriggerButton plan={{ ...plan, ...patch }} />
    </QueryClientProvider>,
  );
  return screen.getByRole('button', { name: '手动触发一次' });
}

it('allows a full new batch while other videos run or publisher login waits', () => {
  expect(show().hasAttribute('disabled')).toBe(false);
});

it('respects a model or storage dependency hold', () => {
  expect(show({ automatic_blocked: true }).hasAttribute('disabled')).toBe(true);
});

it('respects reserved capacity', () => {
  expect(show({ buffer_used: 49 }).hasAttribute('disabled')).toBe(true);
});

it('still requires a full candidate batch', () => {
  expect(show({ eligible_count: 49 }).hasAttribute('disabled')).toBe(true);
});
