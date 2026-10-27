"""Sample search over Live's own browser database — everything Live's browser shows under
Library > Samples (Core Library, packs, User Library and every folder the user added to
Places), with Live's browser tags ("Drums|Kick", "Character|Punchy", "Type|One Shot").

Live's Python API only lists a fraction of these (``browser.samples``: Core Library, packs, User
Library) and no tags; Live's browser database has all of them:

* ``%LOCALAPPDATA%\\Ableton\\Live Database\\Live-files-<N>.db`` (Windows) or
  ``~/Library/Application Support/Ableton/Live Database/Live-files-<N>.db`` (macOS), highest N.
* ``files(file_id, parent_id, name, place_id, ...)`` — every indexed file and folder; a path
  is rebuilt by walking ``parent_id`` up to the place's root folder.
* ``places(file_id, folder_kind, level, name)`` — the roots ("Core Library", "User Library",
  packs, user folders such as "Samples").
* ``metadata(file_id, key, value_id)`` + ``metadata_values(id, value)`` — browser metadata
  keyed by four-letter codes; the browser tags are the ``"Group|Tag"`` values.
* ``files.use_count`` — how often Live loaded the file; ``places.folder_kind`` — what kind of
  place it is (packs incl. the Core Library, User Library, added folders ...).
* ``fe_values(file_id, data, hash)`` — Live's audio-similarity features (``data``: version,
  dimension count, reserved, then that many little-endian float32) and a hash of the audio
  (equal hash + data = the same audio shipped twice).
* ``vfolders(vfolder_id, group_id, name)`` + ``vfolder_patterns(vfolder_id, name)`` — the
  browser's sound categories ("Bass", "Booms", "Ambient & Evolving" ...) and the words that put
  a sample in them.
* the "Plugins" place lists every installed plug-in: ``files.device_id`` like
  ``device:vst3:instr:<id>?n=<name>``, ``device_type`` 1/2/4 = instrument/audio/MIDI effect.
* ``files`` keeps every folder up to the drive root (``C:\\`` -> ``ProgramData`` -> ...), so
  absolute paths come from the database itself.

**Live's database is never opened.** :func:`read_live_database` copies the database and its
``-wal`` file together into a temporary folder, reads the copy and deletes it; Live keeps the
original open. All of this runs in the MCP server process, never inside Live.

The result is a small local index (``~/.livebridge/sample_index.sqlite``) that answers searches
in milliseconds and is rebuilt when Live's database changes.

Root folders: the database stores paths relative to each place's root, so absolute paths need
the root folders. :func:`resolve_roots` tries, per place: the user's ``sample_index.roots``
setting, absolute paths stored in the database, the folders Live reports (Core Library next to
the running Live, User Library and Factory Packs from ``Library.cfg``), then folders of the same
name (``D:\\Samples``, ``%USERPROFILE%\\Music`` ...). A candidate is only accepted when some of
the place's files really exist under it.
"""

from __future__ import annotations

import json
import ntpath
import os
import re
import shutil
import sqlite3
import struct
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

__all__ = [
    "AUDIO_EXTENSIONS", "SampleIndexError", "build_index", "duplicates", "similar", "database_dirs", "find_live_database",
    "index_status", "read_live_database", "resolve_roots", "search", "source_signature",
    "tag_counts",
]

AUDIO_EXTENSIONS = (".wav", ".aif", ".aiff", ".mp3", ".flac", ".ogg", ".m4a")
SCHEMA_VERSION = 3
_DB_RE = re.compile(r"^Live-files-(\d+)\.db$", re.I)
_ABSOLUTE_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|/)")
_SPLIT_RE = re.compile(r"[\\/_\-.\s]+")
_VERIFY_SAMPLES = 8
_MAX_DEPTH = 64
_BUILD_LOCK = threading.Lock()
#: places.folder_kind -> what the place is (Live 12.4, Live-files-12300.db).
PLACE_KINDS = {0: "pack", 1: "user_library", 2: "user_folder", 4: "current_project",
               5: "plugin_presets", 8: "built_in", 9: "cloud", 10: "plugins"}
SOURCES = ("core_library", "pack", "user_library", "user_folder", "current_project", "other")


class SampleIndexError(RuntimeError):
    """The index cannot be built or read (message is meant for the user)."""


# ----------------------------------------------------------------------------- finding

def database_dirs(env: Mapping[str, str] | None = None, platform: str | None = None,
                  home: str | None = None) -> list[Path]:
    """Where Live keeps its browser database on this machine."""
    env = os.environ if env is None else env
    platform = platform or sys.platform
    home = home or os.path.expanduser("~")
    if platform.startswith("win"):
        local = env.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
        return [Path(local) / "Ableton" / "Live Database"]
    if platform == "darwin":
        return [Path(home) / "Library" / "Application Support" / "Ableton" / "Live Database"]
    return [Path(home) / ".local" / "share" / "Ableton" / "Live Database"]


def _newest_in(folder: Path) -> Path | None:
    try:
        found = [(int(m.group(1)), p) for p in folder.iterdir()
                 if (m := _DB_RE.match(p.name)) and p.is_file()]
    except OSError:
        return None
    return max(found)[1] if found else None


def find_live_database(override: str | None = None,
                       env: Mapping[str, str] | None = None,
                       platform: str | None = None, home: str | None = None) -> Path | None:
    """``Live-files-<N>.db`` with the highest N. ``override`` (a file or a folder) wins."""
    if override:
        path = Path(os.path.expanduser(override))
        if path.is_dir():
            return _newest_in(path)
        return path if path.is_file() else None
    for folder in database_dirs(env, platform, home):
        found = _newest_in(folder)
        if found:
            return found
    return None


def source_signature(path: Path) -> dict[str, Any]:
    """Modification time and size of the database and its -wal file (what "changed" means)."""
    def stat(p: Path) -> list[int] | None:
        try:
            info = p.stat()
        except OSError:
            return None
        return [info.st_mtime_ns, info.st_size]
    return {"db": stat(path), "wal": stat(Path(str(path) + "-wal"))}


# ----------------------------------------------------------------------------- reading

def _columns(con: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in con.execute(f"PRAGMA table_info({table})")]


def _features(blob: Any) -> bytes | None:
    """The float32 payload of one ``fe_values.data`` blob (header: version, dimensions,
    reserved — three little-endian uint32), or None when it does not look like one."""
    if not isinstance(blob, (bytes, bytearray)) or len(blob) < 16:
        return None
    _version, dims, _reserved = struct.unpack_from("<3I", blob)
    if not 1 <= dims <= 4096 or len(blob) != 12 + 4 * dims:
        return None
    return bytes(blob[12:])


def _read_copy(copy: Path) -> dict[str, Any]:
    con = sqlite3.connect(str(copy))
    try:
        file_columns = _columns(con, "files")
        if not {"file_id", "parent_id", "name", "place_id"} <= set(file_columns):
            raise SampleIndexError("Live's database has an unexpected layout (table files: "
                              f"{', '.join(file_columns) or 'missing'})")
        extra = [c for c in ("file_size", "mod_date", "use_count") if c in file_columns]
        select = ", ".join(["file_id", "parent_id", "name", "place_id"] + extra)
        files = {row[0]: row[1:] for row in con.execute(f"SELECT {select} FROM files")}
        place_columns = _columns(con, "places")
        places = [dict(zip(place_columns, row))
                  for row in con.execute("SELECT * FROM places")]
        roots: dict[int, dict[str, Any]] = {}
        ids = [p.get("file_id") for p in places if p.get("file_id") is not None]
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            marks = ",".join("?" * len(chunk))
            cursor = con.execute(f"SELECT * FROM files WHERE file_id IN ({marks})", chunk)
            names = [d[0] for d in cursor.description]
            for row in cursor:
                record = dict(zip(names, row))
                roots[record["file_id"]] = record
        tags: dict[int, list[str]] = {}
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if {"metadata", "metadata_values"} <= tables:
            for file_id, value in con.execute(
                    "SELECT m.file_id, v.value FROM metadata m "
                    "JOIN metadata_values v ON v.id = m.value_id"):
                # other metadata (pack name, vendor, URLs, years) is not a browser tag
                if isinstance(value, str) and "|" in value.strip("|"):
                    tags.setdefault(file_id, []).append(value)
        vectors: dict[int, tuple[int | None, bytes]] = {}
        if "fe_values" in tables and {"file_id", "data"} <= set(_columns(con, "fe_values")):
            has_hash = "hash" in _columns(con, "fe_values")
            for file_id, blob, digest in con.execute(
                    "SELECT file_id, data, " + ("hash" if has_hash else "NULL")
                    + " FROM fe_values"):
                features = _features(blob)
                if features is not None:
                    vectors[file_id] = (digest, features)
        categories: dict[int, dict[str, Any]] = {}
        if {"vfolders", "vfolder_patterns"} <= tables:
            for vid, group_id, name in con.execute(
                    "SELECT vfolder_id, group_id, name FROM vfolders"):
                if isinstance(name, str) and name:
                    categories[vid] = {"group_id": group_id, "name": name, "patterns": []}
            for vid, pattern in con.execute("SELECT vfolder_id, name FROM vfolder_patterns"):
                if vid in categories and isinstance(pattern, str) and pattern.strip():
                    categories[vid]["patterns"].append(pattern.strip())
        plugins: list[tuple[Any, ...]] = []
        if {"device_id", "device_type"} <= set(file_columns):
            plugins = con.execute(
                "SELECT file_id, parent_id, name, place_id, device_id, device_type FROM files "
                "WHERE device_id LIKE 'device:%' AND device_id NOT LIKE 'device:ableton:%' "
                "AND device_id NOT LIKE 'device:max:%'").fetchall()
    finally:
        con.close()
    return {"files": files, "extra_columns": extra, "places": places, "roots": roots,
            "tags": tags, "vectors": vectors, "categories": categories, "plugins": plugins}


def read_live_database(path: Path, attempts: int = 2) -> dict[str, Any]:
    """Read Live's database through a temporary copy (database + -wal, copied together).

    The original is only ever read as a file (``shutil.copy2``); SQLite never opens it, so
    Live's database cannot be changed or locked. The copy is deleted afterwards.
    """
    last: Exception | None = None
    for attempt in range(attempts):
        folder = tempfile.mkdtemp(prefix="LiveBridgeDB_")
        try:
            copy = Path(folder) / path.name
            shutil.copy2(path, copy)
            wal = Path(str(path) + "-wal")
            if wal.exists():
                shutil.copy2(wal, Path(str(copy) + "-wal"))
            return _read_copy(copy)
        except SampleIndexError:
            raise
        except (sqlite3.DatabaseError, OSError) as exc:
            # Live may have been writing while we copied: copy again once.
            last = exc
            time.sleep(0.5 * (attempt + 1))
        finally:
            shutil.rmtree(folder, ignore_errors=True)
    raise SampleIndexError(f"could not read a copy of {path}: {last}")


# ----------------------------------------------------------------------------- paths

def _paths(data: dict[str, Any], sep: str
           ) -> dict[int, tuple[int, str | None, str | None]]:
    """``{file_id: (place_id, path relative to its place's root folder | None, absolute path
    from the folders the database keeps up to the drive | None)}`` for every audio file."""
    files = data["files"]
    absolute: dict[int, str | None] = {}

    def folder_abs(fid: int) -> str | None:
        chain = []
        while fid in files and fid not in absolute and len(chain) <= _MAX_DEPTH:
            chain.append(fid)
            fid = files[fid][0]
        if fid in absolute:
            base = absolute[fid]
        else:                                   # reached the top of the tree
            top = chain.pop() if chain else None
            name = files[top][1] if top is not None else None
            base = name if isinstance(name, str) and _ABSOLUTE_RE.match(name) else None
            if top is not None:
                absolute[top] = base
        for folder in reversed(chain):
            name = files[folder][1] or ""
            base = os.path.join(base, name) if base is not None else None
            absolute[folder] = base
        return base

    result = {}
    for fid, row in files.items():
        parent, name, place = row[0], row[1], row[2]
        if not isinstance(name, str) or not name.lower().endswith(AUDIO_EXTENSIONS):
            continue
        parts, current, steps = [name], parent, 0
        while current != place and current in files and steps < _MAX_DEPTH:
            parts.append(files[current][1] or "")
            current = files[current][0]
            steps += 1
        rel = sep.join(reversed(parts)) if current == place else None
        base = folder_abs(parent) if parent in files else None
        result[fid] = (place, rel, os.path.join(base, name) if base else None)
    return result


def _absolute_strings(record: Mapping[str, Any]) -> list[str]:
    found = []
    for value in record.values():
        if not isinstance(value, str):
            continue
        text = value
        if text.startswith("file://"):
            text = text[7:]
            if re.match(r"^/[A-Za-z]:/", text):
                text = text[1:]
        if _ABSOLUTE_RE.match(text):
            found.append(text)
    return found


def _ancestry_root(data: dict[str, Any], root_id: int) -> str | None:
    """An absolute path for a place root from folders the database keeps above it."""
    files = data["files"]
    names, fid, steps = [], root_id, 0
    while fid is not None and fid in files and steps < _MAX_DEPTH:
        names.append(files[fid][1] or "")
        fid = files[fid][0]
        steps += 1
    if len(names) < 2 or not _ABSOLUTE_RE.match(names[-1]):
        return None
    top, rest = names[-1], list(reversed(names[:-1]))
    return os.path.join(top, *rest)


def _drives() -> list[str]:
    if not sys.platform.startswith("win"):
        return []
    lister = getattr(os, "listdrives", None)
    if lister is not None:
        try:
            return list(lister())
        except OSError:
            return []
    return [f"{letter}:\\" for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ"
            if os.path.isdir(f"{letter}:\\")]


def _installed_core_libraries() -> list[str]:
    """Core Library folders of installed Lives at the default install places (used when Live
    itself cannot be asked), newest version name first."""
    import glob
    if sys.platform.startswith("win"):
        data = os.environ.get("ProgramData") or "C:\\ProgramData"
        pattern = os.path.join(data, "Ableton", "Live *", "Resources", "Core Library")
    elif sys.platform == "darwin":
        pattern = "/Applications/Ableton Live *.app/Contents/App-Resources/Core Library"
    else:
        return []
    return sorted(glob.glob(pattern), reverse=True)


def _hint_paths(hints: Mapping[str, Any]) -> list[str]:
    """Every folder path in a ``samples.locations`` answer."""
    paths: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if isinstance(value.get("path"), str):
                paths.append(value["path"])
            for key, inner in value.items():
                if key != "path":
                    walk(inner)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, str) and _ABSOLUTE_RE.match(value):
            paths.append(value)
    walk(dict(hints))
    unique = []
    for path in paths:
        if path not in unique:
            unique.append(path)
    return unique


def _first_path(entry: Any) -> str | None:
    if isinstance(entry, dict):
        return entry.get("path") if isinstance(entry.get("path"), str) else None
    if isinstance(entry, list):
        for item in entry:
            if isinstance(item, dict) and item.get("exists") and item.get("path"):
                return item["path"]
        for item in entry:
            path = _first_path(item)
            if path:
                return path
    if isinstance(entry, str):
        return entry
    return None


def _basename(path: str) -> str:
    return ntpath.basename(path.rstrip("\\/")) if "\\" in path else os.path.basename(
        path.rstrip("/"))


def _candidates(name: str, place: Mapping[str, Any], root_row: Mapping[str, Any] | None,
                data: dict[str, Any], overrides: Mapping[str, str],
                hints: Mapping[str, Any], home: str, drives: Iterable[str]
                ) -> list[tuple[str, str]]:
    lowered = name.strip().lower()
    out: list[tuple[str, str]] = []
    for key, value in overrides.items():
        if str(key).strip().lower() == lowered and value:
            out.append((os.path.expanduser(str(value)), "config"))
    for text in _absolute_strings(place) + _absolute_strings(root_row or {}):
        out.append((text, "database"))
    if root_row is not None:
        above = _ancestry_root(data, root_row["file_id"])
        if above:
            out.append((above, "database"))
    if hints:
        if lowered == "core library":
            core = _first_path(hints.get("core_library"))
            if core:
                out.append((core, "live"))
        if lowered == "user library":
            user = _first_path(hints.get("user_library"))
            if user:
                out.append((user, "live"))
        packs = _first_path(hints.get("factory_packs"))
        if packs:
            out.append((os.path.join(packs, name), "live"))
        for path in _hint_paths(hints):
            if _basename(path).lower() == lowered:
                out.append((path, "live"))
    if lowered == "core library":
        out.extend((path, "guess") for path in _installed_core_libraries())
    for base in [home, os.path.join(home, "Documents"), os.path.join(home, "Music"),
                 os.path.join(home, "Desktop"), os.path.join(home, "Documents", "Ableton"),
                 os.path.join(home, "Music", "Ableton")] + list(drives):
        out.append((os.path.join(base, name), "guess"))
    unique, seen = [], set()
    for path, source in out:
        key = os.path.normcase(os.path.normpath(path))
        if key not in seen:
            seen.add(key)
            unique.append((path, source))
    return unique


def _verified(root: str, rel_paths: list[str]) -> bool:
    if not rel_paths:
        return os.path.isdir(root)
    hits = sum(1 for rel in rel_paths if os.path.isfile(os.path.join(root, rel)))
    return hits >= max(1, (len(rel_paths) + 1) // 2)


def resolve_roots(data: dict[str, Any], paths: dict[int, tuple[int, str | None, str | None]],
                  overrides: Mapping[str, str] | None = None,
                  hints: Mapping[str, Any] | None = None, home: str | None = None,
                  drives: Iterable[str] | None = None) -> dict[int, dict[str, Any]]:
    """``{place_id: {name, root, source, verified, tried}}`` — the folder each place's
    relative paths start from (``root`` None when nothing checks out)."""
    overrides = overrides or {}
    hints = hints or {}
    home = home or os.path.expanduser("~")
    drives = list(_drives() if drives is None else drives)
    by_place: dict[int, list[str]] = {}
    for _fid, (place, rel, _absolute) in paths.items():
        if rel is not None:
            by_place.setdefault(place, []).append(rel)
    result = {}
    for place in data["places"]:
        pid = place.get("file_id")
        if pid is None:
            continue
        name = str(place.get("name") or pid)
        rel_list = by_place.get(pid, [])
        step = max(1, len(rel_list) // _VERIFY_SAMPLES)
        sample = rel_list[::step][:_VERIFY_SAMPLES]
        candidates = _candidates(name, place, data["roots"].get(pid), data, overrides, hints,
                                 home, drives)
        chosen = None
        for path, source in candidates:
            if _verified(path, sample):
                chosen = {"root": path, "source": source, "verified": True}
                break
        if chosen is None:
            configured = [(p, s) for p, s in candidates if s == "config"]
            chosen = ({"root": configured[0][0], "source": "config", "verified": False}
                      if configured else {"root": None, "source": None, "verified": False})
        kind = PLACE_KINDS.get(place.get("folder_kind"), "other")
        if kind == "pack" and name.lower() == "core library":
            kind = "core_library"
        chosen.update(name=name, samples=len(rel_list), tried=len(candidates),
                      kind=kind if kind in SOURCES else "other")
        result[pid] = chosen
    return result


# ----------------------------------------------------------------------------- index

def _kind(tags: list[str], words: str) -> str | None:
    for tag in tags:
        group, _, value = tag.partition("|")
        if group.strip().lower() == "type":
            text = value.lower().replace("-", " ")
            if "one shot" in text or "oneshot" in text:
                return "one_shot"
            if "loop" in text:
                return "loop"
    if re.search(r"\bloops?\b", words):
        return "loop"
    if re.search(r"\bone ?shots?\b|\boneshots?\b", words):
        return "one_shot"
    return None


def _normal(text: str) -> str:
    return _SPLIT_RE.sub(" ", text.lower()).strip()


_PLUGIN_FORMATS = {"vst": "VST2", "vst2": "VST2", "vst3": "VST3", "au": "AU", "auv3": "AU",
                   "clap": "CLAP", "aax": "AAX"}
_PLUGIN_KINDS = {1: "instrument", 2: "audio_effect", 4: "midi_effect"}
_KIND_WORDS = {"instr": "instrument", "audiofx": "audio_effect", "midifx": "midi_effect"}


def _categories(data: dict[str, Any], paths: dict[int, tuple[int, str | None, str | None]]
                ) -> tuple[list[tuple[Any, ...]], list[tuple[int, int]]]:
    """Category rows and (file_id, category_id) memberships. A sample is in a category when
    one of the category's words is the value of one of its tags ("Bass" -> "Sounds|Bass") or
    a word of its file name; the group's label is the tag group its words match most."""
    categories = data.get("categories") or {}
    if not categories:
        return [], []
    by_value: dict[str, set[int]] = {}
    value_groups: dict[str, dict[str, int]] = {}
    for fid in paths:
        for tag in data["tags"].get(fid, []):
            group, _, value = tag.partition("|")
            key = value.strip().lower()
            by_value.setdefault(key, set()).add(fid)
            counts = value_groups.setdefault(key, {})
            counts[group.strip()] = counts.get(group.strip(), 0) + 1
    by_word: dict[str, set[int]] = {}
    for fid in paths:
        name = data["files"][fid][1]
        for word in set(_normal(os.path.splitext(name)[0]).split()):
            by_word.setdefault(word, set()).add(fid)
    labels: dict[Any, str] = {}
    for group_id in {c["group_id"] for c in categories.values()}:
        votes: dict[str, int] = {}
        for category in categories.values():
            if category["group_id"] != group_id:
                continue
            for word in [category["name"]] + category["patterns"]:
                for group, count in value_groups.get(word.lower(), {}).items():
                    votes[group] = votes.get(group, 0) + count
        labels[group_id] = max(votes, key=votes.get) if votes else f"Group {group_id}"
    rows, members = [], []
    for vid, category in sorted(categories.items()):
        words = {w.lower() for w in [category["name"]] + category["patterns"]}
        found: set[int] = set()
        for word in words:
            found |= by_value.get(word, set())
            if " " not in word:
                found |= by_word.get(word, set())
        rows.append((vid, labels[category["group_id"]], category["name"],
                     category["name"].lower(), json.dumps(sorted(category["patterns"])),
                     len(found)))
        members.extend((fid, vid) for fid in found)
    return rows, members


def _plugins(data: dict[str, Any]) -> list[tuple[Any, ...]]:
    """Installed plug-ins: entries of the "Plugins" place (folder_kind 10) with a device id
    ``device:<format>:<kind>:<id>[?n=<name>]``; the vendor is the folder Live lists them in."""
    plugin_places = {p["file_id"] for p in data["places"] if p.get("folder_kind") == 10}
    files = data["files"]
    rows = []
    for fid, parent, name, place, device_id, device_type in data.get("plugins") or []:
        if place not in plugin_places or not isinstance(device_id, str):
            continue
        parts = device_id.split(":")
        fmt = _PLUGIN_FORMATS.get(parts[1].lower(), parts[1].upper()) if len(parts) > 1 \
            else None
        kind = _PLUGIN_KINDS.get(device_type) or (
            _KIND_WORDS.get(parts[2].lower()) if len(parts) > 2 else None)
        label = name if isinstance(name, str) and name else device_id.rpartition("n=")[2]
        label = re.sub(r"\.(vst3?|component|dll|clap)$", "", label, flags=re.I)
        vendor = None
        if parent in files and parent not in plugin_places:
            vendor = files[parent][1]
        rows.append((fid, label, label.lower(), vendor, fmt, kind, device_id))
    return rows


def build_index(index_path: Path, database: Path, overrides: Mapping[str, str] | None = None,
                hints: Mapping[str, Any] | None = None, home: str | None = None,
                drives: Iterable[str] | None = None) -> dict[str, Any]:
    """Read Live's database (through a copy) and write the local index. Returns the status."""
    with _BUILD_LOCK:
        started = time.time()
        signature = source_signature(database)
        data = read_live_database(database)
        paths = _paths(data, os.sep)
        roots = resolve_roots(data, paths, overrides, hints, home, drives)
        index_path.parent.mkdir(parents=True, exist_ok=True)
        temp = index_path.with_name(index_path.name + ".building")
        try:
            temp.unlink()
        except FileNotFoundError:
            pass
        con = sqlite3.connect(str(temp))
        try:
            con.executescript("""
                CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE places (place_id INTEGER PRIMARY KEY, name TEXT, name_lc TEXT,
                    root TEXT, source TEXT, verified INTEGER, samples INTEGER, kind TEXT);
                CREATE TABLE samples (file_id INTEGER PRIMARY KEY, name TEXT, name_lc TEXT,
                    place_id INTEGER, rel_path TEXT, path TEXT, search_lc TEXT, ext TEXT,
                    size INTEGER, mod_date INTEGER, kind TEXT, use_count INTEGER,
                    source TEXT, audio_hash INTEGER);
                CREATE TABLE vectors (file_id INTEGER PRIMARY KEY, data BLOB);
                CREATE TABLE categories (category_id INTEGER PRIMARY KEY, grp TEXT,
                    name TEXT, name_lc TEXT, patterns TEXT, files INTEGER);
                CREATE TABLE sample_categories (file_id INTEGER, category_id INTEGER);
                CREATE TABLE plugins (file_id INTEGER PRIMARY KEY, name TEXT, name_lc TEXT,
                    vendor TEXT, format TEXT, kind TEXT, device_id TEXT);
                CREATE TABLE tags (file_id INTEGER, tag TEXT, tag_lc TEXT, group_lc TEXT,
                    value_lc TEXT);
            """)
            extra = data["extra_columns"]
            size_at = 3 + extra.index("file_size") if "file_size" in extra else None
            date_at = 3 + extra.index("mod_date") if "mod_date" in extra else None
            use_at = 3 + extra.index("use_count") if "use_count" in extra else None
            places = {pid: info["name"] for pid, info in roots.items()}
            sample_rows, tag_rows, vector_rows, tagged = [], [], [], 0
            for fid, (place, rel_path, from_db) in paths.items():
                row = data["files"][fid]
                name = row[1]
                tags = sorted(set(data["tags"].get(fid, [])))
                if tags:
                    tagged += 1
                info = roots.get(place, {})
                root = info.get("root")
                if root and rel_path is not None:
                    path = os.path.join(root, rel_path)
                else:
                    path = from_db
                shown_rel = rel_path if rel_path is not None else name
                place_name = places.get(place, str(place))
                words = _normal(f"{name} {shown_rel} {place_name}")
                vector = data["vectors"].get(fid)
                if vector is not None:
                    vector_rows.append((fid, vector[1]))
                sample_rows.append((
                    fid, name, name.lower(), place, shown_rel, path, words,
                    os.path.splitext(name)[1].lower(),
                    row[size_at] if size_at is not None else None,
                    row[date_at] if date_at is not None else None,
                    _kind(tags, words),
                    row[use_at] if use_at is not None else 0,
                    info.get("kind", "other"),
                    vector[0] if vector is not None else None))
                for tag in tags:
                    group, _, value = tag.partition("|")
                    tag_rows.append((fid, tag, tag.lower(), group.strip().lower() if value
                                     else "", (value or group).strip().lower()))
            con.executemany("INSERT INTO samples VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            sample_rows)
            con.executemany("INSERT INTO tags VALUES (?,?,?,?,?)", tag_rows)
            con.executemany("INSERT INTO vectors VALUES (?,?)", vector_rows)
            category_rows, member_rows = _categories(data, paths)
            con.executemany("INSERT INTO categories VALUES (?,?,?,?,?,?)", category_rows)
            con.executemany("INSERT INTO sample_categories VALUES (?,?)", member_rows)
            plugin_rows = _plugins(data)
            con.executemany("INSERT INTO plugins VALUES (?,?,?,?,?,?,?)", plugin_rows)
            con.executemany("INSERT INTO places VALUES (?,?,?,?,?,?,?,?)", [
                (pid, info["name"], info["name"].lower(), info["root"], info["source"],
                 int(info["verified"]), info["samples"], info["kind"])
                for pid, info in roots.items()])
            con.executescript("""
                CREATE INDEX tags_file ON tags (file_id);
                CREATE INDEX tags_tag ON tags (tag_lc, file_id);
                CREATE INDEX tags_value ON tags (value_lc, file_id);
                CREATE INDEX samples_place ON samples (place_id);
                CREATE INDEX samples_hash ON samples (audio_hash);
                CREATE INDEX sample_categories_cat ON sample_categories (category_id, file_id);
            """)
            meta = {"schema": SCHEMA_VERSION, "source_db": str(database),
                    "signature": signature, "built_at": time.time(),
                    "build_seconds": round(time.time() - started, 1),
                    "samples": len(sample_rows), "tagged": tagged,
                    "with_features": len(vector_rows), "categories": len(category_rows),
                    "plugins": len(plugin_rows),
                    "feature_scale": _feature_scale([v for _f, v in vector_rows]),
                    "overrides": dict(overrides or {})}
            con.executemany("INSERT INTO meta VALUES (?, ?)",
                            [(k, json.dumps(v)) for k, v in meta.items()])
            con.commit()
        finally:
            con.close()
        os.replace(temp, index_path)
    return index_status(index_path)


# ----------------------------------------------------------------------------- reading the index

def _connect(index_path: Path) -> sqlite3.Connection:
    if not index_path.is_file():
        raise SampleIndexError("the sample index has not been built yet")
    return sqlite3.connect(f"file:{index_path.as_posix()}?mode=ro", uri=True)


def read_meta(index_path: Path) -> dict[str, Any]:
    try:
        con = _connect(index_path)
    except SampleIndexError:
        return {}
    try:
        return {k: json.loads(v) for k, v in con.execute("SELECT key, value FROM meta")}
    except sqlite3.DatabaseError:
        return {}
    finally:
        con.close()


def index_status(index_path: Path, current_signature: dict[str, Any] | None = None
                 ) -> dict[str, Any]:
    meta = read_meta(index_path)
    if not meta:
        return {"built": False, "index": str(index_path)}
    con = _connect(index_path)
    try:
        places = []
        for name, root, source, verified, kind, count, missing in con.execute(
                "SELECT p.name, p.root, p.source, p.verified, p.kind, COUNT(s.file_id), "
                "SUM(s.file_id IS NOT NULL AND s.path IS NULL) FROM places p LEFT JOIN samples s "
                "ON s.place_id = p.place_id GROUP BY p.place_id ORDER BY 6 DESC"):
            entry = {"name": name, "kind": kind, "root": root, "root_from": source,
                     "verified": bool(verified), "samples": count}
            if missing:
                entry["without_path"] = missing
            places.append(entry)
        with_path = con.execute("SELECT COUNT(*) FROM samples WHERE path IS NOT NULL"
                                ).fetchone()[0]
    finally:
        con.close()
    status = {"built": True, "index": str(index_path), "source_db": meta.get("source_db"),
              "built_at": time.strftime("%Y-%m-%d %H:%M:%S",
                                        time.localtime(meta.get("built_at", 0))),
              "build_seconds": meta.get("build_seconds"), "samples": meta.get("samples"),
              "tagged": meta.get("tagged"), "with_path": with_path,
              "with_features": meta.get("with_features"),
              "categories": meta.get("categories"), "plugins": meta.get("plugins"),
              "places": [p for p in places if p["samples"]],
              "unresolved": [p["name"] for p in places if p.get("without_path")]}
    if current_signature is not None:
        status["stale"] = current_signature != meta.get("signature")
    return status


def _like(word: str) -> str:
    return "%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _tag_clause(spec: str) -> tuple[str, str]:
    """SQL condition on table alias t for one tag spec, and its parameter."""
    text = spec.strip().lower()
    if "|" in text:
        group, _, value = text.partition("|")
        if value.strip() in ("", "*"):
            return "t.group_lc = ?", group.strip()
        return "t.tag_lc = ?", f"{group.strip()}|{value.strip()}"
    return "t.value_lc = ?", text


def _filters(con: sqlite3.Connection, query: str | None = None,
             tags: list[str] | None = None, any_tags: list[str] | None = None,
             exclude_tags: list[str] | None = None, kind: str | None = None,
             places: list[str] | None = None, extensions: list[str] | None = None,
             sources: list[str] | None = None, categories: list[str] | None = None
             ) -> tuple[str, list[Any], list[str]]:
    """``(" WHERE ...", params, query words)`` for the samples table (alias s)."""
    where: list[str] = []
    params: list[Any] = []
    words = _normal(query or "").split()
    for word in words:
        where.append("s.search_lc LIKE ? ESCAPE '\\'")
        params.append(_like(word))
    for spec in tags or []:
        clause, value = _tag_clause(spec)
        where.append(f"EXISTS (SELECT 1 FROM tags t WHERE t.file_id = s.file_id AND {clause})")
        params.append(value)
    if any_tags:
        parts = [_tag_clause(spec) for spec in any_tags]
        where.append("EXISTS (SELECT 1 FROM tags t WHERE t.file_id = s.file_id AND ("
                     + " OR ".join(c for c, _ in parts) + "))")
        params.extend(v for _, v in parts)
    for spec in exclude_tags or []:
        clause, value = _tag_clause(spec)
        where.append(
            f"NOT EXISTS (SELECT 1 FROM tags t WHERE t.file_id = s.file_id AND {clause})")
        params.append(value)
    if kind:
        where.append("s.kind = ?")
        params.append(kind)
    if places:
        known: dict[str, list[int]] = {}
        for pid, name_lc in con.execute("SELECT place_id, name_lc FROM places"):
            known.setdefault(name_lc, []).append(pid)
        wanted = [p.strip().lower() for p in places]
        missing = [p for p in wanted if p not in known]
        if missing:
            names = [n for (n,) in con.execute(
                "SELECT name FROM places WHERE samples > 0 ORDER BY samples DESC")]
            raise SampleIndexError(f"unknown place(s) {', '.join(missing)} — known: "
                                   + ", ".join(names[:40]))
        ids = [pid for p in wanted for pid in known[p]]
        where.append(f"s.place_id IN ({','.join('?' * len(ids))})")
        params.extend(ids)
    if sources:
        bad = [x for x in sources if x not in SOURCES]
        if bad:
            raise SampleIndexError(f"unknown source(s) {', '.join(bad)} — use "
                                   + ", ".join(SOURCES))
        where.append(f"s.source IN ({','.join('?' * len(sources))})")
        params.extend(sources)
    if categories:
        ids = []
        for wanted in categories:
            group, _, name = wanted.rpartition("/")
            sql, args = "SELECT category_id FROM categories WHERE name_lc = ?", [
                name.strip().lower()]
            if group:
                sql += " AND lower(grp) = ?"
                args.append(group.strip().lower())
            found = [r[0] for r in con.execute(sql, args)]
            if not found:
                names = [f"{g}/{n}" for g, n in con.execute(
                    "SELECT grp, name FROM categories WHERE files > 0 ORDER BY grp, name")]
                raise SampleIndexError(f"unknown category {wanted!r} — known: "
                                       + ", ".join(names[:80]))
            ids.append(found)
        for found in ids:                      # every named category must match (AND)
            where.append("EXISTS (SELECT 1 FROM sample_categories c WHERE c.file_id = "
                         f"s.file_id AND c.category_id IN ({','.join('?' * len(found))}))")
            params.extend(found)
    if extensions:
        exts = [e.lower() if e.startswith(".") else "." + e.lower() for e in extensions]
        where.append(f"s.ext IN ({','.join('?' * len(exts))})")
        params.extend(exts)
    return ((" WHERE " + " AND ".join(where)) if where else ""), params, words


def _items(con: sqlite3.Connection, ids: list[int], check_exists: bool,
           extra: dict[int, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Result entries for these file ids, in this order."""
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    rows = {r[0]: r for r in con.execute(
        "SELECT s.file_id, s.name, p.name, s.rel_path, s.path, s.kind, s.size, s.use_count, "
        f"s.source FROM samples s LEFT JOIN places p ON p.place_id = s.place_id "
        f"WHERE s.file_id IN ({marks})", ids)}
    tag_map: dict[int, list[str]] = {}
    for fid, tag in con.execute(
            f"SELECT file_id, tag FROM tags WHERE file_id IN ({marks}) ORDER BY tag", ids):
        tag_map.setdefault(fid, []).append(tag)
    category_map: dict[int, list[str]] = {}
    for fid, group, name in con.execute(
            "SELECT m.file_id, c.grp, c.name FROM sample_categories m JOIN categories c "
            f"ON c.category_id = m.category_id WHERE m.file_id IN ({marks}) "
            "ORDER BY c.grp, c.name", ids):
        category_map.setdefault(fid, []).append(f"{group}/{name}")
    items = []
    for fid in ids:
        _id, name, place, rel_path, path, kind, size, use_count, source = rows[fid]
        item: dict[str, Any] = {"name": name, "path": path, "place": place, "source": source,
                                "rel_path": rel_path, "tags": tag_map.get(fid, []),
                                "kind": kind}
        if category_map.get(fid):
            item["categories"] = category_map[fid]
        if size is not None:
            item["size"] = size
        if use_count:
            item["use_count"] = use_count
        if extra and fid in extra:
            item.update(extra[fid])
        if check_exists:
            item["exists"] = bool(path) and os.path.isfile(path)
        items.append(item)
    return items


def _page(total: int, offset: int, items: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {"total": total, "offset": offset, "count": len(items),
                              "samples": items}
    if offset + len(items) < total:
        result["next_offset"] = offset + len(items)
    return result


def search(index_path: Path, query: str | None = None, tags: list[str] | None = None,
           any_tags: list[str] | None = None, exclude_tags: list[str] | None = None,
           kind: str | None = None, places: list[str] | None = None,
           extensions: list[str] | None = None, sources: list[str] | None = None,
           categories: list[str] | None = None, order: str = "relevance", seed: int | None = None, offset: int = 0,
           limit: int = 20, check_exists: bool = True) -> dict[str, Any]:
    """Search the index. Every filter is AND-ed; see the tool docstring for the forms."""
    con = _connect(index_path)
    try:
        clause, params, words = _filters(con, query, tags, any_tags, exclude_tags, kind,
                                         places, extensions, sources, categories)
        total = con.execute(f"SELECT COUNT(*) FROM samples s{clause}", params).fetchone()[0]
        order_params: list[Any] = []
        if order == "name":
            order_sql = "s.name_lc, s.file_id"
        elif order == "newest":
            order_sql = "s.mod_date IS NULL, s.mod_date DESC, s.name_lc"
        elif order == "most_used":
            order_sql = "s.use_count DESC, s.name_lc"
        elif order == "random":
            order_sql = "((s.file_id * 2654435761 + ?) % 4294967291), s.file_id"
            order_params.append(seed if seed is not None else int(time.time()))
        elif words:
            score = " + ".join(["(instr(s.name_lc, ?) > 0)"] * len(words))
            order_sql = f"({score}) DESC, length(s.name), s.name_lc"
            order_params.extend(words)
        else:
            order_sql = "s.name_lc, s.file_id"
        ids = [r[0] for r in con.execute(
            f"SELECT s.file_id FROM samples s{clause} ORDER BY {order_sql} LIMIT ? OFFSET ?",
            params + order_params + [limit, offset])]
        return _page(total, offset, _items(con, ids, check_exists))
    finally:
        con.close()


# ----------------------------------------------------------------------------- similarity

def _floats(blob: bytes) -> tuple[float, ...]:
    return struct.unpack(f"<{len(blob) // 4}f", blob)


def _feature_scale(blobs: list[bytes]) -> list[float] | None:
    """Per-dimension standard deviation over the library (dimensions differ in range; the
    distance divides by it so that no single feature dominates)."""
    if not blobs:
        return None
    dims = len(blobs[0]) // 4
    total = [0.0] * dims
    squares = [0.0] * dims
    count = 0
    for blob in blobs:
        if len(blob) != dims * 4:
            continue
        values = _floats(blob)
        count += 1
        for i, x in enumerate(values):
            if x == x and abs(x) != float("inf"):       # skip NaN / inf
                total[i] += x
                squares[i] += x * x
    if not count:
        return None
    scale = []
    for i in range(dims):
        mean = total[i] / count
        variance = max(squares[i] / count - mean * mean, 0.0)
        scale.append(max(variance ** 0.5, 1e-6))
    return scale


def _distances(rows: list[tuple[int, bytes]], target: bytes, scale: list[float]
               ) -> list[tuple[float, int]]:
    """``(scaled euclidean distance, file_id)`` of every row to the target vector (plain
    Python: well under a second for 50,000 samples)."""
    weights = [1.0 / (x * x) for x in scale]
    seed = _floats(target)
    scored = []
    for fid, blob in rows:
        values = _floats(blob)
        distance = sum(w * (a - b) * (a - b) for w, a, b in zip(weights, values, seed))
        scored.append((distance ** 0.5, fid))
    return scored


def _find_seed(con: sqlite3.Connection, seed: str) -> int:
    text = seed.strip()
    name = _basename(text) if ("/" in text or "\\" in text) else text
    rows = con.execute("SELECT file_id, path FROM samples WHERE name_lc = ?",
                       (name.lower(),)).fetchall()
    if "/" in text or "\\" in text:
        wanted = os.path.normcase(os.path.normpath(text))
        rows = [r for r in rows if r[1] and os.path.normcase(os.path.normpath(r[1])) == wanted]
    if not rows:
        raise SampleIndexError(f"{seed!r} is not in the sample index — pass a path or file "
                               "name from live_sample_search")
    if len(rows) > 1:
        raise SampleIndexError(f"{len(rows)} samples are called {name!r} — pass the full path: "
                               + "; ".join(str(r[1]) for r in rows[:5]))
    return rows[0][0]


def similar(index_path: Path, seed: str, query: str | None = None,
            tags: list[str] | None = None, any_tags: list[str] | None = None,
            exclude_tags: list[str] | None = None, kind: str | None = None,
            places: list[str] | None = None, extensions: list[str] | None = None,
            sources: list[str] | None = None, categories: list[str] | None = None,
            offset: int = 0, limit: int = 20, include_copies: bool = False, check_exists: bool = True) -> dict[str, Any]:
    """Samples whose audio features are closest to the seed's (Live's own features from its
    browser database), within the usual filters; ``distance`` 0 = the same sound."""
    meta = read_meta(index_path)
    scale = meta.get("feature_scale")
    con = _connect(index_path)
    try:
        seed_id = _find_seed(con, seed)
        row = con.execute("SELECT data FROM vectors WHERE file_id = ?", (seed_id,)).fetchone()
        if row is None or not scale:
            raise SampleIndexError("Live has no audio features for that sample yet (it "
                                   "analyses files in the background) — try another one")
        seed_hash = con.execute("SELECT audio_hash FROM samples WHERE file_id = ?",
                                (seed_id,)).fetchone()[0]
        target = _floats(row[0])
        clause, params, _words = _filters(con, query, tags, any_tags, exclude_tags, kind,
                                          places, extensions, sources, categories)
        join = " AND " if clause else " WHERE "
        candidates = con.execute(
            f"SELECT s.file_id, v.data, s.audio_hash FROM samples s "
            f"JOIN vectors v ON v.file_id = s.file_id{clause}{join}s.file_id != ?",
            params + [seed_id]).fetchall()
        kept = [(fid, blob) for fid, blob, audio_hash in candidates
                if len(blob) == len(row[0]) and (include_copies or seed_hash is None
                                                 or audio_hash != seed_hash)]
        scored = _distances(kept, row[0], scale)
        scored.sort()
        page = scored[offset:offset + limit]
        extra = {fid: {"distance": round(d, 3)} for d, fid in page}
        result = _page(len(scored), offset,
                       _items(con, [fid for _d, fid in page], check_exists, extra))
        result["seed"] = _items(con, [seed_id], check_exists)[0]
        return result
    finally:
        con.close()


def duplicates(index_path: Path, query: str | None = None, places: list[str] | None = None,
               sources: list[str] | None = None, offset: int = 0, limit: int = 20,
               check_exists: bool = True) -> dict[str, Any]:
    """Groups of samples with identical audio (same audio hash and features — e.g. a sound
    that several packs ship), largest groups first; filters pick which samples count."""
    con = _connect(index_path)
    try:
        clause, params, _words = _filters(con, query, places=places, sources=sources)
        join = " AND " if clause else " WHERE "
        rows = con.execute(
            f"SELECT s.file_id, s.audio_hash, v.data FROM samples s "
            f"JOIN vectors v ON v.file_id = s.file_id{clause}{join}s.audio_hash IN ("
            "SELECT audio_hash FROM samples WHERE audio_hash IS NOT NULL "
            "GROUP BY audio_hash HAVING COUNT(*) > 1)", params).fetchall()
        groups: dict[tuple[int, bytes], list[int]] = {}
        for fid, audio_hash, blob in rows:
            groups.setdefault((audio_hash, blob), []).append(fid)
        found = sorted((ids for ids in groups.values() if len(ids) > 1),
                       key=lambda ids: (-len(ids), ids[0]))
        page = found[offset:offset + limit]
        result: dict[str, Any] = {
            "total_groups": len(found), "offset": offset, "count": len(page),
            "duplicate_files": sum(len(ids) - 1 for ids in found),
            "groups": [{"copies": len(ids), "samples": _items(con, sorted(ids), check_exists)}
                       for ids in page]}
        if offset + len(page) < len(found):
            result["next_offset"] = offset + len(page)
        return result
    finally:
        con.close()


def tag_counts(index_path: Path, group: str | None = None, filter: str | None = None,
               limit: int = 100) -> dict[str, Any]:
    """Tag groups with counts (and their most used tags), or the tags of one group."""
    con = _connect(index_path)
    try:
        if group is None and not filter:
            groups = con.execute(
                "SELECT group_lc, COUNT(DISTINCT file_id) FROM tags GROUP BY group_lc "
                "ORDER BY 2 DESC").fetchall()
            result = []
            for name, count in groups[:limit]:
                top = con.execute(
                    "SELECT tag, COUNT(*) FROM tags WHERE group_lc = ? GROUP BY tag_lc "
                    "ORDER BY 2 DESC LIMIT 8", (name,)).fetchall()
                label = top[0][0].partition("|")[0] if top and "|" in top[0][0] else name
                result.append({"group": label, "files": count,
                               "top": [{"tag": t, "files": c} for t, c in top]})
            return {"groups": result}
        where, params = [], []
        if group is not None:
            where.append("group_lc = ?")
            params.append(group.strip().lower())
        if filter:
            where.append("tag_lc LIKE ? ESCAPE '\\'")
            params.append(_like(filter.strip().lower()))
        rows = con.execute(
            "SELECT tag, COUNT(*) FROM tags WHERE " + " AND ".join(where)
            + " GROUP BY tag_lc ORDER BY 2 DESC LIMIT ?", params + [limit]).fetchall()
        return {"tags": [{"tag": t, "files": c} for t, c in rows]}
    finally:
        con.close()


def category_counts(index_path: Path, group: str | None = None,
                    filter: str | None = None) -> dict[str, Any]:
    """Live's browser categories with how many samples each holds, grouped."""
    con = _connect(index_path)
    try:
        sql, args = "SELECT grp, name, files, patterns FROM categories WHERE files > 0", []
        if group:
            sql += " AND lower(grp) = ?"
            args.append(group.strip().lower())
        if filter:
            sql += " AND (name_lc LIKE ? ESCAPE '\\' OR patterns LIKE ? ESCAPE '\\')"
            args += [_like(filter.strip().lower()), _like(filter.strip())]
        groups: dict[str, list[dict[str, Any]]] = {}
        for grp, name, files, patterns in con.execute(sql + " ORDER BY grp, files DESC, name_lc",
                                                      args):
            groups.setdefault(grp, []).append(
                {"category": f"{grp}/{name}", "files": files, "words": json.loads(patterns)})
        return {"categories": [{"group": g, "categories": c} for g, c in groups.items()]}
    finally:
        con.close()


def plugin_search(index_path: Path, query: str | None = None, format: str | None = None,
                  kind: str | None = None, vendor: str | None = None, offset: int = 0,
                  limit: int = 50) -> dict[str, Any]:
    """Installed plug-ins Live knows (its Plugins browser), filtered."""
    con = _connect(index_path)
    try:
        where, params = [], []
        for word in _normal(query or "").split():
            where.append("(name_lc LIKE ? ESCAPE '\\' OR lower(coalesce(vendor, '')) "
                         "LIKE ? ESCAPE '\\')")
            params += [_like(word), _like(word)]
        if format:
            where.append("lower(format) = ?")
            params.append(format.strip().lower())
        if kind:
            where.append("kind = ?")
            params.append(kind)
        if vendor:
            where.append("lower(coalesce(vendor, '')) LIKE ? ESCAPE '\\'")
            params.append(_like(vendor.strip().lower()))
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        total = con.execute(f"SELECT COUNT(*) FROM plugins{clause}", params).fetchone()[0]
        rows = con.execute(
            f"SELECT name, vendor, format, kind, device_id FROM plugins{clause} "
            "ORDER BY name_lc, format LIMIT ? OFFSET ?", params + [limit, offset]).fetchall()
        formats = dict(con.execute("SELECT format, COUNT(*) FROM plugins GROUP BY format"))
    finally:
        con.close()
    plugins = [{"name": n, "vendor": v, "format": f, "kind": k, "device_id": d}
               for n, v, f, k, d in rows]
    result: dict[str, Any] = {"total": total, "offset": offset, "count": len(plugins),
                              "plugins": plugins, "formats": formats}
    if offset + len(plugins) < total:
        result["next_offset"] = offset + len(plugins)
    return result
