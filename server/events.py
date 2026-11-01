"""learnMe as a service: outside input (messages, alerts, user feedback) opens windows that handle it.

Two ways in, both ending in EventRouter.handle():

Push: anything that can send an HTTP request (an alerting tool, a CI job, a chat-bot relay, a form)

    POST /api/events
    Authorization: Bearer <events token>
    {"source": "alertmanager", "title": "API 5xx rate above 2%", "body": "...full alert...",
     "key": "api-5xx", "project": "my-service", "severity": "critical"}

Pull: watchers ([[events.watchers]] in learnme.toml) run a command or fetch a URL on a schedule and
turn each new item into an event, e.g. `gh issue list --label feedback --json number,title,body`
to follow user feedback. Items are deduplicated by their id; at the first poll the items already
there are only remembered (first_run = "skip"), so starting a watcher doesn't flood you with windows.

A new `key` (default: source + title) opens a window that runs the input policy (server/prompts:
EVENT) in front of the item; the same key while that window is still open is appended to it, so a
flapping alert or a feedback thread stays in one window. Each source can have its own runbook,
project, backend and permission mode ([events.sources.<name>], or on the watcher). The manager
supervises these windows like any other task: it reviews every stop, and a window that needs a human
for a risky step stops with a "NEEDS APPROVAL:" line, which the manager brings to the user.

The push endpoint is off unless a token is set ([events] token, or LEARNME_EVENTS_TOKEN); watchers
need none. At most [events] max_per_day items open or extend windows per day, pushed and pulled together.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import backends
from .i18n import tr
from .prompts import P

if TYPE_CHECKING:
    from .console import Console

log = logging.getLogger("learnme.events")

STATE_KV = "events"
WATCH_KV = "watcher:"  # + name: {"seen": {id: hash}, "baselined": bool}
DEFAULT_MAX_PER_DAY = 50
DEFAULT_EVERY_S = 600
MIN_EVERY_S = 30
FETCH_TIMEOUT_S = 120
BODY_CLIP = 20000
SEEN_KEEP = 5000
URGENT = {"critical", "high", "error", "fatal", "page", "p0", "p1", "sev1", "sev2"}
PER_SOURCE = ("project", "runbook", "backend", "permission_mode")


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


def icon(ev: Event) -> str:
    return "🚨" if (ev.severity or "").lower() in URGENT else "📥"


def parse_items(raw: str) -> list[Any]:
    """A watcher's output: a JSON array, an object holding one array (e.g. {"items": [...]}), or JSON lines."""
    raw = raw.strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        return [json.loads(line) for line in raw.splitlines() if line.strip()]
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        lists = [v for v in data.values() if isinstance(v, list)]
        return lists[0] if len(lists) == 1 else [data]
    return [data]


class EventRouter:
    def __init__(self, console: "Console"):
        self.console = console
        self.cfg = load_settings()
        self.watchers = [Watcher(self, dict(w)) for w in self.cfg.get("watchers") or [] if isinstance(w, dict)]

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get("token"))

    def source_cfg(self, source: str, extra: dict | None = None) -> dict:
        """Per-source settings: the watcher's own, then [events.sources.<name>], then [events]."""
        out = {k: self.cfg.get(k) for k in PER_SOURCE}
        out.update({k: v for k, v in ((self.cfg.get("sources") or {}).get(source) or {}).items() if k in PER_SOURCE})
        out.update({k: v for k, v in (extra or {}).items() if k in PER_SOURCE and v})
        return out

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
                self.console.manager().emit("notice", text=tr("📥 Today's limit of outside items ({n}) is used up; more are dropped until tomorrow", n=self.cfg.get("max_per_day")))
            return False
        daily["count"] += 1
        return True

    @staticmethod
    def runbook(path: str | None) -> str:
        if not path:
            return ""
        try:
            return Path(path).expanduser().read_text(encoding="utf-8").strip()
        except OSError:
            log.warning("runbook %s not readable", path)
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

    async def handle(self, ev: Event, extra: dict | None = None) -> dict:
        state = self._state()
        if not self._count_today(state):
            self.console.store.set_kv(STATE_KV, state)
            return {"ok": False, "dropped": "daily limit"}
        key = f"{ev.source}:{ev.key or ev.title or ev.body[:80]}"
        open_windows: dict = state.setdefault("open", {})
        s = self.console.session(open_windows.get(key, "")) if open_windows.get(key) else None
        if s is not None and s.status != "done":
            self.console.store.set_kv(STATE_KV, state)
            self.console.spawn(s.send(tr("[Update]") + "\n" + self.describe(ev), actor="event"))
            return {"ok": True, "task_id": s.id, "appended": True}
        sc = self.source_cfg(ev.source, extra)
        cwd = None
        for ref in (ev.project, sc.get("project")):
            if ref and not cwd:
                cwd = self.console.resolve_project(ref)[0]
        cwd = cwd or str(self.console.workspace())
        runbook = self.runbook(sc.get("runbook"))
        prompt = P.EVENT.format(runbook=f"\n{P.EVENT_RUNBOOK_HEAD}\n{runbook}\n" if runbook else "") + self.describe(ev)
        backend = sc.get("backend") or None
        if backend and backends.get(backend).problems():
            backend = None
        mark = icon(ev)
        s = self.console.create_task(prompt, cwd, title=f"{mark} {ev.source} · {(ev.title or ev.body)[:60]}",
                                     origin="event", backend=backend,
                                     permission_mode=sc.get("permission_mode") or None, actor="event")
        for k in [k for k, tid in open_windows.items()
                  if (t := self.console.store.get_task(tid)) is None or t["status"] == "done"]:
            open_windows.pop(k, None)  # forget windows that have ended
        open_windows[key] = s.id
        self.console.store.set_kv(STATE_KV, state)
        self.console.manager().emit("notice", text=tr("{icon} From {source}: {title} → window {id}", icon=mark,
                                                      source=ev.source, title=(ev.title or ev.body)[:80], id=s.id))
        return {"ok": True, "task_id": s.id, "appended": False}

    async def run_watchers(self) -> None:
        if self.watchers:
            await asyncio.gather(*(w.run() for w in self.watchers))

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

        @router.get("/api/watchers")
        async def list_watchers():
            return [w.public() for w in self.watchers]

        return router


class Watcher:
    """Polls one source (a command's stdout, or a URL) and hands new items to the router.

    [[events.watchers]]
    name = "feedback"                  # becomes the event source
    command = "gh issue list --label feedback --state open --json number,title,body,url"
    # url = "https://example.com/api/feedback"; headers = { Authorization = "Bearer ${FEEDBACK_TOKEN}" }
    every = 600                        # seconds between polls
    id = "number"                      # the field that identifies an item (default "id")
    title = "title"                    # the field shown as the window title (default "title")
    batch = false                      # true: all new items of one poll go to one window
    track_changes = false              # true: an item whose content changed counts as an update
    first_run = "skip"                 # "skip": items present at the first poll are only remembered
    project / runbook / backend / permission_mode, as for [events.sources.<name>]
    """

    def __init__(self, router: EventRouter, cfg: dict):
        self.router = router
        self.cfg = cfg
        self.name = str(cfg.get("name") or cfg.get("source") or "watcher")
        self.every = max(MIN_EVERY_S, int(cfg.get("every") or DEFAULT_EVERY_S))
        self.last_poll: float | None = None
        self.last_error: str | None = None

    def public(self) -> dict:
        return {"name": self.name, "every": self.every, "kind": "command" if self.cfg.get("command") else "url",
                "last_poll": self.last_poll, "last_error": self.last_error}

    @property
    def store(self):
        return self.router.console.store

    async def fetch(self) -> list[Any]:
        if cmd := self.cfg.get("command"):
            cwd = None
            if self.cfg.get("project"):
                cwd = self.router.console.resolve_project(self.cfg["project"])[0]
            proc = await asyncio.create_subprocess_shell(
                cmd, cwd=cwd or str(self.router.console.workspace()),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                out, err = await asyncio.wait_for(proc.communicate(), FETCH_TIMEOUT_S)
            except TimeoutError:
                proc.kill()
                raise RuntimeError(f"timed out after {FETCH_TIMEOUT_S}s") from None
            if proc.returncode:
                raise RuntimeError(f"exit {proc.returncode}: {err.decode(errors='replace')[-300:]}")
            return parse_items(out.decode(errors="replace"))
        if url := self.cfg.get("url"):
            headers = {k: os.path.expandvars(str(v)) for k, v in (self.cfg.get("headers") or {}).items()}

            def get() -> str:
                req = urllib.request.Request(os.path.expandvars(url), headers=headers)
                with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_S) as r:  # noqa: S310 - the user's own URL
                    return r.read().decode(errors="replace")
            return parse_items(await asyncio.to_thread(get))
        raise RuntimeError("a watcher needs `command` or `url`")

    def item_event(self, item: Any) -> tuple[str, Event]:
        if not isinstance(item, dict):
            item = {"text": str(item)}
        id_field, title_field = self.cfg.get("id") or "id", self.cfg.get("title") or "title"
        body = json.dumps(item, ensure_ascii=False, indent=1)
        item_id = str(item.get(id_field) if item.get(id_field) is not None else hashlib.sha1(body.encode()).hexdigest()[:16])
        title = str(item.get(title_field) or item.get("text") or body)[:200].splitlines()[0]
        return item_id, Event(source=self.name, title=title, body=body, key=item_id,
                              severity=str(item.get("severity") or "") or None)

    async def poll(self) -> int:
        """One poll; returns how many items were handed on."""
        items = await self.fetch()
        state = self.store.get_kv(WATCH_KV + self.name, {"seen": {}, "baselined": False})
        seen: dict = state["seen"]
        fresh: list[Event] = []
        for item in items:
            item_id, ev = self.item_event(item)
            digest = hashlib.sha1(ev.body.encode()).hexdigest()[:16]
            known = seen.get(item_id)
            if known is None or (self.cfg.get("track_changes") and known != digest):
                fresh.append(ev)
            seen.pop(item_id, None)
            seen[item_id] = digest
        while len(seen) > SEEN_KEEP:
            seen.pop(next(iter(seen)))
        first = not state["baselined"]
        state["baselined"] = True
        self.store.set_kv(WATCH_KV + self.name, state)
        if first and (self.cfg.get("first_run") or "skip") == "skip":
            log.info("watcher %s: baseline of %d items", self.name, len(items))
            return 0
        if not fresh:
            return 0
        extra = {k: self.cfg.get(k) for k in PER_SOURCE}
        if self.cfg.get("batch"):
            body = "\n\n".join(ev.body for ev in fresh)
            await self.router.handle(Event(source=self.name, title=tr("{n} new items", n=len(fresh)), body=body,
                                           key="batch"), extra)
        else:
            for ev in fresh:
                await self.router.handle(ev, extra)
        return len(fresh)

    async def run(self) -> None:
        await asyncio.sleep(5)  # let the console come up first
        while True:
            try:
                n = await self.poll()
                if self.last_error:
                    self.router.console.manager().emit("notice", text=tr("📥 Watcher {name} works again", name=self.name))
                self.last_error = None
                if n:
                    log.info("watcher %s: %d new", self.name, n)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - keep polling; say so once
                log.warning("watcher %s failed: %s", self.name, e)
                if self.last_error is None:
                    self.router.console.manager().emit("notice", text=tr("📥 Watcher {name} failed: {err}", name=self.name, err=str(e)[:300]))
                self.last_error = str(e)[:300]
            self.last_poll = time.time()
            await asyncio.sleep(self.every)
