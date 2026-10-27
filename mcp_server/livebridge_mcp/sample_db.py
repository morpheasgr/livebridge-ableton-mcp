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
* ``metadata(file_id, key, value_id)`` + ``metadata_values(id, value)`` — the tags.

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
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

__all__ = [
    "AUDIO_EXTENSIONS", "SampleIndexError", "build_index", "database_dirs", "find_live_database",
    "index_status", "read_live_database", "resolve_roots", "search", "source_signature",
    "tag_counts",
]

AUDIO_EXTENSIONS = (".wav", ".aif", ".aiff", ".mp3", ".flac", ".ogg", ".m4a")
SCHEMA_VERSION = 1
_DB_RE = re.compile(r"^Live-files-(\d+)\.db$", re.I)
_ABSOLUTE_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|/)")
_SPLIT_RE = re.compile(r"[\\/_\-.\s]+")
_VERIFY_SAMPLES = 8
_MAX_DEPTH = 64
_BUILD_LOCK = threading.Lock()


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


def _read_copy(copy: Path) -> dict[str, Any]:
    con = sqlite3.connect(str(copy))
    try:
        file_columns = _columns(con, "files")
        if not {"file_id", "parent_id", "name", "place_id"} <= set(file_columns):
            raise SampleIndexError("Live's database has an unexpected layout (table files: "
                              f"{', '.join(file_columns) or 'missing'})")
        extra = [c for c in ("file_size", "mod_date") if c in file_columns]
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
                if isinstance(value, str) and value:
                    tags.setdefault(file_id, []).append(value)
    finally:
        con.close()
    return {"files": files, "extra_columns": extra, "places": places, "roots": roots,
            "tags": tags}


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

def _rel_paths(data: dict[str, Any], sep: str) -> dict[int, tuple[int, str]]:
    """``{file_id: (place_id, path relative to the place root)}`` for every audio file."""
    files = data["files"]
    place_ids = {p["file_id"] for p in data["places"] if p.get("file_id") is not None}
    folders: dict[int, str | None] = {}

    def folder_path(fid: int | None) -> str | None:
        chain = []
        while fid is not None and fid not in place_ids and fid not in folders:
            row = files.get(fid)
            if row is None or len(chain) > _MAX_DEPTH:
                return None
            chain.append(fid)
            fid = row[0]
        if fid is None:
            return None
        base = "" if fid in place_ids else folders[fid]
        for folder in reversed(chain):
            if base is None:
                folders[folder] = None
                continue
            name = files[folder][1] or ""
            base = name if not base else base + sep + name
            folders[folder] = base
        return base

    result = {}
    for fid, row in files.items():
        parent, name, place = row[0], row[1], row[2]
        if not isinstance(name, str) or not name.lower().endswith(AUDIO_EXTENSIONS):
            continue
        base = folder_path(parent)
        if base is None:
            continue
        result[fid] = (place, base + sep + name if base else name)
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


def resolve_roots(data: dict[str, Any], rel: dict[int, tuple[int, str]],
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
    for _fid, (place, path) in rel.items():
        by_place.setdefault(place, []).append(path)
    result = {}
    for place in data["places"]:
        pid = place.get("file_id")
        if pid is None:
            continue
        name = str(place.get("name") or pid)
        paths = by_place.get(pid, [])
        step = max(1, len(paths) // _VERIFY_SAMPLES)
        sample = paths[::step][:_VERIFY_SAMPLES]
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
        chosen.update(name=name, samples=len(paths), tried=len(candidates))
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


def build_index(index_path: Path, database: Path, overrides: Mapping[str, str] | None = None,
                hints: Mapping[str, Any] | None = None, home: str | None = None,
                drives: Iterable[str] | None = None) -> dict[str, Any]:
    """Read Live's database (through a copy) and write the local index. Returns the status."""
    with _BUILD_LOCK:
        started = time.time()
        signature = source_signature(database)
        data = read_live_database(database)
        rel = _rel_paths(data, os.sep)
        roots = resolve_roots(data, rel, overrides, hints, home, drives)
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
                    root TEXT, source TEXT, verified INTEGER, samples INTEGER);
                CREATE TABLE samples (file_id INTEGER PRIMARY KEY, name TEXT, name_lc TEXT,
                    place_id INTEGER, rel_path TEXT, path TEXT, search_lc TEXT, ext TEXT,
                    size INTEGER, mod_date INTEGER, kind TEXT);
                CREATE TABLE tags (file_id INTEGER, tag TEXT, tag_lc TEXT, group_lc TEXT,
                    value_lc TEXT);
            """)
            extra = data["extra_columns"]
            size_at = 3 + extra.index("file_size") if "file_size" in extra else None
            date_at = 3 + extra.index("mod_date") if "mod_date" in extra else None
            places = {pid: info["name"] for pid, info in roots.items()}
            sample_rows, tag_rows, tagged = [], [], 0
            for fid, (place, rel_path) in rel.items():
                row = data["files"][fid]
                name = row[1]
                tags = sorted(set(data["tags"].get(fid, [])))
                if tags:
                    tagged += 1
                root = roots.get(place, {}).get("root")
                place_name = places.get(place, str(place))
                words = _normal(f"{name} {rel_path} {place_name}")
                sample_rows.append((
                    fid, name, name.lower(), place, rel_path,
                    os.path.join(root, rel_path) if root else None, words,
                    os.path.splitext(name)[1].lower(),
                    row[size_at] if size_at is not None else None,
                    row[date_at] if date_at is not None else None,
                    _kind(tags, words)))
                for tag in tags:
                    group, _, value = tag.partition("|")
                    tag_rows.append((fid, tag, tag.lower(), group.strip().lower() if value
                                     else "", (value or group).strip().lower()))
            con.executemany("INSERT INTO samples VALUES (?,?,?,?,?,?,?,?,?,?,?)", sample_rows)
            con.executemany("INSERT INTO tags VALUES (?,?,?,?,?)", tag_rows)
            con.executemany("INSERT INTO places VALUES (?,?,?,?,?,?,?)", [
                (pid, info["name"], info["name"].lower(), info["root"], info["source"],
                 int(info["verified"]), info["samples"]) for pid, info in roots.items()])
            con.executescript("""
                CREATE INDEX tags_file ON tags (file_id);
                CREATE INDEX tags_tag ON tags (tag_lc, file_id);
                CREATE INDEX tags_value ON tags (value_lc, file_id);
                CREATE INDEX samples_place ON samples (place_id);
            """)
            meta = {"schema": SCHEMA_VERSION, "source_db": str(database),
                    "signature": signature, "built_at": time.time(),
                    "build_seconds": round(time.time() - started, 1),
                    "samples": len(sample_rows), "tagged": tagged,
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
        places = [{"name": n, "root": r, "source": s, "verified": bool(v), "samples": c}
                  for n, r, s, v, c in con.execute(
                      "SELECT name, root, source, verified, samples FROM places "
                      "ORDER BY samples DESC")]
        with_path = con.execute("SELECT COUNT(*) FROM samples WHERE path IS NOT NULL"
                                ).fetchone()[0]
    finally:
        con.close()
    status = {"built": True, "index": str(index_path), "source_db": meta.get("source_db"),
              "built_at": time.strftime("%Y-%m-%d %H:%M:%S",
                                        time.localtime(meta.get("built_at", 0))),
              "build_seconds": meta.get("build_seconds"), "samples": meta.get("samples"),
              "tagged": meta.get("tagged"), "with_path": with_path,
              "places": [p for p in places if p["samples"]],
              "unresolved": [p["name"] for p in places if p["samples"] and not p["root"]]}
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


def search(index_path: Path, query: str | None = None, tags: list[str] | None = None,
           any_tags: list[str] | None = None, exclude_tags: list[str] | None = None,
           kind: str | None = None, places: list[str] | None = None,
           extensions: list[str] | None = None, order: str = "relevance",
           seed: int | None = None, offset: int = 0, limit: int = 20,
           check_exists: bool = True) -> dict[str, Any]:
    """Search the index. Every filter is AND-ed; see the tool docstring for the forms."""
    con = _connect(index_path)
    try:
        where, params = [], []
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
        if extensions:
            exts = [e.lower() if e.startswith(".") else "." + e.lower() for e in extensions]
            where.append(f"s.ext IN ({','.join('?' * len(exts))})")
            params.extend(exts)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        total = con.execute(f"SELECT COUNT(*) FROM samples s{clause}", params).fetchone()[0]
        order_params: list[Any] = []
        if order == "name":
            order_sql = "s.name_lc, s.file_id"
        elif order == "newest":
            order_sql = "s.mod_date IS NULL, s.mod_date DESC, s.name_lc"
        elif order == "random":
            order_sql = "((s.file_id * 2654435761 + ?) % 4294967291), s.file_id"
            order_params.append(seed if seed is not None else int(time.time()))
        elif words:
            score = " + ".join(["(instr(s.name_lc, ?) > 0)"] * len(words))
            order_sql = f"({score}) DESC, length(s.name), s.name_lc"
            order_params.extend(words)
        else:
            order_sql = "s.name_lc, s.file_id"
        rows = con.execute(
            "SELECT s.file_id, s.name, p.name, s.rel_path, s.path, s.kind, s.ext, s.size "
            f"FROM samples s LEFT JOIN places p ON p.place_id = s.place_id{clause} "
            f"ORDER BY {order_sql} LIMIT ? OFFSET ?",
            params + order_params + [limit, offset]).fetchall()
        tag_map: dict[int, list[str]] = {}
        ids = [r[0] for r in rows]
        if ids:
            marks = ",".join("?" * len(ids))
            for fid, tag in con.execute(
                    f"SELECT file_id, tag FROM tags WHERE file_id IN ({marks}) ORDER BY tag",
                    ids):
                tag_map.setdefault(fid, []).append(tag)
    finally:
        con.close()
    samples = []
    for fid, name, place, rel_path, path, sample_kind, _ext, size in rows:
        item: dict[str, Any] = {"name": name, "path": path, "place": place,
                                "rel_path": rel_path, "tags": tag_map.get(fid, []),
                                "kind": sample_kind}
        if size is not None:
            item["size"] = size
        if check_exists:
            item["exists"] = bool(path) and os.path.isfile(path)
        samples.append(item)
    result: dict[str, Any] = {"total": total, "offset": offset, "count": len(samples),
                              "samples": samples}
    if offset + len(samples) < total:
        result["next_offset"] = offset + len(samples)
    return result


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
