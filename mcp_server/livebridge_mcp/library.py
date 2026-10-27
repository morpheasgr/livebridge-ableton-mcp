"""The user's LiveBridge library on the machine of the MCP server: saved device sounds
(``sounds/<slug>.json``) and the session memory (``memory.json``).

Everything lives under ``~/.livebridge`` (``%USERPROFILE%\\.livebridge`` on Windows) next to the
config file; ``LIVEBRIDGE_LIBRARY`` points it somewhere else (tests, a synced folder). Files are
plain UTF-8 JSON written atomically (temp file + rename), so a crash never leaves half a file.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

from .config import config_dir

__all__ = ["library_dir", "read_json", "slug", "write_json"]


def library_dir() -> Path:
    """The library folder (not created here)."""
    override = os.environ.get("LIVEBRIDGE_LIBRARY", "").strip()
    return Path(override).expanduser() if override else config_dir()


def slug(name: str) -> str:
    """A file-name-safe, lower-case form of ``name`` ("Reese Bass #2" -> "reese-bass-2")."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text[:80] or "untitled"


def read_json(path: Path, default: Any) -> Any:
    """The JSON content of ``path``, or ``default`` when it is missing or unreadable."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, data: Any) -> None:
    """Write ``data`` as indented JSON, atomically. Raises OSError when the folder is not
    writable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=1)
            stream.write("\n")
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise
