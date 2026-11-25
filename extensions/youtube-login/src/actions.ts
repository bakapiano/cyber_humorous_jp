import { isRecord } from '@youmo/browser-bridge';
import { BridgeError, bridgeRequest, collectCookie, responseMessage } from './bridge';
import { configureAutomatic, INITIAL, readState, writeState } from './state';

let running = false;
export async function synchronize(): Promise<void> {
  const state = await readState();
  if (!state.grant) throw new BridgeError('尚未授权；请先完成配对。');
  const cookie = await collectCookie();
  const result = await bridgeRequest('/session', state.grant, { cookie });
  await writeState({ ...state, message: responseMessage(result) });
}

async function connect(value: Record<string, unknown>): Promise<void> {
  if (
    value.consent !== true ||
    typeof value.code !== 'string' ||
    !/^[A-Za-z0-9_-]{43}$/.test(value.code) ||
    typeof value.autoSync !== 'boolean'
  )
    throw new BridgeError('请填写有效配对码并明确确认授权。');
  const result = await bridgeRequest('/pair', null, { code: value.code });
  if (!isRecord(result) || typeof result.grant !== 'string')
    throw new BridgeError('配对未完成。');
  await writeState({
    grant: result.grant,
    autoSync: value.autoSync,
    message: '已配对，正在同步。',
  });
  await chrome.storage.session.remove('pairing');
  await configureAutomatic(value.autoSync);
  await synchronize();
}
async function disconnect(): Promise<void> {
  const state = await readState();
  if (state.grant) await bridgeRequest('/session', state.grant);
  await configureAutomatic(false);
  await chrome.storage.session.remove('pairing');
  await writeState({ ...INITIAL, message: '本机连接已撤销。' });
}
async function toggle(value: Record<string, unknown>): Promise<void> {
  const state = await readState();
  if (!state.grant || typeof value.enabled !== 'boolean')
    throw new BridgeError('请先配对。');
  await writeState({
    ...state,
    autoSync: value.enabled,
    message: value.enabled
      ? '已允许自动同步。'
      : '自动同步已暂停；已保存的本机会话仍可使用。',
  });
  await configureAutomatic(value.enabled);
}
export async function runAction(
  value: unknown,
): Promise<{ ok: boolean; message: string }> {
  if (running) return { ok: false, message: '正在同步或验证，请稍后。' };
  if (!isRecord(value)) return { ok: false, message: '请求无效。' };
  running = true;
  try {
    await dispatch(value);
    return { ok: true, message: (await readState()).message };
  } catch (error) {
    const message =
      error instanceof BridgeError ? error.message : '连接操作失败；未输出私有诊断。';
    if (error instanceof BridgeError && error.revoked) {
      await configureAutomatic(false);
      await writeState({ ...INITIAL, message });
    } else await writeState({ ...(await readState()), message });
    return { ok: false, message };
  } finally {
    running = false;
  }
}
async function dispatch(value: Record<string, unknown>): Promise<void> {
  switch (value.action) {
    case 'connect':
      return connect(value);
    case 'sync':
      return synchronize();
    case 'disconnect':
      return disconnect();
    case 'automatic':
      return toggle(value);
    default:
      throw new BridgeError('请求无效。');
  }
}
export async function automaticSync(): Promise<void> {
  const state = await readState();
  if (state.autoSync && state.grant) await runAction({ action: 'sync' });
}
