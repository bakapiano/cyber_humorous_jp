import { allowedPage, isPairMessage } from '@youmo/browser-bridge';
import { automaticSync, runAction } from './actions';
import { configureAutomatic, readState } from './state';

chrome.runtime.onMessageExternal.addListener((value: unknown, sender, respond) => {
  if (!allowedPage(sender.url) || sender.tab?.incognito || !isPairMessage(value)) {
    respond({ ok: false, message: '来源或配对请求不受信。' });
    return false;
  }
  void chrome.storage.session
    .set({ pairing: { code: value.code, expires: Date.now() + 300000 } })
    .then(() => chrome.tabs.create({ url: chrome.runtime.getURL('connect.html') }))
    .then(
      () => respond({ ok: true }),
      () => respond({ ok: false, message: '无法打开扩展授权页。' }),
    );
  return true;
});
chrome.runtime.onMessage.addListener((value: unknown, sender, respond) => {
  if (
    sender.id !== chrome.runtime.id ||
    !sender.url?.startsWith(chrome.runtime.getURL('')) ||
    sender.tab?.incognito
  ) {
    respond({ ok: false, message: '只允许扩展自身页面操作。' });
    return false;
  }
  void runAction(value).then(respond, () =>
    respond({ ok: false, message: '扩展存储不可用。' }),
  );
  return true;
});
chrome.cookies.onChanged.addListener((change) => {
  if (!/^(\.)?(www\.)?youtube\.com$/.test(change.cookie.domain)) return;
  void readState()
    .then(async (state) => {
      if (state.autoSync && state.grant)
        await chrome.alarms.create('youtube-changed', { delayInMinutes: 0.5 });
    })
    .catch(() => undefined);
});
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === 'youtube-periodic' || alarm.name === 'youtube-changed')
    void automaticSync().catch(() => undefined);
});
async function restore(): Promise<void> {
  await chrome.storage.local.setAccessLevel({ accessLevel: 'TRUSTED_CONTEXTS' });
  const state = await readState();
  await configureAutomatic(state.autoSync && Boolean(state.grant));
  await automaticSync();
}
chrome.runtime.onStartup.addListener(() => {
  void restore().catch(() => undefined);
});
chrome.runtime.onInstalled.addListener(() => {
  void restore().catch(() => undefined);
});
