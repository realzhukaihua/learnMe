"""A window for one Slack conversation: the bridge hands it the messages, it decides and replies.

The rules for Slack live in this window's prompt, and the guard is enforced here in code: in a
turn with anyone else's message in it, pre_tool keeps the window from changing the manager,
answering other conversations, or touching tasks it didn't dispatch. A turn on the owner's own
messages (matched by Slack user id, never by display name), or on the user's word typed here or
relayed by the manager, is the user speaking, with the same rights as the main window.
"""

from typing import TYPE_CHECKING

from .backends.base import ToolDef
from .i18n import tr
from .manager import build_tools
from .prompts import P
from .session import ACTIVE, MCP_SERVER, PROFILE_DIR, TaskSession, habits
from .slack_bridge import (
    MANAGER_MEMORY,
    PREFS_FILE,
    STATE_KV,
    TASKS_PER_DAY,
    WINDOWS_KV,
    Convo,
    SlackTurn,
    bash_is_readonly,
    connector_refusal,
)

if TYPE_CHECKING:
    from .console import Console


class SlackWindow(TaskSession):
    is_slack_window = True
    has_guard = True

    def __init__(self, task: dict, store, publish, console: "Console"):
        super().__init__(task, store, publish)
        self.console = console
        self.trigger = "system"  # for build_tools: steering from here is never the user's word in the main window
        self.last_user_text: str | None = None
        meta = (store.get_kv(WINDOWS_KV, {}).get("windows") or {}).get(task["id"]) or {}
        self.key = (meta.get("channel") or "", meta.get("thread_ts"))
        self.convo: Convo | None = None  # the latest messages handed in
        self.owner_turn = False  # an idle window after a restart: someone else's until the owner speaks again
        self.owner_word = False  # the owner said something in this turn: their posts aren't limited
        if task["status"] in ACTIVE:  # a turn cut off by a restart resumes with the rights it had
            was = (store.get_kv(STATE_KV, {}).get("owner_turns") or {}).get(task["id"]) or {}
            self.owner_turn, self.owner_word = bool(was.get("rights")), bool(was.get("word"))

    @property
    def slack_turn(self) -> SlackTurn:
        return SlackTurn([self.convo or Convo(*self.key, "", "")], owner_only=self.owner_turn, window=self.id,
                         owner_word=self.owner_word)

    def instructions(self) -> str:
        bridge = getattr(self.console, "slack", None)
        prompt = P.SLACK_WINDOW.format(profile=PROFILE_DIR, memory=MANAGER_MEMORY, prefs=PREFS_FILE,
                                       owner=bridge.owner_label if bridge else tr("the owner"),
                                       tasks_per_day=bridge.cfg.tasks_per_day if bridge else TASKS_PER_DAY)
        return "\n\n".join(p for p in (prompt, habits()) if p)

    def tools(self) -> list[ToolDef]:
        return build_tools(self.console, self)

    def allowed_tools(self) -> list[str] | None:
        return [f"mcp__{MCP_SERVER}__{t.name}" for t in self.tools()]

    async def deliver(self, convo: Convo, text: str, owner: bool, word: bool | None = None) -> None:
        """New messages in this conversation, or a dispatched task's result, from the bridge.
        owner: the turn's rights; word: the owner themselves speaks in it (by default, as owner)."""
        self.convo = convo
        self._set_owner(owner, word=word)
        await self.send(text, actor="system")

    async def send(self, text: str, correct: bool = False, actor: str = "user",
                   instruction: str | None = None) -> None:
        if actor in ("user", "manager"):  # the user, typing here or through the main window
            self._set_owner(True, replaces=correct)
        await super().send(text, correct, actor, instruction)

    def _set_owner(self, owner: bool, replaces: bool = False, word: bool | None = None) -> None:
        """An idle window starts a turn that is as the new messages are; added to a running turn,
        anyone else's message makes the whole turn someone else's. The owner's word counts only
        in a turn that is all theirs."""
        word = owner if word is None else word
        if replaces or self.status not in ACTIVE:
            self.owner_turn, self.owner_word = owner, word
        else:
            self.owner_turn = self.owner_turn and owner
            self.owner_word = self.owner_turn and (self.owner_word or word)
        if bridge := getattr(self.console, "slack", None):
            bridge.note_turn(self.id, self.owner_turn, self.owner_word)

    async def pre_tool(self, tool: str, inp: dict) -> str | None:
        """Enforced in code, not left to the prompt: other people's Slack messages can't make this
        window change the manager. A turn of the owner's own passes as if said in the main window."""
        turn, bridge = self.slack_turn, getattr(self.console, "slack", None)
        if bridge:
            reason = bridge.check_tool(turn, tool, inp, self.task["cwd"])
        elif turn.owner_only or tool in ("Read", "Grep", "Glob") or (
                tool == "Bash" and bash_is_readonly(inp.get("command") or "")):
            reason = None
        else:
            reason = tr("Slack is not enabled; while handling someone else's messages this window can only read")
        if reason and bridge:
            bridge.blocked(turn, tool, inp, reason)
        return reason

    async def _wrap_up(self, reason: str | None, actor: str) -> None:
        """No memory wrap-up: what people said on Slack doesn't go into memory on its own."""


class SlackTask(TaskSession):
    """A task a Slack window dispatched on someone else's request (origin "slack"). It runs under the
    constraint prefix, and connector tools (which act as the owner elsewhere) are refused in code."""

    has_guard = True

    def __init__(self, task: dict, store, publish, console: "Console"):
        super().__init__(task, store, publish)
        self.console = console

    async def pre_tool(self, tool: str, inp: dict) -> str | None:
        bridge = getattr(self.console, "slack", None)
        prefixes = bridge.cfg.blocked_prefixes if bridge else None
        reason = connector_refusal(tool, inp, prefixes) if prefixes else connector_refusal(tool, inp)
        if reason and bridge:
            bridge.blocked_child(self.id, tool, inp, reason)
        return reason
