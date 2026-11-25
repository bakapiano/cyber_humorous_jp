export const EXTENSION_ID = 'jjkhdkhonlaoocoekdbojkhkbfjcmcci';
export const EXTENSION_KEY =
  'MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAtwlPIEAQ8NIs6Kf1YomMJpA1GKeGggYOznFCmlKcXBo8IlVrdkT+R+jMo675zfoFa18drrn2sMsm17hzTZpN5SKf9DfJDngzu9WQYFArHscQkxWTOeHX26PleKvi+Ec1pqv8UqXE2F5/aBDWL8p6ErF8os7D1l1/IxBDttH5WmB4sdMzOQnVCKpmkDLUI5AvIhd8rzo9p2yYX7qyW7UC2LcQ/6mVclMvajh1Gaiyv3wjkp5We46dQ/s1P//johZ1MXqdqkQNfZxfUWn0IYnnh9eHyCXpbRC+nE8xJ35szVq+dyJroSjnZjrjDQMZrnGwnAf7OrCXl9Mhz7YHBfH/OQIDAQAB';
export const BRIDGE_URL = 'http://127.0.0.1:8767/bridge/v1';
export const APP_ORIGINS = [
  'http://127.0.0.1:8766',
  'http://localhost:8766',
  'http://127.0.0.1:5173',
  'http://localhost:5173',
];
export const YOUTUBE_URL = 'https://www.youtube.com/';
export const COOKIE_NAMES = new Set([
  'SID',
  'HSID',
  'SSID',
  'APISID',
  'SAPISID',
  'SIDCC',
  'LOGIN_INFO',
  '__Secure-1PSID',
  '__Secure-3PSID',
  '__Secure-1PAPISID',
  '__Secure-3PAPISID',
  '__Secure-1PSIDTS',
  '__Secure-3PSIDTS',
  '__Secure-1PSIDCC',
  '__Secure-3PSIDCC',
  'PREF',
  'VISITOR_INFO1_LIVE',
  'VISITOR_PRIVACY_METADATA',
  'YSC',
  'CONSENT',
  'SOCS',
]);
export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
export function isPairMessage(value: unknown): value is { action: 'pair'; code: string } {
  return (
    isRecord(value) &&
    value.action === 'pair' &&
    typeof value.code === 'string' &&
    /^[A-Za-z0-9_-]{43}$/.test(value.code)
  );
}
export function allowedPage(url: string | undefined): boolean {
  try {
    return APP_ORIGINS.includes(new URL(url ?? '').origin);
  } catch {
    return false;
  }
}
export function validCookieHeader(value: string): boolean {
  if (!value || value.length > 16384 || /[^\x20-\x7e]/.test(value)) return false;
  const pairs = value.split(';').map((part) => part.trim());
  const names = pairs.map((part) => part.slice(0, part.indexOf('=')));
  return (
    pairs.every((part) => /^[\w-]+=[^;\s]+$/.test(part)) &&
    names.every((name) => COOKIE_NAMES.has(name)) &&
    new Set(names).size === names.length &&
    names.some((name) => name === 'SAPISID' || name === '__Secure-3PAPISID')
  );
}
