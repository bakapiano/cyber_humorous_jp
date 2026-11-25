import { useState } from 'react';

export function usePageState(initialSize = 10) {
  const [offset, setOffset] = useState(0);
  const [pageSize, setPageSize] = useState(initialSize);
  return {
    offset,
    pageSize,
    onPageChange: (page: number) => setOffset((page - 1) * pageSize),
    onPageSizeChange: (size: number) => {
      setPageSize(size);
      setOffset(0);
    },
  };
}

export function usePagination<T>(items: readonly T[], initialSize = 10) {
  const state = usePageState(initialSize);
  const lastOffset =
    Math.max(0, Math.ceil(items.length / state.pageSize) - 1) * state.pageSize;
  const offset = Math.min(state.offset, lastOffset);
  return {
    ...state,
    offset,
    total: items.length,
    items: items.slice(offset, offset + state.pageSize),
  };
}
