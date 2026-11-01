"""A scripted Anthropic Messages API on localhost, standing in for DeepSeek's Anthropic-compatible
endpoint. The main agent loop (a request that offers a Bash tool) first gets a Bash tool call, and
once the tool result comes back, a final text. Side requests (titles, summaries) get "ok".
Every request's auth headers and model are recorded in `seen`."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

COMMAND = "echo hi-from-mock"
FINAL = "The command printed hi-from-mock. DONE"


def _events(blocks: list[dict], stop: str, model: str) -> list[tuple[str, dict]]:
    out = [("message_start", {"type": "message_start", "message": {
        "id": "msg_mock", "type": "message", "role": "assistant", "model": model, "content": [],
        "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 120, "output_tokens": 1, "cache_read_input_tokens": 0,
                  "cache_creation_input_tokens": 0}}})]
    for i, b in enumerate(blocks):
        if b["type"] == "text":
            out.append(("content_block_start", {"type": "content_block_start", "index": i,
                                                 "content_block": {"type": "text", "text": ""}}))
            out.append(("content_block_delta", {"type": "content_block_delta", "index": i,
                                                 "delta": {"type": "text_delta", "text": b["text"]}}))
        else:
            out.append(("content_block_start", {"type": "content_block_start", "index": i, "content_block": {
                "type": "tool_use", "id": b["id"], "name": b["name"], "input": {}}}))
            out.append(("content_block_delta", {"type": "content_block_delta", "index": i, "delta": {
                "type": "input_json_delta", "partial_json": json.dumps(b["input"])}}))
        out.append(("content_block_stop", {"type": "content_block_stop", "index": i}))
    out.append(("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                                  "usage": {"output_tokens": 12}}))
    out.append(("message_stop", {"type": "message_stop"}))
    return out


class FakeAnthropic:
    def __init__(self):
        self.seen: list[dict] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_GET(self):
                fake.seen.append({"path": self.path, "authorization": self.headers.get("authorization"),
                                  "x-api-key": self.headers.get("x-api-key"), "model": None})
                self._json(404, {"type": "error", "error": {"type": "not_found_error", "message": "not here"}})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
                fake.seen.append({"path": self.path, "authorization": self.headers.get("authorization"),
                                  "x-api-key": self.headers.get("x-api-key"), "model": body.get("model"),
                                  "tools": [t.get("name") for t in body.get("tools") or []]})
                if "count_tokens" in self.path:
                    return self._json(200, {"input_tokens": 120})
                if not self.path.startswith("/v1/messages"):
                    return self._json(404, {"type": "error", "error": {"type": "not_found_error", "message": "no"}})
                model = body.get("model") or "deepseek-flash"
                tools = {t.get("name") for t in body.get("tools") or []}
                last = (body.get("messages") or [{}])[-1]
                content = last.get("content")
                answered = isinstance(content, list) and any(b.get("type") == "tool_result" for b in content)
                if "Bash" in tools and not answered:
                    blocks = [{"type": "text", "text": "Running it."},
                              {"type": "tool_use", "id": "toolu_mock1", "name": "Bash",
                               "input": {"command": COMMAND, "description": "say hi"}}]
                    stop = "tool_use"
                elif "Bash" in tools:
                    blocks, stop = [{"type": "text", "text": FINAL}], "end_turn"
                else:
                    blocks, stop = [{"type": "text", "text": "ok"}], "end_turn"
                if not body.get("stream"):
                    return self._json(200, {"id": "msg_mock", "type": "message", "role": "assistant", "model": model,
                                            "content": [b if b["type"] == "text" else {**b} for b in blocks],
                                            "stop_reason": stop, "stop_sequence": None,
                                            "usage": {"input_tokens": 120, "output_tokens": 12}})
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("cache-control", "no-cache")
                self.send_header("connection", "close")
                self.end_headers()
                for name, data in _events(blocks, stop, model):
                    self.wfile.write(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode())
                    self.wfile.flush()
                self.close_connection = True

            def _json(self, code: int, data: dict):
                raw = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
