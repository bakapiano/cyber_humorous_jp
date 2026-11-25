import { expect, it } from 'vitest';
import { EXTENSION_ID } from '@youmo/browser-bridge';
import { createApp } from '../app.js';
import type { YoutubeProvider } from '../contracts.js';
import { testLogin } from './testing.js';

const token = 'isolated-internal-service-test-token';
const origin = `chrome-extension://${EXTENSION_ID}`;
const provider: YoutubeProvider = {
  home: () => Promise.resolve({ items: [], warnings: [], skipped_count: 0 }),
  video: () => Promise.reject(new Error('Never called')),
};

it('denies webpages, foreign extensions, missing origins and DNS rebinding', async () => {
  const { manager } = await testLogin();
  const app = createApp(provider, token, manager);
  for (const supplied of [
    '',
    'null',
    'https://evil.example',
    'http://127.0.0.1:8766',
    'chrome-extension://other',
  ]) {
    const response = await app.inject({
      method: 'POST',
      url: '/bridge/v1/pair',
      headers: { origin: supplied },
      payload: { code: manager.beginPair().code },
    });
    expect(response.statusCode).toBe(403);
  }
  expect(
    (
      await app.inject({
        method: 'OPTIONS',
        url: '/bridge/v1/session',
        headers: { origin, host: 'evil.example' },
      })
    ).statusCode,
  ).toBe(403);
  await app.close();
});

it('pairs only through the extension and never returns its grant to status endpoints', async () => {
  const { manager } = await testLogin();
  const app = createApp(provider, token, manager);
  const response = await app.inject({
    method: 'POST',
    url: '/bridge/v1/pair',
    headers: { origin },
    payload: { code: manager.beginPair().code },
  });
  expect(response.statusCode).toBe(200);
  const paired = response.json<{ grant: string }>();
  const session = await app.inject({
    method: 'POST',
    url: '/bridge/v1/session',
    headers: { origin, authorization: `Bearer ${paired.grant}` },
    payload: { cookie: 'SAPISID=synthetic-secret' },
  });
  expect(session.statusCode).toBe(200);
  expect(session.body).not.toContain('synthetic-secret');
  const status = await app.inject({
    url: '/internal/v1/auth/status',
    headers: { authorization: `Bearer ${token}` },
  });
  expect(status.body).not.toContain(paired.grant);
  expect(status.body).not.toContain('synthetic-secret');
  expect(
    (
      await app.inject({
        url: '/internal/v1/auth/status',
        headers: { origin, authorization: `Bearer ${token}` },
      })
    ).statusCode,
  ).toBe(403);
  await app.close();
});

it('requires a valid grant before reading body and revokes it on disconnect', async () => {
  const { manager } = await testLogin();
  const app = createApp(provider, token, manager);
  const grant = (await manager.pair(manager.beginPair().code)).grant;
  const headers = { origin, authorization: `Bearer ${grant}` };
  expect(
    (
      await app.inject({
        method: 'POST',
        url: '/bridge/v1/session',
        headers: { origin },
        payload: { cookie: 'SAPISID=synthetic' },
      })
    ).statusCode,
  ).toBe(401);
  expect(
    (
      await app.inject({
        method: 'POST',
        url: '/bridge/v1/session',
        headers,
        payload: { cookie: 'SAPISID=synthetic', unexpected: true },
      })
    ).statusCode,
  ).toBe(400);
  expect(
    (await app.inject({ method: 'DELETE', url: '/bridge/v1/session', headers }))
      .statusCode,
  ).toBe(200);
  expect(
    (
      await app.inject({
        method: 'POST',
        url: '/bridge/v1/session',
        headers,
        payload: { cookie: 'SAPISID=synthetic' },
      })
    ).statusCode,
  ).toBe(401);
  await app.close();
});

it('preflight grants only its fixed extension origin', async () => {
  const { manager } = await testLogin();
  const app = createApp(provider, token, manager);
  const response = await app.inject({
    method: 'OPTIONS',
    url: '/bridge/v1/session',
    headers: { origin },
  });
  expect(response.statusCode).toBe(204);
  expect(response.headers['access-control-allow-origin']).toBe(origin);
  expect(response.headers['cache-control']).toBe('no-store');
  await app.close();
});

it('gives the download session only to the authenticated non-browser worker', async () => {
  const { manager } = await testLogin();
  const app = createApp(provider, token, manager);
  const url = '/internal/v1/auth/download-session';
  const headers = { authorization: `Bearer ${token}` };
  expect((await app.inject({ url, headers })).statusCode).toBe(409);
  const grant = (await manager.pair(manager.beginPair().code)).grant;
  await manager.sync(grant, 'SAPISID=synthetic-secret');
  expect((await app.inject({ url })).statusCode).toBe(401);
  for (const browserHeaders of [{ origin }, { 'sec-fetch-site': 'none' }]) {
    const denied = await app.inject({ url, headers: { ...headers, ...browserHeaders } });
    expect(denied.statusCode).toBe(403);
    expect(denied.body).not.toContain('synthetic-secret');
  }
  const session = await app.inject({ url, headers });
  expect(session.statusCode).toBe(200);
  expect(session.json<{ cookie: string }>().cookie).toBe('SAPISID=synthetic-secret');
  expect(session.headers['cache-control']).toBe('no-store');
  await manager.disconnect();
  expect((await app.inject({ url, headers })).statusCode).toBe(409);
  await app.close();
});

it('treats verification in progress as temporary without expiring the session', async () => {
  const { manager } = await testLogin();
  const app = createApp(provider, token, manager);
  const headers = { authorization: `Bearer ${token}` };
  const url = '/internal/v1/auth/download-session';
  const grant = (await manager.pair(manager.beginPair().code)).grant;
  let release = () => {};
  manager.probe = () =>
    new Promise((resolve) => {
      release = () => resolve({ account_name: 'Synthetic account', homepage_count: 2 });
    });
  const syncing = manager.sync(grant, 'SAPISID=synthetic-secret');
  await new Promise<void>((resolve) => setImmediate(resolve));
  const waiting = await app.inject({ url, headers });
  expect(waiting.statusCode).toBe(503);
  expect(waiting.json<{ code: string }>().code).toBe('SESSION_REFRESHING');
  expect(waiting.body).not.toContain('synthetic-secret');
  expect(manager.status().status).toBe('checking');
  release();
  await syncing;
  expect((await app.inject({ url, headers })).statusCode).toBe(200);
  manager.probe = () => Promise.reject(new Error('private-error'));
  await manager.verify();
  const failed = await app.inject({ url, headers });
  expect(failed.statusCode).toBe(503);
  expect(failed.json<{ code: string }>().code).toBe('SESSION_VERIFY_UNAVAILABLE');
  expect(failed.body).not.toContain('private-error');
  await app.close();
});
