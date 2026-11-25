import { execFile } from 'node:child_process';
import { mkdir, readFile, rename, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { isRecord } from '@youmo/browser-bridge';
import { GatewayError } from '../contracts.js';
import type { SecretState, Vault } from './types.js';

export function dpapi(
  input: Buffer,
  operation: 'Protect' | 'Unprotect',
): Promise<Buffer> {
  if (process.platform !== 'win32')
    return Promise.reject(
      new GatewayError('VAULT_UNSUPPORTED', '会话保险库当前仅支持 Windows。'),
    );
  const command = [
    "$ErrorActionPreference='Stop'",
    'Add-Type -AssemblyName System.Security',
    '$bytes=[Convert]::FromBase64String([Console]::In.ReadToEnd())',
    `$result=[Security.Cryptography.ProtectedData]::${operation}($bytes,$null,[Security.Cryptography.DataProtectionScope]::CurrentUser)`,
    '[Console]::Out.Write([Convert]::ToBase64String($result))',
  ].join(';');
  return new Promise((resolve, reject) => {
    const executable = join(
      process.env.SystemRoot ?? 'C:\\Windows',
      'System32/WindowsPowerShell/v1.0/powershell.exe',
    );
    const child = execFile(
      executable,
      ['-NoProfile', '-NonInteractive', '-Command', command],
      { windowsHide: true, timeout: 10000, maxBuffer: 131072 },
      (error, stdout) => {
        if (error)
          reject(
            new GatewayError(
              'VAULT_UNAVAILABLE',
              '系统会话保险库不可用；未降级为明文存储。',
            ),
          );
        else resolve(Buffer.from(stdout.trim(), 'base64'));
      },
    );
    child.stdin?.on('error', () => {
      /* execFile reports the sanitized failure. */
    });
    child.stdin?.end(input.toString('base64'));
  });
}

function parseSecret(raw: string): SecretState {
  const value: unknown = JSON.parse(raw);
  if (
    !isRecord(value) ||
    value.version !== 1 ||
    !nullableString(value.grantHash) ||
    !nullableString(value.cookie) ||
    !nullableString(value.syncedAt)
  )
    throw new Error('Invalid vault');
  return {
    version: 1,
    grantHash: value.grantHash,
    cookie: value.cookie,
    syncedAt: value.syncedAt,
  };
}
function nullableString(value: unknown): value is string | null {
  return value === null || typeof value === 'string';
}

export async function atomicWrite(path: string, value: string | Buffer): Promise<void> {
  await mkdir(dirname(path), { recursive: true });
  await writeFile(`${path}.new`, value, { mode: 0o600 });
  await rename(`${path}.new`, path);
}

export class WindowsVault implements Vault {
  constructor(private readonly path: string) {}
  async read(): Promise<SecretState | null> {
    try {
      const encrypted = await readFile(this.path);
      return parseSecret((await dpapi(encrypted, 'Unprotect')).toString('utf8'));
    } catch (error) {
      if (isRecord(error) && error.code === 'ENOENT') return null;
      throw new GatewayError(
        'VAULT_UNAVAILABLE',
        '无法解锁保存的会话；请检查 Windows 用户，不读取其他浏览器。',
      );
    }
  }
  async write(value: SecretState): Promise<void> {
    const encrypted = await dpapi(Buffer.from(JSON.stringify(value)), 'Protect');
    await atomicWrite(this.path, encrypted);
  }
}
