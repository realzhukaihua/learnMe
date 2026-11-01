# learnMe

**A local web console that runs many coding-agent sessions at once, keeps a manager agent watching
over them, and learns how *you* decide when to stop, continue or redirect them.**

[中文说明](README.zh-CN.md)

You talk to one **manager** in the main window. Independent work becomes its own **task window**: a
full agent session (Claude Code, OpenAI Codex, or DeepSeek) in one of your project folders. When a
task stops, the manager reviews it, pushes it on, corrects it, or brings it to you. Every steering
decision is logged, and the manager distills them into a **profile** of your habits and judgment
that every later task reads. With the optional **on-call** entry points, alerts, webhooks or Slack
messages open their own windows and get handled without you, and anything risky waits for your yes.

## Features

- **Multi-window dispatch.** "Go to *api-server* and find why the nightly job is slow" opens a window
  there; the main window stays a conversation. Windows stream live: text, thinking, tool calls,
  permission prompts, todo lists, context meter, cost.
- **Supervision by a manager agent.** When a task's turn ends, the manager gets a hand-off, checks the
  evidence (task status, diffs, quick checks), and decides: accept and end it, continue it, redirect
  it, or ask you. It stops pushing on its own after 3 rounds.
- **Learns your judgment.** Every *end / continue / correct / interrupt / approve* is recorded with a
  snapshot of the task, whether you did it, the manager did it on your word, or the manager did it on
  its own. When a task ends, the manager reads that log, especially where you overruled it, and
  updates `profile/judgment.md` (when you stop, continue, redirect) and `profile/habits.md` (how you
  work; every task reads it).
- **Layered memory that doesn't forget.** Storage is unbounded, loading is selective: only a small
  always-loaded slice (habits, judgment, the manager's `MEMORY.md` index) goes into prompts. Over budget
  means splitting into topic files, never deleting. Every memory file (profile, manager memory, each
  project's `CLAUDE.md` / `AGENTS.md` / agent memory) is tracked, diffed, full-text searchable and
  revertible from the UI; your own edits count as the strongest correction.
- **Autonomous on-call.** `POST /api/events` turns an alert (Alertmanager, Grafana, CI, any webhook)
  into a window that diagnoses read-only first, fixes what the runbook allows, and stops with
  `NEEDS APPROVAL:` for anything risky; the manager reviews it and brings that to you. The optional
  **Slack bridge** does the same for messages to your assistant bot: one window per conversation,
  answering what it can, escalating what needs you, @mentioning you for high-impact approvals, with
  guards enforced in code.
- **Pick your model by the key you have.** `backend = "auto"` uses whichever of Claude, Codex or
  DeepSeek is set up on the machine; each task can choose its own; the manager can switch from the top
  bar.
- English and Chinese UI and prompts (`language = "en" | "zh"`).

## Architecture

```
 Browser (web/)  ── WebSocket + REST ──┐
                                       ▼
 ┌──────────────────────── server/ (FastAPI) ────────────────────────┐
 │ console.py   registry · Supervisor (stop → hand-off to manager)   │
 │ manager.py   the manager session + learnMe's tools (ToolDef list) │
 │ session.py   one TaskSession per window: events, decisions log    │
 │ memory.py    tracked memory files: diff, FTS search, revert       │
 │ store.py     SQLite: tasks, events, decisions, memory history     │
 │ mcp_http.py  /mcp/<window>: the tools over MCP Streamable HTTP    │
 │ events.py    /api/events: alerts and webhooks → on-call windows   │
 │ slack_*.py   optional Slack bridge and guarded Slack windows      │
 │                                                                    │
 │ backends/  AgentConnection: start · query · interrupt · compact · │
 │            context_usage · close  →  flat events (text, tool_use, │
 │            tool_result, result, …)                                 │
 │   claude.py    Claude Agent SDK (Claude Code)  ── tools in-process │
 │   deepseek.py  Claude Code → DeepSeek's Anthropic-compatible API   │
 │   codex.py     `codex app-server` JSON-RPC ── tools via HTTP MCP   │
 └────────────────────────────────────────────────────────────────────┘
```

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

## A walk-through

What a run on a fresh install looks like (transcript of a real smoke test, shortened):

```
you      In the project 'demo', calc.py's add() returns the wrong result. Dispatch a task to fix it
         and verify with a one-line python check.
manager  → project_history(demo) → recall("calc add") → dispatch_task(project=demo, title="Fix calc.add()")
         I opened a new window, "Fix calc.add()" (task 8f34b156). It will make the smallest fix,
         check it with a one-line python -c assert, and report the diff and the output.
task     $ cat -n calc.py   →   2  return a - b
         Root cause: add() subtracts. $ sed -i … && git diff && python -c "…assert add(2,3)==5…"  →  ok
[auto-supervision] Review · "Fix calc.add()" · turn done, needs a decision · auto-continued 0/3
manager  → task_status → re-checks with other inputs itself → end_task(reason="accepted: …")
task     Wrapping up: checking whether the project memory needs updating… Nothing to update.
[auto-supervision] Wrap-up · "Fix calc.add()" · ended, reason: accepted: …
manager  → task_decisions → nothing overruled, so judgment.md and habits.md stay as they are.
```

And an alert, with no human in the loop until a decision is needed (also from a real run):

```
$ curl -X POST localhost:8770/api/events -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"source":"ci","title":"demo: calc check failing","body":"$ python -c \"…assert add(2, 2) == 4\"\nAssertionError","key":"demo-smoke","project":"demo"}'
{"ok":true,"task_id":"75a4ffbe","appended":false}
main     🚨 Event from ci: demo: calc check failing → window 75a4ffbe
task     (read-only) git status, git log, git show HEAD:calc.py; reruns the CI check against the committed
         file and against the working copy → "A real bug, not noise: the committed add() subtracts; a fix
         sits uncommitted in the working copy." Pushing it to main is a shared-branch change, so it
         stops with the exact commands for a human.
[auto-supervision] Review · "🚨 ci · demo: calc check failing" · last instruction from an external event
manager  → task_status → checks the repo itself → "The CI alert on demo is waiting for your approval,
         and I haven't approved it myself. Cause: … Plan: …"
```

## On-call: alerts and webhooks

```toml
# learnme.toml
[events]
token = "a-long-random-string"     # or LEARNME_EVENTS_TOKEN; the endpoint is off without one
max_per_day = 50
project = "my-service"             # default project for events that don't name one
permission_mode = "default"        # tool calls needing approval wait in the window for you
runbook = "~/runbooks/on-call.md"  # standing instructions, put in front of every event
```

```bash
curl -X POST http://127.0.0.1:8770/api/events \
  -H "Authorization: Bearer $LEARNME_EVENTS_TOKEN" -H "Content-Type: application/json" \
  -d '{"source": "alertmanager", "title": "API 5xx above 2%", "body": "<the full alert>",
       "key": "api-5xx", "project": "api-server", "severity": "critical"}'
```

The same `key` while its window is open is appended to that window instead of opening another.
The event text is quoted and marked as external data in the prompt, so instructions inside an alert
are treated as information. Point your alerting tool's webhook at this URL (through a small relay if
it can't set an `Authorization` header).

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
