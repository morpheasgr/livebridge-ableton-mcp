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
        assert any(b["text"] == first for b in boxes.values())
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
        {"name": "LiveBridge Host Instrument", "path": "song.tracks[0].devices[0]"},
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
