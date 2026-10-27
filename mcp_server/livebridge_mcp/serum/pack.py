"""Serum 2 state packed into a same-build ``.vstpreset`` Live's browser can load.

Ported from ableton-mcp-pro (MIT, Copyright (c) 2026 nicholasbien; Serum preset work by
kasperikaivola, github.com/kasperikaivola/ableton-mcp-pro commit 6ac32c8) — see
``LICENSE-ableton-mcp-pro.txt`` next to this file. The template is a
``.vstpreset`` the user saved from their own Serum 2 (same build): it gives the VST3
class id and the processor/controller schemas the new state is laid into.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import struct

from . import codec
from .vst3 import read_vst3_preset, write_vst3_preset


METADATA_KEYS = {"fileType", "presetName", "presetAuthor", "presetDescription"}


def unpack_serum_state(raw: bytes) -> tuple[dict, dict]:
    if not raw.startswith(codec.MAGIC):
        raise ValueError("Expected Serum XferJson state.")
    length = struct.unpack_from("<Q", raw, 9)[0]
    end = 17 + length
    decoded, encoding = struct.unpack_from("<II", raw, end)
    if encoding != 2 or decoded > codec.MAX_BYTES:
        raise ValueError("Unsupported Serum state payload.")
    cbor, zstd = codec.libraries()
    data = zstd.ZstdDecompressor().decompress(raw[end + 8:], max_output_size=decoded)
    if len(data) != decoded:
        raise ValueError("Serum state length mismatch.")
    return json.loads(raw[17:end]), cbor.loads(data)


def pack_serum_state(meta: dict, data: dict) -> bytes:
    cbor, zstd = codec.libraries()
    payload = cbor.dumps(data)
    compressed = zstd.ZstdCompressor(level=3).compress(payload)
    # Native Serum host presets store an MD5 of the compressed component.
    stored = dict(meta, hash=hashlib.md5(compressed).hexdigest())
    header = json.dumps(stored, ensure_ascii=False, separators=(",", ":")).encode()
    return (
        codec.MAGIC
        + struct.pack("<Q", len(header))
        + header
        + struct.pack("<II", len(payload), 2)
        + compressed
    )


def pack_serum2(source: Path, template: Path) -> tuple[bytes, dict]:
    source, preset, source_sha, _kind = codec.load(str(source))
    template = template.resolve(strict=True)
    template_raw = template.read_bytes()
    class_id, chunks = read_vst3_preset(template_raw)
    schemas = {}
    for tag, component in ((b"Comp", "processor"), (b"Cont", "controller")):
        if tag not in chunks:
            raise ValueError("Template is not a Serum processor/controller pair.")
        meta, data = unpack_serum_state(chunks[tag])
        if meta.get("product") != "Serum2" or meta.get("component") != component:
            raise ValueError("Template is not a Serum processor/controller pair.")
        for key in ("productVersion", "version"):
            if meta.get(key) != preset["metadata"].get(key):
                raise ValueError(
                    f"Source/template {key} differs; no cross-build conversion."
                )
        schemas[tag] = meta, data
    known = set().union(*(data for _meta, data in schemas.values()))
    unsupported = set(preset["data"]) - known - METADATA_KEYS
    if unsupported:
        names = ", ".join(sorted(unsupported))
        raise ValueError(f"Source fields absent from template schemas: {names}.")
    for tag, (meta, baseline) in schemas.items():
        data = {
            key: copy.deepcopy(preset["data"].get(key, value))
            for key, value in baseline.items()
        }
        data["component"] = meta["component"]
        if tag == b"Cont":
            for key in ("presetName", "presetAuthor", "presetDescription"):
                value = preset["metadata"].get(key, "")
                meta[key] = value
                if key in data:
                    data[key] = value
            if "presetHasBeenEdited" in data:
                data["presetHasBeenEdited"] = False
        chunks[tag] = pack_serum_state(meta, data)
    payload = write_vst3_preset(class_id, chunks)
    if codec.digest(source.read_bytes()) != source_sha:
        raise ValueError("Source changed while packaging.")
    if codec.digest(template.read_bytes()) != codec.digest(template_raw):
        raise ValueError("Template changed while packaging.")
    return payload, {
        "version": preset["metadata"].get("productVersion"),
        "preset_name": preset["metadata"].get("presetName") or source.stem,
        "source_sha256": source_sha,
        "template_sha256": codec.digest(template_raw),
        "class_id": class_id.decode(),
    }
