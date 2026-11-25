import { afterEach, expect, it, vi } from 'vitest';
import { allowedPage, isPairMessage, validCookieHeader } from '@youmo/browser-bridge';
import { bridgeRequest, collectCookie } from './bridge';

afterEach(() => {
  vi.unstubAllGlobals();
});

it('allows exactly the workbench ports and validates one-time pairing messages', () => {
  expect(allowedPage('http://127.0.0.1:8766/')).toBe(true);
  expect(allowedPage('http://localhost:5173/')).toBe(true);
  for (const url of [
    'http://127.0.0.1:9999/',
    'http://localhost.evil:8766',
    'https://youtube.com',
    'garbage',
  ])
    expect(allowedPage(url)).toBe(false);
  expect(isPairMessage({ action: 'pair', code: 'a'.repeat(43) })).toBe(true);
  expect(isPairMessage({ action: 'pair', code: '<script>' })).toBe(false);
});

it('collects only allowed YouTube first-party root cookies, never other domains', async () => {
  const getAll = vi.fn().mockResolvedValue([
    { name: 'SAPISID', value: 'synthetic-only', domain: '.youtube.com', path: '/' },
    { name: 'SID', value: 'google-forbidden', domain: '.google.com', path: '/' },
    { name: 'random_cookie', value: 'forbidden', domain: '.youtube.com', path: '/' },
    {
      name: 'SSID',
      value: 'partitioned',
      domain: '.youtube.com',
      path: '/',
      partitionKey: {},
    },
    { name: 'HSID', value: 'other-path', domain: '.youtube.com', path: '/other' },
  ]);
  vi.stubGlobal('chrome', { cookies: { getAll } });
  expect(await collectCookie()).toBe('SAPISID=synthetic-only');
  expect(getAll).toHaveBeenCalledWith({ url: 'https://www.youtube.com/' });
});

it('sends logout as null and rejects header injection and duplicate names', async () => {
  vi.stubGlobal('chrome', { cookies: { getAll: vi.fn().mockResolvedValue([]) } });
  expect(await collectCookie()).toBeNull();
  expect(validCookieHeader('SAPISID=test; SAPISID=other')).toBe(false);
  expect(validCookieHeader('SAPISID=test\nSID=bad')).toBe(false);
  expect(validCookieHeader('SAPISID=' + 'x'.repeat(17000))).toBe(false);
});

it('only calls the fixed loopback bridge and sanitizes failures', async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValue(new Response('{"message":"synthetic response"}'));
  vi.stubGlobal('fetch', fetcher);
  await bridgeRequest('/session', 'synthetic-grant', { cookie: 'SAPISID=test' });
  expect(fetcher).toHaveBeenCalledWith(
    'http://127.0.0.1:8767/bridge/v1/session',
    expect.objectContaining({ credentials: 'omit', cache: 'no-store', method: 'POST' }),
  );
  fetcher.mockResolvedValue(new Response('private-upstream-error', { status: 401 }));
  await expect(bridgeRequest('/session', 'revoked', {})).rejects.toMatchObject({
    revoked: true,
  });
  fetcher.mockRejectedValue(new Error('private-cookie-path'));
  await expect(bridgeRequest('/session', null, {})).rejects.not.toThrow(
    'private-cookie-path',
  );
});
