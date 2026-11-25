import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import type { Schemas } from '../../api/client';
import { ChannelSection } from './ChannelSection';
import { parseAvatarIndex } from './channel-avatars';
import type * as AvatarModule from './channel-avatars';

const state = vi.hoisted((): { sources: Schemas['SourceDTO'][] } => ({ sources: [] }));
vi.mock('../../api/queries', () => ({
  useSources: () => ({ data: state.sources, isPending: false, error: null }),
  useAction: () => ({ mutate: vi.fn(), isPending: false, isSuccess: false, error: null }),
  syncSources: vi.fn(),
  toggleSource: vi.fn(),
}));
vi.mock('./channel-avatars', async (original) => ({
  ...(await original<typeof AvatarModule>()),
  useSourceAvatars: () => ({
    data: new Map([['source0', '/source-avatars/UCaaaaaaaaaaaaaaaaaaaaaa.png']]),
  }),
}));
afterEach(cleanup);

function seed() {
  state.sources = Array.from({ length: 14 }, (_, i) => ({
    id: `source${i}`,
    name: `频道${i}`,
    url: `https://www.youtube.com/@source${i}`,
    enabled: i < 7,
    evidence_count: 1,
    video_count: 2,
  }));
}

it('paginates six channels, resets visibility, and clamps when sources disappear', () => {
  seed();
  const choose = vi.fn();
  const view = render(<ChannelSection selected="" choose={choose} />);
  expect(screen.getAllByRole('article')).toHaveLength(6);
  fireEvent.click(screen.getByRole('button', { name: '频道下一页' }));
  expect(screen.getAllByRole('article')).toHaveLength(1);
  fireEvent.click(screen.getByRole('button', { name: '查看全部 14 个已核实来源' }));
  expect(screen.getByText('1 / 3')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '频道下一页' }));
  fireEvent.click(screen.getByRole('button', { name: '频道下一页' }));
  expect(screen.getAllByRole('article')).toHaveLength(2);
  state.sources = state.sources.slice(0, 2);
  view.rerender(<ChannelSection selected="" choose={choose} />);
  expect(screen.getByText('1 / 1')).toBeTruthy();
  expect(screen.getAllByRole('article')).toHaveLength(2);
});

it('links to YouTube independently from selecting a source and falls back on broken avatars', () => {
  seed();
  const choose = vi.fn();
  render(<ChannelSection selected="source0" choose={choose} />);
  const link = screen.getByRole('link', { name: '在 YouTube 打开 频道0' });
  expect(link.getAttribute('href')).toBe('https://www.youtube.com/@source0');
  expect(link.getAttribute('target')).toBe('_blank');
  expect(link.closest('button')).toBeNull();
  expect(choose).not.toHaveBeenCalled();
  const button = screen.getByTitle('筛选 频道0 的素材');
  expect(button.getAttribute('aria-pressed')).toBe('true');
  fireEvent.click(button);
  expect(choose).toHaveBeenCalledWith('source0');
  fireEvent.error(screen.getByAltText('频道0 头像'));
  expect(within(button).getByLabelText('暂无频道头像')).toBeTruthy();
});

it('accepts only local validated avatar paths', () => {
  expect(parseAvatarIndex(null).size).toBe(0);
  expect(parseAvatarIndex({ avatars: [] }).size).toBe(0);
  const index = parseAvatarIndex({
    avatars: {
      valid: '/source-avatars/UCaaaaaaaaaaaaaaaaaaaaaa.png',
      remote: 'https://example.com/image.png',
      traversal: '/source-avatars/../secret',
    },
  });
  expect([...index.keys()]).toEqual(['valid']);
});
