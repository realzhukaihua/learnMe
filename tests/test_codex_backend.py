"""The Codex backend against a scripted app-server (tests/fakes/fake_codex.py)."""

import asyncio
import json
import os
import stat
import sys
from pathlib import Path

from server.backends.base import McpMount, PermissionDecision, SessionSpec, ToolPermission
from server.backends.codex import CodexBackend
from server.config import CONFIG

FAKE = Path(__file__).parent / "fakes" / "fake_codex.py"


def fake_command(tmp_path: Path) -> str:
    """An executable that runs the fake with this interpreter, as `<cmd> app-server`."""
    exe = tmp_path / "codex"
    exe.write_text(f"#!/bin/sh\nexec {sys.executable} {FAKE} \"$@\"\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return str(exe)


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 60))


def test_turn_with_approval_mcp_and_usage(tmp_path, monkeypatch):
    monkeypatch.setitem(CONFIG.backends, "codex", {"command": fake_command(tmp_path)})
    log = tmp_path / "requests.jsonl"
    monkeypatch.setenv("FAKE_CODEX_LOG", str(log))
    events: list[dict] = []
    asked: list[ToolPermission] = []
    guarded: list[tuple] = []

    async def can_use_tool(req: ToolPermission) -> PermissionDecision:
        asked.append(req)
        return PermissionDecision(action="allow")

    async def pre_tool(tool, inp):
        guarded.append((tool, inp))
        return None

    async def main():
        done = asyncio.Event()

        def on_event(ev):
            events.append(ev)
            if ev["type"] == "result":
                done.set()

        mount = McpMount(name="learnme", tools=[], url="http://127.0.0.1:1/mcp/main", token="tok")
        spec = SessionSpec(cwd=str(tmp_path), permission_mode="default", instructions="be brief", mcp=mount,
                           can_use_tool=can_use_tool, pre_tool=pre_tool, on_event=on_event)
        conn = CodexBackend().connect(spec)
        await conn.start()
        assert conn.alive
        await conn.query("hello")
        await asyncio.wait_for(done.wait(), 30)
        usage = await conn.context_usage()
        await conn.close()
        assert not conn.alive
        return usage

    usage = run(main())
    types = [e["type"] for e in events]
    assert types[0] == "init" and events[0]["session_id"].startswith("thr-")
    assert "delta" in types
    tools = [e for e in events if e["type"] == "tool_use"]
    assert [t["name"] for t in tools] == ["Bash", "mcp__learnme__list_tasks", "TodoWrite"]
    results = {e["tool_use_id"]: e for e in events if e["type"] == "tool_result"}
    assert results[tools[0]["id"]]["content"] == "hello\n" and not results[tools[0]["id"]]["is_error"]
    assert [e["text"] for e in events if e["type"] == "text"] == ["Checking the files.", "done: accept"]
    final = events[-1]
    assert final["type"] == "result" and final["status"] == "idle" and final["cost_usd"] is None
    assert usage == (1230, 200000)
    assert asked and asked[0].tool == "Bash" and "echo hello" in asked[0].input["command"]
    assert guarded and guarded[0][0] == "Bash"
    sent = [json.loads(line) for line in log.read_text().splitlines()]
    start = next(m for m in sent if m.get("method") == "thread/start")["params"]
    assert start["developerInstructions"] == "be brief"
    assert start["approvalPolicy"] == "untrusted" and start["sandbox"] == "workspace-write"
    assert start["config"]["mcp_servers.learnme"] == {"url": "http://127.0.0.1:1/mcp/main",
                                                        "bearer_token_env_var": "LEARNME_MCP_TOKEN"}


def test_guard_declines_and_interrupt(tmp_path, monkeypatch):
    monkeypatch.setitem(CONFIG.backends, "codex", {"command": fake_command(tmp_path)})
    events: list[dict] = []

    async def pre_tool(tool, inp):
        return "read-only in this window"

    async def main():
        results = asyncio.Queue()

        def on_event(ev):
            events.append(ev)
            if ev["type"] == "result":
                results.put_nowait(ev)

        spec = SessionSpec(cwd=str(tmp_path), pre_tool=pre_tool, on_event=on_event)
        conn = CodexBackend().connect(spec)
        await conn.start()
        await conn.query("first")
        first = await asyncio.wait_for(results.get(), 30)
        await conn.query("slow turn")
        await asyncio.sleep(0.5)
        assert conn.turn_id
        await conn.interrupt()
        second = await asyncio.wait_for(results.get(), 30)
        await conn.close()
        return first, second

    first, second = run(main())
    assert [e["text"] for e in events if e["type"] == "text"][-1] == "done: decline"
    bash = next(e for e in events if e["type"] == "tool_result")
    assert bash["is_error"] and bash["content"] == "declined"
    assert first["status"] == "idle" and second["status"] == "interrupted"


def test_resume_passes_thread_id(tmp_path, monkeypatch):
    monkeypatch.setitem(CONFIG.backends, "codex", {"command": fake_command(tmp_path)})
    log = tmp_path / "requests.jsonl"
    monkeypatch.setenv("FAKE_CODEX_LOG", str(log))
    events: list[dict] = []

    async def main():
        conn = CodexBackend().connect(SessionSpec(cwd=str(tmp_path), resume="thr-old", on_event=events.append))
        await conn.start()
        await conn.compact(None)
        await asyncio.sleep(0.3)
        await conn.close()

    run(main())
    sent = [json.loads(line) for line in log.read_text().splitlines()]
    assert next(m for m in sent if m.get("method") == "thread/resume")["params"]["threadId"] == "thr-old"
    assert events[0]["session_id"] == "thr-old"
    assert any(e["type"] == "compact" and e["trigger"] == "manual" for e in events)


def test_missing_cli_is_a_problem(monkeypatch):
    monkeypatch.setitem(CONFIG.backends, "codex", {"command": "definitely-not-a-codex-binary"})
    assert CodexBackend().problems()
    assert os.environ.get("LEARNME_DATA_DIR")
