"""Surge XT patch writer: round trip, routings, step sequences, tool output (CC0 Init Saw)."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from livebridge_mcp import surge as S

INIT_SAW = Path(__file__).parent / "data" / "surge" / "Init Saw.fxp"


def test_unchanged_patch_is_byte_identical():
    data = INIT_SAW.read_bytes()
    assert S.Patch(data).to_bytes() == data


def test_routings_step_sequence_parameters_and_sizes():
    patch = S.Patch.load(INIT_SAW)
    patch.route("a_filter1_cutoff", "lfo1", 12)
    patch.route("a_osc1_pitch", "keytrack", 12)
    patch.route("a_filter1_cutoff", "lfo1", -5.5)                 # same source: new depth
    patch.route("fx1_type" if patch.params.find("fx1_type") is not None else "a_pan",
                "modwheel", 1.0)
    patch.set_step_sequence(1, [1, -1, 0.5], loop=[1, 8], shuffle=0.25)
    patch.set_parameter("a_filter1_resonance", 0.4)
    patch.set_name("LB Test")
    data = patch.to_bytes()
    again = S.Patch(data)
    got = {(r["target"], r["source"]): r["depth"] for r in again.routings()}
    assert got == {("a_filter1_cutoff", "lfo1"): -5.5, ("a_osc1_pitch", "keytrack"): 12.0,
                   ("a_pan", "modwheel"): 1.0}
    seq = again.step_sequences()[0]
    assert seq["steps"][:4] == [1.0, -1.0, 0.5, 0.0] and seq["loop"] == [1, 8]
    assert seq["shuffle"] == 0.25 and seq["lfo"] == "lfo1"
    assert again.params.find("a_lfo0_shape").get("value") == "7"
    assert again.params.find("a_filter1_resonance").get("value").startswith("0.4")
    assert again.name == "LB Test" and data[28:35] == b"LB Test"
    xml_size = struct.unpack_from("<I", data, 64)[0]
    assert struct.unpack_from(">I", data, 56)[0] == len(data) - 60 == 32 + xml_size


def test_tail_bytes_are_kept():
    data = bytearray(INIT_SAW.read_bytes()) + b"(\xb5/\xfd-nightly-block"
    struct.pack_into(">I", data, 56, len(data) - 60)
    patch = S.Patch(bytes(data))
    patch.route("a_pan", "lfo2", 0.5)
    assert patch.to_bytes().endswith(b"(\xb5/\xfd-nightly-block")


def test_remove_and_refusals():
    patch = S.Patch.load(INIT_SAW)
    patch.route("a_pan", "lfo2", 0.5)
    assert patch.route("a_pan", "lfo2", remove=True)["removed"]
    assert patch.routings() == []
    with pytest.raises(ValueError, match="non-zero"):
        patch.route("a_pan", "lfo2", 0)
    with pytest.raises(ValueError, match="unknown source"):
        patch.route("a_pan", "wobble", 1)
    with pytest.raises(ValueError, match="no Surge parameter"):
        patch.route("a_nothing", "lfo1", 1)
    with pytest.raises(ValueError, match="-1..1"):
        patch.set_step_sequence(1, [2])
    with pytest.raises(ValueError, match="lfo must"):
        patch.set_step_sequence(13, [1])
    with pytest.raises(ValueError, match="not a Surge"):
        S.Patch(b"x" * 100)


def test_write_tool(tmp_path):
    from livebridge_mcp.tools import surge as tool

    tools = {}

    class App:
        def tool(self):
            def wrap(fn):
                tools[fn.__name__] = fn
                return fn
            return wrap

    tool.register(App(), bridge=None)
    out = tmp_path / "Pluck.fxp"
    result = tools["live_surge_write"](
        "Pluck", routings=[{"target": "a_filter1_cutoff", "source": "filtereg", "depth": 24}],
        step_sequences=[{"lfo": 2, "steps": [1, 0, 1, 0]}], template=str(INIT_SAW),
        output_path=str(out))
    assert result["written"] and out.is_file()
    read = tools["live_surge_routings"](str(out))
    assert read["name"] == "Pluck" and read["routings"][0]["source"] == "filtereg"
    assert read["step_sequences"][0]["lfo"] == "lfo2"
    again = tools["live_surge_write"]("Pluck", parameters={"a_pan": 0.1},
                                      template=str(INIT_SAW), output_path=str(out))
    assert "exists" in str(again)
