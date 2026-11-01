"""OpenAI Codex, through `codex app-server` (JSON-RPC over stdio, one process per window).

Protocol reference: https://developers.openai.com/codex/app-server, and the schema your installed
CLI generates with `codex app-server generate-json-schema --out DIR`. Messages are one JSON object
per line without the "jsonrpc" field.

    initialize / initialized             handshake
    thread/start | thread/resume | thread/fork      -> {thread: {id}, model, cwd, ...}
    turn/start {threadId, input}         a new turn; turn/steer adds to the running one
    turn/interrupt {threadId, turnId}    the turn then completes with status "interrupted"
    thread/compact/start {threadId}
    notifications: turn/started, item/started, item/completed, item/agentMessage/delta,
                   item/reasoning/*Delta, turn/plan/updated, thread/tokenUsage/updated,
                   thread/compacted, turn/completed, error
    server requests: item/commandExecution/requestApproval, item/fileChange/requestApproval,
                     item/tool/requestUserInput, ... (answered by learnMe)

learnMe's tools reach Codex as an MCP server over HTTP (config `mcp_servers.<name>.url`), with the
window's bearer token passed in the app-server's environment.

Degraded compared with Claude: no dollar cost (tokens only, unless prices are configured);
/compact can't take learnMe's summary rules or a focus; guards (pre_tool) only see the commands
and file changes Codex asks approval for, which depends on the approval policy, plus every call
to learnMe's own tools (checked in the MCP endpoint); Claude Code history can't be resumed here.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any

from ..config import CONFIG
from .base import AgentConnection, Backend, Capabilities, SessionSpec, ToolPermission

log = logging.getLogger("learnme.backend.codex")

TOKEN_ENV = "LEARNME_MCP_TOKEN"
LINE_LIMIT = 64 * 1024 * 1024  # an item can carry a whole command's output
REQUEST_TIMEOUT_S = 120

# Claude Code permission modes -> (approval policy, sandbox). None keeps ~/.codex/config.toml's.
PERMISSIONS = {
    "default": ("untrusted", "workspace-write"),
    "acceptEdits": ("on-request", "workspace-write"),
    "plan": ("on-request", "read-only"),
    "bypassPermissions": ("never", "danger-full-access"),
}
TURN_STATUS = {"completed": "idle", "interrupted": "interrupted", "failed": "error"}
PLAN_STATUS = {"pending": "pending", "inProgress": "in_progress", "in_progress": "in_progress",
               "completed": "completed"}


def api_key_from_env() -> str:
    return os.environ.get("CODEX_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""


def codex_home(backend_home: str | None) -> Path:
    return Path(os.path.expanduser(backend_home or os.environ.get("CODEX_HOME") or "~/.codex"))


class RpcError(Exception):
    def __init__(self, error: dict):
        super().__init__(error.get("message") or json.dumps(error))
        self.error = error


class CodexBackend(Backend):
    name = "codex"
    capabilities = Capabilities(resume=True, fork=True, steer=True, interrupt=True, compact=True,
                                compact_rules=False, permission_prompts=True, pre_tool_hook=False,
                                context_usage=True, cost_usd=False, thinking=True, claude_history=False)

    def opt(self, key: str, default: Any = None) -> Any:
        return CONFIG.backend_opt(self.name, key, default)

    def connect(self, spec: SessionSpec) -> AgentConnection:
        return CodexConnection(spec, self)

    def command(self) -> str:
        return self.opt("command") or "codex"

    def default_model(self, manager: bool) -> str | None:
        return self.opt("manager_model" if manager else "model") or self.opt("model") or None

    def default_effort(self, manager: bool) -> str | None:
        return self.opt("manager_effort" if manager else "effort") or None

    def models(self) -> list[str]:
        return list(self.opt("models") or [])

    def problems(self) -> list[str]:
        if not shutil.which(self.command()):
            return [f"the Codex CLI ({self.command()!r}) is not on PATH; install it with `npm i -g @openai/codex`"]
        if not (codex_home(self.opt("home")) / "auth.json").exists() and not api_key_from_env():
            return ["Codex is not logged in: run `codex login`, or set OPENAI_API_KEY"]
        return []


class CodexConnection(AgentConnection):
    def __init__(self, spec: SessionSpec, backend: CodexBackend):
        super().__init__(spec)
        self.backend = backend
        self.proc: asyncio.subprocess.Process | None = None
        self.tasks: set[asyncio.Task] = set()
        self.next_id = 0
        self.waiters: dict[int, asyncio.Future] = {}
        self.closed = asyncio.Event()
        self.stopping = False
        self.stderr_tail: collections.deque[str] = collections.deque(maxlen=40)
        self.thread_id: str | None = None
        self.turn_id: str | None = None
        self.finished_turns: set[str] = set()
        self.turn_steps = 0
        self.compacting = False
        self.items: dict[str, dict] = {}   # item id -> item, for approvals that only name the item
        self.ctx: tuple[int, int | None] | None = None
        self.spent = 0.0
        self.priced = False
        self.plan_seq = 0

    # --- process and JSON-RPC plumbing ---

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None and not self.closed.is_set()

    def _spawn(self, coro) -> asyncio.Task:
        t = asyncio.create_task(coro)
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)
        return t

    async def _write(self, msg: dict) -> None:
        if not self.alive:
            raise ConnectionError("codex app-server is not running")
        self.proc.stdin.write((json.dumps(msg, ensure_ascii=False) + "\n").encode())
        await self.proc.stdin.drain()

    async def request(self, method: str, params: dict, timeout: float = REQUEST_TIMEOUT_S) -> Any:
        self.next_id += 1
        rid = self.next_id
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self.waiters[rid] = fut
        try:
            await self._write({"id": rid, "method": method, "params": params})
            return await asyncio.wait_for(fut, timeout)
        finally:
            self.waiters.pop(rid, None)

    async def notify(self, method: str, params: dict) -> None:
        await self._write({"method": method, "params": params})

    async def _respond(self, rid: Any, result: dict | None = None, error: dict | None = None) -> None:
        with contextlib.suppress(ConnectionError, BrokenPipeError, ConnectionResetError):
            await self._write({"id": rid, "error": error} if error else {"id": rid, "result": result or {}})

    async def _read_stdout(self) -> None:
        try:
            while line := await self.proc.stdout.readline():
                try:
                    msg = json.loads(line)
                except ValueError:
                    log.warning("codex: not JSON: %.200s", line)
                    continue
                try:
                    self._dispatch(msg)
                except Exception:  # noqa: BLE001 - one bad message must not kill the window
                    log.exception("codex: failed to handle %.200s", line)
        finally:
            code = await self.proc.wait()
            if not self.stopping:
                tail = "\n".join(self.stderr_tail)
                self.exit_error = RuntimeError(f"codex app-server exited with code {code}" + (f": {tail[-800:]}" if tail else ""))
            for fut in self.waiters.values():
                if not fut.done():
                    fut.set_exception(ConnectionError("codex app-server exited"))
            self.closed.set()

    async def _read_stderr(self) -> None:
        while line := await self.proc.stderr.readline():
            text = line.decode(errors="replace").rstrip()
            self.stderr_tail.append(text)
            self.spec.on_stderr(text)

    def _dispatch(self, msg: dict) -> None:
        if "method" in msg and "id" in msg:      # a request from the server: answer it
            self._spawn(self._server_request(msg["id"], msg["method"], msg.get("params") or {}))
        elif "method" in msg:                    # a notification
            self._notification(msg["method"], msg.get("params") or {})
        elif (fut := self.waiters.get(msg.get("id"))) and not fut.done():
            if "error" in msg:
                fut.set_exception(RpcError(msg["error"] or {}))
            else:
                fut.set_result(msg.get("result"))

    # --- lifecycle ---

    def _thread_params(self) -> dict:
        s = self.spec
        p: dict[str, Any] = {"cwd": s.cwd}
        if s.model:
            p["model"] = s.model
        if s.instructions.strip():
            p["developerInstructions"] = s.instructions
        approval, sandbox = PERMISSIONS.get(s.permission_mode or "", (None, None))
        if approval:
            p["approvalPolicy"] = approval
            p["sandbox"] = sandbox
        config: dict[str, Any] = {}
        if s.mcp:
            config[f"mcp_servers.{s.mcp.name}"] = {"url": s.mcp.url, "bearer_token_env_var": TOKEN_ENV}
        config.update(self.backend.opt("config") or {})
        if config:
            p["config"] = config
        return p

    async def start(self) -> None:
        env = {**os.environ}
        if self.spec.mcp:
            env[TOKEN_ENV] = self.spec.mcp.token
        if home := self.backend.opt("home"):
            env["CODEX_HOME"] = os.path.expanduser(home)
        cmd = shutil.which(self.backend.command()) or self.backend.command()
        self.proc = await asyncio.create_subprocess_exec(
            cmd, "app-server", stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, cwd=self.spec.cwd, env=env, limit=LINE_LIMIT)
        self._spawn(self._read_stdout())
        self._spawn(self._read_stderr())
        try:
            await self.request("initialize", {"clientInfo": {"name": "learnme", "title": "learnMe", "version": "0.1.0"}})
            await self.notify("initialized", {})
            await self._login_with_env_key()
            params = self._thread_params()
            if self.spec.resume:
                r = await self.request("thread/resume", {"threadId": self.spec.resume, **params})
            elif self.spec.fork_from:
                r = await self.request("thread/fork", {"threadId": self.spec.fork_from, **params})
            else:
                r = await self.request("thread/start", params)
        except BaseException:
            await self.close()
            raise
        self.thread_id = r["thread"]["id"]
        self.emit("init", session_id=self.thread_id, model=r.get("model"),
                  permission_mode=self.spec.permission_mode or r.get("approvalPolicy"), cwd=r.get("cwd"))

    async def _login_with_env_key(self) -> None:
        """The app-server only uses its own stored login (`codex login`). With none stored and an API key in
        the environment, log in with that key, as `codex login --with-api-key` would (it is saved in CODEX_HOME)."""
        key = api_key_from_env()
        if not key:
            return
        try:
            acct = await self.request("account/read", {})
            if acct.get("account") or not acct.get("requiresOpenaiAuth"):
                return
            await self.request("account/login/start", {"type": "apiKey", "apiKey": key})
            log.info("codex: logged in with the API key from the environment")
        except RpcError as e:  # an older app-server without these methods: leave auth to the CLI
            log.info("codex: could not log in with the environment key: %s", e)

    async def wait_closed(self) -> None:
        await self.closed.wait()

    async def close(self) -> None:
        self.stopping = True
        proc = self.proc
        if proc and proc.returncode is None:
            with contextlib.suppress(Exception):
                proc.stdin.close()
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except TimeoutError:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), 5)
                except TimeoutError:
                    proc.kill()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.closed.wait(), 5)
        for t in list(self.tasks):
            t.cancel()

    # --- actions ---

    async def query(self, text: str) -> None:
        user_input = [{"type": "text", "text": text}]
        if self.turn_id:
            try:
                await self.request("turn/steer", {"threadId": self.thread_id, "expectedTurnId": self.turn_id,
                                                  "input": user_input})
                return
            except RpcError:
                log.info("codex: turn %s ended before steer; starting a new turn", self.turn_id)
        params: dict[str, Any] = {"threadId": self.thread_id, "input": user_input}
        if self.spec.effort:
            params["effort"] = self.spec.effort
        r = await self.request("turn/start", params)
        tid = (r.get("turn") or {}).get("id")
        if tid and tid not in self.finished_turns:
            self._turn_began(tid)

    async def interrupt(self) -> None:
        if self.turn_id:
            await self.request("turn/interrupt", {"threadId": self.thread_id, "turnId": self.turn_id})
        else:  # nothing running on Codex's side: settle the window anyway
            self.emit("result", status="interrupted", subtype="interrupted", reason="aborted", duration_ms=None,
                      num_turns=0, cost_usd=self._cost(), errors=None, text=None)

    async def compact(self, focus: str | None) -> None:
        self.compacting = True
        await self.request("thread/compact/start", {"threadId": self.thread_id})

    async def context_usage(self) -> tuple[int, int | None] | None:
        return self.ctx

    # --- server -> learnMe ---

    def _turn_began(self, tid: str) -> None:
        if self.turn_id != tid:
            self.turn_id, self.turn_steps, self.turn_t0 = tid, 0, time.time()

    def _cost(self) -> float | None:
        return self.spent if self.priced else None

    def _price(self, last: dict) -> None:
        price_in, price_out = self.backend.opt("price_input_per_mtok"), self.backend.opt("price_output_per_mtok")
        if price_in is None or price_out is None:
            return
        price_hit = self.backend.opt("price_cache_hit_per_mtok", price_in)
        cached = last.get("cachedInputTokens") or 0
        fresh = max((last.get("inputTokens") or 0) - cached, 0)
        self.spent += (fresh * price_in + cached * price_hit + (last.get("outputTokens") or 0) * price_out) / 1e6
        self.priced = True

    def _notification(self, method: str, p: dict) -> None:
        if p.get("threadId") and self.thread_id and p["threadId"] != self.thread_id:
            return  # a sub-agent's thread; the main thread's items are what the window shows
        if method == "turn/started":
            self._turn_began((p.get("turn") or {}).get("id"))
        elif method == "item/agentMessage/delta":
            self.emit("delta", kind="text", text=p.get("delta") or "", parent=None)
        elif method in ("item/reasoning/summaryTextDelta", "item/reasoning/textDelta"):
            self.emit("delta", kind="thinking", text=p.get("delta") or "", parent=None)
        elif method == "item/started":
            self._item_started(p.get("item") or {})
        elif method == "item/completed":
            self._item_completed(p.get("item") or {})
        elif method == "turn/plan/updated":
            self.plan_seq += 1
            todos = [{"content": s.get("step"), "activeForm": s.get("step"),
                      "status": PLAN_STATUS.get(s.get("status"), "pending")} for s in p.get("plan") or []]
            self.emit("tool_use", id=f"plan-{self.plan_seq}", name="TodoWrite", input={"todos": todos}, parent=None)
        elif method == "thread/tokenUsage/updated":
            usage = p.get("tokenUsage") or {}
            last = usage.get("last") or {}
            self._price(last)
            self.ctx = ((last.get("inputTokens") or 0) + (last.get("outputTokens") or 0),
                        usage.get("modelContextWindow"))
            self.emit("context", tokens=self.ctx[0], window=self.ctx[1])
        elif method == "thread/compacted":
            self.emit("compact", trigger="manual" if self.compacting else "auto", pre_tokens=None, duration_ms=None)
        elif method == "turn/completed":
            self._turn_completed(p.get("turn") or {})
        elif method == "error":
            if not p.get("willRetry"):
                err = p.get("error") or {}
                self.emit("error", message=err.get("message") or "Codex error", detail=err.get("additionalDetails"))
        elif method in ("warning", "configWarning", "deprecationNotice", "guardianWarning"):
            log.info("codex %s: %s", method, json.dumps(p, ensure_ascii=False)[:400])
        elif method == "mcpServer/startupStatus/updated" and p.get("status") == "failed":
            log.warning("codex MCP server %s failed to start: %s", p.get("name"), p.get("error"))
            if self.spec.mcp and p.get("name") == self.spec.mcp.name:
                self.emit("error", message=f"learnMe tools unavailable: {p.get('error') or 'MCP startup failed'}",
                          detail=None)

    def _item_started(self, item: dict) -> None:
        iid, t = item.get("id"), item.get("type")
        self.items[iid] = item
        if t == "commandExecution":
            self.emit("tool_use", id=iid, name="Bash", input={"command": item.get("command"), "cwd": item.get("cwd")},
                      parent=None)
        elif t == "fileChange":
            self.emit("tool_use", id=iid, name="ApplyPatch", input={"changes": item.get("changes") or []}, parent=None)
        elif t == "mcpToolCall":
            args = item.get("arguments")
            self.emit("tool_use", id=iid, name=f"mcp__{item.get('server')}__{item.get('tool')}",
                      input=args if isinstance(args, dict) else {"arguments": args}, parent=None)
        elif t == "webSearch":
            self.emit("tool_use", id=iid, name="WebSearch", input={"query": item.get("query")}, parent=None)

    def _item_completed(self, item: dict) -> None:
        iid, t = item.get("id"), item.get("type")
        self.items.pop(iid, None)
        if t != "userMessage":
            self.turn_steps += 1
        if t == "agentMessage" and (item.get("text") or "").strip():
            self.emit("text", text=item["text"], parent=None)
        elif t == "reasoning":
            text = "\n".join(item.get("summary") or []) or "\n".join(item.get("content") or [])
            if text.strip():
                self.emit("thinking", text=text, parent=None)
        elif t == "commandExecution":
            failed = item.get("status") in ("failed", "declined") or item.get("exitCode") not in (0, None)
            out = item.get("aggregatedOutput") or ""
            if item.get("status") == "declined":
                out = out or "declined"
            self.emit("tool_result", tool_use_id=iid, content=out, is_error=failed, parent=None)
        elif t == "fileChange":
            self.emit("tool_result", tool_use_id=iid, content=item.get("status") or "",
                      is_error=item.get("status") not in ("completed", None), parent=None)
        elif t == "mcpToolCall":
            err = item.get("error")
            content = err.get("message") if err else ((item.get("result") or {}).get("content") or [])
            self.emit("tool_result", tool_use_id=iid, content=content, is_error=bool(err), parent=None)
        elif t == "webSearch":
            self.emit("tool_result", tool_use_id=iid, content="", is_error=False, parent=None)

    def _turn_completed(self, turn: dict) -> None:
        tid = turn.get("id")
        if tid:
            self.finished_turns.add(tid)
        if tid and self.turn_id and tid != self.turn_id:
            return
        started = getattr(self, "turn_t0", None)
        self.turn_id = None
        err = turn.get("error") or {}
        status = TURN_STATUS.get(turn.get("status"), "error")
        self.compacting = False
        self.emit("result", status=status, subtype=turn.get("status"), reason=turn.get("status") or "",
                  duration_ms=turn.get("durationMs") or (round((time.time() - started) * 1000) if started else None),
                  num_turns=self.turn_steps, cost_usd=self._cost(), errors=[err["message"]] if err.get("message") else None,
                  text=err.get("message"))

    async def _server_request(self, rid: Any, method: str, p: dict) -> None:
        try:
            if method == "item/commandExecution/requestApproval":
                inp = {"command": p.get("command"), "cwd": p.get("cwd")}
                decision = await self._approve("Bash", inp, p.get("reason"))
                await self._respond(rid, {"decision": decision})
            elif method == "item/fileChange/requestApproval":
                item = self.items.get(p.get("itemId")) or {}
                inp = {"changes": item.get("changes") or [], **({"grant_root": p["grantRoot"]} if p.get("grantRoot") else {})}
                decision = await self._approve("ApplyPatch", inp, p.get("reason"))
                await self._respond(rid, {"decision": decision})
            elif method == "item/tool/requestUserInput":
                await self._respond(rid, {"answers": await self._ask(p.get("questions") or [])})
            elif method == "mcpServer/elicitation/request":
                await self._respond(rid, {"action": "decline"})
            elif method == "item/permissions/requestApproval":
                await self._respond(rid, {"permissions": {}})  # grant nothing beyond the sandbox
            elif method in ("execCommandApproval", "applyPatchApproval"):  # legacy v1 requests
                await self._respond(rid, {"decision": "denied"})
            else:
                await self._respond(rid, error={"code": -32601, "message": f"learnMe does not handle {method}"})
        except Exception as e:  # noqa: BLE001 - never leave Codex waiting
            log.exception("codex: %s failed", method)
            await self._respond(rid, error={"code": -32603, "message": str(e)})

    async def _approve(self, tool: str, inp: dict, reason: str | None) -> str:
        if self.spec.pre_tool and (refusal := await self.spec.pre_tool(tool, inp)):
            log.info("codex: guard refused %s: %s", tool, refusal)
            return "decline"
        if not self.spec.can_use_tool:
            return "accept"
        d = await self.spec.can_use_tool(ToolPermission(tool=tool, input=inp, title=None, description=reason,
                                                        can_always=True))
        if d.action in ("allow", "answer"):
            return "accept"
        if d.action == "allow_always":
            return "acceptForSession"
        return "cancel" if d.interrupt else "decline"

    async def _ask(self, questions: list[dict]) -> dict:
        """Codex's request_user_input, shown as Claude Code's AskUserQuestion card."""
        if not self.spec.can_use_tool or not questions:
            return {}
        inp = {"questions": [{"question": q.get("question"), "header": q.get("header") or "",
                              "multiSelect": False,
                              "options": [{"label": o.get("label"), "description": o.get("description")}
                                          for o in q.get("options") or []]} for q in questions]}
        d = await self.spec.can_use_tool(ToolPermission(tool="AskUserQuestion", input=inp))
        if d.action != "answer":
            return {}
        given = d.answers or {}
        return {q["id"]: {"answers": [given[q.get("question")]]} for q in questions if given.get(q.get("question"))}
