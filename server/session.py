"""One live agent conversation per task window.

A TaskSession keeps an AgentConnection (server/backends) open across turns so the user can append
to a running turn, interrupt it, or redirect it, and turns the connection's events into the
event stream the browser renders. Every steering action is also written to the decisions log
with a snapshot of the task's state.
"""

import asyncio
import collections
import json
import logging
import os
import secrets
import time
import uuid
from collections.abc import Callable
from typing import Any

from . import backends
from .backends.base import AgentConnection, McpMount, PermissionDecision, SessionSpec, ToolDef, ToolPermission
from .config import CONFIG
from .i18n import tr
from .prompts import P
from .store import Store

log = logging.getLogger("learnme.session")

ACTIVE = ("running", "waiting")
WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit", "ApplyPatch")
INPUT_CLIP = 4000
RESULT_CLIP = 6000
PROFILE_DIR = CONFIG.profile_dir
HABITS_FILE = PROFILE_DIR / "habits.md"      # how the user works; given to every session
JUDGMENT_FILE = PROFILE_DIR / "judgment.md"  # when the user stops / continues / redirects; manager only
WRAPUP_TIMEOUT_S = 300
SUMMARY_CLIP = 60000
# Share of the window at which an idle window saves to memory and compacts, before the agent's
# own auto-compaction would cut in during a turn.
AUTO_COMPACT_AT = 0.75
FLUSH_TIMEOUT_S = 600
MCP_SERVER = "learnme"


def clip(value: Any, limit: int = INPUT_CLIP) -> Any:
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + f"\n… (+{len(value) - limit} chars)"
    if isinstance(value, dict):
        return {k: clip(v, limit) for k, v in value.items()}
    if isinstance(value, list):
        return [clip(v, limit) for v in value]
    return value


def result_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return clip(content, RESULT_CLIP)
    parts = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(item.get("text", ""))
        elif isinstance(item, dict):
            parts.append(f"[{item.get('type', 'block')}]")
        else:
            parts.append(str(item))
    return clip("\n".join(parts), RESULT_CLIP)


def habits() -> str:
    return HABITS_FILE.read_text(encoding="utf-8").strip() if HABITS_FILE.exists() else ""


def touched_paths(name: str, inp: dict) -> list[str]:
    """Files a write tool call changes."""
    if name == "ApplyPatch":
        return [c.get("path") for c in inp.get("changes") or [] if isinstance(c, dict) and c.get("path")]
    target = inp.get("file_path") or inp.get("notebook_path")
    return [target] if isinstance(target, str) else []


def brief(ev: dict) -> dict:
    """Compact form of an event for decision snapshots."""
    t = ev["type"]
    out: dict = {"type": t}
    if t in ("text", "user", "notice", "error", "thinking"):
        out["text"] = (ev.get("text") or ev.get("message") or "")[:300]
    elif t == "tool_use":
        out["name"] = ev.get("name")
        out["input"] = json.dumps(ev.get("input"), ensure_ascii=False)[:200]
    elif t == "tool_result":
        out["content"] = (ev.get("content") or "")[:200]
        out["is_error"] = ev.get("is_error")
    elif t == "result":
        out.update(status=ev.get("status"), num_turns=ev.get("num_turns"))
    elif t == "permission":
        out.update(tool=ev.get("tool"), title=ev.get("title"))
    return out


class TaskSession:
    compact_rules = P.COMPACT_RULES
    flush_prompt = P.FLUSH
    is_manager = False
    has_guard = False  # sessions with a pre_tool guard set this

    def __init__(self, task: dict, store: Store, publish: Callable[[dict], None]):
        self.task = task
        self.store = store
        self.publish = publish
        self.backend = backends.get(task.get("backend"))
        self.seq = store.max_seq(task["id"])
        self.conn: AgentConnection | None = None
        self.watcher: asyncio.Task | None = None
        self.lock = asyncio.Lock()
        self.settled = asyncio.Event()  # set whenever no turn is in flight
        if task["status"] not in ACTIVE:
            self.settled.set()
        self.pending: dict[str, tuple[asyncio.Future, ToolPermission]] = {}
        self.cost_base = task["cost_usd"]
        self.last_active = time.time()
        self.stderr_tail: collections.deque[str] = collections.deque(maxlen=40)
        self.model_seen: str | None = None
        self.todos: list | None = None
        # Set by the console: status-change hook (supervision) and shared settings.
        self.on_status: Callable[[TaskSession, str, str], None] | None = None
        self.on_result: Callable[[TaskSession], None] | None = None  # memory scan after each turn
        self.settings: dict = {}
        self.wrapping = False
        self.auto_rounds = 0  # consecutive turns pushed by the supervisor without the user
        self.last_driver: tuple[str, float] | None = None  # (actor, ts) of the last steering action
        self.ended_by: str | None = None
        # What this turn wrote, so memory changes are credited to the session that made them.
        self.touched: set[str] = set()
        self.bash_cmds: list[str] = []
        self.compacting = False  # a compaction turn is running: nothing for the supervisor to review
        self.flushing = False  # saving to memory before an auto compaction: not for review either
        self.compact_armed = True  # one auto compaction per crossing of AUTO_COMPACT_AT
        self.side_tasks: set[asyncio.Task] = set()
        self.mcp_token = secrets.token_urlsafe(24)  # for this window's tools over HTTP (server/mcp_http.py)

    @property
    def id(self) -> str:
        return self.task["id"]

    @property
    def status(self) -> str:
        return self.task["status"]

    @property
    def connected(self) -> bool:
        return self.conn is not None and self.conn.alive

    def public(self) -> dict:
        return {**self.task, "backend": self.backend.name, "pending": list(self.pending), "connected": self.connected,
                "capabilities": self.backend.capabilities.public()}

    # --- event + state plumbing ---

    def emit(self, type_: str, persist: bool = True, **data) -> None:
        ts = time.time()
        seq = None
        if persist:
            self.seq += 1
            seq = self.seq
            self.store.add_event(self.id, seq, ts, type_, data)
        self.publish({"kind": "event", "event": {"task_id": self.id, "seq": seq, "ts": ts, "type": type_, **data}})

    def update(self, **fields) -> None:
        old_status = self.task["status"]
        fields["updated_at"] = time.time()
        self.task.update(fields)
        self.store.update_task(self.id, **fields)
        if "status" in fields:
            if fields["status"] in ACTIVE:
                self.settled.clear()
            else:
                self.settled.set()
        self.publish({"kind": "task", "task": self.public()})
        if self.on_status and fields.get("status", old_status) != old_status:
            self.on_status(self, old_status, fields["status"])

    def mark_running(self) -> None:
        """A queued message can start a fresh turn on its own after a result."""
        if self.status in ("idle", "interrupted", "error") and self.connected:
            self.update(status="running", turn_started_at=time.time())

    def set_context(self, tokens: int | None = None, window: int | None = None) -> None:
        fields = {k: v for k, v in (("ctx_tokens", tokens), ("ctx_window", window)) if v and v != self.task.get(k)}
        if fields:
            self.update(**fields)

    def _side(self, coro) -> None:
        task = asyncio.create_task(coro)
        self.side_tasks.add(task)
        task.add_done_callback(self.side_tasks.discard)

    def refresh_context(self) -> None:
        """Ask the backend for exact usage: right after connecting (a resumed session has made no
        call yet), after each turn, and after a compaction, which streams no usage of its own."""
        if self.conn is not None:
            self._side(self._refresh_context(self.conn))

    async def _refresh_context(self, conn: AgentConnection) -> None:
        try:
            usage = await asyncio.wait_for(conn.context_usage(), 15)
        except Exception as e:  # noqa: BLE001 - the meter is informational
            if conn.alive:
                log.info("context usage unavailable for %s: %s", self.id, type(e).__name__)
            return
        if usage:
            self.set_context(*usage)
        self.check_context()

    def check_context(self) -> None:
        """An idle window that has filled most of its context saves what it hasn't saved to memory
        and compacts now, instead of the agent compacting it in the middle of a turn later."""
        tokens, window = self.task.get("ctx_tokens") or 0, self.task.get("ctx_window") or 0
        if not window or tokens < window * AUTO_COMPACT_AT:
            self.compact_armed = True
            return
        if (not self.compact_armed or self.flushing or self.compacting or self.wrapping or self.status != "idle"
                or not self.backend.capabilities.compact):
            return
        self._side(self.auto_compact())

    async def auto_compact(self) -> None:
        self.flushing = True
        try:
            async with self.lock:
                if self.status != "idle":
                    return
                pct = round(100 * self.task["ctx_tokens"] / self.task["ctx_window"])
                self.emit("notice", text=tr("🗜 Context {pct}% full: saving corrections and conclusions to memory first, then compacting…", pct=pct))
                await self._ensure_client()
                self.update(status="running", turn_started_at=time.time())
                self.last_active = time.time()
                await self.conn.query(self.flush_prompt)
            await asyncio.wait_for(self.settled.wait(), FLUSH_TIMEOUT_S)
            if self.status == "idle":  # not if the user took over meanwhile; retried at the next idle
                await self.compact(actor="system")
                self.compact_armed = not self.compacting
        except Exception:  # noqa: BLE001 - the agent still compacts on its own when the window is full
            log.warning("auto compaction failed for %s", self.id, exc_info=True)
        finally:
            self.flushing = False

    def snapshot(self) -> dict:
        now = time.time()
        started = self.task.get("turn_started_at")
        return {
            "status": self.status,
            "turn_elapsed_s": round(now - started) if started and self.status in ACTIVE else None,
            "task_age_s": round(now - self.task["created_at"]),
            "turns": self.task["turns"],
            "cost_usd": round(self.task["cost_usd"], 4),
            "todos": self.todos,
            "recent": [brief(e) for e in self.store.recent_events(self.id, 16)],
        }

    def record(self, action: str, text: str | None, status_before: str, actor: str = "user",
               **extra) -> None:
        if actor != "system":  # resume / auto compaction: keep who was really driving
            self.last_driver = (actor, time.time())
        context = self.snapshot()
        context.update({k: v for k, v in extra.items() if v is not None})
        self.store.add_decision(self.id, actor, action, text, self.task["cwd"], status_before, context)

    # --- what the agent gets (subclasses add to these) ---

    def instructions(self) -> str:
        return habits()

    def tools(self) -> list[ToolDef]:
        return []

    def allowed_tools(self) -> list[str] | None:
        return None

    async def pre_tool(self, tool: str, inp: dict) -> str | None:
        """A reason to refuse this tool call, or None. Sessions with guards override it."""
        return None

    def mcp_mount(self) -> McpMount | None:
        tools = self.tools()
        if not tools:
            return None
        return McpMount(name=MCP_SERVER, tools=tools, url=f"{CONFIG.mcp_url}/{self.id}", token=self.mcp_token)

    def spec(self) -> SessionSpec:
        t = self.task
        return SessionSpec(
            cwd=t["cwd"],
            model=t["model"] or self.backend.default_model(self.is_manager),
            permission_mode=t["permission_mode"] or None,
            effort=self.backend.default_effort(self.is_manager),
            resume=t["session_id"] or None,
            fork_from=None if t["session_id"] else t.get("fork_from"),
            instructions=self.instructions(),
            compact_rules=self.compact_rules,
            mcp=self.mcp_mount(),
            allowed_tools=self.allowed_tools(),
            is_manager=self.is_manager,
            can_use_tool=self._can_use_tool,
            pre_tool=self.pre_tool if self.has_guard else None,
            on_event=self._on_event,
            on_stderr=self.stderr_tail.append,
        )

    # --- user actions ---

    async def send(self, text: str, correct: bool = False, actor: str = "user",
                   instruction: str | None = None) -> None:
        """`instruction` is what the user said in the main window when the manager acts for them."""
        async with self.lock:
            before = self.status
            if self.seq == 0:
                action = "new"
            elif before in ACTIVE:
                action = "correct" if correct else "append"
            else:
                action = "reopen" if before == "done" else "continue"
            self.record(action, text, before, actor, instruction=instruction)
            self.auto_rounds = self.auto_rounds + 1 if actor == "auto" else 0
            if action == "correct":
                await self._interrupt_and_settle()
            self.emit("user", text=text, action=action, actor=actor)
            try:
                await self._ensure_client()
                if self.status not in ACTIVE:
                    self.update(status="running", turn_started_at=time.time(), end_reason=None)
                self.last_active = time.time()
                await self.conn.query(text)
            except Exception as e:  # noqa: BLE001 - surface any startup failure in the window
                log.exception("send failed for %s", self.id)
                self.emit("error", message=tr("Could not hand the message to the agent: {e}", e=e),
                          detail="\n".join(self.stderr_tail))
                self.update(status="error")

    async def interrupt(self, actor: str = "user", instruction: str | None = None) -> None:
        async with self.lock:
            before = self.status
            if before not in ACTIVE:
                return
            self.record("interrupt", None, before, actor, instruction=instruction)
            self.emit("notice", text=tr("⏹ You interrupted the task") if actor == "user"
                      else tr("⏹ The manager interrupted the task"))
            await self._interrupt_and_settle()

    async def end(self, reason: str | None, actor: str = "user", instruction: str | None = None) -> None:
        async with self.lock:
            before = self.status
            if before == "done":
                return
            self.record("end", reason, before, actor, instruction=instruction)
            self.ended_by = actor
            if before in ACTIVE:
                await self._interrupt_and_settle()
            if self.settings.get("wrapup", True) and self.task["session_id"]:
                await self._wrap_up(reason, actor)
            await self.close()
            head = tr("✓ Task ended") if actor == "user" else tr("✓ The manager ended the task")
            self.emit("notice", text=head + (f": {reason}" if reason else ""))
            self.update(status="done", end_reason=reason)

    async def _wrap_up(self, reason: str | None, actor: str) -> None:
        """One last turn so the task can save what it learned into the project's memory."""
        who = P.WRAPUP_WHO.get(actor, actor)
        self.emit("notice", text=tr("Wrapping up: checking whether the project memory needs updating…"))
        self.wrapping = True
        try:
            await self._ensure_client()
            self.update(status="running", turn_started_at=time.time())
            await self.conn.query(P.WRAPUP.format(who=who, reason=reason or tr("not given")))
            await asyncio.wait_for(self.settled.wait(), WRAPUP_TIMEOUT_S)
        except Exception:  # noqa: BLE001 - wrap-up is best effort; ending must still succeed
            log.warning("wrap-up failed for %s", self.id, exc_info=True)
            self.emit("notice", text=tr("Wrap-up did not finish; ending anyway"))
            if self.status in ACTIVE:
                await self._interrupt_and_settle()
        finally:
            self.wrapping = False

    async def compact(self, focus: str | None = None, actor: str = "user") -> None:
        """Summarize the conversation so far to free the context window (like Claude Code's /compact)."""
        async with self.lock:
            before = self.status
            if before in ACTIVE or before == "done" or not (self.task["session_id"] or self.task.get("fork_from")):
                return
            if not self.backend.capabilities.compact:
                self.emit("notice", text=tr("This backend can't compact on request"))
                return
            self.record("compact", focus, before, actor)
            self.emit("notice", text=tr("🗜 Compacting the context") + (tr(" (keeping: {focus})", focus=focus) if focus else "") + "…")
            self.compacting = True
            try:
                await self._ensure_client()
                self.update(status="running", turn_started_at=time.time())
                self.last_active = time.time()
                await self.conn.compact(focus)
            except Exception as e:  # noqa: BLE001 - surface any startup failure in the window
                log.exception("compact failed for %s", self.id)
                self.emit("error", message=tr("Compaction failed: {e}", e=e), detail="\n".join(self.stderr_tail))
                self.update(status=before)
                self.compacting = False

    def resolve_permission(self, req_id: str, action: str, message: str | None,
                           answers: dict | None) -> bool:
        entry = self.pending.get(req_id)
        if not entry:
            return False
        fut, req = entry
        if action not in ("allow", "allow_always", "answer"):
            message = message or tr("The user declined this action")
        self.record(action, message or (json.dumps(answers, ensure_ascii=False) if answers else None),
                    self.status, tool=req.tool, tool_input=clip(req.input, 500))
        if not fut.done():
            fut.set_result(PermissionDecision(action=action, message=message, answers=answers))
        self.emit("permission_done", req_id=req_id, action=action, message=message, answers=answers)
        return True

    # --- connection lifecycle ---

    async def _ensure_client(self) -> None:
        if self.connected:
            return
        self.model_seen = None
        resuming = bool(self.task["session_id"])  # start() may report a new conversation's id already
        conn = self.backend.connect(self.spec())
        await conn.start()
        self.conn = conn
        self.cost_base = self.task["cost_usd"]
        self.watcher = asyncio.create_task(self._watch(conn), name=f"agent-{self.id}")
        self.refresh_context()
        if resuming:
            self.emit("notice", text=tr("Resumed the existing session"))
        elif self.task.get("fork_from"):
            self.emit("notice", text=tr("Forked from the original Claude Code session; continuing its context"))

    async def _watch(self, conn: AgentConnection) -> None:
        """The agent process went away: on close(), or on its own (a crash)."""
        await conn.wait_closed()
        if self.conn is not conn:
            return
        self.conn = None
        self._deny_pending(tr("The agent process exited"))
        if self.status in ACTIVE:
            err = conn.exit_error
            self.emit("error", message=tr("The agent process exited unexpectedly") + (f": {err}" if err else ""),
                      detail="\n".join(self.stderr_tail))
            self.update(status="error")
        self.publish({"kind": "task", "task": self.public()})

    async def close(self) -> None:
        """Stop the agent process; the conversation can be resumed later by session id."""
        self._deny_pending(tr("The session was closed"))
        conn, self.conn = self.conn, None
        if conn:
            try:
                await conn.close()
            except Exception:  # noqa: BLE001 - best effort teardown
                log.warning("close failed for %s", self.id, exc_info=True)
        self.publish({"kind": "task", "task": self.public()})

    async def _interrupt_and_settle(self) -> None:
        self._deny_pending(tr("The user interrupted the task"), interrupt=True)
        if not self.connected:
            if self.status in ACTIVE:
                self.update(status="interrupted")
            return
        try:
            await asyncio.wait_for(self.conn.interrupt(), 10)
            await asyncio.wait_for(self.settled.wait(), 15)
        except Exception:  # noqa: BLE001 - a wedged agent gets restarted instead
            log.warning("interrupt did not settle for %s; restarting the agent", self.id, exc_info=True)
            self.emit("notice", text=tr("Interrupt timed out; restarted the agent process (the session can continue)"))
            await self.close()
            self.update(status="interrupted")

    def _deny_pending(self, message: str, interrupt: bool = False) -> None:
        for req_id, (fut, _req) in list(self.pending.items()):
            if not fut.done():
                fut.set_result(PermissionDecision(action="deny", message=message, interrupt=interrupt))
                self.emit("permission_done", req_id=req_id, action="cancelled", message=message, answers=None)

    async def _can_use_tool(self, req: ToolPermission) -> PermissionDecision:
        req_id = uuid.uuid4().hex[:10]
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self.pending[req_id] = (fut, req)
        self.emit("permission", req_id=req_id, tool=req.tool, input=clip(req.input),
                  title=req.title, description=req.description, can_always=req.can_always)
        self.update(status="waiting")
        try:
            return await fut
        finally:
            self.pending.pop(req_id, None)
            if not self.pending and self.status == "waiting":
                self.update(status="running")

    # --- backend events -> UI events ---

    def _on_event(self, ev: dict) -> None:
        self.last_active = time.time()
        try:
            self._handle(ev)
        except Exception:  # noqa: BLE001 - one bad event must not kill the session
            log.exception("failed to handle %s for %s", ev.get("type"), self.id)

    def _handle(self, ev: dict) -> None:
        t = ev["type"]
        if t == "context":
            self.set_context(ev.get("tokens"), ev.get("window"))
        elif t == "delta":
            self.mark_running()
            self.emit("delta", persist=False, kind=ev["kind"], text=ev.get("text") or "", parent=ev.get("parent"))
        elif t == "init":
            self.mark_running()
            sid = ev.get("session_id")
            if sid and sid != self.task["session_id"]:
                self.update(session_id=sid)
            model = ev.get("model")
            if model and model != self.model_seen:
                self.model_seen = model
                self.emit("init", model=model, session_id=sid, backend=self.backend.name,
                          effort=self.backend.default_effort(self.is_manager),
                          permission_mode=ev.get("permission_mode"), cwd=ev.get("cwd"))
        elif t == "compacting":
            if not self.compacting:
                self.emit("notice", text=tr("🗜 The context is nearly full; compacting automatically…"))
        elif t == "compact":
            self.emit("compact", trigger=ev.get("trigger"), pre_tokens=ev.get("pre_tokens"),
                      duration_ms=ev.get("duration_ms"))
            self.refresh_context()
        elif t == "compact_summary":
            self.emit("compact_summary", text=clip(ev.get("text") or "", SUMMARY_CLIP))
        elif t in ("text", "thinking"):
            self.mark_running()
            self.emit(t, text=ev["text"], parent=ev.get("parent"))
        elif t == "tool_use":
            self.mark_running()
            name, inp, parent = ev["name"], ev.get("input") or {}, ev.get("parent")
            if name == "TodoWrite" and parent is None:
                self.todos = inp.get("todos")
            if name in WRITE_TOOLS:
                self.touched.update(os.path.normpath(p) for p in touched_paths(name, inp))
            elif name == "Bash" and isinstance(inp.get("command"), str):
                self.bash_cmds.append(inp["command"])
            self.emit("tool_use", id=ev.get("id"), name=name, input=clip(inp), parent=parent)
        elif t == "tool_result":
            self.emit("tool_result", tool_use_id=ev.get("tool_use_id"), content=result_text(ev.get("content")),
                      is_error=bool(ev.get("is_error")), parent=ev.get("parent"))
        elif t == "error":
            self.emit("error", message=ev.get("message"), detail=ev.get("detail"))
        elif t == "rate_limited":
            resets = ev.get("resets_at")
            when = time.strftime("%H:%M", time.localtime(resets)) if resets else tr("later")
            self.emit("error", message=tr("Rate limited; resets at {when}", when=when))
        elif t == "result":
            spent = ev.get("cost_usd")
            cost = self.cost_base + spent if spent is not None else self.task["cost_usd"]
            status = ev.get("status") or "idle"
            self.emit("result", status=status, subtype=ev.get("subtype"), reason=ev.get("reason"),
                      duration_ms=ev.get("duration_ms"), num_turns=ev.get("num_turns"),
                      cost_usd=cost or None, errors=ev.get("errors"), text=ev.get("text"),
                      **({"compact": True} if self.compacting else {}))
            self.update(status=status if self.status != "done" else "done", cost_usd=cost,
                        turns=self.task["turns"] + (ev.get("num_turns") or 0))
            self.compacting = False
            self.refresh_context()
            if self.on_result:
                self.on_result(self)
