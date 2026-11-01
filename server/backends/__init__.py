"""Agent backends. A window's backend is chosen when it is created and kept for its whole life
(its conversation id only means something to that backend).

`auto` (the default) picks the first backend that can run here, in PREFERENCE order: whichever
of Claude Code's login, Codex's login / OPENAI_API_KEY, or DEEPSEEK_API_KEY you have.
"""

from functools import cache

from ..config import BACKENDS, CONFIG
from .base import Backend

PREFERENCE = ("claude", "codex", "deepseek")


@cache
def _build(name: str) -> Backend:
    if CONFIG.demo and name in BACKENDS:
        from ..demo.backend import DemoBackend
        return DemoBackend(name)
    if name == "claude":
        from .claude import ClaudeBackend
        return ClaudeBackend()
    if name == "deepseek":
        from .deepseek import DeepSeekBackend
        return DeepSeekBackend()
    if name == "codex":
        from .codex import CodexBackend
        return CodexBackend()
    raise ValueError(f"unknown backend {name!r}; choose one of {', '.join(BACKENDS)}")


def default_name(manager: bool = False) -> str:
    """The configured default, or with `auto` the first backend that can run here."""
    configured = (CONFIG.manager_backend if manager else CONFIG.backend) or "auto"
    if configured != "auto":
        return configured
    return next((n for n in PREFERENCE if not _build(n).problems()), PREFERENCE[0])


def get(name: str | None = None) -> Backend:
    name = (name or "auto").lower()
    return _build(default_name() if name == "auto" else name)


def overview() -> list[dict]:
    """Every backend, whether it can run here, and what it supports (for the UI and /api/backends)."""
    default, manager = default_name(), default_name(manager=True)
    out = []
    for name in BACKENDS:
        b = _build(name)
        out.append({"name": name, "problems": b.problems(), "capabilities": b.capabilities.public(),
                    "models": b.models(), "default": name == default, "manager": name == manager})
    return out
