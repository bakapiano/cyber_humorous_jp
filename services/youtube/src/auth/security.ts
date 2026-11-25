import type { FastifyInstance } from 'fastify';
import { timingSafeEqual } from 'node:crypto';
import { GatewayError } from '../contracts.js';
import type { LoginManager } from './manager.js';
import { bridgeGuard } from './routes.js';

export function installSecurity(
  app: FastifyInstance,
  token: string,
  login?: LoginManager,
): void {
  app.addHook('onRequest', async (request, reply) => {
    if (!['localhost', '127.0.0.1'].includes(request.hostname))
      throw new GatewayError('LOCAL_ONLY', '仅限本机访问。', 403);
    reply.header('Cache-Control', 'no-store');
    reply.header('X-Content-Type-Options', 'nosniff');
    if (request.url.startsWith('/bridge/')) {
      if (!login) throw new GatewayError('PROFILE_NOT_CONFIGURED', '登录桥接未启用。');
      return bridgeGuard(request, reply);
    }
    if (request.url === '/health') return;
    const supplied = Buffer.from(request.headers.authorization ?? '');
    const expected = Buffer.from(`Bearer ${token}`);
    if (supplied.length !== expected.length || !timingSafeEqual(supplied, expected)) {
      await reply
        .code(401)
        .send({ code: 'INTERNAL_UNAUTHORIZED', message: '内部服务凭据无效。' });
      return;
    }
    if (request.headers.origin || request.headers['sec-fetch-site'])
      await reply
        .code(403)
        .send({ code: 'BROWSER_ACCESS_DENIED', message: '浏览器不能直接访问内部服务。' });
  });
}
