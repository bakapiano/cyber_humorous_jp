import Fastify from 'fastify';
import { GatewayError } from './contracts.js';
import type { HomeRequest, YoutubeProvider } from './contracts.js';
import type { LoginManager } from './auth/manager.js';
import { loginRoutes } from './auth/routes.js';
import { installSecurity } from './auth/security.js';

export function createApp(
  provider: YoutubeProvider,
  token: string,
  login?: LoginManager,
) {
  if (token.length < 24) throw new Error('Internal service token must be configured');
  const app = Fastify({
    logger: false,
    bodyLimit: 8192,
    requestTimeout: 90000,
    ajv: { customOptions: { removeAdditional: false } },
  });
  installSecurity(app, token, login);
  app.setErrorHandler(async (error, _request, reply) => {
    if (error instanceof GatewayError) {
      if (error.code === 'AUTH_REQUIRED') await login?.failedAuthentication();
      await reply.code(error.status).send({ code: error.code, message: error.message });
    } else {
      const status = error instanceof Error && 'validation' in error ? 400 : 502;
      await reply.code(status).send({
        code: status === 400 ? 'INVALID_REQUEST' : 'UPSTREAM_UNAVAILABLE',
        message: '请求参数无效或上游不可用；未返回私有响应。',
      });
    }
  });
  if (login) loginRoutes(app, login);
  app.get('/health', () => ({ status: 'ok', service: 'youmo-youtube' }));
  app.post<{ Body: HomeRequest }>(
    '/internal/v1/home-feed',
    {
      schema: {
        body: {
          type: 'object',
          additionalProperties: false,
          required: ['profile_id'],
          properties: {
            profile_id: { type: 'string', maxLength: 64 },
            limit: { type: 'integer', minimum: 1, maximum: 50 },
            continuation: { type: 'string', maxLength: 100 },
          },
        },
      },
    },
    async (request) => provider.home(request.body),
  );
  app.get<{ Params: { videoId: string } }>(
    '/internal/v1/videos/:videoId',
    {
      schema: {
        params: {
          type: 'object',
          required: ['videoId'],
          properties: { videoId: { type: 'string', pattern: '^[A-Za-z0-9_-]{11}$' } },
        },
      },
    },
    async (request) => provider.video(request.params.videoId),
  );
  return app;
}
