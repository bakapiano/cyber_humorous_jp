import { useState } from 'react';
import { YOUTUBE_URL } from '@youmo/browser-bridge';
import type { beginPairing } from './api';

export function PairingHelp({
  result,
}: {
  result: Awaited<ReturnType<typeof beginPairing>>;
}) {
  const [copied, setCopied] = useState(false);
  const [failed, setFailed] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(result.pairing.code);
      setCopied(true);
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }
  return (
    <div className="connection-card pairing-help">
      <h3>
        {result.opened ? '已打开扩展授权页' : '请在装有扩展的 Chrome / Edge 中完成配对'}
      </h3>
      <p>
        {result.opened
          ? '在扩展页打开 YouTube 并登录，确认授权，再点击“授权并连接”。'
          : '如果当前是应用内浏览器，请用专用 Chrome / Edge profile 打开工作台；也可以把下面的一次性配对码填入扩展。'}
      </p>
      <p>
        配对码截至{' '}
        {new Date(result.pairing.expires_at).toLocaleTimeString('zh-CN', {
          timeZone: 'Asia/Shanghai',
        })}{' '}
        有效，只能使用一次，不是 YouTube Cookie。
      </p>
      <label>
        一次性配对码
        <input
          aria-label="一次性配对码"
          readOnly
          value={result.pairing.code}
          autoComplete="off"
        />
      </label>
      <button
        className="button"
        onClick={() => {
          void copy();
        }}
      >
        {copied ? '已复制配对码' : '复制配对码'}
      </button>
      {failed && <p>剪贴板不可用，请选择上面的配对码手动复制。</p>}
      <p>配对码失效后，重新点击“连接 / 重新配对”。</p>
    </div>
  );
}
export function InstallGuide() {
  return (
    <div className="connection-card">
      <h3>首次安装</h3>
      <ol>
        <li>创建专用 Chrome / Edge profile，用于这个工作流。</li>
        <li>
          <a href="/api/v1/youtube/extension.zip" download>
            下载自有扩展
          </a>
          ，解压到固定目录。
        </li>
        <li>在扩展管理页开启“开发者模式”，选择“加载已解压的扩展”，选中解压目录。</li>
        <li>在同一浏览器打开工作台，点击“连接 / 重新配对”；到扩展页确认授权。</li>
      </ol>
      <a className="button" href={YOUTUBE_URL} target="_blank" rel="noreferrer">
        打开 YouTube 登录
      </a>
      <p>
        自动同步默认关闭，可在扩展中单独允许。浏览器关闭时不能续同步；Google
        要求验证时仍需你手动完成。断开连接不会登出 Google。
      </p>
    </div>
  );
}
