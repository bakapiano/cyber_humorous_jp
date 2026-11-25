import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ExtensionState } from './state';
import { INITIAL } from './state';
import { automaticSync, runAction } from './actions';
import { BridgeError } from './bridge';
import type * as BridgeModule from './bridge';
import type * as StateModule from './state';

const fake = vi.hoisted(() => ({
  request: vi.fn(),
  cookies: vi.fn(),
  read: vi.fn(),
  write: vi.fn(),
  configure: vi.fn(),
}));
vi.mock('./bridge', async (original) => ({
  ...(await original<typeof BridgeModule>()),
  bridgeRequest: fake.request,
  collectCookie: fake.cookies,
}));
vi.mock('./state', async (original) => ({
  ...(await original<typeof StateModule>()),
  readState: fake.read,
  writeState: fake.write,
  configureAutomatic: fake.configure,
}));
let state: ExtensionState;
beforeEach(() => {
  vi.clearAllMocks();
  state = { ...INITIAL };
  fake.read.mockImplementation(() => Promise.resolve({ ...state }));
  fake.write.mockImplementation((value: ExtensionState) => {
    state = value;
    return Promise.resolve();
  });
  fake.configure.mockResolvedValue(undefined);
  fake.cookies.mockResolvedValue('SAPISID=synthetic');
  fake.request.mockImplementation((path: string) =>
    Promise.resolve(
      path === '/pair'
        ? { grant: 'a'.repeat(43) }
        : { message: 'Synthetic check passed' },
    ),
  );
  vi.stubGlobal('chrome', {
    storage: { session: { remove: vi.fn().mockResolvedValue(undefined) } },
  });
});
afterEach(() => {
  vi.unstubAllGlobals();
});

it('never reads cookies before explicit consent', async () => {
  await automaticSync();
  expect(
    (
      await runAction({
        action: 'connect',
        code: 'a'.repeat(43),
        consent: false,
        autoSync: false,
      })
    ).ok,
  ).toBe(false);
  expect(fake.cookies).not.toHaveBeenCalled();
  expect(fake.request).not.toHaveBeenCalled();
});

it('connects once, stores only grant and settings, and defaults to no automatic sync', async () => {
  const result = await runAction({
    action: 'connect',
    code: 'a'.repeat(43),
    consent: true,
    autoSync: false,
  });
  expect(result.ok).toBe(true);
  expect(state.autoSync).toBe(false);
  expect(JSON.stringify(state)).not.toContain('SAPISID');
  fake.cookies.mockClear();
  await automaticSync();
  expect(fake.cookies).not.toHaveBeenCalled();
});

it('auto sync needs opt-in and a revoked grant stops subsequent cookie access', async () => {
  state = { grant: 'a'.repeat(43), autoSync: true, message: '' };
  fake.request.mockRejectedValue(new BridgeError('Revoked', true));
  await automaticSync();
  expect(state.grant).toBeNull();
  expect(state.autoSync).toBe(false);
  fake.cookies.mockClear();
  await automaticSync();
  expect(fake.cookies).not.toHaveBeenCalled();
});

it('keeps a grant on network errors so revocation can be retried', async () => {
  state = { grant: 'a'.repeat(43), autoSync: true, message: '' };
  fake.request.mockRejectedValue(new BridgeError('Offline'));
  expect((await runAction({ action: 'disconnect' })).ok).toBe(false);
  expect(state.grant).not.toBeNull();
});
