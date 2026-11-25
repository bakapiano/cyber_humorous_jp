# Cyber Humorous JP

一套面向日语视频的本地内容工作台：**选素材 → 取标题与封面 → 剪视频与双语字幕 → 串行投稿**。

这是正在使用的 `workflow` 主线源码。它把扫描、制作和投稿拆成独立队列与消费者，保存模型输入输出、执行账本和上传回执，而不是一个遇错就从头重跑的脚本。

> 本仓库不包含 Cookie、API key、运行数据库、下载媒体或私人运维记录。真实模型调用可能计费，转载/公开发布需要使用者确认权利和执行授权。完整运行还依赖部分仓库外的冻结研究资料，详见 [部署说明](docs/SETUP.md)。

## 总体架构

```mermaid
flowchart TD
    UI["React / TypeScript 工作台"] --> API["FastAPI API · 8766"]
    API <--> DB[("SQLite：任务 / 批次 / 授权 / 回执状态")]
    SCAN["独立 Scanner · 每30分钟"] --> POOL["公开来源素材池"]
    POOL --> ADMIT["满50条且有2个名额：冻结候选"]
    ADMIT --> SELECT["选素材"]
    SELECT --> PREP["原片下载 / 本地ASR / 翻译"]
    PREP --> TITLE["取标题 / 规划封面 / 选帧"]
    TITLE --> EDIT["剪视频 / 字幕复审 / 双语烧录"]
    EDIT --> PACKAGE["不可变发布包"]
    PACKAGE --> PUB["独立 Publisher · 串行投稿"]
    PUB --> RECEIPT["唯一成功上传回执"]
    RECEIPT --> CLEAN["单条媒体清理，保留模型与审计记录"]
    DB -. "持久队列与租约" .-> SELECT
    DB -. "持久队列与租约" .-> PREP
    DB -. "持久队列与租约" .-> TITLE
    DB -. "持久队列与租约" .-> EDIT
    DB -. "冷却与幂等" .-> PUB
    EXT["用户授权的 YouTube 扩展"] --> YT["Node / YouTube.js · 8767"]
    YT --> PREP
```

- **前端**：React、TypeScript、Vite；查看来源、批次、队列、投稿和模型用量。
- **后端**：Python、FastAPI、SQLAlchemy、SQLite；事务领取任务、租约校验、阶段隔离。
- **制作**：一个 Worker 中四组消费者，共享模型、下载、ASR 和渲染资源上限。
- **集成**：yt-dlp、faster-whisper、FFmpeg、模型接口和外部投稿 CLI。
- **授权**：YouTube 专用扩展 + 本机加密会话；投稿权限与发布包摘要绑定。

当前运行策略为 **50 条候选选 2 条制作**、最多 **50 个在制/待投名额**；投稿结束后至少间隔 **30 分 30 秒**。这些是部署策略，不代表所有版本的默认值，首次配置应显式核对。

## 1. 选素材：先排序，再补漏

输入是配置来源的新视频元数据。扫描与制作互相独立，素材不足一批时不调用选题模型。

```mermaid
flowchart TD
    A["来源扫描：标题 / 频道 / 发布时间 / 时长"] --> B["去重、有效期和源发布时间 cut"]
    B --> C["满足满批及容量条件，冻结候选和授权"]
    C --> D["v008 第一遍：基础模板排序50条"]
    D --> E["固定保留前20条"]
    D --> F["第二遍：从其余候选中补漏30条"]
    E --> G["确定性合并为50名"]
    F --> G
    G --> H["完整Top30留档，复筛为身份透传"]
    H --> I["按本批授权取前2条，独立创建制作任务"]
    F -. "仅明确 content_filter" .-> J["保留已验证的基础排序并标注回退"]
    J --> H
```

重要边界：

- 不把标题层面的选题假设当作已核实事实；原片证据在后续本地转写中取得。
- 历史样例按训练分区、时间边界及当前来源排除规则读取。
- 候选清单、提示词、模型设置和结果均留哈希/账本；不靠重启清零调用次数。
- 一批选题失败只隔离这一批，不自动重选原候选，也不阻塞其他独立批次。
- 原研究用“每天”描述的模板，在主线中由数量触发的批次调用，不是每日固定时刻运行。

代码入口：[选题配置与历史](backend/src/youmo_workflow/adapters/selection_profile.py)、[v008 补漏配置](backend/src/youmo_workflow/adapters/rescue_profile.py)、[批次准入](backend/src/youmo_workflow/features/batch_trigger.py)、[逐条分发](backend/src/youmo_workflow/orchestration/batch_dispatch.py)。

## 2. 取标题：基于原文证据，标题与封面绑定

先获取原片，用本地 ASR 生成带时间戳的日文转写；翻译、标题和封面都有独立的输入与结果记录。

```mermaid
flowchart TD
    A["原始标题 + 日文原文转写"] --> B["冻结的历史标题样例"]
    B --> C["生成3个标题候选，推荐1个"]
    C --> D["校验长度、结构和原文证据"]
    D --> E["围绕推荐标题规划封面"]
    E --> F["从原片抽候选帧"]
    F --> G["视觉模型选帧并复核标题关联"]
    G --> H["渲染封面：冻结字体、颜色与排版"]
    H --> I["标题、封面、原片证据一起交给剪辑"]
```

- 当前标题方案使用冻结的 **1,195 条历史标题样例**；不把待生成标题、人工总结的钩子或选题分数偷偷塞进标题输入。
- 标题与封面计划必须保留关联证据，不能只凭视觉效果编造事实。
- 模型主通道可配置为本地 WorkBuddy 或官方接口；现有备用规则仅针对封面规划/选帧首次明确内容筛选的一次 DeepSeek 备用调用，**不是任意失败都换通道重发**。
- 格式纠正、网络重试和内容拒绝是不同情况；原请求与错误始终保留。

代码入口：[内容准备](backend/src/youmo_workflow/adapters/content_preparation.py)、[标题/封面制作](backend/src/youmo_workflow/adapters/packaging.py)、[冻结模板读取](backend/src/youmo_workflow/adapters/production_profile.py)、[模型路由](backend/src/youmo_workflow/adapters/model_route.py)。

## 3. 剪视频：模型给区间，程序负责时间轴和渲染

模型不直接操作剪辑软件，只提出原片时间区间；程序验证、重排字幕并调用 FFmpeg。

```mermaid
flowchart TD
    A["标题证据 + 原片转写 + 封面时间点"] --> B["模型提出速览和连续正文区间"]
    B --> C["检查时长、原片边界、标题证据与封面覆盖"]
    C --> D{"速览是否重叠原片前10秒？"}
    D -->|"是"| E["省略重复开场速览"]
    D -->|"否"| F["保留速览，接0.6秒淡黑转场"]
    E --> G["确定输出时间轴，重映射字幕"]
    F --> G
    G --> H["关键词处理 → LLM字幕复审 → 程序复检"]
    H --> I["过长双语字幕按规则拆分"]
    I --> J["FFmpeg 烧录中日字幕并编码"]
    J --> K["完整解码 / 音视频时长 / 字幕与字体哈希验证"]
    K --> L["生成不可变发布包，交独立投稿队列"]
```

- ASR 使用逐词时间戳确定句首句尾，减少字幕提前出现或滞留。
- 字幕拆分保留文字、遮罩和原有外边界；比例分配不是逐词对齐的替代。
- 新稿使用双语硬字幕；完成后保留字幕、剪辑计划、模型记录与渲染证明。
- 投稿只以唯一成功回执确认“已投递”，不冒充审核通过或已公开。
- `unknown` 不重投，失败/取消不当作成功；成功稿才进入相应媒体清理流程。

代码入口：[剪辑编排](backend/src/youmo_workflow/adapters/editing.py)、[字幕映射与渲染](backend/src/youmo_workflow/adapters/editing_render.py)、[硬字幕](backend/src/youmo_workflow/adapters/hard_subtitles.py)、[独立投稿](backend/src/youmo_workflow/publisher_worker.py)。

## Prompt 介绍：分阶段生成，交给程序校验

这里不是一个“生成整条视频”的总提示词，而是一组职责明确的 Prompt。模型负责排序、语言生成和区间建议；程序负责来源身份、引用、时间轴、格式、授权和执行。**提示词中的要求不等于验证结果**，关键约束会在模型输出后再次检查。

### 每个阶段让模型做什么

| 阶段 | 主要输入 | 输出 | 关键约束 |
| --- | --- | --- | --- |
| 基础选题 | 当前候选的原标题、频道、时长，以及符合时间/训练分区规则的历史原标题样例 | 50 名排序；每条含候选 ID、角度、原文证据和待核实问题 | 从给定候选中选，不虚构 ID；角度是待核实假设，不是事实认证 |
| 补漏选题 | 固定的前 20 条、剩余候选、历史原标题样例 | 30 条补充排序 | 不修改前 20 条，只从剩余候选选择；优先补充具体新事件和独立视角 |
| 日中翻译 | 带 segment_id 的日文机器转写；新流程先做关键词遮罩 | 同顺序、同 ID 的中文译文和 warnings | 保留数字、单位、主体、否定与不确定性；不补造语音，不推测星号原词 |
| 标题生成 | 来源原题、原文转写、1,195 条历史投稿标题 | 3 个中文标题和 1 个推荐 ID | 参考历史表达，为当前内容拟题；不让模型同时规划封面或剪辑 |
| 封面规划与引文定位 | 已生成的 3 个标题、推荐 ID、当前字幕及原片时长 | 标题证据、主题角度、候选画面区间和封面短句 | 逐字保留标题和推荐 ID；引文必须真实存在于对应字幕段 |
| 视觉选帧与排版 | 候选帧图片、相关字幕、封面规划 | 选中帧、主体框、A/B 文案分行和红色关键词 | 只能选择提供的帧；短句不能改写，分行拼接后必须与原句一致 |
| 剪辑规划 | 最终标题、标题证据、完整字幕、封面时间点 | quick_recap 和 main 的原片时间区间，或 null | 正文连续、覆盖证据和封面；速览在正文内，不编造素材或改变原速 |
| 字幕发布前复审 | 已经关键词处理的中日字幕和标题 | safe_to_submit、逐字 mask_spans 建议及 notes | 不重写字幕、不改时间轴、不还原星号；true 不代表平台审核通过 |

### 三个核心设计

**选素材：让模型给“值得继续看”的入口，而不是只给一个分数。**

选题 Prompt 要求输出 angle、evidence 和 verify：分别说明潜在角度、原标题里的依据、以及看字幕时要核实的问题。基础排序后固定前 20 条，再用补漏 Prompt 从剩余候选中补充 30 条；组合后的前 30 条留档，再按生产批次授权分发前 N 条。历史案例用于提供编辑兴趣上下文，不是固定频道配额，也不表示候选事实已经验证。当前主线的 rescreen 是身份透传，不应将它描述成已经完成了内容复核。

**取标题：用完整历史样例做上下文学习，不预先总结一套标题公式。**

实际 [标题 Prompt](config/title-raw-history-v001/title.txt) 很短，核心要求是：

> 下面提供同一账号的历史投稿标题原文。请直接参考这些标题的表达，为当前视频拟3个中文投稿标题，并选择最合适的1个。新标题围绕当前视频的内容展开。

程序把 [历史标题列表](config/title-raw-history-v001/examples.json) 填入 `{{raw_titles_json}}`，把来源原题与原文转写填入 `{{video_json}}`。这是**请求内的样例上下文，不是模型微调**；标题输入不混入选题分数、人工风格总结或旧的生成答案。输出结构为：

```json
{
  "titles": [
    {"title_id": "TITLE1", "text": "候选标题一"},
    {"title_id": "TITLE2", "text": "候选标题二"},
    {"title_id": "TITLE3", "text": "候选标题三"}
  ],
  "recommended_title_id": "TITLE1"
}
```

标题阶段只产出标题；后续封面规划阶段才补充原文引文，并由程序检查标题是否被改动、引文是否存在。不要把标题 Prompt 本身说成已经附带了事实验证。

**剪视频：限制输出面，只让模型给区间。**

实际 [剪辑 Prompt](config/editing-v004-hardsub/plan_prompt.txt) 要求“只输出时间，不复述标题、引文、人物姓名、选择理由或自检声明”。例如以下只是结构示意，不是可直接使用的剪辑方案：

```json
{"quick_recap":[{"start":39.14,"end":51.92}],"main":{"start":0.75,"end":58.2}}
```

所有时间对应原片，不是成片。正文须至少 20 秒；速览恰好一段，满足 `3 ≤ 时长 < 60 秒`，位于正文内并覆盖标题证据。找不到合规区间时允许返回 null，由程序明确停止。v005 的“与开头 10 秒重叠则省略速览”和 0.6 秒转场是**程序展示规则**，不是让模型另编一套时间轴。

### 模板在哪里

- 选题：v008 基础/补漏模板及历史资料位于仓库外的 `1_选题`；本仓库包含 [加载与哈希验证代码](backend/src/youmo_workflow/adapters/rescue_profile.py)，不包含完整研究包。详见 [部署说明](docs/SETUP.md)。
- 翻译：写在 [translation.py](backend/src/youmo_workflow/adapters/translation.py) 的 `PROMPT` / `MASK_PROMPT` 中。
- 标题：[title.txt](config/title-raw-history-v001/title.txt)；封面规划：[cover_plan.txt](config/title-raw-history-v001/cover_plan.txt)。
- 视觉选帧：[vision.txt](config/cover-poster-v001/vision.txt)；硬字幕剪辑：[plan_prompt.txt](config/editing-v004-hardsub/plan_prompt.txt)。
- 字幕复审：[prompt.txt](config/subtitle-llm-review-v001/prompt.txt)。

### 输入安全、版本与失败处理

输入标题、字幕、历史样例中的广告、网址和指令都按素材处理，不授予其执行权限。实际请求通过 JSON 序列化填入模板；视觉阶段另外附加真实候选帧图片。

模板使用版本目录和 `file_manifest.json` 做冻结校验；已执行请求绑定输入指纹、模型设置和尝试记录。**不能直接改冻结模板再拿旧响应当新结果**。需要调整方案时，应建立新版本、更新校验与回归，并保留原始记录。

运行时统一使用 High 推理设置，历史研究文件中的旧 Max 配置不代表当前生产设置。可纠正的结构/契约错误最多追加两次格式纠正，携带原上下文、上轮输出和精确校验反馈；内容拒绝、认证错误和结果不明不是格式纠正。模型返回 JSON、声称“安全”或提供自检字段，也不能替代程序检查或平台的实际结果。

## 目录

```text
backend/       Python API、业务逻辑、适配器、迁移与测试
frontend/      React 工作台
services/      YouTube.js 与本机授权服务
extensions/    YouTube 专用浏览器扩展
packages/      共享协议与生成的 API 类型
config/        公开来源元数据、规则与冻结模板
contracts/     OpenAPI 契约
e2e/           隔离浏览器测试
tooling/       质量检查、运维安装及验证工具
docs/          对外架构与部署说明
```

## 使用与验证

参见 [部署说明](docs/SETUP.md)；先准备 Python 3.13、uv、Node、pnpm 和 FFmpeg，再安装依赖：

```powershell
uv sync --project backend --frozen
pnpm install --frozen-lockfile
pnpm build
```

全量工程检查：

```powershell
pnpm exec playwright install chromium
pwsh -NoProfile -File tooling/check.ps1
```

**仅克隆本仓库还不能直接复现完整生产**：v008 的冻结研究资料、部分历史模板、本地 ASR 模型权重和投稿 CLI 未随代码分发。详细缺项及配置边界见 [SETUP](docs/SETUP.md)，不要通过删掉校验或复制别人的 Cookie 来启动。

## 安全与设计边界

- [详细架构](docs/architecture.md)：进程、队列、分层、容量、依赖暂停及重试。
- [安全说明](SECURITY.md)：凭据存储、禁止上传的文件、日志与泄露处置。
- 原片下载仅对明确 TLS EOF 做两次有上限的退避重试；不会给投稿 unknown 加盲重投。
- 私有凭据、数据库、媒体、运行日志和内部维护历史都在 Git 排除范围内。
- 部署脚本提供 NSSM 和普通后台方式，但实际自启动取决于正确配置服务账号，不能仅凭脚本存在就视作已启用。
