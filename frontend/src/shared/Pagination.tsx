import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from 'lucide-react';
import '../styles/pagination.css';

export interface PaginationProps {
  label: string;
  total: number;
  offset: number;
  pageSize: number;
  onPageChange: (page: number) => void;
  onPageSizeChange: (size: number) => void;
  disabled?: boolean;
  sizes?: readonly number[];
}

export function Pagination({
  label,
  total,
  offset,
  pageSize,
  onPageChange,
  onPageSizeChange,
  disabled = false,
  sizes = [5, 10, 20, 50],
}: PaginationProps) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const page = Math.min(pages, Math.floor(offset / pageSize) + 1);
  return (
    <nav className="list-pagination" aria-label={`${label}分页`}>
      <span>
        {total
          ? `${(page - 1) * pageSize + 1}–${Math.min(page * pageSize, total)} / 共 ${total} 条`
          : '暂无记录'}
      </span>
      <div className="pagination-controls">
        <label>
          每页
          <select
            aria-label={`${label}每页条数`}
            value={pageSize}
            disabled={disabled}
            onChange={(event) => onPageSizeChange(Number(event.target.value))}
          >
            {sizes.map((size) => (
              <option key={size} value={size}>
                {size} 条
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          aria-label={`${label}首页`}
          disabled={disabled || page === 1}
          onClick={() => onPageChange(1)}
        >
          <ChevronsLeft size={15} />
        </button>
        <button
          type="button"
          aria-label={`${label}上一页`}
          disabled={disabled || page === 1}
          onClick={() => onPageChange(page - 1)}
        >
          <ChevronLeft size={15} />
        </button>
        <span className="pagination-position" aria-live="polite">
          {page} / {pages}
        </span>
        <button
          type="button"
          aria-label={`${label}下一页`}
          disabled={disabled || page === pages}
          onClick={() => onPageChange(page + 1)}
        >
          <ChevronRight size={15} />
        </button>
        <button
          type="button"
          aria-label={`${label}末页`}
          disabled={disabled || page === pages}
          onClick={() => onPageChange(pages)}
        >
          <ChevronsRight size={15} />
        </button>
      </div>
    </nav>
  );
}
