"""Which plug-in a ``.vstpreset`` belongs to: its VST3 class ID, turned into a plug-in name.

A ``.vstpreset`` starts with ``VST3``, a version and the 32-character hex class ID of the
plug-in it was saved from. The ID is not a name; it becomes one through

* the plug-in's own ``Contents/Resources/moduleinfo.json`` (VST3 SDK 3.7.5+ bundles list each
  class's ``CID`` and ``Name``; not every vendor ships it), found in the VST3 folders, and
* a table LiveBridge learns as it goes (``plugin_ids.json`` in ``~/.livebridge``;
  ``LIVEBRIDGE_LIBRARY`` moves it): a preset that
  changed a hosted plug-in's sound, or a Serum 2 template, ties its ID to that plug-in.

Used by ``live_plugin_host_preset`` to refuse a preset meant for another plug-in and to name a
host that does not know what it holds.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Iterable

from .library import library_dir

_TABLE = "plugin_ids.json"
_MAX_BUNDLES = 2000
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")


def class_id_of(path: str | Path) -> str | None:
    """The class ID (upper-case hex) of a ``.vstpreset`` file, or None when it is not one."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(40)
    except OSError:
        return None
    if len(head) < 40 or head[:4] != b"VST3":
        return None
    raw = head[8:40]
    try:
        int(raw, 16)
    except ValueError:
        return None
    return raw.decode("ascii").upper()


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def same_plugin(a: str | None, b: str | None) -> bool:
    """Two plug-in names for the same plug-in ("Serum 2" / "Serum2")."""
    return bool(a and b and _norm(a) == _norm(b))


def moduleinfo(bundle: Path) -> list[dict[str, str]]:
    """``[{cid, name, vendor, category}]`` from a ``.vst3`` bundle's moduleinfo.json."""
    info = bundle / "Contents" / "Resources" / "moduleinfo.json"
    try:
        text = info.read_text(encoding="utf-8-sig")
    except OSError:
        return []
    try:
        data = json.loads(_TRAILING_COMMA.sub(r"\1", text))   # the format allows trailing commas
    except ValueError:
        return []
    classes = []
    for entry in data.get("Classes") or [] if isinstance(data, dict) else []:
        if not isinstance(entry, dict) or not entry.get("CID"):
            continue
        classes.append({"cid": str(entry["CID"]).upper(), "name": str(entry.get("Name") or ""),
                        "vendor": str(entry.get("Vendor") or ""),
                        "category": str(entry.get("Category") or ""), "file": bundle.stem})
    return classes


def scan(folders: Iterable[Path]) -> dict[str, dict[str, str]]:
    """Every installed plug-in class with a moduleinfo.json: ``{cid: {name, file, vendor}}``
    (audio processor classes only)."""
    found: dict[str, dict[str, str]] = {}
    seen = 0
    for folder in folders:
        for root, dirs, _files in os.walk(folder):
            bundles = [d for d in dirs if d.lower().endswith(".vst3")]
            dirs[:] = [d for d in dirs if not d.lower().endswith(".vst3")
                       and not d.startswith(".")]
            for name in bundles:
                seen += 1
                for entry in moduleinfo(Path(root) / name):
                    if "audio module" in entry["category"].lower():
                        found.setdefault(entry["cid"], entry)
                if seen > _MAX_BUNDLES:
                    return found
    return found


def _table_path() -> Path:
    return library_dir() / _TABLE


def learned() -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(_table_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def learn(cid: str | None, name: str | None, file: str | None = None) -> None:
    """Remember that ``cid`` is plug-in ``name`` (never over a different known name)."""
    if not cid or not name or name == "plug-in":
        return
    table = learned()
    key = cid.upper()
    old = table.get(key)
    if old and old.get("name") and not same_plugin(old.get("name"), name):
        return
    table[key] = {"name": name, **({"file": file} if file else
                                   ({"file": old["file"]} if old and old.get("file") else {}))}
    path = _table_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(table, indent=1, sort_keys=True), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        pass


def lookup(cid: str | None, folders: Iterable[Path] | None = None) -> dict[str, str] | None:
    """``{name, file?, source}`` for a class ID: the learned table first, then the installed
    plug-ins' moduleinfo.json (when ``folders`` is given)."""
    if not cid:
        return None
    key = cid.upper()
    entry = learned().get(key)
    if entry and entry.get("name"):
        return {**entry, "source": "learned"}
    if folders is not None:
        found = scan(folders).get(key)
        if found:
            return {"name": found["name"], "file": found["file"], "source": "moduleinfo"}
    return None


def class_for(file: str | None, name: str | None, folders: Iterable[Path]) -> str | None:
    """The class ID of plug-in ``name`` in bundle ``file``: the learned table, else moduleinfo
    (by name, else the bundle's only audio class)."""
    for cid, entry in learned().items():
        if same_plugin(entry.get("name"), name):
            return cid
    classes = [c for c in scan(folders).values()
               if not file or _norm(c["file"]) == _norm(file)]
    named = [c for c in classes if same_plugin(c["name"], name)]
    if len(named) == 1:
        return named[0]["cid"]
    if file and len(classes) == 1:
        return classes[0]["cid"]
    return None
