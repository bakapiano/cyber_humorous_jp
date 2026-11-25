import { isRecord } from '@youmo/browser-bridge';

export interface ExtensionState {
  grant: string | null;
  autoSync: boolean;
  message: string;
}
export const INITIAL: ExtensionState = {
  grant: null,
  autoSync: false,
  message: '尚未授权同步。',
};
export async function readState(): Promise<ExtensionState> {
  const raw: unknown = (await chrome.storage.local.get('connection')).connection;
  if (
    !isRecord(raw) ||
    (raw.grant !== null && typeof raw.grant !== 'string') ||
    typeof raw.autoSync !== 'boolean' ||
    typeof raw.message !== 'string'
  )
    return { ...INITIAL };
  return { grant: raw.grant, autoSync: raw.autoSync, message: raw.message };
}
export async function writeState(value: ExtensionState): Promise<void> {
  await chrome.storage.local.setAccessLevel({ accessLevel: 'TRUSTED_CONTEXTS' });
  await chrome.storage.local.set({ connection: value });
}
export async function configureAutomatic(enabled: boolean): Promise<void> {
  await chrome.alarms.clear('youtube-periodic');
  await chrome.alarms.clear('youtube-changed');
  if (enabled) await chrome.alarms.create('youtube-periodic', { periodInMinutes: 30 });
}
export async function pendingCode(): Promise<string> {
  const value: unknown = (await chrome.storage.session.get('pairing')).pairing;
  if (
    !isRecord(value) ||
    typeof value.code !== 'string' ||
    typeof value.expires !== 'number' ||
    value.expires <= Date.now()
  )
    return '';
  return value.code;
}
