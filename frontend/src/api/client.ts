import createClient from 'openapi-fetch';
import type { components, paths } from '@youmo/api-types';

export type Schemas = components['schemas'];
export const client = createClient<paths>({ baseUrl: '' });
let token: string | undefined;

async function csrf(): Promise<string> {
  if (token) return token;
  const { data, error } = await client.GET('/api/v1/session');
  if (error || !data) throw new Error('无法建立本地会话，请刷新页面。');
  token = data.csrf_token;
  return token;
}

client.use({
  async onRequest({ request }) {
    if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method)) {
      request.headers.set('X-Workflow-Token', await csrf());
    }
    return request;
  },
  onResponse({ response }) {
    if (response.status === 403) token = undefined;
    return response;
  },
});

export function unwrap<T>(value: { data?: T; error?: unknown }): T {
  if (value.error || value.data === undefined) {
    const message = errorMessage(value.error);
    throw new Error(message);
  }
  return value.data;
}

export function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (
    typeof error === 'object' &&
    error &&
    'message' in error &&
    typeof error.message === 'string'
  ) {
    return error.message;
  }
  return '请求失败，请检查本地 API 和 Worker。';
}
