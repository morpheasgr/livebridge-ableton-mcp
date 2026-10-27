"""Bounded views and JSON Pointer patching of a decoded Serum preset.

Ported from ableton-mcp-pro (MIT, Copyright (c) 2026 nicholasbien; Serum preset work by
kasperikaivola, github.com/kasperikaivola/ableton-mcp-pro commit 6ac32c8) — see
``LICENSE-ableton-mcp-pro.txt`` next to this file. Nothing outside an explicit
operation is projected away.
"""

from __future__ import annotations

import copy
import hashlib
from itertools import islice
import json
import re


def escape(key):
    return str(key).replace("~", "~0").replace("/", "~1")


def tokens(pointer: str):
    if not isinstance(pointer, str) or len(pointer) > 4096:
        raise ValueError("JSON Pointer must be a string of at most 4096 characters.")
    if pointer == "":
        return []
    if not pointer.startswith("/") or re.search(r"~(?![01])", pointer):
        raise ValueError("Use a JSON Pointer: /data/Env0/plainParams; escape ~ as ~0 and / as ~1.")
    return [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]


def index(key, length, append=False):
    if append and key == "-":
        return length
    if not re.fullmatch(r"0|[1-9][0-9]*", key):
        raise ValueError("Array index must be a non-negative integer (no leading zeros).")
    number = int(key)
    if number >= length + int(append):
        raise ValueError("Array index out of range.")
    return number


def get(document, pointer):
    current = document
    for key in tokens(pointer):
        if isinstance(current, dict):
            if key not in current:
                raise ValueError(f"Missing pointer {pointer}; missing keys are not known defaults.")
            current = current[key]
        elif isinstance(current, list):
            current = current[index(key, len(current))]
        else:
            raise ValueError(f"Pointer traverses a scalar: {pointer}")
    return current


def equal_state(left, right):
    """Compare decoded types too: Python's True == 1 is unsuitable for state."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(equal_state(v, right[k]) for k, v in left.items())
    if isinstance(left, (list, tuple)):
        return len(left) == len(right) and all(equal_state(x, y) for x, y in zip(left, right))
    if isinstance(left, float) and left != left and right != right:
        return True  # CBOR can retain NaN even though JSON cannot.
    return left == right


def bounds(offset, limit, depth=0):
    if type(offset) is not int or not 0 <= offset <= 1000000:
        raise ValueError("offset must be an integer from 0 to 1000000.")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer from 1 to 100.")
    if type(depth) is not int or not 0 <= depth <= 5:
        raise ValueError("depth must be an integer from 0 to 5.")


def summary(value, depth=1, budget=None):
    if budget is None:
        budget = [400]
    budget[0] -= 1
    kind = type(value).__name__
    if isinstance(value, (dict, list, tuple)):
        result = {"type": kind, "length": len(value)}
        if depth <= 0 or budget[0] <= 0:
            return result
        if isinstance(value, dict):
            items = islice(value.items(), 20)
            result["entries"] = [{"key": str(k)[:160],
                                   "value": summary(v, depth - 1, budget)}
                                  for k, v in items if budget[0] > 0]
        else:
            count = 4 if len(value) > 64 else 20
            result["preview"] = [summary(v, depth - 1, budget)
                                 for v in value[:count] if budget[0] > 0]
        result["summarized"] = True
        return result
    if isinstance(value, bytes):
        return {"type": "bytes", "length": len(value),
                "sha256": hashlib.sha256(value).hexdigest()}
    if isinstance(value, str) and len(value) > 160:
        return {"type": "str", "length": len(value), "preview": value[:160],
                "summarized": True}
    if type(value) in (str, bool, int, float, type(None)):
        # Nonfinite CBOR values have no JSON representation.
        if isinstance(value, float) and (value != value or abs(value) == float("inf")):
            return {"type": "float", "value": str(value)}
        return value
    return {"type": kind, "note": "Opaque CBOR value preserved; not JSON-editable."}


def read(document, pointer, offset=0, limit=30, depth=2):
    bounds(offset, limit, depth)
    value = get(document, pointer)
    result = {"path": pointer, "type": type(value).__name__, "offset": offset}
    budget = [400]
    if isinstance(value, (dict, list)):
        result["total"] = len(value)
        # Enumerated arrays are sliced directly, avoiding full sample copies.
        if isinstance(value, dict):
            entries = islice(value.items(), offset, offset + limit)
        else:
            entries = ((i, value[i]) for i in range(offset, min(len(value), offset + limit)))
        result["entries"] = [{"path": pointer + "/" + escape(k),
                               "addressable": not isinstance(value, dict) or isinstance(k, str),
                               "value": summary(v, depth, budget)} for k, v in entries]
        result["next_offset"] = offset + limit if offset + limit < len(value) else None
        result["note"] = "Values are bounded previews. Read a returned path to expand; no data is discarded on disk."
    else:
        result["value"] = summary(value, depth, budget)
    return result


def search(document, query, pointer="", offset=0, limit=30, include_array_values=False):
    bounds(offset, limit)
    if not isinstance(query, str) or not query.strip() or len(query) > 256:
        raise ValueError("query must contain 1 to 256 characters (case-insensitive literal search).")
    needle = query.casefold()
    root = get(document, pointer)
    matches = []
    skipped = []
    seen = 0
    budget = [400]

    def walk(value, path):
        nonlocal seen
        scalar = type(value) in (str, bool, int, float, type(None))
        if needle in path.casefold() or (scalar and needle in str(value).casefold()):
            seen += 1
            if seen > offset:
                matches.append({"path": path, "value": summary(value, 1, budget)})
            if len(matches) > limit:
                return True
        if isinstance(value, dict):
            for key, child in value.items():
                if isinstance(key, str) and walk(child, path + "/" + escape(key)):
                    return True
        elif isinstance(value, list):
            if len(value) > 64 and all(type(v) in (int, float, bool, str, type(None)) for v in value):
                if not include_array_values:
                    if len(skipped) < 20:
                        skipped.append({"path": path, "length": len(value)})
                    return False
            for i, child in enumerate(value):
                if walk(child, path + "/" + str(i)):
                    return True
        return False

    more = walk(root, pointer)
    return {"query": query, "scope": pointer, "matches": matches[:limit],
            "next_offset": offset + limit if more else None,
            "skipped_large_arrays": skipped,
            "note": "Literal paths/values only; no parameter units or enum semantics inferred. Large scalar arrays skipped unless include_array_values=true."}


def patch(document, operations, copy_resolver=None):
    if not isinstance(operations, list) or not 1 <= len(operations) <= 100:
        raise ValueError("operations must contain 1 to 100 add/replace/remove/test/copy objects.")
    edited = copy.deepcopy(document)
    changes = []
    for op in operations:
        if not isinstance(op, dict) or op.get("op") not in {"add", "replace", "remove", "test", "copy"}:
            raise ValueError("Supported operations: add, replace, remove, test, copy.")
        action, path = op["op"], op.get("path")
        if not isinstance(path, str) or len(path) > 4096:
            raise ValueError("Operation path must be a JSON Pointer of at most 4096 characters.")
        parts = tokens(path)
        if not parts or parts[0] not in {"metadata", "data"}:
            raise ValueError("Edit paths must start with /metadata or /data; the root cannot be edited.")
        if action in {"add", "replace", "test"}:
            if "value" not in op:
                raise ValueError(f"{action} requires value.")
            # Require actual JSON input: no accidental CBOR type conversion.
            from .codec import _json_compatible
            _json_compatible(op["value"])
            json.dumps(op["value"], allow_nan=False)
        if action == "test":
            if not equal_state(get(edited, path), op["value"]):
                raise ValueError(f"Test failed: {path}. No output written.")
            continue
        parent_path = path.rsplit("/", 1)[0]
        if action == "copy":
            source_pointer = op.get("from")
            source_parts = tokens(source_pointer)
            if not source_parts or source_parts[0] not in {"metadata", "data"}:
                raise ValueError("Copy from must start with /metadata or /data.")
            external = "source_file_path" in op or "source_sha256" in op
            if external:
                if copy_resolver is None:
                    raise ValueError("External preset copies require a guarded copy resolver.")
                copied = copy_resolver(op)
            else:
                copied = get(edited, source_pointer)
            copied = copy.deepcopy(copied)
        parent = get(edited, parent_path)
        key = parts[-1]
        if not isinstance(parent, (dict, list)):
            raise ValueError(f"Parent is not an object or array: {parent_path}")
        if isinstance(parent, list):
            key = index(key, len(parent), append=action in {"add", "copy"})
            exists = key < len(parent)
        else:
            exists = key in parent
        if action not in {"add", "copy"} and not exists:
            raise ValueError(f"Missing target: {path}")
        before = parent[key] if exists else None
        entry = {"op": action, "path": path, "existed": exists, "before": summary(before)}
        if action == "copy":
            entry["from"] = op["from"]
            if external:
                entry["source_file_path"] = op["source_file_path"]
                entry["source_sha256"] = op["source_sha256"]
        if action == "remove":
            del parent[key]
        else:
            new = copied if action == "copy" else copy.deepcopy(op["value"])
            if isinstance(parent, list) and action in {"add", "copy"}:
                parent.insert(key, new)
            else:
                parent[key] = new
            entry["after"] = summary(new)
        changes.append(entry)
    from .codec import validate
    validate(edited)
    return edited, changes


def compare(left, right, pointer="", offset=0, limit=30):
    bounds(offset, limit)
    a, b = get(left, pointer), get(right, pointer)
    results = []
    seen = 0
    budget = [400]
    missing = object()

    def walk(a, b, path):
        nonlocal seen
        if equal_state(a, b):
            return False
        if isinstance(a, dict) and isinstance(b, dict) and all(isinstance(k, str) for k in (*a, *b)):
            for k in list(a) + [k for k in b if k not in a]:
                if walk(a.get(k, missing), b.get(k, missing), path + "/" + escape(k)):
                    return True
            return False
        if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
            for i, (x, y) in enumerate(zip(a, b)):
                if walk(x, y, path + "/" + str(i)):
                    return True
            return False
        seen += 1
        if seen > offset:
            results.append({"path": path,
                            "change": "added" if a is missing else "removed" if b is missing else "changed",
                            "before": None if a is missing else summary(a, 1, budget),
                            "after": None if b is missing else summary(b, 1, budget)})
        return len(results) > limit

    more = walk(a, b, pointer)
    return {"scope": pointer, "equal": seen == 0, "changes": results[:limit],
            "next_offset": offset + limit if more else None,
            "note": "Decoded state comparison; array length changes are summarized at the array path."}
