import { expect, test } from '@playwright/test';
import { EXTENSION_ID } from '../packages/browser-bridge/src/index';
import { launchExtension } from './extension-fixture';

test('real unpacked extension pairs with an isolated bridge, syncs only after consent and revokes', async ({
  browserName,
}, testInfo) => {
  expect(browserName).toBe('chromium');
  const fixture = await launchExtension(testInfo);
  try {
    const { context, manager } = fixture;
    if (!context.serviceWorkers().length) await context.waitForEvent('serviceworker');
    await context.addCookies([
      { name: 'SAPISID', value: 'synthetic-e2e-only', url: 'https://www.youtube.com/' },
    ]);
    const page = await context.newPage();
    await page.route('http://127.0.0.1:8766/**', (route) =>
      route.fulfill({
        contentType: 'text/html',
        body: '<h1>Isolated pairing sender</h1>',
      }),
    );
    await page.goto('http://127.0.0.1:8766/');
    const opened = context.waitForEvent('page');
    const result: unknown = await page.evaluate(
      ({ id, code }) => chrome.runtime.sendMessage(id, { action: 'pair', code }),
      { id: EXTENSION_ID, code: manager.beginPair().code },
    );
    expect(result).toEqual({ ok: true });
    const extension = await opened;
    await expect(
      extension.getByRole('heading', { name: '连接你的 YouTube' }),
    ).toBeVisible();
    expect(manager.status().status).toBe('disconnected');
    expect(await manager.current()).toBeNull();
    await expect(extension.locator('#auto')).not.toBeChecked();
    await extension.locator('#consent').check();
    await extension.getByRole('button', { name: '授权并连接' }).click();
    await expect(extension.getByRole('status')).toContainText('账号与推荐首页验证通过');
    expect(manager.status().status).toBe('connected');
    expect(manager.status().account_name).toBe('Synthetic account');
    await extension.getByRole('button', { name: '撤销连接' }).click();
    await expect(extension.getByRole('status')).toContainText('本机连接已撤销');
    expect(await manager.current()).toBeNull();
    expect(manager.status().paired).toBe(false);
    await extension.screenshot({ path: testInfo.outputPath('extension-revoked.png') });
  } finally {
    await fixture.close();
  }
});

test('extension refuses a localhost page on an unapproved port', async ({
  browserName,
}, testInfo) => {
  expect(browserName).toBe('chromium');
  const fixture = await launchExtension(testInfo);
  try {
    const page = await fixture.context.newPage();
    await page.route('http://127.0.0.1:8765/**', (route) =>
      route.fulfill({ contentType: 'text/html', body: 'Untrusted port' }),
    );
    await page.goto('http://127.0.0.1:8765/');
    const result: unknown = await page.evaluate(
      ({ id, code }) => chrome.runtime.sendMessage(id, { action: 'pair', code }),
      { id: EXTENSION_ID, code: fixture.manager.beginPair().code },
    );
    expect(result).toEqual({ ok: false, message: '来源或配对请求不受信。' });
    expect(fixture.manager.status().paired).toBe(false);
  } finally {
    await fixture.close();
  }
});
