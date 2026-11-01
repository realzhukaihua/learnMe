"""Test settings: every test run gets its own data, profile and project folders, set before
`server` is imported (its config is read once at import)."""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="learnme-test-"))
for name in ("data", "profile", "projects/demo"):
    (TMP / name).mkdir(parents=True, exist_ok=True)
os.environ.update({
    "LEARNME_DATA_DIR": str(TMP / "data"),
    "LEARNME_PROFILE_DIR": str(TMP / "profile"),
    "LEARNME_PROJECT_ROOTS": str(TMP / "projects"),
    "LEARNME_CONFIG": str(TMP / "no-config.toml"),
    "LEARNME_BACKEND": "claude",
    "LEARNME_LANG": "en",
})
os.environ.pop("LEARNME_EVENTS_TOKEN", None)
sys.path.insert(0, str(ROOT))
