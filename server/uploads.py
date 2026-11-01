"""Files attached in the console's message boxes.

The browser uploads each file here first; the message it then sends carries the saved
absolute paths, which the agent opens with its file tools (images, PDFs, tables alike).
"""

import re
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from .config import CONFIG
from .i18n import tr

UPLOADS = CONFIG.data_dir / "uploads"
MAX_MB = 200
MAX_BYTES = MAX_MB * 1024 * 1024
NAME_BYTES = 150  # well under the 255-byte filesystem limit, multi-byte names included
# Uploaded HTML/SVG opened from the console must not run script on its origin.
SAFE_HEADERS = {"Content-Security-Policy": "sandbox", "X-Content-Type-Options": "nosniff"}

router = APIRouter()


def clean_name(name: str) -> str:
    """Keep only the last path component, made of letters, digits and . - _ + ( ) @."""
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[^\w.\-+()@]+", "_", name)
    name = re.sub(r"\.{2,}", ".", name).strip("._ ") or "file"
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem or len(ext) > 16:
        stem, ext = name, ""
    ext = f".{ext}" if ext else ""
    while len((stem + ext).encode()) > NAME_BYTES:
        stem = stem[:-1]
    return stem + ext


def claim(folder: Path, name: str):
    """Create the file exclusively, adding -1, -2… instead of overwriting an existing one."""
    stem, ext = Path(name).stem, Path(name).suffix
    for i in range(10000):
        path = folder / (f"{stem}-{i}{ext}" if i else name)
        try:
            return path.open("xb"), path
        except FileExistsError:
            continue
    raise HTTPException(409, tr("Too many files with the same name"))


@router.post("/api/uploads")
async def upload(request: Request, name: str):
    try:
        declared = int(request.headers.get("content-length") or 0)
    except ValueError:
        declared = 0
    if declared > MAX_BYTES:
        raise HTTPException(413, tr("File is over the {mb}MB limit", mb=MAX_MB))
    day = UPLOADS / time.strftime("%Y-%m-%d")
    day.mkdir(parents=True, exist_ok=True)
    f, path = claim(day, clean_name(name))
    size = 0
    try:
        with f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(413, tr("File is over the {mb}MB limit", mb=MAX_MB))
                f.write(chunk)
    except BaseException:  # too big, client went away, disk full: leave no partial file
        path.unlink(missing_ok=True)
        raise
    return {"name": path.name, "path": str(path), "size": size, "url": f"/api/uploads/{day.name}/{path.name}"}


@router.get("/api/uploads/{day}/{name}")
async def download(day: str, name: str):
    path = (UPLOADS / day / name).resolve()
    if path.parent.parent != UPLOADS.resolve() or not path.is_file():
        raise HTTPException(404, tr("No such attachment"))
    return FileResponse(path, headers=SAFE_HEADERS)
