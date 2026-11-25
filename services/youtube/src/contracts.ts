import type { components } from '@youmo/api-types';

export type DiscoveryVideo = components['schemas']['DiscoveryVideo'];
export type DiscoveryPage = components['schemas']['DiscoveryPage'];
export interface HomeRequest {
  profile_id: string;
  limit?: number;
  continuation?: string;
}
export interface YoutubeProvider {
  home(request: HomeRequest): Promise<DiscoveryPage>;
  video(id: string): Promise<DiscoveryVideo>;
}
export class GatewayError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly status = 409,
  ) {
    super(message);
  }
}
