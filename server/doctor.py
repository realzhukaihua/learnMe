"""Which agent backends can run on this machine, and what the others need.

    python -m server.doctor        (or ./run.sh doctor)
"""

from . import backends
from .config import CONFIG

HOW = {
    "claude": "Claude subscription or Anthropic API key: install Claude Code (npm i -g @anthropic-ai/claude-code) and run "
              "`claude` once to log in, or export ANTHROPIC_API_KEY=...",
    "codex": "ChatGPT plan or OpenAI API key: npm i -g @openai/codex, then `codex login`, or export OPENAI_API_KEY=...",
    "deepseek": "DeepSeek API key: export DEEPSEEK_API_KEY=... (from the DeepSeek platform's API keys page)",
}


def main() -> int:
    rows = backends.overview()
    print("learnMe backends on this machine:\n")
    for b in rows:
        mark = "✓" if not b["problems"] else "✗"
        print(f"  {mark} {b['name']:<9} {'ready' if not b['problems'] else '; '.join(b['problems'])}")
        if b["problems"]:
            print(f"    to enable: {HOW[b['name']]}")
    ready = [b["name"] for b in rows if not b["problems"]]
    print()
    print(f"tasks run on:   {backends.default_name()}  (backend = {CONFIG.backend!r})")
    print(f"manager runs on: {backends.default_name(manager=True)}  (manager_backend = {CONFIG.manager_backend!r})")
    if not ready:
        print("\nNo backend is ready yet: set up any one of the above and run this again.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
