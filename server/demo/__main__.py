"""`./run.sh demo`: a fresh demo instance in data/demo on port 8777 (LEARNME_DEMO_PORT), in the foreground."""

import os
import shutil
import sys
from pathlib import Path

from .fixtures import EVENTS_TOKEN, prepare

ROOT = Path(__file__).resolve().parent.parent.parent


def main() -> None:
    root = ROOT / "data" / "demo"
    shutil.rmtree(root, ignore_errors=True)  # every run starts the story over
    projects = root / "projects"
    config = prepare(root, projects)
    host = os.environ.get("LEARNME_HOST") or "127.0.0.1"
    port = int(os.environ.get("LEARNME_DEMO_PORT") or 8777)
    # Set before the server reads its configuration; the user's own learnme.toml and data are not touched.
    os.environ.update({
        "LEARNME_DEMO": "1", "LEARNME_CONFIG": str(config), "LEARNME_DATA_DIR": str(root),
        "LEARNME_PROFILE_DIR": str(root / "profile"), "LEARNME_PROJECT_ROOTS": str(projects),
        "LEARNME_LANG": "en", "LEARNME_BACKEND": "claude", "LEARNME_MANAGER_BACKEND": "claude",
        "LEARNME_EVENTS_TOKEN": EVENTS_TOKEN, "LEARNME_HOST": host, "LEARNME_PORT": str(port),
    })
    import uvicorn

    shown = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    print(f"learnMe demo → http://{shown}:{port}  (scripted agents, no key needed; Ctrl+C to stop)", flush=True)
    print("The story starts when you open the page and takes about a minute.", flush=True)
    uvicorn.run("server.main:app", host=host, port=port, log_level="warning")


if __name__ == "__main__":
    sys.exit(main())
