import type { FastifyInstance, FastifyReply, FastifyRequest } from 'fastify';
import { EXTENSION_ID } from '@youmo/browser-bridge';
import { GatewayError } from '../contracts.js';
import type { LoginManager } from './manager.js';

export async function bridgeGuard(
  request: FastifyRequest,
  reply: FastifyReply,
): Promise<void> {
  if (request.headers.origin !== `chrome-extension://${EXTENSION_ID}`)
    throw new GatewayError('ORIGIN_DENIED', '仅接受已安装的自有扩展。', 403);
  reply.header('Access-Control-Allow-Origin', `chrome-extension://${EXTENSION_ID}`);
  reply.header('Vary', 'Origin');
  reply.header('Cache-Control', 'no-store');
  if (request.method === 'OPTIONS') {
    reply.header('Access-Control-Allow-Methods', 'POST, DELETE, OPTIONS');
    reply.header('Access-Control-Allow-Headers', 'authorization, content-type');
    await reply.code(204).send();
  }
}

function grant(request: FastifyRequest): string {
  const value = request.headers.authorization ?? '';
  if (!/^Bearer [A-Za-z0-9_-]{43}$/.test(value))
    throw new GatewayError('PAIRING_REQUIRED', '扩展尚未获得同步授权。', 401);
  return value.slice(7);
}

function requireDownloadConnection(login: LoginManager): void {
  const state = login.status().status;
  if (state === 'received' || state === 'checking')
    throw new GatewayError('SESSION_REFRESHING', '会话正在同步或验证，请稍后重试。', 503);
  if (state === 'error')
    throw new GatewayError(
      'SESSION_VERIFY_UNAVAILABLE',
      '会话验证暂未完成，请检查网络后重试。',
      503,
    );
  if (state !== 'connected')
    throw new GatewayError('AUTH_REQUIRED', '下载前请验证已授权的 YouTube 连接。');
}

export function loginRoutes(app: FastifyInstance, login: LoginManager): void {
  app.get('/internal/v1/auth/status', () => login.status());
  app.get('/internal/v1/auth/download-session', async () => {
    requireDownloadConnection(login);
    const snapshot = await login.current();
    requireDownloadConnection(login);
    if (!snapshot) throw new GatewayError('AUTH_REQUIRED', 'YouTube 会话已撤销或更新。');
    // Internal bearer + loopback + no browser Origin/Fetch headers are enforced
    // by installSecurity. This route is never proxied by the public workbench.
    return snapshot;
  });
  app.post('/internal/v1/auth/pairing', () => login.beginPair());
  app.post('/internal/v1/auth/verify', () => login.verify());
  app.delete('/internal/v1/auth/session', () => login.disconnect());
  app.options('/bridge/v1/*', async (_request, reply) => reply.code(204).send());
  app.post<{ Body: { code: string } }>(
    '/bridge/v1/pair',
    {
      schema: {
        body: {
          type: 'object',
          additionalProperties: false,
          required: ['code'],
          properties: { code: { type: 'string', pattern: '^[A-Za-z0-9_-]{43}$' } },
        },
      },
    },
    async (request) => login.pair(request.body.code),
  );
  app.post<{ Body: { cookie: string | null } }>(
    '/bridge/v1/session',
    {
      bodyLimit: 20000,
      onRequest: (request, _reply, done) => {
        login.authorize(grant(request));
        done();
      },
      schema: {
        body: {
          type: 'object',
          additionalProperties: false,
          required: ['cookie'],
          properties: {
            cookie: { anyOf: [{ type: 'string', maxLength: 16384 }, { type: 'null' }] },
          },
        },
      },
    },
    async (request) => login.sync(grant(request), request.body.cookie),
  );
  app.delete('/bridge/v1/session', async (request) => {
    login.authorize(grant(request));
    return login.disconnect();
  });
}
