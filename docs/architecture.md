# 架构与执行边界

## 五个进程

| 入口 | 职责 |
| --- | --- |
| `youmo_workflow.api:app` | FastAPI，工作台接口、静态前端、任务与发布授权 |
| `youmo_workflow.worker --production-only` | 选题、准备、包装、剪辑四组消费者 |
| `youmo_workflow.publisher_worker` | 串行投稿、回执收尾、成功稿媒体清理 |
| `youmo_workflow.scan_worker` | 独立定时扫描公开来源元数据 |
| `services/youtube` | Node.js / YouTube.js，扩展配对、会话保险库和下载会话接口 |

前端默认由 API 在 loopback 8766 服务；YouTube 服务位于 loopback 8767。WorkBuddy 是仓库外的可选本地模型网关，不属于五个工作流进程。

制作 Worker 内默认有 8 个消费者，分为选题 1、准备 3、包装 2、剪辑 2；它们共享进程级资源限额，避免多进程分别放大模型或 GPU 并发。资源配置见 `config/performance.json`。

## 代码分层

```text
backend/src/youmo_workflow/
├── domain/         数据契约、规则、阶段与错误类型
├── features/       任务领取、容量、授权、冷却、恢复等业务逻辑
├── orchestration/  阶段衔接和执行器
├── adapters/       yt-dlp、ASR、模型、FFmpeg、投稿工具
└── storage/        SQLAlchemy 模型和迁移
```

领域层不依赖 HTTP、数据库或适配器；外部工具调用留在 adapters。Import Linter、类型检查和测试保护这些边界。

## 队列与幂等

不使用 Redis/Kafka，复用 SQLite `tasks` 表：

- `kind` 是阶段队列，`unique_key` 是业务幂等键，`batch_id` 是授权/来源关联。
- 任务在数据库事务内原子领取，使用租约 token 和到期时间；旧执行器不能回写已失效租约。
- 阶段成功才创建下一阶段任务；批次不构成跨视频执行锁。
- 满批候选冻结与输出名额预留在同一事务内。终止失败和真正 unknown 不占活跃库存，但原记录、候选冻结和文件不删除。
- 模型请求有冻结输入、哈希、物理尝试账本和有界纠正，不通过重启重置费用预算。

## 重试和失败隔离

| 场景 | 行为 |
| --- | --- |
| 原片明确 TLS EOF | 最多追加 2 次，约 5/15 秒退避，账本持久化 |
| 登录、验证码、证书或 HTTP 拒绝 | 不套用 TLS 重试；按明确依赖故障暂停或保留错误 |
| 超长、明确来源不可用 | 按已授权策略留证 skipped，不补选、不当作投稿成功 |
| 普通模型/渲染失败 | 单条终止，兄弟任务继续，不自动回放历史失败 |
| 投稿明确 601/137022 限流 | 5/10/20/30/60 分钟退避，与 1830 秒投稿间隔取较晚者 |
| 投稿结果不明 | 原 task/publication 保留 unknown，不重投、不清理 |

成功上传的唯一回执是投递完成依据，不代表审核通过或已公开。硬字幕流程据此做本地收尾，逐条清理成功稿媒体，保留模型与审计记录。

## 存储

`data/workflow.sqlite3` 保存队列及状态，`artifacts/` 保存内容产物，`data/run/` 保存本地进程登记及日志，`.secrets/` 保存私有凭据。这些路径均不进入仓库。安全边界见 [SECURITY.md](../SECURITY.md)。
