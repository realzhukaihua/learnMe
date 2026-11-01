"""History: past learnMe windows and every Claude Code session on this machine (Claude backend only).

Claude Code keeps each session as ~/.claude/projects/<slug>/<session id>.jsonl. Scanning
them is incremental (only files whose mtime/size changed are re-read) and skips the big
tool-result lines before parsing, so tens of MB of transcripts stay cheap. A session can
be restored into a learnMe window: the window forks it on first use, so the original
session (possibly still open in VSCode) is never written to.
"""

import json
import logging
import re
from pathlib import Path

from .backends.claude import context_tokens
from .memory import CLAUDE_PROJECTS
from .store import Store

log = logging.getLogger("learnme.history")

SKIP_DIRS = ("-tmp",)
REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
IDE_TAGS = re.compile(r"<(ide_selection|ide_opened_file|pasted_content)[^>]*>.*?</\1[^>]*>", re.S)
IMPORT_LIMIT = 60


def human_text(record: dict) -> str | None:
    """The user's own words in a transcript record, or None for tool results and meta lines."""
    if record.get("type") != "user" or record.get("isSidechain") or "toolUseResult" in record:
        return None
    content = (record.get("message") or {}).get("content")
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    parts = []
    for block in content or []:
        if block.get("type") == "text":
            text = IDE_TAGS.sub("", REMINDER.sub("", block.get("text", ""))).strip()
            if text and not text.startswith("<"):
                parts.append(text)
    return "\n".join(parts) or None


def parse_session(path: Path) -> dict:
    info = {"session_id": path.stem, "cwd": None, "title": None, "first_prompt": None, "last_prompt": None,
            "started": None, "ended": None, "entrypoint": None, "prompts": 0}
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"type":"ai-title"' in line:
                info["title"] = json.loads(line).get("aiTitle") or info["title"]
            elif '"type":"last-prompt"' in line:
                info["last_prompt"] = json.loads(line).get("lastPrompt") or info["last_prompt"]
            elif '"type":"user"' in line and '"toolUseResult"' not in line:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                text = human_text(r)
                if not text:
                    continue
                info["prompts"] += 1
                info["first_prompt"] = info["first_prompt"] or text[:300]
                info["cwd"] = info["cwd"] or r.get("cwd")
                info["entrypoint"] = info["entrypoint"] or r.get("entrypoint")
                info["started"] = info["started"] or r.get("timestamp")
                info["ended"] = r.get("timestamp") or info["ended"]
            elif '"type":"assistant"' in line and '"timestamp":"' in line:
                m = re.search(r'"timestamp":"([^"]+)"', line)
                if m:
                    info["ended"] = m.group(1)
    return info


def scan_claude_sessions(store: Store) -> list[dict]:
    """Refresh the cache from disk (only changed files are re-read) and return all sessions."""
    cache = store.claude_session_cache()
    seen = set()
    for d in sorted(CLAUDE_PROJECTS.iterdir()) if CLAUDE_PROJECTS.is_dir() else []:
        if not d.is_dir() or d.name.startswith(SKIP_DIRS):
            continue
        for f in d.glob("*.jsonl"):
            key = str(f)
            seen.add(key)
            try:
                st = f.stat()
            except OSError:
                continue
            hit = cache.get(key)
            if hit and hit["mtime"] == st.st_mtime and hit["size"] == st.st_size:
                continue
            try:
                info = parse_session(f)
            except (OSError, ValueError):
                log.warning("could not parse %s", f, exc_info=True)
                continue
            store.put_claude_session({"path": key, "mtime": st.st_mtime, "size": st.st_size, **info})
    store.drop_claude_sessions([p for p in cache if p not in seen])
    rows = [r for r in store.claude_session_cache().values() if r["prompts"]]
    return sorted(rows, key=lambda r: r["ended"] or "", reverse=True)


def last_context(path: Path) -> int | None:
    """Context size at the session's last main-thread API call; None if a compaction came after it."""
    tokens = None
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"isSidechain":true' in line:
                continue
            if '"compact_boundary"' in line or ('"type":"assistant"' in line and '"usage"' in line):
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("subtype") == "compact_boundary":
                    tokens = None
                elif r.get("type") == "assistant":
                    tokens = context_tokens((r.get("message") or {}).get("usage")) or tokens
    return tokens


def import_transcript(path: Path, limit: int = IMPORT_LIMIT) -> list[tuple[str, dict]]:
    """The visible conversation of a session (prompts, replies, tool calls), most recent `limit`."""
    entries: list[tuple[str, dict]] = []
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"isSidechain":true' in line or '"toolUseResult"' in line:
                continue
            if '"type":"user"' not in line and '"type":"assistant"' not in line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("type") == "user":
                if text := human_text(r):
                    entries.append(("user", {"text": text}))
                continue
            for block in (r.get("message") or {}).get("content") or []:
                if block.get("type") == "text" and block.get("text", "").strip():
                    entries.append(("text", {"text": block["text"]}))
                elif block.get("type") == "tool_use":
                    i = block.get("input") or {}
                    brief = next((i[k] for k in ("command", "file_path", "pattern", "url", "query", "description")
                                  if isinstance(i.get(k), str)), "")
                    entries.append(("tool", {"name": block.get("name"), "brief": brief.replace("\n", " ")[:160]}))
    return entries[-limit:]
