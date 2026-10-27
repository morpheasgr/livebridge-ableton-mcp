"""Serum 2 preset files — the whole patch, not just 128 knobs: find presets, read and search
their complete state (oscillators and wavetables, filters, envelopes, LFOs, the modulation matrix,
the FX chain and its order), edit or design new ones, compare them, and load one into Live as
Serum 2 on a track. See ``livebridge_mcp.serum`` and the companion guide ``SERUM_PRESETS.md``
(resource ``livebridge://serum-presets``).

Everything but `live_preset_load` works on files only; nothing in Live changes. Needs the
``serum`` extra (cbor2, zstandard), which the installer adds.
"""

from __future__ import annotations

import json
import time
from itertools import islice
from pathlib import Path
from typing import Any

from . import bridge_call, tool_error
from .. import serum
from ..client import BridgeClient

_MAX_CHARS = 32000
_BROWSER_TRIES = 8
_BROWSER_PAUSE_S = 0.5
_HOW_TO_TEMPLATE = (
    "Save one preset from YOUR Serum 2 once: load Serum 2 on a track in Live, open the device's "
    "title-bar menu (or the disk icon) -> Save Preset, and keep it in the User Library. "
    "LiveBridge finds it there and uses it as the container for every preset it loads (same "
    "Serum build only).")
_DESIGN_NOTE = (
    "For a new preset read livebridge://serum-presets (workflow, new-preset-design, "
    "module-editing): start from a preset, design the sources, filters, envelopes/LFOs, matrix, "
    "FX and voicing, write a separate file. For 'make this darker/faster' use the JSON round "
    "trip (existing-preset-edits). Use observed keys and enum strings only; units, defaults and "
    "ranges are not inferred.")


def _fit(result: dict[str, Any]) -> dict[str, Any]:
    """Keep answers under ~32k characters (bounded previews; nothing on disk is lost)."""
    if len(json.dumps(result, ensure_ascii=False, default=str)) <= _MAX_CHARS:
        return result
    for key in ("entries", "matches", "changes", "metadata", "modules"):
        result.pop(key, None)
    result["details_omitted"] = True
    result["note"] = ("The answer was too long: use a smaller limit/depth or a narrower path. "
                      "Files reported as written were written.")
    if len(json.dumps(result, ensure_ascii=False, default=str)) > _MAX_CHARS:
        keep = ("dry_run", "change_count", "output_path", "output_sha256", "output_bytes",
                "output_format", "details_omitted", "note")
        result = {k: result[k] for k in keep if k in result}
    return result


def _failure(exc: Exception, cmd: str) -> dict[str, Any]:
    message = str(exc)[:1000]
    if "cbor2 and zstandard" in message:
        return tool_error(message, type="unsupported", cmd=cmd)
    if isinstance(exc, FileNotFoundError):
        return tool_error(f"no such file: {exc.filename or message}", type="not_found", cmd=cmd)
    return tool_error(message, cmd=cmd)


def _source(file_path: str) -> tuple[Path, dict, str, str, dict[str, Any]]:
    path, document, sha, kind = serum.codec.load(str(serum.resolve(file_path)))
    return path, document, sha, kind, {"file_path": str(path), "source_sha256": sha,
                                       "format": kind}


def register(mcp: Any, bridge: BridgeClient) -> None:
    """Register the Serum 2 preset file tools on the MCP app."""

    def user_libraries() -> list[Path]:
        places = bridge_call(bridge, "samples.locations")
        if not isinstance(places, dict) or "error" in places:
            return []
        return [Path(p["path"]) for p in places.get("user_library") or []
                if isinstance(p, dict) and p.get("exists") and p.get("path")]

    @mcp.tool()
    def live_preset_find(query: str | None = None, limit: int = 20) -> Any:
        """Find Serum 2 presets (`.SerumPreset` files) to start from or to load — by words in
        their name or folder ("reese", "bass dark", "pluck") — and show what loading needs:
        the Serum 2 templates found in Live's User Library. Read-only.

        Args:
            query: Words that must all appear in the preset's name or folder (omit: list).
            limit: At most this many presets (1..200).

        Returns:
            `{presets:[{name, folder, path}], folders, designs_folder, templates:[{path,
            product_version, version}], hint?}` — `designs_folder` is where new designs go
            (bare file names in the other tools mean that folder).
        """
        cmd = "preset.find"
        if not 1 <= limit <= 200:
            return tool_error("limit must be 1..200", cmd=cmd)
        folders = serum.preset_folders()
        result: dict[str, Any] = {
            "presets": serum.find_presets(query, folders, limit),
            "folders": [str(f) for f in folders],
            "designs_folder": str(serum.designs_dir())}
        try:
            templates = serum.find_templates(user_libraries())
        except Exception as exc:  # missing extra
            return _failure(exc, cmd)
        result["templates"] = [{k: t[k] for k in ("path", "product_version", "version")}
                               for t in templates[:10]]
        hints = []
        if not templates:
            hints.append(_HOW_TO_TEMPLATE)
        if not folders or folders == [serum.designs_dir()]:
            hints.append("No Serum 2 preset folder found — set LIVEBRIDGE_SERUM_PRESETS to the "
                         "folder Serum 2 saves to (Serum 2: menu -> Show Presets Folder).")
        if hints:
            result["hint"] = " ".join(hints)
        return result

    @mcp.tool()
    def live_preset_inspect(file_path: str) -> Any:
        """A Serum 2 preset file at a glance: metadata, the modules it holds (first 50 paths)
        and its SHA256 (the edit tools require it). Read-only — a file, not what Serum in Live
        currently plays.

        Args:
            file_path: A `.SerumPreset` (or a `.json` export of one); a bare name means the
                designs folder.

        Returns:
            `{file_path, source_sha256, format, file_bytes, metadata, module_count,
            modules:[{path, value}], instructions}`.
        """
        cmd = "preset.inspect"
        try:
            path, document, _sha, _kind, result = _source(file_path)
            result.update(file_bytes=path.stat().st_size,
                          metadata=serum.tree.summary(document["metadata"], 2),
                          module_count=len(document["data"]),
                          modules=[{"path": "/data/" + serum.tree.escape(k),
                                    "value": serum.tree.summary(v, 0)}
                                   for k, v in islice(document["data"].items(), 50)],
                          instructions=_DESIGN_NOTE)
            return _fit(result)
        except Exception as exc:
            return _failure(exc, cmd)

    @mcp.tool()
    def live_preset_read(
        file_path: str,
        path: str = "",
        offset: int = 0,
        limit: int = 30,
        depth: int = 2,
    ) -> Any:
        """Read part of a Serum 2 preset by JSON Pointer, paged: `/data/Env0/plainParams`,
        `/data/FXRack0/FX/0`, `/data/Oscillator0/WTOsc0`. Values are bounded previews — read a
        returned path to expand it. Read-only.

        Args:
            file_path: The preset (see live_preset_inspect).
            path: JSON Pointer ("" = root: /metadata and /data; escape "/" in a key as ~1, "~"
                as ~0).
            offset, limit: Paging over children (limit 1..100).
            depth: How deep previews go (0..5).

        Returns:
            `{file_path, source_sha256, path, type, total?, entries:[{path, value}],
            next_offset?, value?}`.
        """
        cmd = "preset.read"
        try:
            _p, document, _sha, _kind, result = _source(file_path)
            result.update(serum.tree.read(document, path, offset, limit, depth))
            return _fit(result)
        except Exception as exc:
            return _failure(exc, cmd)

    @mcp.tool()
    def live_preset_search(
        file_path: str,
        query: str,
        path: str = "",
        offset: int = 0,
        limit: int = 30,
        include_array_values: bool = False,
    ) -> Any:
        """Search a Serum 2 preset's paths and values (case-insensitive, literal): controls,
        enum strings, wavetable/asset names, matrix routes, FX — each hit with its exact
        pointer for reading or editing. Read-only.

        Args:
            file_path: The preset.
            query: Text to find ("Cutoff", "kParamDecay", "Distortion", a wavetable name).
            path: Only under this pointer.
            offset, limit: Paging (limit 1..100).
            include_array_values: Also search inside long number arrays (skipped otherwise).

        Returns:
            `{query, scope, matches:[{path, value}], next_offset?, skipped_large_arrays}`.
        """
        cmd = "preset.search"
        try:
            _p, document, _sha, _kind, result = _source(file_path)
            result.update(serum.tree.search(document, query, path, offset, limit,
                                            include_array_values))
            return _fit(result)
        except Exception as exc:
            return _failure(exc, cmd)

    @mcp.tool()
    def live_preset_edit(
        file_path: str,
        expected_sha256: str,
        operations: list[dict[str, Any]],
        output_path: str = "",
        output_format: str = "preset",
        dry_run: bool = True,
        overwrite: bool = False,
    ) -> Any:
        """Edit a Serum 2 preset into a NEW file — JSON Patch operations on its complete state,
        everything else kept (embedded audio, wavetables, unknown fields). The source is never
        changed; nothing in Live changes.

        Args:
            file_path: The preset to start from.
            expected_sha256: Its `source_sha256` from inspect/read/search (guards against a
                changed file).
            operations: 1..100 of {"op": "replace"|"add"|"remove"|"test"|"copy", "path": ...,
                "value"?, "from"?} — e.g. {"op": "replace", "path":
                "/data/Env0/plainParams/kParamDecay", "value": 0.6}. "copy" with
                "source_file_path" + "source_sha256" takes a whole subtree (a wavetable
                oscillator, an FX rack) from another preset without sending it through chat.
            output_path: The new file (`.SerumPreset` or `.json`; a bare name goes to the
                designs folder). Required when dry_run=false.
            output_format: "preset" (default) or "json".
            dry_run: Default true: validate and encode, report the changes, write nothing.
            overwrite: Allow replacing an existing output file (never the source).

        Returns:
            `{file_path, source_sha256, dry_run, changes:[{op, path, existed, before, after}],
            change_count, encoded_bytes, output_path?, output_sha256?}`.

        Gotchas: values are written as given — no ranges, units or enums are checked: copy
        them from presets that sound right, or learn them with live_preset_compare. A new
        sound changes its sources, filter, envelopes/LFOs, matrix, FX and voicing — not one
        knob. Then `live_preset_load` to hear it.
        """
        cmd = "preset.edit"
        if output_format not in ("preset", "json"):
            return tool_error("output_format must be preset or json", cmd=cmd)
        try:
            source, document, sha, _kind, result = _source(file_path)
            if expected_sha256 != sha:
                return tool_error("the file changed since it was read (SHA256 differs) — "
                                  "inspect it again", type="invalid_state", cmd=cmd)
            if len(json.dumps(operations, allow_nan=False)) > 1024 * 1024:
                return tool_error("operations exceed 1 MiB — convert to JSON and edit that "
                                  "file instead", cmd=cmd)
            donors: dict[tuple[str, str], tuple[Path, dict, str]] = {}

            def resolve_copy(op: dict[str, Any]) -> Any:
                donor, expected = op.get("source_file_path"), op.get("source_sha256")
                if not isinstance(donor, str) or not donor or not isinstance(expected, str):
                    raise ValueError("a copy from another preset needs source_file_path and "
                                     "source_sha256")
                key = (donor, expected)
                if key not in donors:
                    donor_path, donor_doc, donor_sha, _k = serum.codec.load(
                        str(serum.resolve(donor)))
                    if donor_sha != expected:
                        raise ValueError("the copy source changed (SHA256 differs) — inspect "
                                         "it again")
                    donors[key] = (donor_path, donor_doc, donor_sha)
                return serum.tree.get(donors[key][1], op["from"])

            edited, changes = serum.tree.patch(document, operations, copy_resolver=resolve_copy)
            raw = serum.codec.encode(edited, output_format)
            serum.codec.decode(raw)                    # the bytes we would write decode again
            for donor_path, _doc, donor_sha in donors.values():
                if serum.codec.digest(donor_path.read_bytes()) != donor_sha:
                    raise ValueError("a copy source changed while editing; nothing written")
            result.update(dry_run=dry_run, changes=changes, change_count=len(changes),
                          output_format=output_format, encoded_bytes=len(raw))
            if not dry_run:
                if not output_path:
                    return tool_error("output_path is required when dry_run=false", cmd=cmd)
                target = serum.resolve(output_path, must_exist=False)
                target.parent.mkdir(parents=True, exist_ok=True)
                result.update(serum.codec.write_output(source, str(target), raw, output_format,
                                                       overwrite, sha))
                result.pop("live_applied", None)
            return _fit(result)
        except Exception as exc:
            return _failure(exc, cmd)

    @mcp.tool()
    def live_preset_convert(
        file_path: str,
        output_path: str,
        output_format: str,
        expected_sha256: str,
        overwrite: bool = False,
    ) -> Any:
        """Unpack a Serum 2 preset to complete JSON on disk (`output_format="json"`), or pack
        such JSON back into a `.SerumPreset` ("preset") — the round trip for big edits: unpack,
        edit the JSON file, pack. Nothing is sent through chat; the source stays unchanged.

        Args:
            file_path: The input (either format).
            output_path: The new file; its extension must match (`.json` / `.SerumPreset`).
            output_format: "json" or "preset".
            expected_sha256: The input's `source_sha256`.
            overwrite: Allow replacing an existing output.

        Returns:
            `{file_path, source_sha256, output_path, output_sha256, output_bytes,
            output_format}`.
        """
        cmd = "preset.convert"
        if output_format not in ("preset", "json"):
            return tool_error("output_format must be preset or json", cmd=cmd)
        try:
            source, document, sha, _kind, result = _source(file_path)
            if expected_sha256 != sha:
                return tool_error("the file changed since it was read (SHA256 differs)",
                                  type="invalid_state", cmd=cmd)
            raw = serum.codec.encode(document, output_format)
            serum.codec.decode(raw)
            target = serum.resolve(output_path, must_exist=False)
            target.parent.mkdir(parents=True, exist_ok=True)
            result.update(serum.codec.write_output(source, str(target), raw, output_format,
                                                   overwrite, sha))
            result.pop("live_applied", None)
            return result
        except Exception as exc:
            return _failure(exc, cmd)

    @mcp.tool()
    def live_preset_compare(
        left_path: str,
        right_path: str,
        path: str = "",
        offset: int = 0,
        limit: int = 30,
    ) -> Any:
        """What differs between two Serum 2 presets (decoded state), as pointers with before
        and after — to confirm an edit changed only what it should, or to learn how a control
        is stored by comparing two presets saved before and after moving it in Serum.
        Read-only.

        Args:
            left_path, right_path: The two presets (either format).
            path: Only under this pointer.
            offset, limit: Paging (limit 1..100).

        Returns:
            `{equal, changes:[{path, change: added|removed|changed, before, after}],
            next_offset?, left_sha256, right_sha256}`.
        """
        cmd = "preset.compare"
        try:
            _l, left, left_sha, _k1, _r1 = _source(left_path)
            _r, right, right_sha, _k2, _r2 = _source(right_path)
            result = serum.tree.compare(left, right, path, offset, limit)
            result.update(left_sha256=left_sha, right_sha256=right_sha)
            return _fit(result)
        except Exception as exc:
            return _failure(exc, cmd)

    @mcp.tool()
    def live_preset_load(
        source_path: str,
        track: int | str | None = None,
        track_name: str | None = None,
        template: str | None = None,
    ) -> Any:
        """Load a Serum 2 preset file into Live: Serum 2 appears with that exact patch —
        wavetables, matrix, FX and all — on a new MIDI track, or replacing the instrument of
        `track` (its clips and effects stay). The preset is packed into a new `.vstpreset` in
        the User Library (`LiveBridge Presets/Serum 2`) and loaded through Live's browser.

        Args:
            source_path: The `.SerumPreset` (e.g. from live_preset_edit / live_preset_find).
            track: Replace this MIDI track's instrument (default: a new MIDI track).
            track_name: Name of the new track (default: the preset's name).
            template: A Serum 2 `.vstpreset` of the same build to pack with (default: the
                newest one found in the User Library — see live_preset_find).

        Returns:
            `{loaded, preset_name, output_path, browser_path, track, device?, version,
            template, notes}`.

        Gotchas: needs a template saved from your own Serum 2 once (live_preset_find says
        how), and Live on this machine. A different Serum build is refused. Loaded Serum
        exposes no knobs to Live until configured — `live_plugin_expose` reloads it
        controllable but with its rack's patch, so expose before tweaking knobs or keep
        editing the file. Live may need a moment to see the new file (retried for ~4 s).
        """
        cmd = "preset.load"
        if track is not None and track_name is not None:
            return tool_error("track_name only applies to a new track (omit track)", cmd=cmd)
        status = bridge_call(bridge, "system.hello")
        if isinstance(status, dict) and "error" in status:
            return status
        try:
            source, document, _sha, _kind, _r = _source(source_path)
            libraries = user_libraries()
            if not libraries:
                return tool_error("Live's User Library was not found (is Live connected and on "
                                  "this machine?)", type="not_found", cmd=cmd)
            wanted = (document["metadata"].get("productVersion"),
                      document["metadata"].get("version"))
            if template:
                chosen = serum.resolve(template)
                info = serum.template_info(chosen)
                if info is None:
                    return tool_error(f"{chosen} is not a Serum 2 .vstpreset", cmd=cmd)
            else:
                templates = serum.find_templates(libraries)
                matching = [t for t in templates
                            if (t["product_version"], t["version"]) == wanted]
                if not matching:
                    found = sorted({str(t["product_version"]) for t in templates})
                    return tool_error(
                        f"no Serum 2 template of build {wanted[0]} in the User Library"
                        + (f" (found: {', '.join(found)})" if found else "") + ". "
                        + _HOW_TO_TEMPLATE, type="not_found", cmd=cmd)
                chosen = Path(matching[0]["path"])
            payload, report = serum.pack.pack_serum2(source, chosen)
            name = report.get("preset_name") or source.stem
            folder = libraries[0].joinpath(*serum.PUBLISHED)
            output = serum.publish(folder, name, payload, [source, chosen])
        except Exception as exc:
            return _failure(exc, cmd)
        browser_path = "user_library/" + "/".join(serum.PUBLISHED) + "/" + output.name
        args: dict[str, Any] = {"path": browser_path}
        if track is None:
            args.update(new_track="midi", track_name=track_name or name)
        else:
            args["track"] = track
        answer: Any = None
        for attempt in range(_BROWSER_TRIES):
            answer = bridge_call(bridge, "browser.load", args, timeout=60.0)
            if not (isinstance(answer, dict) and answer.get("type") == "not_found"):
                break
            if attempt == 0:                          # Live may list it without the extension
                args["path"] = browser_path[:-len(".vstpreset")]
            time.sleep(_BROWSER_PAUSE_S)
        result: dict[str, Any] = {
            "preset_name": name, "output_path": str(output), "browser_path": browser_path,
            "version": report.get("version"), "template": str(chosen),
            "notes": ["Loaded with the preset's complete state; Live reads none of it back "
                      "(Serum's knobs stay hidden until configured). Listen to check it."]}
        if not isinstance(answer, dict) or "error" in answer:
            result["loaded"] = False
            result["error"] = (answer.get("error") if isinstance(answer, dict) else str(answer))
            result["notes"].append("The file is saved: in Live's browser open User Library > "
                                   "LiveBridge Presets > Serum 2 and drag it onto a MIDI track.")
            return result
        devices = [d for d in (answer.get("inserted") or []) + (answer.get("changed") or [])
                   if isinstance(d, dict)]
        serum_devices = [d for d in devices if "serum" in str(d.get("name", "")).lower()]
        result["loaded"] = bool(serum_devices)
        result["track"] = answer.get("track")
        if serum_devices:
            result["device"] = {k: serum_devices[0].get(k) for k in ("name", "path")}
        else:
            result["notes"].append("Live loaded the item but no Serum 2 device appeared — "
                                   "check that Serum 2 (VST3) is installed and enabled in "
                                   "Live's Plug-Ins preferences.")
        if answer.get("removed"):
            result["replaced"] = [d.get("name") for d in answer["removed"]
                                  if isinstance(d, dict)]
        return result
