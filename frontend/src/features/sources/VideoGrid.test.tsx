import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { VideoGrid } from './VideoGrid';
import { localTime, statusLabel } from '../../shared/format';
import { errorMessage } from '../../api/client';

afterEach(cleanup);
describe('source presentation', () => {
  it('does not invent content for an empty source pool', () => {
    render(<VideoGrid items={[]} />);
    expect(screen.getByText('暂无素材')).toBeTruthy();
  });
  it('renders actual returned titles and IDs', () => {
    render(
      <VideoGrid
        items={[
          {
            video_id: 'abcdefghijk',
            title: '返回的真实标题字段',
            source_id: 'tbsnewsdig',
            discovered_at: '2026-11-06T00:00:00Z',
            revision: 1,
            screened: false,
            origin: 'channel',
          },
        ]}
      />,
    );
    expect(screen.getByText('返回的真实标题字段')).toBeTruthy();
    expect(screen.getByText('abcdefghijk')).toBeTruthy();
  });
  it('uses the business timezone', () => {
    expect(localTime('2026-11-06T02:00:00Z')).toContain('10:00');
  });
  it('distinguishes a stopped task from approved content', () => {
    expect(statusLabel('failed')).toBe('已停止');
    expect(errorMessage({ message: '黑盒未接入' })).toBe('黑盒未接入');
  });
});
