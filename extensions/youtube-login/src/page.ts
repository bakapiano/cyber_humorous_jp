import { isRecord, YOUTUBE_URL } from '@youmo/browser-bridge';
import { pendingCode, readState } from './state';
import './page.css';

function input(id: string): HTMLInputElement {
  const element = document.getElementById(id);
  if (!(element instanceof HTMLInputElement)) throw new Error('Missing input');
  return element;
}
function element(id: string): HTMLElement {
  const value = document.getElementById(id);
  if (!value) throw new Error('Missing UI');
  return value;
}
function display(message: string): void {
  element('status').textContent = message;
}
async function refresh(): Promise<void> {
  const state = await readState();
  element('paired-actions').hidden = !state.grant;
  element('toggle').textContent = state.autoSync ? '暂停自动同步' : '允许自动同步';
  display(state.message);
}
async function action(value: object): Promise<void> {
  for (const button of document.querySelectorAll('button')) button.disabled = true;
  display('正在连接或验证，请稍等…');
  try {
    const response: unknown = await chrome.runtime.sendMessage(value);
    await refresh();
    if (isRecord(response) && typeof response.message === 'string')
      display(response.message);
    if (isRecord(response) && response.ok) input('code').value = '';
  } catch {
    display('扩展后台不可用，请重新打开授权页。');
  } finally {
    for (const button of document.querySelectorAll('button')) button.disabled = false;
  }
}
element('login').addEventListener('click', () => {
  void chrome.tabs
    .create({ url: YOUTUBE_URL })
    .catch(() => display('无法打开 YouTube。'));
});
element('pair-form').addEventListener('submit', (event) => {
  event.preventDefault();
  void action({
    action: 'connect',
    code: input('code').value.trim(),
    consent: input('consent').checked,
    autoSync: input('auto').checked,
  });
});
element('sync').addEventListener('click', () => {
  void action({ action: 'sync' });
});
element('disconnect').addEventListener('click', () => {
  void action({ action: 'disconnect' });
});
element('toggle').addEventListener('click', () => {
  void readState()
    .then((state) => action({ action: 'automatic', enabled: !state.autoSync }))
    .catch(() => display('扩展存储不可用。'));
});
void pendingCode()
  .then((code) => {
    input('code').value = code;
    return refresh();
  })
  .catch(() => display('扩展存储不可用。'));
