import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { GatewayError } from '../contracts.js';
import type { CookieSnapshot, CookieSource } from './types.js';

export function digest(value: string): string {
  return createHash('sha256').update(value).digest('hex');
}
export class FileCookieSource implements CookieSource {
  constructor(private readonly path: string | undefined) {}
  async current(): Promise<CookieSnapshot | null> {
    if (!this.path) return null;
    let cookie: string;
    try {
      cookie = (await readFile(this.path, 'utf8')).trim();
    } catch {
      throw new GatewayError('PROFILE_NOT_CONFIGURED', '无法读取显式配置的会话文件。');
    }
    if (
      !cookie ||
      cookie.startsWith('#') ||
      cookie.startsWith('{') ||
      /[\r\n]/.test(cookie)
    )
      throw new GatewayError(
        'COOKIE_FORMAT_INVALID',
        '旧文件模式需要单行 Cookie 请求头值。',
      );
    return { cookie, revision: digest(cookie) };
  }
}
