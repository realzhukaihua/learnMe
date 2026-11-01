"""Settings: an optional learnme.toml, overlaid by LEARNME_* environment variables.

Every key has a default, so a fresh checkout runs with no config at all. See
learnme.example.toml for the full list.
"""

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
BACKENDS = ("claude", "codex", "deepseek")


def _toml() -> dict:
    path = Path(os.environ.get("LEARNME_CONFIG") or ROOT / "learnme.toml").expanduser()
    if not path.is_file():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


def _path(value: str | os.PathLike, base: Path = ROOT) -> Path:
    p = Path(value).expanduser()
    return p if p.is_absolute() else (base / p).resolve()


@dataclass
class Config:
    language: str = "en"                      # "en" | "zh": UI text and the prompts the agents get
    host: str = "127.0.0.1"
    port: int = 8770
    data_dir: Path = ROOT / "data"            # database, uploads, logs, settings; never committed
    profile_dir: Path = ROOT / "profile"      # the user's habits and judgment (see profile/README.md)
    project_roots: list[Path] = field(default_factory=lambda: [ROOT.parent])
    backend: str = "auto"                     # default backend for task windows; auto = the first one usable here
    manager_backend: str = ""                 # backend for the main window; empty = same as `backend`
    backends: dict[str, dict[str, Any]] = field(default_factory=dict)  # [backends.<name>] sections
    manager: dict[str, Any] = field(default_factory=dict)                # [manager] section

    @property
    def workspace(self) -> Path:
        """Where tasks that don't belong to any project run."""
        return self.data_dir / "workspace"

    @property
    def manager_memory_dir(self) -> Path:
        """The manager's own memory; its MEMORY.md is put into the manager's prompt on every backend."""
        return _path(self.manager.get("memory_dir") or self.data_dir / "manager-memory")

    def backend_opt(self, backend: str, key: str, default: Any = None) -> Any:
        return (self.backends.get(backend) or {}).get(key, default)

    @property
    def mcp_url(self) -> str:
        """Where backends that can't host tools in-process reach learnMe's MCP endpoint."""
        host = "127.0.0.1" if self.host in ("0.0.0.0", "::", "") else self.host
        return f"http://{host}:{self.port}/mcp"


def load() -> Config:
    raw = _toml()
    env = os.environ
    cfg = Config()
    cfg.language = (env.get("LEARNME_LANG") or raw.get("language") or cfg.language).lower()
    if cfg.language not in ("en", "zh"):
        cfg.language = "en"
    cfg.host = env.get("LEARNME_HOST") or raw.get("host") or cfg.host
    cfg.port = int(env.get("LEARNME_PORT") or raw.get("port") or cfg.port)
    if d := env.get("LEARNME_DATA_DIR") or raw.get("data_dir"):
        cfg.data_dir = _path(d)
    if d := env.get("LEARNME_PROFILE_DIR") or raw.get("profile_dir"):
        cfg.profile_dir = _path(d)
    roots = env.get("LEARNME_PROJECT_ROOTS")
    if roots is not None:
        cfg.project_roots = [_path(p) for p in roots.split(os.pathsep) if p]
    elif raw.get("project_roots"):
        cfg.project_roots = [_path(p) for p in raw["project_roots"]]
    cfg.backends = {k: dict(v) for k, v in (raw.get("backends") or {}).items() if isinstance(v, dict)}
    cfg.manager = dict(raw.get("manager") or {})
    cfg.backend = (env.get("LEARNME_BACKEND") or raw.get("backend") or cfg.backend).lower()
    cfg.manager_backend = (env.get("LEARNME_MANAGER_BACKEND") or raw.get("manager_backend") or cfg.backend).lower()
    for name in (cfg.backend, cfg.manager_backend):
        if name not in (*BACKENDS, "auto"):
            raise SystemExit(f"learnMe: unknown backend {name!r}; choose auto or one of {', '.join(BACKENDS)}")
    return cfg


CONFIG = load()
