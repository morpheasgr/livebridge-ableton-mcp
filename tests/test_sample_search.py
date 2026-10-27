"""Sample search over Live's browser database: reading a copy (never the original), paths,
root folders, tags, use counts, similarity, duplicates, the index and the MCP tools — against a
small fixture database with the layout of Live 12.4's ``Live-files-12300.db``."""

import hashlib
import json
import os
import sqlite3
import struct
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

# The columns sample search reads, as in Live 12.4's database (other columns omitted).
SCHEMA = """
CREATE TABLE files (file_id INTEGER PRIMARY KEY AUTOINCREMENT, parent_id INTEGER,
    file_kind INTEGER DEFAULT 0, mod_date INTEGER, file_size INTEGER, name TEXT,
    use_count INTEGER DEFAULT 0, place_id INTEGER NOT NULL DEFAULT 0);
CREATE TABLE places (file_id INTEGER, folder_kind INTEGER, level INTEGER NOT NULL DEFAULT 0,
    name TEXT, UNIQUE (file_id) ON CONFLICT IGNORE);
CREATE TABLE metadata (file_id INTEGER, key INTEGER, value_id INTEGER);
CREATE TABLE metadata_values (id INTEGER PRIMARY KEY AUTOINCREMENT, value TEXT);
CREATE TABLE fe_values (file_id INTEGER, data BLOB, hash INTEGER);
"""
TAG_KEY, PACK_KEY = 0x54616773, 0x43426b79           # four-letter codes, like Live's keys


def features(*values):
    """An fe_values blob in Live's layout: version, dimensions, reserved, float32 values."""
    return struct.pack("<3I", 18, len(values), 0) + struct.pack(f"<{len(values)}f", *values)


class LiveSet:
    """A fake Live machine: sample folders on disk and Live's browser database (WAL mode,
    the writer connection kept open like Live keeps it, so the rows live in the -wal file)."""

    def __init__(self, base: Path):
        self.base = base
        self.home = base / "home"
        self.user_library = self.home / "Documents" / "Ableton" / "User Library"
        self.packs = self.home / "Documents" / "Ableton" / "Factory Packs"
        self.drive = base / "drive_d"
        self.field = base / "field recordings"
        self.db_dir = base / "Live Database"
        self.db_dir.mkdir(parents=True)
        (self.db_dir / "Live-files-11000.db").write_bytes(b"old")
        self.db = self.db_dir / "Live-files-12300.db"
        self.con = sqlite3.connect(str(self.db))
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA wal_autocheckpoint=0")
        self.con.executescript(SCHEMA)
        self.values: dict[str, int] = {}
        self.places: dict[str, int] = {}
        # the drive root, like "C:\\" in Live's database: an absolute name, parent 0
        self.top = self.row(0, str(base), 0)

    def row(self, parent, name, place, size=None, use=0):
        cursor = self.con.execute(
            "INSERT INTO files (parent_id, name, place_id, file_size, mod_date, use_count) "
            "VALUES (?,?,?,?,?,?)", (parent, name, place, size, 1700000000, use))
        return cursor.lastrowid

    def folder(self, parent, names, place=0):
        for name in names:
            existing = self.con.execute(
                "SELECT file_id FROM files WHERE parent_id = ? AND name = ?",
                (parent, name)).fetchone()
            parent = existing[0] if existing else self.row(parent, name, place)
        return parent

    def place(self, name, kind, under=None, root_name=None):
        """A place whose root folder sits at ``base/<under...>`` (folders kept up to the
        drive, as Live does), or a detached root when ``under`` is None."""
        parent = self.folder(self.top, under) if under is not None else None
        fid = self.row(parent, root_name or name, 0)
        self.con.execute("UPDATE files SET place_id = ? WHERE file_id = ?", (fid, fid))
        self.con.execute("INSERT INTO places VALUES (?,?,?,?)", (fid, kind, 0, name))
        self.places[name] = fid
        return fid

    def value(self, text):
        if text not in self.values:
            self.values[text] = self.con.execute(
                "INSERT INTO metadata_values (value) VALUES (?)", (text,)).lastrowid
        return self.values[text]

    def sample(self, place, folder_on_disk, rel, tags=(), use=0, vector=None, audio=None,
               create=True, parent=None):
        parts = rel.split("/")
        parent = self.folder(parent or place, parts[:-1], place)
        fid = self.row(parent, parts[-1], place, size=1234, use=use)
        for tag in tags:
            key = TAG_KEY if "|" in tag else PACK_KEY
            self.con.execute("INSERT INTO metadata VALUES (?,?,?)", (fid, key, self.value(tag)))
        if vector is not None:
            self.con.execute("INSERT INTO fe_values VALUES (?,?,?)",
                             (fid, features(*vector), audio if audio is not None else fid))
        if create and folder_on_disk is not None:
            path = Path(folder_on_disk, *parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"RIFF")
        return fid

    def commit(self):
        self.con.commit()

    def hints(self):
        """What the Remote Script's samples.locations answers on this machine."""
        return {"core_library": {"path": str(self.base / "Live 12 Suite" / "Resources"
                                             / "Core Library"), "exists": True},
                "user_library": [{"path": str(self.base / "nowhere"), "exists": False},
                                 {"path": str(self.user_library), "exists": True}],
                "factory_packs": {"path": str(self.packs), "exists": True},
                "music": {"path": str(self.home / "Music"), "exists": True},
                "downloads": {"path": str(self.home / "Downloads"), "exists": True}}


CORE = ("Live 12 Suite", "Resources")


@pytest.fixture
def live(tmp_path):
    s = LiveSet(tmp_path)
    core_dir = tmp_path.joinpath(*CORE, "Core Library")
    core = s.place("Core Library", 0, under=CORE)
    s.sample(core, core_dir, "Samples/One Shots/Drums/Kick/Kick 909 Dry.wav",
             ("Drums|Kick", "Type|One Shot", "Character|Dry", "Core Library",
              "www.ableton.com/0"), use=12, vector=(1.0, 0.0, 0.0, 0.0), audio=777)
    s.sample(core, core_dir, "Samples/Loops/Drums/Break 170 Amen.aif",
             ("Drums|Break", "Type|Loop", "Genres|Drum & Bass"), vector=(0, 0, 5, 5))
    user = s.place("User Library", 1)                                  # detached root
    s.sample(user, s.user_library, "Samples/Imported/Vox Chop.wav", ("Sounds|Vocals",))
    pack = s.place("Drive and Glow", 0)                                # detached root
    s.sample(pack, s.packs / "Drive and Glow", "Samples/Bass/Glow Bass C.wav",
             ("Sounds|Bass", "Type|One Shot"))
    samples = s.place("Samples", 2, under=("drive_d",))                # D:\Samples
    sd = s.drive / "Samples"
    s.sample(samples, sd, "Kicks/Punchy Kick 01.wav",
             ("Drums|Kick", "Character|Punchy", "Type|One Shot"), use=40,
             vector=(1.1, 0.1, 0.0, 0.0))
    s.sample(samples, sd, "Kicks/Punchy Kick 02.wav",
             ("Drums|Kick", "Character|Punchy", "Character|Distorted", "Type|One Shot"),
             use=3, vector=(2.0, 1.0, 0.0, 0.0))
    s.sample(samples, sd, "Kicks/Soft Kick.wav",
             ("Drums|Kick", "Character|Soft", "Type|One Shot"), vector=(0.0, 3.0, 0.0, 0.0))
    s.sample(samples, sd, "Kicks/Kick 909 copy.wav", ("Drums|Kick",),
             vector=(1.0, 0.0, 0.0, 0.0), audio=777)                   # same audio as Core
    s.sample(samples, sd, "Loops/Hat Loop 124.wav", ("Drums|Hihat", "Type|Loop"))
    s.sample(samples, sd, "Kicks/notes.txt")                           # not audio
    # a folder inside Samples that Live also lists as its own place: files of the outer
    # place under it must keep their full path
    nested_root = s.folder(samples, ["Packs"], samples)
    nested = s.row(nested_root, "Nested", samples)
    s.con.execute("UPDATE files SET place_id = ? WHERE file_id = ?", (nested, nested))
    s.con.execute("INSERT INTO places VALUES (?,?,?,?)", (nested, 12, 1, "Nested"))
    s.sample(samples, sd, "Packs/Nested/Deep Tom.wav", ("Drums|Tom",), parent=None)
    music = s.place("Music", 2)                                        # detached root
    s.sample(music, s.home / "Music", "Rain Field.flac", ())           # untagged
    field = s.place("Field", 2, root_name=str(s.field))                # absolute root name
    s.sample(field, s.field, "Birds/Dawn.wav", ("Sounds|Ambience",))
    projects = s.place("Projects", 2, under=("drive_d",))              # files moved away
    s.sample(projects, None, "Song Project/Samples/Recorded/take 1.wav")
    gone = s.place("Elsewhere", 2)                                     # unknown folder
    s.sample(gone, None, "lost.wav")
    s.commit()
    yield s
    s.con.close()


def build(live, index, **kw):
    kw.setdefault("hints", live.hints())
    kw.setdefault("home", str(live.home))
    kw.setdefault("drives", [str(live.drive)])
    return sample_db.build_index(index, live.db, **kw)


@pytest.fixture
def index(live, tmp_path):
    path = tmp_path / "sample_index.sqlite"
    build(live, path)
    return path


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
    assert status["samples"] == 14 and status["tagged"] == 11
    assert status["with_features"] == 6
    assert fingerprint() == before                      # same files, same bytes
    assert set(Path(tempfile.gettempdir()).glob("LiveBridgeDB_*")) == copies  # copy deleted


def test_paths_and_roots(live, tmp_path):
    status = build(live, tmp_path / "sample_index.sqlite")
    places = {p["name"]: p for p in status["places"]}
    core = str(live.base.joinpath(*CORE, "Core Library"))
    assert places["Core Library"] == dict(places["Core Library"], root=core,
                                          root_from="database", kind="core_library")
    assert places["Samples"]["root"] == str(live.drive / "Samples")
    assert places["Samples"]["root_from"] == "database" and places["Samples"]["kind"] == \
        "user_folder"
    assert places["User Library"] == dict(places["User Library"], root=str(live.user_library),
                                          root_from="live", kind="user_library")
    assert places["Drive and Glow"]["root"] == str(live.packs / "Drive and Glow")
    assert places["Drive and Glow"]["kind"] == "pack"
    assert places["Music"]["root"] == str(live.home / "Music")
    assert places["Field"]["root_from"] == "database"
    # Projects: no file exists any more, so its folder is not "verified" — paths still come
    # from the folders the database keeps
    assert places["Projects"]["verified"] is False and "without_path" not in places["Projects"]
    assert status["unresolved"] == ["Elsewhere"] and status["with_path"] == 13


def test_nested_places_keep_full_paths(index, live):
    tom = sample_db.search(index, query="deep tom")["samples"][0]
    assert tom["path"] == str(live.drive / "Samples" / "Packs" / "Nested" / "Deep Tom.wav")
    assert tom["exists"] and tom["place"] == "Samples"
    assert tom["rel_path"] == os.path.join("Packs", "Nested", "Deep Tom.wav")


def test_only_group_tags_are_tags(index):
    kick = sample_db.search(index, query="kick 909 dry")["samples"][0]
    assert kick["tags"] == ["Character|Dry", "Drums|Kick", "Type|One Shot"]
    assert sample_db.search(index, tags=["Core Library"])["total"] == 0


def test_config_roots_win_and_wrong_guesses_are_rejected(live, tmp_path):
    decoy = live.home / "Music"                         # Music exists; the config points
    elsewhere = tmp_path / "rescued"                    # "Elsewhere" at the right folder
    elsewhere.mkdir()
    (elsewhere / "lost.wav").write_bytes(b"x")
    status = build(live, tmp_path / "i.sqlite",
                   overrides={"elsewhere": str(elsewhere), "Music": str(tmp_path / "nope")})
    places = {p["name"]: p for p in status["places"]}
    assert places["Elsewhere"] == dict(places["Elsewhere"], root=str(elsewhere),
                                       root_from="config", verified=True)
    assert places["Music"]["root"] == str(decoy)       # a failing config folder loses
    assert status["unresolved"] == []


def test_without_live_hints(live, tmp_path):
    status = build(live, tmp_path / "i.sqlite", hints={})
    places = {p["name"]: p for p in status["places"]}
    assert places["User Library"]["root"] == str(live.user_library)
    assert places["User Library"]["root_from"] == "guess"
    assert places["Core Library"]["root_from"] == "database"   # Live not needed any more


def test_search_words_tags_kind_place_source_and_paging(index, live):
    punchy = sample_db.search(index, tags=["Drums|Kick", "character|PUNCHY"], kind="one_shot")
    assert [s["name"] for s in punchy["samples"]] == ["Punchy Kick 01.wav",
                                                      "Punchy Kick 02.wav"]
    first = punchy["samples"][0]
    assert first["path"] == str(live.drive / "Samples" / "Kicks" / "Punchy Kick 01.wav")
    assert first["exists"] and first["place"] == "Samples" and first["kind"] == "one_shot"
    assert first["source"] == "user_folder" and first["use_count"] == 40
    assert first["tags"] == ["Character|Punchy", "Drums|Kick", "Type|One Shot"]

    assert sample_db.search(index, tags=["Punchy"])["total"] == 2           # bare tag
    assert sample_db.search(index, tags=["Drums|*"])["total"] == 8          # whole group
    assert sample_db.search(index, tags=["Drums|Kick"], exclude_tags=["Distorted"])[
        "total"] == 4
    assert sample_db.search(index, any_tags=["Drums|Break", "Drums|Hihat"])["total"] == 2
    assert sample_db.search(index, kind="loop")["total"] == 2
    assert sample_db.search(index, query="kick 909")["samples"][0]["name"] == \
        "Kick 909 Dry.wav"
    assert sample_db.search(index, query="one shots drums")["total"] == 1   # folder words
    assert sample_db.search(index, extensions=["aif"])["total"] == 1
    assert sample_db.search(index, places=["samples", "Music"])["total"] == 7
    assert sample_db.search(index, sources=["core_library"])["total"] == 2
    assert sample_db.search(index, sources=["pack", "user_library"])["total"] == 2
    assert sample_db.search(index, query="100%_")["total"] == 0              # LIKE escaped
    page = sample_db.search(index, tags=["Drums|Kick"], limit=2)
    assert page["total"] == 5 and page["next_offset"] == 2
    rest = sample_db.search(index, tags=["Drums|Kick"], offset=2, limit=3)
    assert "next_offset" not in rest and {s["name"] for s in page["samples"]}.isdisjoint(
        s["name"] for s in rest["samples"])
    shuffled = sample_db.search(index, order="random", seed=7, limit=50)
    assert shuffled == sample_db.search(index, order="random", seed=7, limit=50)
    used = sample_db.search(index, order="most_used", tags=["Drums|Kick"])["samples"]
    assert [s["name"] for s in used[:3]] == ["Punchy Kick 01.wav", "Kick 909 Dry.wav",
                                             "Punchy Kick 02.wav"]
    assert sample_db.search(index, query="lost")["samples"][0]["path"] is None
    with pytest.raises(sample_db.SampleIndexError, match="unknown place"):
        sample_db.search(index, places=["Nowhere"])
    with pytest.raises(sample_db.SampleIndexError, match="unknown source"):
        sample_db.search(index, sources=["cloud9"])


def test_search_reports_moved_files(index):
    take = sample_db.search(index, query="take 1")["samples"][0]
    assert take["path"] and take["exists"] is False


def test_similar_sounds(index, live):
    seed = str(live.drive / "Samples" / "Kicks" / "Punchy Kick 01.wav")
    found = sample_db.similar(index, seed)
    names = [s["name"] for s in found["samples"]]
    assert found["seed"]["name"] == "Punchy Kick 01.wav"
    assert names[0] == "Kick 909 Dry.wav" and "Punchy Kick 01.wav" not in names
    assert names.index("Kick 909 copy.wav") == 1 and names[-1] == "Break 170 Amen.aif"
    assert found["samples"][0]["distance"] < found["samples"][-1]["distance"]
    assert found["total"] == 5
    mine = sample_db.similar(index, seed, sources=["user_folder"])
    assert {s["source"] for s in mine["samples"]} == {"user_folder"} and mine["total"] == 3
    # copies of the seed's own audio are skipped unless asked for
    copy = sample_db.similar(index, "Kick 909 Dry.wav")
    assert "Kick 909 copy.wav" not in [s["name"] for s in copy["samples"]]
    with_copies = sample_db.similar(index, "Kick 909 Dry.wav", include_copies=True)
    assert with_copies["samples"][0]["name"] == "Kick 909 copy.wav"
    assert with_copies["samples"][0]["distance"] == 0
    with pytest.raises(sample_db.SampleIndexError, match="no audio features"):
        sample_db.similar(index, "Vox Chop.wav")
    with pytest.raises(sample_db.SampleIndexError, match="not in the sample index"):
        sample_db.similar(index, "nothing.wav")


def test_duplicates(index):
    found = sample_db.duplicates(index)
    assert found["total_groups"] == 1 and found["duplicate_files"] == 1
    group = found["groups"][0]
    assert group["copies"] == 2
    assert sorted(s["name"] for s in group["samples"]) == ["Kick 909 Dry.wav",
                                                           "Kick 909 copy.wav"]
    assert sample_db.duplicates(index, sources=["user_folder"])["total_groups"] == 0


def test_tag_counts(index):
    groups = {g["group"]: g for g in sample_db.tag_counts(index)["groups"]}
    assert groups["Drums"]["files"] == 8 and groups["Type"]["files"] == 7
    assert groups["Drums"]["top"][0] == {"tag": "Drums|Kick", "files": 5}
    assert "" not in groups                              # non-tag metadata left out
    character = sample_db.tag_counts(index, group="character")["tags"]
    assert character[0] == {"tag": "Character|Punchy", "files": 2}
    assert sample_db.tag_counts(index, filter="punch")["tags"] == [
        {"tag": "Character|Punchy", "files": 2}]


def test_feature_blobs():
    assert sample_db._features(features(1.0, 2.0)) == struct.pack("<2f", 1.0, 2.0)
    assert sample_db._features(b"\x12\x00\x00\x00\x40\x00\x00\x00\x00\x00\x00\x00") is None
    assert sample_db._features(struct.pack("<3I", 18, 64, 0) + b"\x00" * 10) is None
    assert sample_db._features(None) is None


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
    assert first["index"]["refreshed"] and first["index"]["unresolved_places"] == ["Elsewhere"]
    assert first["total"] == 2 and first["next_offset"] == 1
    assert first["samples"][0]["path"].endswith(os.path.join("Kicks", "Punchy Kick 01.wav"))
    asked = [r for r in fake.requests if r["cmd"] == "samples.locations"]
    again = call_tool(app, "live_sample_search", {"query": "glow bass"})
    assert "index" not in again and again["samples"][0]["place"] == "Drive and Glow"
    assert len([r for r in fake.requests if r["cmd"] == "samples.locations"]) == len(asked)


def test_search_tool_similar_most_used_source_and_duplicates(tools, live):
    app, _config, _fake = tools
    similar = call_tool(app, "live_sample_search", {"similar_to": "Punchy Kick 01.wav",
                                                    "tags": ["Drums|Kick"], "limit": 2})
    assert similar["seed"]["name"] == "Punchy Kick 01.wav"
    assert [s["name"] for s in similar["samples"]] == ["Kick 909 Dry.wav", "Kick 909 copy.wav"]
    assert "distance" in similar["samples"][0]
    used = call_tool(app, "live_sample_search", {"order": "most_used", "limit": 1})
    assert used["samples"][0]["name"] == "Punchy Kick 01.wav"
    core = call_tool(app, "live_sample_search", {"source": "core_library"})
    assert core["total"] == 2
    dupes = call_tool(app, "live_sample_duplicates", {})
    assert dupes["total_groups"] == 1 and dupes["groups"][0]["copies"] == 2
    assert call_tool(app, "live_sample_search", {"similar_to": "nothing.wav"})["type"] == \
        "not_found"
    assert call_tool(app, "live_sample_search", {"source": "moon"})["type"] == "not_found"
    assert call_tool(app, "live_sample_duplicates", {"limit": 0})["type"] == "bad_args"


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
    assert status["refreshed"] and status["samples"] == 14
    assert status["unresolved"] == ["Elsewhere"] and "sample_index" in status["hint"]
    places = {p["name"]: p["root_from"] for p in status["places"]}
    assert places["Samples"] == "config" and places["Core Library"] == "database"
    tags = call_tool(app, "live_sample_tags", {"group": "Drums"})
    assert tags["tags"][0] == {"tag": "Drums|Kick", "files": 5}
    fake.set_error("samples.locations", "connection", "Live is not running", once=False)
    offline = call_tool(app, "live_sample_index", {"action": "refresh"})
    assert offline["live_reachable"] is False
    assert {"User Library", "Drive and Glow"} <= {p["name"] for p in offline["places"]}


def test_tool_validation_and_missing_database(tools, tmp_path, monkeypatch):
    app, _config, _fake = tools
    for args in ({"kind": "loops"}, {"order": "best"}, {"limit": 0}, {"tags": [""]},
                 {"similar_to": " "}):
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
    monkeypatch.setenv("LIVEBRIDGE_CONFIG", str(tmp_path / "no-config.json"))
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
    assert status["refreshed"] and status["samples"] == 14
    assert sample_check.main(["--tags", "Drums|Kick", "Punchy", "--kind", "one_shot"]) == 0
    found = json.loads(capsys.readouterr().out)
    assert found["total"] == 2
    assert sample_check.main(["--list-tags", "Character"]) == 0
    assert json.loads(capsys.readouterr().out)["tags"][0]["tag"] == "Character|Punchy"
    assert sample_check.main(["--similar", "Punchy Kick 01.wav", "--limit", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["samples"][0]["name"] == "Kick 909 Dry.wav"
    assert sample_check.main(["--order", "most_used", "--limit", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["samples"][0]["use_count"] == 40
    assert sample_check.main(["--duplicates"]) == 0
    assert json.loads(capsys.readouterr().out)["total_groups"] == 1
