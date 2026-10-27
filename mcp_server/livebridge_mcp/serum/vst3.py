"""VST3 preset container (``.vstpreset``), after the public Steinberg layout.

Ported from ableton-mcp-pro (MIT, Copyright (c) 2026 nicholasbien; Serum preset work by
kasperikaivola, github.com/kasperikaivola/ableton-mcp-pro commit 6ac32c8) — see
``LICENSE-ableton-mcp-pro.txt`` next to this file. 
"""

from __future__ import annotations

import struct


def read_vst3_preset(raw: bytes) -> tuple[bytes, dict]:
    if len(raw) < 48 or raw[:4] != b"VST3" or raw[4:8] != b"\x01\x00\x00\x00":
        raise ValueError("Expected a VST3 preset container version 1.")
    class_id = raw[8:40]
    try:
        int(class_id, 16)
    except ValueError as exc:
        raise ValueError("VST3 class id is not hex.") from exc
    offset = struct.unpack_from("<q", raw, 40)[0]
    if not 48 <= offset <= len(raw) - 8 or raw[offset:offset + 4] != b"List":
        raise ValueError("Invalid VST3 chunk list.")
    count = struct.unpack_from("<i", raw, offset + 4)[0]
    if not 1 <= count <= 128 or offset + 8 + count * 20 != len(raw):
        raise ValueError("Invalid VST3 chunk list.")
    chunks = {}
    spans = []
    for index in range(count):
        tag, start, size = struct.unpack_from("<4sqq", raw, offset + 8 + index * 20)
        if tag in chunks or start < 48 or size <= 0 or start + size > offset:
            raise ValueError("Invalid or duplicate VST3 chunk.")
        if any(start < end and start + size > begin for begin, end in spans):
            raise ValueError("Overlapping VST3 chunks.")
        spans.append((start, start + size))
        chunks[tag] = raw[start:start + size]
    return class_id, chunks


def write_vst3_preset(class_id: bytes, chunks: dict) -> bytes:
    if len(class_id) != 32:
        raise ValueError("VST3 class id must be 32 bytes.")
    payload = bytearray(b"VST3" + struct.pack("<i", 1) + class_id + b"\0" * 8)
    entries = []
    for tag, chunk in chunks.items():
        if len(tag) != 4:
            raise ValueError("VST3 chunk tags must be 4 bytes.")
        entries.append((tag, len(payload), len(chunk)))
        payload.extend(chunk)
    struct.pack_into("<q", payload, 40, len(payload))
    payload.extend(b"List" + struct.pack("<i", len(entries)))
    for tag, start, size in entries:
        payload.extend(struct.pack("<4sqq", tag, start, size))
    written = bytes(payload)
    if read_vst3_preset(written) != (class_id, chunks):
        raise ValueError("VST3 preset container did not round-trip.")
    return written
