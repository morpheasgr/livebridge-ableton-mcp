"""Sample search over Live's browser database: reading a copy (never the original), paths,
root folders, tags, the index and the three MCP tools — against a small fixture database with
the layout of Live's ``Live-files-<N>.db``."""

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp_server"))

from livebridge_mcp import sample_db  # noqa: E402
from livebridge_mcp.client import BridgeClient  # noqa: E402
from livebridge_mcp.server import create_app  # noqa: E402

from fake_bridge import FakeBridge  # noqa: E402
from test_consistency import call_tool  # noqa: E402

SCHEMA = """
CREATE TABLE files (file_id INTEGER PRIMARY KEY, parent_id INTEGER, name TEXT,
    place_id INTEGER, file_kind INTEGER, file_size INTEGER, mod_date INTEGER);
CREATE TABLE places (file_id INTEGER, folder_kind INTEGER, level INTEGER, name TEXT);
CREATE TABLE metadata (file_id INTEGER, key INTEGER, value_id INTEGER);
CREATE TABLE metadata_values (id INTEGER PRIMARY KEY, value TEXT);
"""


class LiveSet:
    """A fake Live machine: sample folders on disk and Live's browser database (WAL mode,
    the writer connection kept open like Live keeps it, so the rows live in the -wal file)."""

    def __init__(self, base: Path):
        self.base = base
        self.home = base / "home"
        self.core = base / "Live 12 Suite" / "Resources" / "Core Library"
        self.user_library = self.home / "Documents" / "Ableton" / "User Library"
        self.packs = self.home / "Documents" / "Ableton" / "Factory Packs"
        self.drive = base / "drive_d"
        self.field = base / "field recordings"
        self.archive_top = base / "archive_drive"
        self.db_dir = base / "Live Database"
        self.db_dir.mkdir(parents=True)
        (self.db_dir / "Live-files-11000.db").write_bytes(b"old")
        self.db = self.db_dir / "Live-files-12300.db"
        self.con = sqlite3.connect(str(self.db))
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA wal_autocheckpoint=0")
        self.con.executescript(SCHEMA)
        self.next_id = 1
        self.values: dict[str, int] = {}
        self.places: dict[str, int] = {}

    def row(self, parent, name, place, size=None):
        fid = self.next_id
        self.next_id += 1
        self.con.execute("INSERT INTO files VALUES (?,?,?,?,?,?,?)",
                         (fid, parent, name, place, 1, size, 1700000000 + fid))
        return fid

    def place(self, name, root_name=None, parent=None):
        fid = self.row(parent, root_name or name, None)
        self.con.execute("UPDATE files SET place_id = ? WHERE file_id = ?", (fid, fid))
        self.con.execute("INSERT INTO places VALUES (?,?,?,?)", (fid, 1, 0, name))
        self.places[name] = fid
        return fid

    def sample(self, place, folder_on_disk, rel, tags=(), create=True):
        parts = rel.split("/")
        parent = place
        for part in parts[:-1]:
            existing = self.con.execute(
                "SELECT file_id FROM files WHERE parent_id = ? AND name = ?",
                (parent, part)).fetchone()
            parent = existing[0] if existing else self.row(parent, part, place)
        fid = self.row(parent, parts[-1], place, size=1234)
        for tag in tags:
            if tag not in self.values:
                self.values[tag] = len(self.values) + 1
                self.con.execute("INSERT INTO metadata_values VALUES (?, ?)",
                                 (self.values[tag], tag))
            self.con.execute("INSERT INTO metadata VALUES (?,?,?)", (fid, 1, self.values[tag]))
        if create and folder_on_disk is not None:
            path = Path(folder_on_disk, *parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"RIFF")
        return fid

    def commit(self):
        self.con.commit()

    def hints(self):
        """What the Remote Script's samples.locations answers on this machine."""
        return {"core_library": {"path": str(self.core), "exists": True},
                "user_library": [{"path": str(self.base / "nowhere"), "exists": False},
                                 {"path": str(self.user_library), "exists": True}],
                "factory_packs": {"path": str(self.packs), "exists": True},
                "music": {"path": str(self.home / "Music"), "exists": True},
                "downloads": {"path": str(self.home / "Downloads"), "exists": True}}


@pytest.fixture
def live(tmp_path):
    s = LiveSet(tmp_path)
    core = s.place("Core Library")
    s.sample(core, s.core, "Samples/One Shots/Drums/Kick/Kick 909 Dry.wav",
             ("Drums|Kick", "Type|One Shot", "Character|Dry"))
    s.sample(core, s.core, "Samples/Loops/Drums/Break 170 Amen.aif",
             ("Drums|Break", "Type|Loop", "Genres|Drum & Bass"))
    user = s.place("User Library")
    s.sample(user, s.user_library, "Samples/Imported/Vox Chop.wav", ("Sounds|Vocals",))
    pack = s.place("Drive and Glow")
    s.sample(pack, s.packs / "Drive and Glow", "Samples/Bass/Glow Bass C.wav",
             ("Sounds|Bass", "Type|One Shot"))
    samples = s.place("Samples")                                    # D:\Samples
    s.sample(samples, s.drive / "Samples", "Kicks/Punchy Kick 01.wav",
             ("Drums|Kick", "Character|Punchy", "Type|One Shot"))
    s.sample(samples, s.drive / "Samples", "Kicks/Punchy Kick 02.wav",
             ("Drums|Kick", "Character|Punchy", "Character|Distorted", "Type|One Shot"))
    s.sample(samples, s.drive / "Samples", "Kicks/Soft Kick.wav",
             ("Drums|Kick", "Character|Soft", "Type|One Shot"))
    s.sample(samples, s.drive / "Samples", "Loops/Hat Loop 124.wav",
             ("Drums|Hihat", "Type|Loop"))
    s.sample(samples, s.drive / "Samples", "Kicks/notes.txt")        # not audio
    music = s.place("Music")
    s.sample(music, s.home / "Music", "Rain Field.flac", ())        # untagged
    field = s.place("Field", root_name=str(s.field))                # absolute root in the DB
    s.sample(field, s.field, "Birds/Dawn.wav", ("Sounds|Ambience",))
    top = s.row(None, str(s.archive_top), None)                     # folders above the root
    archive = s.place("Archive", root_name="Old", parent=top)
    s.sample(archive, s.archive_top / "Old", "Moved/Snare Gone.wav", ("Drums|Snare",))
    projects = s.place("Projects")                                  # folder unknown
    s.sample(projects, None, "Song Project/Samples/Recorded/take 1.wav")
    s.commit()
    yield s
    s.con.close()


def build(live, index, **kw):
    kw.setdefault("hints", live.hints())
    kw.setdefault("home", str(live.home))
    kw.setdefault("drives", [str(live.drive)])
    return sample_db.build_index(index, live.db, **kw)


# ----------------------------------------------------------------------------- engine

def test_newest_database_and_override(live, tmp_path):
    assert sample_db.find_live_database(env={"LOCALAPPDATA": str(tmp_path / "x")},
                                        platform="win32") is None
    assert sample_db.find_live_database(str(live.db_dir)) == live.db
    assert sample_db.find_live_database(str(live.db)) == live.db
    assert sample_db.find_live_database(str(tmp_path / "missing.db")) is None
    local = tmp_path / "local"
    folder = local / "Ableton" / "Live Database"
    folder.mkdir(parents=True)
    for n in (900, 12300, 11000):
        (folder / f"Live-files-{n}.db").write_bytes(b"x")
    (folder / "Live-plugins-1.db").write_bytes(b"x")
    assert sample_db.find_live_database(env={"LOCALAPPDATA": str(local)},
                                        platform="win32") == folder / "Live-files-12300.db"


def test_reads_a_copy_including_the_wal_and_never_touches_the_original(live, tmp_path):
    wal = Path(str(live.db) + "-wal")
    assert wal.stat().st_size > 0                       # the rows are only in the WAL
    def fingerprint():
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(live.db_dir.iterdir())}
    copies = set(Path(tempfile.gettempdir()).glob("LiveBridgeDB_*"))
    before = fingerprint()
    status = build(live, tmp_path / "idx" / "sample_index.sqlite")
    assert status["samples"] == 12 and status["tagged"] == 10
    assert fingerprint() == before                      # same files, same bytes
    assert set(Path(tempfile.gettempdir()).glob("LiveBridgeDB_*")) == copies  # copy deleted


def test_roots_resolved_and_verified(live, tmp_path):
    status = build(live, tmp_path / "sample_index.sqlite")
    places = {p["name"]: p for p in status["places"]}
    assert places["Core Library"]["root"] == str(live.core)
    assert places["Core Library"]["source"] == "live"
    assert places["User Library"]["root"] == str(live.user_library)   # first *existing* hint
    assert places["Drive and Glow"]["root"] == str(live.packs / "Drive and Glow")
    assert places["Samples"]["root"] == str(live.drive / "Samples")
    assert places["Samples"]["source"] == "guess"
    assert places["Music"]["root"] == str(live.home / "Music")
    assert places["Field"]["root"] == str(live.field)
    assert places["Field"]["source"] == "database"
    assert places["Archive"]["root"] == str(live.archive_top / "Old")
    assert places["Archive"]["source"] == "database"
    assert all(p["verified"] for n, p in places.items() if n != "Projects")
    assert status["unresolved"] == ["Projects"] and status["with_path"] == 11


def test_config_roots_win_and_wrong_guesses_are_rejected(live, tmp_path):
    decoy = live.home / "Samples"                       # same name, wrong content
    (decoy / "Kicks").mkdir(parents=True)
    projects = tmp_path / "proj"
    (projects / "Song Project" / "Samples" / "Recorded").mkdir(parents=True)
    (projects / "Song Project" / "Samples" / "Recorded" / "take 1.wav").write_bytes(b"x")
    status = build(live, tmp_path / "i.sqlite",
                   overrides={"projects": str(projects), "Music": str(tmp_path / "nope")})
    places = {p["name"]: p for p in status["places"]}
    assert places["Samples"]["root"] == str(live.drive / "Samples")    # decoy skipped
    assert places["Projects"] == dict(places["Projects"], root=str(projects),
                                      source="config", verified=True)
    # a configured folder that fails the check loses to one that passes
    assert places["Music"]["root"] == str(live.home / "Music")
    assert status["unresolved"] == []


def test_without_live_hints_the_user_library_is_still_found(live, tmp_path):
    status = build(live, tmp_path / "i.sqlite", hints={})
    places = {p["name"]: p for p in status["places"]}
    assert places["User Library"]["root"] == str(live.user_library)
    assert places["User Library"]["source"] == "guess"
    assert places["Core Library"]["root"] is None      # only Live knows where it is installed


def test_search_words_tags_kind_place_and_paging(live, tmp_path):
    index = tmp_path / "i.sqlite"
    build(live, index)
    punchy = sample_db.search(index, tags=["Drums|Kick", "character|PUNCHY"], kind="one_shot")
    assert [s["name"] for s in punchy["samples"]] == ["Punchy Kick 01.wav",
                                                      "Punchy Kick 02.wav"]
    first = punchy["samples"][0]
    assert first["path"] == str(live.drive / "Samples" / "Kicks" / "Punchy Kick 01.wav")
    assert first["exists"] and first["place"] == "Samples" and first["kind"] == "one_shot"
    assert first["tags"] == ["Character|Punchy", "Drums|Kick", "Type|One Shot"]

    assert sample_db.search(index, tags=["Punchy"])["total"] == 2           # bare tag
    assert sample_db.search(index, tags=["Drums|*"])["total"] == 7          # whole group
    assert sample_db.search(index, tags=["Drums|Kick"], exclude_tags=["Distorted"])[
        "total"] == 3
    assert sample_db.search(index, any_tags=["Drums|Break", "Drums|Hihat"])["total"] == 2
    assert sample_db.search(index, kind="loop")["total"] == 2
    assert sample_db.search(index, query="kick 909")["samples"][0]["name"] == \
        "Kick 909 Dry.wav"
    assert sample_db.search(index, query="one shots drums")["total"] == 1   # folder words
    assert sample_db.search(index, extensions=["aif"])["total"] == 1
    assert sample_db.search(index, places=["samples", "Music"])["total"] == 5
    assert sample_db.search(index, query="100%_")["total"] == 0              # LIKE escaped
    page = sample_db.search(index, tags=["Drums|Kick"], limit=2)
    assert page["total"] == 4 and page["next_offset"] == 2
    rest = sample_db.search(index, tags=["Drums|Kick"], offset=2, limit=2)
    assert "next_offset" not in rest and {s["name"] for s in page["samples"]}.isdisjoint(
        s["name"] for s in rest["samples"])
    shuffled = sample_db.search(index, order="random", seed=7, limit=50)
    assert shuffled == sample_db.search(index, order="random", seed=7, limit=50)
    assert sample_db.search(index, query="take")["samples"][0]["path"] is None
    with pytest.raises(sample_db.SampleIndexError, match="unknown place"):
        sample_db.search(index, places=["Nowhere"])


def test_search_reports_moved_files(live, tmp_path):
    index = tmp_path / "i.sqlite"
    build(live, index)
    (live.archive_top / "Old" / "Moved" / "Snare Gone.wav").unlink()
    gone = sample_db.search(index, query="snare gone")["samples"][0]
    assert gone["path"] and gone["exists"] is False


def test_tag_counts(live, tmp_path):
    index = tmp_path / "i.sqlite"
    build(live, index)
    groups = {g["group"]: g for g in sample_db.tag_counts(index)["groups"]}
    assert groups["Drums"]["files"] == 7 and groups["Type"]["files"] == 7
    assert groups["Drums"]["top"][0] == {"tag": "Drums|Kick", "files": 4}
    character = sample_db.tag_counts(index, group="character")["tags"]
    assert character[0] == {"tag": "Character|Punchy", "files": 2}
    assert sample_db.tag_counts(index, filter="punch")["tags"] == [
        {"tag": "Character|Punchy", "files": 2}]


def test_unexpected_layout_is_reported(tmp_path):
    db = tmp_path / "Live-files-1.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE files (id INTEGER)")
    con.execute("CREATE TABLE places (file_id INTEGER, name TEXT)")
    con.commit()
    con.close()
    with pytest.raises(sample_db.SampleIndexError, match="unexpected layout"):
        sample_db.build_index(tmp_path / "i.sqlite", db)


# ----------------------------------------------------------------------------- tools

@pytest.fixture
def tools(live, tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"sample_index": {"roots": {
        "Samples": str(live.drive / "Samples")}}}), encoding="utf-8")
    monkeypatch.setenv("LIVEBRIDGE_CONFIG", str(config))
    monkeypatch.setenv("LIVEBRIDGE_LIBRARY", str(tmp_path / "library"))
    monkeypatch.setenv("LIVEBRIDGE_LIVE_DATABASE", str(live.db_dir))
    fake = FakeBridge().start()
    fake.set_result("samples.locations", live.hints())
    client = BridgeClient(host=fake.host, port=fake.port, timeout=5.0)
    try:
        yield create_app(client), config, fake
    finally:
        client.close()
        fake.stop()


def test_search_tool_builds_then_reuses_the_index(tools, live):
    app, _config, fake = tools
    first = call_tool(app, "live_sample_search", {"tags": ["Drums|Kick", "Character|Punchy"],
                                                  "kind": "one_shot", "limit": 1})
    assert first["index"]["refreshed"] and first["index"]["unresolved_places"] == ["Projects"]
    assert first["total"] == 2 and first["next_offset"] == 1
    assert first["samples"][0]["path"].endswith(os.path.join("Kicks", "Punchy Kick 01.wav"))
    asked = [r for r in fake.requests if r["cmd"] == "samples.locations"]
    again = call_tool(app, "live_sample_search", {"query": "glow bass"})
    assert "index" not in again and again["samples"][0]["place"] == "Drive and Glow"
    assert len([r for r in fake.requests if r["cmd"] == "samples.locations"]) == len(asked)


def test_changes_in_lives_database_refresh_the_index(tools, live):
    app, config, _fake = tools
    data = json.loads(config.read_text(encoding="utf-8"))
    data["sample_index"]["auto_refresh_seconds"] = 0
    config.write_text(json.dumps(data), encoding="utf-8")
    call_tool(app, "live_sample_index", {"action": "refresh"})
    live.sample(live.places["Samples"], live.drive / "Samples", "Kicks/Fresh Kick.wav",
                ("Drums|Kick",))
    live.commit()
    stale = call_tool(app, "live_sample_search", {"query": "fresh"})
    assert stale["total"] == 0 and stale["index"] == {"stale": True}       # no auto refresh
    assert call_tool(app, "live_sample_index", {})["stale"] is True
    data["sample_index"]["auto_refresh_seconds"] = 0.001
    config.write_text(json.dumps(data), encoding="utf-8")
    fresh = call_tool(app, "live_sample_search", {"query": "fresh"})
    assert fresh["total"] == 1 and fresh["index"]["refreshed"]
    assert call_tool(app, "live_sample_index", {})["stale"] is False


def test_index_and_tags_tools(tools, live):
    app, _config, fake = tools
    status = call_tool(app, "live_sample_index", {"action": "refresh"})
    assert status["refreshed"] and status["samples"] == 12 and status["unresolved"] == [
        "Projects"]
    assert "sample_index" in status["hint"]
    places = {p["name"]: p["source"] for p in status["places"]}
    assert places["Samples"] == "config" and places["Core Library"] == "live"
    tags = call_tool(app, "live_sample_tags", {"group": "Drums"})
    assert tags["tags"][0] == {"tag": "Drums|Kick", "files": 4}
    fake.set_error("samples.locations", "connection", "Live is not running", once=False)
    offline = call_tool(app, "live_sample_index", {"action": "refresh"})
    assert offline["live_reachable"] is False
    assert "Core Library" in offline["unresolved"]


def test_tool_validation_and_missing_database(tools, tmp_path, monkeypatch):
    app, _config, _fake = tools
    for args in ({"kind": "loops"}, {"order": "best"}, {"limit": 0}, {"tags": [""]}):
        assert call_tool(app, "live_sample_search", args)["type"] == "bad_args", args
    assert call_tool(app, "live_sample_index", {"action": "wipe"})["type"] == "bad_args"
    aif = call_tool(app, "live_sample_search", {"extensions": "aif, flac"})
    assert sorted(x["name"] for x in aif["samples"]) == ["Break 170 Amen.aif", "Rain Field.flac"]
    assert call_tool(app, "live_sample_search", {"place": "Mars"})["type"] == "not_found"
    monkeypatch.setenv("LIVEBRIDGE_LIVE_DATABASE", str(tmp_path / "missing"))
    missing = call_tool(app, "live_sample_search", {"query": "kick"})
    assert missing["type"] == "not_found" and "Live-files" in missing["error"]


def test_a_found_sample_loads_onto_a_drum_pad(live, tcp_bridge, tmp_path, monkeypatch):
    monkeypatch.setenv("LIVEBRIDGE_LIBRARY", str(tmp_path / "library"))
    monkeypatch.setenv("LIVEBRIDGE_LIVE_DATABASE", str(live.db))
    config = tmp_path / "c.json"
    config.write_text(json.dumps({"sample_index": {"roots": {
        "Samples": str(live.drive / "Samples")}}}), encoding="utf-8")
    monkeypatch.setenv("LIVEBRIDGE_CONFIG", str(config))
    client = BridgeClient(host="127.0.0.1", port=tcp_bridge.port, timeout=5.0)
    try:
        app = create_app(client)
        found = call_tool(app, "live_sample_search", {"tags": ["Punchy"], "limit": 1})
        path = found["samples"][0]["path"]
        loaded = call_tool(app, "live_drumrack_set_pad",
                           {"note": "C1", "track": "Drums", "file_path": path})
        assert "error" not in loaded, loaded
    finally:
        client.close()


def test_sample_check_command(tools, live, capsys):
    from livebridge_mcp import sample_check
    assert sample_check.main(["--refresh"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["refreshed"] and status["samples"] == 12
    assert sample_check.main(["--tags", "Drums|Kick", "Punchy", "--kind", "one_shot"]) == 0
    found = json.loads(capsys.readouterr().out)
    assert found["total"] == 2
    assert sample_check.main(["--list-tags", "Character"]) == 0
    assert json.loads(capsys.readouterr().out)["tags"][0]["tag"] == "Character|Punchy"
