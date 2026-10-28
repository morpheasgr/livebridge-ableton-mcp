"""Plug-in host tools — every parameter of a VST3 plug-in, past Live's 128 (experimental).

Live's API reaches only the plug-in parameters added with Configure (at most 128). The
LiveBridge Host Max for Live devices (``max_for_live/LiveBridge Host``: an effect and an
instrument) load the plug-in inside Max's ``vst~`` instead, which lists and sets all of them:
`live_plugin_host_load` puts one on a track and loads the plug-in, `live_plugin_host_parameters`
lists and searches its parameters, `live_plugin_host_set` sets them by number or display text.

Limits: hosted parameters are not Live parameters (no automation lanes — use
`live_engine_modulate` or `live_plugin_expose` for a natively loaded copy), the plug-in runs
inside Max (Max for Live needed to open the set), VST3 only. Device protocol: like the
Engine's (:mod:`livebridge_mcp.engine`), registry kind "host".
"""

from __future__ import annotations

import os
import platform
import re
import time
from pathlib import Path
from typing import Any

from . import bridge_call, tool_error
from .. import engine
from ..client import BridgeClient

_DEVICES = {"effect": ("LiveBridge Host Effect", "audio_effect"),
            "instrument": ("LiveBridge Host Instrument", "instrument")}
_WAIT_S = 12.0
_LOAD_TIMEOUT = 30.0
_SET_TIMEOUT = 60.0


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
            load_args.update({"new_track": True, "track_name": track_name or plugin})
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
