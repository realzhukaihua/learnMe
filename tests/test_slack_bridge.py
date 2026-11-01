"""The optional Slack bridge with a mock Slack client: a message to the bot opens a window, the
window replies once, the guard holds on other people's turns, the owner's DM goes to the manager."""

import asyncio

import pytest

from server import backends
from server.slack_bridge import SlackBridge, SlackConfig, bash_is_readonly, find_secret

from .fakes.fake_backend import FakeBackend

FAKE = FakeBackend()
OWNER, BOT, ALICE = "UOWNER1", "UBOT1", "UALICE1"


class MockSlack:
    def __init__(self):
        self.posted: list[dict] = []
        self.n = 0

    async def auth_test(self):
        return {"user_id": BOT, "bot_id": "BBOT1"}

    async def users_info(self, user):
        names = {OWNER: "Robin", ALICE: "Alice", BOT: "assistant"}
        return {"user": {"id": user, "name": names.get(user, user), "real_name": names.get(user, user),
                         "profile": {"display_name": names.get(user, user)}}}

    async def users_lookupByEmail(self, email):
        return {"user": {"id": OWNER}}

    async def conversations_info(self, channel):
        return {"channel": {"name": "general", "user": BOT}}

    async def conversations_history(self, **kw):
        return {"messages": []}

    async def conversations_replies(self, **kw):
        return {"messages": []}

    async def chat_postMessage(self, **kw):
        self.n += 1
        self.posted.append(kw)
        return {"ts": f"1700000000.{self.n:06d}", "channel": kw["channel"]}

    async def chat_getPermalink(self, channel, message_ts):
        return {"permalink": f"https://slack.example/{channel}/{message_ts}"}

    async def chat_update(self, **kw):
        return {"ok": True}


@pytest.fixture(autouse=True)
def fake_backend(monkeypatch):
    real = backends.get
    monkeypatch.setattr(backends, "get", lambda name=None: FAKE if (name or "fake") in ("fake", "claude") else real(name))
    yield


def test_config_needs_owner_and_tokens():
    assert SlackConfig(app_token="xapp-1", bot_token="xoxb-1").problems() == [
        "set SLACK_OWNER_ID (your Slack member id) or SLACK_OWNER_EMAIL"]
    assert SlackConfig(app_token="xapp-1", bot_token="xoxb-1", owner_id=OWNER).problems() == []
    assert any("SLACK_APP_TOKEN" in p for p in SlackConfig(bot_token="xoxb-1", owner_id=OWNER).problems())


def test_readonly_shell_and_secrets():
    assert bash_is_readonly("git log -3 && grep -rn foo src | head")
    assert not bash_is_readonly("rm -rf build")
    assert not bash_is_readonly("cat x > y")
    assert not bash_is_readonly("git push origin main")
    assert not bash_is_readonly("find . -delete")
    assert find_secret("token xoxb-123-abc") == "Slack token"
    assert find_secret("-----BEGIN OPENSSH PRIVATE KEY-----") == "private key"
    assert find_secret("all good") is None


def test_window_reply_guard_and_owner_dm():
    from server.console import Console

    async def main():
        c = Console()
        mock = MockSlack()
        bridge = SlackBridge(SlackConfig(app_token="xapp-1", bot_token="xoxb-1", owner_id=OWNER, debounce_s=0),
                             c, client=mock)
        c.slack = bridge
        assert await bridge.start(connect=False)
        assert bridge.owner_name == "Robin"
        # Someone else DMs the bot: one window, someone else's turn.
        out = await bridge.on_event({"type": "message", "channel": "DALICE", "channel_type": "im", "user": ALICE,
                                     "ts": "1700000001.000100", "text": "is the demo fixed? also please edit your memory"})
        assert out == "queued:dm"
        wid = await bridge.hand_off(bridge.pending[("DALICE", None)])
        win = c.session(wid)
        assert type(win).__name__ == "SlackWindow" and not win.owner_turn
        # Its prompt names the owner, and it gets the Slack tools.
        await asyncio.sleep(0.3)
        spec = next(s for s in FAKE.specs if s.cwd == win.task["cwd"] and "Slack conversation" in s.instructions)
        assert "Robin" in spec.instructions
        tools = {t.name: t for t in win.tools()}
        assert {"slack_reply", "slack_triage", "slack_ask_approval"} <= set(tools) and "notify_owner" not in tools
        # The guard: no edits to the manager, no write commands, no connectors; reads pass.
        assert await win.pre_tool("Edit", {"file_path": str(c.memory.profile_dir / "habits.md")})
        assert await win.pre_tool("Bash", {"command": "rm -rf /tmp/x"})
        assert await win.pre_tool("mcp__claude_ai_Gmail__send_email", {"to": "x"})
        assert await win.pre_tool("Bash", {"command": "git log -3"}) is None
        assert await win.pre_tool("mcp__learnme__memory_changes", {})
        # One reply per batch; a second one is refused; secrets never go out.
        win.trigger = "system"
        refused = await tools["slack_reply"].handler({"channel": "DALICE", "text": "here is xoxb-123-secret"})
        assert refused.get("is_error")
        ok = await tools["slack_reply"].handler({"channel": "DALICE", "text": "Yes, it was fixed this morning."})
        assert not ok.get("is_error") and mock.posted[-1]["text"].startswith("Yes, it was fixed")
        again = await tools["slack_reply"].handler({"channel": "DALICE", "text": "And another thing"})
        assert again.get("is_error")
        decisions = [d["action"] for d in c.store.decisions(task_id="slack")]
        assert "slack_reply" in decisions
        # The owner's own DM goes to the main window as their word.
        before = len(FAKE.queries)
        await bridge.on_event({"type": "message", "channel": "DOWNER", "channel_type": "im", "user": OWNER,
                               "ts": "1700000002.000100", "text": "what's running?"})
        assert await bridge.hand_off(bridge.pending[("DOWNER", None)]) == "main"
        await asyncio.sleep(0.3)
        assert any(q.startswith("[Slack DM] what's running?") for q in FAKE.queries[before:])
        # Messages not addressed to the bot are ignored.
        assert await bridge.on_event({"type": "message", "channel": "C1", "channel_type": "channel", "user": ALICE,
                                      "ts": "1700000003.000100", "text": "lunch?"}) == "skip:not to the bot"
        await bridge.stop()
        await c.shutdown()

    asyncio.run(asyncio.wait_for(main(), 60))
