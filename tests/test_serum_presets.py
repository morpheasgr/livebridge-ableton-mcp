"""Serum 2 preset files (``livebridge_mcp.serum``, the ``live_preset_*`` tools) on synthetic
presets — no copyrighted preset fixtures — and loading through a fake Live."""

import json
import struct
import sys
from pathlib import Path

import pytest

pytest.importorskip("cbor2")
pytest.importorskip("zstandard")

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "mcp_server"))

from fake_bridge import FakeBridge  # noqa: E402
from livebridge_mcp import serum  # noqa: E402
from livebridge_mcp.client import BridgeClient  # noqa: E402
from livebridge_mcp.server import create_app  # noqa: E402

from test_consistency import call_tool  # noqa: E402

CLASS_ID = b"1234567890ABCDEF1234567890ABCDEF"


def _data():
    return {
        "Oscillator0": {"WTOsc0": {"table": "Basic Shapes", "plainParams": {"kParamLevel": 0.8}},
                        "SampleOsc0": {"embedded_audio": {"data": [[0.1] * 200, [0.2] * 200]}}},
        "Env0": {"plainParams": {"kParamAttack": 0.01, "kParamDecay": 0.3}},
        "Filter0": {"plainParams": {"kParamCutoff": 0.5, "kParamType": "MG Low 12"}},
        "FXRack0": {"FX": [{"FXDistortion": {"plainParams": {"kParamDrive": 0.4}}}]},
        "Weird/Key~": 7,
    }


def _preset(path, data=None, name="Glass Comet", version="2.0.16"):
    document = {"metadata": {"fileType": "SerumPreset", "product": "Serum2",
                             "productVersion": version, "version": 6, "presetName": name,
                             "presetAuthor": "LB", "presetDescription": "test", "hash": "x"},
                "data": data if data is not None else _data()}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(serum.codec.encode(document, "preset"))
    return path


def _template(path, version="2.0.16"):
    def chunk(component):
        meta = {"product": "Serum2", "component": component, "productVersion": version,
                "version": 6}
        if component == "controller":
            meta.update(presetName="", presetAuthor="", presetDescription="")
        data = {key: None for key in _data()}
        data["component"] = component
        return serum.pack.pack_serum_state(meta, data)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(serum.vst3.write_vst3_preset(CLASS_ID, {
        b"Comp": chunk("processor"), b"Cont": chunk("controller"), b"Info": b"INFO"}))
    return path


# ----------------------------------------------------------------------------- the format

def test_codec_round_trip_and_rejections(tmp_path):
    path = _preset(tmp_path / "a.SerumPreset")
    loaded, document, sha, kind = serum.codec.load(str(path))
    assert kind == "preset" and document["data"] == _data() and len(sha) == 64
    as_json = serum.codec.encode(document, "json")
    again, kind = serum.codec.decode(as_json)
    assert kind == "json" and again == document
    raw = path.read_bytes()
    for broken in (raw[:20], serum.codec.MAGIC + b"\xff" * 30, b'{"metadata": {}}'):
        with pytest.raises(ValueError):
            serum.codec.decode(broken)
    legacy = dict(document, metadata=dict(document["metadata"], product="Serum"))
    with pytest.raises(ValueError, match="Serum2"):
        serum.codec.encode(legacy, "preset")


def test_tree_read_search_patch_compare(tmp_path):
    _path, document, _sha, _kind = serum.codec.load(str(_preset(tmp_path / "a.SerumPreset")))
    page = serum.tree.read(document, "/data", 0, 2, 1)
    assert page["total"] == 5 and len(page["entries"]) == 2 and page["next_offset"] == 2
    escaped = serum.tree.read(document, "/data/Weird~1Key~0")
    assert escaped["value"] == 7
    found = serum.tree.search(document, "cutoff")
    assert [m["path"] for m in found["matches"]] == ["/data/Filter0/plainParams/kParamCutoff"]
    # long number arrays (the embedded audio) are skipped unless asked for
    assert [a["path"] for a in found["skipped_large_arrays"]] == [
        "/data/Oscillator0/SampleOsc0/embedded_audio/data/0",
        "/data/Oscillator0/SampleOsc0/embedded_audio/data/1"]
    edited, changes = serum.tree.patch(document, [
        {"op": "test", "path": "/data/Env0/plainParams/kParamDecay", "value": 0.3},
        {"op": "replace", "path": "/data/Env0/plainParams/kParamDecay", "value": 0.6},
        {"op": "add", "path": "/data/FXRack0/FX/-", "value": {"FXReverb": {}}},
        {"op": "copy", "from": "/data/Env0", "path": "/data/Env1"}])
    assert [c["op"] for c in changes] == ["replace", "add", "copy"]
    assert edited["data"]["Oscillator0"] == document["data"]["Oscillator0"]   # assets kept
    diff = serum.tree.compare(document, edited)
    assert not diff["equal"] and {c["path"] for c in diff["changes"]} == {
        "/data/Env0/plainParams/kParamDecay", "/data/FXRack0/FX", "/data/Env1"}
    with pytest.raises(ValueError, match="Test failed"):
        serum.tree.patch(document, [{"op": "test", "path": "/data/Env0/plainParams/"
                                     "kParamDecay", "value": 0.9}])
    with pytest.raises(ValueError):
        serum.tree.patch(document, [{"op": "replace", "path": "/", "value": 1}])


def test_pack_into_a_vstpreset(tmp_path):
    source = _preset(tmp_path / "Glass Comet.SerumPreset")
    template = _template(tmp_path / "lib" / "Mine.vstpreset")
    payload, report = serum.pack.pack_serum2(source, template)
    assert report["version"] == "2.0.16" and report["preset_name"] == "Glass Comet"
    class_id, chunks = serum.vst3.read_vst3_preset(payload)
    assert class_id == CLASS_ID and chunks[b"Info"] == b"INFO"
    meta, data = serum.pack.unpack_serum_state(chunks[b"Comp"])
    assert meta["component"] == "processor" and data["Filter0"] == _data()["Filter0"]
    controller_meta, _ = serum.pack.unpack_serum_state(chunks[b"Cont"])
    assert controller_meta["presetName"] == "Glass Comet"
    other = _template(tmp_path / "lib" / "Old.vstpreset", version="2.0.14")
    with pytest.raises(ValueError, match="no cross-build"):
        serum.pack.pack_serum2(source, other)


def test_templates_are_found_in_the_user_library(tmp_path):
    library = tmp_path / "User Library"
    _template(library / "Presets" / "Plug-Ins" / "Serum 2" / "Init.vstpreset")
    _template(library.joinpath(*serum.PUBLISHED) / "Generated.vstpreset")
    (library / "Not Serum.vstpreset").write_bytes(b"VST3 nope")
    found = serum.find_templates([library])
    assert [Path(t["path"]).name for t in found] == ["Init.vstpreset", "Generated.vstpreset"]
    assert found[0]["product_version"] == "2.0.16"
    # the same User Library listed twice (two spellings of one folder): each template once
    twice = serum.find_templates([library, tmp_path / "." / "User Library"])
    assert len(twice) == 2


# ----------------------------------------------------------------------------- the tools

@pytest.fixture
def fake(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVEBRIDGE_LIBRARY", str(tmp_path / "home"))
    monkeypatch.setenv("LIVEBRIDGE_SERUM_PRESETS", str(tmp_path / "presets"))
    library = tmp_path / "User Library"
    library.mkdir()
    bridge = FakeBridge().start()
    bridge.set_result("samples.locations", {"user_library": [{"path": str(library),
                                                               "exists": True}]})
    bridge.set_result("system.hello", {"version": "1.0.0"})
    client = BridgeClient(host=bridge.host, port=bridge.port, timeout=5.0)
    app = create_app(client)
    try:
        yield app, bridge, library
    finally:
        client.close()
        bridge.stop()


def test_find_inspect_read_search(fake, tmp_path):
    app, _bridge, library = fake
    _preset(tmp_path / "presets" / "Bass" / "Dark Reese.SerumPreset")
    _preset(tmp_path / "presets" / "Leads" / "Bright Pluck.SerumPreset")
    listing = call_tool(app, "live_preset_find", {"query": "reese"})
    assert [p["name"] for p in listing["presets"]] == ["Dark Reese"]
    assert listing["templates"] == [] and "Save Preset" in listing["hint"]
    _template(library / "Serum 2 Init.vstpreset")
    assert call_tool(app, "live_preset_find", {})["templates"][0]["product_version"] == \
        "2.0.16"
    path = listing["presets"][0]["path"]
    info = call_tool(app, "live_preset_inspect", {"file_path": path})
    assert info["module_count"] == 5 and info["modules"][0]["path"] == "/data/Oscillator0"
    read = call_tool(app, "live_preset_read", {"file_path": path,
                                                "path": "/data/Filter0/plainParams"})
    assert {e["path"]: e["value"] for e in read["entries"]}[
        "/data/Filter0/plainParams/kParamType"] == "MG Low 12"
    hits = call_tool(app, "live_preset_search", {"file_path": path, "query": "drive"})
    assert hits["matches"][0]["path"].endswith("kParamDrive")
    missing = call_tool(app, "live_preset_inspect", {"file_path": "nope.SerumPreset"})
    assert missing["type"] == "not_found"


def test_edit_convert_compare(fake, tmp_path):
    app, _bridge, _library = fake
    source = str(_preset(tmp_path / "presets" / "Dark Reese.SerumPreset"))
    sha = call_tool(app, "live_preset_inspect", {"file_path": source})["source_sha256"]
    ops = [{"op": "replace", "path": "/data/Filter0/plainParams/kParamCutoff", "value": 0.2}]
    dry = call_tool(app, "live_preset_edit", {"file_path": source, "expected_sha256": sha,
                                              "operations": ops})
    assert dry["dry_run"] and dry["change_count"] == 1 and "output_path" not in dry
    stale = call_tool(app, "live_preset_edit", {"file_path": source, "expected_sha256": "0",
                                                "operations": ops})
    assert stale["type"] == "invalid_state"
    written = call_tool(app, "live_preset_edit", {
        "file_path": source, "expected_sha256": sha, "operations": ops, "dry_run": False,
        "output_path": "Darker Reese.SerumPreset"})
    out = Path(written["output_path"])
    assert out.parent == tmp_path / "home" / "serum" and out.is_file()
    again = call_tool(app, "live_preset_edit", {
        "file_path": source, "expected_sha256": sha, "operations": ops, "dry_run": False,
        "output_path": "Darker Reese.SerumPreset"})
    assert again["type"] == "bad_args" and "exists" in again["error"]
    diff = call_tool(app, "live_preset_compare", {"left_path": source,
                                                   "right_path": "Darker Reese.SerumPreset"})
    assert [c["path"] for c in diff["changes"]] == ["/data/Filter0/plainParams/kParamCutoff"]
    unpacked = call_tool(app, "live_preset_convert", {
        "file_path": source, "output_path": "reese.json", "output_format": "json",
        "expected_sha256": sha})
    assert json.loads(Path(unpacked["output_path"]).read_text())["data"]["Env0"]
    # a whole module from another preset, without sending it through chat
    donor = str(_preset(tmp_path / "presets" / "Pad.SerumPreset", data=dict(
        _data(), Oscillator0={"WTOsc0": {"table": "Spectral Pad"}})))
    donor_sha = call_tool(app, "live_preset_inspect", {"file_path": donor})["source_sha256"]
    copied = call_tool(app, "live_preset_edit", {
        "file_path": source, "expected_sha256": sha, "dry_run": False,
        "output_path": "Reese Pad.SerumPreset", "operations": [
            {"op": "copy", "from": "/data/Oscillator0", "path": "/data/Oscillator0",
             "source_file_path": donor, "source_sha256": donor_sha}]})
    _p, document, _s, _k = serum.codec.load(copied["output_path"])
    assert document["data"]["Oscillator0"] == {"WTOsc0": {"table": "Spectral Pad"}}


def test_load_packs_publishes_and_loads(fake, tmp_path):
    app, bridge, library = fake
    source = str(_preset(tmp_path / "presets" / "Dark Reese.SerumPreset", name="Dark Reese"))
    refused = call_tool(app, "live_preset_load", {"source_path": source})
    assert refused["type"] == "not_found" and "Save Preset" in refused["error"]
    _template(library / "Old.vstpreset", version="2.0.14")
    wrong = call_tool(app, "live_preset_load", {"source_path": source})
    assert "found: 2.0.14" in wrong["error"]
    _template(library / "Mine.vstpreset")
    bridge.set_result("browser.load", {"loaded": {"name": "Dark Reese"},
                                       "track": {"name": "Dark Reese", "path": "song.tracks[3]"},
                                       "inserted": [{"name": "Serum 2",
                                                     "path": "song.tracks[3].devices[0]"}]})
    loaded = call_tool(app, "live_preset_load", {"source_path": source})
    assert loaded["loaded"] is True and loaded["device"]["name"] == "Serum 2"
    published = library / "LiveBridge Presets" / "Serum 2" / "Dark Reese.vstpreset"
    assert Path(loaded["output_path"]) == published and published.is_file()
    request = [r for r in bridge.requests if r["cmd"] == "browser.load"][-1]["args"]
    assert request == {"path": "user_library/LiveBridge Presets/Serum 2/Dark Reese.vstpreset",
                       "new_track": "midi", "track_name": "Dark Reese"}
    # the same preset again reuses its file; a changed one under that name never overwrites
    again = call_tool(app, "live_preset_load", {"source_path": source})
    assert Path(again["output_path"]) == published
    source = str(_preset(tmp_path / "presets" / "Dark Reese v2.SerumPreset", name="Dark Reese",
                         data=dict(_data(), Env0={"plainParams": {"kParamDecay": 0.9}})))
    bridge.set_result("browser.load", {"loaded": {"name": "x"}, "track": {"name": "Bass"},
                                       "inserted": [], "changed": [{"name": "Serum 2",
                                                                    "path": "p"}],
                                       "removed": [{"name": "Operator"}]})
    replaced = call_tool(app, "live_preset_load", {"source_path": source, "track": "Bass"})
    assert replaced["loaded"] and replaced["replaced"] == ["Operator"]
    assert Path(replaced["output_path"]).name.startswith("Dark Reese-")
    bridge.set_error("browser.load", "not_found", "no such item", once=False)
    lost = call_tool(app, "live_preset_load", {"source_path": source})
    assert lost["loaded"] is False and "LiveBridge Presets" in lost["notes"][-1]
    assert call_tool(app, "live_preset_load", {"source_path": source, "track": 1,
                                               "track_name": "x"})["type"] == "bad_args"


def test_missing_libraries_are_reported(fake, tmp_path, monkeypatch):
    app, _bridge, _library = fake
    source = str(_preset(tmp_path / "presets" / "x.SerumPreset"))

    def missing():
        raise ValueError("Serum preset files need cbor2 and zstandard: pip install x")

    monkeypatch.setattr(serum.codec, "libraries", missing)
    assert call_tool(app, "live_preset_inspect", {"file_path": source})["type"] == \
        "unsupported"
