"""The DeepSeek backend end to end: the real Claude Code CLI (bundled with claude-agent-sdk) pointed
at a local fake of DeepSeek's Anthropic-compatible API. One question, one Bash tool call, one answer.
Also checks that only the DeepSeek key is ever sent to the endpoint, never the user's own Anthropic
credentials."""

import asyncio

import pytest

from server.backends.base import PermissionDecision, SessionSpec
from server.backends.deepseek import DeepSeekBackend
from server.config import CONFIG

from .fakes.fake_anthropic import COMMAND, FINAL, FakeAnthropic

KEY = "sk-test-not-a-real-deepseek-key"


def claude_cli_available() -> bool:
    try:
        import claude_agent_sdk  # noqa: F401
    except ImportError:
        return False
    return True


@pytest.mark.skipif(not claude_cli_available(), reason="claude-agent-sdk is not installed")
def test_one_question_one_tool_call(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", KEY)
    events: list[dict] = []
    asked: list[str] = []

    async def can_use_tool(req):
        asked.append(req.tool)
        return PermissionDecision(action="allow")

    with FakeAnthropic() as api:
        monkeypatch.setitem(CONFIG.backends, "deepseek", {"base_url": api.url, "price_input_per_mtok": 1.0,
                                                          "price_output_per_mtok": 2.0})
        backend = DeepSeekBackend()
        assert backend.problems() == []

        async def main():
            done = asyncio.Event()

            def on_event(ev):
                events.append(ev)
                if ev["type"] == "result":
                    done.set()

            spec = SessionSpec(cwd=str(tmp_path), model=backend.default_model(False), permission_mode="default",
                               instructions="Answer briefly.", can_use_tool=can_use_tool, on_event=on_event)
            conn = backend.connect(spec)
            await conn.start()
            await conn.query("Run a shell command that says hi, then tell me what it printed.")
            await asyncio.wait_for(done.wait(), 120)
            await conn.close()

        asyncio.run(asyncio.wait_for(main(), 180))

        seen = list(api.seen)
    types = [e["type"] for e in events]
    assert "init" in types
    tool = next(e for e in events if e["type"] == "tool_use")
    assert tool["name"] == "Bash" and tool["input"]["command"] == COMMAND
    result = next(e for e in events if e["type"] == "tool_result")
    assert "hi-from-mock" in str(result["content"]) and not result["is_error"]
    assert any(e["type"] == "text" and FINAL in e["text"] for e in events)
    final = [e for e in events if e["type"] == "result"][-1]
    assert final["status"] == "idle"
    assert final["cost_usd"] is not None and final["cost_usd"] > 0  # priced from tokens, not the CLI's figure
    assert asked in ([], ["Bash"])  # Claude Code may auto-allow a harmless echo
    main_calls = [s for s in seen if "Bash" in (s.get("tools") or [])]
    assert main_calls and all(s["model"].startswith("deepseek-flash") for s in main_calls)
    # Only the DeepSeek key ever reaches the endpoint.
    for s in seen:
        assert s["authorization"] in (f"Bearer {KEY}", None), s
        assert s["x-api-key"] in (KEY, None, ""), s
    assert any(s["authorization"] == f"Bearer {KEY}" or s["x-api-key"] == KEY for s in seen)


def test_missing_key_is_a_problem(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setitem(CONFIG.backends, "deepseek", {})
    assert any("DEEPSEEK_API_KEY" in p for p in DeepSeekBackend().problems())


def test_env_points_claude_code_at_deepseek(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", KEY)
    monkeypatch.setitem(CONFIG.backends, "deepseek", {"model": "deepseek-v4-pro"})
    env = DeepSeekBackend().env(SessionSpec(cwd="/tmp"))
    assert env["ANTHROPIC_BASE_URL"] == "https://api.deepseek.com/anthropic"
    assert env["ANTHROPIC_AUTH_TOKEN"] == KEY and env["ANTHROPIC_API_KEY"] == ""
    assert env["ANTHROPIC_MODEL"] == "deepseek-v4-pro" and env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "deepseek-flash"
