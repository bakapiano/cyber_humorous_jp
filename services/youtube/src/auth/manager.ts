import { randomBytes, randomUUID, timingSafeEqual } from 'node:crypto';
import { EXTENSION_ID, validCookieHeader } from '@youmo/browser-bridge';
import { GatewayError } from '../contracts.js';
import { digest } from './legacy.js';
import { EMPTY_STATE } from './types.js';
import type {
  ConnectionStatus,
  CookieSource,
  Pairing,
  ProbeResult,
  SecretState,
  Vault,
} from './types.js';

type Publisher = (status: ConnectionStatus) => Promise<void>;
export class LoginManager implements CookieSource {
  private secret: SecretState = { ...EMPTY_STATE };
  private managed = false;
  private revision = randomUUID();
  private pending: { hash: string; expires: number } | null = null;
  private busy = false;
  private view: ConnectionStatus = {
    status: 'disconnected',
    paired: false,
    source: 'none',
    last_sync: null,
    last_verified: null,
    account_name: null,
    homepage_count: 0,
    message: '尚未连接 YouTube。',
  };
  probe: () => Promise<ProbeResult> = () =>
    Promise.reject(new Error('Probe not attached'));
  constructor(
    private readonly vault: Vault,
    private readonly publish: Publisher,
    private readonly legacy: CookieSource,
    private readonly clock: () => number = Date.now,
  ) {}

  async initialize(): Promise<void> {
    const saved = await this.vault.read();
    this.managed = saved !== null;
    this.secret = saved ?? { ...EMPTY_STATE };
    const cookie = await this.current();
    this.view = {
      ...this.view,
      paired: Boolean(this.secret.grantHash),
      source: saved ? (saved.grantHash ? 'extension' : 'none') : cookie ? 'file' : 'none',
      status: cookie ? 'received' : this.secret.grantHash ? 'paired' : 'disconnected',
      last_sync: this.secret.syncedAt,
      message: cookie ? '已保存会话；需要重新验证推荐页。' : '请连接扩展并授权同步。',
    };
    await this.publish(this.status());
  }
  status(): ConnectionStatus {
    return { ...this.view };
  }
  async current() {
    if (!this.managed) return this.legacy.current();
    return this.secret.cookie
      ? { cookie: this.secret.cookie, revision: this.revision }
      : null;
  }
  beginPair(): Pairing {
    const code = randomBytes(32).toString('base64url');
    const expires = this.clock() + 300000;
    this.pending = { hash: digest(code), expires };
    return {
      code,
      expires_at: new Date(expires).toISOString(),
      extension_id: EXTENSION_ID,
    };
  }
  async pair(code: string): Promise<{ grant: string }> {
    return this.exclusive(async () => {
      if (
        !this.pending ||
        this.pending.expires <= this.clock() ||
        this.pending.hash !== digest(code)
      )
        throw new GatewayError(
          'PAIRING_EXPIRED',
          '配对已失效或不匹配，请在工作台重新发起。',
          401,
        );
      this.pending = null;
      const grant = randomBytes(32).toString('base64url');
      await this.save({ ...EMPTY_STATE, grantHash: digest(grant) });
      this.view = {
        ...this.view,
        status: 'paired',
        source: 'extension',
        paired: true,
        account_name: null,
        last_sync: null,
        last_verified: null,
        homepage_count: 0,
        message: '扩展已配对，等待你授权同步 YouTube 会话。',
      };
      await this.publish(this.status());
      return { grant };
    });
  }
  authorize(grant: string): void {
    const expected = this.secret.grantHash;
    if (!expected || !timingSafeEqual(Buffer.from(digest(grant)), Buffer.from(expected)))
      throw new GatewayError('PAIRING_REQUIRED', '扩展连接已撤销，请重新配对。', 401);
  }
  async sync(grant: string, cookie: string | null): Promise<ConnectionStatus> {
    return this.exclusive(async () => {
      this.authorize(grant);
      if (cookie !== null && !validCookieHeader(cookie))
        throw new GatewayError(
          'COOKIE_FORMAT_INVALID',
          '仅接受限定范围的 YouTube 会话。',
          400,
        );
      await this.save({
        ...this.secret,
        cookie,
        syncedAt: new Date(this.clock()).toISOString(),
      });
      this.view = {
        ...this.view,
        status: cookie ? 'received' : 'expired',
        last_sync: this.secret.syncedAt,
        last_verified: null,
        account_name: null,
        homepage_count: 0,
        message: cookie
          ? '会话已收到，正在验证。'
          : '浏览器已退出登录；服务端会话已清除。',
      };
      await this.publish(this.status());
      return cookie ? this.check() : this.status();
    });
  }
  async verify(): Promise<ConnectionStatus> {
    return this.exclusive(() => this.check());
  }
  async disconnect(): Promise<ConnectionStatus> {
    return this.exclusive(async () => {
      this.pending = null;
      await this.save({ ...EMPTY_STATE });
      this.view = {
        status: 'disconnected',
        paired: false,
        source: 'none',
        last_sync: null,
        last_verified: null,
        account_name: null,
        homepage_count: 0,
        message: '已清除本地会话并撤销扩展授权；不会退出浏览器中的 Google 账号。',
      };
      await this.publish(this.status());
      return this.status();
    });
  }
  async failedAuthentication(): Promise<void> {
    if (this.busy || this.view.status !== 'connected') return;
    this.view = {
      ...this.view,
      status: 'expired',
      message: '登录验证失效，请重新登录并同步。',
    };
    await this.publish(this.status());
  }
  private async check(): Promise<ConnectionStatus> {
    if (!(await this.current())) return this.status();
    this.view = { ...this.view, status: 'checking', message: '正在验证账号和推荐首页。' };
    await this.publish(this.status());
    try {
      const result = await this.probe();
      this.view = {
        ...this.view,
        ...result,
        status: 'connected',
        last_verified: new Date(this.clock()).toISOString(),
        message: '账号与推荐首页验证通过。',
      };
    } catch (error) {
      const expired = error instanceof GatewayError && error.code === 'AUTH_REQUIRED';
      this.view = {
        ...this.view,
        status: expired ? 'expired' : 'error',
        account_name: null,
        homepage_count: 0,
        message: expired
          ? 'YouTube 要求重新登录或验证；请在浏览器完成后同步。'
          : '推荐页验证未通过；会话已保存，可检查网络后重试。',
      };
    }
    await this.publish(this.status());
    return this.status();
  }
  private async save(next: SecretState): Promise<void> {
    await this.vault.write(next);
    this.secret = next;
    this.managed = true;
    this.revision = randomUUID();
  }
  private async exclusive<T>(action: () => Promise<T>): Promise<T> {
    if (this.busy)
      throw new GatewayError('PROFILE_BUSY', '会话正在更新或验证，请稍后重试。', 429);
    this.busy = true;
    try {
      return await action();
    } finally {
      this.busy = false;
    }
  }
}
