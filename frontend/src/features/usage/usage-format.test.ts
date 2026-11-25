import { expect, it } from 'vitest';
import { cost, money } from './usage-format';
import type { Totals } from './usage-format';

const empty: Totals = {
  calls: 0,
  retries: 0,
  metered_calls: 0,
  unmetered_calls: 0,
  unpriced_calls: 0,
  input_tokens: 0,
  output_tokens: 0,
  cache_hit_tokens: 0,
  cache_miss_tokens: 0,
  cache_unknown_tokens: 0,
  reasoning_tokens: 0,
  total_tokens: 0,
  cost_nano_cny: 0,
};
it('formats the complete-call subtotal without partial-usage labels', () => {
  expect(money(20587000)).toContain('0.020587');
  expect(cost({ ...empty, cost_nano_cny: 20587000 })).toBe(money(20587000));
  expect(cost(empty)).toContain('0.0000');
});
