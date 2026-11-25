import { Innertube, Log, YTNodes } from 'youtubei.js';
import type { CookieSnapshot, CookieSource } from '../auth/types.js';
import { GatewayError } from '../contracts.js';

export class YoutubeSession {
  private client: Innertube | null = null;
  private revision: string | null = null;
  constructor(private readonly source: CookieSource) {}
  clear(): void {
    this.client = null;
    this.revision = null;
  }
  async get(required: boolean): Promise<Innertube> {
    const snapshot = await this.source.current();
    if (required && !snapshot) {
      this.clear();
      throw new GatewayError('PROFILE_NOT_CONFIGURED', '请在工作台连接 YouTube 扩展。');
    }
    return this.forSnapshot(snapshot);
  }
  async account(client: Innertube): Promise<string> {
    const account = await client.account.getInfo().catch(() => {
      this.clear();
      throw new GatewayError(
        'UPSTREAM_UNAVAILABLE',
        '账号查询未完成，请检查网络或浏览器验证状态。',
        502,
      );
    });
    const items = account.contents?.contents.filter((node) =>
      node.is(YTNodes.AccountItem),
    );
    const item = items?.find((node) => node.is_selected) ?? items?.[0];
    if (!item || !client.session.logged_in) {
      this.clear();
      throw new GatewayError(
        'AUTH_REQUIRED',
        '会话未通过账号查询校验，请重新登录并同步。',
      );
    }
    return item.account_name.toString();
  }
  private async forSnapshot(snapshot: CookieSnapshot | null): Promise<Innertube> {
    const revision = snapshotRevision(snapshot);
    if (this.client && this.revision === revision) return this.client;
    const client = await createClient(snapshot?.cookie ?? '');
    if (snapshot) await this.account(client);
    if (snapshotRevision(await this.source.current()) !== revision)
      throw new GatewayError('SESSION_CHANGED', '会话已更新，请重试。');
    this.client = client;
    this.revision = revision;
    return client;
  }
}
function snapshotRevision(snapshot: CookieSnapshot | null): string {
  return snapshot ? snapshot.revision : 'public';
}
function createClient(cookie: string): Promise<Innertube> {
  Log.setLevel(Log.Level.NONE);
  return Innertube.create({
    lang: 'ja',
    location: 'JP',
    retrieve_player: false,
    cookie,
    fetch: (input, init) => fetch(input, { ...init, signal: AbortSignal.timeout(20000) }),
  });
}
