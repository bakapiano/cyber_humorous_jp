import { useState } from 'react';
import { Radio, Layers3, Send, Clock3, KeyRound, Coins } from 'lucide-react';
import { useDashboard } from './api/queries';
import type { Schemas } from './api/client';
import { SourcePanel } from './features/sources/SourcePanel';
import { BatchPanel } from './features/batches/BatchPanel';
import { PublishingPanel } from './features/publishing/PublishingPanel';
import { ErrorNotice } from './shared/Notice';
import { LoginPanel } from './features/youtube/LoginPanel';
import { ModelUsagePanel } from './features/usage/ModelUsagePanel';

const pages = [
  { id: 'sources', label: '来源与素材', icon: Radio },
  { id: 'batches', label: '生产批次', icon: Layers3 },
  { id: 'publishing', label: '发布工作台', icon: Send },
  { id: 'usage', label: '模型用量', icon: Coins },
  { id: 'youtube', label: 'YouTube 连接', icon: KeyRound },
] as const;
type Page = (typeof pages)[number]['id'];

export default function App() {
  const [page, setPage] = useState<Page>('sources');
  const query = useDashboard();
  return (
    <div className="app-shell">
      <Sidebar page={page} setPage={setPage} status={query.status} />
      <div className="main-shell">
        <header className="topbar">
          <h1>{pages.find((item) => item.id === page)?.label}</h1>
          <span className="badge neutral">
            <Clock3 size={12} />
            北京时间
          </span>
        </header>
        <main>
          <ErrorNotice error={query.error} />
          {page === 'sources' && <Overview data={query.data} />}
          {page === 'sources' && <SourcePanel />}
          {page === 'batches' && <BatchPanel />}
          {page === 'publishing' && <PublishingPanel />}
          {page === 'usage' && <ModelUsagePanel />}
          {page === 'youtube' && <LoginPanel />}
        </main>
      </div>
    </div>
  );
}

function Sidebar({
  page,
  setPage,
  status,
}: {
  page: Page;
  setPage: (page: Page) => void;
  status: 'pending' | 'error' | 'success';
}) {
  return (
    <aside className="sidebar">
      <a href="/" className="brand" aria-label="内容工作台">
        <div className="brand-mark" aria-hidden="true">
          <span />
          <span />
          <span />
        </div>
        <span className="brand-label">内容工作台</span>
      </a>
      <nav aria-label="主导航">
        {pages.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            className={page === id ? 'active' : ''}
            aria-current={page === id ? 'page' : undefined}
            title={label}
            onClick={() => setPage(id)}
          >
            <Icon size={18} />
            {label}
            {page === id && <span className="nav-dot" />}
          </button>
        ))}
      </nav>
      <div className="local-status">
        <span className={`status-dot ${status === 'success' ? 'succeeded' : status}`} />
        {{ pending: '连接中…', error: 'API 未连接', success: 'API 已连接' }[status]}
      </div>
    </aside>
  );
}

function Overview({ data }: { data: Schemas['Dashboard'] | undefined }) {
  if (!data) return null;
  return (
    <section className="metrics" aria-label="来源概览">
      <Metric
        label="来源"
        value={data.sources}
        suffix="个"
        note={`${data.enabled_sources} 个已启用`}
      />
      <Metric label="已入库素材" value={data.videos} suffix="条" note="仅元数据" />
      <Metric
        label="可入新批次"
        value={data.eligible_videos}
        suffix="条"
        note={`近 ${data.policy.candidate_window_hours} 小时 · 未占用`}
      />
      <Metric
        label="生产批次"
        value={data.batches}
        suffix="批"
        note="按数量触发 · 与频道扫描独立"
      />
    </section>
  );
}

function Metric({
  label,
  value,
  suffix,
  note,
}: {
  label: string;
  value: number | string | undefined;
  suffix?: string;
  note: string;
}) {
  return (
    <div className="metric">
      <span>{label}</span>
      <div>
        {value ?? '—'}
        {suffix && <small>{suffix}</small>}
      </div>
      <p>{note}</p>
    </div>
  );
}
