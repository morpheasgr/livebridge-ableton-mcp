"""Native Instruments Massive (classic) patch files: read and write the modulation routings.

Works on the ``.vstpreset`` Live saves when the Massive device is dragged into the User
Library. Live reaches Massive's knobs, selectors and wavetables by name; what it cannot reach
is the modulation matrix (which source drives which knob, and how far). This module edits
exactly that in a copy of a template preset and keeps every other byte.

Format (see ``docs/MASSIVE_FORMAT.md``, worked out from the user's own labelled presets):
the VST3 ``Comp`` chunk holds NI's nested container; its ``DSINs`` chunk carries the patch as
``<u8 1><u32 raw size><u32 packed size>`` + a FastLZ level-1 stream. In the unpacked patch,
each modulatable knob has a record ``u32 4, f32 lo, f32 hi, u32 n`` then ``n`` slots of
``f32 amount, i32 source (-1 = empty), 2 flag bytes``, closed by the knob's ``u32`` index in
``remote_script/LiveBridge/plugin_maps/massive_vst3.json``. Slot counts are fixed, so edits
never change the patch's size.

Pure Python, no Live calls. The FastLZ code was written for LiveBridge from the public format
description.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

CLASS_ID = "5653544E694D616D6173736976650000"
_MAP = Path(__file__).resolve().parents[2] / "remote_script" / "LiveBridge" / "plugin_maps" / \
    "massive_vst3.json"

SOURCES: dict[str, int] = {
    **{f"env{i}": i - 1 for i in range(1, 5)},
    **{f"slot{i}": i - 1 for i in range(5, 9)},
    **{f"macro{i}": 12 + i for i in range(1, 9)},
    "ktr": 21, "vel": 22, "trr": 23, "at": 24,
}
_ALIASES = {**{f"envelope{i}": f"env{i}" for i in range(1, 5)},
            **{f"lfo{i}": f"slot{i}" for i in range(5, 9)},
            **{f"mod{i}": f"slot{i}" for i in range(5, 9)},
            "keytrack": "ktr", "velocity": "vel", "trigger": "trr", "aftertouch": "at"}
SOURCE_NAMES = {number: name for name, number in SOURCES.items()}
PITCH_RANGE_ST = 128.0
CURVES = ("sine", "saw", "square", "triangle")         # Massive's curve buttons, in order
SLOT_TYPES = {0: "lfo", 1: "performer", 2: "stepper"}          # a pitch amount of 1.0 is 128 semitones


# ---------------------------------------------------------------- FastLZ level 1

def fastlz_decompress(src: bytes, size: int) -> bytes:
    out, ip = bytearray(), 0
    ctrl = src[ip] & 31
    ip += 1
    while True:
        if ctrl >= 32:                               # match: length, then distance back
            length = (ctrl >> 5) - 1
            if length == 6:
                length += src[ip]
                ip += 1
            ref = len(out) - ((ctrl & 31) << 8) - 1 - src[ip]
            ip += 1
            if ref < 0:
                raise ValueError("corrupt FastLZ stream")
            for _ in range(length + 3):
                out.append(out[ref])
                ref += 1
        else:                                        # literal run of ctrl + 1 bytes
            out += src[ip:ip + ctrl + 1]
            ip += ctrl + 1
        if ip >= len(src) or len(out) >= size:
            return bytes(out)
        ctrl = src[ip]
        ip += 1


def fastlz_compress(data: bytes) -> bytes:
    """Greedy LZ77 in FastLZ level-1 form (3..264-byte matches, up to 8192 back)."""
    out = bytearray()
    literals = bytearray()
    table: dict[bytes, int] = {}

    def flush() -> None:
        for start in range(0, len(literals), 32):
            run = literals[start:start + 32]
            out.append(len(run) - 1)
            out.extend(run)
        literals.clear()

    i, n = 0, len(data)
    while i < n:
        best = 0
        if i + 3 <= n:
            key = data[i:i + 3]
            ref = table.get(key)
            table[key] = i
            if ref is not None and 0 < i - ref <= 8192:
                limit = min(264, n - i)
                while best < limit and data[ref + best] == data[i + best]:
                    best += 1
        if best >= 3 and i > 0:
            flush()
            distance = i - ref - 1
            if best <= 8:
                out.append(((best - 2) << 5) | (distance >> 8))
            else:
                out.append((7 << 5) | (distance >> 8))
                out.append(best - 9)
            out.append(distance & 255)
            for k in range(i + 1, min(i + best, n - 2)):
                table[data[k:k + 3]] = k
            i += best
        else:
            literals.append(data[i])
            i += 1
    flush()
    return bytes(out)


# ---------------------------------------------------------------- containers

def parameter_names() -> list[str]:
    params = json.loads(_MAP.read_text(encoding="utf-8"))["parameters"]
    names: list[str] = []
    for entry in params:                       # [name, index] pairs (or {"name", "index"})
        name, index = (entry["name"], entry["index"]) if isinstance(entry, dict) else entry
        names.extend([""] * (int(index) + 1 - len(names)))
        names[int(index)] = str(name)
    return names


def _vstpreset(data: bytes) -> tuple[str, dict[str, bytes]]:
    if len(data) < 48 or data[:4] != b"VST3":
        raise ValueError("not a .vstpreset file")
    class_id = data[8:40].decode("ascii", "replace").upper()
    offset = struct.unpack_from("<q", data, 40)[0]
    if data[offset:offset + 4] != b"List":
        raise ValueError(".vstpreset has no chunk list")
    chunks = {}
    for k in range(struct.unpack_from("<i", data, offset + 4)[0]):
        entry = data[offset + 8 + 20 * k:offset + 28 + 20 * k]
        start, size = struct.unpack_from("<qq", entry, 4)
        chunks[entry[:4].decode("ascii", "replace")] = data[start:start + size]
    return class_id, chunks


def _build_vstpreset(class_id: str, chunks: dict[str, bytes]) -> bytes:
    body, table = b"", []
    for chunk_id, payload in chunks.items():
        table.append((chunk_id, 48 + len(body), len(payload)))
        body += payload
    data = b"VST3" + struct.pack("<i", 1) + class_id.encode("ascii") + \
        struct.pack("<q", 48 + len(body)) + body + b"List" + struct.pack("<i", len(table))
    for chunk_id, start, size in table:
        data += chunk_id.encode("ascii") + struct.pack("<qq", start, size)
    return data


def _patch_location(comp: bytes) -> tuple[int, int, int]:
    """(header offset, raw size, packed size) of the compressed patch in the Comp chunk."""
    at = comp.find(b"DSINs")
    if at < 0:
        raise ValueError("no Massive patch in this preset (DSINs chunk missing)")
    for o in range(at, min(at + 64, len(comp) - 9)):
        if comp[o] == 1:
            raw, packed = struct.unpack_from("<II", comp, o + 1)
            if 0 < packed < raw < 1 << 24 and o + 9 + packed <= len(comp):
                return o, raw, packed
    raise ValueError("the Massive patch header was not found")


def _size_fields(comp: bytes, start: int, end: int) -> list[int]:
    """Offsets of the u64 block/chunk sizes whose extent covers comp[start:end]."""
    fields = []
    for p in range(0, start - 11):
        tag = comp[p + 8:p + 12]
        if tag == b"DSIN" or (tag == b"\x01\x00\x00\x00" and comp[p + 12:p + 16] == b"hsin"):
            size = struct.unpack_from("<Q", comp, p)[0]
            if p + size >= end and p + size <= len(comp):
                fields.append(p)
    return fields


class Patch:
    """A Massive ``.vstpreset`` with its unpacked patch and routing records."""

    def __init__(self, data: bytes):
        self.class_id, self.chunks = _vstpreset(data)
        if self.class_id != CLASS_ID:
            raise ValueError("this .vstpreset is not Massive (classic VST3)")
        comp = self.chunks.get("Comp") or b""
        self.header, raw, packed = _patch_location(comp)
        self.payload = bytearray(fastlz_decompress(comp[self.header + 9:
                                                        self.header + 9 + packed], raw))
        if len(self.payload) != raw:
            raise ValueError("the Massive patch did not unpack to its stated size")
        self.records = _records(self.payload)

    @classmethod
    def load(cls, path: str | Path) -> "Patch":
        return cls(Path(path).read_bytes())

    def routings(self) -> list[dict[str, Any]]:
        names = parameter_names()
        out = []
        for index, slots in sorted(self.records.items()):
            for slot, (offset, _lo, _hi) in enumerate(slots):
                amount, source = struct.unpack_from("<fi", self.payload, offset)
                if source != -1:
                    entry = {"target": names[index] if index < len(names) else index,
                             "source": SOURCE_NAMES.get(source, source),
                             "amount": round(amount, 4), "slot": slot + 1}
                    if len(slots) == 3 and slot == 2:
                        entry["slot"] = "sc"          # the sidechain slot (manual 3.2.3)
                    if _is_pitch(entry["target"]):
                        entry["semitones"] = round(amount * PITCH_RANGE_ST, 2)
                    out.append(entry)
        return out

    def slots(self) -> dict[int, dict[str, Any]]:
        """Mod slots 5-8: {slot: {type, curve_a, curve_b} | {type: "stepper", steps}}."""
        out = {}
        for k, (base, kind) in enumerate(_slot_walk(self.payload)):
            if kind == 0:
                curve_a, curve_b = struct.unpack_from("<ii", self.payload, base + 16)
                out[5 + k] = {"type": "lfo", "curve_a": CURVES[curve_a],
                              "curve_b": CURVES[curve_b]}
            else:
                steps = struct.unpack_from("<16f", self.payload, base + 8)
                glide = [n + 1 for n in range(16) if self.payload[base + 72 + n]]
                out[5 + k] = {"type": "stepper", "steps": [round(v, 4) for v in steps],
                              "glide": glide}
        return out

    def set_slot(self, slot: int, type: str | None = None, curve_a: str | None = None,
                 curve_b: str | None = None, steps: list[float] | None = None,
                 glide: list[int] | None = None) -> dict[str, Any]:
        """Change mod slot 5-8: its type (lfo/stepper), LFO curves, stepper steps."""
        if slot not in (5, 6, 7, 8):
            raise ValueError("slot must be 5..8")
        before = self.slots()[slot]
        change: dict[str, Any] = {"slot": slot}
        if type is not None:
            kind = str(type).strip().lower()
            if kind not in ("lfo", "stepper"):
                raise ValueError("type must be lfo or stepper (performer is not supported "
                                 "yet)")
            if kind != before["type"]:
                base = _slot_walk(self.payload)[slot - 5][0]
                old, new = (_LFO_BODY, _STEPPER_BODY) if kind == "stepper" else \
                    (_STEPPER_BODY, _LFO_BODY)
                fields = _payload_size_fields(self.payload)
                struct.pack_into("<i", self.payload, base, 2 if kind == "stepper" else 0)
                self.payload[base + 4:base + 4 + len(old)] = new
                for p in fields:
                    struct.pack_into("<Q", self.payload, p,
                                     struct.unpack_from("<Q", self.payload, p)[0] +
                                     len(new) - len(old))
                self.records = _records(self.payload)
                change.update(type=kind, type_before=before["type"])
        now = self.slots()[slot]
        base = _slot_walk(self.payload)[slot - 5][0]
        if curve_a is not None or curve_b is not None:
            if now["type"] != "lfo":
                raise ValueError(f"slot {slot} is a {now['type']}: curves are for LFO slots")
            for offset, value, key in ((16, curve_a, "curve_a"), (20, curve_b, "curve_b")):
                if value is None:
                    continue
                name = str(value).strip().lower().replace("saw down", "saw")
                if name not in CURVES:
                    raise ValueError(f"{key} must be one of {', '.join(CURVES)}")
                struct.pack_into("<i", self.payload, base + offset, CURVES.index(name))
                change.update({key: name, key + "_before": now[key]})
        if steps is not None:
            if now["type"] != "stepper":
                raise ValueError(f"slot {slot} is an LFO: steps need type='stepper'")
            values = [float(v) for v in steps]
            if not 1 <= len(values) <= 16 or not all(0.0 <= v <= 1.0 for v in values):
                raise ValueError("steps must be 1..16 values 0..1 (0 = bottom)")
            struct.pack_into(f"<{len(values)}f", self.payload, base + 8, *values)
            change.update(steps=[round(v, 4) for v in values], steps_before=now["steps"])
        if glide is not None:
            if now["type"] != "stepper":
                raise ValueError(f"slot {slot} is an LFO: glide needs type='stepper'")
            numbers = sorted({int(n) for n in glide})
            if not all(1 <= n <= 16 for n in numbers):
                raise ValueError("glide lists step numbers 1..16")
            for n in range(16):
                self.payload[base + 72 + n] = 1 if n + 1 in numbers else 0
            change.update(glide=numbers, glide_before=now["glide"])
        return change

    def targets(self) -> list[str]:
        names = parameter_names()
        return [names[i] for i in sorted(self.records) if i < len(names)]

    def route(self, target: str | int, source: str, amount: float | None = None,
              semitones: float | None = None, remove: bool = False) -> dict[str, Any]:
        index, name = _target_index(target)
        if index not in self.records:
            raise ValueError(f"{name} cannot be modulated in Massive (no routing slots)")
        number = source_number(source)
        if not remove:
            if semitones is not None:
                if not _is_pitch(name):
                    raise ValueError("semitones only applies to a pitch knob")
                amount = semitones / PITCH_RANGE_ST
            if amount is None:
                raise ValueError("give amount (-1..1) or semitones, or remove=true")
            if not -1.0 <= amount <= 1.0:
                raise ValueError("amount must be -1..1 (a fraction of the knob's range)")
        slots = [offset for offset, _lo, _hi in self.records[index]]
        current = [struct.unpack_from("<fi", self.payload, o) for o in slots]
        hit = next((k for k, (_a, s) in enumerate(current) if s == number), None)
        if remove:
            if hit is None:
                return {"target": name, "source": source, "changed": False,
                        "note": "was not routed"}
            struct.pack_into("<i", self.payload, slots[hit] + 4, -1)
            return {"target": name, "source": source, "removed": True, "slot": hit + 1,
                    "before": round(current[hit][0], 4)}
        # A knob with three slots has two normal ones and the sidechain slot ("SC"): with
        # SC on, the third scales the other two instead of adding, so it is never filled here.
        usable = 2 if len(slots) == 3 else len(slots)
        if hit is not None and hit >= usable:
            raise ValueError(f"{name}: {SOURCE_NAMES[number]} is in the sidechain (SC) slot — "
                             "remove it there first (remove=true) to route it normally")
        if hit is None:
            hit = next((k for k, (_a, s) in enumerate(current[:usable]) if s == -1), None)
            if hit is None:
                used = ", ".join(SOURCE_NAMES.get(s, str(s)) for _a, s in current[:usable])
                raise ValueError(f"{name} has no free slot ({usable} slots: {used}) — "
                                 "remove one first")
            before = None
        else:
            before = round(current[hit][0], 4)
        struct.pack_into("<fi", self.payload, slots[hit], float(amount), number)
        change = {"target": name, "source": SOURCE_NAMES[number], "amount": round(amount, 4),
                  "slot": hit + 1, "before": before}
        if _is_pitch(name):
            change["semitones"] = round(amount * PITCH_RANGE_ST, 2)
        return change

    def to_bytes(self) -> bytes:
        comp = self.chunks["Comp"]
        _raw, packed = struct.unpack_from("<II", comp, self.header + 1)
        start, end = self.header + 9, self.header + 9 + packed
        stream = fastlz_compress(bytes(self.payload))
        if fastlz_decompress(stream, len(self.payload)) != bytes(self.payload):
            raise AssertionError("FastLZ round trip failed")
        delta = len(stream) - packed
        out = bytearray(comp[:self.header + 1] + struct.pack("<II", len(self.payload),
                                                             len(stream)) + stream + comp[end:])
        for p in _size_fields(comp, start, end):
            struct.pack_into("<Q", out, p, struct.unpack_from("<Q", comp, p)[0] + delta)
        chunks = dict(self.chunks)
        chunks["Comp"] = bytes(out)
        return _build_vstpreset(self.class_id, chunks)


# A slot's data after its i32 type: an LFO's 20 bytes (2, 0, 0, curve A, curve B), or a
# stepper's 140 (2, 16 f32 steps, 16 per-step glide bytes, 2, 16 x 3 per-step bytes, 0) — the default
# stepper as Massive saves it (labelled presets 05 and 20).
_LFO_BODY = struct.pack("<5i", 2, 0, 0, 0, 1)
_STEPPER_BODY = struct.pack("<i", 2) + bytes(80) + struct.pack("<i", 2) + \
    b"\x00\x01\x00" * 16 + bytes(4)


def _slot_ok(payload: bytes, base: int) -> int | None:
    """The type at base if a valid LFO (0) or stepper (2) block starts there."""
    if base + 144 > len(payload):
        return None
    kind, two, z1, z2, a, b = struct.unpack_from("<6i", payload, base)
    if kind == 0 and two == 2 and z1 == z2 == 0 and 0 <= a <= 3 and 0 <= b <= 3:
        return 0
    if kind == 2 and two == 2 and struct.unpack_from("<i", payload, base + 88)[0] == 2 and \
            all(0.0 <= v <= 1.0 for v in struct.unpack_from("<16f", payload, base + 8)) and \
            set(payload[base + 92:base + 140]) <= {0, 1}:
        return 2
    return None


def _slot_walk(payload: bytes) -> list[tuple[int, int]]:
    """[(offset, type)] of mod slots 5-8 (LFOs and steppers; a performer stops the walk)."""
    hits = []
    for start in range(0, len(payload) - 96, 4):
        walk, base = [], start
        for _ in range(4):
            kind = _slot_ok(payload, base)
            if kind is None:
                break
            walk.append((base, kind))
            base += 24 if kind == 0 else 144
        if len(walk) == 4:
            hits.append(walk)
    if len(hits) != 1:
        raise ValueError("mod slots 5-8 were not found (they must be LFOs or steppers; a "
                         "performer slot is not supported yet)")
    return hits[0]


def _payload_size_fields(payload: bytes) -> list[int]:
    """The five u64 sizes in the unpacked patch that cover the mod slots."""
    if payload[96:100] != b"hsin" or payload[132:136] != b"DSIN" or \
            payload[200:204] != b"tsrp":
        raise ValueError("unexpected patch layout: changing a slot type is not safe here")
    return [0, 84, 124, 180, 192]


def _records(payload: bytes) -> dict[int, list[tuple[int, float, float]]]:
    """{map index: [(slot offset, lo, hi)]} for every knob record in the patch."""
    found: dict[int, list[tuple[int, float, float]]] = {}
    o = 0
    while o < len(payload) - 30:
        if payload[o:o + 4] == b"\x04\x00\x00\x00":
            lo, hi, count = struct.unpack_from("<ffI", payload, o + 4)
            if 1 <= count <= 4 and -1000.0 < lo < hi < 1000.0:
                p, slots = o + 16, []
                for _ in range(count):
                    amount, source = struct.unpack_from("<fi", payload, p)
                    if not -1 <= source <= 64 or not -16.0 <= amount <= 16.0:
                        break
                    slots.append((p, lo, hi))
                    p += 10
                else:
                    index = struct.unpack_from("<i", payload, p)[0]
                    if 0 <= index < 512 and index not in found:
                        found[index] = slots
                    o = p
                    continue
        o += 1
    return found


def source_number(source: str | int) -> int:
    if isinstance(source, int) and source in SOURCE_NAMES:
        return source
    key = str(source).strip().lower().replace(" ", "").replace("_", "")
    key = _ALIASES.get(key, key)
    if key not in SOURCES:
        raise ValueError(f"unknown source {source!r} — use env1-4, slot5-8 (LFO/stepper/"
                         "performer slots), macro1-8, ktr, vel, trr or at")
    return SOURCES[key]


def _target_index(target: str | int) -> tuple[int, str]:
    names = parameter_names()
    if isinstance(target, int):
        if not 0 <= target < len(names):
            raise ValueError(f"no Massive parameter #{target}")
        return target, names[target]
    want = target.strip().lower()
    for index, name in enumerate(names):
        if name.lower() == want:
            return index, name
    raise ValueError(f"no Massive parameter named {target!r} (use the names of "
                     "live_plugin_param_map, e.g. 'FILTER1-CUT/PRM.1')")


def _is_pitch(name: Any) -> bool:
    return isinstance(name, str) and name.upper().endswith("-PITCH")
