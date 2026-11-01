# learnMe（主理人 agent）

**主理人 agent：一个本地网页控制台。它同时跑很多个编程 agent 会话，自己盯着它们，并且学习*你*怎么判断该停、该继续、该换方向。**

[English](README.md)

![演示：一句话开出三个子窗口，自动验收，告警进来，纠正一次变成规则](docs/images/demo.gif)
*`./run.sh demo` 的录屏，约 2 倍速。[完整视频（70 秒，mp4）](https://github.com/realzhukaihua/learnMe/releases/download/v0.1.0/learnme-demo.mp4)。*

## 30 秒试用

不需要任何 key，也不用装 agent 命令行。演示模式用剧本代替模型，控制台本身是真实代码：

```bash
git clone https://github.com/realzhukaihua/learnMe.git && cd learnMe
./run.sh demo        # 需要 uv（https://docs.astral.sh/uv/）；然后打开 http://127.0.0.1:8777
```

打开页面看一分钟左右：你在主窗口提三件事，主理人开三个子窗口；每个窗口一停，主理人就验收：文档修复它自己重跑链接检查后通过，待办功能缺测试就让它补，ETL 窗口在调 Spark 内存，它纠正方向去查周一的表结构变更。接着一条告警进来，开窗口只读排查，停在等批准。然后主理人让 ETL 窗口在线上库建索引，你反对一次，它把这条写成规则；后面一条用户反馈要在线上库回填数据，它就先来问你。结束后可以在主窗口输入 "approve rollback" 或 "run the backfill"，自己批准。

agent 说的话来自剧本；派任务、验收、告警接入、反馈 watcher、记忆的 diff 都是真实代码在跑。演示剧本是英文的。数据放在 `data/demo`，每次启动清空。

## 为什么需要主理人

**1. agent 做到一半就停，得有人一直催。** 没有 learnMe 时：

> **Agent：** 不好意思，发现一个错误导致中断。  
> **我：** 有错误就改啊。  
> **Agent：** 好的，有错误我会查清楚原因……  
> **我：** 那你改了没有？  
> **Agent：** 好的……

有了主理人：子任务一停，主理人马上自动验收。没做完，就让它继续，并说清还差什么；在原地打转，就纠正方向。不用人守在旁边问「改了没有」。（[通过](docs/images/supervise-accept.png) · [继续](docs/images/supervise-continue.png) · [纠正方向](docs/images/supervise-redirect.png)）

**2. 长程任务没达到要求就停，因为上下文腐烂（context rot）。** 上下文腐烂：会话越拉越长，模型会逐渐忘掉早先的要求和细节，表现越来越差。没有 learnMe 时，长任务忘了一半要求就说做完了。有了主理人：你的要求和原话写进分层记忆和规则，每个子任务都会读到；子任务停下时，主理人（任务说明是它写的，它也不背子任务那段长上下文）对照你的原始要求验收，没达标就继续推进；窗口压缩上下文时，摘要必须保留待办事项和你的原话（Claude、DeepSeek 后端）。（[记忆分层](docs/images/memory-layers.zh.svg) · [学到的一条规则](docs/images/learn-rule.png)）

**3. 不用每天开一堆窗口，分别和 Claude、Codex 对话。** 没有 learnMe 时：每个 agent、每个项目各开一个终端，哪个在跑什么全靠你自己记。有了主理人，all in one：你只和主理人说话，它判断是自己答，还是派任务开窗口；每个窗口可以用不同的后端（Claude、Codex 或 DeepSeek），一个页面看全部进度。（[一句话开出三个窗口](docs/images/dispatch.gif)）

**4. 你不在时，它替你值守。** 没有 learnMe 时，*Alice（Slack）：「billing 服务的 5xx 是不是你们改的？」* 只能等你哪天看到。有了主理人：你这边的 agent 先读消息，判断自己能不能处理：能处理，就查清楚后自动回复；超出权限或有风险，就请你批准。线上告警、用户反馈也一样，进来就开窗口自动处理。（[无人值守](docs/images/unattended.gif) · [等你批准的告警](docs/images/alert-approval.png)）

## 怎么工作

![同时开着多个窗口的 learnMe](docs/images/overview-zh.png)
*左边是主理人；右边是正在跑的子任务和等你决策的告警窗口。图里所有数据都是编造的示例。*

你在主窗口里只和**主理人**说话。独立的事会开成**子窗口**：每个子窗口是一个完整的 agent 会话（Claude Code、OpenAI Codex 或 DeepSeek），跑在你的某个项目目录里。子任务停下时，主理人来验收：通过就结束，没做完就推进，方向错了就纠正，要你拍板的就交给你。每一次干预都会记下来，主理人把它们提炼成你的**画像**（工作习惯和判断方式），以后每个子任务都会读到。它还能作为常驻服务**无人值守**：告警、别人发来的消息、用户反馈会各自开窗口自动处理，有风险的操作等你点头。

## 功能

### 一句话，开出几个窗口（自动分发）

![自动分发：主窗口一句话，开出三个子窗口开始干活](docs/images/dispatch.gif)

*在主窗口说一句话。主理人先查项目历史，再开出三个子窗口（其中一个用 Codex），它们马上开始干活。*

说「去 *api-server* 查一下夜间任务为什么慢」，主理人就在那个项目里开一个窗口；主窗口始终只是对话。窗口实时显示文字、思考、工具调用、权限确认、todo 清单、上下文用量和费用。发消息前后对比：[一句话](docs/images/dispatch-before.png) → [三个窗口在干活](docs/images/dispatch-after.png)。

### 主动监督

子任务一轮结束，监督器把它交给主理人；主理人看证据（任务状态、diff、自己做的快速检查）后做决定。自动推进最多 3 轮；你本人正在指挥的任务它不插手。

| | |
|---|---|
| ![通过](docs/images/supervise-accept.png) | ![继续](docs/images/supervise-continue.png) |
| **通过：** 主理人自己重跑一遍链接检查，再把证据写进结束理由。 | **继续：** 功能能用但缺测试，主理人说清还差什么。 |
| ![纠正方向](docs/images/supervise-redirect.png) | ![交给用户](docs/images/supervise-escalate.png) |
| **纠正方向：** 调了 20 分钟 Spark 内存，主理人让它去查周一的表结构变更。 | **交给用户：** 线上回滚要你决定，主理人报原因和方案，不替你批准。 |

![监督流程](docs/images/supervision.zh.svg)

### 无人值守：告警、消息、用户反馈

![无人值守：watcher 发现新反馈，窗口修复后停在等待批准，主理人交给你](docs/images/unattended.gif)

*全程没人点任何东西：watcher 发现一条新的用户反馈，窗口自动打开、复现、改代码并补测试，因为要在线上数据库回填而停在 `NEEDS APPROVAL`，主理人把这个决定交给你。*

外部系统可以用 `POST /api/events` 把内容推进来（告警系统、CI、聊天机器人转发、表单），也可以让 **watcher** 定时去拉：按计划跑一条命令或请求一个 URL，比如拉取打了 `feedback` 标签的 GitHub issue。每个来源可以配自己的 runbook。窗口先只读诊断，runbook 允许的自己做，有风险的以 `NEEDS APPROVAL:` 停下。

![停在 NEEDS APPROVAL 的告警窗口](docs/images/alert-approval.png)
*一条 Alertmanager 告警：窗口看日志和上线记录，找到原因，留下一个可回滚的方案等人批准。*

### 学习你的判断方式

每一次「结束 / 继续 / 纠正 / 中断 / 批准」都连同当时的任务快照记下来，并区分是你本人、主理人按你的话，还是主理人自己做的。你纠正一次，就变成一条带依据的规则，下次同类情况按新规则办。

| 1. 你纠正一次 | 2. 多了一条带依据的规则 | 3. 下次同类情况 |
|---|---|---|
| ![纠正](docs/images/learn-correction.png) | ![规则 diff](docs/images/learn-rule.png) | ![按新规则处理](docs/images/learn-applied.png) |
| 主理人放任务去线上建索引，你说不行；它叫停任务，把规则写下来。 | `judgment.md` 多了一条规则，带日期和你的原话；改动记在主理人名下，可以回滚。 | 后来一个反馈窗口提出要在线上回填数据，主理人引用新规则，交给你而不是自己批准。 |

### 分层记忆，不丢知识

![记忆分层](docs/images/memory-layers.zh.svg)

存储不设上限，加载有选择：只有一小块常驻内容（习惯、判断、主理人的 `MEMORY.md` 索引）进 prompt；超了预算就拆成主题文件，绝不靠删知识压字数。所有记忆文件（画像、主理人记忆、各项目的 `CLAUDE.md` / `AGENTS.md` / agent 记忆）都有追踪、diff、全文检索，界面上可以回滚。

![记忆面板（示例规则）](docs/images/memory-zh.png)
*记忆面板：你的画像（示例规则）、主理人记忆、各项目记忆和变更记录。*

### 有什么 key 就用什么模型

`backend = "auto"` 自动用本机已经配好的 Claude、Codex 或 DeepSeek；每个任务也可以单独选；主理人的后端在顶栏切换。见[下文](#有什么-key-就用什么后端)。

### 中英文

界面和提示词都有中英文两套（`language = "en" | "zh"`），英文界面见[英文说明](README.md)。

## 架构

![架构](docs/images/architecture.zh.svg)

| 模块 | 作用 |
|---|---|
| `server/console.py` | 窗口注册表；监督器把每次停下变成交给主理人的验收项 |
| `server/manager.py` | 主理人会话和 learnMe 的工具（与后端无关的 `ToolDef` 列表） |
| `server/session.py` | 每个窗口一个 `TaskSession`：事件流和决策记录 |
| `server/memory.py` | 记忆文件追踪：diff、全文检索、归属、回滚 |
| `server/store.py` | SQLite：任务、事件、决策、记忆变更历史 |
| `server/mcp_http.py` | `/mcp/<窗口>`：用 MCP Streamable HTTP 提供工具，给不能进程内挂载的后端用 |
| `server/events.py` | 外部输入：`POST /api/events` 和 watcher |
| `server/slack_*.py` | 可选的 Slack 桥和带护栏的 Slack 窗口 |
| `server/backends/` | `claude.py`（Claude Agent SDK）、`deepseek.py`（Claude Code 接 DeepSeek 的 Anthropic 兼容接口）、`codex.py`（`codex app-server` JSON-RPC） |

会话层不直接依赖任何厂商 SDK。后端把 `SessionSpec`（工作目录、模型、权限模式、恢复/分叉 id、系统提示、工具、权限和护栏回调）变成一个活的连接，吐出同一套事件；工具名统一映射成 Claude Code 的（`Bash`、`Edit`、`TodoWrite`、`mcp__learnme__dispatch_task`），所以界面对所有后端渲染一致。详见 `server/backends/base.py`。

## 安装

需要 Python 3.11+、[uv](https://docs.astral.sh/uv/)（或 pip），以及下面任意一个后端。

```bash
git clone https://github.com/realzhukaihua/learnMe.git && cd learnMe
uv sync                    # 或者：python -m venv .venv && .venv/bin/pip install -e .
./run.sh doctor            # 看哪些后端已经能用、其他的还缺什么
./run.sh start             # → http://127.0.0.1:8770
```

可选：`cp learnme.example.toml learnme.toml`，设置语言（`language = "zh"`）、项目目录、模型等。默认把仓库上一级目录下的子目录当作项目。

## 有什么 key 就用什么后端

| 你有 | 这样做 | 后端 |
|---|---|---|
| Claude 订阅（Pro/Max/Team） | 安装 [Claude Code](https://docs.anthropic.com/en/docs/claude-code)，运行一次 `claude` 登录 | `claude` |
| Anthropic API key | `export ANTHROPIC_API_KEY=...` | `claude` |
| ChatGPT 订阅 | `npm i -g @openai/codex && codex login` | `codex` |
| OpenAI API key | `npm i -g @openai/codex && export OPENAI_API_KEY=...`（首次使用时 learnMe 会用它登录 Codex，等同 `codex login --with-api-key`） | `codex` |
| DeepSeek API key | `export DEEPSEEK_API_KEY=...` | `deepseek` |

就这些：`backend = "auto"`（默认）会按 claude → codex → deepseek 的顺序用第一个可用的。想指定：

```toml
# learnme.toml
backend = "codex"           # 子任务默认用的后端
manager_backend = "claude"  # 主窗口用的后端
```

或者 `LEARNME_BACKEND=deepseek ./run.sh start`。每个任务也可以单独选后端（派发对话框里的「后端」，或主理人调用 `dispatch_task(backend=...)`）；顶栏可以切换主理人的后端（切换后对话从头开始，旧消息仍可搜索）。

### Claude

用 [Claude Agent SDK](https://docs.anthropic.com/en/docs/claude-code/sdk) 和它自带的 Claude Code CLI，全部功能可用。模型在 `[backends.claude]` 里设（`model`、`manager_model`、`effort`）。

### DeepSeek

把 Claude Code 指向 DeepSeek 的 [Anthropic 兼容接口](https://api-docs.deepseek.com/guides/anthropic_api)（`https://api.deepseek.com/anthropic`），做法同 DeepSeek 官方的 [Claude Code 接入说明](https://api-docs.deepseek.com/quick_start/agent_integrations/claude_code)。功能和 Claude 相同。learnMe 只给这个会话设置 `ANTHROPIC_BASE_URL`、`ANTHROPIC_AUTH_TOKEN` 和模型变量，并清空 `ANTHROPIC_API_KEY`，你的 Anthropic 凭证不会发给 DeepSeek。默认模型 `deepseek-flash`（也可选 `deepseek-v4-pro`）。在 `[backends.deepseek]` 里填价格才会显示费用。

### Codex

驱动 [`codex app-server`](https://developers.openai.com/codex/app-server)（stdio 上的 JSON-RPC，每个窗口一个进程）：`thread/start|resume|fork`、`turn/start`、`turn/steer`（往运行中的回合追加消息）、`turn/interrupt`、`thread/compact/start`、命令和改文件的审批请求、token 用量。learnMe 的工具以 HTTP MCP 挂进去（`mcp_servers.learnme.url`，每个窗口一个 bearer token）。权限模式映射到 Codex 的审批策略加沙箱：`default` → untrusted + workspace-write，`acceptEdits` → on-request + workspace-write，`plan` → on-request + read-only，`bypassPermissions` → never + danger-full-access；不填则沿用你的 `~/.codex/config.toml`。在 codex-cli 0.144 上测试过；app-server 官方标为实验性接口。

### 各后端支持情况

| | Claude | DeepSeek | Codex |
|---|---|---|---|
| 流式文字和思考 | ✓ | ✓（忽略思考预算） | ✓（推理摘要） |
| 运行中追加消息 | ✓ | ✓ | ✓ `turn/steer` |
| 中断 | ✓ | ✓ | ✓ |
| 重启后恢复会话 | ✓ | ✓ | ✓ `thread/resume` |
| 分叉会话 | ✓ | ✓ | ✓ `thread/fork`（适配器支持；界面只对 Claude Code 历史会话分叉） |
| 窗口里的权限确认 | ✓ 所有工具 | ✓ 所有工具 | ✓ Codex 请求审批的命令和改文件 |
| 护栏（在代码里否决工具调用） | ✓ 每次工具调用 | ✓ 每次工具调用 | 部分：审批请求 + 所有 learnMe 工具 |
| 主理人工具（MCP） | 进程内（或 HTTP） | 进程内 | HTTP |
| 手动压缩 | ✓ 带 learnMe 的摘要规则 | ✓ 带 learnMe 的摘要规则 | ✓ 用 Codex 自己的摘要（不能加规则和重点） |
| 上下文用量 | 精确 | 精确（按 Claude Code 的算法） | 来自 token 用量通知 |
| 费用 | Claude Code 报的美元 | token × 你配置的价格 | 只有 token；配置价格后才有美元 |
| 从历史恢复 Claude Code 会话 | ✓ | ✗ | ✗ |

## 作为服务运行：告警、消息、用户反馈

推：任何能发 HTTP 请求的系统。

```toml
# learnme.toml
[events]
token = "一个足够长的随机串"      # 或 LEARNME_EVENTS_TOKEN；不设就不开放这个入口
max_per_day = 50                   # 推和拉合计
project = "my-service"             # 没写项目时用的默认项目
permission_mode = "default"        # 需要授权的工具调用会停在窗口里等你

[events.sources.alertmanager]      # 按来源单独配置
project = "api-server"
runbook = "~/runbooks/alerts.md"   # 这个来源的长期规则，放在每条内容前面
```

```bash
curl -X POST http://127.0.0.1:8770/api/events \
  -H "Authorization: Bearer $LEARNME_EVENTS_TOKEN" -H "Content-Type: application/json" \
  -d '{"source": "alertmanager", "title": "API 5xx 超过 2%", "body": "<完整告警>",
       "key": "api-5xx", "severity": "critical"}'
```

拉：watcher 定时跑命令或请求 URL，把新条目交给 learnMe。例如跟踪以 GitHub issue 形式提交的用户反馈：

```toml
[[events.watchers]]
name = "feedback"
command = "gh issue list --repo you/app --label feedback --state open --json number,title,body,url"
id = "number"                      # 标识一条内容的字段
every = 600                        # 秒
project = "app"
runbook = "~/runbooks/feedback.md" # 比如：复现、分类、小 bug 带测试修掉；用 `gh issue comment` 回复
```

按 id 去重；第一次拉取时已经存在的内容只记基线，不会一启动就开一堆窗口。`batch = true` 把一次拉到的新内容合进一个窗口；改用 `url = "..."` 加 `headers = { Authorization = "Bearer ${TOKEN}" }` 就是轮询 HTTP 接口。

同一个 `key` 的内容在窗口还开着时追加到那个窗口，不会重复开。内容在 prompt 里以引用块标成外部数据，里面夹带的指令只当信息。把告警系统的 webhook 指到推送地址即可（不能加 `Authorization` 头的，中间加一层小转发）。

## Slack（可选）

见 [docs/slack.md](docs/slack.md)：助手 bot 替你回私信和 @，每个对话一个窗口，要你表态的交给你，影响大的在 thread 里 @你 请你批准。只有把你自己的 token 和成员 ID 写进 `data/slack.env` 才会启用。

## 你的画像

`profile/habits.md` 和 `profile/judgment.md` 首次启动时从 `*.example.md` 模板复制而来，可以随便改，也可以让主理人慢慢长出来；格式说明见 [profile/README.md](profile/README.md)。

## 开发

```bash
uv sync --extra slack --group dev
uv run pytest            # 单元和集成测试（假 Codex app-server、假 DeepSeek 接口、模拟 Slack）
uv run ruff check server tests
```

## 已知限制

- **单用户，网页没有登录。** 默认只绑定 127.0.0.1；要对外暴露，请先在前面加认证。
- **Codex**：app-server 协议官方标为实验性，CLI 版本之间可能变化。护栏只看得到 Codex 请求审批的动作（以及所有 learnMe 工具调用），所以 Codex 上的 Slack 窗口以只读沙箱运行。压缩用 Codex 自己的摘要，不能加 learnMe 的规则和重点。不配置价格就没有美元费用。
- **DeepSeek** 开发时用的是本地模拟的 Anthropic 兼容接口加官方文档；DeepSeek 忽略 prompt 缓存，`deepseek-v4-pro` 不支持图片。
- Claude 登录状态的检测是尽力而为（macOS 的登录在钥匙串里，默认视为已登录）。
- 从历史恢复会话只支持 Claude Code 的会话。
- Agent 以你的用户权限在项目目录里运行。权限模式和护栏能降低风险，但不是沙箱（Codex 的沙箱模式除外）。

## 许可证

MIT（见 [LICENSE](LICENSE)）。
