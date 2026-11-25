import { expect, it } from 'vitest';
import { GatewayError } from '../contracts.js';
import { LoginManager } from './manager.js';
import { testLogin } from './testing.js';

const cookie = 'SAPISID=synthetic-cookie; SID=synthetic-id';

it('uses expiring single-use codes, never exposes credentials in status', async () => {
  let time = 0;
  const { manager, vault } = await testLogin(() => time);
  const first = manager.beginPair();
  time = 300001;
  await expect(manager.pair(first.code)).rejects.toMatchObject({
    code: 'PAIRING_EXPIRED',
  });
  const pair = manager.beginPair();
  const { grant } = await manager.pair(pair.code);
  await expect(manager.pair(pair.code)).rejects.toMatchObject({
    code: 'PAIRING_EXPIRED',
  });
  expect(vault.saved?.grantHash).not.toBe(grant);
  const status = await manager.sync(grant, cookie);
  expect(status.status).toBe('connected');
  expect(JSON.stringify(status)).not.toContain('synthetic-cookie');
  expect(JSON.stringify(status)).not.toContain(grant);
});

it('rotates grants, hot-loads updates, and revokes cookies and pending pairing', async () => {
  const { manager, vault } = await testLogin();
  const { grant } = await manager.pair(manager.beginPair().code);
  await manager.sync(grant, cookie);
  const original = await manager.current();
  await manager.sync(grant, 'SAPISID=new-synthetic-cookie');
  expect((await manager.current())?.revision).not.toBe(original?.revision);
  const pending = manager.beginPair();
  await manager.disconnect();
  expect(await manager.current()).toBeNull();
  expect(vault.saved?.cookie).toBeNull();
  await expect(manager.sync(grant, cookie)).rejects.toMatchObject({
    code: 'PAIRING_REQUIRED',
  });
  await expect(manager.pair(pending.code)).rejects.toMatchObject({
    code: 'PAIRING_EXPIRED',
  });
});

it('re-pairing replaces the old grant and refuses non-YouTube header names', async () => {
  const { manager } = await testLogin();
  const old = await manager.pair(manager.beginPair().code);
  const current = await manager.pair(manager.beginPair().code);
  expect(() => manager.authorize(old.grant)).toThrow(GatewayError);
  await expect(
    manager.sync(current.grant, `${cookie}; bank_session=forbidden`),
  ).rejects.toMatchObject({ code: 'COOKIE_FORMAT_INVALID' });
  await expect(
    manager.sync(current.grant, 'SAPISID=bad\r\nAuthorization: bad'),
  ).rejects.toMatchObject({ code: 'COOKIE_FORMAT_INVALID' });
  expect(await manager.current()).toBeNull();
});

it('keeps upstream errors distinct from successful login and clears browser logout', async () => {
  const { manager, views } = await testLogin();
  const { grant } = await manager.pair(manager.beginPair().code);
  manager.probe = () => Promise.reject(new GatewayError('AUTH_REQUIRED', cookie));
  expect((await manager.sync(grant, cookie)).status).toBe('expired');
  manager.probe = () => Promise.reject(new Error(cookie));
  expect((await manager.verify()).status).toBe('error');
  expect(JSON.stringify(views)).not.toContain('synthetic-cookie');
  await manager.sync(grant, null);
  expect(await manager.current()).toBeNull();
  expect(manager.status().paired).toBe(true);
});

it('restores persisted sessions without claiming a fresh verification', async () => {
  const { manager, vault } = await testLogin();
  const { grant } = await manager.pair(manager.beginPair().code);
  await manager.sync(grant, cookie);
  const restored = new LoginManager(vault, () => Promise.resolve(), {
    current: () => Promise.resolve(null),
  });
  await restored.initialize();
  expect(restored.status().status).toBe('received');
  expect(restored.status().last_verified).toBeNull();
  expect((await restored.current())?.cookie).toBe(cookie);
  await restored.disconnect();
  const afterRevoke = new LoginManager(vault, () => Promise.resolve(), {
    current: () => Promise.resolve({ cookie, revision: 'legacy' }),
  });
  await afterRevoke.initialize();
  expect(await afterRevoke.current()).toBeNull();
});

it('rejects concurrent mutations while verification is running', async () => {
  const { manager } = await testLogin();
  const { grant } = await manager.pair(manager.beginPair().code);
  const pending = manager.sync(grant, cookie);
  await expect(manager.sync(grant, cookie)).rejects.toMatchObject({
    code: 'PROFILE_BUSY',
  });
  await pending;
  await manager.failedAuthentication();
  expect(manager.status().status).toBe('expired');
});
