"""The scripted backend behind demo mode. It speaks the same events as a real one (see
backends/base.py), calls learnMe's real tools for the manager, and really edits the demo's files;
only the words and the shell output are made up.

One script for the whole story: the manager's lines are picked by what it is asked (a request,
the user's correction, an auto-supervision batch), a task window's by what it was told to do.
STATE carries the story across windows (how often each task was reviewed, which window is which).
"""

import asyncio
import json
import os
import re
import uuid
from pathlib import Path

from ..backends.base import AgentConnection, Backend, Capabilities

PACE = float(os.environ.get("LEARNME_DEMO_PACE") or 1)  # tests run the story faster
MODEL = "scripted demo"
STATE: dict = {"reviews": {}, "ids": {}}

FALLBACK = ("(Demo mode: my replies are scripted, so I can only follow the story. In the main window, try "
            "“approve rollback” or “run the backfill”. To use learnMe for real, stop the demo and run "
            "`./run.sh start` with Claude Code, Codex or a DeepSeek key.)")
RULE = ("- Production database changes (indexes, migrations, backfills) always come to me first, even when the fix "
        "looks obvious. (Learned in the demo, after I let the ETL window start an index build: \"Don't let a task "
        "change the production database on its own.\")\n")
RULE_AFTER = "## When to end, continue, or escalate\n"


def reset() -> None:
    STATE.clear()
    STATE.update(reviews={}, ids={})


async def nap(seconds: float) -> None:
    await asyncio.sleep(seconds * PACE)


class DemoBackend(Backend):
    capabilities = Capabilities(resume=True, fork=True, steer=True, interrupt=True, compact=True, compact_rules=True,
                                permission_prompts=True, pre_tool_hook=True, context_usage=True, cost_usd=True)

    def __init__(self, name: str):
        self.name = name
        self.priced = name == "claude"

    def connect(self, spec):
        return DemoConnection(spec, self)

    def models(self):
        return [MODEL]


class DemoConnection(AgentConnection):
    def __init__(self, spec, backend: DemoBackend):
        super().__init__(spec)
        self.backend = backend
        self.closed = asyncio.Event()
        self.play: asyncio.Task | None = None
        self.queue: list[str] = []
        self.pending: str | None = None  # a tool call still running when the turn is cut off
        self.spent = 0.0
        self.ctx = 18000
        self.steps = 0
        self.n = 0

    @property
    def alive(self):
        return not self.closed.is_set()

    async def start(self):
        self.emit("init", session_id=self.spec.resume or f"demo-{uuid.uuid4().hex[:8]}", model=MODEL,
                  permission_mode=self.spec.permission_mode or "default", cwd=self.spec.cwd)

    async def query(self, text):
        if self.play and not self.play.done():
            self.queue.append(text)  # joins the running turn, as steering does on a real backend
            return
        self.play = asyncio.create_task(self._run(text))

    async def _run(self, text):
        self.steps = 0
        try:
            await (manager_turn if self.spec.is_manager else task_turn)(self, text)
        except asyncio.CancelledError:
            if self.pending:
                self.emit("tool_result", tool_use_id=self.pending, content="Interrupted before it finished.",
                          is_error=True, parent=None)
                self.pending = None
            self.emit("result", status="interrupted", subtype="interrupted", reason="aborted", duration_ms=1200,
                      num_turns=self.steps, cost_usd=self._cost(), errors=None, text=None)
            return
        self.emit("context", tokens=self.ctx, window=self._window())
        self.emit("result", status="idle", subtype="success", reason="", duration_ms=4000 + 900 * self.steps,
                  num_turns=self.steps, cost_usd=self._cost(), errors=None, text=None)
        if self.queue:
            self.play = asyncio.create_task(self._run(self.queue.pop(0)))

    def _window(self):
        return 1_000_000 if self.spec.is_manager else 200_000

    def _cost(self):
        if not self.backend.priced:
            return None
        self.spent += 0.012 * max(self.steps, 1)
        return self.spent

    async def interrupt(self):
        if self.play and not self.play.done():
            self.play.cancel()
        else:
            self.emit("result", status="interrupted", subtype="interrupted", reason="aborted", duration_ms=1,
                      num_turns=0, cost_usd=None, errors=None, text=None)

    async def compact(self, focus):
        self.ctx = 12000
        self.emit("compact", trigger="manual", pre_tokens=self.ctx, duration_ms=2000)
        self.emit("result", status="idle", subtype="success", reason="", duration_ms=2000, num_turns=0,
                  cost_usd=None, errors=None, text=None)

    async def context_usage(self):
        return self.ctx, self._window()

    async def wait_closed(self):
        await self.closed.wait()

    async def close(self):
        if self.play and not self.play.done():
            self.play.cancel()
        self.closed.set()

    # --- what a script can do ---

    async def say(self, text: str, pause: float = 0.9) -> None:
        await nap(pause)
        for i in range(0, len(text), 60):  # stream it, as a model would
            self.emit("delta", kind="text", text=text[i:i + 60], parent=None)
            await nap(0.03)
        self.emit("text", text=text, parent=None)
        self.steps += 1
        self.ctx += 900

    def _use(self, name: str, inp: dict) -> str:
        self.n += 1
        tid = f"toolu_demo{self.n:03d}"
        self.emit("tool_use", id=tid, name=name, input=inp, parent=None)
        self.steps += 1
        self.ctx += 1500
        return tid

    async def tool(self, name: str, inp: dict, out: str | None, pause: float = 0.8, run: float = 0.9,
                   error: bool = False) -> None:
        """A tool call with scripted output; out=None leaves it running until the turn is cut off."""
        await nap(pause)
        tid = self._use(name, inp)
        if out is None:
            self.pending = tid
            await nap(600)
            return
        await nap(run)
        self.emit("tool_result", tool_use_id=tid, content=out, is_error=error, parent=None)

    async def read(self, rel: str) -> str:
        """Really read a file in the window's project."""
        p = Path(self.spec.cwd) / rel
        await nap(0.8)
        tid = self._use("Read", {"file_path": str(p)})
        text = p.read_text(encoding="utf-8") if p.is_file() else ""
        await nap(0.5)
        self.emit("tool_result", tool_use_id=tid, content=text or "File does not exist.", is_error=not text, parent=None)
        return text

    async def edit(self, path: Path, old: str, new: str, pause: float = 0.8) -> None:
        """Really edit a file (a project file, or the profile for the manager)."""
        await nap(pause)
        tid = self._use("Edit", {"file_path": str(path), "old_string": old, "new_string": new})
        await nap(0.6)
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        ok = old in text
        if ok:
            path.write_text(text.replace(old, new, 1), encoding="utf-8")
        self.emit("tool_result", tool_use_id=tid, content="The file was updated." if ok else "old_string not found",
                  is_error=not ok, parent=None)

    async def write(self, rel: str, content: str) -> None:
        p = Path(self.spec.cwd) / rel
        await nap(0.8)
        tid = self._use("Write", {"file_path": str(p), "content": content})
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        await nap(0.4)
        self.emit("tool_result", tool_use_id=tid, content="File created.", is_error=False, parent=None)

    async def learnme(self, name: str, args: dict, pause: float = 0.7) -> dict:
        """Call one of learnMe's own tools, the way a model would."""
        await nap(pause)
        tool = next(t for t in self.spec.mcp.tools if t.name == name)
        tid = self._use(f"mcp__learnme__{name}", args)
        out = await tool.handler(args)
        self.emit("tool_result", tool_use_id=tid, content=out["content"], is_error=bool(out.get("is_error")),
                  parent=None)
        try:
            return json.loads(out["content"][0]["text"])
        except (ValueError, KeyError, IndexError):
            return {}


def judgment_file() -> Path:
    from ..session import PROFILE_DIR
    return PROFILE_DIR / "judgment.md"


def todos(done: int, active: str | None = None) -> dict:
    items = ["Add dueDate to the Todo model + migration", "Date picker in the edit form", "Sort overdue items first",
             "Daily 9:00 reminder job", "Tests for overdue sorting and reminders"]
    out = []
    for i, item in enumerate(items):
        status = "completed" if i < done else "in_progress" if i == done and active else "pending"
        out.append({"content": item, "status": status, "activeForm": active if status == "in_progress" else ""})
    return {"todos": out}


# ------------------------------------------------------------------ the manager

async def manager_turn(c: DemoConnection, text: str) -> None:
    low = text.lower()
    if text.startswith("[auto-supervision]"):
        for line in text.splitlines()[1:]:
            m = re.search(r"task_id=(\w+)", line)
            if not m:
                continue
            if line.startswith("- Wrap-up"):
                await c.learnme("task_decisions", {"task_id": m.group(1), "limit": 10})
                await c.say("Wrapped up. Nothing in that task's decisions changes my rules, so judgment.md stays as it is.")
            else:
                await review(c, m.group(1))
        return
    if "due dates" in low and "etl" in low:
        await c.learnme("project_history", {"project": "data-pipeline", "limit": 5})
        await c.learnme("recall", {"query": "nightly ETL"})
        await c.learnme("dispatch_task", {"project": "todo-app", "title": "Add due dates to todo items", "prompt":
            "In todo-app, add due dates to todo items: an optional due date in the edit form, overdue items sorted "
            "first, and a daily 9:00 reminder for items due today. Keep the change small; run the tests."})
        await c.learnme("dispatch_task", {"project": "data-pipeline", "backend": "codex",
            "title": "Nightly ETL slower since Monday", "prompt":
            "The nightly ETL went from ~25 min to ~70 min starting Monday. Find out why. Report the stage that got "
            "slower, the cause, and a fix with its expected effect."})
        await c.learnme("dispatch_task", {"project": "docs-site", "title": "Fix broken links in docs", "prompt":
            "Find and fix the broken links in docs-site. Run the link checker before and after; report the numbers."})
        await c.say("Opened three windows: **due dates** (todo-app), **nightly ETL** (data-pipeline, on Codex) and "
                    "**broken links** (docs-site). I'll review each one when it stops.")
    elif "on its own" in low and "production database" in low:  # the user's correction
        path = judgment_file()
        await c.edit(path, RULE_AFTER, RULE_AFTER + RULE)
        if etl := STATE["ids"].get("etl"):
            await c.learnme("message_task", {"task_id": etl, "correct": True, "text":
                "Hold: don't create the index. Production database changes need the user's go-ahead. Report what "
                "was applied, if anything, and wait."})
        await c.say("You're right, I shouldn't have waved that through. I stopped the ETL window before the index "
                    "build finished and added a rule to **judgment.md**:\n\n> Production database changes (indexes, "
                    "migrations, backfills) always come to me first, even when the fix looks obvious.\n\n"
                    "From now on I'll bring those to you instead of approving them.")
    elif "approve" in low and "rollback" in low and (alert := STATE["ids"].get("alert")):
        await c.learnme("message_task", {"task_id": alert, "text":
            "The user approved the rollback. Roll billing-api back to v2.13.3, then check the 5xx rate."})
        await c.say("Forwarded your approval to the alert window; I'll check the error rate when it reports back.")
    elif "backfill" in low and (fb := STATE["ids"].get("feedback")):
        await c.learnme("message_task", {"task_id": fb, "text":
            "The user approved the backfill. Run it, then count the exports that are still broken."})
        await c.say("Forwarded: the feedback window will run the backfill and count what's left.")
    else:
        await c.say(FALLBACK)


async def review(c: DemoConnection, tid: str) -> None:
    st = await c.learnme("task_status", {"task_id": tid})
    title = (st.get("title") or "").lower()
    kind = next((k for k in ("broken links", "due dates", "etl", "alertmanager", "feedback") if k in title), None)
    n = STATE["reviews"][tid] = STATE["reviews"].get(tid, 0) + 1
    if kind == "broken links":
        await c.tool("Bash", {"command": "cd docs-site && npm run check-links"}, "✓ 412 links checked, 0 broken")
        await c.learnme("end_task", {"task_id": tid, "reason": "accepted: 7 → 0 broken links; I re-ran the checker myself"})
        await c.say("✅ **Accepted** “Fix broken links in docs”: 7 → 0 broken links, and I re-ran the link checker myself.")
    elif kind == "due dates" and n == 1:
        await c.learnme("message_task", {"task_id": tid, "text": "Good progress. Still missing: tests for overdue "
                                                                  "sorting and the reminder job. Add them and run the suite."})
        await c.say("▶ **Continued** “due dates”: the feature works, but tests for overdue sorting and the reminder "
                    "job are missing. Asked for them.")
    elif kind == "due dates":
        await c.tool("Bash", {"command": "cd todo-app && npm test -- --runInBand | tail -2"},
                     "Tests:       14 passed, 14 total\nTime:        6.81 s")
        await c.learnme("end_task", {"task_id": tid, "reason": "accepted: feature done; 3 new tests; suite 14/14 green (re-run myself)"})
        await c.say("✅ **Accepted** “due dates”: 3 new tests, the suite is 14/14 green, and I re-ran it myself.")
    elif kind == "etl" and n == 1:
        STATE["ids"]["etl"] = tid
        await c.learnme("message_task", {"task_id": tid, "correct": True, "text":
            "Stop tuning Spark memory: the slowdown starts with Monday's schema change. The new events.user_id "
            "column has no index, so the dedup join scans the whole table. Confirm with EXPLAIN and propose the index."})
        await c.say("↩ **Redirected** “nightly ETL”: it spent its turn tuning Spark memory; the slowdown lines up "
                    "with Monday's schema change (an unindexed `user_id` join), so I pointed it there.")
    elif kind == "etl" and n == 2:
        await c.learnme("message_task", {"task_id": tid, "text": "Looks right. Go ahead and create the index."})
        await c.say("▶ The index plan for the ETL is sound; told the window to create it.")
    elif kind == "etl":
        await c.say("The ETL window is holding; the index plan waits for your go-ahead.")
    elif kind == "alertmanager" and n == 1:
        STATE["ids"]["alert"] = tid
        await c.say("🔔 **Needs your approval**: billing-service 5xx alert. v2.14.0 (deployed 14:02) cut the payment "
                    "timeout to 2 s while the provider's p95 is 2.4 s, so ~3% of charges fail. The window proposes "
                    "rolling back to v2.13.3 (one command, reversible). I haven't approved it. Say “approve "
                    "rollback” and I'll forward it.")
    elif kind == "alertmanager":
        await c.tool("Bash", {"command": "curl -s metrics/billing/5xx_ratio?window=5m"}, "0.04%")
        await c.learnme("end_task", {"task_id": tid, "reason": "resolved: rolled back to v2.13.3 on the user's approval; 5xx 3.1% → 0.04%"})
        await c.say("✅ **Resolved** the billing alert: rolled back on your approval; 5xx is down from 3.1% to 0.04% "
                    "(I checked the metric myself). The timeout fix still needs a code change; the window noted it.")
    elif kind == "feedback" and n == 1:
        STATE["ids"]["feedback"] = tid
        rule = next((ln for ln in judgment_file().read_text(encoding="utf-8").splitlines()
                     if "Production database changes" in ln), "")
        await c.tool("Read", {"file_path": str(judgment_file())}, f"…\n{rule}\n…")
        await c.say("🔔 **Needs your decision**: the CSV emoji report is a real bug (exports were written as latin-1). "
                    "The code fix is small and tested in the window. It also proposes re-encoding 1,204 stored "
                    "exports with a backfill on the **production database**. Per your rule from earlier, production "
                    "database changes come to you, so I haven't approved the backfill. Say “run the backfill” to go "
                    "ahead.")
    elif kind == "feedback":
        await c.learnme("end_task", {"task_id": tid, "reason": "accepted: UTF-8 fix with tests; backfill re-encoded 1,204 exports on the user's approval, 0 left"})
        await c.say("✅ **Accepted** the CSV feedback: code fixed with tests, and the backfill you approved left 0 "
                    "broken exports.")
    else:
        await c.say(f"Checked “{st.get('title') or tid}”. It isn't part of the demo's script, so I left it as it is.")


# ------------------------------------------------------------------ task windows

async def task_turn(c: DemoConnection, text: str) -> None:
    t = text.lower()
    if text.startswith("This task has ended"):
        memory = Path(c.spec.cwd) / "CLAUDE.md"
        if memory.is_file() and "--runInBand" not in memory.read_text(encoding="utf-8"):
            await c.edit(memory, "- Keep changes small",
                         "- Run tests with `npm test -- --runInBand`: the reminder tests share one database.\n"
                         "- Keep changes small")
            await c.say("Added one line to the project's CLAUDE.md: run the tests in band (the reminder tests share "
                        "a database).")
        else:
            await c.say("Nothing to add to the project memory: no new conventions or pitfalls came up.")
    elif "add due dates to todo items" in t:
        await c.say("Starting with the data model and the list view.")
        await c.tool("TodoWrite", todos(0, "Adding dueDate to the model"), "")
        await c.read("src/models/todo.ts")
        await c.edit(Path(c.spec.cwd) / "src/models/todo.ts", "  done: boolean;\n}",
                     "  done: boolean;\n  dueDate?: string; // ISO date, UTC\n}")
        await c.tool("Bash", {"command": "npm run migrate -- add-due-date"}, "✓ migration 0007_add_due_date applied", run=1.5)
        await c.tool("TodoWrite", todos(4), "", pause=2.0)
        await c.say("Model, migration, date picker, overdue sorting and the reminder job are in. Checked by hand in "
                    "the dev server: an item due yesterday moves to the top and shows a red badge.")
    elif "tests for overdue" in t:
        await c.tool("TodoWrite", todos(4, "Writing tests for overdue sorting and reminders"), "")
        await c.write("src/__tests__/overdue.test.ts",
                      "test('overdue items sort first', () => {\n"
                      "  const items = [todo('b', '2026-03-09'), todo('a', '2026-03-01')];\n"
                      "  expect(sortForList(items, NOW).map(t => t.id)).toEqual(['a', 'b']);\n});\n")
        await c.tool("Bash", {"command": "npm test -- --runInBand"},
                     "PASS src/__tests__/overdue.test.ts\nPASS src/__tests__/reminders.test.ts\n"
                     "Tests:       14 passed, 14 total", pause=1.0, run=8.0)
        await c.tool("TodoWrite", todos(5), "", pause=0.4)
        await c.say("Added 3 tests (overdue sorting, the 9:00 reminder, and an item without a due date). The suite "
                    "is 14/14 green.")
    elif "broken links" in t:
        await c.tool("Bash", {"command": "npm run check-links"}, "✗ 7 broken links in 5 files")
        await c.say("7 broken: 3 point to pages renamed last week, 4 are dead external URLs. Fixing them.")
        await c.edit(Path(c.spec.cwd) / "docs/index.md", "(./setup.md)", "(./getting-started.md)")
        await c.tool("Bash", {"command": "npm run check-links"}, "✓ 412 links checked, 0 broken", run=1.5)
        await c.say("Done: 7 → 0 broken links.")
    elif "nightly etl went" in t:
        await c.say("Profiling the last four runs by stage.")
        await c.tool("Bash", {"command": "python -m etl.profile --since 2026-03-02"},
                     "stage          Fri    Mon    Tue    Wed\nextract        312    318    309    315\n"
                     "dedup_join     405  2,960  2,941  2,987\nload           205    211    208    210", run=1.6)
        await c.say("dedup_join is 7× slower. Trying more shuffle partitions and executor memory.")
        await c.tool("Bash", {"command": "spark-submit --conf spark.sql.shuffle.partitions=800 --executor-memory 8g "
                                         "etl/jobs.py --stage dedup_join --dry-run"}, "dedup_join  2,944s", run=2.5)
        await c.say("More memory didn't help (2,944 s). Trying a broadcast join next.")
    elif "schema change" in t:
        await c.tool("Bash", {"command": "psql analytics -c \"EXPLAIN ANALYZE SELECT … FROM events e JOIN users u "
                                         "USING (user_id) WHERE e.day = '2026-03-04'\""},
                     "Hash Join  (actual time=2871.4..2950.2)\n  ->  Seq Scan on events e  (rows=48,201,113)", run=1.6)
        await c.say("**Confirmed.** The join does a sequential scan over 48M rows because `events.user_id` (added "
                    "Monday) has no index.\n\n**Fix:**\n```sql\nCREATE INDEX CONCURRENTLY idx_events_user_id ON "
                    "events (user_id);\n```\nExpected: dedup_join back to ~400 s, total run ~25 min. The build takes "
                    "~6 min and doesn't lock writes.")
    elif "go ahead and create the index" in t:
        await c.say("Creating the index on the production database.")
        await c.tool("Bash", {"command": "psql analytics -c 'CREATE INDEX CONCURRENTLY idx_events_user_id ON events (user_id)'"},
                     None)
    elif t.startswith("hold:"):
        await c.tool("Bash", {"command": "psql analytics -c \"SELECT count(*) FROM pg_indexes WHERE indexname = "
                                         "'idx_events_user_id'\""}, " count\n-------\n     0")
        await c.say("Held. Nothing was applied: the build was cancelled before it finished, and no "
                    "`idx_events_user_id` exists. The index plan is ready whenever you give the go-ahead.")
    elif "source: alertmanager" in t:
        await c.say("Reading the error logs and the deploy history, read-only.")
        await c.tool("Bash", {"command": "kubectl -n billing logs deploy/billing-api --since=30m | grep -c ' 503 '"}, "1284")
        await c.tool("Bash", {"command": "kubectl -n billing rollout history deploy/billing-api | tail -3"},
                     "REVISION  CHANGE-CAUSE\n41        v2.13.3\n42        v2.14.0 (deployed 14:02)")
        await c.read("src/payments/client.ts")
        await c.tool("Bash", {"command": "git diff v2.13.3 v2.14.0 -- src/payments/client.ts"},
                     "-export const client = { timeout: 10_000 };\n+export const client = { timeout: 2_000 };")
        await c.say("**Cause (high confidence):** v2.14.0 cut the payment provider timeout from 10 s to 2 s. The "
                    "provider's p95 latency today is 2.4 s, so about 3% of charges time out and return 503. Errors "
                    "started at 14:03, a minute after the deploy.\n\n**Plan:** roll back to v2.13.3.\n```\nkubectl -n "
                    "billing rollout undo deploy/billing-api --to-revision=41\n```\nExpected: 5xx under 0.1% within 5 "
                    "minutes. Reversible with `--to-revision=42`. Follow-up: restore the 10 s timeout in code.\n\n"
                    "NEEDS APPROVAL: roll back billing-api in production to v2.13.3 to stop failed charges.")
    elif "approved the rollback" in t:
        await c.tool("Bash", {"command": "kubectl -n billing rollout undo deploy/billing-api --to-revision=41"},
                     "deployment.apps/billing-api rolled back", run=2.0)
        await c.tool("Bash", {"command": "kubectl -n billing rollout status deploy/billing-api"},
                     "deployment \"billing-api\" successfully rolled out", run=2.5)
        await c.say("Rolled back to v2.13.3. 5xx over the last 5 minutes: 0.04%. The 2 s timeout is still in the "
                    "code on main; that needs a fix before the next deploy.")
    elif "source: feedback" in t:
        await c.say("Reproducing the report with a list that contains emoji.")
        await c.tool("Bash", {"command": "node scripts/export-sample.js --title '🎉 party' | head -2"}, "id,title\n1,? party")
        await c.read("src/export/csv.ts")
        await c.edit(Path(c.spec.cwd) / "src/export/csv.ts", "rows.join('\\n'), 'latin1')",
                     "'\\ufeff' + rows.join('\\n'), 'utf8')")
        await c.tool("Bash", {"command": "npm test -- csv"}, "Tests:       4 passed, 4 total", run=2.0)
        await c.say("**Real bug.** Exports were written as latin-1, so emoji became `?`. Fixed in `src/export/csv.ts` "
                    "(UTF-8 with a BOM, so Excel reads it); tests pass.\n\n1,204 exports already stored are broken. "
                    "Re-encoding them needs a backfill on the production database:\n```sql\nUPDATE exports SET body = "
                    "convert_from(convert_to(body, 'LATIN1'), 'UTF8') WHERE created_at < '2026-03-05';\n```\n\n"
                    "NEEDS APPROVAL: run the backfill on the production database (1,204 rows).")
    elif "approved the backfill" in t:
        await c.tool("Bash", {"command": "psql app -f backfill_exports.sql"}, "UPDATE 1204", run=2.5)
        await c.tool("Bash", {"command": "psql app -c \"SELECT count(*) FROM exports WHERE body LIKE '%?%' AND "
                                         "created_at < '2026-03-05'\""}, " count\n-------\n     0")
        await c.say("Backfill done: 1,204 rows re-encoded, 0 broken exports left.")
    else:
        await c.say(FALLBACK)
