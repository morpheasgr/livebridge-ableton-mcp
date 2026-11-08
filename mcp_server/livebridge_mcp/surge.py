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
        self.tail = chunk[32 + xml_size:]           # wavetables + nightly data, kept as is
        if sum(self.wavetable_sizes) > len(self.tail):
            raise ValueError("the patch is truncated (wavetables missing)")
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
            out.append({"scene": "ab"[int(seq.get("scene", "0"))],
                        "lfo": _lfo_label(int(seq.get("i", "0"))),
                        "steps": steps, "loop": [int(seq.get("loop_start", "0")) + 1,
                                                 int(seq.get("loop_end", "15")) + 1],
                        "shuffle": float(seq.get("shuffle", "0"))})
        return out

    def parameter(self, name: str) -> ET.Element:
        element = self.params.find(name)
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
        param = self.parameter(name)
        before = param.get("value")
        match = re.fullmatch(r"([ab])_filter([12])_(type|subtype)", name)
        if match and isinstance(value, str):
            value = _filter_index(self, match.group(1), match.group(2), match.group(3), value)
        elif isinstance(value, str):
            raise ValueError(f"{name}: give a number in the patch's units (names are only "
                             "understood for filter types and subtypes)")
        if param.get("type") == "0":
            param.set("value", str(int(value)))
        else:
            param.set("value", f"{float(value):.14f}")
        return {"parameter": name, "value": param.get("value"), "before": before}

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
        return list(dict.fromkeys(out))

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
            struct.pack("<6I", *self.wavetable_sizes) + xml + self.tail
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


def _norm(text: str) -> str:
    return re.sub(r"[\s\-_]", "", text.lower()).replace("notch", "n")


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


def _lfo_label(index: int) -> str:
    return f"lfo{index + 1}" if index < 6 else f"slfo{index - 5}"


def _num(value: float) -> str:
    return format(value, ".8g")
