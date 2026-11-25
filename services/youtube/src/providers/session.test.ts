import { expect, it, vi } from 'vitest';
import type { CookieSnapshot } from '../auth/types.js';

const upstream = vi.hoisted(() => ({
  create: vi.fn(),
  account: vi.fn(),
}));
vi.mock('youtubei.js', () => ({
  Innertube: { create: upstream.create },
  YTNodes: { AccountItem: class {} },
  Log: { setLevel: vi.fn(), Level: { NONE: 0 } },
}));
import { YoutubeSession } from './session.js';

it('reuses sessions, reloads on revision changes and refuses cached login after revoke', async () => {
  upstream.account.mockResolvedValue({
    contents: {
      contents: [{ is: () => true, account_name: { toString: () => 'Synthetic' } }],
    },
  });
  upstream.create.mockImplementation(() =>
    Promise.resolve({
      session: { logged_in: true },
      account: { getInfo: upstream.account },
    }),
  );
  let snapshot: CookieSnapshot | null = {
    cookie: 'SAPISID=synthetic',
    revision: 'first',
  };
  const session = new YoutubeSession({ current: () => Promise.resolve(snapshot) });
  const first = await session.get(true);
  expect(await session.get(true)).toBe(first);
  snapshot = { cookie: 'SAPISID=updated', revision: 'second' };
  expect(await session.get(true)).not.toBe(first);
  snapshot = null;
  await expect(session.get(true)).rejects.toMatchObject({
    code: 'PROFILE_NOT_CONFIGURED',
  });
});

it('does not mislabel a network exception as a confirmed expired login', async () => {
  upstream.account.mockRejectedValue(new Error('synthetic-private-upstream-details'));
  upstream.create.mockImplementation(() =>
    Promise.resolve({
      session: { logged_in: true },
      account: { getInfo: upstream.account },
    }),
  );
  const session = new YoutubeSession({
    current: () => Promise.resolve({ cookie: 'SAPISID=synthetic', revision: 'new' }),
  });
  await expect(session.get(true)).rejects.toMatchObject({ code: 'UPSTREAM_UNAVAILABLE' });
  await expect(session.get(true)).rejects.not.toThrow(
    'synthetic-private-upstream-details',
  );
});
