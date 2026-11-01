"""An in-process backend for session/console tests: scripted turns, no agent process."""

import asyncio

from server.backends.base import AgentConnection, Backend, Capabilities, SessionSpec


class FakeConnection(AgentConnection):
    def __init__(self, spec: SessionSpec, backend: "FakeBackend"):
        super().__init__(spec)
        self.backend = backend
        self._alive = False
        self.closed = asyncio.Event()

    @property
    def alive(self) -> bool:
        return self._alive

    async def start(self) -> None:
        self._alive = True
        self.backend.specs.append(self.spec)
        self.emit("init", session_id=self.spec.resume or f"fake-{len(self.backend.specs)}", model="fake-model",
                  permission_mode=self.spec.permission_mode, cwd=self.spec.cwd)

    async def query(self, text: str) -> None:
        self.backend.queries.append(text)
        asyncio.get_running_loop().call_later(0.05, self._answer, text)

    def _answer(self, text: str) -> None:
        self.emit("context", tokens=1000, window=100000)
        self.emit("tool_use", id="t1", name="Bash", input={"command": "echo ok"}, parent=None)
        self.emit("tool_result", tool_use_id="t1", content="ok", is_error=False, parent=None)
        self.emit("text", text=f"answered: {text[:40]}", parent=None)
        self.emit("result", status="idle", subtype="success", reason="", duration_ms=5, num_turns=1,
                  cost_usd=0.01 * len(self.backend.queries), errors=None, text=None)

    async def interrupt(self) -> None:
        self.emit("result", status="interrupted", subtype="interrupted", reason="aborted", duration_ms=1, num_turns=0,
                  cost_usd=None, errors=None, text=None)

    async def compact(self, focus):
        self.emit("compact", trigger="manual", pre_tokens=1000, duration_ms=1)
        self.emit("result", status="idle", subtype="success", reason="", duration_ms=1, num_turns=0, cost_usd=None,
                  errors=None, text=None)

    async def context_usage(self):
        return (1000, 100000)

    async def wait_closed(self) -> None:
        await self.closed.wait()

    async def close(self) -> None:
        self._alive = False
        self.closed.set()


class FakeBackend(Backend):
    name = "fake"
    capabilities = Capabilities(compact=True, context_usage=True, steer=True)

    def __init__(self):
        self.specs: list[SessionSpec] = []
        self.queries: list[str] = []

    def connect(self, spec: SessionSpec) -> AgentConnection:
        return FakeConnection(spec, self)
