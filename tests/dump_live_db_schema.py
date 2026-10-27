"""Dump the layout of Live's browser database (Live-files-<N>.db) to a JSON file.

Used to build sample-search features (use counts, similar sounds, keywords, place kinds)
against the real tables. Reads a temporary COPY of the database and its -wal file; Live's own
files are never opened by SQLite, and the copy is deleted afterwards. Standard library only.

    python tests/dump_live_db_schema.py                  # newest Live-files-<N>.db
    python tests/dump_live_db_schema.py --db "C:\\path\\Live-files-12300.db"
    python tests/dump_live_db_schema.py --out schema.json --rows 3

The JSON holds table definitions, row counts, column statistics and a few example rows per
table (long text shortened, binary data shown as size + first bytes). Example rows contain
file and folder names from your library: look the file over before sharing it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

AUDIO = (".wav", ".aif", ".aiff", ".mp3", ".flac", ".ogg", ".m4a")


def newest_db() -> Path:
    if sys.platform.startswith("win"):
        folder = Path(os.environ.get("LOCALAPPDATA", "")) / "Ableton" / "Live Database"
    else:
        folder = Path.home() / "Library" / "Application Support" / "Ableton" / "Live Database"
    found = []
    for path in folder.glob("Live-files-*.db"):
        match = re.match(r"^Live-files-(\d+)\.db$", path.name)
        if match:
            found.append((int(match.group(1)), path))
    if not found:
        raise SystemExit(f"No Live-files-*.db in {folder} (pass --db)")
    return max(found)[1]


def show(value, width=100):
    """A JSON-safe, shortened view of one value."""
    if isinstance(value, bytes):
        return {"blob_bytes": len(value), "head_hex": value[:24].hex()}
    if isinstance(value, str) and len(value) > width:
        return value[:width] + f"... (+{len(value) - width} chars)"
    return value


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def table_report(con, name, sql, rows, audio_ids):
    report = {"sql": sql}
    try:
        info = con.execute(f"PRAGMA table_info({quote(name)})").fetchall()
        report["columns"] = [{"name": c[1], "type": c[2], "pk": bool(c[5])} for c in info]
        report["indexes"] = [r[1] for r in con.execute(f"PRAGMA index_list({quote(name)})")]
        report["rows"] = con.execute(f"SELECT COUNT(*) FROM {quote(name)}").fetchone()[0]
    except sqlite3.Error as exc:            # e.g. FTS tables with Live's own tokenizer
        report["error"] = str(exc)
        return report
    columns = [c["name"] for c in report["columns"]]
    stats = {}
    for column in columns:
        q = quote(column)
        try:
            row = con.execute(
                f"SELECT COUNT({q}), COUNT(DISTINCT {q}), MIN({q}), MAX({q}), "
                f"MIN(length({q})), MAX(length({q})), "
                f"SUM(typeof({q}) = 'blob'), SUM(typeof({q}) = 'text'), "
                f"SUM(typeof({q}) = 'integer'), SUM(typeof({q}) = 'real') "
                f"FROM {quote(name)}").fetchone()
        except sqlite3.Error as exc:
            stats[column] = {"error": str(exc)}
            continue
        entry = {"non_null": row[0], "distinct": row[1],
                 "types": {k: v for k, v in zip(("blob", "text", "integer", "real"), row[6:])
                           if v}}
        if entry["types"].get("blob") or entry["types"].get("text"):
            entry["length"] = [row[4], row[5]]
        if not entry["types"].get("blob"):
            entry["min"], entry["max"] = show(row[2], 60), show(row[3], 60)
        if 0 < row[1] <= 12 and not entry["types"].get("blob"):
            entry["values"] = [
                [show(v, 60), n] for v, n in con.execute(
                    f"SELECT {q}, COUNT(*) FROM {quote(name)} GROUP BY {q} ORDER BY 2 DESC")]
        stats[column] = entry
    report["column_stats"] = stats
    report["examples"] = [[show(v) for v in r] for r in con.execute(
        f"SELECT * FROM {quote(name)} LIMIT ?", (rows,))]
    if "file_id" in columns and name != "files" and audio_ids is not None:
        linked = {r[0] for r in con.execute(f"SELECT DISTINCT file_id FROM {quote(name)}")}
        report["file_ids"] = {"distinct": len(linked), "audio_files": len(linked & audio_ids)}
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--db", help="Live-files-<N>.db (default: the newest)")
    parser.add_argument("--out", default="live_db_schema.json")
    parser.add_argument("--rows", type=int, default=3, help="example rows per table")
    args = parser.parse_args()
    source = Path(args.db) if args.db else newest_db()
    started = time.time()
    folder = tempfile.mkdtemp(prefix="LiveDBSchema_")
    try:
        copy = Path(folder) / source.name
        shutil.copy2(source, copy)
        wal = Path(str(source) + "-wal")
        if wal.exists():
            shutil.copy2(wal, Path(str(copy) + "-wal"))
        con = sqlite3.connect(str(copy))
        try:
            objects = con.execute("SELECT type, name, tbl_name, sql FROM sqlite_master "
                                  "ORDER BY type, name").fetchall()
            audio_ids = None
            try:
                audio_ids = {fid for fid, name in con.execute("SELECT file_id, name FROM files")
                             if isinstance(name, str) and name.lower().endswith(AUDIO)}
            except sqlite3.Error:
                pass
            result = {
                "source": str(source),
                "source_bytes": source.stat().st_size,
                "wal_bytes": wal.stat().st_size if wal.exists() else None,
                "sqlite_version": sqlite3.sqlite_version,
                "audio_files": len(audio_ids) if audio_ids is not None else None,
                "objects": [{"type": t, "name": n, "table": tb} for t, n, tb, _ in objects],
                "tables": {},
            }
            for kind, name, _table, sql in objects:
                if kind == "table":
                    print(f"  {name} ...", flush=True)
                    result["tables"][name] = table_report(con, name, sql, args.rows, audio_ids)
            for kind, name, _table, sql in objects:
                if kind in ("view", "trigger") and sql:
                    result.setdefault(kind + "s", {})[name] = sql
            try:                              # every place, with its number of files
                cols = [c[1] for c in con.execute("PRAGMA table_info(places)")]
                result["places_full"] = {"columns": cols, "rows": [
                    [show(v) for v in row] + [count] for *row, count in con.execute(
                        "SELECT p.*, (SELECT COUNT(*) FROM files f WHERE f.place_id = p.file_id) "
                        "FROM places p")]}
            except sqlite3.Error as exc:
                result["places_full"] = {"error": str(exc)}
        finally:
            con.close()
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    result["seconds"] = round(time.time() - started, 1)
    Path(args.out).write_text(json.dumps(result, indent=1, ensure_ascii=False, default=str),
                              encoding="utf-8")
    print(f"\nWrote {Path(args.out).resolve()}  ({len(result['tables'])} tables, "
          f"{result['audio_files']} audio files, {result['seconds']} s)")
    print("Look it over before sharing: example rows contain file and folder names.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
