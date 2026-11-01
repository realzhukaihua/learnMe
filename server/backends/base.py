"""What learnMe needs from an agent backend, and the events it expects back.

A backend turns a SessionSpec into an AgentConnection: one live agent conversation in one
working directory. The session layer (server/session.py) never imports a vendor SDK; it calls
the connection's methods and handles the flat events the connection emits:

    init          session_id, model, permission_mode, cwd
    context       tokens, window          prompt size at the latest model call, window size if known
    delta         kind ("text" | "thinking"), text, parent     streaming, not persisted
    text          text, parent
    thinking      text, parent
    tool_use      id, name, input, parent
    tool_result   tool_use_id, content, is_error, parent
    compacting                            the backend started summarizing on its own
    compact       trigger, pre_tokens, duration_ms
    compact_summary text
    result        status ("idle" | "interrupted" | "error"), subtype, reason, duration_ms,
                  num_turns, cost_usd (cumulative since start(); None if the backend can't price),
                  errors, text
    error         message, detail
    rate_limited  resets_at

Tool names follow Claude Code's (Bash, Edit, Write, Read, TodoWrite, mcp__<server>__<tool>) so
the UI renders every backend the same way; backends map their own items onto them.
"""

from __future__ import annotations

import abc
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

Event = dict[str, Any]


@dataclass(frozen=True)
class Capabilities:
    """What a backend can do. The session and UI degrade on whatever is False."""
    resume: bool = True              # continue a conversation by its id after a restart
    fork: bool = False               # branch a conversation without touching the original
    steer: bool = False              # add a message to a turn that is still running
    interrupt: bool = True
    compact: bool = False            # summarize the conversation on request
    compact_rules: bool = False      # ... with learnMe's own instructions for the summary
    permission_prompts: bool = False  # ask the user before a tool runs
    pre_tool_hook: bool = False      # learnMe can veto every tool call (guards)
    context_usage: bool = False      # exact context size on request
    cost_usd: bool = False           # reports real dollar cost
    thinking: bool = False           # streams the model's reasoning
    claude_history: bool = False     # can resume sessions found in ~/.claude/projects

    def public(self) -> dict:
        return dict(self.__dict__)


@dataclass
class ToolPermission:
    """A tool call waiting for the user's yes."""
    tool: str
    input: dict
    title: str | None = None
    description: str | None = None
    can_always: bool = False
    raw: Any = None  # the backend's own request object, handed back in the decision


@dataclass
class PermissionDecision:
    action: str                      # "allow" | "allow_always" | "deny" | "answer"
    message: str | None = None
    answers: dict | None = None
    interrupt: bool = False          # deny and stop the turn


@dataclass
class ToolDef:
    """A tool learnMe offers an agent (the manager's dispatch_task, list_tasks, ...).

    `handler(args)` returns MCP-style content: {"content": [{"type": "text", "text": ...}], "is_error"?}.
    """
    name: str
    description: str
    schema: dict
    handler: Callable[[dict], Awaitable[dict]]


@dataclass
class McpMount:
    """learnMe's tools for one window. In-process where the backend supports it (Claude), else over
    HTTP: `url` is that window's endpoint and `token` its bearer token (see server/mcp_http.py)."""
    name: str
    tools: list[ToolDef]
    url: str
    token: str


@dataclass
class SessionSpec:
    cwd: str
    model: str | None = None
    permission_mode: str | None = None   # Claude Code's names: default | acceptEdits | plan | bypassPermissions
    effort: str | None = None
    resume: str | None = None            # the backend's own conversation id
    fork_from: str | None = None         # branch this conversation on first use (needs Capabilities.fork)
    instructions: str = ""               # appended to the backend's own system prompt
    compact_rules: str = ""              # what a summary must keep (needs Capabilities.compact_rules)
    mcp: McpMount | None = None
    allowed_tools: list[str] | None = None
    is_manager: bool = False
    can_use_tool: Callable[[ToolPermission], Awaitable[PermissionDecision]] | None = None
    pre_tool: Callable[[str, dict], Awaitable[str | None]] | None = None  # returns a reason to refuse
    on_event: Callable[[Event], None] = lambda _e: None
    on_stderr: Callable[[str], None] = lambda _s: None
    extra: dict = field(default_factory=dict)


class AgentConnection(abc.ABC):
    """One live conversation. start() before anything else; close() ends the process, and the
    conversation can be picked up again later with SessionSpec.resume."""

    def __init__(self, spec: SessionSpec):
        self.spec = spec
        self.exit_error: BaseException | None = None

    def emit(self, type_: str, **data) -> None:
        self.spec.on_event({"type": type_, **data})

    @abc.abstractmethod
    async def start(self) -> None:
        """Connect; raises if the agent can't be started."""

    @abc.abstractmethod
    async def query(self, text: str) -> None:
        """Send a user message: starts a turn, or joins the running one where the backend can."""

    @abc.abstractmethod
    async def interrupt(self) -> None:
        """Stop the running turn; a `result` event with status "interrupted" follows."""

    async def compact(self, focus: str | None) -> None:
        raise NotImplementedError

    async def context_usage(self) -> tuple[int, int | None] | None:
        """(tokens in context, window size) if the backend can say exactly."""
        return None

    @abc.abstractmethod
    async def wait_closed(self) -> None:
        """Returns when the agent process has gone, whether close() was called or it died."""

    @abc.abstractmethod
    async def close(self) -> None:
        """Stop the agent process. Idempotent."""

    @property
    @abc.abstractmethod
    def alive(self) -> bool: ...


class Backend(abc.ABC):
    name: str = ""
    capabilities = Capabilities()

    @abc.abstractmethod
    def connect(self, spec: SessionSpec) -> AgentConnection: ...

    def default_model(self, manager: bool) -> str | None:
        return None

    def default_effort(self, manager: bool) -> str | None:
        return None

    def models(self) -> list[str]:
        """Suggestions for the UI's model picker (any string the backend accepts also works)."""
        return []

    def problems(self) -> list[str]:
        """Why this backend can't run here (missing CLI, missing key); empty if it can."""
        return []
