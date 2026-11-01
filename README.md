# learnMe

**A local web console that runs many coding-agent sessions at once, keeps a manager agent watching
over them, and learns how *you* decide when to stop, continue or redirect them.**
(Its Chinese nickname is 主理人 agent, roughly "the agent in charge".)

[中文说明](README.zh-CN.md)

![learnMe with several windows at once](docs/images/overview.png)
*The manager on the left; on the right, an alert waiting for a decision, a task waiting for a
permission, and two tasks running. All data in these pictures is made up.*

## Why learnMe

**1. Agents stop halfway, and someone has to keep nudging them.** Without learnMe:

> **Agent:** Sorry, I ran into an error, so I stopped.  
> **Me:** If there's an error, fix it.  
> **Agent:** Sure. When there's an error, I'll find out what caused it…  
> **Me:** So did you fix it?  
> **Agent:** Sure…

With learnMe, the manager reviews every task the moment it stops. Not done yet: it tells the window to
keep going and what is still missing. Going in circles: it corrects the direction. Nobody has to sit
there asking "did you fix it?" ([accept](docs/images/supervise-accept.png) ·
[continue](docs/images/supervise-continue.png) · [redirect](docs/images/supervise-redirect.png))

**2. Long tasks stop short of what you asked, because of context rot.** *Context rot*: as a session's
context grows, the model gradually loses track of the requirements and details from early on, and its
work gets worse. Without learnMe, a long task forgets half the brief and calls itself done. With
learnMe, your requirements and your own words go into layered memory and rules that every task reads.
When a task stops, the manager, which wrote the brief and doesn't carry the task's long context,
checks the result against what you asked for and pushes it on if it falls short. When a window is
compacted, the summary must keep the open to-dos and your words verbatim (Claude and DeepSeek
backends). ([layered memory](#layered-memory-that-doesnt-forget) · [a rule learned](docs/images/learn-rule.png))

**3. No more juggling a pile of Claude and Codex windows every day.** Without learnMe: a terminal per
agent per project, and you are the one who remembers what is running where. With learnMe it's all in
one: you talk only to the manager. It answers you itself, or dispatches a task and opens a window for
it; each window can run a different backend (Claude, Codex or DeepSeek), and one page shows them all.
([one message, three windows](docs/images/dispatch.gif))

**4. It keeps watch while you're away.** Without learnMe, *Alice (Slack): "Is the 5xx spike on billing
from your change?"* waits until you happen to see it. With learnMe, an agent on your side reads it
first and works out whether it can handle it: if it can, it looks into it and replies on its own; if
it's beyond its permissions or risky, it asks for your approval. Alerts and user feedback work the
same way: they come in, get a window and get handled.
([unattended](docs/images/unattended.gif) · [alerts and feedback](#runs-unattended-alerts-messages-user-feedback) ·
[Slack](#slack-optional))

## How it works

You talk to one **manager** in the main window. Independent work becomes its own **task window**: a
full agent session (Claude Code, OpenAI Codex, or DeepSeek) in one of your project folders. When a
task stops, the manager reviews it, pushes it on, corrects it, or brings it to you. Every steering
decision is logged, and the manager distills them into a **profile** of your habits and judgment
that every later task reads. Run as a service, it also takes **outside input** on its own: alerts,
messages and user feedback open windows that get handled without you, and anything risky waits for
your yes.

## Features

### One sentence, several windows

![Dispatch: one message in the main window, three task windows at work](docs/images/dispatch.gif)

*One message in the main window. The manager checks the project history, opens three task windows
(one of them on Codex), and they start working.*

"Go to *api-server* and find why the nightly job is slow" opens a window there; the main window stays a
conversation. Windows stream live: text, thinking, tool calls, permission prompts, todo lists, a
context meter and cost. Before and after: [one message](docs/images/dispatch-before.png) →
[three windows at work](docs/images/dispatch-after.png).

### Active supervision

When a task's turn ends, the supervisor hands it to the manager, which checks the evidence (task
status, diffs, quick checks of its own) and decides. It stops pushing on its own after 3 rounds, and
stays out of tasks you are steering yourself.

| | |
|---|---|
| ![Accept](docs/images/supervise-accept.png) | ![Continue](docs/images/supervise-continue.png) |
| **Accept:** the manager re-runs the link checker itself, then ends the task with the evidence as the reason. | **Continue:** the feature works but tests are missing, so the manager says exactly what is left. |
| ![Redirect](docs/images/supervise-redirect.png) | ![Escalate](docs/images/supervise-escalate.png) |
| **Redirect:** 20 minutes of tuning Spark memory, so the manager points it at Monday's schema change. | **Escalate:** a production rollback is for you to decide; the manager reports the cause and the plan, and doesn't approve it. |

![The supervision loop](docs/images/supervision.en.svg)

### Runs unattended: alerts, messages, user feedback

![Unattended: a watcher finds new feedback; the window fixes it and stops for approval; the manager asks you](docs/images/unattended.gif)

*Nobody clicks anything: a watcher finds a new piece of user feedback, a window opens and reproduces
it, fixes the code with a test, stops at `NEEDS APPROVAL` for a production backfill, and the manager
brings that decision to you.*

Push items in with `POST /api/events` (alerting tools, CI, chat relays, forms), or let **watchers**
pull them: a command or URL polled on a schedule, such as new GitHub issues labelled `feedback`.
Each source can have its own runbook. Windows diagnose read-only first, do what the runbook allows,
and stop with `NEEDS APPROVAL:` for anything risky.

![An alert window stopped at NEEDS APPROVAL](docs/images/alert-approval.png)
*An Alertmanager alert: the window reads logs and the deploy history, finds the cause, and stops with
a reversible rollback plan for a human.*

### Learns how you judge

Every *end / continue / correct / interrupt / approve* is recorded with a snapshot of the task, and
with who did it: you, the manager on your word, or the manager on its own. Your corrections become
rules with their evidence, and the next similar case follows them.

| 1. You correct the manager once | 2. A rule with its evidence | 3. The next similar case |
|---|---|---|
| ![Correction](docs/images/learn-correction.png) | ![Rule diff](docs/images/learn-rule.png) | ![Rule applied](docs/images/learn-applied.png) |
| The manager had let a task start a production index build; you say no. It stops the task and writes the rule down. | `judgment.md` gains a rule with the date and your words; the change is attributed to the manager and can be reverted. | Later, a feedback window proposes a production backfill; the manager cites the new rule and asks you instead of approving. |

### Layered memory that doesn't forget

![Memory layers](docs/images/memory-layers.en.svg)

Storage is unbounded, loading is selective: only a small always-loaded slice (habits, judgment, the
manager's `MEMORY.md` index) goes into prompts. Going over budget means splitting into topic files,
never deleting. Every memory file (profile, manager memory, each project's `CLAUDE.md` / `AGENTS.md`
/ agent memory) is tracked, diffed, full-text searchable and revertible from the UI.

![The memory panel with example rules](docs/images/memory.png)
*The memory panel: your profile (example rules), the manager's memory, each project's memory, and
the change history.*

### Pick your model by the key you have

`backend = "auto"` uses whichever of Claude, Codex or DeepSeek is set up on the machine; each task
can choose its own; the manager can switch from the top bar. See [below](#choose-a-backend-by-the-key-you-have).

### English and Chinese

UI and prompts come in both (`language = "en" | "zh"`).

![The Chinese UI](docs/images/overview-zh.png)

## Architecture

![Architecture](docs/images/architecture.en.svg)

| Module | What it does |
|---|---|
| `server/console.py` | registry of windows; the Supervisor turns every stop into a review item for the manager |
| `server/manager.py` | the manager session and learnMe's tools (a backend-neutral `ToolDef` list) |
| `server/session.py` | one `TaskSession` per window: the event stream and the decision log |
| `server/memory.py` | tracked memory files: diffs, full-text search, attribution, revert |
| `server/store.py` | SQLite: tasks, events, decisions, memory history |
| `server/mcp_http.py` | `/mcp/<window>`: the tools over MCP Streamable HTTP, for backends that can't host them in-process |
| `server/events.py` | outside input: `POST /api/events` and the watchers |
| `server/slack_*.py` | the optional Slack bridge and its guarded windows |
| `server/backends/` | `claude.py` (Claude Agent SDK), `deepseek.py` (Claude Code on DeepSeek's Anthropic-compatible API), `codex.py` (`codex app-server` JSON-RPC) |

The session layer never imports a vendor SDK. A backend turns a `SessionSpec` (cwd, model, permission
mode, resume/fork id, instructions, tools, permission and guard callbacks) into a live connection and
emits the same event stream, with tool names mapped onto Claude Code's (`Bash`, `Edit`, `TodoWrite`,
`mcp__learnme__dispatch_task`) so the UI renders every backend the same way. See
`server/backends/base.py`.

## Install

Requirements: Python 3.11+, [uv](https://docs.astral.sh/uv/) (or pip), and at least one backend below.

```bash
git clone <this repo> learnme && cd learnme
uv sync                    # or: python -m venv .venv && .venv/bin/pip install -e .
./run.sh doctor            # which backends are ready, and what the others need
./run.sh start             # → http://127.0.0.1:8770
```

Optional: `cp learnme.example.toml learnme.toml` to set the language, project folders, models and
more. By default the subfolders of the folder you cloned into are offered as projects.

## Choose a backend by the key you have

| You have | Do this | Backend |
|---|---|---|
| A Claude subscription (Pro/Max/Team) | install [Claude Code](https://docs.anthropic.com/en/docs/claude-code) and run `claude` once to log in | `claude` |
| An Anthropic API key | `export ANTHROPIC_API_KEY=...` | `claude` |
| A ChatGPT plan | `npm i -g @openai/codex && codex login` | `codex` |
| An OpenAI API key | `npm i -g @openai/codex && export OPENAI_API_KEY=...` (learnMe logs Codex in with it on first use, as `codex login --with-api-key` would) | `codex` |
| A DeepSeek API key | `export DEEPSEEK_API_KEY=...` | `deepseek` |

That's all: with `backend = "auto"` (the default) learnMe uses the first ready backend in the order
claude → codex → deepseek. To choose explicitly:

```toml
# learnme.toml
backend = "codex"           # default for task windows
manager_backend = "claude"  # the main window
```

or `LEARNME_BACKEND=deepseek ./run.sh start`. A task can also pick its own backend (the "Backend"
field when dispatching, or `dispatch_task(backend=...)` from the manager), and the top bar switches
the manager's backend (its conversation starts fresh; the old one stays searchable).

### Claude

Uses the [Claude Agent SDK](https://docs.anthropic.com/en/docs/claude-code/sdk) and the Claude Code
CLI it bundles. Everything is supported. Set models in `[backends.claude]` (`model`, `manager_model`,
`effort`).

### DeepSeek

Claude Code pointed at DeepSeek's [Anthropic-compatible API](https://api-docs.deepseek.com/guides/anthropic_api)
(`https://api.deepseek.com/anthropic`), as in DeepSeek's own
[Claude Code guide](https://api-docs.deepseek.com/quick_start/agent_integrations/claude_code). Same
features as Claude; learnMe sets `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN` and the model variables
for that session only and blanks `ANTHROPIC_API_KEY`, so your Anthropic credentials are never sent to
DeepSeek. Default model `deepseek-flash` (`deepseek-v4-pro` also listed). Set prices in
`[backends.deepseek]` to see costs.

### Codex

Drives [`codex app-server`](https://developers.openai.com/codex/app-server) (JSON-RPC over stdio, one
process per window): `thread/start|resume|fork`, `turn/start`, `turn/steer` to add to a running turn,
`turn/interrupt`, `thread/compact/start`, approval requests for commands and file changes, and token
usage. learnMe's tools are mounted as an MCP server over HTTP (`mcp_servers.learnme.url`, with a
per-window bearer token). Permission modes map to Codex approval policy + sandbox:
`default` → untrusted + workspace-write, `acceptEdits` → on-request + workspace-write,
`plan` → on-request + read-only, `bypassPermissions` → never + danger-full-access; empty keeps your
`~/.codex/config.toml`. Tested with codex-cli 0.144; the app-server is marked experimental upstream.

### What each backend supports

| | Claude | DeepSeek | Codex |
|---|---|---|---|
| Streaming text and thinking | ✓ | ✓ (thinking budgets ignored) | ✓ (reasoning summaries) |
| Add to a running turn | ✓ | ✓ | ✓ `turn/steer` |
| Interrupt | ✓ | ✓ | ✓ |
| Resume after a restart | ✓ | ✓ | ✓ `thread/resume` |
| Fork a conversation | ✓ | ✓ | ✓ `thread/fork` (adapter only; the UI forks only Claude Code history) |
| Permission prompts in the window | ✓ every tool | ✓ every tool | ✓ commands and file changes Codex asks about |
| Guards (veto a tool call in code) | ✓ every tool call | ✓ every tool call | partial: approval requests + all learnMe tools |
| Manager tools (MCP) | in-process (or HTTP) | in-process | HTTP |
| Compact on request | ✓ with learnMe's summary rules | ✓ with learnMe's summary rules | ✓ Codex's own summary (no rules, no focus) |
| Context meter | exact | exact (as Claude Code counts it) | from token-usage updates |
| Cost | $ reported by Claude Code | $ from tokens × your prices | tokens; $ only with configured prices |
| Restore Claude Code sessions from history | ✓ | ✗ | ✗ |

## Running as a service: alerts, messages, user feedback

Push: anything that can send an HTTP request.

```toml
# learnme.toml
[events]
token = "a-long-random-string"     # or LEARNME_EVENTS_TOKEN; the endpoint is off without one
max_per_day = 50                   # pushed and pulled together
project = "my-service"             # default project for items that don't name one
permission_mode = "default"        # tool calls needing approval wait in the window for you

[events.sources.alertmanager]      # per-source settings
project = "api-server"
runbook = "~/runbooks/alerts.md"   # standing instructions, put in front of every item from this source
```

```bash
curl -X POST http://127.0.0.1:8770/api/events \
  -H "Authorization: Bearer $LEARNME_EVENTS_TOKEN" -H "Content-Type: application/json" \
  -d '{"source": "alertmanager", "title": "API 5xx above 2%", "body": "<the full alert>",
       "key": "api-5xx", "severity": "critical"}'
```

Pull: watchers poll a command or a URL and hand each new item on. For example, to follow user
feedback filed as GitHub issues:

```toml
[[events.watchers]]
name = "feedback"
command = "gh issue list --repo you/app --label feedback --state open --json number,title,body,url"
id = "number"                      # the field that identifies an item
every = 600                        # seconds
project = "app"
runbook = "~/runbooks/feedback.md" # e.g. reproduce, classify, fix small bugs with a test; reply with `gh issue comment`
```

Items are deduplicated by id; at the first poll the items already there are only remembered, so
starting a watcher doesn't flood you. `batch = true` puts all new items of one poll in one window;
`url = "..."` with `headers = { Authorization = "Bearer ${TOKEN}" }` polls an HTTP API instead.

The same `key` while its window is open is appended to that window instead of opening another. The
item is quoted and marked as external data in the prompt, so instructions inside it are treated as
information. Point your alerting tool's webhook at the push URL (through a small relay if it can't
set an `Authorization` header).

## Slack (optional)

See [docs/slack.md](docs/slack.md): your assistant bot answers DMs and @mentions for you, one window
per conversation, escalates what needs you, and asks for your approval in the thread for anything high
impact. Off unless you put your own tokens and member id in `data/slack.env`.

## Your profile

`profile/habits.md` and `profile/judgment.md` start as copies of the `*.example.md` templates. Edit
them freely, or let the manager grow them; see [profile/README.md](profile/README.md) for the format.

## Development

```bash
uv sync --extra slack --group dev
uv run pytest            # unit + integration tests (fake Codex app-server, fake DeepSeek API, mock Slack)
uv run ruff check server tests
```

`tests/test_deepseek_backend.py` runs the real Claude Code CLI (bundled with the SDK) against a local
fake of DeepSeek's API, and checks that only the DeepSeek key reaches it. `tests/test_codex_backend.py`
runs the Codex adapter against a scripted app-server.

## Known limitations

- **Single user, no login on the web UI.** It binds to 127.0.0.1 by default; put authentication in
  front of it before exposing it anywhere.
- **Codex**: the app-server protocol is marked experimental and can change between CLI versions.
  Guards only see what Codex asks approval for (plus every learnMe tool call), so Slack windows on
  Codex run read-only. Compaction uses Codex's own summary, without learnMe's rules or a focus. No
  dollar cost unless you configure prices.
- **DeepSeek** was developed against a local mock of its Anthropic-compatible API plus DeepSeek's
  documentation; prompt caching is ignored by DeepSeek, and `deepseek-v4-pro` drops images.
- Claude login detection is best effort (on macOS the login lives in the keychain and is assumed).
- Restoring sessions from history works for Claude Code sessions only.
- Agents run with your user's permissions in your project folders. Permission modes and guards reduce
  risk; they are not a sandbox (Codex's sandbox modes are the exception).

## License

MIT (see [LICENSE](LICENSE)).
