"""Demo mode end to end: the director plays the whole story on the scripted backend, sped up, and the
real console reacts (dispatch, review, the alert intake, the watcher, the rule written to judgment.md)."""

import asyncio

from server import backends
from server import console as console_mod
from server.config import CONFIG
from server.demo import backend as demo_backend
from server.demo.fixtures import prepare


async def until(cond, timeout=30.0):
    end = asyncio.get_running_loop().time() + timeout
    while not cond():
        if asyncio.get_running_loop().time() > end:
            raise TimeoutError
        await asyncio.sleep(0.02)


def test_demo_story(monkeypatch):
    config = prepare(CONFIG.data_dir / "demo", CONFIG.project_roots[0])
    monkeypatch.setenv("LEARNME_CONFIG", str(config))
    monkeypatch.setattr(CONFIG, "data_dir", CONFIG.data_dir / "demo")  # where the director finds the feed
    monkeypatch.setattr(CONFIG, "demo", True)
    monkeypatch.setattr(demo_backend, "PACE", 0.02)
    monkeypatch.setattr(console_mod, "REVIEW_BATCH_S", 0.05)
    backends._build.cache_clear()

    from server.console import Console
    from server.demo.story import play
    from server.events import EventRouter
    from server.session import PROFILE_DIR

    async def main():
        c = Console()
        events = EventRouter(c)
        assert [w.name for w in events.watchers] == ["feedback"] and events.enabled
        supervisor = asyncio.create_task(c.supervisor.run())
        c.hub.subscribe()  # a browser is watching: the story starts
        await asyncio.wait_for(play(c, events), 90)

        tasks = {t["title"]: t for t in c.tasks()}
        assert all(t["backend"] in ("claude", "codex") for t in tasks.values())
        assert tasks["Fix broken links in docs"]["status"] == "done"
        assert tasks["Add due dates to todo items"]["status"] == "done"
        etl = tasks["Nightly ETL slower since Monday"]
        assert etl["backend"] == "codex" and etl["status"] != "done"
        alert = next(t for title, t in tasks.items() if "alertmanager" in title)
        feedback = [t for title, t in tasks.items() if "feedback ·" in title]
        assert len(feedback) == 1 and "CSV export" in feedback[0]["title"]  # the item already in the feed is skipped

        # Supervision: accepted, continued, redirected (a correction that interrupted nothing), then held.
        actions = {(d["task_id"], d["actor"], d["action"]) for d in c.store.decisions(500)}
        todo = tasks["Add due dates to todo items"]["id"]
        assert (todo, "auto", "continue") in actions and (todo, "auto", "end") in actions
        assert any(d["task_id"] == etl["id"] and d["action"] == "correct" for d in c.store.decisions(500))

        # Learning: the user's correction became a rule in judgment.md, credited to the manager.
        judgment = (PROFILE_DIR / "judgment.md").read_text()
        assert "Production database changes (indexes, migrations, backfills) always come to me first" in judgment
        changes = c.memory.history(50)
        assert any(ch["actor"] == "manager" and ch["name"] == "judgment.md" for ch in changes)
        assert any(ch["actor"] == "task" and ch["name"] == "CLAUDE.md" for ch in changes)

        # The alert and the feedback both stopped for a human; the manager approved neither.
        texts = [e["text"] for e in c.store.events("main") if e["type"] == "text"]
        assert any("Needs your approval" in t for t in texts) and any("Per your rule" in t for t in texts)
        assert c.session(alert["id"]).status == "idle"

        # The visitor can give the approval, and the manager forwards it and closes the window.
        m = c.manager()
        await m.send("approve rollback")
        await until(lambda: c.session(alert["id"]).status == "done")
        assert "rolled back" in c.session(alert["id"]).task["end_reason"]

        supervisor.cancel()
        await c.shutdown()

    try:
        asyncio.run(asyncio.wait_for(main(), 120))
    finally:
        backends._build.cache_clear()
