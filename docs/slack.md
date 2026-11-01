# Slack: an assistant that answers for you (optional)

With the bridge on, people can DM your assistant bot or @mention it in a channel. Every conversation
(a DM, or a thread) gets its own learnMe window, titled `💬 Slack · …`, which decides on its own:

- **Answer** factual questions it can back with evidence (task progress, links, numbers), and do
  read-only lookups, dispatching a read-only task when it needs one.
- **Hand it to you** (`slack_triage escalate`, with a suggested reply) when it would commit you to
  something, or when it isn't sure. You say "send it" in the main window.
- **Ask for your approval** (`slack_ask_approval`) for anything high impact: production changes,
  merges, mass messages, anything irreversible or privacy-related. It @mentions you in the thread and
  waits for *your* reply (by Slack user id, never by display name) there or in the main window.
- **Dispatch a low-impact task** for someone else's request, under constraints the system puts in
  front of the task (read-only or small reversible changes; at most `SLACK_TASKS_PER_DAY` a day).

What is enforced in code, not left to the prompt (`server/slack_bridge.py: check_tool`):

- On anyone else's message, a window can't change the manager (memory, profile, learnMe's code and
  config), can't write files except the Slack reply preferences, runs only read-only shell commands,
  can't use connector tools that act as you elsewhere, and can only touch its own conversation and
  the tasks it dispatched.
- Each batch of new messages gets one answer; each stop of a task it dispatched one more. Your own
  word has no limit. Replies containing tokens, passwords or private keys are refused.
- Hourly and daily limits (see `slack.env.example`).

Your own DM to the bot goes straight to the main window, as if you typed it there, and the manager's
reply is posted back to the DM.

Full guard coverage needs the Claude or DeepSeek backend (PreToolUse hooks see every tool call). On
Codex, Slack windows run with a read-only sandbox and ask for approval for anything else; the guard
sees those approvals and every call to learnMe's own tools.

## Setup

1. api.slack.com/apps → **Create New App → From an app manifest** → paste `docs/slack_manifest.yaml`.
   Install it to your workspace (an admin may need to approve).
2. **Basic Information → App-Level Tokens**: create one with scope `connections:write` (`xapp-…`).
3. **OAuth & Permissions**: copy the Bot User OAuth Token (`xoxb-…`).
4. Copy `docs/slack.env.example` to `data/slack.env`, fill in the two tokens and your member id
   (Slack profile → ⋮ → Copy member ID), then `chmod 600 data/slack.env`.
5. Install the Slack extra (`uv sync --extra slack`) and restart learnMe (`./run.sh restart`). The main
   window shows "💬 Slack connected"; if not, `./run.sh logs` says why.

Socket Mode means the machine needs no public URL.
