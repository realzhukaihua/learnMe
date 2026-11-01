"""Autonomous on-call: external events (alerts, webhooks, CI failures) open windows that handle them.

    POST /api/events
    Authorization: Bearer <events token>
    {"source": "alertmanager", "title": "API 5xx rate above 2%", "body": "...full alert...",
     "key": "api-5xx", "project": "my-service", "severity": "critical"}

Only `title` or `body` is required. A new `key` (default: source + title) opens a window that runs
the on-call policy (server/prompts: EVENT) in front of the event; the same key while that window
is still open is appended to it, so a flapping alert doesn't open twenty windows. The manager
supervises these windows like any other task: it reviews every stop, and a window that needs a
human for a risky step stops with a "NEEDS APPROVAL:" line, which the manager brings to the user.
Tool calls that need permission (per the window's permission mode) wait in the window for the user.

The endpoint is off unless a token is configured ([events] token in learnme.toml, or
LEARNME_EVENTS_TOKEN). At most [events] max_per_day events open or extend windows per day.
"""

import hmac
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import backends
from .i18n import tr
from .prompts import P

if TYPE_CHECKING:
    from .console import Console

log = logging.getLogger("learnme.events")

STATE_KV = "events"
DEFAULT_MAX_PER_DAY = 50
BODY_CLIP = 20000


def load_settings() -> dict:
    from .config import _toml
    cfg = dict(_toml().get("events") or {})
    if token := os.environ.get("LEARNME_EVENTS_TOKEN"):
        cfg["token"] = token
    cfg.setdefault("max_per_day", DEFAULT_MAX_PER_DAY)
    return cfg


class Event(BaseModel):
    source: str = Field(default="webhook", max_length=100)
    title: str = Field(default="", max_length=500)
    body: str = Field(default="", max_length=200000)
    key: str | None = Field(default=None, max_length=300)
    project: str | None = None
    severity: str | None = Field(default=None, max_length=50)


class EventRouter:
    def __init__(self, console: "Console"):
        self.console = console
        self.cfg = load_settings()

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get("token"))

    def _state(self) -> dict:
        return self.console.store.get_kv(STATE_KV, {"open": {}, "daily": {}})

    def _count_today(self, state: dict) -> bool:
        day = time.strftime("%Y-%m-%d")
        daily = state.setdefault("daily", {})
        if daily.get("day") != day:
            daily.update(day=day, count=0, noted=False)
        if daily["count"] >= int(self.cfg.get("max_per_day") or DEFAULT_MAX_PER_DAY):
            if not daily.get("noted"):
                daily["noted"] = True
                self.console.manager().emit("notice", text=tr("🚨 Today's external event limit ({n}) is used up; further events are dropped until tomorrow", n=self.cfg.get("max_per_day")))
            return False
        daily["count"] += 1
        return True

    def runbook(self) -> str:
        path = self.cfg.get("runbook")
        if not path:
            return ""
        try:
            return Path(path).expanduser().read_text(encoding="utf-8").strip()
        except OSError:
            log.warning("events runbook %s not readable", path)
            return ""

    def describe(self, ev: Event) -> str:
        lines = [f"source: {ev.source}"]
        if ev.severity:
            lines.append(f"severity: {ev.severity}")
        if ev.title:
            lines.append(f"title: {ev.title}")
        lines.append(f"received: {time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        body = ev.body[:BODY_CLIP] + ("\n…" if len(ev.body) > BODY_CLIP else "")
        quoted = "\n".join("> " + line for line in body.splitlines()) if body else ""
        return "\n".join(lines) + ("\n\n" + quoted if quoted else "")

    async def handle(self, ev: Event) -> dict:
        state = self._state()
        if not self._count_today(state):
            self.console.store.set_kv(STATE_KV, state)
            return {"ok": False, "dropped": "daily limit"}
        key = f"{ev.source}:{ev.key or ev.title or ev.body[:80]}"
        open_windows: dict = state.setdefault("open", {})
        s = self.console.session(open_windows.get(key, "")) if open_windows.get(key) else None
        if s is not None and s.status != "done":
            self.console.store.set_kv(STATE_KV, state)
            self.console.spawn(s.send(tr("[Event update]") + "\n" + self.describe(ev), actor="event"))
            return {"ok": True, "task_id": s.id, "appended": True}
        if ev.project:
            cwd, _ = self.console.resolve_project(ev.project)
        else:
            cwd = None
        cwd = cwd or (self.console.resolve_project(self.cfg["project"])[0] if self.cfg.get("project") else None)
        cwd = cwd or str(self.console.workspace())
        runbook = self.runbook()
        prompt = P.EVENT.format(runbook=f"\n{P.EVENT_RUNBOOK_HEAD}\n{runbook}\n" if runbook else "") + self.describe(ev)
        backend = self.cfg.get("backend") or None
        if backend and backends.get(backend).problems():
            backend = None
        s = self.console.create_task(prompt, cwd, title=f"🚨 {ev.source} · {(ev.title or ev.body)[:60]}",
                                     origin="event", backend=backend,
                                     permission_mode=self.cfg.get("permission_mode") or None, actor="event")
        for k in [k for k, tid in open_windows.items()
                  if (t := self.console.store.get_task(tid)) is None or t["status"] == "done"]:
            open_windows.pop(k, None)  # forget windows that have ended
        open_windows[key] = s.id
        self.console.store.set_kv(STATE_KV, state)
        self.console.manager().emit("notice", text=tr("🚨 Event from {source}: {title} → window {id}", source=ev.source,
                                                      title=(ev.title or ev.body)[:80], id=s.id))
        return {"ok": True, "task_id": s.id, "appended": False}

    def router(self) -> APIRouter:
        router = APIRouter()

        @router.post("/api/events")
        async def post_event(ev: Event, request: Request):
            if not self.enabled:
                raise HTTPException(404, "events are not enabled")
            token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
            if not token or not hmac.compare_digest(token, str(self.cfg["token"])):
                raise HTTPException(401, "bad token")
            if not (ev.title or ev.body):
                raise HTTPException(400, "title or body is required")
            return await self.handle(ev)

        return router
