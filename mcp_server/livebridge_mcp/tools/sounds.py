"""Sound tools — a personal library of device sounds: `live_sound_save` stores every parameter
value of a device (a Serum 2 rack, Operator, an effect chain's EQ, a rack's macros) under a name,
`live_sound_load` puts them back on the same kind of device later, `live_sound_list` and
`live_sound_delete` manage the library.

Live's API cannot save presets or read a plug-in's internal state, so this is the way to keep a
sound Claude designed: the values of the exposed parameters. It also bridges a re-exposure of
Serum 2 (`live_plugin_expose` restarts the sound): save, expose the bigger set, load.

The library lives on the machine of the MCP server (``~/.livebridge/sounds``, see
:mod:`livebridge_mcp.library`).
"""

from __future__ import annotations

import time
from typing import Any

from . import bridge_call, drop_none, tool_error
from .. import library
from ..client import BridgeClient
from .devices import check_address

_PAGE = 1000
_MAX_PARAMETERS = 4096
_SET_CHUNK = 512


def _is_error(value: Any) -> bool:
    return isinstance(value, dict) and "error" in value and "type" in value


def _sounds_dir():
    return library.library_dir() / "sounds"


def _sound_path(name: str):
    return _sounds_dir() / (library.slug(name) + ".json")


def _keyed(parameters: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    """``(key, parameter)`` with keys unique per device: the lower-case name, and for the n-th
    repeat of a name ``name#n`` (some devices repeat names, e.g. per band)."""
    seen: dict[str, int] = {}
    keyed = []
    for parameter in parameters:
        name = str(parameter.get("name", "")).strip().lower()
        count = seen.get(name, 0)
        seen[name] = count + 1
        keyed.append((name if count == 0 else f"{name}#{count}", parameter))
    return keyed


def _matches(name: str, filters: list[str] | None) -> bool:
    if not filters:
        return True
    lowered = name.lower()
    return any(text.lower() in lowered for text in filters)


def register(mcp: Any, bridge: BridgeClient) -> None:
    """Register the sound library tools on the MCP app."""

    def read_parameters(track: Any, device: Any, cmd: str) -> Any:
        """``{"device": {...}, "parameters": [...]}`` of a device (all pages), or an error."""
        first: dict[str, Any] | None = None
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = bridge_call(bridge, "devices.parameters",
                               {**drop_none(track=track, device=device), "offset": offset,
                                "limit": _PAGE, "detail": "summary"})
            if _is_error(page) or not isinstance(page, dict):
                return page
            first = first or page
            rows.extend(p for p in page.get("parameters") or [] if isinstance(p, dict))
            offset = page.get("next_offset")
            if not offset or len(rows) >= _MAX_PARAMETERS:
                break
        if not rows:
            return tool_error("the device has no parameters to save", type="invalid_state",
                              cmd=cmd)
        return {"device": (first or {}).get("device") or {}, "parameters": rows}

    @mcp.tool()
    def live_sound_save(
        name: str,
        track: int | str | None = None,
        device: int | str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        overwrite: bool = False,
    ) -> Any:
        """Save a device's sound (every parameter value) to the user's sound library, to load it
        again later on the same kind of device — the way to keep a sound you designed, since
        Live's API cannot save presets. Works for any device: a Serum 2 / VST3 rack from
        `live_plugin_expose` (only the exposed parameters are saved), Operator, Wavetable, Drift,
        an EQ Eight, a rack's macros. Read-only in Live.

        Args:
            name: Name in the library ("Dark Reese", "Acid 303 squelch").
            track, device: The device, as in the `live_device_*` tools; for an exposed plug-in
                pass the `device` path `live_plugin_expose` returned (the plug-in inside the
                rack, not the rack).
            description: What it is / where it fits ("F minor DnB Reese, macro 1 = filter").
            tags: Words to find it again: ["bass", "dnb", "serum"].
            overwrite: Replace an existing sound with the same name.

        Returns:
            `{name, file, device:{name, class_name}, parameters (count), saved_at}`.

        Gotchas: the values are Live's internal values, so a sound loads correctly only on the
        same device type (`live_sound_load` checks the class). A plug-in's state beyond its
        exposed parameters (wavetables, mod routings) is not saved — keep the preset file for
        those. "Device On" is not part of the sound.
        """
        cmd = "devices.parameters"
        error = check_address(track, device, cmd)
        if error:
            return error
        if not isinstance(name, str) or not name.strip():
            return tool_error("name must be a non-empty string", cmd=cmd)
        path = _sound_path(name)
        if path.exists() and not overwrite:
            return tool_error(f"a sound named {library.read_json(path, {}).get('name', name)!r} "
                              "already exists — pass overwrite=true or choose another name",
                              type="invalid_state", cmd=cmd)
        answer = read_parameters(track, device, cmd)
        if _is_error(answer) or not isinstance(answer, dict):
            return answer
        stored = [{"name": p.get("name"), "value": p.get("value"), "display": p.get("display")}
                  for key, p in _keyed(answer["parameters"])
                  if key != "device on" and isinstance(p.get("value"), (int, float))]
        info = answer["device"]
        record = {
            "name": name.strip(),
            "description": (description or "").strip(),
            "tags": sorted({str(t).strip().lower() for t in tags or [] if str(t).strip()}),
            "device": {"name": info.get("name"), "class_name": info.get("class_name")},
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "parameters": stored,
        }
        try:
            library.write_json(path, record)
        except OSError as exc:
            return tool_error(f"could not write {path}: {exc}", type="internal", cmd=cmd)
        return {"name": record["name"], "file": str(path), "device": record["device"],
                "parameters": len(stored), "saved_at": record["saved_at"]}

    @mcp.tool()
    def live_sound_load(
        name: str,
        track: int | str | None = None,
        device: int | str | None = None,
        only: list[str] | None = None,
        force: bool = False,
        dry_run: bool = False,
    ) -> Any:
        """Load a saved sound onto a device: sets every parameter the device has from the
        library entry, in one undo step. Use it to recall a sound, to restore a Serum 2 sound
        after `live_plugin_expose` re-exposed it (save first, expose, then load), or to copy a
        sound to a second instance (save from one, load onto the other).

        Args:
            name: The sound's name (as in `live_sound_list`).
            track, device: The target device (see `live_sound_save`).
            only: Load only parameters whose names contain one of these texts (["filter",
                "env 1"]) — e.g. take just the filter of another sound.
            force: Load even when the device type differs from the saved one (values are
                Live's internal 0..1 / raw numbers and may mean something else).
            dry_run: Report what would change without writing.

        Returns:
            `{name, device, changed, unchanged, missing:[names the device lacks — e.g. not
            exposed], skipped:[names mapped to a macro, which cannot be set], parameters?:[{name,
            display}] (what changed), dry_run?}`.
        """
        cmd = "devices.set_parameters"
        error = check_address(track, device, cmd)
        if error:
            return error
        if not isinstance(name, str) or not name.strip():
            return tool_error("name must be a non-empty string", cmd=cmd)
        record = library.read_json(_sound_path(name), None)
        if not isinstance(record, dict) or not isinstance(record.get("parameters"), list):
            names = sorted(library.read_json(p, {}).get("name", p.stem)
                           for p in _sounds_dir().glob("*.json"))
            return tool_error(f"no sound named {name!r} in the library"
                              + (f" (saved: {', '.join(names[:20])})" if names else
                                 " (it is empty — live_sound_save first)"),
                              type="not_found", cmd=cmd)
        answer = read_parameters(track, device, cmd)
        if _is_error(answer) or not isinstance(answer, dict):
            return answer
        saved_class = (record.get("device") or {}).get("class_name")
        target_class = answer["device"].get("class_name")
        if saved_class and target_class and saved_class != target_class and not force:
            return tool_error(f"{record['name']!r} was saved from {saved_class}, this device "
                              f"is {target_class} — pass force=true to load it anyway",
                              type="invalid_state", cmd=cmd)
        targets = dict(_keyed(answer["parameters"]))
        writes, missing, skipped, unchanged = [], [], [], 0
        for key, saved in _keyed(record["parameters"]):
            label = str(saved.get("name"))
            if key == "device on" or not _matches(label, only):
                continue
            target = targets.get(key)
            if target is None:
                missing.append(label)
            elif target.get("enabled") is False:
                skipped.append(label)
            elif target.get("value") == saved.get("value"):
                unchanged += 1
            else:
                writes.append({"parameter": target.get("index"), "value": saved.get("value"),
                               "label": label, "display": saved.get("display")})
        result: dict[str, Any] = {"name": record["name"], "device": answer["device"],
                                  "changed": len(writes), "unchanged": unchanged,
                                  "missing": missing, "skipped": skipped}
        if dry_run or not writes:
            result["parameters"] = [{"name": w["label"], "display": w["display"]}
                                    for w in writes]
            if dry_run:
                result["dry_run"] = True
            return result
        address = drop_none(track=track, device=device)
        device_path = answer["device"].get("path")
        if device_path:
            address = {"device": device_path}
        changed = []
        for start in range(0, len(writes), _SET_CHUNK):
            chunk = writes[start:start + _SET_CHUNK]
            done = bridge_call(bridge, cmd, {**address, "normalized": False, "values": [
                {"parameter": w["parameter"], "value": w["value"]} for w in chunk]})
            if _is_error(done) or not isinstance(done, dict):
                if changed:
                    done = dict(done, written_before_error=len(changed))
                return done
            changed.extend({"name": p.get("name"), "display": p.get("display")}
                           for p in done.get("parameters") or [])
        result["parameters"] = changed
        return result

    @mcp.tool()
    def live_sound_list(filter: str | None = None, tag: str | None = None,
                        device_class: str | None = None) -> Any:
        """List the sounds in the user's library (newest first). Read-only.

        Args:
            filter: Text in the name or description ("reese").
            tag: Only sounds with this tag ("bass").
            device_class: Only sounds saved from this device class ("PluginDevice" for
                plug-ins such as Serum 2, "Operator", "InstrumentVector" for Wavetable).

        Returns:
            `{folder, count, sounds:[{name, description, tags, device:{name, class_name},
            parameters, saved_at}]}`.
        """
        sounds = []
        for path in _sounds_dir().glob("*.json"):
            record = library.read_json(path, None)
            if not isinstance(record, dict):
                continue
            text = f"{record.get('name', '')} {record.get('description', '')}".lower()
            if filter and filter.lower() not in text:
                continue
            if tag and tag.strip().lower() not in (record.get("tags") or []):
                continue
            device_info = record.get("device") or {}
            if device_class and str(device_info.get("class_name", "")).lower() != \
                    device_class.lower():
                continue
            sounds.append({"name": record.get("name", path.stem),
                           "description": record.get("description", ""),
                           "tags": record.get("tags") or [], "device": device_info,
                           "parameters": len(record.get("parameters") or []),
                           "saved_at": record.get("saved_at", "")})
        sounds.sort(key=lambda s: s["saved_at"], reverse=True)
        return {"folder": str(_sounds_dir()), "count": len(sounds), "sounds": sounds}

    @mcp.tool()
    def live_sound_delete(name: str) -> Any:
        """Delete a sound from the user's library (the file; nothing in Live changes). Ask the
        user first — this cannot be undone.

        Args:
            name: The sound's name.

        Returns:
            `{deleted: name, file}`.
        """
        path = _sound_path(name) if isinstance(name, str) and name.strip() else None
        if path is None or not path.exists():
            return tool_error(f"no sound named {name!r} in the library", type="not_found")
        try:
            path.unlink()
        except OSError as exc:
            return tool_error(f"could not delete {path}: {exc}", type="internal")
        return {"deleted": name, "file": str(path)}
