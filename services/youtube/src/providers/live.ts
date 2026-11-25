import { randomUUID } from 'node:crypto';
import { YTNodes } from 'youtubei.js';
import type { Innertube } from 'youtubei.js';
import { FileCookieSource } from '../auth/legacy.js';
import type { CookieSource, ProbeResult } from '../auth/types.js';
import { YoutubeSession } from './session.js';
import { GatewayError } from '../contracts.js';
import type {
  DiscoveryPage,
  DiscoveryVideo,
  HomeRequest,
  YoutubeProvider,
} from '../contracts.js';
import { normalizeLockup, normalizeVideo } from './normalize.js';

type HomeFeed = Awaited<ReturnType<Innertube['getHomeFeed']>>;
interface Cursor {
  feed: HomeFeed;
  expires: number;
  profile: string;
  revision: string;
}

export class LiveYoutubeProvider implements YoutubeProvider {
  private readonly source: CookieSource;
  private readonly sessions: YoutubeSession;
  private cursors = new Map<string, Cursor>();
  private busy = new Set<string>();
  constructor(source: CookieSource | string | undefined) {
    this.source = typeof source === 'object' ? source : new FileCookieSource(source);
    this.sessions = new YoutubeSession(this.source);
  }

  async probe(): Promise<ProbeResult> {
    this.sessions.clear();
    const client = await this.sessions.get(true);
    const account_name = await this.sessions.account(client);
    const feed = await this.home({ profile_id: 'default', limit: 30 });
    return { account_name, homepage_count: feed.items.length };
  }

  async home(request: HomeRequest): Promise<DiscoveryPage> {
    if (request.profile_id !== 'default')
      throw new GatewayError(
        'PROFILE_NOT_CONFIGURED',
        '仅支持已配置的 default profile。',
      );
    if (this.busy.has(request.profile_id))
      throw new GatewayError('PROFILE_BUSY', '该账号正在读取推荐，请稍后重试。', 429);
    this.busy.add(request.profile_id);
    try {
      return await this.homePage(request);
    } finally {
      this.busy.delete(request.profile_id);
    }
  }

  private async homePage(request: HomeRequest): Promise<DiscoveryPage> {
    const revision = await this.revision();
    const client = await this.sessions.get(true);
    if (!client.session.logged_in)
      throw new GatewayError(
        'AUTH_REQUIRED',
        '未确认有效登录态，不返回公共首页冒充个性化推荐。',
      );
    const feed = request.continuation
      ? await this.next(request.continuation, request.profile_id, revision)
      : await client.getHomeFeed();
    const normalized = normalizeFeed(feed);
    const items = normalized.filter((item): item is DiscoveryVideo => item !== null);
    if (feed.videos.length && !items.length)
      throw new GatewayError('UPSTREAM_PARSE_ERROR', '首页结构未能解析为有效视频。', 502);
    if ((await this.revision()) !== revision)
      throw new GatewayError('SESSION_CHANGED', '会话已更新，请重新读取首页。');
    const continuation = feed.has_continuation
      ? this.remember(feed, request.profile_id, revision)
      : null;
    return {
      items: items.slice(0, request.limit ?? 30),
      continuation,
      skipped_count: normalized.length - items.length,
      warnings: ['推荐页不提供可靠精确发布时间；进入批次前需补齐。'],
    };
  }

  async video(id: string): Promise<DiscoveryVideo> {
    const client = await this.sessions.get(false);
    const response = await client.getBasicInfo(id);
    if (response.playability_status?.status === 'LOGIN_REQUIRED') {
      throw new GatewayError(
        'AUTH_REQUIRED',
        'YouTube 要求登录验证；请在工作台连接并同步会话后重试。',
        403,
      );
    }
    const info = response.basic_info;
    if (!info.title)
      throw new GatewayError('UPSTREAM_PARSE_ERROR', '视频没有有效标题。', 502);
    return {
      video_id: id,
      title: info.title,
      channel_id: info.channel_id ?? null,
      channel_title: info.author ?? null,
      thumbnail_url: info.thumbnail?.[0]?.url ?? null,
      published_at: null,
      duration_seconds: info.duration ?? null,
      origin: 'manual',
    };
  }

  private remember(feed: HomeFeed, profile: string, revision: string): string {
    for (const [id, cursor] of this.cursors)
      if (cursor.expires <= Date.now()) this.cursors.delete(id);
    if (this.cursors.size >= 100) this.cursors.clear();
    const id = randomUUID();
    this.cursors.set(id, { feed, profile, revision, expires: Date.now() + 300000 });
    return id;
  }

  private async revision(): Promise<string> {
    const snapshot = await this.source.current();
    if (!snapshot)
      throw new GatewayError('PROFILE_NOT_CONFIGURED', '会话已断开，请重新连接。');
    return snapshot.revision;
  }

  private async next(id: string, profile: string, revision: string): Promise<HomeFeed> {
    const cursor = this.cursors.get(id);
    if (
      !cursor ||
      cursor.profile !== profile ||
      cursor.revision !== revision ||
      cursor.expires <= Date.now()
    )
      throw new GatewayError('CURSOR_EXPIRED', '分页游标失效，请重新读取首页。');
    this.cursors.delete(id);
    return cursor.feed.getContinuation();
  }
}
function normalizeFeed(feed: HomeFeed) {
  return feed.videos.map((node) => {
    if (node.is(YTNodes.Video)) return normalizeVideo(node);
    if (node.is(YTNodes.LockupView)) return normalizeLockup(node);
    return null;
  });
}
