import { useMutation, useQueryClient } from '@tanstack/react-query';
import { KeyRound, RefreshCw, Unplug } from 'lucide-react';
import { useAction } from '../../api/queries';
import type { Schemas } from '../../api/client';
import { ErrorNotice } from '../../shared/Notice';
import {
  beginPairing,
  disconnectYoutube,
  useYoutubeConnection,
  verifyConnection,
} from './api';
import { InstallGuide, PairingHelp } from './PairingHelp';

const labels: Record<Schemas['YoutubeConnection']['status'], string> = {
  disconnected: '未连接',
  paired: '已配对 · 待同步',
  received: '已保存 · 待验证',
  checking: '正在验证',
  connected: '连接正常',
  expired: '需要重新登录',
  error: '验证未通过',
};
export function LoginPanel() {
  const query = useYoutubeConnection();
  const cache = useQueryClient();
  const pair = useMutation({
    mutationFn: beginPairing,
    onSuccess: async () => {
      await cache.invalidateQueries({ queryKey: ['youtube-connection'] });
    },
  });
  const verify = useAction(verifyConnection);
  const disconnect = useAction(disconnectYoutube);
  const busy = [
    pair.isPending,
    verify.isPending,
    disconnect.isPending,
    query.data?.status === 'checking',
  ].some(Boolean);
  const sessionDisabled = busy || !query.data || query.data.source === 'none';
  return (
    <section className="youtube-login">
      <div className="section-heading">
        <div>
          <h2>YouTube 登录连接</h2>
        </div>
      </div>
      <ErrorNotice
        error={[query.error, pair.error, verify.error, disconnect.error].find(Boolean)}
      />
      <ConnectionCard data={query.data} />
      <div className="login-actions">
        <button
          className="button primary"
          disabled={busy || query.isLoading || query.isError}
          onClick={() => pair.mutate()}
        >
          <KeyRound size={15} />
          连接 / 重新配对
        </button>
        <button
          className="button"
          disabled={sessionDisabled}
          onClick={() => verify.mutate()}
        >
          <RefreshCw size={15} />
          验证推荐首页
        </button>
        <button
          className="button"
          disabled={sessionDisabled}
          onClick={() => disconnect.mutate()}
        >
          <Unplug size={15} />
          断开并撤销授权
        </button>
      </div>
      {pair.data && <PairingHelp result={pair.data} />}
      <InstallGuide />
      <p className="connection-note">
        仅连接默认 YouTube 账号，不开启自动生产或投稿。切换账号后请重新验证。
      </p>
    </section>
  );
}
function ConnectionCard({ data }: { data: Schemas['YoutubeConnection'] | undefined }) {
  if (!data) return <div className="connection-card">等待本地登录服务响应。</div>;
  return (
    <div className="connection-card">
      <div>
        <span
          className={`status-dot ${data.status === 'connected' ? 'succeeded' : 'failed'}`}
        />
        <strong>{labels[data.status]}</strong>
      </div>
      <p role="status">{data.message}</p>
      <dl>
        <div>
          <dt>验证账号</dt>
          <dd>{data.account_name ?? '尚未验证'}</dd>
        </div>
        <div>
          <dt>最近同步</dt>
          <dd>{formatTime(data.last_sync)}</dd>
        </div>
        <div>
          <dt>最近验证</dt>
          <dd>{formatTime(data.last_verified)}</dd>
        </div>
      </dl>
      {data.status === 'connected' && (
        <p>推荐页采样读取 {data.homepage_count} 条；这不代表已经完成入库或筛选。</p>
      )}
    </div>
  );
}
function formatTime(value: string | null | undefined): string {
  return value
    ? new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })
    : '—';
}
