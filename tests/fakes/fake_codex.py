"""A scripted stand-in for `codex app-server`: JSON-RPC over stdio, enough of the protocol for
learnMe's Codex backend. Invoked as `fake_codex.py app-server`.

Each turn: an agent message (streamed as deltas), a shell command that asks for approval, an
MCP tool call, token usage, then turn/completed. A turn whose input contains "slow" waits for
turn/interrupt instead of finishing. Requests it received are appended to $FAKE_CODEX_LOG.
"""

import json
import os
import sys
import threading
import time
import uuid

LOG = os.environ.get("FAKE_CODEX_LOG")
lock = threading.Lock()
pending: dict = {}          # our request id -> event
answers: dict = {}
next_id = [1000]
thread_id = None
interrupted = threading.Event()


def send(msg: dict) -> None:
    with lock:
        sys.stdout.write(json.dumps(msg) + "\n")
        sys.stdout.flush()


def log(msg: dict) -> None:
    if LOG:
        with open(LOG, "a") as f:
            f.write(json.dumps(msg) + "\n")


def notify(method: str, **params) -> None:
    send({"method": method, "params": {"threadId": thread_id, **params}})


def ask(method: str, params: dict) -> dict:
    next_id[0] += 1
    rid = next_id[0]
    ev = threading.Event()
    pending[rid] = ev
    send({"id": rid, "method": method, "params": params})
    ev.wait(30)
    return answers.pop(rid, {})


def run_turn(turn_id: str, text: str) -> None:
    notify("turn/started", turn={"id": turn_id, "items": [], "status": "inProgress"})
    if "slow" in text:
        interrupted.wait(30)
        notify("turn/completed", turn={"id": turn_id, "items": [], "status": "interrupted", "error": None})
        interrupted.clear()
        return
    mid = "msg-" + uuid.uuid4().hex[:6]
    notify("item/started", turnId=turn_id, item={"type": "agentMessage", "id": mid, "text": ""})
    for part in ("Checking ", "the files."):
        notify("item/agentMessage/delta", turnId=turn_id, itemId=mid, delta=part)
    notify("item/completed", turnId=turn_id, item={"type": "agentMessage", "id": mid, "text": "Checking the files."})
    cid = "exec-" + uuid.uuid4().hex[:6]
    cmd = "/bin/bash -lc 'echo hello'"
    notify("item/started", turnId=turn_id, item={"type": "commandExecution", "id": cid, "command": cmd, "cwd": "/tmp",
                                                 "status": "inProgress"})
    decision = ask("item/commandExecution/requestApproval",
                   {"threadId": thread_id, "turnId": turn_id, "itemId": cid, "command": cmd, "cwd": "/tmp",
                    "reason": "needs a shell", "startedAtMs": int(time.time() * 1000)}).get("decision")
    ran = decision in ("accept", "acceptForSession")
    notify("item/completed", turnId=turn_id, item={"type": "commandExecution", "id": cid, "command": cmd, "cwd": "/tmp",
                                                   "status": "completed" if ran else "declined",
                                                   "exitCode": 0 if ran else None,
                                                   "aggregatedOutput": "hello\n" if ran else ""})
    tid = "mcp-" + uuid.uuid4().hex[:6]
    notify("item/started", turnId=turn_id, item={"type": "mcpToolCall", "id": tid, "server": "learnme",
                                                 "tool": "list_tasks", "arguments": {"filter": "open"},
                                                 "status": "inProgress"})
    notify("item/completed", turnId=turn_id, item={"type": "mcpToolCall", "id": tid, "server": "learnme",
                                                   "tool": "list_tasks", "arguments": {"filter": "open"},
                                                   "status": "completed",
                                                   "result": {"content": [{"type": "text", "text": "[]"}]}})
    notify("turn/plan/updated", turnId=turn_id, plan=[{"step": "look", "status": "completed"},
                                                      {"step": "fix", "status": "inProgress"}])
    usage = {"inputTokens": 1200, "cachedInputTokens": 200, "outputTokens": 30, "reasoningOutputTokens": 0,
             "totalTokens": 1230}
    notify("thread/tokenUsage/updated", turnId=turn_id,
           tokenUsage={"last": usage, "total": usage, "modelContextWindow": 200000})
    fid = "msg-" + uuid.uuid4().hex[:6]
    notify("item/completed", turnId=turn_id, item={"type": "agentMessage", "id": fid, "text": f"done: {decision}"})
    notify("turn/completed", turn={"id": turn_id, "items": [], "status": "completed", "error": None, "durationMs": 42})


def main() -> None:
    global thread_id
    assert sys.argv[1:] == ["app-server"], sys.argv
    initialized = False
    for line in sys.stdin:
        msg = json.loads(line)
        log(msg)
        if "method" not in msg:  # an answer to one of our requests
            rid = msg.get("id")
            answers[rid] = msg.get("result") or {}
            if ev := pending.pop(rid, None):
                ev.set()
            continue
        method, params, rid = msg["method"], msg.get("params") or {}, msg.get("id")
        if method == "initialize":
            send({"id": rid, "result": {"userAgent": "fake", "codexHome": "/tmp", "platformFamily": "unix",
                                        "platformOs": "linux"}})
        elif method == "initialized":
            initialized = True
        elif not initialized:
            send({"id": rid, "error": {"code": -32600, "message": "Not initialized"}})
        elif method == "account/read":
            send({"id": rid, "result": {"account": {"type": "apiKey"}, "requiresOpenaiAuth": True}})
        elif method in ("thread/start", "thread/resume", "thread/fork"):
            thread_id = params.get("threadId") if method == "thread/resume" else "thr-" + uuid.uuid4().hex[:8]
            send({"id": rid, "result": {"thread": {"id": thread_id}, "model": params.get("model") or "fake-model",
                                        "cwd": params.get("cwd"), "approvalPolicy": params.get("approvalPolicy") or "on-request",
                                        "sandbox": {"type": "workspaceWrite"}, "modelProvider": "fake",
                                        "approvalsReviewer": None}})
            notify("thread/started", thread={"id": thread_id})
        elif method == "turn/start":
            turn_id = "turn-" + uuid.uuid4().hex[:8]
            send({"id": rid, "result": {"turn": {"id": turn_id, "items": [], "status": "inProgress"}}})
            text = " ".join(i.get("text", "") for i in params.get("input") or [])
            threading.Thread(target=run_turn, args=(turn_id, text), daemon=True).start()
        elif method == "turn/steer":
            send({"id": rid, "result": {"turnId": params.get("expectedTurnId")}})
        elif method == "turn/interrupt":
            interrupted.set()
            send({"id": rid, "result": {}})
        elif method == "thread/compact/start":
            send({"id": rid, "result": {}})
            notify("thread/compacted", turnId="turn-compact")
        else:
            send({"id": rid, "error": {"code": -32601, "message": f"unknown {method}"}})


if __name__ == "__main__":
    main()
