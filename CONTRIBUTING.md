# Contributing to learnMe

Thanks for taking a look. Bug reports, fixes, new backends and better docs are all welcome.

## Getting set up

```bash
git clone https://github.com/realzhukaihua/learnMe.git && cd learnMe
uv sync --extra slack --group dev
uv run pytest                     # about 15 seconds; no key or network needed
uv run ruff check server tests
./run.sh demo                     # the scripted demo on http://127.0.0.1:8777
```

The tests use fakes for every outside service: `tests/fakes/` has a scripted backend, a fake Codex
app-server and a fake of DeepSeek's API, and the Slack tests use a mock client. `tests/test_demo.py`
plays the whole demo story sped up, so it is a quick end-to-end check of dispatch, supervision, the
event intake and memory tracking.

## Where things are

| Path | What it is |
|---|---|
| `server/session.py` | one window: its agent connection, the event stream, permission prompts, wrap-up |
| `server/manager.py` | the main window: the manager's tools and prompt |
| `server/console.py` | the registry of windows, the supervisor that hands stopped tasks to the manager |
| `server/backends/` | Claude, Codex and DeepSeek behind one interface (`base.py` explains the events) |
| `server/events.py` | outside input: `POST /api/events` and the watchers |
| `server/memory.py` | tracking, diffing and reverting memory files |
| `server/demo/` | demo mode: the scripted backend and the director |
| `server/prompts/`, `server/locales/`, `web/i18n.js` | English and Chinese text |
| `web/` | the browser UI (plain JavaScript, no build step) |

## Changes

- Keep a pull request to one change, and add or update a test for it.
- UI text goes through `tr()` (server) or `tr()` in `web/i18n.js`, with a Chinese entry next to the
  English one; `tests/test_i18n.py` fails if a string has no Chinese.
- A new backend implements `Backend` and `AgentConnection` from `server/backends/base.py` and maps its
  tool calls onto Claude Code's names (`Bash`, `Edit`, `TodoWrite`, ...) so the UI renders it the same
  way. Add a fake of its protocol under `tests/fakes/` rather than calling the real service in tests.
- Prompts change how the manager behaves for everyone. If you change one, say in the pull request what
  you saw before and after.

## Keep your own data out

learnMe runs on your real projects, so its data is personal. `data/`, `learnme.toml`, `*.env` and
your `profile/*.md` are git-ignored; keep it that way. For screenshots and recordings use
`./run.sh demo`, where every name and number is made up. Before you push, check that a diff or an image
doesn't show a path, host name, token or someone's name from your own setup.

## Reporting a security problem

Please don't open a public issue for anything that could let someone else run commands or read data
through learnMe. Report it privately from the Security tab of this repository (Report a vulnerability).
