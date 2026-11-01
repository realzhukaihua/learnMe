"""Process-wide registry: browser fan-out, task sessions, project discovery."""

import asyncio
import json
import logging
import shutil
import time
import uuid
from collections.abc import Coroutine
from pathlib import Path

from . import backends
from .config import CONFIG, ROOT
from .history import import_transcript, last_context, scan_claude_sessions
from .i18n import tr
from .manager import MANAGER_ID, ManagerSession, _ago, status_labels
from .memory import CLAUDE_PROJECTS, MemoryTracker, claude_slug
from .prompts import P
from .session import ACTIVE, PROFILE_DIR, TaskSession
from .slack_bridge import WINDOWS_KV
from .slack_window import SlackTask, SlackWindow
from .store import Store

log = logging.getLogger("learnme.console")

PROJECT_ROOTS = CONFIG.project_roots
IDLE_DISCONNECT_S = 30 * 60
AUTO_ROUND_LIMIT = 3
MEMORY_SCAN_S = 20
CONSOLIDATE_RETRY_S = {"profile": 6 * 3600, "manager": 6 * 3600, "project": 24 * 3600}
SETTINGS_FILE = CONFIG.data_dir / "settings.json"
DEFAULT_SETTINGS = {
    "supervise": True,  # hand finished sub-task turns to the manager for review
    "wrapup": True,     # let a task update its project memory before it is closed
}


def bootstrap_profile() -> None:
    """First run: start the profile from the examples, so the manager has the files to grow."""
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    for name in ("habits", "judgment"):
        real, example = PROFILE_DIR / f"{name}.md", ROOT / "profile" / f"{name}.example.md"
        if not real.exists() and example.exists():
            shutil.copyfile(example, real)


class Hub:
    """Fans every message out to each connected browser tab, in order."""

    def __init__(self):
        self.queues: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=20000)
        self.queues.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.queues.discard(q)

    def publish(self, msg: dict) -> None:
        for q in list(self.queues):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                # A stalled tab: drop its backlog and tell it to refetch.
                while not q.empty():
                    q.get_nowait()
                q.put_nowait({"kind": "resync"})


class Supervisor:
    """Hands sub-tasks that stopped (or ended) to the manager, batched, whenever it is free."""

    def __init__(self, console: "Console"):
        self.console = console
        self.items: dict[str, str] = {}  # task_id -> "review" | "ended"; "mem:<path>" -> "consolidate"
        self.wake = asyncio.Event()
        # When each over-limit file was last raised, kept across restarts so a restart doesn't re-nag.
        self.consolidate_asked: dict[str, float] = console.store.get_kv("consolidate_asked", {})
        self.overages: dict[str, dict] = {}  # path -> budget overage, for the consolidation hand-off

    @staticmethod
    def drivers() -> dict[str, str]:
        return {"user": tr("the user (in the task window)"), "manager": tr("the manager (on the user's word in the main window)"),
                "auto": tr("the manager (automatic supervision)"), "event": tr("an external event"),
                "slack": tr("a Slack window")}

    def ask_consolidation(self, over: list[dict]) -> None:
        now = time.time()
        for o in over:
            retry = CONSOLIDATE_RETRY_S[o["scope"].split(":")[0]]
            if now - self.consolidate_asked.get(o["path"], 0) < retry:
                continue
            self.consolidate_asked[o["path"]] = now
            self.console.store.set_kv("consolidate_asked", self.consolidate_asked)
            self.items[f"mem:{o['path']}"] = "consolidate"
            self.overages[o["path"]] = o
            self.wake.set()

    def on_status(self, s: TaskSession, old: str, new: str) -> None:
        if s.id == MANAGER_ID and (slack := getattr(self.console, "slack", None)):
            slack.on_manager_status(old, new)  # its reply to the owner's Slack DM goes back there
        if s.id == MANAGER_ID or s.wrapping or s.compacting or s.flushing:
            return
        if (slack := getattr(self.console, "slack", None)) and slack.on_task_status(s, old, new):
            return  # a Slack window, or a task one dispatched: the window checks it, not the manager
        if not self.console.settings["supervise"]:
            return
        if new == "done":
            self.items[s.id] = "ended"
        elif old in ACTIVE and new in ("idle", "error"):
            self.items[s.id] = "review"
        elif new in ACTIVE and self.items.get(s.id) == "review":
            del self.items[s.id]  # it picked up work again; review later
            return
        else:
            return
        self.wake.set()

    async def run(self) -> None:
        while True:
            await self.wake.wait()
            await asyncio.sleep(3)  # batch near-simultaneous completions
            manager = self.console.manager()  # not cached: switching its backend replaces the session
            await manager.settled.wait()
            self.wake.clear()
            items, self.items = self.items, {}
            if items and self.console.settings["supervise"]:
                try:
                    await manager.send(self.compose(items), actor="system")
                except Exception:  # noqa: BLE001 - keep supervising other tasks
                    log.exception("supervisor hand-off failed")

    def compose(self, items: dict[str, str]) -> str:
        lines = [P.AUTO_HEAD]
        labels = status_labels()
        for task_id, kind in items.items():
            if kind == "consolidate":
                o = self.overages.get(task_id[4:])
                if o and o["scope"].startswith("project:"):
                    lines.append(tr("- Project memory needs layering · {label}: the always-loaded {name} has {size}{unit} "
                                    "(limit {limit}{unit}). It is that project's own memory, so don't edit it yourself: "
                                    "tell the user here that layering only reorganizes and deletes no knowledge, and "
                                    "dispatch restructure_memory once they agree.", label=o["scope_label"], name=o["name"],
                                    size=o["size"], unit=o["unit"], limit=o["limit"]))
                elif o:
                    lines.append(tr("- Memory needs layering · {label}: {name} ({path}) has {size}{unit}, over the "
                                    "always-loaded limit of {limit}{unit}. Split the details into separate files (the "
                                    "profile into profile/<topic>.md, your memory into topic-<topic>.md) and keep only "
                                    "the most used rules and an index to the details in the loaded file. Delete no knowledge.",
                                    label=o["scope_label"], name=o["name"], path=o["path"], size=o["size"],
                                    unit=o["unit"], limit=o["limit"]))
                continue
            s = self.console.session(task_id)
            if not s:
                continue
            t = s.task
            head = tr("\"{title}\" task_id={id} · project {project}", title=t["title"], id=task_id, project=Path(t["cwd"]).name)
            if kind == "ended":
                by = self.drivers().get(s.ended_by or "", tr("unknown")).split(" (")[0]
                lines.append(tr("- Wrap-up · {head} · ended, reason: {reason} (ended by {by})", head=head,
                                reason=t.get("end_reason") or tr("not given"), by=by))
                continue
            driver = tr("unknown")
            if s.last_driver:
                driver = tr("{who}, {ago} ago", who=self.drivers().get(s.last_driver[0], s.last_driver[0]),
                            ago=_ago(s.last_driver[1]))
            line = tr("- Review · {head} · {status} · last instruction from {driver} · auto-continued {n}/{limit} rounds",
                      head=head, status=labels.get(t["status"], t["status"]), driver=driver, n=s.auto_rounds,
                      limit=AUTO_ROUND_LIMIT)
            if s.auto_rounds >= AUTO_ROUND_LIMIT:
                line += tr(" (limit reached: don't push it on; report to the user with a recommendation)")
            lines.append(line)
        return "\n".join(lines)


class Console:
    def __init__(self):
        CONFIG.data_dir.mkdir(parents=True, exist_ok=True)
        bootstrap_profile()
        self.store = Store(CONFIG.data_dir / "learnme.db")
        self.hub = Hub()
        self.sessions: dict[str, TaskSession] = {}
        self.background: set[asyncio.Task] = set()
        self.settings = {**DEFAULT_SETTINGS, **self._load_settings()}
        self.supervisor = Supervisor(self)
        self.memory = MemoryTracker(self.store, ROOT, PROFILE_DIR, PROJECT_ROOTS, self.hub.publish)
        self.slack = None  # set by main when the Slack bridge is configured

    @staticmethod
    def _load_settings() -> dict:
        try:
            return json.loads(SETTINGS_FILE.read_text())
        except (OSError, ValueError):
            return {}

    def update_settings(self, **changes) -> dict:
        self.settings.update({k: v for k, v in changes.items() if k in DEFAULT_SETTINGS})
        SETTINGS_FILE.write_text(json.dumps(self.settings, indent=2))
        self.hub.publish({"kind": "settings", "settings": self.settings})
        return self.settings

    def _register(self, s: TaskSession) -> TaskSession:
        s.on_status = self.supervisor.on_status
        s.on_result = self.after_turn
        s.settings = self.settings
        self.sessions[s.id] = s
        return s

    def spawn(self, coro: Coroutine) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self.background.add(task)
        task.add_done_callback(self.background.discard)
        return task

    # --- sessions ---

    def session(self, task_id: str) -> TaskSession | None:
        if task_id == MANAGER_ID:
            return self.manager()
        s = self.sessions.get(task_id)
        if s:
            return s
        task = self.store.get_task(task_id)
        if not task:
            return None
        return self._new_session(task)

    def _new_session(self, task: dict) -> TaskSession:
        # Read from the store, not the bridge: a Slack window keeps its guard even with Slack off.
        if task["id"] in (self.store.get_kv(WINDOWS_KV, {}).get("windows") or {}):
            return self._register(SlackWindow(task, self.store, self.hub.publish, self))
        if task["origin"] == "slack":  # dispatched on someone else's Slack request: connectors held back
            return self._register(SlackTask(task, self.store, self.hub.publish, self))
        return self._register(TaskSession(task, self.store, self.hub.publish))

    def manager(self) -> ManagerSession:
        s = self.sessions.get(MANAGER_ID)
        if s:
            return s  # type: ignore[return-value]
        if not self.store.get_task(MANAGER_ID):
            now = time.time()
            self.store.create_task({
                "id": MANAGER_ID, "kind": "manager", "origin": "user", "title": "main",
                "cwd": str(ROOT), "status": "idle", "backend": backends.default_name(manager=True),
                "created_at": now, "updated_at": now,
            })
        task = self.store.get_task(MANAGER_ID)
        # The backend picked in the UI wins over the config; either way an existing conversation keeps its own.
        wanted = self.store.get_kv("manager_backend") or backends.default_name(manager=True)
        if (task.get("backend") or backends.default_name()) != wanted and not task.get("session_id"):
            self.store.update_task(MANAGER_ID, backend=wanted)  # no conversation to keep yet
            task = self.store.get_task(MANAGER_ID)
        return self._register(ManagerSession(task, self.store, self.hub.publish, self))  # type: ignore[return-value]

    def set_manager_backend(self, name: str) -> None:
        """Switch the main window's backend: its conversation belongs to the old one, so it starts fresh."""
        backends.get(name)
        self.store.set_kv("manager_backend", name)
        old = self.sessions.pop(MANAGER_ID, None)
        if old:
            self.spawn(old.close())
        self.store.update_task(MANAGER_ID, backend=name, session_id=None, ctx_tokens=None, ctx_window=None)
        self.manager()

    def create_task(self, prompt: str, cwd: str, *, title: str | None = None, model: str | None = None,
                    permission_mode: str | None = None, origin: str = "user", backend: str | None = None,
                    instruction: str | None = None, actor: str | None = None,
                    task_id: str | None = None) -> TaskSession:
        now = time.time()
        task_id = task_id or uuid.uuid4().hex[:8]
        first_line = prompt.strip().splitlines()[0] if prompt.strip() else tr("task")
        self.store.create_task({
            "id": task_id, "kind": "task", "origin": origin,
            "title": (title or first_line)[:80], "cwd": cwd, "model": model or None,
            "permission_mode": permission_mode or None, "status": "idle",
            "backend": backends.get(backend).name,
            "created_at": now, "updated_at": now,
        })
        s = self._new_session(self.store.get_task(task_id))
        self.memory.track_project(cwd)
        self.hub.publish({"kind": "task", "task": s.public(), "created": True})
        self.spawn(s.send(prompt, actor=actor or origin, instruction=instruction))
        return s

    def tasks(self) -> list[dict]:
        out = []
        for t in self.store.list_tasks():
            s = self.sessions.get(t["id"])
            out.append(s.public() if s else {**t, "pending": [], "connected": False})
        return out

    # --- history ---

    def history_windows(self, q: str = "") -> list[dict]:
        rows = self.store.search_tasks(q, 200) if q.strip() else self.store.list_tasks(300)
        out = []
        for t in rows:
            s = self.sessions.get(t["id"])
            last = self.store.last_message(t["id"])
            out.append({**(s.public() if s else {**t, "pending": [], "connected": False}),
                        "project": Path(t["cwd"]).name,
                        "preview": (last.get("text") or "")[:240] if last else None})
        return out

    async def history_sessions(self, q: str = "") -> list[dict]:
        rows = await asyncio.to_thread(scan_claude_sessions, self.store)
        mine = self.store.known_session_ids()
        terms = [t.lower() for t in q.split() if t]
        out = []
        for r in rows:
            if r["session_id"] in mine:
                continue  # learnMe's own windows show up in the other list
            hay = " ".join(str(r.get(k) or "") for k in ("title", "first_prompt", "last_prompt", "cwd")).lower()
            if all(t in hay for t in terms):
                out.append({k: r[k] for k in ("path", "session_id", "cwd", "title", "first_prompt", "last_prompt",
                                              "started", "ended", "entrypoint", "prompts")}
                           | {"project": Path(r["cwd"]).name if r["cwd"] else None})
        return out

    def restore_session(self, path: str) -> TaskSession:
        """Open a Claude Code session as a window: recent history is imported, and the first
        message forks the session so the original transcript is never written to."""
        p = Path(path).resolve()
        if p.suffix != ".jsonl" or CLAUDE_PROJECTS.resolve() not in p.parents or not p.is_file():
            raise ValueError(tr("Not a Claude Code session transcript"))
        info = self.store.claude_session_cache().get(str(p))
        if not info or not info["cwd"] or not Path(info["cwd"]).is_dir():
            raise ValueError(tr("This session's working directory no longer exists"))
        if claude_slug(info["cwd"]) != p.parent.name:
            raise ValueError(tr("The transcript doesn't match its working directory; it can't be resumed in place"))
        now = time.time()
        task_id = uuid.uuid4().hex[:8]
        title = info["title"] or (info["first_prompt"] or tr("past session")).splitlines()[0]
        self.store.create_task({
            "id": task_id, "kind": "task", "origin": "user", "title": title[:80], "cwd": info["cwd"],
            "status": "idle", "fork_from": info["session_id"], "ctx_tokens": last_context(p),
            "backend": "claude", "created_at": now, "updated_at": now,
        })
        s = self._register(TaskSession(self.store.get_task(task_id), self.store, self.hub.publish))
        self.memory.track_project(info["cwd"])
        entries = import_transcript(p)
        s.emit("notice", text=tr("Restored from a Claude Code session ({entry} · {n} prompts); the last {k} entries follow",
                                 entry=info["entrypoint"] or "cli", n=info["prompts"], k=len(entries)))
        for kind, data in entries:
            if kind == "user":
                s.emit("user", text=data["text"], action="history", actor="history")
            elif kind == "text":
                s.emit("text", text=data["text"], parent=None, imported=True)
            else:
                s.emit("history_tool", name=data["name"], brief=data["brief"])
        s.emit("notice", text=tr("—— Above is the original session. Send a message to continue from here: it forks a new session, the original is untouched ——"))
        s.record("restore", info["session_id"], "idle")
        self.hub.publish({"kind": "task", "task": s.public(), "created": True})
        return s

    # --- projects ---

    def projects(self) -> list[dict]:
        usage = self.store.project_usage()
        found: dict[str, dict] = {}
        for root in PROJECT_ROOTS:
            if not root.is_dir():
                continue
            for p in root.iterdir():
                if p.is_dir() and not p.name.startswith((".", "_", "$")):
                    found[str(p)] = {"path": str(p), "name": p.name}
        for cwd in usage:
            found.setdefault(cwd, {"path": cwd, "name": Path(cwd).name})
        for item in found.values():
            item.update(usage.get(item["path"], {"uses": 0, "last_used": None}))
        return sorted(found.values(), key=lambda x: (-(x["last_used"] or 0), x["name"].lower()))

    def workspace(self) -> Path:
        """Where tasks that don't belong to any project run."""
        ws = CONFIG.workspace
        ws.mkdir(parents=True, exist_ok=True)
        return ws

    def resolve_project(self, ref: str) -> tuple[str | None, list[str]]:
        """Map a project name or path to a directory. Returns (path, candidates)."""
        ref = ref.strip().rstrip("/")
        p = Path(ref).expanduser()
        if p.is_absolute():
            return (str(p), []) if p.is_dir() else (None, [])
        projects = self.projects()
        for match in (
            lambda x: x["name"] == ref,
            lambda x: x["name"].lower() == ref.lower(),
        ):
            hits = [x["path"] for x in projects if match(x)]
            if len(hits) == 1:
                return hits[0], []
        hits = [x["path"] for x in projects if ref.lower() in x["name"].lower()]
        if len(hits) == 1:
            return hits[0], []
        return None, hits[:20]

    # --- memory ---

    def memory_scopes(self, s: TaskSession) -> set[str]:
        if s.id == MANAGER_ID:
            return {"profile", "manager"}
        if isinstance(s, SlackWindow):  # on the owner's word it may edit the manager's memory too
            return {"profile", "manager", self.memory.scope_of_project(s.task["cwd"])}
        return {"profile", self.memory.scope_of_project(s.task["cwd"])}

    def after_turn(self, s: TaskSession) -> None:
        """Credit memory changes to the turn that just ended, but only for files it wrote
        (Write/Edit/patches) or named in a shell command; anything else changed meanwhile is external."""
        actor = "manager" if s.id == MANAGER_ID else "task"
        scopes = self.memory_scopes(s)
        touched, cmds = s.touched, s.bash_cmds
        s.touched, s.bash_cmds = set(), []

        def who(path: str, scope: str) -> tuple[str, str] | None:
            if scope in scopes and (path in touched or any(Path(path).name in c for c in cmds)):
                return s.id, actor
            return None

        self._announce(self.memory.scan(attribute=who))

    def _announce(self, changes: list[dict]) -> None:
        for ch in changes:
            if ch["task_id"] and (s := self.session(ch["task_id"])):
                s.emit("memory", change_id=ch["id"], name=ch["name"], scope_label=ch["scope_label"],
                       change_kind=ch["kind"], diff=ch["diff"], path=ch["path"])
        if changes:
            self.supervisor.ask_consolidation(self.memory.over_budget())

    def restructure_memory(self, scope: str, origin: str = "user",
                           instruction: str | None = None) -> TaskSession:
        """Dispatch a task into the project to split its memory into a deeper index."""
        if not scope.startswith("project:"):
            raise ValueError(tr("Only project memory is reorganized by a task; the manager layers the profile and its own memory itself"))
        cwd = scope.split(":", 1)[1]
        return self.create_task(self.memory.restructure_prompt(scope), cwd,
                                title=tr("Memory layering · {name}", name=Path(cwd).name), origin=origin,
                                instruction=instruction)

    async def watch_memory(self) -> None:
        """Catch edits made outside any turn (the user's editor, other agent sessions)."""
        while True:
            await asyncio.sleep(MEMORY_SCAN_S)
            busy = set()
            for s in list(self.sessions.values()):
                if s.status in ACTIVE:
                    busy |= self.memory_scopes(s)
            try:
                self._announce(self.memory.scan(skip_scopes=busy))
            except Exception:  # noqa: BLE001 - keep watching
                log.exception("memory scan failed")

    # --- lifecycle ---

    def backfill_context(self) -> None:
        """Claude windows from before the context meter: take their last usage from the transcript."""
        for t in [self.store.get_task(MANAGER_ID), *self.store.list_tasks()]:
            if not t or t["ctx_tokens"] or not t["session_id"] or (t.get("backend") or "claude") != "claude":
                continue
            path = CLAUDE_PROJECTS / claude_slug(t["cwd"]) / f"{t['session_id']}.jsonl"
            if path.is_file() and (tokens := last_context(path)):
                self.store.update_task(t["id"], ctx_tokens=tokens, updated_at=t["updated_at"])

    def recover(self) -> list[TaskSession]:
        """Turns in flight when the server stopped were cut off; return them so they can resume."""
        self.backfill_context()
        self.memory.discover_projects()
        self._announce(self.memory.scan())  # edits made while we were down
        self.memory.ensure_index()
        cut_off = []
        for t in self.store.tasks_with_status(ACTIVE):
            s = self.session(t["id"])
            s.emit("notice", text=tr("A server restart cut this turn off; continuing automatically"))
            s.update(status="interrupted")
            cut_off.append(s)
        return cut_off

    def resume(self, sessions: list[TaskSession]) -> None:
        for s in sessions:
            self.spawn(s.send(P.RESUME, actor="system"))

    async def reap_idle(self) -> None:
        while True:
            await asyncio.sleep(60)
            now = time.time()
            for s in list(self.sessions.values()):
                if s.connected and s.status not in ACTIVE and now - s.last_active > IDLE_DISCONNECT_S:
                    log.info("closing idle session %s", s.id)
                    self.spawn(s.close())

    async def shutdown(self) -> None:
        await asyncio.gather(*(s.close() for s in self.sessions.values() if s.connected),
                             return_exceptions=True)
