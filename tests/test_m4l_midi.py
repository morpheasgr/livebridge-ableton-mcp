"""The LiveBridge MIDI Max for Live device: the built .amxd, lbmidi.js run under node with a
stand-in for Max's API (see test_m4l_engine) and the live_midi_* tools."""

import json
import os
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "mcp_server"))
sys.path.insert(0, str(REPO / "max_for_live"))

import build as m4l_build  # noqa: E402
from livebridge_mcp import engine  # noqa: E402
from livebridge_mcp.client import BridgeClient  # noqa: E402
from livebridge_mcp.server import create_app  # noqa: E402
from livebridge_mcp.tools.midi import notes_from  # noqa: E402

from test_consistency import call_tool  # noqa: E402
from test_m4l_engine import engines  # noqa: E402,F401  (fixture)

MIDI_JS = REPO / "max_for_live" / "LiveBridge MIDI" / "lbmidi.js"


def test_midi_amxd_is_built_and_wired():
    assert m4l_build.main(["--check"]) == 0, "run python max_for_live/build.py"
    device_type, patcher = m4l_build.read_amxd(m4l_build.MIDI_AMXD.read_bytes())
    assert device_type == m4l_build.MIDI_EFFECT
    boxes = {b["box"]["id"]: b["box"] for b in patcher["patcher"]["boxes"]}
    for line in patcher["patcher"]["lines"]:
        (source, outlet), (destination, inlet) = (line["patchline"]["source"],
                                                  line["patchline"]["destination"])
        assert outlet < boxes[source]["numoutlets"] and inlet < boxes[destination]["numinlets"]
    texts = {b.get("text") for b in boxes.values()}
    assert {"midiin", "midiout", "js lbmidi_context.js"} <= texts
    for dependency in patcher["patcher"]["dependency_cache"]:
        assert (m4l_build.MIDI_DIR / dependency["name"]).is_file(), dependency["name"]
    # MIDI passes straight through, and notes from lbmidi.js join it
    lines = {(l["patchline"]["source"][0], l["patchline"]["destination"][0])
             for l in patcher["patcher"]["lines"]}
    assert ("obj-midiin", "obj-midiout") in lines and ("obj-midiformat", "obj-midiout") in lines
    assert m4l_build.MIDI_CONTEXT.read_bytes() == m4l_build.ENGINE_CONTEXT.read_bytes()


def test_notes_from_played_events():
    answer = {"tempo": 120.0, "now": 10_000, "now_beat": 9.0, "notes": [
        {"pitch": 60, "velocity": 100, "ms": 1000, "beat": 5.05, "end_ms": 1500, "end_beat": 6.0},
        {"pitch": 64, "velocity": 90, "ms": 1500, "beat": 6.02, "end_ms": None,
         "end_beat": None, "held": True},
    ]}
    result = notes_from(answer, 4.0)
    assert result["origin_beat"] == 4.0 and result["bars"] == 2
    first, second = result["notes"]
    assert first == {"pitch": 60, "start": 1.05, "duration": 0.95, "velocity": 100}
    assert result["held"] == [64] and "held" not in second
    assert second["duration"] == pytest.approx(9.0 - 6.02)
    snapped = notes_from(answer, 4.0, quantize=0.25)["notes"]
    assert snapped[0]["start"] == 1.0 and snapped[0]["duration"] == 1.0
    # no song position: beats from the clock at the tempo
    loose = notes_from({"tempo": 60.0, "now": 4000, "now_beat": None, "notes": [
        {"pitch": 48, "velocity": 80, "ms": 2000, "beat": None, "end_ms": 3000,
         "end_beat": None}]}, 4.0)
    assert loose["notes"][0]["duration"] == pytest.approx(1.0)
    assert notes_from({"notes": []}, 4.0) == {"notes": [], "origin_beat": None, "bars": 0}


def _plays(outlets):
    path = Path(str(outlets) + ".play")
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_midi_device_end_to_end(engines):  # noqa: F811
    _process, send, outlets = engines(script=MIDI_JS)
    send("context", "track", "Keys")
    assert engine.wait_for(lambda: engine.devices(kind="midi") and
                           engine.devices(kind="midi")[0]["role"] == "track", 5)
    assert engine.devices() == []                         # not an Engine
    app = create_app(BridgeClient(host="127.0.0.1", port=9, timeout=0.5))
    assert call_tool(app, "live_engine_listen", {})["type"] == "not_found"
    status = call_tool(app, "live_engine_status", {})
    assert status["midi_devices"][0]["track"] == "Keys"

    # what was played: 120 BPM from beat 4, a note of ~half a beat and one still held
    send("transport", 4, 120, 1)
    time.sleep(0.05)
    send("note", 60, 100)
    send("cc", 1, 64)
    time.sleep(0.25)
    send("note", 60, 0)
    send("note", 64, 90)
    send("cc", 1, 70)
    time.sleep(0.1)
    played = call_tool(app, "live_midi_played", {"seconds": 5, "quantize": "1/8"})
    assert played["device"] == {"track": "Keys"} and played["origin_beat"] == 4.0
    first, second = played["notes"]
    assert first["pitch"] == 60 and first["velocity"] == 100 and first["duration"] == 0.5
    assert second["pitch"] == 64 and played["held"] == [64]
    assert played["controllers"] == [{"cc": 1, "values": 2, "last": 70}]
    assert call_tool(app, "live_midi_played", {"seconds": 0})["type"] == "bad_args"
    assert call_tool(app, "live_midi_played", {"quantize": "2 bars"})["type"] == "bad_args"
    assert call_tool(app, "live_midi_played", {"track": "Bass"})["type"] == "not_found"

    # playing: at 240 BPM (a beat every 0.25 s), now, twice
    send("transport", 0, 240, 0)
    time.sleep(0.05)
    started = time.time() * 1000
    phrase = [{"pitch": 60, "start": 0, "duration": 0.5, "velocity": 110},
              {"pitch": 67, "start": 1, "duration": 0.5}]
    result = call_tool(app, "live_midi_play", {"notes": phrase, "start": "now", "repeat": 2})
    assert result["playing"]["loop_beats"] == 2 and result["playing"]["seconds"] == \
        pytest.approx(1.0, abs=0.05)
    assert engine.wait_for(lambda: len(_plays(outlets)) >= 8, 3)
    events = [(round((t - started) / 250, 1), p, v) for t, _, p, v in _plays(outlets)]
    assert [(p, v) for _, p, v in events] == [(60, 110), (60, 0), (67, 100), (67, 0)] * 2
    starts = [beat for beat, _, v in events if v]
    assert starts == pytest.approx([0, 1, 2, 3], abs=0.25)

    # stop releases what sounds; a new phrase replaces the old one
    call_tool(app, "live_midi_play", {"notes": [{"pitch": 48, "start": 0, "duration": 8}],
                                      "start": "now"})
    assert engine.wait_for(lambda: _plays(outlets)[-1][2:] == [48, 100], 2)
    stopped = call_tool(app, "live_midi_stop", {})
    assert stopped["stopped"][0]["was"]["notes"] == 1
    assert _plays(outlets)[-1][2:] == [48, 0]
    for args in ({"notes": []}, {"notes": [{"pitch": 200, "start": 0, "duration": 1}]},
                 {"notes": phrase, "start": "later"}, {"notes": phrase, "repeat": 0}):
        assert call_tool(app, "live_midi_play", args)["type"] == "bad_args", args


def test_midi_tools_without_a_device(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVEBRIDGE_ENGINE_DIR", str(tmp_path))
    app = create_app(BridgeClient(host="127.0.0.1", port=9, timeout=0.5))
    missing = call_tool(app, "live_midi_played", {})
    assert missing["type"] == "not_found" and "live_browser_load" in missing["error"]
    assert call_tool(app, "live_midi_stop", {})["type"] == "not_found"
