"""Massive patch writer: FastLZ round trip, routing records, container sizes (synthetic file)."""

from __future__ import annotations

import struct

import pytest

from livebridge_mcp import massive as M


def _record(lo, hi, slots, index):
    body = struct.pack("<IffI", 4, lo, hi, len(slots))
    for amount, source, flags in slots:
        body += struct.pack("<fi", amount, source) + flags
    return body + struct.pack("<i", index)


def _payload():
    head = bytearray(bytes(range(64)) * 4)
    head[96:100], head[132:136], head[200:204] = b"hsin", b"DSIN", b"tsrp"
    return bytes(head) + b"\x00\x0b\x00\x00\x00" + \
        _record(-3.0, 10.0, [(0.65, 0, b"\x00\x01"), (0.0, -1, b"\x00\x00"),
                             (0.0, -1, b"\x00\x01")], 33) + b"\x00\x00\x00\x00\x00" + \
        _record(0.0, 1.0, [(0.0, -1, b"\x00\x00"), (0.0, -1, b"\x00\x00")], 35) + \
        b"\x00" * 4 + _record(0.0, 10.583, [(0.09375, 2, b"\x00\x00"),
                                            (0.0, -1, b"\x00\x00")], 8) + bytes(40) + \
        b"".join(struct.pack("<6i", 0, 2, 0, 0, 0, 1) for _ in range(4)) + bytes(300)


def _preset(payload):
    stream = M.fastlz_compress(payload)
    data = struct.pack("<I", 1) + b"\x01" + struct.pack("<II", len(payload), len(stream)) + \
        stream
    chunk = b"DSINs\x00\x00\x00" + data
    chunk = struct.pack("<Q", len(chunk) + 8) + chunk
    inner = b"\x01\x00\x00\x00hsin\x01\x00\x00\x00\x00\x00\x00\x00" + bytes(16) + chunk
    inner = struct.pack("<Q", len(inner) + 8) + inner
    comp = b"\x01\x00\x00\x00hsin\x01\x00\x00\x00\x00\x00\x00\x00" + bytes(16) + inner
    comp = struct.pack("<Q", len(comp) + 8) + comp + b"tail"
    return M._build_vstpreset(M.CLASS_ID, {"Comp": comp, "Cont": b"", "Info": b"<x/>"})


def test_fastlz_round_trip():
    for data in (b"", b"a", bytes(5000), bytes(range(256)) * 40, _payload()):
        assert M.fastlz_decompress(M.fastlz_compress(data), len(data)) == data if data else True


def test_reads_routings_and_targets():
    patch = M.Patch(_preset(_payload()))
    assert patch.routings() == [
        {"target": "OSC1-PITCH", "source": "env3", "amount": 0.0938, "slot": 1,
         "semitones": 12.0},
        {"target": "FILTER1-CUT/PRM.1", "source": "env1", "amount": 0.65, "slot": 1}]
    assert "FILTER1-RES/PRM.3" in patch.targets()


def test_route_add_change_remove_and_repack():
    patch = M.Patch(_preset(_payload()))
    assert patch.route("FILTER1-RES/PRM.3", "env2", 0.5)["slot"] == 1
    assert patch.route("filter1-cut/prm.1", "env1", 1.0)["before"] == 0.65
    assert patch.route("FILTER1-CUT/PRM.1", "lfo5", -0.25)["slot"] == 2
    assert patch.route("OSC1-PITCH", "env3", remove=True)["removed"]
    assert patch.route("OSC1-PITCH", "macro1", semitones=-24)["amount"] == -0.1875
    again = M.Patch(patch.to_bytes())
    assert again.payload == patch.payload
    comp = again.chunks["Comp"]
    assert struct.unpack_from("<Q", comp, 0)[0] == len(comp) - 4      # sizes follow the stream
    assert struct.unpack_from("<Q", comp, 40)[0] == len(comp) - 44
    got = {(r["target"], r["source"]): r["amount"] for r in again.routings()}
    assert got == {("OSC1-PITCH", "macro1"): -0.1875, ("FILTER1-CUT/PRM.1", "env1"): 1.0,
                   ("FILTER1-CUT/PRM.1", "slot5"): -0.25, ("FILTER1-RES/PRM.3", "env2"): 0.5}
    # the flag bytes of an existing slot are kept
    offset = again.records[33][0][0]
    assert again.payload[offset + 8:offset + 10] == b"\x00\x01"


def test_full_knob_and_bad_input_are_refused():
    patch = M.Patch(_preset(_payload()))
    patch.route("FILTER1-RES/PRM.3", "env2", 0.5)
    patch.route("FILTER1-RES/PRM.3", "env3", 0.5)
    with pytest.raises(ValueError, match="no free slot"):
        patch.route("FILTER1-RES/PRM.3", "env4", 0.5)
    with pytest.raises(ValueError, match="unknown source"):
        patch.route("FILTER1-RES/PRM.3", "lfo2", 0.5)
    with pytest.raises(ValueError, match="-1..1"):
        patch.route("FILTER1-CUT/PRM.1", "env2", 1.5)
    with pytest.raises(ValueError, match="cannot be modulated"):
        patch.route("FILTER2-CUT/PRM.1", "env2", 0.5)
    with pytest.raises(ValueError, match="semitones only"):
        patch.route("FILTER1-CUT/PRM.1", "env2", semitones=12)


def test_not_massive_is_refused():
    data = M._build_vstpreset("0" * 32, {"Comp": b"x"})
    with pytest.raises(ValueError, match="not Massive"):
        M.Patch(data)


def test_write_tool_writes_next_to_the_template(tmp_path):
    from livebridge_mcp.tools import massive as tool

    tools = {}

    class App:
        def tool(self):
            def wrap(fn):
                tools[fn.__name__] = fn
                return fn
            return wrap

    tool.register(App(), bridge=None)
    template = tmp_path / "Massive Init (LB routed).vstpreset"
    template.write_bytes(_preset(_payload()))
    result = tools["live_massive_write"](
        [{"target": "FILTER1-RES/PRM.3", "source": "env2", "amount": 0.5}],
        name="Wobble", template=str(template))
    assert result["written"] and result["output_path"] == str(tmp_path / "Wobble.vstpreset")
    read = tools["live_massive_routings"](result["output_path"])
    assert {"target": "FILTER1-RES/PRM.3", "source": "env2", "amount": 0.5,
            "slot": 1} in read["routings"]
    again = tools["live_massive_write"](
        [{"target": "FILTER1-RES/PRM.3", "source": "env2", "amount": 0.5}],
        name="Wobble", template=str(template))
    assert "exists" in again["error"]["message"] if isinstance(again.get("error"), dict) \
        else "exists" in str(again)
    same = tools["live_massive_write"]([{"target": "OSC1-PITCH", "source": "env3",
                                         "remove": True}],
                                       output_path=str(template), template=str(template))
    assert "replace the template" in str(same)


def test_lfo_curves_and_stepper():
    patch = M.Patch(_preset(_payload()))
    size, field = len(patch.payload), struct.unpack_from("<Q", patch.payload, 84)[0]
    assert patch.slots()[5] == {"type": "lfo", "curve_a": "sine", "curve_b": "saw"}
    assert patch.set_slot(6, curve_a="triangle")["curve_a_before"] == "sine"
    patch.set_slot(7, curve_b="Square")
    change = patch.set_slot(5, type="stepper", steps=[1, 0, 0.5])
    assert change["type_before"] == "lfo" and len(patch.payload) == size + 120
    assert struct.unpack_from("<Q", patch.payload, 84)[0] == field + 120
    again = M.Patch(patch.to_bytes())
    slots = again.slots()
    assert slots[5]["type"] == "stepper" and slots[5]["steps"][:4] == [1.0, 0.0, 0.5, 0.0]
    assert (slots[6]["curve_a"], slots[7]["curve_b"], slots[8]["curve_a"]) == \
        ("triangle", "square", "sine")
    assert again.routings() == patch.routings()                  # records found again
    with pytest.raises(ValueError, match="curves are for LFO"):
        again.set_slot(5, curve_a="sine")
    with pytest.raises(ValueError, match="steps need"):
        again.set_slot(6, steps=[1])
    with pytest.raises(ValueError, match="0..1"):
        again.set_slot(5, steps=[2])
    again.set_slot(5, type="lfo")
    assert len(again.payload) == size and again.slots()[5]["curve_a"] == "sine"
    with pytest.raises(ValueError, match="curve_a must be"):
        patch.set_slot(6, curve_a="noise")
    with pytest.raises(ValueError, match="5..8"):
        patch.set_slot(4, curve_a="sine")
    with pytest.raises(ValueError, match="performer"):
        patch.set_slot(6, type="performer")


def test_the_sidechain_slot_is_never_filled():
    patch = M.Patch(_preset(_payload()))
    patch.route("FILTER1-CUT/PRM.1", "env2", 0.3)          # slot 2: the last normal one
    with pytest.raises(ValueError, match="no free slot"):
        patch.route("FILTER1-CUT/PRM.1", "env3", 0.3)
    offset = patch.records[33][2][0]
    struct.pack_into("<fi", patch.payload, offset, 0.5, 13)  # macro 1 on SC, as in Massive
    assert {"target": "FILTER1-CUT/PRM.1", "source": "macro1", "amount": 0.5,
            "slot": "sc"} in patch.routings()
    with pytest.raises(ValueError, match="sidechain"):
        patch.route("FILTER1-CUT/PRM.1", "macro1", 0.2)
    assert patch.route("FILTER1-CUT/PRM.1", "macro1", remove=True)["slot"] == 3
