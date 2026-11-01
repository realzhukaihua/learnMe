# learnMe

**一个本地网页控制台：同时跑很多个编程 agent 会话，由一个主控 agent 盯着它们，并且学习*你*怎么判断该停、该继续、该换方向。**

[English](README.md)

你在主窗口里只和一个**主控**说话。独立的事会开成**子窗口**：每个子窗口是一个完整的 agent 会话（Claude Code、OpenAI Codex 或 DeepSeek），跑在你的某个项目目录里。子任务停下时，主控来验收：通过就结束，没做完就推进，方向错了就纠正，要你拍板的就报给你。每一次干预都会记下来，主控把它们提炼成你的**画像**（工作习惯和判断方式），以后每个子任务都会读到。开了**自动值守**入口后，告警、webhook、Slack 消息会各自开窗口自动处理，有风险的操作要等你点头。

## 功能

- **多窗口派发。** 说「去 *api-server* 查一下夜间任务为什么慢」，主控就在那个项目里开一个窗口；主窗口始终只是对话。窗口实时显示文字、思考、工具调用、权限确认、todo 清单、上下文用量和费用。
- **主控监督。** 子任务一轮结束，主控收到交接，看证据（任务状态、diff、快速自查）后决定：验收结束、继续推进、纠正方向，或者交给你。自动推进最多 3 轮。
- **学习你的判断。** 每一次「结束 / 继续 / 纠正 / 中断 / 批准」都连同当时的任务快照记下来，并区分是你亲自做的、主控按你的话做的，还是主控自己做的。任务结束时主控读这份记录，重点看你推翻它的地方，更新 `profile/judgment.md`（什么时候停、继续、换方向）和 `profile/habits.md`（你的工作习惯，所有子任务都读）。
- **分层记忆，不丢知识。** 存储不设上限，加载有选择：只有一小块常驻内容（习惯、判断、主控的 `MEMORY.md` 索引）进 prompt；超了预算就拆成主题文件，绝不靠删知识压字数。所有记忆文件（画像、主控记忆、各项目的 `CLAUDE.md` / `AGENTS.md` / agent 记忆）都有追踪、diff、全文检索，界面上可以回滚；你亲手改的算最重要的纠正信号。
- **自动值守。** `POST /api/events` 把一条告警（Alertmanager、Grafana、CI、任意 webhook）变成一个窗口：先只读诊断，runbook 允许的就自己处理，有风险的以 `NEEDS APPROVAL:` 停下，由主控验收后报给你。可选的 **Slack 桥** 对发给助手 bot 的消息做同样的事：每个对话一个窗口，能回的自己回，要你表态的交给你，影响大的在 thread 里 @你 等批准，护栏在代码里强制执行。
- **有什么 key 就用什么模型。** `backend = "auto"` 自动选本机已经配好的 Claude、Codex 或 DeepSeek；每个任务也可以单独选；主控的后端在顶栏切换。
- 界面和提示词都有中英文两套（`language = "en" | "zh"`）。

## 架构

```
 浏览器 (web/)  ── WebSocket + REST ──┐
                                      ▼
 ┌──────────────────────── server/ (FastAPI) ────────────────────────┐
 │ console.py   注册表 · Supervisor（任务停下 → 交给主控验收）       │
 │ manager.py   主控会话 + learnMe 的工具（ToolDef 列表）            │
 │ session.py   每个窗口一个 TaskSession：事件流、决策记录           │
 │ memory.py    记忆文件追踪：diff、全文检索、回滚                   │
 │ store.py     SQLite：任务、事件、决策、记忆变更历史               │
 │ mcp_http.py  /mcp/<窗口>：通过 MCP Streamable HTTP 提供工具        │
 │ events.py    /api/events：告警和 webhook → 值守窗口                │
 │ slack_*.py   可选的 Slack 桥和带护栏的 Slack 窗口                  │
 │                                                                    │
 │ backends/  AgentConnection：start · query · interrupt · compact · │
 │            context_usage · close → 统一事件（text、tool_use、      │
 │            tool_result、result …）                                 │
 │   claude.py    Claude Agent SDK（Claude Code），工具进程内挂载     │
 │   deepseek.py  Claude Code → DeepSeek 的 Anthropic 兼容接口        │
 │   codex.py     `codex app-server` JSON-RPC，工具走 HTTP MCP        │
 └────────────────────────────────────────────────────────────────────┘
```

会话层不直接依赖任何厂商 SDK。后端把 `SessionSpec`（工作目录、模型、权限模式、恢复/分叉 id、系统提示、工具、权限和护栏回调）变成一个活的连接，吐出同一套事件；工具名统一映射成 Claude Code 的（`Bash`、`Edit`、`TodoWrite`、`mcp__learnme__dispatch_task`），所以界面对所有后端渲染一致。详见 `server/backends/base.py`。

## 安装

需要 Python 3.11+、[uv](https://docs.astral.sh/uv/)（或 pip），以及下面任意一个后端。

```bash
git clone <本仓库> learnme && cd learnme
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

或者 `LEARNME_BACKEND=deepseek ./run.sh start`。每个任务也可以单独选后端（派发对话框里的「后端」，或主控调用 `dispatch_task(backend=...)`）；顶栏可以切换主控的后端（切换后对话从头开始，旧消息仍可搜索）。

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
| 主控工具（MCP） | 进程内（或 HTTP） | 进程内 | HTTP |
| 手动压缩 | ✓ 带 learnMe 的摘要规则 | ✓ 带 learnMe 的摘要规则 | ✓ 用 Codex 自己的摘要（不能加规则和重点） |
| 上下文用量 | 精确 | 精确（按 Claude Code 的算法） | 来自 token 用量通知 |
| 费用 | Claude Code 报的美元 | token × 你配置的价格 | 只有 token；配置价格后才有美元 |
| 从历史恢复 Claude Code 会话 | ✓ | ✗ | ✗ |

## 演示

新装好之后一次真实冒烟测试的记录（有删节）：

```
你       在 demo 项目里，calc.py 的 add() 结果不对。派个任务修一下，用一行 python 验证。
主控     → project_history(demo) → recall("calc add") → dispatch_task(project=demo, title="Fix calc.add()")
         开了窗口「Fix calc.add()」（任务 8f34b156），它会做最小修改、用 python -c 断言验证，再交回 diff 和输出。
子任务   $ cat -n calc.py → 2  return a - b
         根因：add() 做了减法。$ sed -i … && git diff && python -c "…assert add(2,3)==5…" → ok
[自动监督] 验收 · 「Fix calc.add()」 · 本轮完成，待决策 · 已自动推进 0/3 轮
主控     → task_status → 自己换几组输入再验一遍 → end_task(reason="验收通过：…")
子任务   收尾：检查项目记忆是否需要更新… 无需更新。
[自动监督] 收尾 · 「Fix calc.add()」 · 已结束
主控     → task_decisions → 没有被推翻的地方，judgment.md 和 habits.md 不用改。
```

一条告警，直到需要拍板之前都不用人管（同样是真实运行）：

```
$ curl -X POST localhost:8770/api/events -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"source":"ci","title":"demo: calc check failing","body":"…AssertionError","key":"demo-smoke","project":"demo"}'
{"ok":true,"task_id":"75a4ffbe","appended":false}
主窗口   🚨 来自 ci 的事件：demo: calc check failing → 窗口 75a4ffbe
子任务   （只读）git status、git log、git show HEAD:calc.py；分别对已提交的文件和工作区重跑 CI 检查
         →「是真 bug 不是噪音：已提交的 add() 做了减法，修复在工作区里还没提交。」推到 main 会动共享分支，
         所以停下来，写好给人执行的命令。
[自动监督] 验收 · 「🚨 ci · demo: calc check failing」 · 最近一次指令来自外部事件
主控     → task_status → 自己查一遍仓库 →「这条 CI 告警在等你批准，我没有替你批准。原因：… 方案：…」
```

## 自动值守：告警和 webhook

```toml
# learnme.toml
[events]
token = "一个足够长的随机串"      # 或 LEARNME_EVENTS_TOKEN；不设就不开放这个入口
max_per_day = 50
project = "my-service"             # 事件没写项目时用的默认项目
permission_mode = "default"        # 需要授权的工具调用会停在窗口里等你
runbook = "~/runbooks/on-call.md"  # 值守的长期规则，放在每个事件前面
```

```bash
curl -X POST http://127.0.0.1:8770/api/events \
  -H "Authorization: Bearer $LEARNME_EVENTS_TOKEN" -H "Content-Type: application/json" \
  -d '{"source": "alertmanager", "title": "API 5xx 超过 2%", "body": "<完整告警>",
       "key": "api-5xx", "project": "api-server", "severity": "critical"}'
```

同一个 `key` 的事件在窗口还开着时会追加到那个窗口，不会重复开。事件内容在 prompt 里以引用块标成外部数据，告警里夹带的指令只当信息。把告警系统的 webhook 指到这个地址即可（不能加 `Authorization` 头的，中间加一层小转发）。

## Slack（可选）

见 [docs/slack.md](docs/slack.md)：助手 bot 替你回私信和 @，每个对话一个窗口，要你表态的交给你，影响大的在 thread 里 @你 请你批准。只有把你自己的 token 和成员 ID 写进 `data/slack.env` 才会启用。

## 你的画像

`profile/habits.md` 和 `profile/judgment.md` 首次启动时从 `*.example.md` 模板复制而来，可以随便改，也可以让主控慢慢长出来；格式说明见 [profile/README.md](profile/README.md)。

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
