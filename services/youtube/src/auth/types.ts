import type { components } from '@youmo/api-types';

export type ConnectionStatus = components['schemas']['YoutubeConnection'];
export type Pairing = components['schemas']['YoutubePairing'];
export interface SecretState {
  version: 1;
  grantHash: string | null;
  cookie: string | null;
  syncedAt: string | null;
}
export interface Vault {
  read(): Promise<SecretState | null>;
  write(value: SecretState): Promise<void>;
}
export interface CookieSnapshot {
  cookie: string;
  revision: string;
}
export interface CookieSource {
  current(): Promise<CookieSnapshot | null>;
}
export interface ProbeResult {
  account_name: string;
  homepage_count: number;
}
export const EMPTY_STATE: SecretState = {
  version: 1,
  grantHash: null,
  cookie: null,
  syncedAt: null,
};
