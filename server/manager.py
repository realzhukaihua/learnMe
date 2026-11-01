"""The main window: a long-lived agent session that dispatches and supervises task windows.

The manager talks with the user and acts on task windows through learnMe's tools (build_tools),
which every backend can mount: in-process for Claude, over HTTP MCP for the others. When it
steers a task, the decision is logged with actor="manager" and the user's latest main-window
message as the instruction behind it.
"""

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import backends
from .backends.base import ToolDef
from .config import BACKENDS, CONFIG
from .i18n import tr
from .prompts import P
from .session import ACTIVE, HABITS_FILE, JUDGMENT_FILE, MCP_SERVER, PROFILE_DIR, TaskSession, brief, habits
from .slack_bridge import NOTES_PER_HOUR, ReplyRefused, SlackTurn

if TYPE_CHECKING:
    from .console import Console
    from .slack_bridge import SlackBridge

MANAGER_ID = "main"
MEMORY_INDEX_LINES = 150
MEMORY_INDEX_CHARS = 8000


def status_labels() -> dict[str, str]:
    return {"running": tr("running"), "waiting": tr("waiting for approval"), "idle": tr("turn done, needs a decision"),
            "interrupted": tr("interrupted, needs a decision"), "error": tr("error, needs a decision"),
            "done": tr("ended")}


TOOL_NAMES = ["list_projects", "dispatch_task", "list_tasks", "task_status",
              "message_task", "interrupt_task", "end_task", "task_decisions",
              "project_history", "recall", "memory_changes", "restructure_memory",
              "search_history", "restore_session"]
SLACK_TOOLS = ["slack_reply", "slack_ask_approval", "slack_triage", "slack_thread", "slack_pending", "slack_update"]
MANAGER_ONLY_TOOLS = ["notify_owner"]  # not in a Slack window's set


def _ok(data: Any) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=1)}]}


def _err(message: str) -> dict:
    return {"content": [{"type": "text", "text": message}], "is_error": True}


def _ago(ts: float | None) -> str | None:
    if not ts:
        return None
    s = int(time.time() - ts)
    return f"{s}s" if s < 60 else f"{s // 60}m" if s < 3600 else f"{s // 3600}h{s % 3600 // 60}m"


def _task_row(t: dict) -> dict:
    return {
        "task_id": t["id"], "title": t["title"], "project": Path(t["cwd"]).name,
        "status": t["status"], "status_label": status_labels().get(t["status"], t["status"]),
        "backend": t.get("backend") or backends.default_name(),
        "origin": t["origin"], "turns": t["turns"], "cost_usd": round(t["cost_usd"], 3),
        "updated": _ago(t["updated_at"]) + " ago",
    }


def _schema(required: list[str] | None = None, **props) -> dict:
    out: dict = {"type": "object", "properties": props}
    if required:
        out["required"] = required
    return out


def build_tools(console: "Console", manager: "ManagerSession") -> list[ToolDef]:
    """learnMe's tools for the manager or a Slack window (`manager` is the session using them)."""
    tools: list[ToolDef] = []

    def tool(name: str, description: str, schema: dict):
        def deco(fn):
            tools.append(ToolDef(name=name, description=description, schema=schema, handler=fn))
            return fn
        return deco

    def target(task_id: str) -> TaskSession | None:
        return None if task_id == MANAGER_ID else console.session(task_id)

    def acting() -> dict:
        """Steering on the user's word is 'manager'; on its own during supervision it is 'auto'."""
        if manager.trigger == "user":
            return {"actor": "manager", "instruction": manager.last_user_text}
        return {"actor": "auto", "instruction": None}

    def slack() -> "SlackBridge | None":
        return getattr(console, "slack", None)

    def by_user() -> bool:
        """The user's own word: said in the main window, or in a Slack window's turn where they spoke
        themselves (a turn on a task's result may have their rights, but it is not their word)."""
        turn = manager.slack_turn
        return bool(turn.owner_word) if turn else manager.trigger == "user"

    @tool("list_projects", tr("List the project folders tasks can be dispatched to (most recently used first). Filter by a name substring with query."),
          _schema(query={"type": "string", "description": tr("name substring, case-insensitive")},
                  limit={"type": "integer", "description": tr("max rows, default 40")}))
    async def list_projects(args):
        q = (args.get("query") or "").lower()
        items = [p for p in console.projects() if q in p["name"].lower()]
        return _ok([{"name": p["name"], "path": p["path"], "uses": p["uses"],
                     "last_used": _ago(p["last_used"])} for p in items[: args.get("limit") or 40]])

    @tool("dispatch_task", tr("Open a new task window in a project folder and hand the work to a separate agent session. Returns the task_id at once; the task runs in the background."),
          _schema(["prompt"],
                  project={"type": "string", "description": tr("project name (folder name) or absolute path; leave empty for tasks that belong to no project")},
                  prompt={"type": "string", "description": tr("the full instructions; the task can't see the main window's conversation")},
                  title={"type": "string", "description": tr("short window title")},
                  backend={"type": "string", "enum": list(BACKENDS), "description": tr("optional: which agent runs it; default is the configured one")},
                  model={"type": "string", "description": tr("optional model name for that backend; default follows the configuration")},
                  permission_mode={"type": "string", "description": tr("optional: acceptEdits / bypassPermissions / plan / default; default follows the configuration")},
                  slack_channel={"type": "string", "description": tr("required when dispatching for a [Slack] request: that conversation's channel")},
                  slack_thread_ts={"type": "string", "description": tr("likewise: its thread_ts (empty for a direct DM reply)")}))
    async def dispatch_task(args):
        if not args.get("project"):
            path, candidates = str(console.workspace()), []
        else:
            path, candidates = console.resolve_project(args["project"])
        if not path:
            hint = tr("candidates: {names}", names=", ".join(Path(c).name for c in candidates)) if candidates else tr("no matching project")
            return _err(tr("No project {name!r}. {hint}", name=args["project"], hint=hint))
        if args.get("backend") and (bad := backends.get(args["backend"]).problems()):
            return _err(tr("Backend {name} can't run here: {why}", name=args["backend"], why="; ".join(bad)))
        prompt, title, origin = args["prompt"], args.get("title"), "manager"
        turn, bridge = manager.slack_turn, slack()
        key = (args.get("slack_channel") or "", args.get("slack_thread_ts") or None)
        if turn and not args.get("slack_channel"):  # a Slack window dispatches for its own conversation
            key = turn.convos[0].key
        for_slack = bool(turn and bridge and bridge.dispatchable(key, turn))
        from_others = for_slack and not turn.owner_only
        if from_others:  # the guard already checked the daily cap; the limits go in front, whatever was asked
            prompt = bridge.task_prefix(key) + prompt
            title, origin = f"[Slack] {title or args['prompt'].strip().splitlines()[0]}", "slack"
        s = console.create_task(prompt, path, title=title, model=args.get("model"), backend=args.get("backend"),
                                permission_mode=args.get("permission_mode"), origin=origin,
                                instruction=acting()["instruction"], actor=acting()["actor"] if from_others else None)
        out = {"task_id": s.id, "title": s.task["title"], "project": Path(path).name, "cwd": path,
               "backend": s.backend.name, "note": tr("started in a new window")}
        if turn and bridge:  # from a Slack window: it hears when the task stops, not the manager
            bridge.add_child(s.id, manager.id, turn.owner_only)
        if for_slack:
            bridge.note_dispatch(key, s.id, s.task["title"], from_others)
            out["note"] += tr("; you will be told when it stops: review it and post the conclusion to this conversation (one reply per stop)")
        return _ok(out)

    @tool("list_tasks", tr("List tasks and their state. filter: active=running or waiting for approval, needs_decision=turn stopped and waiting for the user, done=ended, all=everything (default: everything not ended)."),
          _schema(filter={"type": "string", "enum": ["active", "needs_decision", "done", "all", "open"]},
                  project={"type": "string", "description": tr("project name substring")}))
    async def list_tasks(args):
        groups = {"active": ACTIVE, "needs_decision": ("idle", "interrupted", "error"), "done": ("done",)}
        f = args.get("filter") or "open"
        proj = (args.get("project") or "").lower()
        rows = []
        for t in console.tasks():
            if f == "open" and t["status"] == "done":
                continue
            if f in groups and t["status"] not in groups[f]:
                continue
            if proj and proj not in Path(t["cwd"]).name.lower():
                continue
            rows.append(_task_row(t))
        return _ok(rows[:60])

    @tool("task_status", tr("A task's progress in detail: state, todo list, recent actions, its last reply."),
          _schema(["task_id"], task_id={"type": "string"},
                  recent={"type": "integer", "description": tr("how many recent events, default 15")}))
    async def task_status(args):
        s = target(args["task_id"])
        if not s:
            return _err(tr("No such task"))
        events = console.store.recent_events(s.id, 80)
        last_text = next((e["text"] for e in reversed(events) if e["type"] == "text" and not e.get("parent")), None)
        return _ok({**_task_row(s.task), "cwd": s.task["cwd"], "end_reason": s.task.get("end_reason"),
                    "pending_permissions": len(s.pending), "todos": s.todos,
                    "last_reply": last_text or "",
                    "recent": [brief(e) for e in events[-(args.get("recent") or 15):]]})

    @tool("message_task", tr("Send a task a message. While it runs, the message is appended (it sees it at its next step); correct=true interrupts first and redirects with this message; on a stopped task it continues the conversation."),
          _schema(["task_id", "text"], task_id={"type": "string"}, text={"type": "string"},
                  correct={"type": "boolean", "description": tr("interrupt the current turn first, then send this correction")}))
    async def message_task(args):
        s = target(args["task_id"])
        if not s:
            return _err(tr("No such task"))
        before = s.status
        console.spawn(s.send(args["text"], bool(args.get("correct")), **acting()))
        return _ok({"ok": True, "status_before": before})

    @tool("interrupt_task", tr("Interrupt a task's running turn (the session is kept and can continue later)."),
          _schema(["task_id"], task_id={"type": "string"}))
    async def interrupt_task(args):
        s = target(args["task_id"])
        if not s:
            return _err(tr("No such task"))
        if s.status not in ACTIVE:
            return _ok({"ok": False, "note": tr("the task is {status}; nothing to interrupt", status=s.status)})
        console.spawn(s.interrupt(**acting()))
        return _ok({"ok": True})

    @tool("end_task", tr("End a task (interrupt, close the session, mark it ended). Say clearly in reason why it ends."),
          _schema(["task_id", "reason"], task_id={"type": "string"}, reason={"type": "string"}))
    async def end_task(args):
        s = target(args["task_id"])
        if not s:
            return _err(tr("No such task"))
        console.spawn(s.end(args["reason"], **acting()))
        return _ok({"ok": True, "note": tr("the task wraps up first (updating its project memory), then closes")})

    @tool("task_decisions", tr("The decisions made on tasks (end / continue / correct / interrupt / approve…, who made them, the task's state at the time), to learn how the user judges. Without task_id: the latest across all tasks. actor: user = the user in the task window, manager = you on the user's word in the main window, auto = you on your own during supervision."),
          _schema(task_id={"type": "string"}, limit={"type": "integer", "description": tr("default 40")}))
    async def task_decisions(args):
        rows = console.store.decisions(args.get("limit") or 40, args.get("task_id"))
        out = []
        for d in reversed(rows):
            ctx = d["context"]
            todos = ctx.get("todos") or []
            out.append({
                "task_id": d["task_id"], "time": time.strftime("%m-%d %H:%M", time.localtime(d["ts"])),
                "actor": d["actor"], "action": d["action"], "text": d["text"],
                "project": Path(d["cwd"] or "").name, "status_before": d["status_before"],
                "instruction": ctx.get("instruction"), "turn_elapsed_s": ctx.get("turn_elapsed_s"),
                "turns": ctx.get("turns"), "cost_usd": ctx.get("cost_usd"),
                "todos_done": f"{sum(t.get('status') == 'completed' for t in todos)}/{len(todos)}" if todos else None,
                "recent": (ctx.get("recent") or [])[-5:],
            })
        return _ok(out)

    @tool("project_history", tr("What earlier learnMe tasks did in a project: what they did, the outcome, why they ended. Check before dispatching."),
          _schema(["project"], project={"type": "string", "description": tr("project name or path")},
                  limit={"type": "integer", "description": tr("default 15")}))
    async def project_history(args):
        path, candidates = console.resolve_project(args["project"])
        if not path:
            return _err(tr("No such project; candidates: {names}", names=", ".join(Path(c).name for c in candidates) or "-"))
        rows = []
        for t in console.store.project_tasks(path, args.get("limit") or 15):
            rows.append({**_task_row(t), "created": time.strftime("%m-%d %H:%M", time.localtime(t["created_at"])),
                         "prompt": (console.store.first_prompt(t["id"]) or "")[:300],
                         "end_reason": t.get("end_reason")})
        return _ok({"project": path, "tasks": rows})

    @tool("recall", tr("Search across memory: every memory file (profile, your memory, each project's memory), the decision log and past tasks, by keyword. Separate keywords with spaces; all must match."),
          _schema(["query"], query={"type": "string"},
                  limit={"type": "integer", "description": tr("max rows per kind, default 8")}))
    async def recall(args):
        q, n = args["query"], args.get("limit") or 8
        decisions = [{"task": d.get("title"), "task_id": d["task_id"], "actor": d["actor"], "action": d["action"],
                      "text": (d["text"] or "")[:200], "time": time.strftime("%m-%d", time.localtime(d["ts"]))}
                     for d in console.store.search_decisions(q, n)]
        tasks = [{**_task_row(t), "end_reason": t.get("end_reason")} for t in console.store.search_tasks(q, n)]
        return _ok({"memory": console.memory.search(q, n), "decisions": decisions, "tasks": tasks})

    @tool("memory_changes", tr("Recent memory changes: which file, who changed it (user = the user by hand or by revert, manager = you, task = a task, external = outside learnMe), and the diff."),
          _schema(limit={"type": "integer", "description": tr("default 20")}))
    async def memory_changes(args):
        rows = console.memory.history(args.get("limit") or 20)
        return _ok([{"time": time.strftime("%m-%d %H:%M", time.localtime(r["ts"])), "actor": r["actor"],
                     "file": f"{r['scope_label']} / {r['name']}", "kind": r["kind"], "task_id": r["task_id"],
                     "diff": r["diff"][:1500]} for r in rows])

    @tool("restructure_memory", tr("Dispatch a task into a project to layer its agent memory: MEMORY.md keeps only standing rules and a topic index, details go to topic-<topic>.md, stale state is archived, no knowledge is deleted. Only with the user's consent."),
          _schema(["project"], project={"type": "string", "description": tr("project name or path")}))
    async def restructure_memory(args):
        path, candidates = console.resolve_project(args["project"])
        if not path:
            return _err(tr("No such project; candidates: {names}", names=", ".join(Path(c).name for c in candidates) or "-"))
        try:
            s = console.restructure_memory(console.memory.scope_of_project(path), origin="manager",
                                           instruction=acting()["instruction"])
        except ValueError as e:
            return _err(str(e))
        return _ok({"task_id": s.id, "title": s.task["title"], "note": tr("started in a new window")})

    @tool("search_history", tr("Search past conversations. source=windows: learnMe windows (ended ones too); source=sessions: every Claude Code session on this machine (editor and terminal). Separate keywords with spaces."),
          _schema(query={"type": "string"},
                  source={"type": "string", "enum": ["windows", "sessions"], "description": tr("default sessions")},
                  limit={"type": "integer", "description": tr("default 10")}))
    async def search_history(args):
        q, n = args.get("query") or "", args.get("limit") or 10
        if args.get("source") == "windows":
            rows = console.history_windows(q)[:n]
            return _ok([{**_task_row(t), "preview": t.get("preview"), "end_reason": t.get("end_reason")} for t in rows])
        rows = (await console.history_sessions(q))[:n]
        return _ok([{"path": r["path"], "title": r["title"], "project": r["project"], "ended": r["ended"],
                     "entrypoint": r["entrypoint"], "prompts": r["prompts"],
                     "first_prompt": (r["first_prompt"] or "")[:200], "last_prompt": (r["last_prompt"] or "")[:200]}
                    for r in rows])

    @tool("restore_session", tr("Turn a Claude Code session into a learnMe window: its recent conversation is imported, and the next message continues from its context (as a fork; the original is untouched). path comes from search_history(source=sessions). learnMe's own windows don't need restoring: open them from history or continue them with message_task."),
          _schema(["path"], path={"type": "string"}))
    async def restore_session(args):
        try:
            s = console.restore_session(args["path"])
        except ValueError as e:
            return _err(str(e))
        return _ok({"task_id": s.id, "title": s.task["title"], "note": tr("restored in a new window")})

    if not (slack() or getattr(manager, "is_slack_window", False)):
        return tools

    off = tr("Slack is not enabled (no data/slack.env, or SLACK_MODE=off)")
    slack_where = {"channel": {"type": "string"},
                   "thread_ts": {"type": "string", "description": tr("the thread_ts given in [Slack]; empty for a direct DM reply")}}

    @tool("slack_reply", tr("Reply on Slack for the user, in that conversation (empty thread_ts posts straight into the DM). Conversations handed in by [Slack] and not handled yet are answered under the Slack rules; ones handed to the user wait for the user's \"send it\". Replies containing tokens, passwords or private keys are refused."),
          _schema(["channel", "text"], **slack_where,
                  text={"type": "string", "description": tr("the reply (Slack mrkdwn), in the other person's language")}))
    async def slack_reply(args):
        if not (b := slack()):
            return _err(off)
        try:
            return _ok(await b.reply(args["channel"], args.get("thread_ts"), args["text"], by_user=by_user(),
                                     instruction=manager.last_user_text if manager.trigger == "user" else None))
        except ReplyRefused as e:
            return _err(str(e))
        except Exception as e:  # noqa: BLE001 - report Slack API errors to the agent
            return _err(tr("Send failed: {e}", e=f"{type(e).__name__}: {e}"))

    @tool("slack_ask_approval", tr("For high-impact requests: @mention the owner in that thread with what would be done and how big the impact is, and notify the main window. Only the owner's own reply in the thread (no @mention needed) or confirmation in the main window lets it go ahead."),
          _schema(["channel", "text", "plan", "impact"], **slack_where,
                  text={"type": "string", "description": tr("what to post in the thread, in the other person's language. The system @mentions the owner at the start; don't add it yourself")},
                  plan={"type": "string", "description": tr("what will be done once approved")},
                  impact={"type": "string", "description": tr("how big the impact is: scope, whether it is reversible")}))
    async def slack_ask_approval(args):
        if not (b := slack()):
            return _err(off)
        try:
            return _ok(await b.ask_approval(args["channel"], args.get("thread_ts"), args["text"], args["plan"],
                                            args["impact"], by_user=by_user()))
        except ReplyRefused as e:
            return _err(str(e))
        except Exception as e:  # noqa: BLE001
            return _err(tr("Send failed: {e}", e=f"{type(e).__name__}: {e}"))

    @tool("slack_triage", tr("For a [Slack] conversation you won't answer yourself: escalate = hand it to the user (with a suggested draft reply), ignore = needs no reply (a plain notice, chat between others, already answered)."),
          _schema(["channel", "action", "reason"], **slack_where,
                  action={"type": "string", "enum": ["escalate", "ignore"]}, reason={"type": "string"},
                  draft={"type": "string", "description": tr("for escalate: the reply you suggest")}))
    async def slack_triage(args):
        if not (b := slack()):
            return _err(off)
        try:
            return _ok(b.triage(args["channel"], args.get("thread_ts"), args["action"], args["reason"],
                                args.get("draft")))
        except ReplyRefused as e:
            return _err(str(e))

    @tool("slack_thread", tr("Read Slack context: with ts, that thread (its first message and the latest replies); without ts, the latest messages of this conversation."),
          _schema(["channel"], channel={"type": "string"}, ts={"type": "string"},
                  limit={"type": "integer", "description": tr("default 30")}))
    async def slack_thread(args):
        if not (b := slack()):
            return _err(off)
        try:
            return _ok(await b.thread(args["channel"], args.get("ts"), args.get("limit") or 30))
        except Exception as e:  # noqa: BLE001
            return _err(tr("Read failed: {e}", e=f"{type(e).__name__}: {e}"))

    @tool("slack_update", tr("Edit a message the bot already posted in this conversation (chat.update). Only on the owner's word; the edit is logged and the main window notified. Mentions added in an edit don't notify anyone: to get the owner's decision, post a new message with slack_ask_approval."),
          _schema(["channel", "ts", "text"], **slack_where,
                  ts={"type": "string", "description": tr("ts of the message to edit: from slack_reply, or the bot's own message in slack_thread")},
                  text={"type": "string", "description": tr("the full new text")}))
    async def slack_update(args):
        if not (b := slack()):
            return _err(off)
        key = (args["channel"], args.get("thread_ts") or None)
        if (turn := manager.slack_turn) and key not in turn.keys:
            return _err(tr("Only messages in this window's conversation can be edited"))
        try:
            return _ok(await b.update(args["channel"], args.get("thread_ts"), args["ts"], args["text"], by_user=by_user(),
                                      instruction=manager.last_user_text if manager.trigger == "user" else None))
        except ReplyRefused as e:
            return _err(str(e))
        except Exception as e:  # noqa: BLE001
            return _err(tr("Edit failed: {e}", e=f"{type(e).__name__}: {e}"))

    @tool("slack_pending", tr("The Slack conversations waiting for the user: handed to the user (with a suggested reply) and waiting for approval (with plan and impact), and the window handling each. When the user says \"send it\" or \"approve\" in the main window, find the window here and forward their words with message_task."),
          _schema())
    async def slack_pending(args):
        if not (b := slack()):
            return _err(off)
        return _ok(b.waiting_on_user())

    if manager.id == MANAGER_ID:
        @tool("notify_owner", tr("Send the owner a Slack DM on your own (their DM with the assistant bot), in any turn. For results they asked to hear about: after a task finishes and you reviewed it. Short, conclusion first. At most {n} per hour; tokens, passwords and private keys are refused; what you sent is logged and shown in the main window. Not in a turn on their own DM: that turn's text is posted back automatically.", n=NOTES_PER_HOUR),
              _schema(["text"], text={"type": "string", "description": tr("the message (Slack mrkdwn)")}))
        async def notify_owner(args):
            if not (b := slack()):
                return _err(off)
            try:
                return _ok(await b.notify_owner(args["text"], instruction=acting()["instruction"]))
            except ReplyRefused as e:
                return _err(str(e))
            except Exception as e:  # noqa: BLE001
                return _err(tr("Send failed: {e}", e=f"{type(e).__name__}: {e}"))

    return tools


def memory_index() -> str:
    """The head of the manager's MEMORY.md, the part that goes into every prompt."""
    path = CONFIG.manager_memory_dir / "MEMORY.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    text = "\n".join(text.splitlines()[:MEMORY_INDEX_LINES])[:MEMORY_INDEX_CHARS].strip()
    return f"{P.MANAGER_MEMORY_HEADER.format(path=path)}\n{text}" if text else ""


class ManagerSession(TaskSession):
    compact_rules = P.MANAGER_COMPACT_RULES
    is_manager = True

    def __init__(self, task: dict, store, publish, console: "Console"):
        super().__init__(task, store, publish)
        self.console = console
        self.last_user_text: str | None = None
        self.trigger = "user"  # who started the turn being processed: "user" or "system"
        self.slack_turn: SlackTurn | None = None  # always None here; a SlackWindow's is its current turn
        CONFIG.manager_memory_dir.mkdir(parents=True, exist_ok=True)

    @property
    def flush_prompt(self) -> str:
        return P.MANAGER_FLUSH.format(habits=HABITS_FILE, judgment=JUDGMENT_FILE, memory_dir=CONFIG.manager_memory_dir)

    def instructions(self) -> str:
        names = ", ".join(b["name"] for b in backends.overview() if not b["problems"]) or backends.default_name()
        manager = P.MANAGER.format(habits=HABITS_FILE, judgment=JUDGMENT_FILE, profile_dir=PROFILE_DIR,
                                   memory_dir=CONFIG.manager_memory_dir, backends=names,
                                   default_backend=backends.default_name())
        parts = [manager]
        if bridge := getattr(self.console, "slack", None):
            parts.append(P.MANAGER_SLACK.format(owner=bridge.owner_label, notes_per_hour=NOTES_PER_HOUR))
        if JUDGMENT_FILE.exists():
            parts.append(JUDGMENT_FILE.read_text(encoding="utf-8").strip())
        parts += [memory_index(), habits()]
        return "\n\n".join(p for p in parts if p)

    def tools(self) -> list[ToolDef]:
        return build_tools(self.console, self)

    def allowed_tools(self) -> list[str] | None:
        return [f"mcp__{MCP_SERVER}__{t.name}" for t in self.tools()]

    def record(self, *args, **kwargs) -> None:
        """The main chat itself is not a task being judged."""

    async def send(self, text: str, correct: bool = False, actor: str = "user",
                   instruction: str | None = None) -> None:
        self.trigger = "system" if actor == "system" else "user"
        if actor == "user":
            self.last_user_text = text
        await super().send(text, correct, actor, instruction)

    async def new_conversation(self) -> None:
        async with self.lock:
            if self.status in ACTIVE:
                await self._interrupt_and_settle()
            await self.close()
            self.update(session_id=None, status="idle", ctx_tokens=None)
            self.emit("clear")  # the UI drops everything before this; the events stay in the DB
            self.emit("notice", text=tr("—— Conversation cleared; the manager starts fresh (old messages stay searchable) ——"))
