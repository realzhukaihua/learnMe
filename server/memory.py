"""Memory: every memory file a session can write is tracked, indexed, diffed and revertible.

Memory lives in three places:
- the profile (profile/ by default): the user's habits (every session) and judgment rules (manager)
- the manager's own memory dir (data/manager-memory by default); its MEMORY.md goes into the
  manager's prompt on every backend
- each project's own memory: <project>/CLAUDE.md, AGENTS.md, and its Claude Code memory dir

Design for scale: storage is unbounded, loading is selective. Only the always-loaded slice
(MEMORY.md, profile/habits.md, profile/judgment.md) has a size limit, and going over it
means restructuring into a deeper index (MEMORY.md -> topic-*.md -> memory files) and
archiving finished state, never deleting knowledge. Everything stays searchable through a
full-text index, and the last content seen for every file is kept in SQLite, so edits made
while the server was down are still caught and every change keeps its full before/after text.
"""

import difflib
import logging
import os
import re
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

from .config import CONFIG
from .i18n import tr
from .prompts import P
from .store import Store

log = logging.getLogger("learnme.memory")

CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
PROJECT_DOCS = ("CLAUDE.md", "CLAUDE.local.md", "AGENTS.md")
DIFF_CLIP = 6000

# Limits apply only to what is loaded into every prompt.
PROFILE_LOADED_CHARS = {"habits.md": 2500, "judgment.md": 4000}
INDEX_LOADED_LINES = 150  # only the head of MEMORY.md is loaded
INDEX_LOADED_CHARS = 8000

LINK = re.compile(r"\]\(([^)\s]+?\.md)\)")
STATE_WORDS = re.compile(r"in[- ]flight|pending|todo|running|wip|in progress|awaiting|待合|待验证|待上线|进行中|在跑|未上线", re.I)
STALE_DAYS = 30
SHORT_TERM = 3  # trigram needs >= 3 chars per term




def claude_slug(path: str) -> str:
    """Claude Code's directory name for a project: every non-alphanumeric char becomes '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def claude_memory_dir(cwd: str) -> Path:
    return CLAUDE_PROJECTS / claude_slug(cwd) / "memory"


def unified_diff(before: str, after: str, name: str) -> str:
    lines = difflib.unified_diff(before.splitlines(), after.splitlines(),
                                 fromfile=f"a/{name}", tofile=f"b/{name}", lineterm="", n=2)
    text = "\n".join(lines)
    return text if len(text) <= DIFF_CLIP else text[:DIFF_CLIP] + "\n" + tr("… (diff too long, truncated)")


def read(p: Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return None


class MemoryTracker:
    def __init__(self, store: Store, root: Path, profile_dir: Path, project_roots: list[Path],
                 publish: Callable[[dict], None]):
        self.store = store
        self.root = root
        self.profile_dir = profile_dir
        self.project_roots = project_roots
        self.publish = publish
        self.projects: set[str] = set()

    # --- what is tracked ---

    def discover_projects(self) -> None:
        """Projects with tasks, plus any project that already has Claude memory or a CLAUDE.md."""
        self.projects.update(self.store.task_cwds())
        for root in self.project_roots:
            if not root.is_dir():
                continue
            for p in root.iterdir():
                if p.is_dir() and (claude_memory_dir(str(p)).is_dir() or (p / "CLAUDE.md").exists()):
                    self.projects.add(str(p))
        self.projects.discard(str(self.root))

    def track_project(self, cwd: str) -> None:
        if cwd != str(self.root) and cwd not in self.projects:
            self.projects.add(cwd)
            self.scan(baseline_only={self.scope_of_project(cwd)})

    @staticmethod
    def scope_of_project(cwd: str) -> str:
        return f"project:{cwd}"

    def memory_dir_of(self, scope: str) -> Path | None:
        if scope == "manager":
            return CONFIG.manager_memory_dir
        if scope.startswith("project:"):
            return claude_memory_dir(scope.split(":", 1)[1])
        return None

    def files(self) -> list[tuple[str, Path]]:
        out = [("profile", p) for p in sorted(self.profile_dir.glob("*.md"))
               if not p.name.endswith(".example.md") and p.name != "README.md"]
        out += [("manager", p) for p in sorted(CONFIG.manager_memory_dir.glob("*.md"))]
        for cwd in sorted(self.projects):
            scope = self.scope_of_project(cwd)
            out += [(scope, Path(cwd) / n) for n in PROJECT_DOCS if (Path(cwd) / n).is_file()]
            out += [(scope, p) for p in sorted(claude_memory_dir(cwd).glob("*.md"))]
        return out

    def scope_label(self, scope: str) -> str:
        if scope == "profile":
            return tr("Your profile")
        if scope == "manager":
            return tr("Manager memory")
        return tr("Project · {name}", name=Path(scope.split(':', 1)[1]).name)

    def display_name(self, scope: str, path: str) -> str:
        p = Path(path)
        if scope.startswith("project:") and p.parent.name == "memory":
            return f"memory/{p.name}"
        return p.name

    # --- change detection ---

    def _snapshot(self, path: str, scope: str, text: str) -> None:
        self.store.set_memory_snapshot(path, scope, text)
        self.store.index_memory_file(path, scope, text)

    def ensure_index(self) -> None:
        """Make the full-text index cover exactly the files we track (first run, or new projects)."""
        indexed = self.store.memory_fts_paths()
        current = {str(p): scope for scope, p in self.files()}
        for path, scope in current.items():
            if path not in indexed and (text := read(Path(path))) is not None:
                self.store.index_memory_file(path, scope, text)
        for path in indexed - set(current):
            self.store.index_memory_file(path, "", "")

    def scan(self, attribute: Callable[[str, str], tuple[str, str] | None] | None = None,
             skip_scopes: set[str] | None = None, baseline_only: set[str] | None = None) -> list[dict]:
        """Compare tracked files with what we saw last time and record every difference.

        `attribute(path, scope)` names the (task_id, actor) that made a change, or None for
        an edit from outside learnMe. `baseline_only` scopes are snapshotted without recording
        changes (newly tracked projects); so is everything on the very first scan.
        """
        skip_scopes = skip_scopes or set()
        seen = self.store.memory_snapshots()
        first_scan = not seen
        current = {str(p): scope for scope, p in self.files()}
        # Files we saw before that are gone now count as deletions, in their old scope.
        paths = set(current) | {p for p, s in seen.items() if s["scope"] not in skip_scopes}
        changes = []
        for path in sorted(paths):
            scope = current.get(path) or seen[path]["scope"]
            if scope in skip_scopes:
                continue
            text = read(Path(path)) if path in current else ""
            if text is None:
                continue
            if path in seen:
                before = seen[path]["content"]
            elif first_scan or (baseline_only and scope in baseline_only):
                before = None  # baseline, not a change
            else:
                before = ""  # a file created since the last scan
            if before == text:
                continue
            self._snapshot(path, scope, text)
            if before is None or (baseline_only and scope in baseline_only):
                continue
            task_id, actor = (attribute and attribute(path, scope)) or (None, "external")
            changes.append(self.record(path, scope, before, text, task_id, actor))
        return changes

    def record(self, path: str, scope: str, before: str, after: str, task_id: str | None,
               actor: str) -> dict:
        change_id = self.store.add_memory_change(path, scope, task_id, actor, before, after)
        name = self.display_name(scope, path)
        change = {
            "id": change_id, "ts": time.time(), "path": path, "scope": scope,
            "scope_label": self.scope_label(scope), "name": name, "task_id": task_id, "actor": actor,
            "kind": "created" if not before else "deleted" if not after else "modified",
            "diff": unified_diff(before, after, name),
        }
        log.info("memory %s by %s: %s", change["kind"], actor, path)
        self.publish({"kind": "memory_change", "change": change})
        return change

    # --- what is loaded into every prompt ---

    def over_budget(self) -> list[dict]:
        """Always-loaded files that outgrew their limit and should be split into a deeper index."""
        out = []
        for scope, p in self.files():
            text = read(p)
            if text is None:
                continue
            if scope == "profile" and p.name in PROFILE_LOADED_CHARS:
                size, limit, unit = len(text), PROFILE_LOADED_CHARS[p.name], tr(" chars")
            elif p.name == "MEMORY.md" and len(text.splitlines()) > INDEX_LOADED_LINES:
                size, limit, unit = len(text.splitlines()), INDEX_LOADED_LINES, tr(" lines")
            elif p.name == "MEMORY.md":
                size, limit, unit = len(text), INDEX_LOADED_CHARS, tr(" chars")
            else:
                continue
            if size > limit:
                out.append({"path": str(p), "scope": scope, "scope_label": self.scope_label(scope),
                            "name": self.display_name(scope, str(p)), "size": size, "limit": limit, "unit": unit})
        return out

    # --- health ---

    def lint(self, scope: str) -> dict:
        """Orphans (in no index), dead links, and old state-type entries that may be stale."""
        d = self.memory_dir_of(scope)
        empty = {"orphans": [], "dead_links": [], "stale": []}
        if not d or not d.is_dir():
            return empty
        files = {p.name: p for p in d.glob("*.md")}
        indexes = {n: read(p) or "" for n, p in files.items() if n == "MEMORY.md" or n.startswith("topic-")}
        index_text = "\n".join(indexes.values())
        dead = []
        for n, text in indexes.items():
            for target in LINK.findall(text):
                if Path(target).name not in files:
                    dead.append({"in": n, "target": target})
        orphans = sorted(n for n in files if n not in indexes and n not in index_text)
        stale, now = [], time.time()
        for n, p in files.items():
            if n in indexes:
                continue
            text = read(p) or ""
            if "status: archived" in text:
                continue
            age = (now - p.stat().st_mtime) / 86400
            if age > STALE_DAYS and STATE_WORDS.search(text):
                stale.append({"name": n, "age_days": int(age)})
        return {"orphans": orphans, "dead_links": dead, "stale": sorted(stale, key=lambda s: -s["age_days"])}

    def stats(self, scope: str) -> dict:
        files = [p for s, p in self.files() if s == scope]
        texts = [read(p) or "" for p in files]
        index = next((t for p, t in zip(files, texts) if p.name == "MEMORY.md"), "")
        return {"files": len(files), "chars": sum(map(len, texts)), "loaded_chars": len(index)}

    def restructure_prompt(self, scope: str) -> str:
        d = self.memory_dir_of(scope)
        if not d:
            raise ValueError(tr("Only an agent memory directory can be layered"))
        lint, st = self.lint(scope), self.stats(scope)
        parts = []
        if lint["orphans"]:
            parts.append(tr("{n} orphan files ({names})", n=len(lint['orphans']),
                            names=", ".join(lint['orphans'][:15]) + (" …" if len(lint['orphans']) > 15 else "")))
        if lint["dead_links"]:
            parts.append(tr("{n} dead links ({names})", n=len(lint['dead_links']),
                            names=", ".join(x['target'] for x in lint['dead_links'][:10])))
        if lint["stale"]:
            parts.append(tr("{n} state-type entries not updated for over {days} days ({names})", n=len(lint['stale']),
                            days=STALE_DAYS,
                            names=", ".join(x['name'] for x in lint['stale'][:15]) + (" …" if len(lint['stale']) > 15 else "")))
        return P.RESTRUCTURE.format(
            memdir=d, files=st["files"], chars=st["chars"], loaded=st["loaded_chars"],
            target=INDEX_LOADED_CHARS * 3 // 4, today=time.strftime("%Y-%m-%d"),
            lint="; ".join(parts) or tr("none"))

    # --- recall ---

    def search(self, query: str, limit: int = 12) -> list[dict]:
        """Full-text search across every tracked memory file; all terms must match.

        Terms of 3+ chars go through the trigram index; shorter ones (common 2-char Chinese
        words) filter the candidates, or fall back to a LIKE scan over the indexed sections.
        """
        terms = [t for t in query.split() if t]
        if not terms:
            return []
        long_terms = [t for t in terms if len(t) >= SHORT_TERM]
        short_terms = [t.lower() for t in terms if len(t) < SHORT_TERM]
        try:
            if long_terms:
                rows = self.store.search_memory(long_terms, limit=200 if short_terms else limit)
            else:
                rows = self.store.scan_memory(short_terms, limit=400)
        except sqlite3.OperationalError:
            log.warning("memory search failed for %r", query, exc_info=True)
            return []
        hits = []
        for r in rows:
            low = r["body"].lower()
            if short_terms and not all(t in low for t in short_terms):
                continue
            snippet = r.get("snip") or self._snippet(r["body"], terms)
            hits.append({"path": r["path"], "scope": self.scope_label(r["scope"]), "section": r["section"],
                         "snippet": snippet, "score": r.get("rank", -sum(low.count(t.lower()) for t in terms))})
        hits.sort(key=lambda h: h["score"])
        return hits[:limit]

    @staticmethod
    def _snippet(body: str, terms: list[str], width: int = 120) -> str:
        low = body.lower()
        i = min((low.find(t.lower()) for t in terms if t.lower() in low), default=0)
        start = max(0, i - width // 3)
        return ("…" if start else "") + body[start:start + width].replace("\n", " ") + "…"

    # --- user actions ---

    def tracked_path(self, path: str) -> str | None:
        current = {str(p): scope for scope, p in self.files()}
        return current.get(os.path.normpath(path))

    def write(self, path: str, content: str, actor: str = "user") -> dict:
        scope = self.tracked_path(path)
        if scope is None:
            raise ValueError(tr("Not a tracked memory file"))
        before = Path(path).read_text(encoding="utf-8")
        if before == content:
            raise ValueError(tr("Nothing changed"))
        Path(path).write_text(content, encoding="utf-8")
        self._snapshot(path, scope, content)
        return self.record(path, scope, before, content, None, actor)

    def revert(self, change_id: int) -> dict:
        ch = self.store.get_memory_change(change_id)
        if not ch:
            raise LookupError(tr("No such change"))
        p = Path(ch["path"])
        now = p.read_text(encoding="utf-8") if p.exists() else ""
        if now != ch["after"]:
            raise RuntimeError(tr("The file changed again after that edit, so it can't be reverted directly; edit it by hand in the memory panel"))
        if ch["before"]:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(ch["before"], encoding="utf-8")
        else:
            p.unlink()  # the change created this file
        self._snapshot(ch["path"], ch["scope"], ch["before"])
        return self.record(ch["path"], ch["scope"], now, ch["before"], None, "user")

    # --- views ---

    def overview(self) -> list[dict]:
        groups: dict[str, dict] = {}
        for scope, p in self.files():
            g = groups.setdefault(scope, {"scope": scope, "label": self.scope_label(scope),
                                          "dir": str(p.parent), "files": []})
            content = read(p)
            if content is None:
                continue
            g["files"].append({"path": str(p), "name": self.display_name(scope, str(p)),
                               "content": content, "mtime": p.stat().st_mtime})
        for g in groups.values():
            g["stats"] = self.stats(g["scope"])
            lint = self.lint(g["scope"])
            g["lint"] = {"orphans": len(lint["orphans"]), "dead_links": len(lint["dead_links"]),
                         "stale": len(lint["stale"])}
            g["restructurable"] = self.memory_dir_of(g["scope"]) is not None and g["scope"] != "manager"
        order = {"profile": 0, "manager": 1}
        return sorted(groups.values(), key=lambda g: (order.get(g["scope"], 2),
                                                      -max((f["mtime"] for f in g["files"]), default=0)))

    def history(self, limit: int = 100) -> list[dict]:
        rows = self.store.memory_changes(limit)
        for r in rows:
            r["scope_label"] = self.scope_label(r["scope"])
            r["name"] = self.display_name(r["scope"], r["path"])
            r["kind"] = "created" if not r["before"] else "deleted" if not r["after"] else "modified"
            r["diff"] = unified_diff(r.pop("before"), r.pop("after"), r["name"])
        return rows
