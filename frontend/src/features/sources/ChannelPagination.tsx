import { ChevronLeft, ChevronRight } from 'lucide-react';

export function ChannelPagination({
  page,
  pages,
  total,
  onChange,
}: {
  page: number;
  pages: number;
  total: number;
  onChange: (page: number) => void;
}) {
  return (
    <nav className="channel-pagination" aria-label="来源频道分页">
      <span>共 {total} 个频道 · 每页 6 个</span>
      <div>
        <button
          aria-label="频道上一页"
          disabled={page <= 1}
          onClick={() => onChange(page - 1)}
        >
          <ChevronLeft size={16} />
        </button>
        <span aria-live="polite">
          {page} / {pages}
        </span>
        <button
          aria-label="频道下一页"
          disabled={page >= pages}
          onClick={() => onChange(page + 1)}
        >
          <ChevronRight size={16} />
        </button>
      </div>
    </nav>
  );
}
