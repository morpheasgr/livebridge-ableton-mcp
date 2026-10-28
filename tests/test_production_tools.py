"""Generative notes, the sound library, session memory, masking and before/after comparison:
the pure helpers and the MCP tools end to end against the stub set."""

import math
import struct
import sys
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp_server"))

from livebridge_mcp import library  # noqa: E402
from livebridge_mcp.client import BridgeClient  # noqa: E402
from livebridge_mcp.server import create_app  # noqa: E402
from livebridge_mcp.tools.generate import euclid, row_string, step_beats  # noqa: E402

from test_consistency import _APP, call_tool  # noqa: E402


def steps(pattern):
    return "".join("x" if hit else "." for hit in pattern)


@pytest.fixture
def lib(tmp_path, monkeypatch):
    folder = tmp_path / "library"
    monkeypatch.setenv("LIVEBRIDGE_LIBRARY", str(folder))
    return folder


@pytest.fixture
def live_app(tcp_bridge, lib):
    client = BridgeClient(host="127.0.0.1", port=tcp_bridge.port, timeout=5.0)
    try:
        yield create_app(client)
    finally:
        client.close()


# ----------------------------------------------------------------------------- generative

def test_euclidean_rhythms():
    assert steps(euclid(3, 8)) == "x..x..x."            # tresillo
    assert steps(euclid(4, 16)) == "x...x...x...x..."   # four on the floor
    assert steps(euclid(5, 8)) == "x.x.xx.x"
    assert steps(euclid(3, 8, rotate=2)) == "x.x..x.."
    assert steps(euclid(3, 8, rotate=-1)) == "..x..x.x"
    assert steps(euclid(0, 4)) == "...." and steps(euclid(9, 4)) == "xxxx"
    assert all(sum(euclid(h, n)) == h for n in range(1, 17) for h in range(n + 1))


def test_row_strings_polymeter_accent_and_velocity():
    spec = {"hits": 2, "steps": 5, "rotate": 0, "velocity": None, "accent": False}
    assert row_string(spec, 10) == "x..x.x..x."           # the 5-step cycle repeats
    accented = dict(spec, accent=True)
    assert row_string(accented, 10) == "X..x.X..x."
    assert row_string(dict(spec, velocity=70), 5) == "5..5."  # 70/127 -> level 5 of 9
    assert step_beats("1/16") == 0.25 and step_beats("1/8") == 0.5
    assert abs(step_beats("1/8T") - 1 / 3) < 1e-9 and step_beats("x") is None


def test_write_euclid_end_to_end(live_app):
    result = call_tool(live_app, "live_clip_write_euclid", {
        "track": "Drums", "slot": 2, "bars": 2,
        "rhythms": {"kick": "4/16", "hat": {"hits": 5, "steps": 7, "accent": True},
                    "rim": "3/8+2"}})
    assert result["steps"] == 32 and result["created"] is True
    assert result["patterns"]["kick"] == "x...x...x...x..." * 2
    assert result["patterns"]["hat"].startswith("X.xx.xxX")
    notes = call_tool(live_app, "live_clip_get_notes", {"track": "Drums", "slot": 2,
                                                        "pitch": "C1"})
    assert [n[1] for n in notes["notes"]] == [0, 1, 2, 3, 4, 5, 6, 7]


def test_write_euclid_validates(live_app):
    for rhythms in ({"kick": "4-16"}, {"kick": "17/16"}, {"kick": {"hits": 2}},
                    {"kick": {"hits": 2, "steps": 8, "swing": 1}}, {}):
        result = call_tool(live_app, "live_clip_write_euclid",
                           {"track": "Drums", "slot": 2, "rhythms": rhythms})
        assert result["type"] == "bad_args", rhythms
    assert call_tool(live_app, "live_clip_write_euclid", {
        "track": "Drums", "slot": 2, "rhythms": {"kick": "4/16"}, "step": "1/5x"
    })["type"] == "bad_args"


def test_note_chance_keeps_downbeats(live_app):
    call_tool(live_app, "live_clip_write_pattern", {
        "track": "Drums", "slot": 3, "pattern": {"hat": "xxxxxxxxxxxxxxxx"}})
    result = call_tool(live_app, "live_clip_note_chance", {
        "track": "Drums", "slot": 3, "pitch": "F#1", "probability": [0.4, 0.9], "seed": 3,
        "velocity_deviation": 12})
    assert result["changed"] == 12 and result["kept"] == 4
    assert all(0.4 <= n["probability"] <= 0.9 for n in result["notes"])
    notes = call_tool(live_app, "live_clip_get_notes", {"track": "Drums", "slot": 3})["notes"]
    by_start = {n[1]: n[5] for n in notes}
    assert by_start[0] == 1 and by_start[1] == 1 and by_start[0.25] < 1
    again = call_tool(live_app, "live_clip_note_chance", {
        "track": "Drums", "slot": 3, "probability": 0.5, "keep_downbeats": False})
    assert again["changed"] == 16
    for bad in (1.5, [0.9, 0.2], [0.1]):
        assert call_tool(live_app, "live_clip_note_chance", {
            "track": "Drums", "slot": 3, "probability": bad})["type"] == "bad_args"


# ----------------------------------------------------------------------------- sounds

def test_sound_library_round_trip(live_app, lib):
    operator = {"track": "Bass", "device": "Operator"}
    saved = call_tool(live_app, "live_sound_save",
                      dict(operator, name="Deep Bass", tags=["Bass", "dub"]))
    assert saved["parameters"] == 6 and saved["device"]["class_name"] == "Operator"
    assert (lib / "sounds" / "deep-bass.json").is_file()
    assert call_tool(live_app, "live_sound_save",
                     dict(operator, name="deep bass"))["type"] == "invalid_state"

    call_tool(live_app, "live_device_set_parameters",
              dict(operator, values={"Filter Freq": 5000.0, "Resonance": 0.9, "Attack": 0.3}))
    preview = call_tool(live_app, "live_sound_load", dict(operator, name="Deep Bass",
                                                          dry_run=True))
    assert preview["dry_run"] and preview["changed"] == 3
    assert call_tool(live_app, "live_device_get_parameter",
                     dict(operator, parameter="Filter Freq"))["value"] == 5000.0

    partial = call_tool(live_app, "live_sound_load",
                        dict(operator, name="Deep Bass", only=["filter"]))
    assert partial["changed"] == 1
    loaded = call_tool(live_app, "live_sound_load", dict(operator, name="Deep Bass"))
    assert loaded["changed"] == 2 and loaded["unchanged"] == 4
    for name, value in (("Filter Freq", 800.0), ("Resonance", 0.2), ("Attack", 0.01)):
        assert call_tool(live_app, "live_device_get_parameter",
                         dict(operator, parameter=name))["value"] == pytest.approx(value)

    listing = call_tool(live_app, "live_sound_list", {"tag": "bass"})
    assert listing["count"] == 1 and listing["sounds"][0]["tags"] == ["bass", "dub"]
    assert call_tool(live_app, "live_sound_list", {"filter": "lead"})["count"] == 0


def test_sound_load_checks_device_type_and_names(live_app):
    call_tool(live_app, "live_sound_save", {"name": "Op", "track": "Bass", "device": "Operator"})
    reverb = {"track": "Vocals", "device": "Reverb"}
    refused = call_tool(live_app, "live_sound_load", dict(reverb, name="Op"))
    assert refused["type"] == "invalid_state" and "force" in refused["error"]
    forced = call_tool(live_app, "live_sound_load", dict(reverb, name="Op", force=True,
                                                          dry_run=True))
    assert forced["missing"]                    # Operator knobs the Reverb does not have
    missing = call_tool(live_app, "live_sound_load", dict(reverb, name="Nope"))
    assert missing["type"] == "not_found" and "Op" in missing["error"]
    assert call_tool(live_app, "live_sound_delete", {"name": "Op"})["deleted"] == "Op"
    assert call_tool(live_app, "live_sound_delete", {"name": "Op"})["type"] == "not_found"


def test_library_helpers(tmp_path, monkeypatch):
    assert library.slug("Reese Bass #2") == "reese-bass-2"
    assert library.slug("Ünïcode Pad") == "unicode-pad" and library.slug("!!!") == "untitled"
    target = tmp_path / "deep" / "file.json"
    library.write_json(target, {"a": [1, 2]})
    assert library.read_json(target, None) == {"a": [1, 2]}
    assert library.read_json(tmp_path / "missing.json", {"x": 1}) == {"x": 1}
    assert not list(target.parent.glob("*.tmp"))
    monkeypatch.setenv("LIVEBRIDGE_LIBRARY", str(tmp_path / "lib"))
    assert library.library_dir() == tmp_path / "lib"


# ----------------------------------------------------------------------------- memory

def test_memory_notes_recall_and_preferences(live_app, lib):
    assert call_tool(live_app, "live_memory_note",
                     {"text": "kept the Reese", "kind": "decision",
                      "project": "Night Drive"})["project"] == "Night Drive"
    idea = call_tool(live_app, "live_memory_note", {"text": "chorus needs lift", "kind": "idea",
                                                    "tags": ["Chorus"]})
    assert idea["project"] == "Night Drive" and idea["fingerprint"]["tracks"] == 3
    call_tool(live_app, "live_memory_note", {"text": "likes dry drums", "kind": "preference"})

    recall = call_tool(live_app, "live_memory_recall", {})
    assert recall["matched_by"] == "fingerprint" and recall["similarity"] == 1.0
    assert [e["text"] for e in recall["entries"]] == ["kept the Reese", "chorus needs lift"]
    assert [p["text"] for p in recall["preferences"]] == ["likes dry drums"]
    assert call_tool(live_app, "live_memory_recall",
                     {"kind": "idea"})["entries"][0]["tags"] == ["chorus"]

    call_tool(live_app, "live_memory_update", {"id": idea["id"], "done": True})
    assert len(call_tool(live_app, "live_memory_recall", {"project": "night drive"})
               ["entries"]) == 1
    assert len(call_tool(live_app, "live_memory_recall", {"include_done": True})
               ["entries"]) == 2
    assert call_tool(live_app, "live_memory_forget",
                     {"project": "Night Drive"})["deleted"] == 2
    after = call_tool(live_app, "live_memory_recall", {})
    assert after["entries"] == [] and len(after["preferences"]) == 1
    assert (lib / "memory.json").is_file()


def test_memory_follows_the_open_set(live_app, song):
    # Never saved: the note goes to "default" and says so.
    early = call_tool(live_app, "live_memory_note", {"text": "four-chord loop", "kind": "idea"})
    assert early["project"] == "default" and early["unsaved_set"] is True
    assert call_tool(live_app, "live_memory_recall", {})["unsaved_set"] is True

    # Saved as aitest2: move the early notes over, then new notes land there by themselves.
    song._file_path = r"D:\Projects\aitest2 Project\aitest2.als"
    song._name = "aitest2"
    moved = call_tool(live_app, "live_memory_update", {"id": early["id"], "project": "aitest2",
                                                       "whole_project": True})
    assert moved["project"] == "aitest2" and moved["moved"] == 1
    note = call_tool(live_app, "live_memory_note", {"text": "kept the pad", "kind": "decision"})
    assert note["project"] == "aitest2" and "unsaved_set" not in note

    # Another set is open meanwhile; coming back finds aitest2 by its file.
    song._file_path, song._name = r"D:\Projects\Other Project\Other.als", "Other"
    call_tool(live_app, "live_memory_note", {"text": "other set", "kind": "note"})
    song._file_path, song._name = r"d:/projects/aitest2 project/aitest2.als", "aitest2"
    recall = call_tool(live_app, "live_memory_recall", {})
    assert recall["matched_by"] == "set" and recall["project"] == "aitest2"
    assert recall["set"] == "aitest2"
    assert [e["text"] for e in recall["entries"]] == ["four-chord loop", "kept the pad"]
    assert "default" not in [p["name"] for p in recall["projects"]]
    assert call_tool(live_app, "live_memory_update",
                     {"id": note["id"], "whole_project": True, "done": True})["type"] == \
        "bad_args"


def test_memory_validation(live_app):
    assert call_tool(live_app, "live_memory_note",
                     {"text": "x", "kind": "rumour"})["type"] == "bad_args"
    assert call_tool(live_app, "live_memory_note", {"text": " "})["type"] == "bad_args"
    assert call_tool(live_app, "live_memory_update", {"id": 99, "done": True})["type"] == \
        "not_found"
    assert call_tool(live_app, "live_memory_update", {"id": 1})["type"] == "bad_args"
    assert call_tool(live_app, "live_memory_forget", {})["type"] == "bad_args"
    assert call_tool(live_app, "live_memory_recall", {"limit": 0})["type"] == "bad_args"


def test_memory_without_live_uses_last_project(lib):
    offline = create_app(BridgeClient(host="127.0.0.1", port=9, timeout=0.5))
    note = call_tool(offline, "live_memory_note", {"text": "sketch", "project": "Offline"})
    assert note["project"] == "Offline" and "fingerprint" not in note
    recall = call_tool(offline, "live_memory_recall", {})
    assert recall["matched_by"] == "last_used" and recall["entries"][0]["text"] == "sketch"


# ----------------------------------------------------------------------------- audio

def write_tone(path, freqs, seconds=6.0, rate=44100, amp=0.3, gate=None):
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        frames = bytearray()
        for i in range(int(seconds * rate)):
            t = i / rate
            on = 1.0 if gate is None or (t % 0.5) < gate else 0.0
            value = on * amp * sum(math.sin(2 * math.pi * f * t) for f in freqs) / len(freqs)
            frames += struct.pack("<h", int(value * 32767))
        stream.writeframes(bytes(frames))
    return str(path)


def test_masking_finds_the_kick_bass_fight(tmp_path):
    pytest.importorskip("numpy")
    pytest.importorskip("scipy")
    pytest.importorskip("soundfile")
    kick = write_tone(tmp_path / "kick.wav", [55, 110], gate=0.15)
    bass = write_tone(tmp_path / "bass.wav", [55, 82, 110])
    hat = write_tone(tmp_path / "hat.wav", [8000, 10000], amp=0.1)
    result = call_tool(_APP, "live_audio_masking", {"sources": [
        kick, {"file_path": bass, "name": "Bass"}, hat]})
    worst = result["pairs"][0]
    assert worst["parts"] == ["kick", "Bass"] and worst["score"] >= 20
    assert worst["bands"][0]["region"] in ("sub", "bass")
    assert "side-chain" in worst["advice"][0]
    assert all(pair["score"] < 5 for pair in result["pairs"][1:])
    assert call_tool(_APP, "live_audio_masking", {"sources": [kick]})["type"] == "bad_args"
    assert call_tool(_APP, "live_audio_masking", {
        "sources": [kick, str(tmp_path / "missing.wav")]})["type"] == "not_found"


def test_compare_reports_changes_and_verdicts(tmp_path):
    pytest.importorskip("numpy")
    pytest.importorskip("pyloudnorm")
    before = write_tone(tmp_path / "before.wav", [55, 220])
    after = write_tone(tmp_path / "after.wav", [55, 220, 3000], amp=0.6)
    result = call_tool(_APP, "live_audio_compare", {"before": before, "after": after})
    comparison = result["comparison"]
    assert comparison["delta"]["lufs"] > 3
    assert any(text.startswith("presence") for text in comparison["changes"])
    assert any("louder always sounds better" in note for note in comparison["notes"])
    same = call_tool(_APP, "live_audio_compare", {"before": before, "after": before})
    assert same["comparison"]["changes"] == ["no measurable change"]
