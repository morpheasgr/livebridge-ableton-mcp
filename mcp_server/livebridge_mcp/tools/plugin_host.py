"""Plug-in host tools — every parameter of a VST3 plug-in, past Live's 128 (experimental).

Live's API reaches only the plug-in parameters added with Configure (at most 128). The
LiveBridge Host Max for Live devices (``max_for_live/LiveBridge Host``: an effect and an
instrument) load the plug-in inside Max's ``vst~`` instead, which lists and sets all of them:
`live_plugin_host_load` puts one on a track and loads the plug-in, `live_plugin_host_parameters`
lists and searches its parameters, `live_plugin_host_set` sets them by number or display text,
`live_plugin_host_preset` loads a preset file into it (a Serum 2 design is packed first),
and `live_plugin_host_bank` swaps in a per-plug-in host whose chosen parameters are Live dials
(automatable).

Limits: hosted parameters are not Live parameters until a bank makes dials of them, the
plug-in runs
inside Max (Max for Live needed to open the set), VST3 only. Device protocol: like the
Engine's (:mod:`livebridge_mcp.engine`), registry kind "host".
"""

from __future__ import annotations

import hashlib
import os
import platform
import re
import time
from pathlib import Path
from typing import Any

from . import bridge_call, tool_error
from .. import engine, host_device, serum
from ..client import BridgeClient

_DEVICES = {"effect": ("LiveBridge Host Effect", "audio_effect"),
            "instrument": ("LiveBridge Host Instrument", "instrument")}
_WAIT_S = 12.0
_LOAD_TIMEOUT = 30.0
_SET_TIMEOUT = 60.0
_BANK_FOLDER = ("LiveBridge Presets", "Plug-in Hosts")
_HOST_JS = ("lbhost.js", "lbhost_context.js")
_GENERIC = ("LiveBridge Host Instrument", "LiveBridge Host Effect")
_BROWSER_TRIES = 4


def _js_sources(libraries: list[Path]) -> Path | None:
    """A folder holding lbhost.js + lbhost_context.js: the repo's, else the installed host's."""
    candidates = [Path(__file__).resolve().parents[3] / "max_for_live" / "LiveBridge Host"]
    for library in libraries:
        candidates += [library / "Presets" / "Instruments" / "Max Instrument" / _GENERIC[0],
                       library / "Presets" / "Audio Effects" / "Max Audio Effect" / _GENERIC[1]]
    for folder in candidates:
        if all((folder / name).is_file() for name in _HOST_JS):
            return folder
    return None


def _stem(name: str) -> str:
    cleaned = "".join(" " if c in '<>:"/\\|?*' or ord(c) < 32 else c for c in name)
    return (" ".join(cleaned.split()).strip().rstrip(".") or "Plug-in")[:60]


def write_bank(folder: Path, name: str, payload: bytes, scripts: Path) -> Path:
    """``<name>.amxd`` (or ``<name> <hash>.amxd`` when that name holds another bank) next to
    copies of the host scripts; an identical file is reused."""
    folder.mkdir(parents=True, exist_ok=True)
    for script in _HOST_JS:
        source = (scripts / script).read_bytes()
        target = folder / script
        if not target.is_file() or target.read_bytes() != source:
            target.write_bytes(source)
    digest = hashlib.sha256(payload).hexdigest()[:8]
    for path in (folder / f"{_stem(name)}.amxd", folder / f"{_stem(name)} {digest}.amxd"):
        if path.exists():
            if path.read_bytes() == payload:
                return path
            continue
        path.write_bytes(payload)
        return path
    raise OSError("could not write the bank device without overwriting another one")


def vst3_folders() -> list[Path]:
    """Where VST3 plug-ins live (``LIVEBRIDGE_VST3_PATH``, os.pathsep list, comes first)."""
    found: list[Path] = []
    extra = os.environ.get("LIVEBRIDGE_VST3_PATH", "").strip()
    candidates = [Path(p).expanduser() for p in extra.split(os.pathsep) if p.strip()]
    system = platform.system()
    if system == "Windows":
        for var in ("COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)"):
            if os.environ.get(var):
                candidates.append(Path(os.environ[var]) / "VST3")
        if os.environ.get("LOCALAPPDATA"):
            candidates.append(Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Common" / "VST3")
    elif system == "Darwin":
        candidates += [Path("/Library/Audio/Plug-Ins/VST3"),
                       Path.home() / "Library" / "Audio" / "Plug-Ins" / "VST3"]
    else:
        candidates += [Path.home() / ".vst3", Path("/usr/lib/vst3"), Path("/usr/local/lib/vst3")]
    for folder in candidates:
        try:
            if folder.is_dir() and folder not in found:
                found.append(folder)
        except OSError:
            continue
    return found


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def find_vst3(name: str, folders: list[Path] | None = None) -> list[dict[str, str]]:
    """``.vst3`` bundles/files matching a plug-in name ("Serum 2" -> Serum2.vst3), best first."""
    wanted = _norm(name)
    hits: list[tuple[int, dict[str, str]]] = []
    seen = 0
    for folder in folders if folders is not None else vst3_folders():
        for root, dirs, files in os.walk(folder):
            entries = [d for d in dirs if d.lower().endswith(".vst3")] + \
                [f for f in files if f.lower().endswith(".vst3")]
            dirs[:] = [d for d in dirs if not d.lower().endswith(".vst3") and
                       not d.startswith(".")]
            for entry in entries:
                stem = entry[:-5]
                key = _norm(stem)
                if not wanted or not key:
                    continue
                if key == wanted:
                    rank = 0
                elif key.startswith(wanted) or wanted.startswith(key):
                    rank = 1
                elif wanted in key or key in wanted:
                    rank = 2
                else:
                    continue
                hits.append((rank, {"file": stem, "path": str(Path(root) / entry)}))
            seen += 1
            if seen > 5000:
                break
    hits.sort(key=lambda h: (h[0], len(h[1]["file"])))
    unique, files = [], set()
    for _rank, hit in hits:
        if hit["file"].lower() not in files:
            files.add(hit["file"].lower())
            unique.append(hit)
    return unique


def _is_error(value: Any) -> bool:
    return isinstance(value, dict) and "error" in value and "type" in value


def _failure(exc: engine.EngineError, cmd: str) -> dict[str, Any]:
    kind = exc.type if exc.type in ("bad_args", "not_found", "invalid_state") else "connection"
    return tool_error(str(exc), type=kind, cmd=cmd)


def _hosts() -> list[dict[str, Any]]:
    return engine.devices(kind="host")


def _host_on(track: str | None) -> dict[str, Any]:
    found = _hosts()
    if not found:
        raise engine.EngineError("not_found", "no LiveBridge Host device is running — load a "
                                 "plug-in with live_plugin_host_load")
    if track is None:
        loaded = [d for d in found if d.get("plugin")]
        if len(loaded) == 1:
            return loaded[0]
        raise engine.EngineError("bad_args", "several hosts run — say which track: "
                                 + ", ".join(repr(d.get("track")) for d in found))
    for device in found:
        if str(device.get("track") or "").lower() == track.strip().lower():
            return device
    raise engine.EngineError("not_found", f"no LiveBridge Host on track {track!r} — running "
                             "on: " + ", ".join(repr(d.get("track")) for d in found))


def register(mcp: Any, bridge: BridgeClient) -> None:
    """Register the plug-in host tools on the MCP app."""

    @mcp.tool()
    def live_plugin_host_load(
        plugin: str,
        kind: str = "instrument",
        track: str | None = None,
        track_name: str | None = None,
        file: str | None = None,
    ) -> Any:
        """Load a VST3 plug-in with ALL of its parameters reachable — past the 128 that Live's
        Configure allows (Serum 2: ~540, Diva ~280, Pro-Q 4 ~600). It goes inside a LiveBridge
        Host Max for Live device (Max's vst~), on a new MIDI track (instrument) or at the end of
        `track`. Then `live_plugin_host_parameters` / `live_plugin_host_set` (experimental).

        Args:
            plugin: the plug-in's name ("Serum 2", "Diva", "Pro-Q 4").
            kind: "instrument" (a synth, MIDI in) or "effect" (audio through).
            track: existing track name (default for an instrument: a new MIDI track).
            track_name: name for the new track (default: the plug-in's name).
            file: the VST3 file name when the name search guesses wrong ("Serum2").

        Returns:
            `{track, plugin:{file, name, synth}, parameters, hidden_midi, vst3_path?}`.

        Gotchas: the plug-in's parameters are not Live parameters: they can't be automated
        (load the plug-in natively with `live_plugin_expose` for that, or modulate with
        `live_engine_modulate`). A user's existing plug-in can't be moved into a host with its
        settings — Live's API can't read plug-in state. VST3 only; the set needs Max for Live.
        Undo covers the plug-in's whole state at once.
        """
        cmd = "plugin_host.load"
        if kind not in _DEVICES:
            return tool_error("kind must be 'instrument' or 'effect'", cmd=cmd)
        if not plugin.strip():
            return tool_error("plugin must name a plug-in", cmd=cmd)
        if kind == "effect" and not track:
            return tool_error("an effect needs track= (the track it processes)", cmd=cmd)
        vst3_path = None
        if file is None:
            matches = find_vst3(plugin)
            if not matches:
                return tool_error(f"no VST3 named like {plugin!r} in "
                                  + ", ".join(str(f) for f in vst3_folders())
                                  + " — pass file= (the .vst3 name) or set "
                                    "LIVEBRIDGE_VST3_PATH", type="not_found", cmd=cmd)
            file, vst3_path = matches[0]["file"], matches[0]["path"]
        device_name, category = _DEVICES[kind]
        before = {d.get("instance") for d in _hosts()}
        load_args: dict[str, Any] = {"query": device_name, "category": category}
        if track:
            load_args["track"] = track
        else:
            load_args.update({"new_track": "midi", "track_name": track_name or plugin})
        loaded = bridge_call(bridge, "browser.load", load_args)
        if _is_error(loaded):
            if loaded.get("type") == "not_found":
                loaded["error"] += (f" — is the {device_name} device installed (the LiveBridge "
                                    "installer puts it in the User Library) and Max for Live "
                                    "available?")
            return loaded
        end = time.time() + _WAIT_S
        host = None
        while time.time() < end and host is None:
            fresh = [d for d in _hosts() if d.get("instance") not in before]
            host = next((d for d in fresh if d.get("role") == "track"), None) or \
                (fresh[0] if fresh and time.time() > end - _WAIT_S / 2 else None)
            if host is None:
                time.sleep(0.25)
        if host is None:
            return tool_error(f"{device_name} was loaded but did not start (Node for Max): "
                              "check Max for Live, then retry", type="invalid_state", cmd=cmd)
        try:
            result = engine.request(host, "load", {"file": file, "sub": plugin,
                                                   "name": plugin}, timeout=_LOAD_TIMEOUT)
        except engine.EngineError as exc:
            return _failure(exc, cmd)
        result = dict(result or {})
        result["track"] = host.get("track")
        if vst3_path:
            result["vst3_path"] = vst3_path
        return result

    @mcp.tool()
    def live_plugin_host_parameters(
        track: str | None = None,
        search: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> Any:
        """List a hosted plug-in's parameters (all of them, not just 128) with their values
        and display text; search by name. The per-MIDI-CC stand-in parameters VST3 plug-ins
        add are left out. Read-only.

        Args:
            track: the track whose LiveBridge Host to read (optional when there is one).
            search: case-insensitive part of the name ("filter", "env 2", "osc a").
            offset, limit: paging (limit 1..500).

        Returns:
            `{plugin, total, offset, count, parameters:[{id ("h12"), name, value (0..1),
            display}], next_offset?}`.
        """
        cmd = "plugin_host.params"
        if not 1 <= limit <= 500 or offset < 0:
            return tool_error("limit must be 1..500 and offset >= 0", cmd=cmd)
        try:
            host = _host_on(track)
            return engine.request(host, "params", {"search": search, "offset": offset,
                                                   "limit": limit}, timeout=_SET_TIMEOUT)
        except engine.EngineError as exc:
            return _failure(exc, cmd)

    @mcp.tool()
    def live_plugin_host_set(
        values: dict[str, Any],
        track: str | None = None,
        editor: bool | None = None,
    ) -> Any:
        """Set any parameters of a hosted plug-in: by name or id ("h12"), to a number 0..1 or
        the text the plug-in shows ("1.2 kHz", "-6 dB", "35 %", "Saw", "1/16 dot") — the value
        is searched by reading the plug-in's own display. One request for many parameters.

        Args:
            values: `{"Filter 1 Freq": "800 Hz", "A Uni Detune": 0.25, "h40": "On"}`.
            track: the track whose LiveBridge Host to change (optional when there is one).
            editor: also open (true) or close (false) the plug-in's own window.

        Returns:
            `{plugin, applied, results:[{name, id, ok, value?, display?, error?}],
            editor?}`; a text no value displays leaves the parameter as it was.
        """
        cmd = "plugin_host.set"
        if not isinstance(values, dict) or not values:
            return tool_error("values must be a non-empty {name: value} object", cmd=cmd)
        bad = [k for k, v in values.items() if isinstance(v, bool) or
               not isinstance(v, (int, float, str))]
        if bad:
            return tool_error(f"values must be numbers 0..1 or display text: {bad}", cmd=cmd)
        try:
            host = _host_on(track)
            result = engine.request(host, "set", {"values": values}, timeout=_SET_TIMEOUT)
            if editor is not None:
                engine.request(host, "editor", {"open": bool(editor)})
                result = dict(result or {}, editor=bool(editor))
            return result
        except engine.EngineError as exc:
            return _failure(exc, cmd)

    @mcp.tool()
    def live_plugin_host_bank(
        parameters: list[str],
        track: str | None = None,
        name: str | None = None,
        replace: bool = True,
    ) -> Any:
        """Make chosen parameters of a hosted plug-in automatable: writes a per-plug-in host
        device whose Live dials are named after them (up to 128 — Live reads a device's
        parameters when it loads, so they are baked into the file), loads it on the same track,
        copies every parameter value across and removes the old host. Then record the dials
        like any device knob (`live_automation_record`, clip envelopes, Arrangement Record) or
        map them to rack macros.

        Args:
            parameters: names or ids ("h12") from `live_plugin_host_parameters`, 1..128.
            track: the track whose LiveBridge Host to bank (optional when there is one).
            name: device file name (default "<plug-in> Bank"); saved to User Library >
                LiveBridge Presets > Plug-in Hosts and reusable from the browser.
            replace: remove the old host after the values are copied (false: keep both).

        Returns:
            `{device_file, browser_path, track, bank:[{name, id}], restored, removed_old,
            notes}`.

        Gotchas: parameter values move across; plug-in state that is not a parameter
        (wavetables, samples, a preset's internal data) does not — make the bank before
        designing such a sound, or reload the preset in the plug-in's window. The dials run
        0..1 (the plug-in's window shows the real value). Edits in the plug-in's own window do
        not move the dials. Needs Live connected on this machine.
        """
        cmd = "plugin_host.bank"
        if not isinstance(parameters, list) or not 1 <= len(parameters) <= host_device.MAX_BANK:
            return tool_error(f"parameters must list 1..{host_device.MAX_BANK} names or ids",
                              cmd=cmd)
        try:
            old = _host_on(track)
            hello = engine.request(old, "hello")
            plugin = (hello or {}).get("plugin")
            if not plugin:
                return tool_error("that host has no plug-in loaded", type="invalid_state",
                                  cmd=cmd)
            resolved = engine.request(old, "resolve", {"names": parameters},
                                      timeout=_SET_TIMEOUT)["parameters"]
            missing = [p["name"] for p in resolved if p.get("error")]
            if missing:
                return tool_error(f"unknown parameter(s) {missing} — see "
                                  "live_plugin_host_parameters", type="not_found", cmd=cmd)
            snapshot = engine.request(old, "values", timeout=_SET_TIMEOUT)["values"]
        except engine.EngineError as exc:
            return _failure(exc, cmd)
        places = bridge_call(bridge, "samples.locations")
        libraries = [Path(p["path"]) for p in (places.get("user_library") or [])
                     if isinstance(p, dict) and p.get("exists") and p.get("path")] \
            if isinstance(places, dict) and not _is_error(places) else []
        if not libraries:
            return tool_error("Live's User Library was not found (is Live connected and on "
                              "this machine?)", type="not_found", cmd=cmd)
        scripts = _js_sources(libraries)
        if scripts is None:
            return tool_error("lbhost.js was not found — re-run the LiveBridge installer",
                              type="not_found", cmd=cmd)
        instrument = old.get("host_kind") == "instrument" or hello.get("host_kind") == \
            "instrument"
        bank = [{"name": p["name"], "index": p["index"]} for p in resolved]
        payload = host_device.bank_device({"file": plugin.get("file"),
                                           "sub": plugin.get("name"),
                                           "name": plugin.get("name")}, instrument, bank)
        try:
            path = write_bank(libraries[0].joinpath(*_BANK_FOLDER),
                              name or f"{plugin.get('name') or plugin.get('file')} Bank",
                              payload, scripts)
        except OSError as exc:
            return tool_error(f"could not write the bank device: {exc}", type="invalid_state",
                              cmd=cmd)
        browser_path = "user_library/" + "/".join(_BANK_FOLDER) + "/" + path.name
        where = old.get("track")
        before = {d.get("instance") for d in _hosts()}
        args: dict[str, Any] = {"path": browser_path, "track": where}
        answer: Any = None
        for attempt in range(_BROWSER_TRIES):
            answer = bridge_call(bridge, "browser.load", args, timeout=60.0)
            if not (isinstance(answer, dict) and answer.get("type") == "not_found"):
                break
            if attempt == 0:
                args["path"] = browser_path[:-len(".amxd")]
            time.sleep(1.5)
        result: dict[str, Any] = {"device_file": str(path), "browser_path": browser_path,
                                  "track": where, "notes": []}
        if _is_error(answer):
            result.update(loaded=False, error=answer.get("error"))
            result["notes"].append("The device is saved: drag it from User Library > "
                                   "LiveBridge Presets > Plug-in Hosts onto the track.")
            return result
        end = time.time() + _WAIT_S + _LOAD_TIMEOUT
        new = None
        while time.time() < end:
            fresh = [d for d in _hosts() if d.get("instance") not in before and d.get("plugin")]
            if fresh:
                new = fresh[0]
                break
            time.sleep(0.5)
        if new is None:
            result.update(loaded=False, error="the bank device did not report its plug-in")
            return result
        try:
            restored = engine.request(new, "restore", {"values": snapshot},
                                      timeout=_SET_TIMEOUT)["restored"]
        except engine.EngineError as exc:
            restored = 0
            result["notes"].append(f"values not copied: {exc}")
        result.update(loaded=True, restored=restored,
                      bank=[{"name": p["name"], "id": p["id"]} for p in resolved])
        removed = False
        if replace and any(d.get("instance") == old.get("instance") for d in _hosts()):
            listing = bridge_call(bridge, "devices.list", {"track": where})
            devices = listing.get("devices") if isinstance(listing, dict) else None
            candidates = [d for d in devices or [] if str(d.get("name")) in _GENERIC
                          or (str(d.get("name")) == str(hello.get("device_name") or ""))]
            if len(candidates) == 1:
                gone = bridge_call(bridge, "devices.delete", {"track": where,
                                                             "device": candidates[0]["path"]})
                removed = not _is_error(gone)
            if not removed:
                result["notes"].append("The old host is still on the track: delete it once "
                                       "the bank sounds right.")
        else:
            removed = replace          # Live replaced the instrument itself
        result["removed_old"] = removed
        result["notes"].append("Dials run 0..1; the plug-in's window shows real values. "
                               "Record them like any knob (live_automation_record).")
        return result

    @mcp.tool()
    def live_plugin_host_preset(
        source_path: str,
        track: str | None = None,
        template: str | None = None,
    ) -> Any:
        """Load a preset file into a hosted plug-in, so a patch designed as a file can then be
        fine-tuned parameter by parameter on the same instance: a `.vstpreset` (any VST3),
        or a Serum 2 `.SerumPreset` / design JSON from `live_preset_edit`, which is first packed
        into a `.vstpreset` the way `live_preset_load` does (User Library > LiveBridge Presets >
        Serum 2, with your same-build template). Bank dials follow the new values.

        Args:
            source_path: the preset: absolute path, or a bare name in the Serum designs folder.
            track: the track whose LiveBridge Host gets it (optional when there is one).
            template: Serum 2 only — the `.vstpreset` template to pack with (default: found
                in the User Library by build).

        Returns:
            `{path, preset_name?, sampled, changed, notes}` — `changed`: how many of the first
            parameters moved (0 = the preset did not take).

        Gotchas: the preset must be for the hosted plug-in. vst~ reports nothing back, so the
        check is a before/after comparison. `.fxp`/`.fxb` files load too (VST2 formats).
        """
        cmd = "plugin_host.preset"
        path = Path(os.path.expanduser(source_path))
        suffix = path.suffix.lower()
        result: dict[str, Any] = {"notes": []}
        try:
            host = _host_on(track)
        except engine.EngineError as exc:
            return _failure(exc, cmd)
        if suffix in (".vstpreset", ".fxp", ".fxb"):
            if not path.is_file():
                return tool_error(f"{path} does not exist", type="not_found", cmd=cmd)
        else:
            places = bridge_call(bridge, "samples.locations")
            libraries = [Path(p["path"]) for p in (places.get("user_library") or [])
                         if isinstance(p, dict) and p.get("exists") and p.get("path")] \
                if isinstance(places, dict) and not _is_error(places) else []
            try:
                path, name, _report = serum.vstpreset_for(source_path, libraries, template)
            except FileNotFoundError as exc:
                return tool_error(str(exc), type="not_found", cmd=cmd)
            except ImportError as exc:
                return tool_error(f"Serum preset files need the serum extra ({exc})",
                                  type="unsupported", cmd=cmd)
            except (ValueError, OSError) as exc:
                return tool_error(str(exc), type="invalid_state", cmd=cmd)
            result["preset_name"] = name
        try:
            answer = engine.request(host, "preset", {"path": str(path)}, timeout=_SET_TIMEOUT)
        except engine.EngineError as exc:
            return _failure(exc, cmd)
        result.update(answer or {})
        if not result.get("changed"):
            result["notes"].append("No sampled parameter changed: the preset may be for another "
                                   "plug-in, or already match — open the plug-in's window "
                                   "(live_plugin_host_set(editor=true)) to check.")
        return result
