"""HTTP + WebSocket front door for the learnMe console."""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import backends
from .config import BACKENDS, CONFIG, ROOT
from .console import Console
from .events import EventRouter
from .i18n import tr
from .manager import MANAGER_ID
from .mcp_http import build_router as mcp_router
from .session import ACTIVE, TaskSession
from .slack_bridge import SlackBridge
from .uploads import router as uploads_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("learnme")

WEB = ROOT / "web"
NO_CACHE = {"Cache-Control": "no-cache"}

console = Console()
console.slack = SlackBridge.load(console)  # None unless data/slack.env exists and SLACK_MODE isn't off
events = EventRouter(console)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    for b in backends.overview():
        if b["problems"] and (b["default"] or b["manager"]):
            log.warning("backend %s: %s", b["name"], "; ".join(b["problems"]))
    console.resume(console.recover())
    loops = [asyncio.create_task(console.reap_idle()), asyncio.create_task(console.supervisor.run()),
             asyncio.create_task(console.watch_memory()), asyncio.create_task(events.run_watchers())]
    if console.slack:
        console.spawn(console.slack.start())
    yield
    for t in loops:
        t.cancel()
    if console.slack:
        await console.slack.stop()
    await console.shutdown()


app = FastAPI(title="learnMe", lifespan=lifespan)
app.include_router(uploads_router)
app.include_router(mcp_router(console))
app.include_router(events.router())


class NewTask(BaseModel):
    prompt: str = Field(min_length=1)
    cwd: str
    title: str | None = None
    model: str | None = None
    permission_mode: str | None = None
    backend: Literal[BACKENDS] | None = None  # type: ignore[valid-type]


class Message(BaseModel):
    text: str = Field(min_length=1)
    correct: bool = False


class EndTask(BaseModel):
    reason: str | None = None


class Compact(BaseModel):
    focus: str | None = None


class Settings(BaseModel):
    supervise: bool | None = None
    wrapup: bool | None = None


class ManagerBackend(BaseModel):
    backend: Literal[BACKENDS]  # type: ignore[valid-type]


class RestoreSession(BaseModel):
    path: str


class MemoryScope(BaseModel):
    scope: str


class MemoryEdit(BaseModel):
    path: str
    content: str


class PermissionReply(BaseModel):
    action: Literal["allow", "allow_always", "deny", "answer"]
    message: str | None = None
    answers: dict[str, str] | None = None


def session_or_404(task_id: str) -> TaskSession:
    s = console.session(task_id)
    if not s:
        raise HTTPException(404, tr("No such task"))
    return s


@app.get("/lang.js")
async def lang():
    """The UI language, before app.js runs."""
    return Response(f"window.LEARNME_LANG = {json.dumps(CONFIG.language)};", media_type="application/javascript",
                    headers=NO_CACHE)


@app.get("/api/state")
async def state():
    return {"manager": console.manager().public(), "tasks": console.tasks(), "settings": console.settings,
            "lang": CONFIG.language, "backends": backends.overview(),
            "slack": bool(console.slack), "events": events.enabled, "watchers": [w.name for w in events.watchers]}


@app.get("/api/backends")
async def list_backends():
    return backends.overview()


@app.post("/api/settings")
async def settings(body: Settings):
    return console.update_settings(**body.model_dump(exclude_none=True))


@app.post("/api/manager/backend")
async def manager_backend(body: ManagerBackend):
    if bad := backends.get(body.backend).problems():
        raise HTTPException(400, "; ".join(bad))
    if console.manager().status in ACTIVE:
        raise HTTPException(409, tr("The manager is busy; wait for this turn to finish"))
    console.set_manager_backend(body.backend)
    m = console.manager()
    m.emit("notice", text=tr("—— The manager now runs on {name}; the conversation starts fresh ——", name=body.backend))
    return m.public()


@app.get("/api/projects")
async def projects():
    return console.projects()


@app.post("/api/tasks")
async def create_task(body: NewTask):
    cwd = Path(body.cwd).expanduser()
    if not cwd.is_dir():
        raise HTTPException(400, tr("No such folder: {path}", path=cwd))
    if body.backend and (bad := backends.get(body.backend).problems()):
        raise HTTPException(400, "; ".join(bad))
    s = console.create_task(body.prompt, str(cwd), title=body.title, model=body.model,
                            permission_mode=body.permission_mode, backend=body.backend)
    return s.public()


@app.get("/api/tasks/{task_id}/events")
async def task_events(task_id: str, after: int = 0):
    session_or_404(task_id)
    # After "clear conversation" the screen starts at the clear event; older events stay in the DB for search/recall.
    cleared = console.store.cleared_seq(task_id)
    return console.store.events(task_id, max(after, cleared - 1) if cleared else after)


@app.post("/api/tasks/{task_id}/messages")
async def message(task_id: str, body: Message):
    s = session_or_404(task_id)
    console.spawn(s.send(body.text, body.correct))
    return {"ok": True}


@app.post("/api/tasks/{task_id}/interrupt")
async def interrupt(task_id: str):
    s = session_or_404(task_id)
    console.spawn(s.interrupt())
    return {"ok": True}


@app.post("/api/tasks/{task_id}/end")
async def end(task_id: str, body: EndTask):
    if task_id == MANAGER_ID:
        raise HTTPException(400, tr("The main window can't be ended; clear its conversation instead"))
    s = session_or_404(task_id)
    console.spawn(s.end(body.reason))
    return {"ok": True}


@app.post("/api/tasks/{task_id}/compact")
async def compact(task_id: str, body: Compact):
    s = session_or_404(task_id)
    if s.status in ACTIVE:
        raise HTTPException(409, tr("This turn is still running; compact after it stops"))
    if s.status == "done":
        raise HTTPException(400, tr("The task has ended; send a message to reopen it before compacting"))
    if not (s.task["session_id"] or s.task.get("fork_from")):
        raise HTTPException(400, tr("No conversation yet; nothing to compact"))
    if not s.backend.capabilities.compact:
        raise HTTPException(400, tr("This backend can't compact on request"))
    console.spawn(s.compact((body.focus or "").strip() or None))
    return {"ok": True}


@app.post("/api/manager/reset")
async def reset_manager():
    console.spawn(console.manager().new_conversation())
    return {"ok": True}


@app.post("/api/tasks/{task_id}/permissions/{req_id}")
async def permission(task_id: str, req_id: str, body: PermissionReply):
    s = session_or_404(task_id)
    if not s.resolve_permission(req_id, body.action, body.message, body.answers):
        raise HTTPException(409, tr("This request was already handled or has expired"))
    return {"ok": True}


@app.get("/api/history/windows")
async def history_windows(q: str = ""):
    return console.history_windows(q)


@app.get("/api/history/sessions")
async def history_sessions(q: str = ""):
    return await console.history_sessions(q)


@app.post("/api/history/restore")
async def history_restore(body: RestoreSession):
    try:
        return console.restore_session(body.path).public()
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/decisions")
async def decisions(limit: int = 200, task_id: str | None = None):
    return console.store.decisions(limit, task_id)


@app.get("/api/memory")
async def memory():
    return {"groups": console.memory.overview(), "over_budget": console.memory.over_budget()}


@app.get("/api/memory/changes")
async def memory_changes(limit: int = 100):
    return console.memory.history(limit)


@app.post("/api/memory/file")
async def memory_write(body: MemoryEdit):
    try:
        return console.memory.write(body.path, body.content)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/memory/restructure")
async def memory_restructure(body: MemoryScope):
    try:
        return console.restructure_memory(body.scope).public()
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/memory/search")
async def memory_search(q: str, limit: int = 20):
    return console.memory.search(q, limit)


@app.post("/api/memory/revert/{change_id}")
async def memory_revert(change_id: int):
    try:
        return console.memory.revert(change_id)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    q = console.hub.subscribe()

    async def pump():
        while True:
            await websocket.send_json(await q.get())

    sender = asyncio.create_task(pump())
    try:
        while True:
            await websocket.receive_text()  # keepalive pings; nothing else is expected
    except WebSocketDisconnect:
        pass
    finally:
        sender.cancel()
        console.hub.unsubscribe(q)


app.mount("/static", StaticFiles(directory=WEB), name="static")


@app.get("/")
@app.get("/task/{task_id}")
async def index(task_id: str | None = None):
    return FileResponse(WEB / "index.html", headers=NO_CACHE)
