export function localTime(value?: string | null): string {
  if (!value) return '尚未同步';
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(value));
}

export function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    pending: '排队中',
    queued: '排队中',
    running: '执行中',
    succeeded: '已完成',
    completed: '已完成',
    completed_with_skips: '已处理（含审核跳过）',
    skipped: '审核未通过，已跳过',
    cleaning: '视频清理中',
    failed: '已停止',
    retry_wait: '稍后重试',
    ready: '制作就绪',
    prepared: '待确认',
    confirmed: '已确认',
    unknown: '结果待对账',
    uploaded: '已投递',
    reviewing: '平台审核中',
    awaiting_subtitles: '等待双语字幕',
    subtitle_submitting: '字幕提交中',
    subtitle_unknown: '字幕等待回查',
    subtitle_rejected: '字幕审核退回',
    video_rejected: '视频审核退回',
    withdrawn: '已下架 · 仅自己可见',
    superseded: '已由新版替代',
    cancelled: '已取消',
  };
  return labels[status] ?? status;
}
