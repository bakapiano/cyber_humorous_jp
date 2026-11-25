import { describe, expect, it } from 'vitest';
import { createApp } from './app.js';
import { GatewayError } from './contracts.js';
import type { DiscoveryVideo, YoutubeProvider } from './contracts.js';
import { LiveYoutubeProvider } from './providers/live.js';

const token = 'isolated-test-token-not-a-real-credential';
const video: DiscoveryVideo = {
  video_id: 'abcdefghijk',
  title: '离线来源测试',
  origin: 'home',
  published_at: null,
};
const provider: YoutubeProvider = {
  home: () =>
    Promise.resolve({
      items: [video],
      warnings: [],
      skipped_count: 0,
      continuation: null,
    }),
  video: () => Promise.resolve(video),
};

describe('internal gateway', () => {
  it('only health is anonymous', async () => {
    const app = createApp(provider, token);
    expect((await app.inject('/health')).statusCode).toBe(200);
    expect((await app.inject('/internal/v1/videos/abcdefghijk')).statusCode).toBe(401);
    await app.close();
  });
  it('serves typed metadata with internal authentication', async () => {
    const app = createApp(provider, token);
    const response = await app.inject({
      url: '/internal/v1/videos/abcdefghijk',
      headers: { authorization: `Bearer ${token}` },
    });
    expect(response.statusCode).toBe(200);
    expect(response.body).toContain('abcdefghijk');
    expect(response.body).not.toContain(token);
    await app.close();
  });
  it('denies browser access even with token', async () => {
    const app = createApp(provider, token);
    const response = await app.inject({
      url: '/internal/v1/videos/abcdefghijk',
      headers: { authorization: `Bearer ${token}`, origin: 'http://localhost:5173' },
    });
    expect(response.statusCode).toBe(403);
    await app.close();
  });
  it('rejects invalid limits', async () => {
    const app = createApp(provider, token);
    const response = await app.inject({
      method: 'POST',
      url: '/internal/v1/home-feed',
      headers: { authorization: `Bearer ${token}` },
      payload: { profile_id: 'default', limit: 500 },
    });
    expect(response.statusCode).toBe(400);
    await app.close();
  });
  it('does not expose upstream exception text', async () => {
    const failing: YoutubeProvider = {
      ...provider,
      video: () => Promise.reject(new Error('cookie=SECRET')),
    };
    const app = createApp(failing, token);
    const response = await app.inject({
      url: '/internal/v1/videos/abcdefghijk',
      headers: { authorization: `Bearer ${token}` },
    });
    expect(response.statusCode).toBe(502);
    expect(response.body).not.toContain('SECRET');
    await app.close();
  });
  it('reports absent login without falling back to anonymous home', async () => {
    await expect(
      new LiveYoutubeProvider(undefined).home({ profile_id: 'default' }),
    ).rejects.toMatchObject({ code: 'PROFILE_NOT_CONFIGURED' });
  });
  it('preserves explicit safe error codes', async () => {
    const failing: YoutubeProvider = {
      ...provider,
      home: () => Promise.reject(new GatewayError('AUTH_REQUIRED', '会话失效')),
    };
    const app = createApp(failing, token);
    const response = await app.inject({
      method: 'POST',
      url: '/internal/v1/home-feed',
      headers: { authorization: `Bearer ${token}` },
      payload: { profile_id: 'default' },
    });
    expect(response.body).toContain('AUTH_REQUIRED');
    await app.close();
  });
});
