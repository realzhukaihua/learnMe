"""DeepSeek, through its Anthropic-compatible API: Claude Code itself, pointed at DeepSeek.

DeepSeek serves the Anthropic Messages API at https://api.deepseek.com/anthropic and documents
running Claude Code against it (https://api-docs.deepseek.com/quick_start/agent_integrations/claude_code),
so this backend is the Claude backend with a different base URL, key and model names: tools,
hooks, permissions, resume/fork and compaction all work unchanged.

What differs (https://api-docs.deepseek.com/guides/anthropic_api): prompt caching is ignored,
thinking budgets are ignored, and the CLI's dollar figure assumes Anthropic prices, so the cost
shown is computed from token counts and the prices in [backends.deepseek] (none set: no cost).
"""

from __future__ import annotations

import os

from .base import Capabilities, SessionSpec
from .claude import ClaudeBackend

DEFAULT_BASE_URL = "https://api.deepseek.com/anthropic"
DEFAULT_MODEL = "deepseek-flash"


class DeepSeekBackend(ClaudeBackend):
    name = "deepseek"
    capabilities = Capabilities(resume=True, fork=True, steer=True, interrupt=True, compact=True,
                                compact_rules=True, permission_prompts=True, pre_tool_hook=True,
                                context_usage=True, cost_usd=False, thinking=True, claude_history=False)

    def api_key(self) -> str:
        return os.environ.get(self.opt("api_key_env") or "DEEPSEEK_API_KEY", "")

    def default_model(self, manager: bool) -> str | None:
        return self.opt("manager_model" if manager else "model") or self.opt("model") or DEFAULT_MODEL

    def default_effort(self, manager: bool) -> str | None:
        return None  # DeepSeek ignores thinking budgets

    def models(self) -> list[str]:
        return ["deepseek-flash", "deepseek-v4-pro"]

    def env(self, spec: SessionSpec) -> dict[str, str]:
        model = spec.model or self.default_model(spec.is_manager)
        small = self.opt("small_model") or DEFAULT_MODEL
        return {
            **super().env(spec),
            "ANTHROPIC_BASE_URL": self.opt("base_url") or DEFAULT_BASE_URL,
            "ANTHROPIC_AUTH_TOKEN": self.api_key(),
            # Never fall back to the user's Anthropic credentials against a third-party endpoint.
            "ANTHROPIC_API_KEY": "",
            "ANTHROPIC_MODEL": model,
            "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
            "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": small,
            "ANTHROPIC_SMALL_FAST_MODEL": small,
            "CLAUDE_CODE_SUBAGENT_MODEL": small,
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "API_TIMEOUT_MS": str(self.opt("timeout_ms") or 600000),
        }

    def turn_cost(self, conn, total_cost_usd: float | None, usage: dict | None) -> float | None:
        """Cumulative since start(), from tokens and the configured $/million-token prices."""
        price_in, price_out = self.opt("price_input_per_mtok"), self.opt("price_output_per_mtok")
        if price_in is None or price_out is None or not usage:
            return None
        price_hit = self.opt("price_cache_hit_per_mtok", price_in)
        hit = usage.get("cache_read_input_tokens") or 0
        miss = (usage.get("input_tokens") or 0) + (usage.get("cache_creation_input_tokens") or 0)
        cost = (miss * price_in + hit * price_hit + (usage.get("output_tokens") or 0) * price_out) / 1e6
        conn.spent += cost
        return conn.spent

    def problems(self) -> list[str]:
        out = []
        try:
            import claude_agent_sdk  # noqa: F401
        except ImportError:
            out.append("the claude-agent-sdk Python package is not installed")
        if not self.api_key():
            out.append(f"{self.opt('api_key_env') or 'DEEPSEEK_API_KEY'} is not set")
        return out
