"""The demo's director: plays the user's part on a timeline, once a browser is watching.

It only does what a user or an outside system would: types in the main window, sends an alert
through the events router, and adds an item to the feed the feedback watcher reads. Everything
that follows (dispatch, review, escalation, the new rule) is learnMe reacting on its own.
"""

import asyncio
import json
import logging
import time

from ..config import CONFIG
from ..events import Event
from ..manager import MANAGER_ID
from .backend import nap, reset
from .fixtures import FEED, FEED_START

log = logging.getLogger("learnme.demo")

ASK = ("Add due dates to the todo app (with a daily reminder), and find out why the nightly ETL got slower since "
       "Monday. Also fix the broken links in the docs.")
CORRECTION = ("Wait, don't let a task change the production database on its own. Indexes, migrations, backfills: "
              "ask me first.")
ALERT = Event(source="alertmanager", title="billing-service 5xx above 2%", severity="critical", key="billing-5xx",
              body="HighErrorRate firing: 5xx ratio 3.1% (threshold 2%) for 6m\nservice=billing-api env=production")
FEEDBACK = {"id": "fb-102", "title": "CSV export turns emoji into ?",
            "body": "Exported my list and every 🎉 became '?'. Opened it in Excel and in Numbers, same thing."}
TIMEOUT_S = 240


def said(console, *words: str) -> bool:
    """Has the manager said something with all these words (case-insensitive) since the demo began?"""
    for e in console.store.events(MANAGER_ID, 0):
        if e["type"] == "text" and all(w.lower() in (e.get("text") or "").lower() for w in words):
            return True
    return False


async def until(cond, what: str) -> None:
    from . import backend  # its PACE, read now: tests speed the story up
    end = time.monotonic() + TIMEOUT_S
    while not cond():
        if time.monotonic() > end:
            raise TimeoutError(f"demo: waited too long for {what}")
        await asyncio.sleep(0.3 * min(backend.PACE, 1))


async def play(console, events) -> None:
    reset()
    while not console.hub.queues:  # wait for someone to watch
        await asyncio.sleep(0.3)
    feedback = next(w for w in events.watchers if w.name == "feedback")
    await feedback.poll()  # the items already in the feed are old news (first_run = "skip")
    m = console.manager()
    m.emit("notice", text="🎬 Demo: the agents' replies are scripted and no model is called. Your messages below are "
                          "typed by the demo; everything learnMe does in response is the real code.")
    await nap(3)

    # 1. One request in the main window; the manager splits it into three windows.
    await m.send(ASK, actor="user")
    log.info("demo: asked for three tasks")

    # 2. Once the first task is accepted, an alert comes in from outside.
    await until(lambda: said(console, "Accepted", "broken links"), "the docs task to be accepted")
    await nap(2)
    await events.handle(ALERT)
    log.info("demo: alert sent")

    # 3. The manager approves an index build on the production database; the user objects.
    await until(lambda: said(console, "told the window to create it"), "the manager to approve the index")
    await nap(4)
    await m.send(CORRECTION, actor="user")
    log.info("demo: correction sent")

    # 4. New user feedback; the fix needs another production change, and the new rule applies.
    await until(lambda: said(console, "From now on I'll bring those to you"), "the rule to be written")
    await nap(3)
    (CONFIG.data_dir / FEED).write_text(json.dumps(FEED_START + [FEEDBACK], ensure_ascii=False, indent=1), encoding="utf-8")
    await feedback.poll()
    log.info("demo: feedback item added")

    await until(lambda: said(console, "per your rule") and said(console, "Accepted", "due dates"), "the last escalation")
    await nap(2)
    m.emit("notice", text="🎬 That's the story. The manager is waiting on two approvals: type “approve rollback” or "
                          "“run the backfill” in the main window to give them. Open 🧠 Memory → Changes to see the "
                          "rule it wrote. Run ./run.sh demo again to replay.")
    log.info("demo: done")
