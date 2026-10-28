"""The LiveBridge Host devices (a VST3 inside Max's vst~, every parameter reachable): the built
.amxd files, lbhost.js under node with a simulated plug-in (LIVEBRIDGE_HOST_FAKE=1) and the
live_plugin_host_* tools."""

import sys
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
    assert listed["parameters"][3]["id"] == "h133"
    assert call_tool(app, "live_plugin_host_parameters", {"search": "res"})["total"] == 1

    changed = call_tool(app, "live_plugin_host_set", {"values": {
        "Cutoff": "1000 Hz", "Mode": "HP", "resonance": 0.25, "h133": "-6 dB",
        "Nope": 1, "Volume ": "999 dB"}})
    results = {r["name"]: r for r in changed["results"]}
    assert results["Cutoff"]["ok"] and abs(int(results["Cutoff"]["display"].split()[0])
                                           - 1000) <= 30
    assert results["Mode"]["display"] == "HP"
    assert results["Resonance"]["display"] == "25 %"
    assert results["Nope"]["ok"] is False
    assert changed["applied"] >= 4
    volume = [r for r in changed["results"] if r.get("id") == "h133"]
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
