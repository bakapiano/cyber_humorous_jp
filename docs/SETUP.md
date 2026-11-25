# 本地配置与运行

本仓库是 `workflow` 主线源码，**不是含登录态、历史研究数据、模型权重和投稿工具的即用整机备份**。代码面向 Windows；登录态保险库和 NSSM 脚本依赖 Windows 功能。

## 环境

- Python 3.13、uv。
- Node.js 22 系列、pnpm 10.11.0（见 package.json）。
- FFmpeg / FFprobe 在 PATH。
- 支持中日文字形的字体；默认尝试 Microsoft YaHei，也可显式设置字体路径。
- 本地 faster-whisper 模型缓存；运行代码使用 `local_files_only=True`，不会自动获取权重。GPU 模式另需匹配的 CUDA/cuDNN 运行库。

```powershell
uv sync --project backend --frozen
pnpm install --frozen-lockfile
pnpm build
uv run --project backend python -m youmo_workflow.cli init
```

`init` 创建本地运行数据库、来源初始化及内部服务令牌，不等同于授权付费制作或真实投稿。

## 本地配置

`.env.example` 是说明模板；程序不会自动加载它，需要将变量设置到启动服务的进程环境。

| 用途 | 配置方式 |
| --- | --- |
| YouTube 元数据扫描 | `YOUTUBE_API_KEY`，或既有采集器私有文件 |
| 模型官方通道 | `DEEPSEEK_API_KEY`，或本地私有文件 |
| WorkBuddy 通道 | `config/model-route.txt`；通过 `WORKFLOW_WORKBUDDY_CONFIG` 指向自己的网关配置，默认端口 7863 |
| 网络代理 | `WORKFLOW_PROXY`；未设置时只读原采集器的本地代理配置 |
| 原片下载授权 | 工作台的 YouTube 扩展配对流程，用户自行完成登录/验证 |
| B站投稿 | 用户自己的同账号授权登录态和投稿工具；不能复制别人的 session |
| 字幕字体 | 可选 `WORKFLOW_SUBTITLE_FONT` |
| 转写设备 | 可选 `WORKFLOW_ASR_DEVICE`，默认 CUDA |

部分适配器保留原工作区的默认路径，迁移到其他电脑时应核对配置；不要仅复制文件就启用自动投稿。

## 仓库外依赖

当前选题、历史兼容和部分测试仍读取以下同级资源，未随本仓库发布：

```text
workspace/
├── workflow/                本仓库
├── 1_选题/                  v008 入口、冻结模板和训练历史索引
├── 3_标题和封面生成/        部分历史渲染配置
├── 4剪辑/                  历史 v002/v003 剪辑提示词
├── 0_youtube_collector/     可选原片复用、旧代理配置
└── 3_bilibili_upload/       自行安装并核验的投稿 CLI
```

选题资料包含绝对路径与哈希验证，不应通过修改冻结文件或删除校验来凑齐运行条件。缺少这些资料时，独立克隆可阅读主线代码、构建前端和运行不依赖外部资料的测试，但不能宣称完整生产复现；`tooling/check.ps1` 中依赖历史资料的测试可能失败。

仓库内 `config/` 已包含当前标题、封面和硬字幕剪辑的冻结模板，以及公开来源元数据；不是私人运行历史或用户授权的替代品。

## 启动与授权

普通本地进程可用 `start.ps1` / `stop.ps1` 管理。若注册了 NSSM 服务，脚本会阻止重复启动，应按照服务安装脚本配置正确的当前用户身份，并核对服务登录权限。

首次扫描、模型费用、生产比例、公开转载与清理边界应由使用者明确确认。仓库不会带入原运行实例的自动开关或队列；**不要在未配置凭据和权利授权时开启真实生产**。

## 检查

```powershell
pnpm exec playwright install chromium
pwsh -NoProfile -File tooling/check.ps1
```

默认测试使用模拟服务及合成媒体，不进行真实模型调用或投稿。完整检查覆盖格式、类型、导入边界、单元测试、API 契约一致性、构建和浏览器测试；生产网络可用性需要另做明确授权的验收。
