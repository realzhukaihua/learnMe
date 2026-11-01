"""The demo's made-up world: four small projects, two runbooks, a feedback feed and a learnme.toml.

Imports nothing from the server, so it can run before the configuration is read.
"""

import json
import shlex
from pathlib import Path

EVENTS_TOKEN = "demo"
FEED = "feeds/feedback.json"  # relative to the demo's data dir; the director appends to it
FEED_START = [{"id": "fb-101", "title": "Love the new dark mode", "body": "Looks great on my phone."}]

FILES = {
    "todo-app/CLAUDE.md": (
        "# todo-app\n\n"
        "- Dates are stored as ISO strings in UTC; the UI converts to local time.\n"
        "- Keep changes small and run the tests before saying something is done.\n"),
    "todo-app/src/models/todo.ts": "export interface Todo {\n  id: string;\n  title: string;\n  done: boolean;\n}\n",
    "todo-app/src/export/csv.ts": (
        "import fs from 'fs';\n\n"
        "export function writeCsv(path: string, rows: string[]) {\n"
        "  fs.writeFileSync(path, rows.join('\\n'), 'latin1');\n"
        "}\n"),
    "data-pipeline/etl/jobs.py": "def dedup_join(events, users):\n    return events.join(users, on=\"user_id\")\n",
    "docs-site/docs/index.md": "# Docs\n\n- [Setup](./setup.md)\n- [API reference](./api.md)\n",
    "billing-service/AGENTS.md": "# billing-service\n\n- Production changes (deploys, rollbacks, config) always need a human.\n",
    "billing-service/src/payments/client.ts": "export const client = { timeout: 2_000 };\n",
}

RUNBOOKS = {
    "alerts.md": "billing-service: read logs, metrics and deploy history freely. Any production change needs approval.\n",
    "feedback.md": "Reproduce each report in todo-app, fix small bugs in code with a test, and never reply to users directly.\n",
}


def _q(value: str | Path) -> str:
    return json.dumps(str(value))  # a TOML basic string


def prepare(root: Path, projects: Path) -> Path:
    """Write the demo world under `root` (projects under `projects`); returns the learnme.toml path."""
    for rel, text in FILES.items():
        p = projects / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    for name, text in RUNBOOKS.items():
        p = root / "runbooks" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    feed = root / FEED
    feed.parent.mkdir(parents=True, exist_ok=True)
    feed.write_text(json.dumps(FEED_START, indent=1), encoding="utf-8")
    config = root / "learnme.toml"
    config.write_text(f"""# Written by the demo on every start.
language = "en"

[events]
token = {_q(EVENTS_TOKEN)}

[events.sources.alertmanager]
project = "billing-service"
runbook = {_q(root / "runbooks" / "alerts.md")}

# The director polls the feed on cue instead of waiting for the timer, so the story keeps its pace.
[[events.watchers]]
name = "feedback"
command = {_q("cat " + shlex.quote(str(feed)))}
id = "id"
every = 3600
project = "todo-app"
backend = "codex"
runbook = {_q(root / "runbooks" / "feedback.md")}
""", encoding="utf-8")
    return config
