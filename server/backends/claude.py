"""Claude Code, through the Claude Agent SDK (one ClaudeSDKClient per window).

Everything learnMe does was built on this backend first, so it supports every capability:
streaming, appending to a running turn, interrupt, resume and fork, permission prompts,
PreToolUse guards, /compact with learnMe's summary rules, exact context usage and cost.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from ..config import CONFIG
from .base import AgentConnection, Backend, Capabilities, McpMount, PermissionDecision, SessionSpec, ToolPermission

log = logging.getLogger("learnme.backend.claude")

CONTEXT_KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def context_tokens(usage: dict | None) -> int:
    """Prompt size of one API call, i.e. what the context window holds at that point (as /context counts it)."""
    return sum((usage or {}).get(k) or 0 for k in CONTEXT_KEYS)


def claude_logged_in() -> bool:
    """Best effort: an API key or token in the environment, a cloud provider, or a stored login."""
    env = os.environ
    if any(env.get(k) for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
                                "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX")):
        return True
    if sys.platform == "darwin":
        return True  # the login lives in the macOS keychain, which can't be checked cheaply
    home = Path(env.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    return (home / ".credentials.json").exists()


def sdk_mcp_server(mount: McpMount):
    """learnMe's tools as an in-process MCP server: no network, no token."""
    from claude_agent_sdk import create_sdk_mcp_server, tool

    tools = [tool(t.name, t.description, t.schema)(t.handler) for t in mount.tools]
    return create_sdk_mcp_server(name=mount.name, version="0.1.0", tools=tools)


class ClaudeBackend(Backend):
    name = "claude"
    capabilities = Capabilities(resume=True, fork=True, steer=True, interrupt=True, compact=True,
                                compact_rules=True, permission_prompts=True, pre_tool_hook=True,
                                context_usage=True, cost_usd=True, thinking=True, claude_history=True)

    def connect(self, spec: SessionSpec) -> AgentConnection:
        return ClaudeConnection(spec, self)

    def opt(self, key: str, default: Any = None) -> Any:
        return CONFIG.backend_opt(self.name, key, default)

    def default_model(self, manager: bool) -> str | None:
        return self.opt("manager_model" if manager else "model") or None

    def default_effort(self, manager: bool) -> str | None:
        return self.opt("manager_effort" if manager else "effort") or None

    def models(self) -> list[str]:
        return ["opus", "sonnet", "haiku"]

    def env(self, spec: SessionSpec) -> dict[str, str]:
        """Extra environment for the CLI process."""
        # The manager's memory is learnMe's own (data/manager-memory, put into its prompt on every
        # backend); Claude Code's auto memory for the learnMe folder would be a second, competing one.
        return {"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"} if spec.is_manager else {}

    def in_process_tools(self) -> bool:
        return (self.opt("mcp_transport") or "inprocess") != "http"

    def turn_cost(self, conn: ClaudeConnection, total_cost_usd: float | None, usage: dict | None) -> float | None:
        """Cumulative cost since start(), as the CLI reports it."""
        return total_cost_usd

    def problems(self) -> list[str]:
        try:
            import claude_agent_sdk  # noqa: F401
        except ImportError:
            return ["the claude-agent-sdk Python package is not installed"]
        if not claude_logged_in():
            return ["Claude Code is not logged in: run `claude login` (or `claude setup-token`), or set ANTHROPIC_API_KEY"]
        return []


class ClaudeConnection(AgentConnection):
    def __init__(self, spec: SessionSpec, backend: ClaudeBackend):
        super().__init__(spec)
        self.backend = backend
        self.client: Any = None
        self.owner: asyncio.Task | None = None
        self.stop = asyncio.Event()
        self.expect_summary = False  # the next plain user message is the compaction summary
        self.stderr_tail: collections.deque[str] = collections.deque(maxlen=40)
        self.spent = 0.0  # for backends that price tokens themselves

    # --- options ---

    def options(self):
        from claude_agent_sdk import ClaudeAgentOptions
        from claude_agent_sdk.types import HookMatcher

        s = self.spec
        system_prompt: dict = {"type": "preset", "preset": "claude_code"}
        if s.instructions.strip():
            system_prompt["append"] = s.instructions
        hooks = {"PreCompact": [HookMatcher(hooks=[self._pre_compact])]}
        if s.pre_tool:
            hooks["PreToolUse"] = [HookMatcher(hooks=[self._pre_tool])]
        mcp_servers: dict = {}
        if s.mcp:
            if self.backend.in_process_tools():
                mcp_servers[s.mcp.name] = sdk_mcp_server(s.mcp)
            else:
                mcp_servers[s.mcp.name] = {"type": "http", "url": s.mcp.url,
                                           "headers": {"Authorization": f"Bearer {s.mcp.token}"}}
        opts = ClaudeAgentOptions(
            cwd=s.cwd,
            model=s.model or None,
            permission_mode=s.permission_mode or None,
            resume=s.resume or s.fork_from or None,
            fork_session=not s.resume and bool(s.fork_from),
            system_prompt=system_prompt,
            hooks=hooks,
            include_partial_messages=True,
            can_use_tool=self._can_use_tool if s.can_use_tool else None,
            stderr=self._stderr,
            env=self.backend.env(s),
        )
        if s.effort:
            opts.effort = s.effort
        if mcp_servers:
            opts.mcp_servers = mcp_servers
        if s.allowed_tools:
            opts.allowed_tools = s.allowed_tools
        if cli := self.backend.opt("cli_path") or None:
            opts.cli_path = shutil.which(cli) or cli
        return opts

    def _stderr(self, line: str) -> None:
        self.stderr_tail.append(line)
        self.spec.on_stderr(line)

    # --- lifecycle ---

    @property
    def alive(self) -> bool:
        return self.client is not None and self.owner is not None and not self.owner.done()

    async def start(self) -> None:
        ready: asyncio.Future = asyncio.get_running_loop().create_future()
        self.owner = asyncio.create_task(self._own(ready), name="claude-client")
        await ready

    async def _own(self, ready: asyncio.Future) -> None:
        # connect() and disconnect() must happen in the same task: the SDK holds an anyio task
        # group open between them.
        from claude_agent_sdk import ClaudeSDKClient

        client = ClaudeSDKClient(options=self.options())
        try:
            await client.connect()
        except BaseException as e:
            if not ready.done():
                ready.set_exception(e)
            return
        self.client = client
        ready.set_result(None)
        pump = asyncio.create_task(self._pump(client))
        stopper = asyncio.create_task(self.stop.wait())
        try:
            await asyncio.wait({pump, stopper}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            pump.cancel()
            stopper.cancel()
            try:
                async with asyncio.timeout(10):
                    await client.disconnect()
            except BaseException:  # noqa: BLE001 - best effort teardown
                log.warning("disconnect failed", exc_info=True)
            self.client = None
        if not self.stop.is_set() and pump.done() and not pump.cancelled():
            self.exit_error = pump.exception()

    async def _pump(self, client) -> None:
        async for msg in client.receive_messages():
            try:
                self._handle(msg)
            except Exception:  # noqa: BLE001 - one bad message must not kill the session
                log.exception("failed to handle %s", type(msg).__name__)

    async def wait_closed(self) -> None:
        if self.owner:
            await asyncio.shield(self.owner)

    async def close(self) -> None:
        owner = self.owner
        if owner and not owner.done():
            self.stop.set()
            try:
                await asyncio.wait_for(asyncio.shield(owner), 15)
            except TimeoutError:
                owner.cancel()
        self.client = None

    # --- actions ---

    async def query(self, text: str) -> None:
        await self.client.query(text)

    async def interrupt(self) -> None:
        await self.client.interrupt()

    async def compact(self, focus: str | None) -> None:
        await self.client.query(" ".join(["/compact", *(focus or "").split()]))  # rules come from _pre_compact

    async def context_usage(self) -> tuple[int, int | None] | None:
        if self.client is None:
            return None
        usage = await self.client.get_context_usage()
        return usage["totalTokens"], usage["rawMaxTokens"]

    # --- callbacks from the CLI ---

    async def _pre_compact(self, data: Any, _tool_use_id: str | None, _context: Any) -> dict:
        log.info("compacting (%s)", data.get("trigger"))
        return {"systemMessage": self.spec.compact_rules} if self.spec.compact_rules else {}

    async def _pre_tool(self, data: Any, _tool_use_id: str | None, _context: Any) -> dict:
        reason = await self.spec.pre_tool(data.get("tool_name") or "", data.get("tool_input") or {})
        if not reason:
            return {}
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": reason}}

    async def _can_use_tool(self, tool_name: str, tool_input: dict, ctx: Any):
        from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny

        req = ToolPermission(tool=tool_name, input=tool_input, title=ctx.title, description=ctx.description,
                             can_always=bool(ctx.suggestions), raw=ctx)
        d: PermissionDecision = await self.spec.can_use_tool(req)
        if d.action == "allow":
            return PermissionResultAllow()
        if d.action == "allow_always":
            return PermissionResultAllow(updated_permissions=ctx.suggestions or None)
        if d.action == "answer":
            return PermissionResultAllow(updated_input={**tool_input, "answers": d.answers or {}})
        return PermissionResultDeny(message=d.message or "", interrupt=d.interrupt)

    # --- SDK message -> learnMe events ---

    def _handle(self, m: Any) -> None:
        from claude_agent_sdk.types import (
            AssistantMessage,
            RateLimitEvent,
            ResultMessage,
            StreamEvent,
            SystemMessage,
            TextBlock,
            ThinkingBlock,
            ToolResultBlock,
            ToolUseBlock,
            UserMessage,
        )

        if isinstance(m, StreamEvent):
            ev = m.event
            if ev.get("type") == "message_start" and m.parent_tool_use_id is None:
                self.emit("context", tokens=context_tokens((ev.get("message") or {}).get("usage")), window=None)
            elif ev.get("type") == "content_block_delta":
                d = ev.get("delta") or {}
                kind = {"text_delta": "text", "thinking_delta": "thinking"}.get(d.get("type"))
                if kind:
                    self.emit("delta", kind=kind, text=d.get("text") or d.get("thinking") or "",
                              parent=m.parent_tool_use_id)
            return

        if isinstance(m, SystemMessage):
            if m.subtype == "init":
                self.emit("init", session_id=m.data.get("session_id"), model=m.data.get("model"),
                          permission_mode=m.data.get("permissionMode"), cwd=m.data.get("cwd"))
            elif m.subtype == "status" and m.data.get("status") == "compacting":
                self.emit("compacting")
            elif m.subtype == "compact_boundary":
                meta = m.data.get("compact_metadata") or {}
                self.emit("compact", trigger=meta.get("trigger"), pre_tokens=meta.get("pre_tokens"),
                          duration_ms=meta.get("duration_ms"))
                self.expect_summary = True
            return

        if isinstance(m, AssistantMessage):
            parent = m.parent_tool_use_id
            for b in m.content:
                if isinstance(b, TextBlock) and b.text.strip():
                    self.emit("text", text=b.text, parent=parent)
                elif isinstance(b, ThinkingBlock) and b.thinking.strip():
                    self.emit("thinking", text=b.thinking, parent=parent)
                elif isinstance(b, ToolUseBlock):
                    self.emit("tool_use", id=b.id, name=b.name, input=b.input, parent=parent)
            if m.error:
                self.emit("error", message=f"API error: {m.error}", detail=None)
            return

        if isinstance(m, UserMessage):
            blocks = m.content if isinstance(m.content, list) else [TextBlock(text=m.content)]
            for b in blocks:
                if isinstance(b, ToolResultBlock):
                    self.emit("tool_result", tool_use_id=b.tool_use_id, content=b.content,
                              is_error=bool(b.is_error), parent=m.parent_tool_use_id)
                elif isinstance(b, TextBlock) and self.expect_summary and not b.text.startswith("<"):
                    self.expect_summary = False
                    self.emit("compact_summary", text=b.text)
            return

        if isinstance(m, ResultMessage):
            reason = m.terminal_reason or ""
            status = "interrupted" if reason.startswith("aborted") else "error" if m.is_error else "idle"
            self.expect_summary = False
            self.emit("result", status=status, subtype=m.subtype, reason=reason, duration_ms=m.duration_ms,
                      num_turns=m.num_turns, cost_usd=self.backend.turn_cost(self, m.total_cost_usd, m.usage),
                      errors=m.errors, text=(m.result or "")[:2000] if m.is_error else None)
            return

        if isinstance(m, RateLimitEvent):
            info = m.rate_limit_info
            if getattr(info, "status", None) == "rejected":
                self.emit("rate_limited", resets_at=getattr(info, "resets_at", None))
