"""learnMe's tools as an MCP server over Streamable HTTP, for backends that can't host them in-process.

Each window that has tools (the manager, Slack windows) gets its own endpoint, /mcp/<window id>,
and its own bearer token, generated when the server starts and handed only to that window's
agent. The endpoint answers JSON-RPC with plain JSON responses (no server-initiated streams),
which the Streamable HTTP transport allows: initialize, tools/list, tools/call, ping.

Guards run here too: a window with a pre_tool guard has every tool call checked before it runs,
whichever backend made it.
"""

import hmac
import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from .session import MCP_SERVER

if TYPE_CHECKING:
    from .console import Console

log = logging.getLogger("learnme.mcp")

PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "learnme", "version": "0.1.0"}


def _error(rid: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def build_router(console: "Console") -> APIRouter:
    router = APIRouter()

    def authorized(window: str, request: Request):
        s = console.sessions.get(window) or (console.manager() if window == "main" else None)
        token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if s is None or not token or not hmac.compare_digest(token, s.mcp_token):
            return None
        return s

    async def handle(s, msg: dict) -> dict | None:
        rid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
        if rid is None:  # a notification (notifications/initialized, cancelled, ...)
            return None
        if method == "initialize":
            asked = params.get("protocolVersion")
            version = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": version, "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO}}
        if method == "ping":
            return {"jsonrpc": "2.0", "id": rid, "result": {}}
        if method == "tools/list":
            tools = [{"name": t.name, "description": t.description, "inputSchema": t.schema} for t in s.tools()]
            return {"jsonrpc": "2.0", "id": rid, "result": {"tools": tools}}
        if method == "tools/call":
            name, args = params.get("name"), params.get("arguments") or {}
            tool = next((t for t in s.tools() if t.name == name), None)
            if tool is None:
                return _error(rid, -32602, f"unknown tool {name!r}")
            if s.has_guard and (reason := await s.pre_tool(f"mcp__{MCP_SERVER}__{name}", args)):
                return {"jsonrpc": "2.0", "id": rid,
                        "result": {"content": [{"type": "text", "text": reason}], "isError": True}}
            try:
                out = await tool.handler(args)
            except Exception as e:  # noqa: BLE001 - a tool error is the agent's to read
                log.exception("tool %s failed", name)
                out = {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "is_error": True}
            result = {"content": out.get("content") or []}
            if out.get("is_error"):
                result["isError"] = True
            return {"jsonrpc": "2.0", "id": rid, "result": result}
        return _error(rid, -32601, f"method not found: {method}")

    @router.post("/mcp/{window}")
    async def post(window: str, request: Request):
        s = authorized(window, request)
        if s is None:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse(_error(None, -32700, "parse error"), status_code=400)
        batch = isinstance(body, list)
        replies = [r for m in (body if batch else [body]) if isinstance(m, dict) and (r := await handle(s, m))]
        if not replies:
            return Response(status_code=202)
        return JSONResponse(replies if batch else replies[0])

    @router.get("/mcp/{window}")
    async def get(window: str):
        return Response(status_code=405, headers={"Allow": "POST"})  # no server-initiated stream

    @router.delete("/mcp/{window}")
    async def delete(window: str):
        return Response(status_code=200)

    return router
