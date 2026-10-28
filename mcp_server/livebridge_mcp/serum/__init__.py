"""Serum 2 preset files: read, edit, design and load complete ``.SerumPreset`` state.

Live's API (and a VST3's Configure list) reaches at most 128 Serum 2 parameters — no
wavetables, modulation matrix routes or FX order. The preset FILE holds all of it. This package
decodes it (``codec``), reads and patches it by JSON Pointer (``tree``) and packs it into a
``.vstpreset`` that Live's browser loads as Serum 2 with that state (``vst3`` + ``pack``).

The codec, tree, VST3 container and packing are ported from ableton-mcp-pro (MIT, Copyright (c)
2026 nicholasbien; the Serum preset work by kasperikaivola) — see
``LICENSE-ableton-mcp-pro.txt``. This module adds what LiveBridge needs around them: where
templates and presets are, and publishing a new ``.vstpreset`` into the User Library.

Packing needs a *template*: a ``.vstpreset`` saved from the user's own Serum 2 of the same build
or a newer one
(in Live: the plug-in device's title bar -> Save Preset, into the User Library). It supplies the
VST3 class id and the processor/controller schemas; a preset from an older Serum goes into a
newer template (an upgrade, reported), a preset from a newer Serum than every template is refused.
Needs ``cbor2`` and ``zstandard`` (the ``serum`` extra).
"""

from __future__ import annotations

import hashlib
import os
import platform
from pathlib import Path
from typing import Any, Iterable

from ..library import library_dir

#: where LiveBridge writes new preset designs (.SerumPreset / .json)
DESIGNS = "serum"
#: under the User Library: the .vstpreset files Live's browser loads
PUBLISHED = ("LiveBridge Presets", "Serum 2")
_MAX_SCAN = 20000
_MAX_TEMPLATE_BYTES = 64 * 1024 * 1024


def designs_dir() -> Path:
    """``~/.livebridge/serum`` (``LIVEBRIDGE_LIBRARY`` moves it): new preset designs."""
    return library_dir() / DESIGNS


def resolve(path: str, must_exist: bool = True) -> Path:
    """A user path: absolute, ``~``, or a bare/relative name inside :func:`designs_dir`."""
    candidate = Path(os.path.expanduser(str(path)))
    if not candidate.is_absolute():
        inside = designs_dir() / candidate
        if inside.exists() or not must_exist or not candidate.exists():
            candidate = inside
    return candidate


def preset_folders() -> list[Path]:
    """Folders with ``.SerumPreset`` files: ``LIVEBRIDGE_SERUM_PRESETS`` (os.pathsep list),
    Serum 2's usual preset folders, and the designs folder — existing ones only."""
    home = Path.home()
    found: list[Path] = []
    override = os.environ.get("LIVEBRIDGE_SERUM_PRESETS", "").strip()
    candidates = [Path(p).expanduser() for p in override.split(os.pathsep) if p.strip()]
    for base in (home / "Documents", home / "Music", home):
        candidates += [base / "Xfer" / "Serum 2 Presets", base / "Serum 2 Presets"]
    if platform.system() == "Darwin":
        candidates.append(Path("/Library/Audio/Presets/Xfer Records/Serum 2 Presets"))
    public = os.environ.get("PUBLIC")
    if public:
        candidates.append(Path(public) / "Documents" / "Xfer" / "Serum 2 Presets")
    candidates.append(designs_dir())
    for folder in candidates:
        try:
            if folder.is_dir() and folder not in found:
                found.append(folder)
        except OSError:
            continue
    return found


def _walk(roots: Iterable[Path], suffix: str) -> Iterable[Path]:
    seen = 0
    for root in roots:
        for folder, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for name in files:
                if name.lower().endswith(suffix):
                    yield Path(folder) / name
                seen += 1
                if seen > _MAX_SCAN:
                    return


def find_presets(query: str | None, folders: list[Path], limit: int = 20
                 ) -> list[dict[str, Any]]:
    """``.SerumPreset`` files whose name or folder contains every word of ``query``."""
    words = [w for w in (query or "").lower().split() if w]
    hits = []
    for path in _walk(folders, ".serumpreset"):
        text = (path.stem + " " + path.parent.name).lower()
        if all(w in text for w in words):
            hits.append({"name": path.stem, "folder": path.parent.name, "path": str(path)})
            if len(hits) >= limit:
                break
    return hits


def template_info(path: Path) -> dict[str, Any] | None:
    """``{path, product_version, version, modified}`` when ``path`` is a Serum 2
    ``.vstpreset`` (processor + controller state), else None."""
    from . import pack, vst3
    try:
        if path.stat().st_size > _MAX_TEMPLATE_BYTES:
            return None
        _class_id, chunks = vst3.read_vst3_preset(path.read_bytes())
        if b"Comp" not in chunks or b"Cont" not in chunks:
            return None
        meta, _data = pack.unpack_serum_state(chunks[b"Comp"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    except Exception:  # an undecodable payload is just not a template
        return None
    if meta.get("product") != "Serum2":
        return None
    return {"path": str(path), "product_version": meta.get("productVersion"),
            "version": meta.get("version"), "modified": path.stat().st_mtime}


def find_templates(user_libraries: list[Path]) -> list[dict[str, Any]]:
    """Serum 2 ``.vstpreset`` templates in the User Library, newest first (LiveBridge's own
    published presets last: they work, but a preset the user saved is the safer schema)."""
    found, seen = [], set()
    roots: list[Path] = []
    for root in user_libraries:              # one folder may be listed under two spellings
        try:
            if root.is_dir() and not any(root.samefile(r) for r in roots):
                roots.append(root)
        except OSError:
            continue
    for path in _walk(roots, ".vstpreset"):
        key = os.path.normcase(os.path.realpath(path))
        if key in seen:
            continue
        seen.add(key)
        info = template_info(path)
        if info:
            info["generated"] = all(part in path.parts for part in PUBLISHED)
            found.append(info)
    found.sort(key=lambda t: (t["generated"], -t["modified"]))
    return found


def _stem(name: str) -> str:
    cleaned = "".join(" " if c in '<>:"/\\|?*' or ord(c) < 32 else c for c in name)
    text = " ".join(cleaned.split()).strip().rstrip(".")
    return (text or "Preset")[:80]


def publish(folder: Path, name: str, payload: bytes, forbidden: Iterable[Path]) -> Path:
    """``<name>.vstpreset`` in ``folder`` holding ``payload``: an existing file with exactly
    these bytes is reused, anything else is never overwritten (a taken name gets the payload's
    short hash)."""
    folder.mkdir(parents=True, exist_ok=True)
    stem = _stem(name)
    digest = hashlib.sha256(payload).hexdigest()[:8]
    blocked = {Path(p).resolve() for p in forbidden}
    for path in (folder / f"{stem}.vstpreset", folder / f"{stem}-{digest}.vstpreset"):
        if path.resolve() in blocked:
            continue
        if path.exists():
            try:
                if path.is_file() and path.read_bytes() == payload:
                    return path                      # the same preset, already published
            except OSError:
                pass
            continue
        try:
            with path.open("xb") as stream:
                stream.write(payload)
        except FileExistsError:
            continue
        return path
    raise ValueError("could not write a new preset file without overwriting an existing one")


from . import codec, pack, tree, vst3  # noqa: E402  (the ported modules, as serum.<module>)


def pick_template(templates: list[dict[str, Any]], wanted: tuple[Any, Any]) -> Path:
    """The template for a preset of build ``wanted`` (productVersion, version): the same build
    if there is one, else the oldest newer one (Serum upgrades older presets). Raises
    ValueError when every template is older than the preset, or there is none."""
    from .pack import build_relation
    source = {"productVersion": wanted[0], "version": wanted[1]}
    same, newer = [], []
    for template in templates:
        relation = build_relation(source, {"productVersion": template["product_version"],
                                           "version": template["version"]})
        (same if relation == "same" else newer if relation == "older" else []).append(template)
    if same:
        return Path(same[0]["path"])
    if newer:
        from .pack import build_key
        newer.sort(key=lambda t: (build_key(t["product_version"]), build_key(t["version"])))
        return Path(newer[0]["path"])
    found = sorted({str(t["product_version"]) for t in templates})
    raise ValueError(f"no Serum 2 template of build {wanted[0]} or newer in the User Library"
                     + (f" (found: {', '.join(found)})" if found else "")
                     + ": save any preset from your Serum 2 in Live (the device's title bar > "
                       "Save Preset) once, then retry")


def vstpreset_for(source: str, user_libraries: list[Path], template: str | None = None
                  ) -> tuple[Path, str, dict[str, Any]]:
    """Pack a ``.SerumPreset`` (or design JSON) into a new ``.vstpreset`` in the User Library
    (``PUBLISHED`` folder) with a template of the same or a newer build (see :func:`pick_template`): ``(path, preset name, report)``.

    Raises ValueError with a readable message (no template, wrong build ...).
    """
    path, document, _sha, _kind = codec.load(str(resolve(source)))
    if not user_libraries:
        raise ValueError("Live's User Library was not found (is Live connected and on this "
                         "machine?)")
    wanted = (document["metadata"].get("productVersion"), document["metadata"].get("version"))
    if template:
        chosen = resolve(template)
        if template_info(chosen) is None:
            raise ValueError(f"{chosen} is not a Serum 2 .vstpreset")
    else:
        chosen = pick_template(find_templates(user_libraries), wanted)
    payload, report = pack.pack_serum2(path, chosen)
    name = report.get("preset_name") or path.stem
    output = publish(user_libraries[0].joinpath(*PUBLISHED), name, payload, [path, chosen])
    return output, name, dict(report, template=str(chosen))
