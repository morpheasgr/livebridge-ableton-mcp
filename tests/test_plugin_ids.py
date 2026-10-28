"""Which plug-in a .vstpreset belongs to (plugin_ids) and live_plugin_host_preset refusing a
preset meant for another plug-in."""

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "mcp_server"))

from livebridge_mcp import engine, plugin_ids  # noqa: E402
from livebridge_mcp.client import BridgeClient  # noqa: E402
from livebridge_mcp.server import create_app  # noqa: E402

from test_consistency import call_tool  # noqa: E402
from test_m4l_engine import engines  # noqa: E402,F401  (fixture)

HOST_JS = REPO / "max_for_live" / "LiveBridge Host" / "lbhost.js"
DIVA = "D39D5B69D6AF42FA1234567890ABCDEF"
SERUM = "56535458667350736572756D20320000"


def _bundle(folder, file, classes, trailing_comma=True):
    resources = folder / f"{file}.vst3" / "Contents" / "Resources"
    resources.mkdir(parents=True)
    body = json.dumps({"Name": file, "Classes": [
        {"CID": cid, "Category": category, "Name": name, "Vendor": "Test"}
        for cid, name, category in classes]}, indent=1)
    if trailing_comma:                                   # moduleinfo.json allows them
        body = body.replace('"Test"\n  }', '"Test",\n  }')
    (resources / "moduleinfo.json").write_text(body, encoding="utf-8")


def _vstpreset(path, cid):
    path.write_bytes(b"VST3" + b"\x01\x00\x00\x00" + cid.encode() + b"\x00" * 16)
    return path


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVEBRIDGE_LIBRARY", str(tmp_path / "home"))
    monkeypatch.setenv("LIVEBRIDGE_VST3_PATH", str(tmp_path / "vst3"))
    folder = tmp_path / "vst3"
    _bundle(folder, "Diva(x64)", [(DIVA, "Diva", "Audio Module Class"),
                                  ("AA" * 16, "Diva Controller", "Component Controller Class")])
    return folder


def test_class_ids_names_and_learning(tmp_path, library):
    assert plugin_ids.class_id_of(_vstpreset(tmp_path / "a.vstpreset", DIVA.lower())) == DIVA
    assert plugin_ids.class_id_of(tmp_path / "missing.vstpreset") is None
    (tmp_path / "b.fxp").write_bytes(b"CcnK" + b"\x00" * 60)
    assert plugin_ids.class_id_of(tmp_path / "b.fxp") is None
    found = plugin_ids.scan([library])
    assert set(found) == {DIVA} and found[DIVA]["file"] == "Diva(x64)"   # controller left out
    assert plugin_ids.lookup(DIVA, [library])["source"] == "moduleinfo"
    assert plugin_ids.class_for("Diva(x64)", "Diva", [library]) == DIVA
    assert plugin_ids.lookup(SERUM, [library]) is None
    plugin_ids.learn(SERUM, "Serum 2", "Serum2")
    assert plugin_ids.lookup(SERUM)["name"] == "Serum 2"
    plugin_ids.learn(SERUM, "Something Else")               # a known ID keeps its name
    assert plugin_ids.lookup(SERUM)["name"] == "Serum 2"
    assert plugin_ids.class_for(None, "Serum2", []) == SERUM
    assert plugin_ids.same_plugin("Serum 2", "serum2") and not plugin_ids.same_plugin("a", "")


def test_host_refuses_a_preset_for_another_plugin(engines, monkeypatch, tmp_path,  # noqa: F811
                                                  library):
    from fake_bridge import FakeBridge
    monkeypatch.setenv("LIVEBRIDGE_HOST_FAKE", "1")
    _process, send, _outlets = engines(script=HOST_JS)
    send("context", "track", "Lead")
    assert engine.wait_for(lambda: any(d.get("track") == "Lead"
                                       for d in engine.devices(kind="host")), 5)
    (host,) = engine.devices(kind="host")
    engine.request(host, "load", {"file": "Serum2", "sub": "Serum 2", "name": "Serum 2"})
    plugin_ids.learn(SERUM, "Serum 2", "Serum2")
    bridge = FakeBridge().start()
    app = create_app(BridgeClient(host=bridge.host, port=bridge.port, timeout=5.0))
    try:
        wrong = call_tool(app, "live_plugin_host_preset", {
            "source_path": str(_vstpreset(tmp_path / "Diva Bass.vstpreset", DIVA))})
        assert wrong["type"] == "bad_args" and "for Diva" in wrong["error"]
        assert "holds Serum 2" in wrong["error"]
        right = call_tool(app, "live_plugin_host_preset", {
            "source_path": str(_vstpreset(tmp_path / "Pad.vstpreset", SERUM))})
        assert right["changed"] == 4 and right["preset_plugin"] == "Serum 2"
        assert engine.devices(kind="host")[0]["plugin"]["class_id"] == SERUM
    finally:
        bridge.stop()


def test_unknown_preset_ids_are_learned_from_a_load_that_works(engines, monkeypatch,  # noqa: F811
                                                               tmp_path, library):
    from fake_bridge import FakeBridge
    monkeypatch.setenv("LIVEBRIDGE_HOST_FAKE", "1")
    _process, _send, _outlets = engines(script=HOST_JS)
    (host,) = engine.devices(kind="host")
    engine.request(host, "load", {"file": "Hive", "sub": "Hive", "name": "Hive"})
    bridge = FakeBridge().start()
    app = create_app(BridgeClient(host=bridge.host, port=bridge.port, timeout=5.0))
    hive = "BB" * 16
    try:
        loaded = call_tool(app, "live_plugin_host_preset", {
            "source_path": str(_vstpreset(tmp_path / "Hive Pad.vstpreset", hive))})
        assert loaded["changed"] == 4 and "preset_plugin" not in loaded
    finally:
        bridge.stop()
    assert plugin_ids.lookup(hive)["name"] == "Hive"            # learned for next time
