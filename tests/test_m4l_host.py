"""The LiveBridge Host devices (a VST3 inside Max's vst~, every parameter reachable): the built
.amxd files, lbhost.js under node with a simulated plug-in (LIVEBRIDGE_HOST_FAKE=1) and the
live_plugin_host_* tools."""

import json
import sys
import threading
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
from livebridge_mcp.tools import plugin_host  # noqa: E402

from test_consistency import call_tool  # noqa: E402
from test_m4l_engine import engines  # noqa: E402,F401  (fixture)

HOST_JS = REPO / "max_for_live" / "LiveBridge Host" / "lbhost.js"


def test_host_amxd_files_are_built_and_wired():
    assert m4l_build.main(["--check"]) == 0, "run python max_for_live/build.py"
    for path, kind, first in ((m4l_build.HOST_EFFECT_AMXD, m4l_build.AUDIO_EFFECT, "plugin~"),
                              (m4l_build.HOST_INSTRUMENT_AMXD, m4l_build.INSTRUMENT,
                               "midiparse")):
        device_type, patcher = m4l_build.read_amxd(path.read_bytes())
        assert device_type == kind
        boxes = {b["box"]["id"]: b["box"] for b in patcher["patcher"]["boxes"]}
        lines = {(tuple(l["patchline"]["source"]), tuple(l["patchline"]["destination"]))
                 for l in patcher["patcher"]["lines"]}
        assert boxes["obj-vst"]["text"].startswith("vst~ 2 2")
        assert (("obj-vst", 0), ("obj-plugout", 0)) in lines
        assert (("obj-route", 3), ("obj-vst", 0)) in lines           # lbhost.js -> vst~
        assert (("obj-vst", 2), ("obj-vstout0", 0)) in lines         # vst~ -> lbhost.js
        assert any(b.get("text") == first for b in boxes.values())
        # a button on the device opens the plug-in's window
        assert boxes["obj-open"]["maxclass"] == "textbutton" and boxes["obj-open"]["presentation"]
        assert (("obj-open", 0), ("obj-openbang", 0)) in lines
        assert (("obj-openmsg", 0), ("obj-vst", 0)) in lines and \
            boxes["obj-openmsg"]["text"] == "open"
        assert patcher["patcher"]["project"]["searchpath"] == {}
    assert m4l_build.HOST_CONTEXT.read_bytes() == m4l_build.ENGINE_CONTEXT.read_bytes()


def test_find_vst3(tmp_path):
    for name in ("Serum2.vst3", "Serum2 FX.vst3", "FabFilter Pro-Q 4.vst3", "Diva(x64).vst3"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "Contents").mkdir()
    assert plugin_host.find_vst3("Serum 2", [tmp_path])[0]["file"] == "Serum2"
    assert plugin_host.find_vst3("Pro-Q 4", [tmp_path])[0]["file"] == "FabFilter Pro-Q 4"
    assert plugin_host.find_vst3("Diva", [tmp_path])[0]["file"] == "Diva(x64)"
    assert plugin_host.find_vst3("Nothing", [tmp_path]) == []


def test_host_device_end_to_end(engines, monkeypatch):  # noqa: F811
    monkeypatch.setenv("LIVEBRIDGE_HOST_FAKE", "1")
    process, send, _outlets = engines(script=HOST_JS)
    send("context", "track", "Lead")
    assert engine.wait_for(lambda: any(d.get("track") == "Lead"
                                       for d in engine.devices(kind="host")), 5)
    (host,) = engine.devices(kind="host")
    assert engine.devices() == []                    # not an Engine
    loaded = engine.request(host, "load", {"file": "Serum2", "sub": "Serum 2",
                                           "name": "Serum 2"})
    assert loaded["parameters"] == 4 and loaded["hidden_midi"] == 130
    # the Live device is named after the plug-in (kept in the Set, read back after a reopen)
    assert ["context", "rename", "Serum 2"] in [json.loads(l) for l in
                                                _outlets.read_text().splitlines()]

    app = create_app(BridgeClient(host="127.0.0.1", port=1, timeout=1.0))
    listed = call_tool(app, "live_plugin_host_parameters", {"track": "Lead"})
    assert [p["name"] for p in listed["parameters"]] == ["Cutoff", "Resonance", "Mode",
                                                         "Volume"]
    assert listed["parameters"][3]["id"] == "h134"
    assert call_tool(app, "live_plugin_host_parameters", {"search": "res"})["total"] == 1

    changed = call_tool(app, "live_plugin_host_set", {"values": {
        "Cutoff": "1000 Hz", "Mode": "HP", "resonance": 0.25, "h134": "-6 dB",
        "Nope": 1, "Volume ": "999 dB"}})
    results = {r["name"]: r for r in changed["results"]}
    assert results["Cutoff"]["ok"] and abs(int(results["Cutoff"]["display"].split()[0])
                                           - 1000) <= 30
    assert results["Mode"]["display"] == "HP"
    assert results["Resonance"]["display"] == "25 %"
    assert results["Nope"]["ok"] is False
    assert changed["applied"] >= 4
    volume = [r for r in changed["results"] if r.get("id") == "h134"]
    assert volume[0]["display"] == "-6.0 dB"
    assert volume[-1]["ok"] is False and "-6.0 dB" in volume[-1]["error"]   # put back

    assert call_tool(app, "live_plugin_host_set", {"values": {}})["type"] == "bad_args"
    assert call_tool(app, "live_plugin_host_parameters", {"track": "Bass"})["type"] == \
        "not_found"


def test_host_tools_without_a_device(tmp_path, monkeypatch, tcp_bridge):
    monkeypatch.setenv("LIVEBRIDGE_ENGINE_DIR", str(tmp_path / "none"))
    monkeypatch.setenv("LIVEBRIDGE_VST3_PATH", str(tmp_path))
    (tmp_path / "Serum2.vst3").mkdir()
    app = create_app(BridgeClient(host="127.0.0.1", port=tcp_bridge.port, timeout=10.0))
    assert call_tool(app, "live_plugin_host_parameters", {})["type"] == "not_found"
    assert call_tool(app, "live_plugin_host_set", {"values": {"a": 1}})["type"] == "not_found"
    assert call_tool(app, "live_plugin_host_load", {"plugin": "Nothing at all"})["type"] == \
        "not_found"
    assert call_tool(app, "live_plugin_host_load", {"plugin": "Serum 2", "kind": "fx"})[
        "type"] == "bad_args"
    assert call_tool(app, "live_plugin_host_load", {"plugin": "Serum 2", "kind": "effect"})[
        "type"] == "bad_args"
    # the stub browser has no LiveBridge Host device: the hint says what to install
    missing = call_tool(app, "live_plugin_host_load", {"plugin": "Serum 2"})
    assert missing["type"] == "not_found" and "installed" in missing["error"]


# ----------------------------------------------------------------------------- automation bank

def test_bank_device_patch():
    from livebridge_mcp import host_device
    bank = [{"name": "Filter Cutoff", "index": 7}, {"name": "Filter Cutoff", "index": 9},
            {"name": "Env 2 Attack", "index": 30}]
    device_type, patcher = m4l_build.read_amxd(host_device.bank_device(
        {"file": "Serum2", "sub": "Serum 2", "name": "Serum 2"}, True, bank))
    assert device_type == m4l_build.INSTRUMENT
    boxes = {b["box"]["id"]: b["box"] for b in patcher["patcher"]["boxes"]}
    lines = {(tuple(l["patchline"]["source"]), tuple(l["patchline"]["destination"]))
             for l in patcher["patcher"]["lines"]}
    names = [boxes[f"obj-dial{i}"]["saved_attribute_attributes"]["valueof"]
             ["parameter_longname"] for i in range(3)]
    assert names == ["Filter Cutoff", "Filter Cutoff 2", "Env 2 Attack"]   # unique for Live
    assert boxes["obj-dialout1"]["text"] == "prepend 9"
    assert (("obj-dial1", 0), ("obj-dialout1", 0)) in lines
    assert (("obj-dialout1", 0), ("obj-vst", 0)) in lines
    assert boxes["obj-bankroute"]["text"] == "route 7 9 30"
    assert (("obj-bankroute", 2), ("obj-dialset2", 0)) in lines
    assert (("obj-dialset2", 0), ("obj-dial2", 0)) in lines
    token = boxes["obj-node"]["text"].split("@args ")[1]
    config = json.loads(bytes.fromhex(token[len("lbhost:"):]).decode())
    assert config["kind"] == "instrument" and config["autoload"]["file"] == "Serum2"
    assert [e["index"] for e in config["bank"]] == [7, 9, 30]
    assert patcher["patcher"]["devicewidth"] > 200


def _config(**config):
    from livebridge_mcp import host_device
    return host_device.encode_config(config)


def test_bank_end_to_end(engines, monkeypatch, tmp_path):  # noqa: F811
    from fake_bridge import FakeBridge
    monkeypatch.setenv("LIVEBRIDGE_HOST_FAKE", "1")
    _process, send, _outlets = engines(script=HOST_JS)
    send("context", "track", "Lead")
    assert engine.wait_for(lambda: any(d.get("track") == "Lead"
                                       for d in engine.devices(kind="host")), 5)
    (old,) = engine.devices(kind="host")
    engine.request(old, "load", {"file": "FakeSynth", "sub": "Fake", "name": "Fake"})
    engine.request(old, "set", {"values": {"Cutoff": "1000 Hz", "Resonance": 0.8}})

    library = tmp_path / "User Library"
    library.mkdir()
    bridge = FakeBridge().start()
    bridge.set_result("samples.locations", {"user_library": [{"path": str(library),
                                                               "exists": True}]})
    bridge.set_result("browser.load", {"track": "Lead", "inserted": []})
    bridge.set_result("devices.list", {"devices": [
        {"name": "Fake", "path": "song.tracks[0].devices[0]"},          # renamed by load()
        {"name": "Fake Bank", "path": "song.tracks[0].devices[1]"}]})
    bridge.set_result("devices.delete", {"deleted": True})

    # what Live does when the bank device loads: a second host starts with its config
    def live_loads_the_bank():
        time.sleep(1.0)
        monkeypatch.setenv("LIVEBRIDGE_HOST_CONFIG", _config(
            kind="instrument", autoload={"file": "FakeSynth", "sub": "Fake", "name": "Fake"},
            bank=[{"name": "Cutoff", "index": 1}]))
        _p, send2, outlets2 = engines(script=HOST_JS)
        send2("context", "track", "Lead")
        started.append(outlets2)

    started = []
    loader = threading.Thread(target=live_loads_the_bank)
    loader.start()
    app = create_app(BridgeClient(host=bridge.host, port=bridge.port, timeout=5.0))
    try:
        result = call_tool(app, "live_plugin_host_bank", {"parameters": ["Cutoff", "h2"],
                                                          "track": "Lead"})
    finally:
        loader.join()
        bridge.stop()
    assert result["loaded"] and result["removed_old"], result
    assert [b["name"] for b in result["bank"]] == ["Cutoff", "Resonance"]
    assert result["restored"] == 4
    device_file = Path(result["device_file"])
    assert device_file.name == "Fake Bank.amxd"
    assert (device_file.parent / "lbhost.js").is_file()
    load = [r for r in bridge.requests if r["cmd"] == "browser.load"][-1]["args"]
    assert load == {"path": "user_library/LiveBridge Presets/Plug-in Hosts/Fake Bank.amxd",
                    "track": "Lead"}
    new = next(d for d in engine.devices(kind="host") if d["instance"] != old["instance"])
    assert new["bank"] == ["Cutoff"]
    listed = engine.request(new, "params", {"search": "cutoff"})["parameters"][0]
    assert listed["display"] == "1000 Hz" or abs(int(listed["display"].split()[0]) - 1000) < 40
    assert engine.request(new, "params", {"search": "reso"})["parameters"][0]["value"] == 0.8
    # restored values move the bank dial too ("bank <index> <value>" to the patch)
    assert engine.wait_for(lambda: any(json.loads(l)[0] == "bank" for l in
                                       started[0].read_text().splitlines()), 3)

    bad = create_app(BridgeClient(host="127.0.0.1", port=1, timeout=1.0))
    assert call_tool(bad, "live_plugin_host_bank", {"parameters": []})["type"] == "bad_args"
    assert call_tool(bad, "live_plugin_host_bank", {"parameters": ["Nope"], "track": "Lead"})[
        "type"] == "not_found"


# ----------------------------------------------------------------------------- presets

def test_preset_into_the_host(engines, monkeypatch, tmp_path):  # noqa: F811
    from fake_bridge import FakeBridge
    pytest.importorskip("cbor2")
    pytest.importorskip("zstandard")
    from test_serum_presets import _preset, _template
    monkeypatch.setenv("LIVEBRIDGE_HOST_FAKE", "1")
    monkeypatch.setenv("LIVEBRIDGE_LIBRARY", str(tmp_path / "home"))
    _process, send, outlets = engines(script=HOST_JS)
    send("context", "track", "Lead")
    assert engine.wait_for(lambda: any(d.get("track") == "Lead"
                                       for d in engine.devices(kind="host")), 5)
    (host,) = engine.devices(kind="host")
    engine.request(host, "load", {"file": "Serum2", "sub": "Serum 2", "name": "Serum 2"})

    library = tmp_path / "User Library"
    library.mkdir()
    bridge = FakeBridge().start()
    bridge.set_result("samples.locations", {"user_library": [{"path": str(library),
                                                               "exists": True}]})
    app = create_app(BridgeClient(host=bridge.host, port=bridge.port, timeout=5.0))
    try:
        direct = tmp_path / "Pad.vstpreset"
        direct.write_bytes(b"VST3")
        loaded = call_tool(app, "live_plugin_host_preset", {"source_path": str(direct)})
        assert loaded["changed"] == 4 and loaded["notes"] == []
        again = call_tool(app, "live_plugin_host_preset", {"source_path": str(direct)})
        assert again["changed"] == 0 and "No sampled parameter changed" in again["notes"][0]

        source = _preset(tmp_path / "Glass Comet.SerumPreset")
        missing = call_tool(app, "live_plugin_host_preset", {"source_path": str(source)})
        assert missing["type"] == "invalid_state" and "template" in missing["error"]
        _template(library / "Serum 2 Init.vstpreset")
        packed = call_tool(app, "live_plugin_host_preset", {"source_path": str(source)})
        assert packed["preset_name"] == "Glass Comet"
        assert packed["path"].endswith("LiveBridge Presets/Serum 2/Glass Comet.vstpreset")
        assert Path(packed["path"]).is_file()

        assert call_tool(app, "live_plugin_host_preset", {
            "source_path": str(tmp_path / "none.vstpreset")})["type"] == "not_found"
    finally:
        bridge.stop()
    sent = [json.loads(line) for line in outlets.read_text().splitlines()]
    assert not any(m[0] == "bank" for m in sent)        # no bank on this host


def test_load_asks_the_browser_for_a_new_midi_track(tmp_path, monkeypatch):
    from fake_bridge import FakeBridge
    monkeypatch.setenv("LIVEBRIDGE_ENGINE_DIR", str(tmp_path / "none"))
    bridge = FakeBridge().start()
    bridge.set_error("browser.load", "not_found", "stop here")
    app = create_app(BridgeClient(host=bridge.host, port=bridge.port, timeout=5.0))
    try:
        call_tool(app, "live_plugin_host_load", {"plugin": "Diva", "file": "Diva"})
    finally:
        bridge.stop()
    load = [r for r in bridge.requests if r["cmd"] == "browser.load"][-1]["args"]
    assert load == {"query": "LiveBridge Host Instrument", "category": "instrument",
                    "new_track": "midi", "track_name": "Diva"}


def test_every_device_holds_messages_until_node_is_ready(engines):  # noqa: F811
    for path in (m4l_build.ENGINE_AMXD, m4l_build.MIDI_AMXD, m4l_build.HOST_EFFECT_AMXD,
                 m4l_build.HOST_INSTRUMENT_AMXD):
        _type, patcher = m4l_build.read_amxd(path.read_bytes())
        boxes = {b["box"]["id"]: b["box"] for b in patcher["patcher"]["boxes"]}
        lines = [(tuple(l["patchline"]["source"]), tuple(l["patchline"]["destination"]))
                 for l in patcher["patcher"]["lines"]]
        into_node = [src for src, dst in lines if dst == ("obj-node", 0)]
        assert into_node == [("obj-gate", 0)], (path.name, into_node)
        assert boxes["obj-gate"]["text"] == "gate 1 0"              # closed at load
        route = boxes["obj-route"]["text"].split()[1:]
        ready = route.index("ready")
        assert ready == len(route) - 1
        assert (("obj-route", ready), ("obj-ready", 0)) in lines
        assert (("obj-ready", 0), ("obj-gate", 0)) in lines
        # what route rejects still reaches the print box (its outlet moved one along)
        assert (("obj-route", ready + 1), ("obj-print", 0)) in lines, path.name
    # each script says "ready" once Node for Max runs it
    for script in (m4l_build.HERE / "LiveBridge Engine" / "engine.js",
                   m4l_build.HERE / "LiveBridge MIDI" / "lbmidi.js", HOST_JS):
        _process, _send, outlets = engines(script=script)
        assert engine.wait_for(lambda: '["ready"]' in outlets.read_text().splitlines(), 5)


def test_a_host_reopened_with_its_plugin_adopts_it(engines, monkeypatch):  # noqa: F811
    # vst~ restored the plug-in from the Set before the script ran: no load, no reset
    monkeypatch.setenv("LIVEBRIDGE_HOST_FAKE", "1")
    monkeypatch.setenv("LIVEBRIDGE_HOST_FAKE_PRELOAD", "FakeSynth")
    _process, send, outlets = engines(script=HOST_JS)
    send("context", "track", "Pad")
    # it asks for the device's name (load() named it after the plug-in); answer like
    # context.js does
    assert engine.wait_for(lambda: ["context", "devicename"] in
                           [json.loads(l) for l in outlets.read_text().splitlines()], 5)
    send("devicename", "Serum", "2")
    assert engine.wait_for(lambda: any(d.get("plugin") for d in engine.devices(kind="host")), 5)
    (host,) = engine.devices(kind="host")
    assert host["plugin"]["name"] == "Serum 2" and host["plugin"]["restored"] is True
    listed = engine.request(host, "params", {"search": "cutoff"})
    assert listed["total"] == 1 and listed["parameters"][0]["value"] == 0.5   # state kept
    assert ["status", "Serum 2"] in [json.loads(l) for l in outlets.read_text().splitlines()]


def test_unnamed_host_takes_a_serum_preset_name(engines, monkeypatch):  # noqa: F811
    monkeypatch.setenv("LIVEBRIDGE_HOST_FAKE", "1")
    monkeypatch.setenv("LIVEBRIDGE_HOST_FAKE_PRELOAD", "FakeSynth")
    _process, send, outlets = engines(script=HOST_JS)
    (host,) = engine.devices(kind="host")
    engine.request(host, "params", {})                 # adopted, no name came back
    assert engine.devices(kind="host")[0]["plugin"]["name"] == "plug-in"
    engine.request(host, "preset", {"path": "x.vstpreset", "plugin_name": "Serum 2"})
    assert engine.devices(kind="host")[0]["plugin"]["name"] == "Serum 2"
    assert ["context", "rename", "Serum 2"] in [json.loads(l) for l in
                                                outlets.read_text().splitlines()]


def test_requests_adopt_before_the_startup_check(engines, monkeypatch):  # noqa: F811
    monkeypatch.setenv("LIVEBRIDGE_HOST_FAKE", "1")
    monkeypatch.setenv("LIVEBRIDGE_HOST_FAKE_PRELOAD", "FakeSynth")
    _process, send, _outlets = engines(script=HOST_JS)
    (host,) = engine.devices(kind="host")
    assert engine.request(host, "params", {})["total"] == 4               # no "no plug-in"


# ----------------------------------------------------------------------------- scout, save, hand over

def _fake_host(engines, monkeypatch, name="Fake"):  # noqa: F811
    monkeypatch.setenv("LIVEBRIDGE_HOST_FAKE", "1")
    _process, send, outlets = engines(script=HOST_JS)
    send("context", "track", "Keys")
    assert engine.wait_for(lambda: any(d.get("track") == "Keys"
                                       for d in engine.devices(kind="host")), 5)
    (host,) = engine.devices(kind="host")
    engine.request(host, "load", {"file": "FakeSynth", "sub": name, "name": name})
    return host


def test_snapshot_shows_what_the_user_moved(engines, monkeypatch):  # noqa: F811
    host = _fake_host(engines, monkeypatch)
    app = create_app(BridgeClient(host="127.0.0.1", port=1, timeout=1.0))
    taken = call_tool(app, "live_plugin_host_parameters", {"snapshot": "before"})
    assert taken == {"snapshot": "before", "parameters": 4}
    engine.request(host, "set", {"values": {"Cutoff": 0.9, "Mode": "HP"}})   # "the user"
    moved = call_tool(app, "live_plugin_host_parameters", {"changed_since": "before"})
    assert moved["changed"] == 2
    assert [p["name"] for p in moved["parameters"]] == ["Mode", "Cutoff"] or \
        [p["name"] for p in moved["parameters"]] == ["Cutoff", "Mode"]
    cutoff = next(p for p in moved["parameters"] if p["name"] == "Cutoff")
    assert cutoff["before"] != cutoff["after"] and cutoff["id"] == "h1"
    assert call_tool(app, "live_plugin_host_parameters", {"changed_since": "nope"})[
        "type"] == "not_found"
    assert call_tool(app, "live_plugin_host_parameters", {"snapshot": "a",
                                                          "changed_since": "a"})["type"] == \
        "bad_args"


def test_save_and_hand_over_to_a_native_rack(engines, monkeypatch, tmp_path):  # noqa: F811
    from fake_bridge import FakeBridge
    from livebridge_mcp import plugin_ids
    monkeypatch.setenv("LIVEBRIDGE_LIBRARY", str(tmp_path / "home"))
    host = _fake_host(engines, monkeypatch)
    library = tmp_path / "User Library"
    library.mkdir()
    bridge = FakeBridge().start()
    bridge.set_result("samples.locations", {"user_library": [{"path": str(library),
                                                               "exists": True}]})
    app = create_app(BridgeClient(host=bridge.host, port=bridge.port, timeout=5.0))
    try:
        saved = call_tool(app, "live_plugin_host_save", {"name": "Warm Keys"})
        path = Path(saved["path"])
        assert path == library / "LiveBridge Presets" / "Plug-in Presets" / "Fake" / \
            "Warm Keys.vstpreset" and path.is_file()
        assert saved["class_id"] == "FA" * 16 and plugin_ids.lookup("FA" * 16)["name"] == "Fake"
        again = call_tool(app, "live_plugin_host_save", {"name": "Warm Keys"})
        assert Path(again["path"]).name == "Warm Keys 2.vstpreset"     # nothing overwritten

        bridge.set_result("plugin_racks.map", {"status": "done", "count": 3,
                                               "source": "probed"})
        bridge.set_result("plugin_racks.expose", {"status": "done", "track": "Keys",
                                                  "device": "song.tracks[0].devices[1]"})
        bridge.set_result("devices.list", {"devices": [
            {"name": "Fake", "path": "song.tracks[0].devices[0]",
             "class_name": "MxDeviceInstrument"},
            {"name": "Fake Rack", "path": "song.tracks[0].devices[1]",
             "class_name": "InstrumentGroupDevice"}]})
        bridge.set_result("devices.delete", {"deleted": True})
        handed = call_tool(app, "live_plugin_host_expose", {"parameters": ["Cutoff", "Mode"],
                                                            "name": "Handover"})
        assert handed["host_removed"] is True and handed["map"]["count"] == 3
        expose = [r for r in bridge.requests if r["cmd"] == "plugin_racks.expose"][-1]["args"]
        assert expose["parameters"] == ["Cutoff", "Mode"] and expose["plugin"] == "Fake"
        assert expose["preset_file"].endswith("Handover.vstpreset") and expose["track"] == "Keys"
        delete = [r for r in bridge.requests if r["cmd"] == "devices.delete"][-1]["args"]
        assert delete["device"] == "song.tracks[0].devices[0]"            # the host, not the rack

        bridge.set_result("plugin_racks.map", {"status": "done", "count": 0, "source": "probed"})
        unmapped = call_tool(app, "live_plugin_host_expose", {"parameters": ["Cutoff"]})
        assert "could not be mapped" in unmapped["notes"][0] and "exposed" not in unmapped
        assert call_tool(app, "live_plugin_host_expose", {"parameters": []})["type"] == \
            "bad_args"
    finally:
        bridge.stop()
    assert host
