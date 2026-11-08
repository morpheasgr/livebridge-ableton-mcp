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
    assert result["written"] and out.is_file() and out.with_suffix(".vstpreset").is_file()
    vst = out.with_suffix(".vstpreset").read_bytes()
    assert vst[:4] == b"VST3" and vst[8:40] == S.CLASS_ID.encode()
    assert S.Patch(vst).routings() == S.Patch(out.read_bytes()).routings()
    read = tools["live_surge_routings"](str(out))
    assert read["name"] == "Pluck" and read["routings"][0]["source"] == "filtereg"
    assert read["step_sequences"][0]["lfo"] == "lfo2"
    again = tools["live_surge_write"]("Pluck", parameters={"a_pan": 0.1},
                                      template=str(INIT_SAW), output_path=str(out))
    assert "exists" in str(again)
    wet = tools["live_surge_write"](
        "Wet", fx=[{"slot": "A1", "type": "Reverb 2", "values": {"Mix": 0.4}}],
        routings=[{"target": "fx1_p9", "source": "modwheel", "depth": 0.5}],
        template=str(INIT_SAW), output_path=str(tmp_path / "Wet.fxp"))
    assert wet["written"] and wet["effects"][0]["type"] == "Reverb 2"
    read = tools["live_surge_routings"](str(tmp_path / "Wet.vstpreset"))
    assert read["effects"][0]["values"]["Mix"] == 0.4
    assert read["routings"] == [{"target": "fx1_p9", "source": "modwheel", "depth": 0.5}]
    assert "slot" in str(tools["live_surge_write"]("X", fx=[{"type": "Delay"}],
                                                   template=str(INIT_SAW)))


def test_vstpreset_round_trip_keeps_the_state():
    patch = S.Patch.load(INIT_SAW)
    vst = patch.to_vstpreset()
    again = S.Patch(vst)
    assert again.to_bytes()[60:] == INIT_SAW.read_bytes()[60:]      # state == fxp body
    with pytest.raises(ValueError, match="not Surge"):
        S.Patch(vst[:8] + b"0" * 32 + vst[40:])


def test_warnings_for_an_off_filter_and_a_free_running_step_sequencer():
    patch = S.Patch.load(INIT_SAW)
    patch.route("a_filter1_cutoff", "lfo1", 12)
    patch.set_step_sequence(1, [1, -1])
    text = " ".join(patch.warnings())
    assert "filter 1 of scene A is Off" in text and "runs free at 1 Hz" in text
    patch.set_parameter("a_filter1_type", 1)
    patch.params.find("a_lfo0_rate").set("temposync", "1")
    assert patch.warnings() == []


def test_synced_rates_match_the_users_patches():
    assert S.synced_rate("1/8") == 2.0 and S.synced_rate("1/16") == 3.0
    assert S.synced_rate("1/4") == 1.0 and S.synced_rate("1/1") == -1.0
    assert S.synced_rate("1/8d") == pytest.approx(1.415037, abs=1e-5)
    assert S.synced_rate("1/8t") == pytest.approx(2.584963, abs=1e-5)
    patch = S.Patch.load(INIT_SAW)
    patch.set_step_sequence(1, [1, -1], rate="1/16")
    rate = patch.params.find("a_lfo0_rate")
    assert float(rate.get("value")) == 3.0 and rate.get("temposync") == "1"
    assert not any("runs free" in w for w in patch.warnings())
    assert patch.step_sequences()[0]["rate"] == "1/16"
    patch.set_step_sequence(2, [1], rate="1/8d")
    assert patch.step_sequences()[1]["rate"] == "1/8d"
    with pytest.raises(ValueError, match="note length"):
        S.synced_rate("fast")


def test_filter_types_and_subtypes_by_name():
    patch = S.Patch.load(INIT_SAW)
    patch.set_parameter("a_filter1_type", "LP 24 dB")
    patch.set_parameter("a_filter1_subtype", "Driven")
    assert patch.params.find("a_filter1_type").get("value") == "2"
    assert patch.params.find("a_filter1_subtype").get("value") == "1"
    patch.set_parameter("a_filter2_type", "Notch 12 dB")           # "N" or "Notch"
    assert patch.params.find("a_filter2_type").get("value") == "7"
    patch.set_parameter("b_filter1_type", "multi xpander")
    assert patch.params.find("b_filter1_type").get("value") == "35"
    with pytest.raises(ValueError, match="no subtype"):
        patch.set_parameter("a_filter2_subtype", "Driven")
    with pytest.raises(ValueError, match="unknown filter type"):
        patch.set_parameter("a_filter1_type", "Moog")
    with pytest.raises(ValueError, match="patch's units"):
        patch.set_parameter("a_filter1_cutoff", "220 Hz")
    assert len(S.FILTER_TYPES) == 36 and S.FILTER_TYPES[34][0] == "MULTI Fast SVF"


def test_host_values_match_the_values_measured_in_live():
    assert S.host_value("a_filter1_cutoff", -12) == pytest.approx(0.369231, abs=1e-6)
    assert S.host_value("a_filter1_cutoff", 3) == pytest.approx(0.484615, abs=1e-6)
    assert S.host_value("a_lfo0_rate", 2) == pytest.approx(0.5625)
    assert S.host_value("a_osc1_pitch", 5) == pytest.approx(0.857143, abs=1e-6)
    assert S.host_value("a_env1_attack", 0.263034) == pytest.approx(0.636, abs=1e-3)
    assert S.host_value("a_filter1_type", 2) == pytest.approx(0.061571, abs=1e-6)
    assert S.host_value("a_osc1_param0", 0.5) is None          # depends on the osc type
    assert S.host_value("a_osc1_type", 2) == pytest.approx(0.185)      # Wavetable (measured)
    assert S.host_value("fx1_type", 11) == pytest.approx(0.35629, abs=1e-5)   # Reverb 2
    assert S.host_value("a_level_o2", 0.5) == 0.5
    patch = S.Patch.load(INIT_SAW)
    patch.set_parameter("a_filter1_cutoff", -12.0)
    values, skipped = S.host_values(
        patch, {"A Filter 1 Cutoff": "a_filter1_cutoff", "A Osc 1 Shape": "a_osc1_param0"},
        ["A Filter 1 Cutoff", "A Osc 1 Shape", "Unknown"])
    assert values == {"A Filter 1 Cutoff": pytest.approx(0.369231, abs=1e-6),
                      "A Osc 1 Shape": 0.5}             # Classic: shape -1..1, value 0
    assert skipped == ["Unknown"]


def test_expose_restores_surge_values_after_the_late_load(monkeypatch, tmp_path):
    import asyncio
    from livebridge_mcp.tools import plugin_racks as racks

    patch = S.Patch.load(INIT_SAW)
    patch.set_parameter("a_filter1_cutoff", -12.0)
    patch.set_parameter("a_filter1_type", "LP 24 dB")
    preset = tmp_path / "Check.vstpreset"
    preset.write_bytes(patch.to_vstpreset())
    calls = []

    def fake_call(bridge, cmd, args, timeout=None):
        calls.append((cmd, args))
        if cmd == "devices.parameters":               # the re-read after Surge relabels
            return {"parameters": [{"index": 1, "name": "A Filter 1 Cutoff", "value": 0.369231,
                                    "display": "after"}]}
        return {"count": len(args["values"])}

    monkeypatch.setattr(racks, "bridge_call", fake_call)
    monkeypatch.setattr(racks, "_SURGE_SETTLE_S", 0)
    monkeypatch.setattr(racks, "_SURGE_RELABEL_S", 0)
    result = {"plugin": "Surge XT", "device": "tracks 0 devices 0 chains 0 devices 0",
              "warning": "reset", "parameters": [{"index": 1, "name": "A Filter 1 Cutoff",
                                                   "value": 0.48, "display": "before"},
                                                  {"name": "A Filter 1 Type"},
                                                  {"name": "A Osc 1 Shape"}]}
    asyncio.run(racks._restore_surge_values(None, result, str(preset)))
    (cmd, args), (reread, _a) = calls
    assert reread == "devices.parameters"
    sent = {v["parameter"]: v["value"] for v in args["values"]}
    assert cmd == "devices.set_parameters" and args["normalized"] is True
    assert sent == {1: pytest.approx(0.369231, abs=1e-6),       # by index: survives relabels
                    "A Filter 1 Type": pytest.approx(0.061571, abs=1e-6),
                    "A Osc 1 Shape": 0.5}
    assert result["restored"] == 3 and "warning" not in result
    assert result["parameters"][0]["display"] == "after"         # reported after the restore
    assert result["parameters"][0]["value"] == pytest.approx(0.369231, abs=1e-6)


def _wt(frames=4, length=256, flags=4):
    samples = bytes(frames * length * (2 if flags & 4 else 4))
    return b"vawt" + struct.pack("<IHH", length, frames, flags) + samples


def test_wavetables_go_into_the_right_oscillator(tmp_path):
    patch = S.Patch.load(INIT_SAW)
    info = patch.set_wavetable(2, _wt(), "Growl")
    assert info["frames"] == 4 and info["format"] == "int16"
    patch.set_wavetable(1, _wt(8, 512, 0), "Float", scene="b", window=True)
    patch.route("a_pan", "lfo1", 0.5)
    again = S.Patch(patch.to_bytes())
    assert [len(t) for t in again.wavetables] == [0, 12 + 4 * 256 * 2, 0,
                                                  12 + 8 * 512 * 4, 0, 0]
    assert again.wavetables[1][:4] == bytes(4)               # zero word, not 'vawt'
    assert again.params.find("a_osc2_type").get("value") == "2"
    assert again.params.find("b_osc1_type").get("value") == "7"
    name = again.root.find("extraoscdata/osc_extra_sc0_osc1").get("wavetable_display_name")
    assert name == "Growl" and again.routings()[0]["target"] == "a_pan"
    with pytest.raises(ValueError, match="vawt"):
        patch.set_wavetable(1, b"RIFF" + bytes(20), "x")
    with pytest.raises(ValueError, match="sample"):
        patch.set_wavetable(1, _wt(flags=5), "x")
    with pytest.raises(ValueError, match="truncated"):
        patch.set_wavetable(1, _wt()[:100], "x")


def test_wavetable_keeps_nightly_data_after_the_tables():
    data = bytearray(INIT_SAW.read_bytes()) + b"(\xb5/\xfd-nightly"
    struct.pack_into(">I", data, 56, len(data) - 60)
    patch = S.Patch(bytes(data))
    patch.set_wavetable(3, _wt(), "T")
    out = patch.to_bytes()
    assert out.endswith(b"(\xb5/\xfd-nightly") and S.Patch(out).wavetables[2][:4] == bytes(4)


def test_oscillator_and_fx_types_by_name():
    patch = S.Patch.load(INIT_SAW)
    patch.set_parameter("a_osc1_type", "Wavetable")
    patch.set_parameter("b_osc3_type", "s&h noise")
    assert patch.params.find("a_osc1_type").get("value") == "2"
    assert patch.params.find("b_osc3_type").get("value") == "3"
    patch.params.append(S.ET.Element("fx1_type", {"type": "0", "value": "0"}))
    patch.set_parameter("fx1_type", "Reverb 2")
    assert patch.params.find("fx1_type").get("value") == "11"
    assert patch.params.find("fx1_p11") is not None                 # controls filled in
    with pytest.raises(ValueError, match="FX slot is empty"):
        patch.set_parameter("fx2_p0", 0.5)
    with pytest.raises(ValueError, match="unknown oscillator type"):
        patch.set_parameter("a_osc1_type", "Supersaw")
    assert len(S.OSC_TYPES) == 12 and len(S.FX_TYPES) == 32


def test_switching_the_oscillator_type_starts_from_surges_own_values():
    patch = S.Patch.load(INIT_SAW)
    change = patch.set_parameter("a_osc1_type", "FM2")
    assert "FM2 start values" in change["controls"]
    p1 = patch.params.find("a_osc1_param1")
    assert (p1.get("type"), p1.get("value")) == ("0", "1")        # integer ratio, as Init FM2
    assert patch.params.find("a_osc1_param6").get("type") == "2"
    patch.set_parameter("a_osc1_param0", 0.4)                     # user values apply afterwards
    assert patch.params.find("a_osc1_param0").get("value").startswith("0.4")
    patch.set_parameter("a_osc1_type", "FM2")                     # same type: nothing reset
    assert patch.params.find("a_osc1_param0").get("value").startswith("0.4")
    kept = patch.set_parameter("a_osc2_type", "Twist")
    assert "kept the previous type's controls" in kept["controls"]


def test_oscillator_controls_convert_per_type_and_sends():
    assert S.host_value("a_osc1_param4", 30, osc_type=0) == pytest.approx(0.5)    # Sync 30 st
    assert S.host_value("a_osc1_param1", 17, osc_type=6) == pytest.approx(0.515968, abs=1e-6)  # C:17 centre
    assert S.host_value("a_osc1_param3", -60, osc_type=1) == 0.0                 # Sine low cut
    assert S.host_value("a_osc1_param6", 16, osc_type=0) == pytest.approx(0.995)  # 16 voices
    assert S.host_value("a_osc1_param0", 0.5, osc_type=9) is None              # String exciter
    assert S.host_value("a_osc1_param0", 0.5) is None                          # type unknown
    assert S.host_value("a_send_fx_1", 0.5) == pytest.approx(0.31498, abs=1e-5)


def test_type_specific_oscillator_labels_translate_to_map_names():
    patch = S.Patch.load(INIT_SAW)
    patch.set_parameter("a_osc1_type", "FM2")
    patch.set_parameter("b_osc2_type", "Sine")
    got = S.generic_names(["A Osc 1 M1 Amount", "A Osc 1 Feedback", "B Osc 2 Feedback",
                           "A Osc 1 Shape", "A Filter 1 Cutoff", "a osc 1 m1 ratio"], patch)
    assert got == ["A Osc 1 Shape", "A Osc 1 Unison Voices", "B Osc 2 Width 1",
                   "A Osc 1 Shape", "A Filter 1 Cutoff", "A Osc 1 Width 1"]
    assert S.generic_names(["A Osc 1 M1 Amount"]) == ["A Osc 1 M1 Amount"]   # Classic: no alias


def test_fx_in_an_empty_slot_starts_from_surges_values():
    patch = S.Patch.load(INIT_SAW)
    assert patch.params.find("fx9_type") is None                     # Init Saw has no FX
    change = patch.set_fx("A3", "Reverb 2", {"Decay Time": 2.0, "mix": 0.25})
    assert change["slot"] == "A3" and change["fx"] == 9 and change["type"] == "Reverb 2"
    assert change["values"] == {"Decay Time": 2.0, "Mix": 0.25}
    tags = [e.tag for e in patch.params]
    assert tags[tags.index("fx_bypass") + 1:tags.index("fx_bypass") + 14] == \
        ["fx9_type"] + [f"fx9_p{k}" for k in range(12)]
    patch.set_fx(1, "Delay")                                         # A1 goes before A3
    tags = [e.tag for e in patch.params]
    assert tags.index("fx1_p11") < tags.index("fx9_type")
    assert patch.params.find("fx1_p0").get("value") == "-2.00000000000000"
    assert patch.params.find("fx1_p9").attrib == {"type": "0", "value": "0"}
    again = S.Patch(patch.to_bytes())
    fx = {e["slot"]: e for e in again.effects()}
    assert fx["A3"]["values"]["Decay Time"] == 2.0 and fx["A3"]["values"]["Mix"] == 0.25
    assert fx["A1"]["type"] == "Delay" and "Time Left" in fx["A1"]["values"]


def test_fx_type_change_resets_its_controls_and_names_are_checked():
    patch = S.Patch.load(INIT_SAW)
    patch.set_fx("A1", "Delay", {"Feedback": 0.9})
    patch.set_fx("A1", "Delay")                                      # same type: kept
    assert patch.params.find("fx1_p2").get("value").startswith("0.9")
    patch.set_parameter("fx1_type", "Phaser")                        # other type: reset
    assert patch.params.find("fx1_p2").get("value") == "0.00000000000000"
    patch.set_fx("A1", values={"p8": 6, "Speaker - Rate": 1.0})       # number / label tail
    assert patch.params.find("fx1_p8").get("value") == "6"
    with pytest.raises(ValueError, match="Phaser has no control 'Decay'"):
        patch.set_fx("A1", values={"Decay": 1})
    with pytest.raises(ValueError, match="FX slot"):
        patch.set_fx("Z9", "Delay")
    with pytest.raises(ValueError, match="empty"):
        patch.set_fx("B1", values={"Mix": 1})
    assert patch.set_fx("A1", "Off")["controls"] == "cleared"
    assert patch.effects() == []


def test_fx_tables_cover_every_type():
    from livebridge_mcp import surge_fx as F
    assert set(F.FX_PARAMS) == set(F.FX_DEFAULTS) == set(range(1, len(S.FX_TYPES)))
    for fx_type, entries in F.FX_PARAMS.items():
        assert len(entries) == 12 and len(F.FX_DEFAULTS[fx_type]) == 12
        for (label, _r), start in zip(entries, F.FX_DEFAULTS[fx_type]):
            if label is None:
                assert start == {"type": "0", "value": "0"}


def test_fx_warnings_and_host_values():
    patch = S.Patch.load(INIT_SAW)
    patch.set_fx("S1", "Reverb 2")
    patch.set_fx("G1", "Convolution")
    text = " ".join(patch.warnings())
    assert "a_send_fx_1" in text and "impulse response" in text
    patch.set_parameter("a_send_fx_1", 1.0)
    assert "a_send_fx_1" not in " ".join(patch.warnings())
    # Reverb 2 Decay Time: log2 s -4..6; Mix 0..1; FX Delay mod rate log2 Hz -7..9
    assert S.host_value("fx5_p2", 1.0, fx_type=11) == pytest.approx(0.5)
    assert S.host_value("fx5_p9", 0.33, fx_type=11) == pytest.approx(0.33)
    assert S.host_value("fx1_p3", 0.0, fx_type=None) is None
    patch.set_fx("A1", "Delay", {"Mix": 0.5})
    patch.set_fx("A2", "Ensemble")
    names = {"FX A1 Param 11": "fx1_p10", "FX A1 Param 10": "fx1_p9", "FX A2 Param 6": "fx2_p5"}
    values, skipped = S.host_values(patch, names, list(names))
    assert values == {"FX A1 Param 11": 0.5}            # p9 unused: neither set nor reported
    assert skipped == ["FX A2 Param 6"]                  # Ensemble's delay type: unknown menu
    assert S.generic_names(["FX A1 Mix", "FX A1 Param 3", "FX S1 Decay Time"], patch) == \
        ["FX A1 Param 11", "FX A1 Param 3", "FX S1 Param 3"]


def test_fx_values_with_units():
    patch = S.Patch.load(INIT_SAW)
    patch.set_fx("A1", "Reverb 2", {"Decay Time": "2 s", "Pre-Delay": "62.5 ms", "Mix": "30 %",
                                    "Width": "+3 dB"})
    get = lambda k: float(patch.params.find(f"fx1_p{k}").get("value"))
    assert get(2) == pytest.approx(1.0) and get(0) == pytest.approx(-4.0)
    assert get(9) == pytest.approx(0.3) and get(8) == pytest.approx(3.0)
    patch.set_fx("A2", "Delay", {"Low Cut": "220 Hz", "High Cut": "1.76 kHz", "Mod Rate": "4 Hz",
                                 "Mod Depth": "50 cents"})
    get = lambda k: float(patch.params.find(f"fx2_p{k}").get("value"))
    assert get(4) == pytest.approx(-12.0) and get(5) == pytest.approx(24.0, abs=1e-6)
    assert get(6) == pytest.approx(2.0) and get(7) == pytest.approx(0.5)
    patch.set_fx("A3", "Resonator", {"Gain 1": "-6 dB", "Gain 2": "-inf dB"})
    assert float(patch.params.find("fx9_p2").get("value")) == pytest.approx(2 ** (-1 / 3))
    assert float(patch.params.find("fx9_p5").get("value")) == 0.0
    patch.set_fx("A4", "Ring Mod", {"Frequency": "440 Hz"})
    assert float(patch.params.find("fx10_p1").get("value")) == pytest.approx(69.0)
    with pytest.raises(ValueError, match="does not fit"):
        patch.set_fx("A1", values={"Decay Time": "30 %"})
    with pytest.raises(ValueError, match="patch's units"):
        patch.set_fx("A1", values={"Decay Time": "long"})
    # Surge's own labels work too ("Section - Name")
    patch.set_fx("B1", "Neuron", {"Comb - Frequency": "880 Hz", "Output - Gain": -6})
    assert float(patch.params.find("fx3_p5").get("value")) == pytest.approx(12.0)


def test_restore_warns_with_the_knobs_final_names(tmp_path, monkeypatch):
    import asyncio
    from livebridge_mcp.tools import plugin_racks as racks

    patch = S.Patch.load(INIT_SAW)
    patch.set_fx("A2", "Ensemble", {"Mix": "50 %"})
    preset = tmp_path / "E.vstpreset"
    preset.write_bytes(patch.to_vstpreset())
    monkeypatch.setattr(racks, "_SURGE_SETTLE_S", 0)
    monkeypatch.setattr(racks, "_SURGE_RELABEL_S", 0)
    final = {0: "A2 Output - Mix", 1: "A2 Delay - Type"}

    def fake_call(_bridge, cmd, args, _timeout=None):
        if cmd == "devices.parameters":
            return {"parameters": [{"index": i, "name": n, "value": 0.5, "display": "x"}
                                   for i, n in final.items()]}
        assert [v["parameter"] for v in args["values"]] == [0]
        return {"ok": True}

    monkeypatch.setattr(racks, "bridge_call", fake_call)
    result = {"device": 1, "parameters": [{"index": 0, "name": "FX A2 Param 11"},
                                          {"index": 1, "name": "FX A2 Param 6"}]}
    asyncio.run(racks._restore_surge_values(None, result, str(preset)))
    assert result["restored"] == 1
    assert "A2 Delay - Type" in result["warning"] and "Param 6" not in result["warning"]
