import { afterEach, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Pagination } from './Pagination';
import { usePagination } from './usePagination';

afterEach(cleanup);

function Example({ count }: { count: number }) {
  const paging = usePagination(Array.from({ length: count }, (_, i) => i + 1));
  return (
    <>
      <ul>
        {paging.items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
      <Pagination {...paging} label="测试" />
    </>
  );
}

it('pages every item, changes size, clamps shrinking data and disables empty navigation', () => {
  const view = render(<Example count={25} />);
  expect(screen.getAllByRole('listitem')).toHaveLength(10);
  fireEvent.click(screen.getByRole('button', { name: '测试下一页' }));
  expect(screen.getAllByRole('listitem')[0]?.textContent).toBe('11');
  fireEvent.click(screen.getByRole('button', { name: '测试末页' }));
  expect(screen.getAllByRole('listitem')).toHaveLength(5);
  fireEvent.change(screen.getByLabelText('测试每页条数'), { target: { value: '20' } });
  expect(screen.getByText('1 / 2')).toBeTruthy();
  expect(screen.getAllByRole('listitem')).toHaveLength(20);
  fireEvent.click(screen.getByRole('button', { name: '测试下一页' }));
  view.rerender(<Example count={3} />);
  expect(screen.getByText('1 / 1')).toBeTruthy();
  expect(screen.getAllByRole('listitem')).toHaveLength(3);
  view.rerender(<Example count={0} />);
  expect(screen.queryAllByRole('listitem')).toHaveLength(0);
  expect(
    screen.getByRole('button', { name: '测试下一页' }).hasAttribute('disabled'),
  ).toBe(true);
});
