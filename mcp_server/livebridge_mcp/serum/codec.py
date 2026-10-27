"""Serum 2 ``.SerumPreset`` container: decode, encode, guarded writes.

Ported from ableton-mcp-pro (MIT, Copyright (c) 2026 nicholasbien; Serum preset work by
kasperikaivola, github.com/kasperikaivola/ableton-mcp-pro commit 6ac32c8) — see
``LICENSE-ableton-mcp-pro.txt`` next to this file. The upstream note: an independent
implementation of the container documented by KennethWussmann/serum-preset-packager
(README); no upstream implementation is vendored. Re-encoding preserves decoded state,
not byte layout.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import struct
import tempfile

MAGIC = b"XferJson\0"
MAX_BYTES = 256 * 1024 * 1024
MAX_METADATA = 4 * 1024 * 1024


def libraries():
    try:
        import cbor2
        import zstandard
    except ImportError as exc:
        raise ValueError("Serum preset files need cbor2 and zstandard: pip install 'livebridge-mcp[serum]' (the LiveBridge installer adds them)") from exc
    return cbor2, zstandard


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def strict_json(raw):
    def invalid(value):
        raise ValueError(f"Non-finite JSON number: {value}")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    return json.loads(raw, parse_constant=invalid, object_pairs_hook=unique)


def validate(document):
    if not isinstance(document, dict) or set(document) != {"metadata", "data"}:
        raise ValueError("Preset document must contain exactly metadata and data.")
    meta = document["metadata"]
    if not isinstance(meta, dict) or not isinstance(document["data"], dict):
        raise ValueError("metadata and data must be objects.")
    if meta.get("fileType") != "SerumPreset" or meta.get("product") != "Serum2":
        raise ValueError("Only Serum2 SerumPreset documents are supported (not legacy FXP).")


def decode(raw: bytes) -> tuple[dict, str]:
    if len(raw) > MAX_BYTES:
        raise ValueError("Input exceeds the 256 MiB limit.")
    if not raw.startswith(MAGIC):
        obj = strict_json(raw.decode("utf-8-sig"))
        validate(obj)
        return obj, "json"
    if len(raw) < 25:
        raise ValueError("Truncated XferJson header.")
    meta_len = struct.unpack_from("<Q", raw, 9)[0]
    end = 17 + meta_len
    if meta_len > MAX_METADATA or end + 8 >= len(raw):
        raise ValueError("Invalid metadata length or missing compressed payload.")
    decoded_len, encoding = struct.unpack_from("<II", raw, end)
    if encoding != 2 or not 0 < decoded_len <= MAX_BYTES:
        raise ValueError("Unsupported payload encoding or decoded size (limit 256 MiB).")
    cbor2, zstd = libraries()
    compressed = raw[end + 8:]
    frame_size = zstd.frame_content_size(compressed)
    if frame_size not in (zstd.CONTENTSIZE_UNKNOWN, decoded_len):
        raise ValueError("Zstandard frame size differs from the declared payload size.")
    decoded = zstd.ZstdDecompressor().decompress(
        compressed, max_output_size=decoded_len, allow_extra_data=False
    )
    if len(decoded) != decoded_len:
        raise ValueError("Decoded payload length mismatch.")
    stream = io.BytesIO(decoded)
    data = cbor2.CBORDecoder(stream).decode()
    if stream.tell() != len(decoded):
        raise ValueError("Trailing data after the CBOR document.")
    obj = {"metadata": strict_json(raw[17:end].decode("utf-8")), "data": data}
    validate(obj)
    return obj, "preset"


def load(file_path: str):
    path = Path(file_path).expanduser().resolve(strict=True)
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError("Input must be a file no larger than 256 MiB.")
    raw = path.read_bytes()
    obj, kind = decode(raw)
    return path, obj, digest(raw), kind


def _json_compatible(value):
    # Reject bytes, non-string map keys, tags, sets and tuples explicitly rather
    # than let json.dumps silently convert a CBOR type or key.
    if type(value) in (str, int, float, bool, type(None)):
        return
    if type(value) is list:
        for item in value:
            _json_compatible(item)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _json_compatible(item)
        return
    raise ValueError("Non-JSON CBOR data detected. Use binary preset editing to preserve its types.")


def encode(obj: dict, output_format: str) -> bytes:
    validate(obj)
    if output_format == "json":
        _json_compatible(obj)
        raw = json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    elif output_format == "preset":
        cbor2, zstd = libraries()
        metadata = json.dumps(obj["metadata"], ensure_ascii=False,
                              separators=(",", ":"), allow_nan=False).encode("utf-8")
        payload = cbor2.dumps(obj["data"])
        if len(metadata) > MAX_METADATA or len(payload) > MAX_BYTES:
            raise ValueError("Encoded metadata or payload exceeds the size limit.")
        raw = (MAGIC + struct.pack("<Q", len(metadata)) + metadata
               + struct.pack("<II", len(payload), 2)
               + zstd.ZstdCompressor(level=3).compress(payload))
    else:
        raise ValueError("output_format must be json or preset.")
    if len(raw) > MAX_BYTES:
        raise ValueError("Output exceeds the 256 MiB limit.")
    return raw


def write_output(source: Path, output_path: str, raw: bytes, kind: str,
                 overwrite: bool, expected_sha256: str) -> dict:
    target = Path(output_path).expanduser().absolute()
    extension = ".json" if kind == "json" else ".serumpreset"
    if target.suffix.lower() != extension:
        raise ValueError(f"Output must have the {extension} extension.")
    if target.resolve() == source or (target.exists() and os.path.samefile(source, target)):
        raise ValueError("Output must be separate from the source, including aliases/hard links.")
    if target.is_symlink():
        raise ValueError("Output cannot be a symbolic link.")
    if target.exists() and not overwrite:
        raise ValueError("Output already exists; choose a new path or explicitly set overwrite=true.")
    if digest(source.read_bytes()) != expected_sha256:
        raise ValueError("Source changed while processing; inspect it again before writing.")
    # Same-directory temporary file, then atomic publication. link() never
    # clobbers a concurrently created output; replace() is explicitly authorized.
    name = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".serum-", delete=False) as temp:
            name = temp.name
            temp.write(raw)
            temp.flush()
            os.fsync(temp.fileno())
        if overwrite:
            os.replace(name, target)
        else:
            os.link(name, target)
        return {"output_path": str(target), "output_sha256": digest(raw),
                "output_bytes": len(raw), "output_format": kind,
                "live_applied": False}
    finally:
        if name and os.path.exists(name):
            os.unlink(name)
