import {
  BRIDGE_URL,
  COOKIE_NAMES,
  YOUTUBE_URL,
  isRecord,
  validCookieHeader,
} from '@youmo/browser-bridge';

export class BridgeError extends Error {
  constructor(
    message: string,
    public readonly revoked = false,
  ) {
    super(message);
  }
}
export async function bridgeRequest(
  path: string,
  grant: string | null,
  body?: object,
): Promise<unknown> {
  let response: Response;
  try {
    response = await fetch(`${BRIDGE_URL}${path}`, {
      method: body ? 'POST' : 'DELETE',
      credentials: 'omit',
      cache: 'no-store',
      headers: {
        ...(body ? { 'Content-Type': 'application/json' } : {}),
        ...(grant ? { Authorization: `Bearer ${grant}` } : {}),
      },
      ...(body ? { body: JSON.stringify(body) } : {}),
      signal: AbortSignal.timeout(85000),
    });
  } catch {
    throw new BridgeError('本机服务未连接或验证超时，请启动工作流后重试。');
  }
  if (response.status === 401)
    throw new BridgeError('配对过期或授权已撤销，请在工作台重新连接。', true);
  if (response.status === 429) throw new BridgeError('本机正在验证会话，请稍后重试。');
  if (!response.ok) throw new BridgeError('本机服务拒绝了请求，请检查连接状态。');
  const value: unknown = await response.json();
  return value;
}
export async function collectCookie(): Promise<string | null> {
  const cookies = await chrome.cookies.getAll({ url: YOUTUBE_URL });
  const selected = new Map<string, string>();
  for (const cookie of cookies) {
    if (!allowedCookie(cookie)) continue;
    selected.set(cookie.name, cookie.value);
  }
  if (!selected.has('SAPISID') && !selected.has('__Secure-3PAPISID')) return null;
  const header = [...selected].map(([name, value]) => `${name}=${value}`).join('; ');
  if (!validCookieHeader(header))
    throw new BridgeError('浏览器会话格式不支持；未发送 Cookie。');
  return header;
}
function allowedCookie(cookie: chrome.cookies.Cookie): boolean {
  const domain = cookie.domain.replace(/^\./, '');
  return (
    COOKIE_NAMES.has(cookie.name) &&
    !cookie.partitionKey &&
    (domain === 'youtube.com' || domain === 'www.youtube.com') &&
    cookie.path === '/' &&
    Boolean(cookie.value)
  );
}
export function responseMessage(value: unknown): string {
  if (!isRecord(value) || typeof value.message !== 'string')
    throw new BridgeError('本机返回格式不符合预期。');
  return value.message;
}
