"""Surge XT patch files (``.fxp``): read and write modulation routings, step sequences and
parameters.

Layout (``docs/SURGE_FORMAT.md``, worked out from the user's labelled patches and Surge's CC0
templates; no Surge code used): a 60-byte FXP header (big-endian, plug-in id ``cjs3``), then
``sub3``, the XML size, six wavetable sizes, the XML and the wavetables; nightlies (revision 30+)
append a compressed block. This module rewrites the XML only and copies everything after it
byte for byte, fixing the two sizes that cover the XML.

Pure Python, no Live calls.
"""

from __future__ import annotations

import re
import struct
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .surge_fx import FX_DEFAULTS, FX_PARAMS, FX_SLOTS, SEND_OF_SLOT, FX_MENUS, fx_unit

PLUGIN_ID = b"cjs3"
CLASS_ID = "ABCDEF019182FAEB566D624153675854"          # Surge XT's VST3 class id
_INFO = ("<?xml version='1.0' encoding='utf-8'?>\n<MetaInfo>" + "".join(
    f"<Attribute id='{k}' value='{v}' type='string' flags='writeProtected'></Attribute>"
    for k, v in (("MediaType", "VstPreset"), ("PlugInCategory", "Instrument|Synth"),
                 ("plugtype", "Instrument|Synth"), ("PlugInName", "Surge XT"),
                 ("plugname", "Surge XT"), ("PlugInVendor", "Surge Synth Team")))
    + "</MetaInfo>").encode()
REVISIONS = range(16, 32)               # patch revisions seen: 21-31; older ones still parse
SOURCES: dict[str, int] = {
    "velocity": 1, "keytrack": 2, "polyat": 3, "aftertouch": 4, "pitchbend": 5,
    "modwheel": 6, **{f"macro{i}": 6 + i for i in range(1, 9)}, "ampeg": 15, "filtereg": 16,
    **{f"lfo{i}": 16 + i for i in range(1, 7)}, **{f"slfo{i}": 22 + i for i in range(1, 7)},
    "releasevelocity": 30, "lowestkey": 38, "highestkey": 39,
}
_ALIASES = {"vel": "velocity", "kt": "keytrack", "polyaftertouch": "polyat",
            "channelat": "aftertouch", "mpepressure": "aftertouch", "pb": "pitchbend",
            "mw": "modwheel", "env1": "ampeg", "env2": "filtereg", "fenv": "filtereg",
            "aenv": "ampeg", **{f"scenelfo{i}": f"slfo{i}" for i in range(1, 7)}}
SOURCE_NAMES = {n: s for s, n in SOURCES.items()}
STEPSEQ_SHAPE = 7
# Filter types in Surge's own numbering (the patch's a_filterN_type), with their subtypes —
# read from Surge XT 1.4.0 in Live by local Claude (type 0-35, subtype 0-15 per type).
FILTER_TYPES: list[tuple[str, list[str]]] = [
    ('Off', []),
    ('LP 12 dB', ['Standard', 'Driven', 'Clean']),
    ('LP 24 dB', ['Standard', 'Driven', 'Clean']),
    ('LP Legacy Ladder', ['6 dB', '12 dB', '18 dB', '24 dB']),
    ('HP 12 dB', ['Standard', 'Driven', 'Clean']),
    ('HP 24 dB', ['Standard', 'Driven', 'Clean']),
    ('BP 12 dB', ['Standard', 'Driven', 'Clean', 'Driven (Legacy)', 'Clean (Legacy)']),
    ('N 12 dB', ['Standard', 'Mild']),
    ('FX Comb +', ['50% Wet', '100% Wet']),
    ('FX Sample & Hold', []),
    ('LP Vintage Ladder', ['Type 1', 'Type 1 Compensated', 'Type 2', 'Type 2 Compensated', 'Type 3', 'Type 3 Compensated']),
    ('LP OB-Xd 12 dB', ['Standard', 'Pushed']),
    ('LP OB-Xd 24 dB', ['6 dB', '12 dB', '18 dB', '24 dB', '24 dB (Legacy)']),
    ('LP K35', ['No Saturation', 'Mild Saturation', 'Moderate Saturation', 'Heavy Saturation', 'Extreme Saturation']),
    ('HP K35', ['No Saturation', 'Mild Saturation', 'Moderate Saturation', 'Heavy Saturation', 'Extreme Saturation']),
    ('LP Diode Ladder', ['6 dB', '12 dB', '18 dB', '24 dB']),
    ('LP Cutoff Warp', ['1 Stage tanh', '2 Stages tanh', '3 Stages tanh', '4 Stages tanh', '1 Stage Soft Clip', '2 Stages Soft Clip', '3 Stages Soft Clip', '4 Stages Soft Clip', '1 Stage OJD', '2 Stages OJD', '3 Stages OJD', '4 Stages OJD']),
    ('HP Cutoff Warp', ['1 Stage tanh', '2 Stages tanh', '3 Stages tanh', '4 Stages tanh', '1 Stage Soft Clip', '2 Stages Soft Clip', '3 Stages Soft Clip', '4 Stages Soft Clip', '1 Stage OJD', '2 Stages OJD', '3 Stages OJD', '4 Stages OJD']),
    ('N Cutoff Warp', ['1 Stage tanh', '2 Stages tanh', '3 Stages tanh', '4 Stages tanh', '1 Stage Soft Clip', '2 Stages Soft Clip', '3 Stages Soft Clip', '4 Stages Soft Clip', '1 Stage OJD', '2 Stages OJD', '3 Stages OJD', '4 Stages OJD']),
    ('BP Cutoff Warp', ['1 Stage tanh', '2 Stages tanh', '3 Stages tanh', '4 Stages tanh', '1 Stage Soft Clip', '2 Stages Soft Clip', '3 Stages Soft Clip', '4 Stages Soft Clip', '1 Stage OJD', '2 Stages OJD', '3 Stages OJD', '4 Stages OJD']),
    ('HP OB-Xd 12 dB', ['Standard', 'Pushed']),
    ('N OB-Xd 12 dB', ['Standard', 'Pushed']),
    ('BP OB-Xd 12 dB', ['Standard', 'Pushed']),
    ('BP 24 dB', ['Standard', 'Driven', 'Clean']),
    ('N 24 dB', ['Standard', 'Mild']),
    ('FX Comb -', ['50% Wet', '100% Wet']),
    ('FX Allpass', []),
    ('FX Cutoff Warp AP', ['1 Stage tanh', '2 Stages tanh', '3 Stages tanh', '4 Stages tanh', '1 Stage Soft Clip', '2 Stages Soft Clip', '3 Stages Soft Clip', '4 Stages Soft Clip', '1 Stage OJD', '2 Stages OJD', '3 Stages OJD', '4 Stages OJD']),
    ('LP Res Warp', ['1 Stage tanh', '2 Stages tanh', '3 Stages tanh', '4 Stages tanh', '1 Stage Soft Clip', '2 Stages Soft Clip', '3 Stages Soft Clip', '4 Stages Soft Clip']),
    ('HP Res Warp', ['1 Stage tanh', '2 Stages tanh', '3 Stages tanh', '4 Stages tanh', '1 Stage Soft Clip', '2 Stages Soft Clip', '3 Stages Soft Clip', '4 Stages Soft Clip']),
    ('N Res Warp', ['1 Stage tanh', '2 Stages tanh', '3 Stages tanh', '4 Stages tanh', '1 Stage Soft Clip', '2 Stages Soft Clip', '3 Stages Soft Clip', '4 Stages Soft Clip']),
    ('BP Res Warp', ['1 Stage tanh', '2 Stages tanh', '3 Stages tanh', '4 Stages tanh', '1 Stage Soft Clip', '2 Stages Soft Clip', '3 Stages Soft Clip', '4 Stages Soft Clip']),
    ('FX Res Warp AP', ['1 Stage tanh', '2 Stages tanh', '3 Stages tanh', '4 Stages tanh', '1 Stage Soft Clip', '2 Stages Soft Clip', '3 Stages Soft Clip', '4 Stages Soft Clip']),
    ('MULTI Tri-pole', ['Low->Low->Low First', 'Low->High->Low First', 'High->Low->High First', 'High->High->High First', 'Low->Low->Low Second', 'Low->High->Low Second', 'High->Low->High Second', 'High->High->High Second', 'Low->Low->Low Third', 'Low->High->Low Third', 'High->Low->High Third', 'High->High->High Third']),
    ('MULTI Fast SVF', ['LP', 'HP', 'BP', 'Notch', 'Peak', 'Allpass', 'Bell', 'Low Shelf', 'High Shelf']),
    ('MULTI Xpander', ['LP1', 'LP2', 'LP3', 'LP4', 'HP1', 'HP2', 'HP3', 'BP2', 'BP4', 'N2', 'PH3', 'HP2+LP1', 'HP3+LP1', 'N2+LP1', 'PH3+LP1']),
]


def synced_rate(text: str) -> float:
    """A tempo-synced LFO rate as stored: log2 of its frequency at 120 BPM ("1/16" -> 3,
    "1/8" -> 2, "1/4" -> 1, "1/1" -> -1; "1/8d" dotted, "1/8t" triplet). From the user's
    labelled patches (1/8 = 2, 1/16 = 3)."""
    match = re.fullmatch(r"\s*(\d+)\s*/\s*(\d+)\s*([dt]?)\s*", str(text).lower())
    if not match or int(match.group(1)) == 0 or int(match.group(2)) == 0:
        raise ValueError(f"rate {text!r}: use a note length like '1/16', '1/8d', '1/4t', '2/1'")
    beats = 4.0 * int(match.group(1)) / int(match.group(2))      # length in quarter notes
    beats *= {"": 1.0, "d": 1.5, "t": 2.0 / 3.0}[match.group(3)]
    hertz = 2.0 / beats                                         # 120 BPM = 2 beats a second
    import math
    return math.log2(hertz)


def source_number(source: str | int) -> int:
    if isinstance(source, int) and source > 0:
        return source
    key = re.sub(r"[\s_\-]", "", str(source).lower())
    key = _ALIASES.get(key, key)
    if key not in SOURCES:
        raise ValueError(f"unknown source {source!r} — use velocity, keytrack, polyat, "
                         "aftertouch, pitchbend, modwheel, macro1-8, ampeg, filtereg, lfo1-6, "
                         "slfo1-6, releasevelocity, lowestkey, highestkey (or a number)")
    return SOURCES[key]


class Patch:
    """A Surge XT ``.fxp`` patch: its XML tree plus the bytes around it."""

    def __init__(self, data: bytes):
        if data[:4] == b"VST3":                     # a .vstpreset: its state is the fxp body
            data = _fxp_from_vstpreset(data)
        if len(data) < 92 or data[:4] != b"CcnK" or data[16:20] != PLUGIN_ID:
            raise ValueError("not a Surge XT patch (.fxp with plug-in id cjs3)")
        self.head = bytearray(data[:60])
        chunk = data[60:]
        if chunk[:4] != b"sub3":
            raise ValueError("unknown Surge patch chunk (no 'sub3')")
        xml_size = struct.unpack_from("<I", chunk, 4)[0]
        self.wavetable_sizes = struct.unpack_from("<6I", chunk, 8)
        self.xml = chunk[32:32 + xml_size]
        rest = chunk[32 + xml_size:]               # wavetables, then nightly data (kept)
        if sum(self.wavetable_sizes) > len(rest):
            raise ValueError("the patch is truncated (wavetables missing)")
        self.wavetables: list[bytes] = []
        offset = 0
        for size in self.wavetable_sizes:
            self.wavetables.append(rest[offset:offset + size])
            offset += size
        self.extra = rest[offset:]
        self.root = ET.fromstring(self.xml)
        if self.root.tag != "patch":
            raise ValueError("the patch XML has no <patch> root")
        self.revision = int(self.root.get("revision", "0"))
        if self.revision not in REVISIONS:
            raise ValueError(f"patch revision {self.revision} is newer than LiveBridge knows "
                             f"({REVISIONS.stop - 1}): save it from a Surge release, or update")
        self.params = self.root.find("parameters")
        if self.params is None:
            raise ValueError("the patch has no <parameters>")

    @classmethod
    def load(cls, path: str | Path) -> "Patch":
        return cls(Path(path).read_bytes())

    # --------------------------------------------------------------- reading
    @property
    def name(self) -> str:
        meta = self.root.find("meta")
        return meta.get("name", "") if meta is not None else ""

    def routings(self) -> list[dict[str, Any]]:
        out = []
        for param in self.params:
            for mod in param.findall("modrouting"):
                number = int(mod.get("source", "0"))
                entry = {"target": param.tag,
                         "source": SOURCE_NAMES.get(number, number),
                         "depth": float(mod.get("depth", "0"))}
                if mod.get("source_index", "0") != "0":
                    entry["output"] = int(mod.get("source_index", "0"))
                if mod.get("muted") == "1":
                    entry["muted"] = True
                out.append(entry)
        return out

    def step_sequences(self) -> list[dict[str, Any]]:
        out = []
        for seq in self.root.iter("sequence"):
            steps = [float(seq.get(f"s{k}", "0")) for k in range(16)]
            scene, index = "ab"[int(seq.get("scene", "0"))], int(seq.get("i", "0"))
            entry = {"scene": scene, "lfo": _lfo_label(index),
                     "steps": steps, "loop": [int(seq.get("loop_start", "0")) + 1,
                                              int(seq.get("loop_end", "15")) + 1],
                     "shuffle": float(seq.get("shuffle", "0"))}
            rate = self.params.find(f"{scene}_lfo{index}_rate")
            if rate is not None:
                value = float(rate.get("value", "0"))
                entry["rate"] = _note_length(value) if rate.get("temposync") == "1" \
                    else f"{2 ** value:.3g} Hz"
            out.append(entry)
        return out

    def parameter(self, name: str) -> ET.Element:
        element = self.params.find(name)
        if element is None and re.fullmatch(r"fx\d+_(type|p\d+)", name):
            raise ValueError(f"{name}: this FX slot is empty — give its type first "
                             f"({name.split('_')[0]}_type, or fx=[{{'slot': ..., 'type': ...}}])")
        if element is None:
            raise ValueError(f"no Surge parameter {name!r} in this patch (names as in the "
                             "patch: a_filter1_cutoff, a_osc1_pitch, fx1_type ...)")
        return element

    # --------------------------------------------------------------- writing
    def route(self, target: str, source: str | int, depth: float | None = None,
              remove: bool = False, output: int = 0) -> dict[str, Any]:
        param = self.parameter(target)
        number = source_number(source)
        hits = [m for m in param.findall("modrouting")
                if int(m.get("source", "0")) == number
                and int(m.get("source_index", "0")) == output]
        label = SOURCE_NAMES.get(number, number)
        if remove:
            for mod in hits:
                param.remove(mod)
            return {"target": target, "source": label, "removed": bool(hits)}
        if depth is None or not isinstance(depth, (int, float)) or float(depth) == 0.0:
            raise ValueError("depth must be a non-zero number in the target's units "
                             "(semitones for pitch and cutoff, 1.0 = 100 % for levels)")
        before = float(hits[0].get("depth", "0")) if hits else None
        mod = hits[0] if hits else ET.SubElement(param, "modrouting")
        mod.set("source", str(number))
        mod.set("depth", _num(float(depth)))
        mod.set("muted", "0")
        mod.set("source_index", str(output))
        if not param.tag.startswith(("a_", "b_")):
            mod.set("source_scene", "0")            # global targets (FX) name the scene
        return {"target": target, "source": label, "depth": float(depth), "before": before}

    def set_parameter(self, name: str, value: float | int | str) -> dict[str, Any]:
        fx = re.fullmatch(r"fx(\d+)_type", name)
        if fx:
            return self.set_fx(int(fx.group(1)), value)
        param = self.parameter(name)
        before = param.get("value")
        osc = None
        match = re.fullmatch(r"([ab])_filter([12])_(type|subtype)", name)
        if match and isinstance(value, str):
            value = _filter_index(self, match.group(1), match.group(2), match.group(3), value)
        elif isinstance(value, str) and re.fullmatch(r"[ab]_osc[123]_type", name):
            value = _list_index(OSC_TYPES, value, "oscillator type")
        osc = re.fullmatch(r"([ab]_osc[123])_type", name)
        if osc and int(float(before or 0)) != int(value):
            note = self._reset_osc_controls(osc.group(1), int(value))
        elif isinstance(value, str):
            raise ValueError(f"{name}: give a number in the patch's units (names are understood "
                             "for filter, oscillator and FX types and filter subtypes)")
        if param.get("type") == "0":
            param.set("value", str(int(value)))
        else:
            param.set("value", f"{float(value):.14f}")
        result = {"parameter": name, "value": param.get("value"), "before": before}
        if osc and int(float(before or 0)) != int(value):
            result["controls"] = note
        return result

    # --------------------------------------------------------------- effects
    def effects(self) -> list[dict[str, Any]]:
        """The used FX slots: type and every named control's value (patch units)."""
        out = []
        for number in range(1, 17):
            kind = self.params.find(f"fx{number}_type")
            if kind is None or int(kind.get("value", "0")) == 0:
                continue
            fx_type = int(kind.get("value", "0"))
            entry: dict[str, Any] = {"slot": FX_SLOTS[number - 1], "fx": number,
                                     "type": FX_TYPES[fx_type] if fx_type < len(FX_TYPES)
                                     else fx_type, "values": {}}
            for k, (label, _range) in enumerate(FX_PARAMS.get(fx_type, [])):
                element = self.params.find(f"fx{number}_p{k}")
                if label and element is not None:
                    value = float(element.get("value", "0"))
                    entry["values"][label] = int(value) if element.get("type") == "0" \
                        else round(value, 6)
            out.append(entry)
        return out

    def set_fx(self, slot: int | str, fx_type: int | str | None = None,
               values: dict[str, Any] | None = None) -> dict[str, Any]:
        """Put an effect in a slot (1-16 or "A1".."G4"), empty or not, and/or set its controls
        by name ({"Decay Time": 1.5, "Mix": 0.3}, patch units) or by number ("p3")."""
        number = fx_slot(slot)
        result: dict[str, Any] = {"slot": FX_SLOTS[number - 1], "fx": number}
        kind = self.params.find(f"fx{number}_type")
        before = int(kind.get("value", "0")) if kind is not None else 0
        if fx_type is not None:
            new = _list_index(FX_TYPES, fx_type, "FX type") if isinstance(fx_type, str) \
                else int(fx_type)
            if not 0 <= new < len(FX_TYPES):
                raise ValueError(f"FX type must be 0-{len(FX_TYPES) - 1} or a name")
            if kind is None:
                kind = self._new_fx_slot(number)
            kind.set("value", str(new))
            if new != before or self.params.find(f"fx{number}_p0") is None:
                for k in range(12):
                    element = self.params.find(f"fx{number}_p{k}")
                    if element is None:              # a slot written without its controls
                        element = ET.Element(f"fx{number}_p{k}")
                        self.params.insert(list(self.params).index(kind) + 1 + k, element)
                    element.attrib.clear()
                    element.attrib.update(FX_DEFAULTS.get(new, [{"type": "0", "value": "0"}]
                                                       * 12)[k])
                result["controls"] = "Surge's start values" if new else "cleared"
            result.update(type=FX_TYPES[new], before=FX_TYPES[before]
                          if before < len(FX_TYPES) else before)
        current = int(kind.get("value", "0")) if kind is not None else 0
        if values:
            if current == 0:
                raise ValueError(f"FX {FX_SLOTS[number - 1]} is empty: give its type")
            names = FX_PARAMS.get(current, [])
            written = {}
            for key, value in values.items():
                k = fx_control(current, key)
                element = self.parameter(f"fx{number}_p{k}")
                if isinstance(value, str):
                    value = fx_patch_value(current, k, value)
                if element.get("type") == "0":
                    element.set("value", str(int(value)))
                else:
                    element.set("value", f"{float(value):.14f}")
                written[names[k][0] or f"p{k}"] = value
            result["values"] = written
        return result

    def _new_fx_slot(self, number: int) -> ET.Element:
        """Create fxN_type and fxN_p0..p11 after the previous slot (Surge omits empty ones)."""
        children = list(self.params)
        anchor = next((i for i, e in enumerate(children) if e.tag == "fx_bypass"), None)
        for i, element in enumerate(children):
            match = re.fullmatch(r"fx(\d+)_(type|p\d+)", element.tag)
            if match and int(match.group(1)) < number:
                anchor = i
        at = (anchor + 1) if anchor is not None else len(children)
        made = [ET.Element(f"fx{number}_type", {"type": "0", "value": "0"})]
        made += [ET.Element(f"fx{number}_p{k}", {"type": "0", "value": "0"}) for k in range(12)]
        for offset, element in enumerate(made):
            self.params.insert(at + offset, element)
        return made[0]

    def _reset_osc_controls(self, osc: str, osc_type: int) -> str:
        """Start the oscillator's seven controls from the new type's own init values."""
        start = OSC_DEFAULTS.get(osc_type)
        if start is None:
            return ("kept the previous type's controls (no Surge template for "
                    f"{OSC_TYPES[osc_type] if 0 <= osc_type < len(OSC_TYPES) else osc_type}): "
                    "set them in Live or Surge")
        for k, attrs in enumerate(start):
            element = self.parameter(f"{osc}_param{k}")
            for key in list(element.attrib):
                del element.attrib[key]
            element.attrib.update(attrs)
        return f"reset to Surge's {OSC_TYPES[osc_type]} start values"

    def set_step_sequence(self, lfo: int, steps: list[float], scene: str = "a",
                          loop: list[int] | None = None, shuffle: float | None = None,
                          rate: str | None = None) -> dict[str, Any]:
        """Make voice LFO ``lfo`` (1-6) or scene LFO (7-12 = S-LFO 1-6) a step sequencer."""
        if scene not in ("a", "b"):
            raise ValueError("scene must be 'a' or 'b'")
        if not 1 <= int(lfo) <= 12:
            raise ValueError("lfo must be 1-6 (voice LFOs) or 7-12 (scene LFOs 1-6)")
        values = [float(v) for v in steps]
        if not 1 <= len(values) <= 16 or not all(-1.0 <= v <= 1.0 for v in values):
            raise ValueError("steps must be 1..16 values -1..1 (0 = middle)")
        index = int(lfo) - 1
        self.parameter(f"{scene}_lfo{index}_shape").set("value", str(STEPSEQ_SHAPE))
        if rate is not None:
            rate_param = self.parameter(f"{scene}_lfo{index}_rate")
            rate_param.set("value", f"{synced_rate(rate):.14f}")
            rate_param.set("temposync", "1")
        holder = self.root.find("stepsequences")
        if holder is None:
            holder = ET.SubElement(self.root, "stepsequences")
        scene_n = "ab".index(scene)
        seq = next((s for s in holder.findall("sequence")
                    if s.get("scene") == str(scene_n) and s.get("i") == str(index)), None)
        if seq is None:
            seq = ET.SubElement(holder, "sequence", {"scene": str(scene_n), "i": str(index)})
        for k in range(16):
            seq.attrib.pop(f"s{k}", None)
        rest = {k: seq.attrib.pop(k) for k in list(seq.attrib) if k not in ("scene", "i")}
        for k, v in enumerate(values):
            if v != 0.0:
                seq.set(f"s{k}", _num(v))
        first, last = (loop or [1, len(values)])
        if not 1 <= first <= last <= 16:
            raise ValueError("loop must be [first, last] steps, 1..16")
        rest.update(loop_start=str(first - 1), loop_end=str(last - 1))
        if shuffle is not None:
            if not -1.0 <= float(shuffle) <= 1.0:
                raise ValueError("shuffle must be -1..1")
            rest["shuffle"] = _num(float(shuffle))
        for key in ("shuffle", "trigmask", "trigmask_0to15", "trigmask_16to31",
                    "trigmask_32to47"):
            rest.setdefault(key, "0")
        seq.attrib.update(rest)
        result = {"scene": scene, "lfo": _lfo_label(index), "steps": values,
                  "loop": [first, last]}
        if rate is not None:
            result["rate"] = rate
        return result

    def warnings(self) -> list[str]:
        """What will keep the written routings / step sequences from being heard."""
        out = []
        for routing in self.routings():
            match = re.match(r"([ab])_filter([12])_(cutoff|resonance|envmod|keytrack)$",
                             routing["target"])
            if match:
                scene, number = match.group(1), match.group(2)
                kind = self.params.find(f"{scene}_filter{number}_type")
                if kind is not None and kind.get("value") == "0":
                    out.append(f"{routing['target']}: filter {number} of scene "
                               f"{scene.upper()} is Off (type 0) — set a filter type (in Live "
                               f"by name, or parameters={{'{scene}_filter{number}_type': ...}})")
        for seq in self.step_sequences():
            index = int(seq["lfo"][-1]) - 1 + (6 if seq["lfo"].startswith("slfo") else 0)
            rate = self.params.find(f"{seq['scene']}_lfo{index}_rate")
            if rate is not None and rate.get("temposync") != "1":
                hertz = 2 ** float(rate.get("value", "0"))
                out.append(f"{seq['lfo']} (scene {seq['scene'].upper()}) runs free at "
                           f"{hertz:.3g} Hz and moves one step per cycle: sync its rate to "
                           "the tempo in Surge/Live for rhythmic steps")
        for number, send in SEND_OF_SLOT.items():
            kind = self.params.find(f"fx{number}_type")
            if kind is None or kind.get("value", "0") == "0":
                continue
            levels = [self.params.find(f"{s}_send_fx_{send}") for s in "ab"]
            if all(e is None or float(e.get("value", "0")) == 0 for e in levels):
                out.append(f"FX {FX_SLOTS[number - 1]} is a send effect and both scenes send "
                           f"nothing to it: set a_send_fx_{send} (0..1.59, 1 = 0 dB)")
        for entry in self.effects():
            if entry["type"] == "Convolution":
                out.append(f"FX {entry['slot']} Convolution needs an impulse response: "
                           "load one in Surge's FX editor")
        return list(dict.fromkeys(out))

    @property
    def tail(self) -> bytes:
        return b"".join(self.wavetables) + self.extra

    def set_wavetable(self, osc: int, data: bytes, name: str, scene: str = "a",
                      window: bool = False) -> dict[str, Any]:
        """Put a wavetable (the bytes of a ``.wt`` file) into oscillator ``osc`` (1-3) and
        make it a Wavetable oscillator (or Window with ``window=True``)."""
        if scene not in ("a", "b") or int(osc) not in (1, 2, 3):
            raise ValueError("scene must be 'a' or 'b', osc 1-3")
        block, frames, length, flags = wavetable_block(data)
        slot = "ab".index(scene) * 3 + int(osc) - 1
        before = len(self.wavetables[slot])
        self.wavetables[slot] = block
        osc_type = self.parameter(f"{scene}_osc{osc}_type")
        osc_type.set("value", "7" if window else "2")
        extra = self.root.find(f"extraoscdata/osc_extra_sc{'ab'.index(scene)}_osc{int(osc) - 1}")
        if extra is not None:
            extra.set("wavetable_display_name", name)
        return {"scene": scene, "osc": int(osc), "wavetable": name, "frames": frames,
                "samples_per_frame": length, "format": "float32" if not flags & 4 else "int16",
                "replaced_bytes": before, "type": "Window" if window else "Wavetable"}

    def set_name(self, name: str) -> None:
        meta = self.root.find("meta")
        if meta is not None:
            meta.set("name", name)
        encoded = name.encode("ascii", "replace")[:27]
        self.head[28:56] = encoded + bytes(28 - len(encoded))

    def to_bytes(self) -> bytes:
        body = ET.tostring(self.root, encoding="unicode", short_empty_elements=True)
        xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>' + body).encode()
        ET.fromstring(xml)                                    # what we write parses again
        chunk = b"sub3" + struct.pack("<I", len(xml)) + \
            struct.pack("<6I", *(len(t) for t in self.wavetables)) + xml + \
            b"".join(self.wavetables) + self.extra
        head = bytearray(self.head)
        struct.pack_into(">I", head, 56, len(chunk))
        return bytes(head) + chunk

    def to_vstpreset(self) -> bytes:
        """The same patch as a VST3 preset (Live's browser and live_plugin_expose load it)."""
        chunks = {"Comp": self.to_bytes()[60:], "Cont": b"", "Info": _INFO}
        body, table = b"", []
        for chunk_id, payload in chunks.items():
            table.append((chunk_id, 48 + len(body), len(payload)))
            body += payload
        out = b"VST3" + struct.pack("<i", 1) + CLASS_ID.encode() + \
            struct.pack("<q", 48 + len(body)) + body + b"List" + struct.pack("<i", len(table))
        for chunk_id, start, size in table:
            out += chunk_id.encode() + struct.pack("<qq", start, size)
        return out


def _fxp_from_vstpreset(data: bytes) -> bytes:
    if data[8:40].decode("ascii", "replace").upper() != CLASS_ID:
        raise ValueError("this .vstpreset is not Surge XT")
    offset = struct.unpack_from("<q", data, 40)[0]
    if data[offset:offset + 4] != b"List":
        raise ValueError(".vstpreset has no chunk list")
    for k in range(struct.unpack_from("<i", data, offset + 4)[0]):
        entry = data[offset + 8 + 20 * k:offset + 28 + 20 * k]
        if entry[:4] == b"Comp":
            start, size = struct.unpack_from("<qq", entry, 4)
            body = data[start:start + size]
            head = b"CcnK" + bytes(4) + b"FPCh" + struct.pack(">I", 1) + PLUGIN_ID + \
                struct.pack(">II", 1, 1) + bytes(28) + struct.pack(">I", len(body))
            return head + body
    raise ValueError("the .vstpreset has no Surge state (Comp chunk)")


# Oscillator and FX types in Surge's numbering (patch values), read in Live by local Claude
# (Surge XT 1.4.0) and checked against the factory templates.
OSC_TYPES = ["Classic", "Sine", "Wavetable", "S&H Noise", "Audio Input", "FM3", "FM2",
             "Window", "Modern", "String", "Twist", "Alias"]
FX_TYPES = ["Off", "Delay", "Reverb 1", "Phaser", "Rotary", "Distortion", "EQ", "Freq Shift",
            "Conditioner", "Chorus", "Vocoder", "Reverb 2", "Flanger", "Ring Mod", "Airwindows",
            "Neuron", "Graphic EQ", "Resonator", "CHOW", "Exciter", "Ensemble", "Combulator",
            "Nimbus", "Tape", "Treemonster", "Waveshaper", "Mid-Side Tool", "Spring Reverb",
            "Bonsai", "Audio In", "Floaty Delay", "Convolution"]

# The seven oscillator controls (param0-6) as Surge's CC0 templates start each oscillator type
# (Init Saw, Sine, Wavetable, FM2, Modern, Experimental, Paraphonic): a type switch starts
# from these, so "FM2" means Surge's FM2, not Init Saw's knobs under another label.
OSC_DEFAULTS: dict[int, list[dict[str, str]]] = {
    0: [
        {'type': '2', 'value': '0.00000000000000'},
        {'type': '2', 'value': '0.50000000000000', 'extend_range': '1'},
        {'type': '2', 'value': '0.50000000000000'},
        {'type': '2', 'value': '0.00000000000000'},
        {'type': '2', 'value': '0.00000000000000'},
        {'type': '2', 'value': '0.10000000149012', 'extend_range': '0'},
        {'type': '0', 'value': '1'},
    ],
    1: [
        {'type': '0', 'value': '0'},
        {'type': '2', 'value': '0.00000000000000', 'extend_range': '1', 'deform_type': '0'},
        {'type': '0', 'value': '1'},
        {'type': '2', 'value': '-60.00000000000000', 'deactivated': '1'},
        {'type': '2', 'value': '70.00000000000000', 'deactivated': '1'},
        {'type': '2', 'value': '0.10000000149012', 'extend_range': '0'},
        {'type': '0', 'value': '1'},
    ],
    2: [
        {'type': '2', 'value': '0.00000000000000', 'extend_range': '1', 'deform_type': '1'},
        {'type': '2', 'value': '0.00000000000000', 'extend_range': '1'},
        {'type': '2', 'value': '0.00000000000000'},
        {'type': '2', 'value': '0.00000000000000'},
        {'type': '2', 'value': '0.00000000000000'},
        {'type': '2', 'value': '0.10000000149012', 'extend_range': '0'},
        {'type': '0', 'value': '1'},
    ],
    3: [
        {'type': '2', 'value': '0.75000000000000'},
        {'type': '2', 'value': '0.50000000000000'},
        {'type': '2', 'value': '-60.00000000000000', 'deactivated': '1'},
        {'type': '2', 'value': '70.00000000000000', 'deactivated': '1'},
        {'type': '2', 'value': '0.00000000000000'},
        {'type': '2', 'value': '0.10000000149012', 'extend_range': '0'},
        {'type': '0', 'value': '1', 'extend_range': '1'},
    ],
    4: [
        {'type': '2', 'value': '0.00000000000000'},
        {'type': '2', 'value': '0.00000000000000', 'extend_range': '1'},
        {'type': '2', 'value': '0.00000000000000'},
        {'type': '2', 'value': '6.01000022888184'},
        {'type': '2', 'value': '1.00000000000000'},
        {'type': '2', 'value': '-60.00000000000000', 'deactivated': '1'},
        {'type': '2', 'value': '70.00000000000000', 'deactivated': '1'},
    ],
    5: [
        {'type': '2', 'value': '0.00000000000000'},
        {'type': '2', 'value': '0.50000000000000', 'extend_range': '0'},
        {'type': '2', 'value': '0.00000000000000'},
        {'type': '2', 'value': '16.00000000000000', 'extend_range': '0'},
        {'type': '2', 'value': '0.00000000000000'},
        {'type': '2', 'value': '0.00000000000000', 'extend_range': '0'},
        {'type': '2', 'value': '0.00000000000000', 'extend_range': '1', 'deform_type': '0'},
    ],
    6: [
        {'type': '2', 'value': '0.00000000000000'},
        {'type': '0', 'value': '1', 'extend_range': '1'},
        {'type': '2', 'value': '0.00000000000000'},
        {'type': '0', 'value': '1'},
        {'type': '2', 'value': '0.00000000000000', 'extend_range': '0'},
        {'type': '2', 'value': '0.00000000000000'},
        {'type': '2', 'value': '0.00000000000000', 'extend_range': '1', 'deform_type': '0'},
    ],
    7: [
        {'type': '2', 'value': '0.00000000000000', 'extend_range': '1'},
        {'type': '2', 'value': '0.00000000000000', 'extend_range': '1'},
        {'type': '0', 'value': '1'},
        {'type': '2', 'value': '-60.00000000000000', 'deactivated': '1'},
        {'type': '2', 'value': '70.00000000000000', 'deactivated': '1'},
        {'type': '2', 'value': '0.10000000149012', 'extend_range': '0'},
        {'type': '0', 'value': '1'},
    ],
    8: [
        {'type': '2', 'value': '1.00000000000000'},
        {'type': '2', 'value': '0.00000000000000', 'extend_range': '1'},
        {'type': '2', 'value': '0.00000000000000', 'deform_type': '0'},
        {'type': '2', 'value': '0.50000000000000'},
        {'type': '2', 'value': '0.00000000000000'},
        {'type': '2', 'value': '0.10000000149012', 'extend_range': '0'},
        {'type': '0', 'value': '1'},
    ],
}

# Per oscillator type: each control's range in patch units, ("menu", first, count), or None
# when unknown — measured in Live by local Claude (display at 0 / 0.5 / 1, Surge XT 1.4.0).
_VOICES = ("menu", 1, 16)
OSC_PARAM_RANGES: dict[int, list[Any]] = {
    0: [(-1, 1), (0, 1), (0, 1), (0, 1), (0, 60), (0, 1), _VOICES],                  # Classic
    1: [("menu", 0, 32), None, ("menu", 0, 2), (-60, 70), (-60, 70), (0, 1), _VOICES],  # Sine
    2: [(0, 1), (-1, 1), (0, 1), (0, 60), (-1, 1), (0, 1), _VOICES],                 # Wavetable
    5: [(0, 1), None, (0, 1), (0, 32), (0, 1), (-69, 61), (-1, 1)],                  # FM3
    6: [(0, 1), ("menu", 1, 32), (0, 1), ("menu", 1, 32), (-10, 10), (0, 1), (-1, 1)],  # FM2
    7: [(0, 1), (-60, 60), None, (-60, 70), (-60, 70), (0, 1), _VOICES],             # Window
    8: [(-1, 1), (-1, 1), (-1, 1), (0, 1), (0, 60), (0, 1), _VOICES],                # Modern
    9: [None, (0, 1), (0, 1), (0, 1), None, (-1, 1), (-1, 1)],                        # String
    10: [None, (-1, 1), (-1, 1), (-1, 1), (0, 1), (0, 1), (0, 1)],                    # Twist
    11: [None, (0, 1), (0, 1), (0, 1), (1, 8), (0, 1), _VOICES],                      # Alias
}

# What Surge calls the seven oscillator controls per type (Live renames the exposed knobs to
# these once Surge has switched type); the map only has the Classic names. Read in Live by
# local Claude.
GENERIC_OSC_LABELS = ["Shape", "Width 1", "Width 2", "Sub Mix", "Sync", "Unison Detune",
                      "Unison Voices"]
OSC_LABELS: dict[int, list[str]] = {
    0: GENERIC_OSC_LABELS,
    1: ["Shape", "Feedback", "Behavior", "Low Cut", "High Cut", "Unison Detune", "Unison Voices"],
    2: ["Morph", "Skew Vertical", "Saturate", "Formant", "Skew Horizontal", "Unison Detune",
        "Unison Voices"],
    5: ["M1 Amount", "M1 Ratio", "M2 Amount", "M2 Ratio", "M3 Amount", "M3 Frequency",
        "Feedback"],
    6: ["M1 Amount", "M1 Ratio", "M2 Amount", "M2 Ratio", "M1/2 Offset", "M1/2 Phase",
        "Feedback"],
    7: ["Morph", "Formant", "Window", "Low Cut", "High Cut", "Unison Detune", "Unison Voices"],
    8: ["Sawtooth", "Pulse", "Triangle", "Width", "Sync", "Unison Detune", "Unison Voices"],
    9: ["Exciter", "Exciter Level", "String 1 Decay", "String 2 Decay", "String 2 Detune",
        "String Balance", "Stiffness"],
    10: ["Engine", "Detune", "Square Shape", "Saw Shape", "Sync Mix", "LPG Response",
         "LPG Decay"],
    11: ["Shape", "Wrap", "Mask", "Threshold", "Bitcrush", "Unison Detune", "Unison Voices"],
}


def generic_names(names: list[str], patch: "Patch | None" = None) -> list[str]:
    """Translate type-specific oscillator labels ("A Osc 1 M1 Amount") to the map's names
    ("A Osc 1 Shape"), by each oscillator's type in ``patch`` (Classic when no patch)."""
    out = []
    for name in names:
        fx = re.fullmatch(r"FX ([ABSG][1-4]) (.+)", name.strip(), re.IGNORECASE)
        if fx and patch is not None and not re.fullmatch(r"(FX Type|Param \d+)", fx.group(2),
                                                         re.IGNORECASE):
            number = FX_SLOTS.index(fx.group(1).upper()) + 1
            kind = patch.params.find(f"fx{number}_type")
            try:
                k = fx_control(int(kind.get("value", "0")) if kind is not None else 0,
                               fx.group(2))
                out.append(f"FX {fx.group(1).upper()} Param {k + 1}")
                continue
            except ValueError:
                pass
        match = re.fullmatch(r"([AB]) Osc ([123]) (.+)", name.strip(), re.IGNORECASE)
        if not match:
            out.append(name)
            continue
        scene, number, label = match.group(1).upper(), match.group(2), match.group(3)
        kind = patch.params.find(f"{scene.lower()}_osc{number}_type") if patch else None
        osc_type = int(float(kind.get("value", "0"))) if kind is not None else 0
        labels = [x.lower() for x in OSC_LABELS.get(osc_type, GENERIC_OSC_LABELS)]
        if label.lower() in labels and label.lower() not in \
                [x.lower() for x in GENERIC_OSC_LABELS]:
            label = GENERIC_OSC_LABELS[labels.index(label.lower())]
        out.append(f"{scene} Osc {number} {label}")
    return out


# Patch value -> Live's 0..1 value. Floats are linear in the patch's units between these
# ends (measured in Live by local Claude: display at 0 / 0.5 / 1 of every sound_design control,
# converted: Hz -> semitones from 440, rates and times -> log2, % -> fraction).
_FLOAT_RANGES = [
    (r"[ab]_filter[12]_cutoff", -60.0, 70.0), (r"[ab]_lowcut", -72.0, 15.0),
    (r"[ab]_filter[12]_resonance", 0.0, 1.0), (r"[ab]_filter[12]_envmod", -96.0, 96.0),
    (r"[ab]_filter[12]_keytrack", -1.0, 1.0), (r"[ab]_f_balance", -1.0, 1.0),
    (r"[ab]_feedback", -1.0, 1.0), (r"[ab]_ws_drive", -24.0, 24.0),
    (r"[ab]_noisecol", -1.0, 1.0), (r"[ab]_fm_depth", -48.0, 24.0),
    (r"[ab]_osc[123]_pitch", -7.0, 7.0), (r"[ab]_pitch", -7.0, 7.0),
    (r"[ab]_env[12]_(attack|decay|release)", -8.0, 5.0), (r"[ab]_env[12]_sustain", 0.0, 1.0),
    (r"[ab]_lfo\d+_rate", -7.0, 9.0), (r"[ab]_lfo\d+_(magnitude|phase)", 0.0, 1.0),
    (r"[ab]_lfo\d+_deform", -1.0, 1.0), (r"[ab]_portamento", -8.0, 2.0),
    (r"[ab]_drift", 0.0, 1.0), (r"[ab]_(width|pan)", -1.0, 1.0), (r"volume", -48.0, 0.0),
    # levels: 0..1 in the patch, shown as dB on a curve (measured 0.5 = -18 dB, 1 = 0 dB)
    (r"[ab]_level_(o[123]|noise|ring12|ring23)", 0.0, 1.0), (r"[ab]_volume", 0.0, 1.0),
    # sends: dB = 18 * log2(value), up to +12 dB = 2^(2/3) (measured by local Claude)
    (r"[ab]_send_fx_[1-4]", 0.0, 2 ** (2 / 3)),
]
_MENUS = [  # (pattern, first value, count)
    (r"[ab]_filter[12]_type", 0, 36), (r"[ab]_filter[12]_subtype", 0, 16),
    (r"[ab]_osc[123]_type", 0, 12), (r"fx\d+_type", 0, 32),
    (r"[ab]_(osc[123]_)?octave", -3, 7), (r"[ab]_lfo\d+_trigmode", 0, 3),
    (r"[ab]_lfo\d+_unipolar", 0, 2), (r"[ab]_mute_(o[123]|noise)", 0, 2),
]


def _convert(spec: Any, patch_value: float) -> float | None:
    if spec is None:
        return None
    if spec[0] == "menu":
        _kind, first, count = spec
        index = min(count - 1, max(0, int(round(float(patch_value))) - first))
        return 0.005 + 0.99 * index / (count - 1)
    low, high = spec
    return min(1.0, max(0.0, (float(patch_value) - low) / (high - low)))


def host_value(storage_name: str, patch_value: float, osc_type: int | None = None,
               fx_type: int | None = None) -> float | None:
    """Live's 0..1 value of a patch value, or None when the conversion is not known.
    Oscillator controls (``a_osc1_param0``...) need the oscillator's type, FX controls
    (``fx1_p0``...) the effect's type."""
    match = re.fullmatch(r"fx\d+_p(\d+)", storage_name)
    if match:
        entries = FX_PARAMS.get(fx_type) if fx_type is not None else None
        return _convert(entries[int(match.group(1))][1], patch_value) \
            if entries and int(match.group(1)) < 12 else None
    match = re.fullmatch(r"[ab]_osc[123]_param([0-6])", storage_name)
    if match:
        ranges = OSC_PARAM_RANGES.get(osc_type) if osc_type is not None else None
        return _convert(ranges[int(match.group(1))], patch_value) if ranges else None
    for pattern, low, high in _FLOAT_RANGES:
        if re.fullmatch(pattern, storage_name):
            return min(1.0, max(0.0, (float(patch_value) - low) / (high - low)))
    for pattern, first, count in _MENUS:
        if re.fullmatch(pattern, storage_name):
            index = min(count - 1, max(0, int(round(float(patch_value))) - first))
            return 0.005 + 0.99 * index / (count - 1)
    return None


def host_values(patch: "Patch", storage_names: dict[str, str], names: list[str]
                ) -> tuple[dict[str, float], list[str]]:
    """{exposed name: 0..1 value from the patch} and the names that cannot be converted."""
    values, skipped = {}, []
    for name in names:
        key = storage_names.get(name)
        element = patch.params.find(key) if key else None
        osc_type = None
        match = re.fullmatch(r"([ab]_osc[123])_param\d", key or "")
        if match:
            kind = patch.params.find(f"{match.group(1)}_type")
            osc_type = int(float(kind.get("value", "0"))) if kind is not None else None
        fx_type = None
        match = re.fullmatch(r"(fx\d+)_p\d+", key or "")
        if match:
            kind = patch.params.find(f"{match.group(1)}_type")
            fx_type = int(float(kind.get("value", "0"))) if kind is not None else None
        if fx_type is not None and _unused_fx_control(fx_type, key):
            continue                                  # shows "-": nothing to set
        value = host_value(key, float(element.get("value", "0")), osc_type, fx_type) \
            if element is not None else None
        if value is None:
            skipped.append(name)
        else:
            values[name] = round(value, 6)
    return values, skipped


_UNIT_TEXT = re.compile(r"\s*([-+]?(?:\d+\.?\d*|\.\d+)|-inf)\s*(ms|s|khz|hz|%|cents?|ct|db)\s*",
                        re.IGNORECASE)


def fx_patch_value(fx_type: int, k: int, text: str) -> float:
    """An FX control's patch value from text with a unit: "2 s", "250 ms", "1.5 kHz",
    "30 %", "-6 dB", "10 cents" (Hz controls -> semitones from 440, times and rates -> log2,
    % -> fraction, levels -> Surge's level curve)."""
    import math
    if (fx_type, k) in FX_MENUS:
        return float(_list_index(FX_MENUS[(fx_type, k)], text, "entry"))
    kind = fx_unit(fx_type, k)
    match = _UNIT_TEXT.fullmatch(text)
    if kind is None or not match:
        raise ValueError(f"{text!r}: give a number in the patch's units"
                         + ("" if kind is None else f" or a value with its unit ({_UNIT_HINT[kind]})"))
    number, unit = match.group(1), match.group(2).lower()
    x = float("-inf") if number == "-inf" else float(number)
    if kind == "time" and unit in ("s", "ms") and x > 0:
        return math.log2(x / 1000 if unit == "ms" else x)
    if kind in ("rate", "hz", "note") and unit in ("hz", "khz") and x > 0:
        hz = x * 1000 if unit == "khz" else x
        return {"rate": math.log2(hz), "hz": 12 * math.log2(hz / 440),
                "note": 69 + 12 * math.log2(hz / 440)}[kind]
    if kind == "percent" and unit == "%":
        return x / 100
    if kind == "cents" and unit in ("cent", "cents", "ct"):
        return x / 100
    if kind == "db" and unit == "db":
        return x
    if kind == "amp" and unit == "db":
        return 0.0 if x == float("-inf") else 2 ** (x / 18)
    raise ValueError(f"{text!r} does not fit this control ({_UNIT_HINT[kind]})")


_UNIT_HINT = {"time": "s or ms", "rate": "Hz", "hz": "Hz or kHz", "note": "Hz",
              "percent": "%", "cents": "cents", "db": "dB", "amp": "dB"}


def _unused_fx_control(fx_type: int, storage_name: str) -> bool:
    match = re.fullmatch(r"fx\d+_p(\d+)", storage_name)
    entries = FX_PARAMS.get(fx_type)
    return bool(match and entries and int(match.group(1)) < 12
                and entries[int(match.group(1))][0] is None)


def fx_slot(slot: int | str) -> int:
    """An FX slot as its patch number 1-16 (fx1..fx16) from 1-16 or "A1".."G4"."""
    if isinstance(slot, str) and slot.strip().upper() in FX_SLOTS:
        return FX_SLOTS.index(slot.strip().upper()) + 1
    if isinstance(slot, str) and slot.strip().isdigit():
        slot = int(slot.strip())
    if isinstance(slot, int) and 1 <= slot <= 16:
        return slot
    raise ValueError(f"FX slot {slot!r}: 1-16 (patch order) or A1-A4, B1-B4, S1-S4 "
                     "(sends), G1-G4 (global)")


def fx_control(fx_type: int, key: str | int) -> int:
    """The index 0-11 of an FX control: its name for that type, "p3", or 3."""
    if isinstance(key, int) or (isinstance(key, str) and key.strip().isdigit()):
        k = int(key)
    else:
        match = re.fullmatch(r"\s*p(?:aram)?\s*(\d+)\s*", str(key), re.IGNORECASE)
        names = [label for label, _range in FX_PARAMS.get(fx_type, [])]
        wanted = {_norm(str(key)), _norm(str(key).split(" - ")[-1])}
        hits = [i for i, label in enumerate(names) if label and _norm(label) in wanted]
        if hits:
            return hits[0]
        if not match:
            label = FX_TYPES[fx_type] if 0 <= fx_type < len(FX_TYPES) else fx_type
            raise ValueError(f"{label} has no control {key!r} — one of: "
                             + ", ".join(n for n in names if n) + " (or p0-p11)")
        k = int(match.group(1))
    if not 0 <= k < 12:
        raise ValueError("FX controls are p0-p11")
    return k


def _norm(text: str) -> str:
    return re.sub(r"[\s\-_]", "", text.lower()).replace("notch", "n")


def _list_index(names: list[str], value: str, what: str) -> int:
    for index, label in enumerate(names):
        if _norm(label) == _norm(value):
            return index
    raise ValueError(f"unknown {what} {value!r} — one of: " + ", ".join(names))


def _filter_index(patch: "Patch", scene: str, number: str, kind: str, name: str) -> int:
    if kind == "type":
        for index, (label, _subs) in enumerate(FILTER_TYPES):
            if _norm(label) == _norm(name):
                return index
        raise ValueError(f"unknown filter type {name!r} — one of: "
                         + ", ".join(label for label, _s in FILTER_TYPES))
    type_index = int(float(patch.parameter(f"{scene}_filter{number}_type").get("value", "0")))
    label, subtypes = FILTER_TYPES[type_index] if 0 <= type_index < len(FILTER_TYPES) \
        else ("?", [])
    for index, sub in enumerate(subtypes):
        if _norm(sub) == _norm(name):
            return index
    raise ValueError(f"{label} has no subtype {name!r}"
                     + (" — one of: " + ", ".join(subtypes) if subtypes else
                        " (it has none)") + "; set the type first")


def wavetable_block(data: bytes) -> tuple[bytes, int, int, int]:
    """A ``.wt`` file (``vawt``, u32 samples per frame, u16 frames, u16 flags, samples) as
    the block a patch embeds (same header, a zero word in place of ``vawt``)."""
    if len(data) < 12 or data[:4] != b"vawt":
        raise ValueError("not a Surge wavetable (.wt files start with 'vawt')")
    length, frames, flags = struct.unpack_from("<IHH", data, 4)
    if flags & 1:
        raise ValueError("this .wt holds a sample, not a wavetable")
    if not (length and length & (length - 1) == 0 and length <= 4096) or not 1 <= frames <= 512:
        raise ValueError(f"unsupported wavetable: {frames} frames of {length} samples")
    width = 2 if flags & 4 else 4
    size = 12 + frames * length * width
    if len(data) < size:
        raise ValueError("the .wt file is truncated")
    return bytes(4) + data[4:size], frames, length, flags


def _note_length(value: float) -> str:
    """The note length a synced rate names ("1/16", "1/8d" ...), or its value at 120 BPM."""
    for top, bottom in ((4, 1), (2, 1), (1, 1), (1, 2), (1, 4), (1, 8), (1, 16), (1, 32),
                        (1, 64)):
        for suffix in ("", "d", "t"):
            label = f"{top}/{bottom}{suffix}"
            if abs(synced_rate(label) - value) < 1e-4:
                return label
    return f"{2 ** value:.3g} Hz at 120 BPM (synced)"


def _lfo_label(index: int) -> str:
    return f"lfo{index + 1}" if index < 6 else f"slfo{index - 5}"


def _num(value: float) -> str:
    return format(value, ".8g")
