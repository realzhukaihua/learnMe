"""SQLite persistence: tasks, their event streams, and the user's decisions.

The `decisions` table is the point of this project: every time the user steers a
task (continue / correct / interrupt / end / approve ...) we record what they did
together with a snapshot of what the task looked like at that moment.
"""

import json
import re
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id              TEXT PRIMARY KEY,
    kind            TEXT NOT NULL DEFAULT 'task',   -- 'task' | 'manager'
    origin          TEXT NOT NULL DEFAULT 'user',   -- who dispatched it: 'user' | 'manager' | 'slack' | 'event'
    title           TEXT NOT NULL,
    cwd             TEXT NOT NULL,
    model           TEXT,
    permission_mode TEXT,
    status          TEXT NOT NULL,
    session_id      TEXT,
    cost_usd        REAL NOT NULL DEFAULT 0,
    turns           INTEGER NOT NULL DEFAULT 0,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    turn_started_at REAL,
    end_reason      TEXT,
    fork_from       TEXT,   -- a session this window was restored from (forked on first use)
    ctx_tokens      INTEGER, -- context window in use, as /context counts it
    ctx_window      INTEGER, -- the model's full context window
    backend         TEXT     -- the agent backend this window runs on (server/backends)
);
CREATE TABLE IF NOT EXISTS events (
    task_id TEXT NOT NULL,
    seq     INTEGER NOT NULL,
    ts      REAL NOT NULL,
    type    TEXT NOT NULL,
    data    TEXT NOT NULL,
    PRIMARY KEY (task_id, seq)
);
CREATE TABLE IF NOT EXISTS decisions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id       TEXT NOT NULL,
    ts            REAL NOT NULL,
    actor         TEXT NOT NULL,   -- 'user' (in the task window) | 'manager' (via the main window) | 'auto' | ...
    action        TEXT NOT NULL,
    text          TEXT,
    cwd           TEXT,
    status_before TEXT,
    context       TEXT
);
CREATE INDEX IF NOT EXISTS decisions_task ON decisions (task_id, ts);
CREATE TABLE IF NOT EXISTS memory_files (      -- last content seen for each tracked memory file
    path    TEXT PRIMARY KEY,
    scope   TEXT NOT NULL,
    content TEXT NOT NULL,
    ts      REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_changes (    -- full history; every change is revertible
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL NOT NULL,
    path    TEXT NOT NULL,
    scope   TEXT NOT NULL,
    task_id TEXT,
    actor   TEXT NOT NULL,   -- 'user' | 'manager' | 'task' | 'external'
    before  TEXT NOT NULL,
    after   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (               -- small bits of state that must survive restarts
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS claude_sessions (   -- scan cache of ~/.claude/projects/*/*.jsonl
    path         TEXT PRIMARY KEY,
    mtime        REAL NOT NULL,
    size         INTEGER NOT NULL,
    session_id   TEXT,
    cwd          TEXT,
    title        TEXT,
    first_prompt TEXT,
    last_prompt  TEXT,
    started      TEXT,
    ended        TEXT,
    entrypoint   TEXT,
    prompts      INTEGER
);
-- Full-text index over every tracked memory file, one row per markdown section.
-- trigram tokenizes CJK without a segmenter (terms need >= 3 chars).
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    path UNINDEXED, scope UNINDEXED, section, body, tokenize = 'trigram'
);
"""

CHUNK_CHARS = 2000
HEADING = re.compile(r"^#{1,6}\s")


def chunk_markdown(text: str) -> list[tuple[str, str]]:
    """Split a memory file into (section heading, body) chunks of bounded size."""
    chunks, heading, buf = [], "", []

    def flush():
        body = "\n".join(buf).strip()
        while body:
            chunks.append((heading, body[:CHUNK_CHARS]))
            body = body[CHUNK_CHARS:]

    for line in text.splitlines():
        if HEADING.match(line):
            flush()
            heading, buf = line.lstrip("#").strip(), [line]
        else:
            buf.append(line)
    flush()
    return chunks

TASK_FIELDS = {
    "title", "model", "permission_mode", "status", "session_id", "cost_usd",
    "turns", "updated_at", "turn_started_at", "end_reason", "fork_from", "ctx_tokens", "ctx_window", "backend",
}
MIGRATIONS = {
    ("tasks", "fork_from"): "ALTER TABLE tasks ADD COLUMN fork_from TEXT",
    ("tasks", "ctx_tokens"): "ALTER TABLE tasks ADD COLUMN ctx_tokens INTEGER",
    ("tasks", "ctx_window"): "ALTER TABLE tasks ADD COLUMN ctx_window INTEGER",
    ("tasks", "backend"): "ALTER TABLE tasks ADD COLUMN backend TEXT",
}


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        for (table, column), sql in MIGRATIONS.items():
            if column not in {r[1] for r in self.db.execute(f"PRAGMA table_info({table})")}:
                self.db.execute(sql)
        self.lock = threading.Lock()

    def _exec(self, sql: str, args=()):
        with self.lock:
            return self.db.execute(sql, args)

    # --- tasks ---

    def create_task(self, task: dict) -> None:
        cols = ", ".join(task)
        marks = ", ".join("?" for _ in task)
        self._exec(f"INSERT INTO tasks ({cols}) VALUES ({marks})", tuple(task.values()))

    def update_task(self, task_id: str, **fields) -> None:
        bad = set(fields) - TASK_FIELDS
        if bad:
            raise ValueError(f"unknown task fields: {bad}")
        fields.setdefault("updated_at", time.time())
        sets = ", ".join(f"{k} = ?" for k in fields)
        self._exec(f"UPDATE tasks SET {sets} WHERE id = ?", (*fields.values(), task_id))

    def get_task(self, task_id: str) -> dict | None:
        row = self._exec("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return dict(row) if row else None

    def list_tasks(self, limit: int = 300) -> list[dict]:
        rows = self._exec(
            "SELECT * FROM tasks WHERE kind = 'task' ORDER BY created_at DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows.fetchall()]

    def tasks_with_status(self, statuses: tuple[str, ...]) -> list[dict]:
        marks = ", ".join("?" for _ in statuses)
        rows = self._exec(f"SELECT * FROM tasks WHERE status IN ({marks})", statuses)
        return [dict(r) for r in rows.fetchall()]

    def project_usage(self) -> dict[str, dict]:
        rows = self._exec(
            "SELECT cwd, COUNT(*) AS uses, MAX(created_at) AS last_used FROM tasks "
            "WHERE kind = 'task' GROUP BY cwd"
        ).fetchall()
        return {r["cwd"]: {"uses": r["uses"], "last_used": r["last_used"]} for r in rows}

    def task_cwds(self) -> list[str]:
        return [r[0] for r in self._exec("SELECT DISTINCT cwd FROM tasks WHERE kind = 'task'").fetchall()]

    def project_tasks(self, cwd: str, limit: int = 30) -> list[dict]:
        rows = self._exec(
            "SELECT * FROM tasks WHERE kind = 'task' AND cwd = ? ORDER BY created_at DESC LIMIT ?",
            (cwd, limit))
        return [dict(r) for r in rows.fetchall()]

    def first_prompt(self, task_id: str) -> str | None:
        row = self._exec(
            "SELECT data FROM events WHERE task_id = ? AND type = 'user' ORDER BY seq LIMIT 1",
            (task_id,)).fetchone()
        return json.loads(row[0]).get("text") if row else None

    def search_decisions(self, query: str, limit: int = 20) -> list[dict]:
        like = f"%{query}%"
        rows = self._exec(
            "SELECT d.*, t.title FROM decisions d LEFT JOIN tasks t ON t.id = d.task_id "
            "WHERE d.text LIKE ? OR t.title LIKE ? OR d.context LIKE ? ORDER BY d.ts DESC LIMIT ?",
            (like, like, like, limit))
        return [dict(r) for r in rows.fetchall()]

    def search_tasks(self, query: str, limit: int = 20) -> list[dict]:
        like = f"%{query}%"
        rows = self._exec(
            "SELECT DISTINCT t.* FROM tasks t LEFT JOIN events e ON e.task_id = t.id AND e.type IN ('user', 'text') "
            "WHERE t.kind = 'task' AND (t.title LIKE ? OR t.end_reason LIKE ? OR e.data LIKE ?) "
            "ORDER BY t.updated_at DESC LIMIT ?", (like, like, like, limit))
        return [dict(r) for r in rows.fetchall()]

    # --- memory ---

    def memory_snapshots(self) -> dict[str, dict]:
        rows = self._exec("SELECT path, scope, content FROM memory_files").fetchall()
        return {r["path"]: {"scope": r["scope"], "content": r["content"]} for r in rows}

    def set_memory_snapshot(self, path: str, scope: str, content: str) -> None:
        self._exec(
            "INSERT INTO memory_files (path, scope, content, ts) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET scope = excluded.scope, content = excluded.content, ts = excluded.ts",
            (path, scope, content, time.time()))

    def add_memory_change(self, path: str, scope: str, task_id: str | None, actor: str,
                          before: str, after: str) -> int:
        cur = self._exec(
            "INSERT INTO memory_changes (ts, path, scope, task_id, actor, before, after) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (time.time(), path, scope, task_id, actor, before, after))
        return cur.lastrowid

    def get_memory_change(self, change_id: int) -> dict | None:
        row = self._exec("SELECT * FROM memory_changes WHERE id = ?", (change_id,)).fetchone()
        return dict(row) if row else None

    def index_memory_file(self, path: str, scope: str, text: str) -> None:
        with self.lock:
            self.db.execute("DELETE FROM memory_fts WHERE path = ?", (path,))
            self.db.executemany(
                "INSERT INTO memory_fts (path, scope, section, body) VALUES (?, ?, ?, ?)",
                [(path, scope, sec, body) for sec, body in chunk_markdown(text)])

    def memory_fts_paths(self) -> set[str]:
        return {r[0] for r in self._exec("SELECT DISTINCT path FROM memory_fts").fetchall()}

    def search_memory(self, terms: list[str], limit: int = 12) -> list[dict]:
        """All terms must match (trigram, so each term >= 3 chars); best BM25 first."""
        match = " AND ".join('"' + t.replace('"', '""') + '"' for t in terms)
        rows = self._exec(
            "SELECT path, scope, section, body, snippet(memory_fts, 3, '«', '»', '…', 24) AS snip, "
            "bm25(memory_fts, 0, 0, 2.0, 1.0) AS rank FROM memory_fts WHERE memory_fts MATCH ? "
            "ORDER BY rank LIMIT ?", (match, limit))
        return [dict(r) for r in rows.fetchall()]

    def scan_memory(self, terms: list[str], limit: int = 400) -> list[dict]:
        """Substring scan for terms too short for the trigram index (e.g. 2-char Chinese words)."""
        where = " AND ".join("body LIKE ?" for _ in terms)
        rows = self._exec(f"SELECT path, scope, section, body FROM memory_fts WHERE {where} LIMIT ?",
                          (*[f"%{t}%" for t in terms], limit))
        return [dict(r) for r in rows.fetchall()]

    def memory_changes(self, limit: int = 100) -> list[dict]:
        rows = self._exec("SELECT * FROM memory_changes ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows.fetchall()]

    def last_message(self, task_id: str) -> dict | None:
        row = self._exec(
            "SELECT type, data, ts FROM events WHERE task_id = ? AND type IN ('text', 'user') "
            "ORDER BY seq DESC LIMIT 1", (task_id,)).fetchone()
        return {"type": row["type"], "ts": row["ts"], **json.loads(row["data"])} if row else None

    # --- key/value ---

    def get_kv(self, key: str, default=None):
        row = self._exec("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_kv(self, key: str, value) -> None:
        self._exec("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, json.dumps(value)))

    # --- Claude Code session scan cache ---

    def claude_session_cache(self) -> dict[str, dict]:
        return {r["path"]: dict(r) for r in self._exec("SELECT * FROM claude_sessions").fetchall()}

    def put_claude_session(self, row: dict) -> None:
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        self._exec(f"INSERT OR REPLACE INTO claude_sessions ({cols}) VALUES ({marks})", tuple(row.values()))

    def drop_claude_sessions(self, paths: list[str]) -> None:
        for path in paths:
            self._exec("DELETE FROM claude_sessions WHERE path = ?", (path,))

    def known_session_ids(self) -> set[str]:
        rows = self._exec("SELECT session_id FROM tasks WHERE session_id IS NOT NULL").fetchall()
        return {r[0] for r in rows}

    # --- events ---

    def add_event(self, task_id: str, seq: int, ts: float, type_: str, data: dict) -> None:
        self._exec(
            "INSERT INTO events (task_id, seq, ts, type, data) VALUES (?, ?, ?, ?, ?)",
            (task_id, seq, ts, type_, json.dumps(data, ensure_ascii=False)),
        )

    def events(self, task_id: str, after: int = 0) -> list[dict]:
        rows = self._exec(
            "SELECT seq, ts, type, data FROM events WHERE task_id = ? AND seq > ? ORDER BY seq",
            (task_id, after),
        ).fetchall()
        return [_event_row(task_id, r) for r in rows]

    def recent_events(self, task_id: str, n: int) -> list[dict]:
        rows = self._exec(
            "SELECT seq, ts, type, data FROM events WHERE task_id = ? ORDER BY seq DESC LIMIT ?",
            (task_id, n),
        ).fetchall()
        return [_event_row(task_id, r) for r in reversed(rows)]

    def max_seq(self, task_id: str) -> int:
        row = self._exec("SELECT MAX(seq) FROM events WHERE task_id = ?", (task_id,)).fetchone()
        return row[0] or 0

    def cleared_seq(self, task_id: str) -> int:
        """Seq of the window's last 'clear' event (clear conversation), 0 if never cleared."""
        row = self._exec("SELECT MAX(seq) FROM events WHERE task_id = ? AND type = 'clear'", (task_id,)).fetchone()
        return row[0] or 0

    # --- decisions ---

    def add_decision(self, task_id: str, actor: str, action: str, text: str | None, cwd: str,
                     status_before: str, context: dict) -> None:
        self._exec(
            "INSERT INTO decisions (task_id, ts, actor, action, text, cwd, status_before, context) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (task_id, time.time(), actor, action, text, cwd, status_before,
             json.dumps(context, ensure_ascii=False)),
        )

    def decisions(self, limit: int = 200, task_id: str | None = None) -> list[dict]:
        if task_id:
            rows = self._exec(
                "SELECT * FROM decisions WHERE task_id = ? ORDER BY ts DESC LIMIT ?", (task_id, limit))
        else:
            rows = self._exec("SELECT * FROM decisions ORDER BY ts DESC LIMIT ?", (limit,))
        out = []
        for r in rows.fetchall():
            d = dict(r)
            d["context"] = json.loads(d["context"] or "{}")
            out.append(d)
        return out


def _event_row(task_id: str, r: sqlite3.Row) -> dict:
    return {"task_id": task_id, "seq": r["seq"], "ts": r["ts"], "type": r["type"], **json.loads(r["data"])}
