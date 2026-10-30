"""Mixing and mastering by measurement: `live_sound_check` (does every track make a healthy
sound before you build on it), `live_mix_balance` (faders to role levels, measured with the
LiveBridge Engine, plus where each band problem belongs: a track or the master) and
`live_master_auto` (a master chain driven to a loudness target with guardrails).

The listening comes from LiveBridge Engine devices (:mod:`livebridge_mcp.engine`): one on the
master and one at the end of each track to balance. The decisions are in
:mod:`livebridge_mcp.mix`.
"""

from __future__ import annotations

import functools
import inspect
import math
import re
import time
from typing import Any

from . import bridge_call, tool_error
from .. import engine, jobs, library, mix
from ..client import BridgeClient
from .engine import _HOW_TO_ADD, _engine_failure

_MAX_PASSES = 4
_MAX_STEPS = 8
_SETTLE_S = 1.0
_PLACE_WAIT_S = 30.0
_PLUGIN_LIMITER_RE = re.compile(r"limit|maxim|pro-l|ozone|\bl[123]\b|elevate|loudmax", re.I)


def _is_error(value: Any) -> bool:
    return isinstance(value, dict) and "error" in value and "type" in value


def _num(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def _reading(result: Any) -> dict[str, Any]:
    loud = (result or {}).get("loudness") or {}
    return {"lufs": loud.get("lufs"), "peak_db": loud.get("peak_db"),
            "crest_db": loud.get("crest_db")}


#: The Max device of chain="livebridge" (max_for_live/LiveBridge Master).
_MASTER_DEVICE = "LiveBridge Master"
#: LiveBridge Master: how far its compressor's makeup may drive the limiter beyond Limit Gain,
#: and the total drive above which the report warns that the transients are being flattened
_MAKEUP_MAX_DB = 12.0
_SQUASH_DB = 12.0
#: band of live_mix_balance's `bands` -> (LiveBridge Master EQ control, mid frequency in Hz)
_MASTER_EQ_BANDS: dict[str, tuple[str, float | None]] = {
    "sub": ("EQ Low", None), "bass": ("EQ Low", None), "low_mid": ("EQ Mid", 180.0),
    "mud": ("EQ Mid", 350.0), "mid": ("EQ Mid", 700.0), "presence": ("EQ Mid", 3500.0),
    "high_mid": ("EQ Mid", 3500.0), "high": ("EQ High", None), "air": ("EQ High", None)}
#: The polarity / delay / all-pass device of live_mix_phase (max_for_live/LiveBridge Phase).
_PHASE_DEVICE = "LiveBridge Phase"
#: The side-chained dynamic EQ of live_mix_unmask (max_for_live/LiveBridge Unmask).
_UNMASK_DEVICE = "LiveBridge Unmask"
#: Frequency controls of the LiveBridge devices: their dials follow a curve, so Live's raw value
#: is not in Hz — they are set as display text ("300 Hz"), which devices.set_parameters finds.
_HZ_PARAMS = frozenset({"HP Freq", "Mid Freq", "Bass Mono Freq", "EQ Mid Freq", "Phase Freq",
                        "Freq"})


def _for_live(values: dict[str, Any]) -> dict[str, Any]:
    """Device values ready for devices.set_parameters: frequencies as "<n> Hz" text."""
    return {name: (f"{float(value):g} Hz" if name in _HZ_PARAMS and
                   isinstance(value, (int, float)) else value)
            for name, value in values.items()}


#: Side-chain timing by the dominant part's role: (attack ms, release ms).
_UNMASK_TIMING = {"kick": (2.0, 100.0), "snare": (2.0, 120.0), "drums": (3.0, 120.0),
                  "perc": (3.0, 100.0), "vocal": (10.0, 250.0), "lead": (10.0, 200.0)}


def _chain_devices(kind: str) -> tuple[tuple[str, str], ...]:
    """(device name, class name or _MASTER_DEVICE) of a master chain, in order."""
    if kind == "livebridge":
        return ((_MASTER_DEVICE, _MASTER_DEVICE),)
    return (("EQ Eight", "Eq8"), ("Glue Compressor", "GlueCompressor"), ("Limiter", "Limiter"))


#: What live_mix_auto last set on each Channel (track name -> parameter -> value): a value that
#: differs from both this and the rest value was set by hand and is kept on the next run.
_STRIPS_FILE = "channel_strips.json"
#: per set and track what the balance did to a fader (where it left it, how far it raised and
#: moved it in all, whether it counted the part): limits and the one-shot check carry over
#: from run to run while the fader stays there
_BALANCE_FILE = "balance_state.json"
#: the one-shot check's hysteresis: a part the last run counted is skipped only below
#: min_active - this, one it skipped is counted only above min_active + this
_ACTIVE_MARGIN = 0.1
_NUMBER_RE = re.compile(r"(-?\d+(?:[.,]\d+)?)\s*(k?)", re.I)


def _strip_number(param: dict[str, Any]) -> float | None:
    """A Channel parameter's value in its own units (Hz, dB, ms, %, 0/1) from its display."""
    if param.get("name") == "Bass Mono":
        return float(param.get("value") or 0.0)
    match = _NUMBER_RE.search(str(param.get("display") or ""))
    if not match:
        return None
    number = float(match.group(1).replace(",", "."))
    return number * 1000.0 if match.group(2) else number


def _same(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return True
    return abs(a - b) <= max(0.05, abs(b) * 0.01)


def _fmt(value: float | None) -> str:
    return "?" if value is None else f"{value:g}"


def _summary(result: dict[str, Any]) -> dict[str, Any]:
    """live_mix_auto's report without the bulk (every reading and reason): per part what moved
    and what was measured, the balance moves, the master outcome — detail="full" keeps all."""
    short = {k: v for k, v in result.items() if k not in ("tracks", "balance", "master",
                                                           "low_end")}
    short["tracks"] = [{k: v for k, v in t.items() if k in (
        "name", "role", "changes", "kept", "measured", "balance", "error")}
        for t in result.get("tracks", [])]
    balance = result.get("balance")
    if isinstance(balance, dict):
        plan = balance.get("plan") or {}
        short["balance"] = {k: v for k, v in balance.items() if k in (
            "applied", "genre", "headroom_trim_db", "notes", "error", "type", "job", "status")}
        for key in ("unchanged", "protected", "held"):
            if plan.get(key):
                short["balance"][key] = plan[key]
        short["balance"]["moves"] = [
            {k: v for k, v in m.items() if k in ("name", "fader_db", "new_fader_db", "move_db",
                                                 "capped", "budget_capped", "raise_capped",
                                                 "at_max")}
            for m in plan.get("moves") or []]
    master = result.get("master")
    if isinstance(master, dict):
        short["master"] = {k: v for k, v in master.items() if k in (
            "destination", "target_lufs", "before", "after", "limiter_gain_db", "makeup_db",
            "limiting_db", "peak_reduction_db", "reset", "eq", "steps", "settings",
            "headroom_trim_db", "device_check", "stopped", "missing_gain_db", "contrast", "notes", "error",
            "type")}
    low = result.get("low_end")
    if isinstance(low, dict):
        short["low_end"] = {name: {k: v for k, v in (part or {}).items() if k in (
            "applied", "gain_db", "band", "depth_db", "notes", "error")}
            for name, part in low.items()}
    short["detail"] = "summary — detail='full' for every reading and reason"
    return short


def _background(tool: str):
    """Run a long mix/master tool as a job (:mod:`livebridge_mcp.jobs`): its report as usual
    when it ends within ~40 s, else ``{job, status: "running"}`` and ``live_mix_job`` later."""
    def wrap(fn):
        @functools.wraps(fn)
        def run(*args: Any, **kwargs: Any) -> Any:
            try:
                return jobs.start(tool, lambda: fn(*args, **kwargs))
            except jobs.Busy as exc:
                return tool_error(str(exc), type="busy", cmd="mix.job")
        return run
    return wrap


def register(mcp: Any, bridge: BridgeClient) -> None:
    """Register the mix tools on the MCP app."""

    def playing() -> bool | None:
        state = bridge_call(bridge, "transport.get")
        if isinstance(state, dict) and not _is_error(state):
            return bool(state.get("is_playing"))
        return None

    def faders(names: list[str]) -> dict[str, dict[str, Any]]:
        """name -> {volume (dB or None at -inf), mute} for the named tracks."""
        state = bridge_call(bridge, "mixer.get", {"track": names, "detail": "minimal"})
        found: dict[str, dict[str, Any]] = {}
        if isinstance(state, dict) and isinstance(state.get("tracks"), list):
            for name, entry in zip(names, state["tracks"]):
                if isinstance(entry, dict):
                    found[name] = {"volume": _num(entry.get("volume")),
                                   "mute": bool(entry.get("mute"))}
        return found

    def master_chain_before_engine() -> list[str]:
        """The active devices in front of the master's Engine: its peak is then theirs (a
        limiter's output), not the mix's."""
        listing = bridge_call(bridge, "devices.list", {"track": "master"})
        before: list[str] = []
        for device in (listing.get("devices") or []) if isinstance(listing, dict) and \
                not _is_error(listing) else []:
            name = str(device.get("name") or "")
            if "livebridge engine" in name.lower():
                return before
            if device.get("is_active", True):
                before.append(name)
        return []

    def current_set() -> str:
        """The open set's key for per-set memories: its path, else its name, else "unsaved"."""
        summary = bridge_call(bridge, "song.summary", {"detail": "minimal"})
        if isinstance(summary, dict) and not _is_error(summary):
            return str(summary.get("set_path") or summary.get("set_name") or "unsaved")
        return "unsaved"

    #: the loop a run holds (its start in beats), set by ``looped`` for the outermost run
    held_loop: dict[str, float | None] = {"start": None, "bar_beats": 4.0}

    def rewind() -> None:
        """Restart the held loop from its first bar before a listen: a window shorter than the
        loop (16 bars at 124 BPM are 31 s) otherwise hears whatever bars play then, and
        before/after or two passes compare different music (a master 'before' read 6 dB hot)."""
        start = held_loop["start"]
        if start is None:
            return
        answer = bridge_call(bridge, "transport.play", {"position": start})
        if _is_error(answer):
            return
        # Live quantizes the jump (to the next bar): wait until the playhead is there
        end = time.time() + 4.0
        while time.time() < end:
            state = bridge_call(bridge, "transport.get")
            beats = _num(((state.get("position") or {}).get("beats"))) \
                if isinstance(state, dict) else None
            if beats is None or start <= beats < start + held_loop["bar_beats"]:
                break
            time.sleep(0.1)
        time.sleep(_SETTLE_S)

    def listen_all(devices: list[dict[str, Any]], seconds: float) -> list[Any]:
        rewind()
        return engine.each(devices, lambda d: engine.listen(d, seconds))

    def before_meter(track: str, index: int) -> int | Any:
        """Move the device at ``index`` in front of the track's meter (its LiveBridge Channel
        or Engine), so the meter and the Channel's strip hear what it does; the new index."""
        listing = bridge_call(bridge, "devices.list", {"track": track})
        meter_at = next((d.get("index") for d in (listing.get("devices") or [])
                         if "livebridge channel" in str(d.get("name", "")).lower()
                         or "livebridge engine" in str(d.get("name", "")).lower()), None) \
            if isinstance(listing, dict) else None
        if meter_at is None or meter_at > index:
            return index
        moved = bridge_call(bridge, "devices.move", {"track": track, "device": index,
                                                     "position": meter_at})
        return moved if _is_error(moved) else meter_at

    def load_device(device_name: str, track: str) -> Any:
        """``browser.load`` a LiveBridge device onto ``track``; a "not found" is retried once
        with a cleared browser cache (a device installed after the cache was built)."""
        args = {"query": device_name, "category": "audio_effect", "track": track}
        loaded = bridge_call(bridge, "browser.load", args, timeout=60.0)
        if _is_error(loaded) and loaded.get("type") == "not_found":
            bridge_call(bridge, "browser.cache", {"action": "clear"})
            loaded = bridge_call(bridge, "browser.load", args, timeout=60.0)
        return loaded

    def hold_loop(seconds: float) -> dict[str, Any] | None:
        """Loop the bars that play now for a run (the playhead must not wander into another
        section while it listens); None when the arrangement loop is already on (the user's
        own loop is kept) or the song is stopped. Returns what :func:`release_loop` restores."""
        state = bridge_call(bridge, "transport.get")
        if _is_error(state) or not isinstance(state, dict) or not state.get("is_playing"):
            return None
        loop = state.get("loop") or {}
        tempo = float(state.get("tempo") or 120.0)
        if loop.get("on"):                        # the user's own loop: keep it, listen to it
            return {"own": True,
                    "loop_seconds": float(loop.get("length") or 0.0) * 60.0 / tempo}
        match = re.match(r"^(\d+)/(\d+)$", str(state.get("signature", "")))
        per_bar = int(match.group(1)) * 4.0 / int(match.group(2)) if match and \
            int(match.group(2)) else 4.0
        position = float(((state.get("position") or {}).get("beats")) or 0.0)
        bars = max(4, int(math.ceil((seconds + 2.0) * tempo / 60.0 / per_bar / 4.0)) * 4)
        start = math.floor(position / per_bar) * per_bar
        held = bridge_call(bridge, "transport.set_loop",
                           {"start": start, "length": bars * per_bar, "enabled": True})
        if _is_error(held):
            return None
        return {"start": loop.get("start"), "length": loop.get("length"),
                "held": f"bars {int(start / per_bar) + 1}-{int(start / per_bar) + bars}",
                "loop_seconds": bars * per_bar * 60.0 / tempo}

    def release_loop(saved: dict[str, Any] | None) -> None:
        if saved is None or saved.get("own"):
            return
        args: dict[str, Any] = {"enabled": False}
        if saved.get("start") is not None and saved.get("length"):
            args.update(start=saved["start"], length=saved["length"])
        bridge_call(bridge, "transport.set_loop", args)

    def start_section(bars: list[int] | None) -> dict[str, Any] | None:
        """Play what a run should hear when it has to start the transport itself: ``bars``
        (looped), or the arrangement loop when it is on and the song is stopped. What
        :func:`stop_section` undoes; None when nothing was started; a tool error when ``bars``
        is wrong."""
        state = bridge_call(bridge, "transport.get")
        if _is_error(state) or not isinstance(state, dict):
            return None
        loop = state.get("loop") or {}
        was_playing = bool(state.get("is_playing"))
        # starting at a bar moves Live's start marker: it goes back afterwards
        marker = _num((state.get("start_time") or {}).get("beats")) \
            if isinstance(state.get("start_time"), dict) else None
        if bars is not None:
            if not (isinstance(bars, (list, tuple)) and len(bars) == 2 and all(
                    isinstance(b, (int, float)) and b >= 1 for b in bars) and bars[1] >= bars[0]):
                return tool_error("section_bars must be [first, last] (1-based, last >= first)",
                                  cmd="mix.section")
            first, last = int(bars[0]), int(bars[1])
            held = bridge_call(bridge, "transport.set_loop", {
                "start_bar": first, "bars": last - first + 1, "enabled": True})
            if _is_error(held):
                return held
            played = bridge_call(bridge, "transport.play", {"position": f"{first}.1.1"})
            if _is_error(played):
                return played
            time.sleep(1.0)                    # let the section sound before listening
            return {"stop": not was_playing, "loop": {
                "start": loop.get("start"), "length": loop.get("length"),
                "on": bool(loop.get("on"))}, "bars": f"bars {first}-{last}",
                "marker": marker}
        if was_playing or not loop.get("on"):
            return None
        played = bridge_call(bridge, "transport.play", {"position": loop.get("start") or 0.0})
        if _is_error(played):
            return None
        time.sleep(1.0)
        return {"stop": True, "bars": "the arrangement loop", "marker": marker}

    def stop_section(started: dict[str, Any] | None) -> None:
        if not started or "error" in started:
            return
        if started.get("stop"):
            bridge_call(bridge, "transport.stop")
            if started.get("marker") is not None:
                bridge_call(bridge, "transport.set_position",
                            {"position": started["marker"], "set_start_marker": True})
        old = started.get("loop")
        if old is not None:
            args: dict[str, Any] = {"enabled": old["on"]}
            if old.get("start") is not None and old.get("length"):
                args.update(start=old["start"], length=old["length"])
            bridge_call(bridge, "transport.set_loop", args)

    #: the listening windows a tool accepts: a loop that fits is listened to exactly once
    #: per window, so every measurement (before/after, each pass) hears the same bars
    window_limits = {"live_mix_auto": (3.0, 20.0), "live_mix_balance": (3.0, 30.0),
                     "live_mix_unmask": (2.0, 15.0)}

    def looped(fn):
        """Hold the playing bars in a loop for the whole run (outermost run only: inside a run
        the loop is already on), then give the user's loop back."""
        signature = inspect.signature(fn)
        limits = window_limits.get(fn.__name__)

        @functools.wraps(fn)
        def run(*args: Any, **kwargs: Any) -> Any:
            jobs.enter()
            try:
                return looped_run(*args, **kwargs)
            finally:
                jobs.leave()

        def looped_run(*args: Any, **kwargs: Any) -> Any:
            bound = signature.bind_partial(*args, **kwargs)
            bound.apply_defaults()
            if bound.arguments.get("loud_bar") is not None:
                return fn(*args, **kwargs)        # it jumps between sections itself
            started = None
            if not jobs.nested() and "section_bars" in bound.arguments:
                started = start_section(bound.arguments.get("section_bars"))
                if isinstance(started, dict) and "error" in started:
                    return started
            try:
                return held_run(bound, started)
            finally:
                stop_section(started)

        def held_run(bound: inspect.BoundArguments, started: dict[str, Any] | None) -> Any:
            seconds = float(bound.arguments.get("seconds") or 6.0)
            saved = hold_loop(seconds)
            outermost = held_loop["start"] is None
            if outermost:
                state = bridge_call(bridge, "transport.get")
                loop = (state.get("loop") or {}) if isinstance(state, dict) else {}
                if isinstance(state, dict) and state.get("is_playing") and loop.get("on"):
                    held_loop["start"] = _num(loop.get("start"))
                    match = re.match(r"^(\d+)/(\d+)$", str(state.get("signature", "")))
                    held_loop["bar_beats"] = int(match.group(1)) * 4.0 / int(match.group(2)) \
                        if match and int(match.group(2)) else 4.0
            loop_seconds = (saved or {}).get("loop_seconds") or 0.0
            if limits and limits[0] <= loop_seconds <= limits[1] and \
                    limits[0] <= seconds <= limits[1] and not jobs.nested():
                bound.arguments["seconds"] = round(loop_seconds, 2)   # one full loop
            try:
                result = fn(*bound.args, **bound.kwargs)
            finally:
                if outermost:
                    held_loop["start"] = None
                release_loop(saved)
            if saved and not saved.get("own") and isinstance(result, dict) and \
                    isinstance(result.get("notes"), list):
                result["notes"].append(f"Looped {saved['held']} while measuring (the playhead "
                                       "would have moved on to another section); your loop "
                                       "setting is back.")
            if started and isinstance(result, dict) and isinstance(result.get("notes"), list):
                result["notes"].append(f"Played {started['bars']} for the run" + (
                    " and stopped again" if started.get("stop") else "") + "; your loop "
                    "setting is back.")
            return result
        return run

    def place_meters(tracks: list[str] | None = None, master: bool = True,
                     strips: bool = True, include_tracks: bool = True) -> dict[str, Any]:
        """Put the devices the mix tools need in place, so nobody has to drag them in: a
        LiveBridge Channel at the end of every track that makes sound (``strips``; it replaces
        a LiveBridge Engine there — two meters on a track only measure twice) or an Engine
        where ``strips`` is false, and an Engine on the master. Waits until they answer.
        ``{placed:[...], replaced:[...], skipped:[...], error?}``."""
        report: dict[str, Any] = {"placed": [], "replaced": [], "skipped": []}
        listing = bridge_call(bridge, "tracks.list", {"include_returns": False,
                                                      "include_master": False,
                                                      "detail": "summary"}) \
            if include_tracks else {"tracks": []}
        if _is_error(listing) or not isinstance(listing, dict):
            report["error"] = listing
            return report
        wanted = {t.lower() for t in tracks} if tracks else None
        expected: list[str] = []
        for item in listing.get("tracks") or []:
            name = str(item.get("name") or "")
            if item.get("type") not in ("midi", "audio") or (
                    wanted is not None and name.lower() not in wanted):
                continue
            devs = item.get("devices") or []
            names = [str(d.get("name") or "").lower() for d in devs]
            if item.get("type") == "midi" and not any(
                    d.get("type") == "instrument" or d.get("class_name") in (
                        "InstrumentGroupDevice", "DrumGroupDevice") for d in devs):
                report["skipped"].append(f"{name} (MIDI track without an instrument)")
                continue
            have_channel = any("livebridge channel" in n for n in names)
            have_engine = any("livebridge engine" in n for n in names)
            if have_channel or (have_engine and not strips):
                expected.append(name)
                continue
            if have_engine and strips:
                index = next(d.get("index") for d in devs
                             if "livebridge engine" in str(d.get("name") or "").lower())
                gone = bridge_call(bridge, "devices.delete", {"track": name, "device": index})
                if _is_error(gone):
                    report["skipped"].append(f"{name} (could not remove its Engine)")
                    continue
                report["replaced"].append(name)
            device_name = "LiveBridge Channel" if strips else "LiveBridge Engine"
            loaded = load_device(device_name, name)
            if _is_error(loaded):
                report["error"] = loaded
                if loaded.get("type") == "not_found":
                    loaded["error"] += (f" — is {device_name} installed (the LiveBridge "
                                        "installer puts it in the User Library) and Max for "
                                        "Live available" + (" (Max 9)" if strips else "") + "?")
                return report
            report["placed"].append(name)
            expected.append(name)
        need_master = False
        if master:
            found = engine.devices()
            if not any(d.get("role") == "master" for d in found):
                loaded = load_device("LiveBridge Engine", "master")
                if _is_error(loaded):
                    report["error"] = loaded
                    return report
                report["placed"].append("master")
                need_master = True
        if report["placed"]:
            # each device's Node script starts and registers within a few seconds
            end = time.time() + _PLACE_WAIT_S
            while time.time() < end:
                found = engine.devices()
                tracks_up = {str(d.get("track") or "").lower() for d in found
                             if d.get("role") == "track"}
                master_up = any(d.get("role") == "master" for d in found)
                if all(n.lower() in tracks_up for n in expected) and \
                        (master_up or not need_master):
                    break
                time.sleep(0.5)
            else:
                report["slow"] = "some devices did not answer yet — run again in a moment"
        return report

    # ------------------------------------------------------------------ sound check
    @mcp.tool()
    def live_sound_check(
        tracks: list[str] | None = None,
        seconds: float = 6.0,
        roles: dict[str, str] | None = None,
    ) -> Any:
        """Pre-flight before building an arrangement on the parts (and whenever something is
        missing, thin or inaudible): for every track, does it make sound, and is the sound
        healthy? Checks the chain (a MIDI track without an instrument, a muted track, another
        track soloed), each part's level while the song plays, a patch strangled by its filter
        (one band holding nearly all the energy, nothing above 2 kHz, no transients where there
        should be) and every filter cutoff against the working floor of the part's role (a lead
        low-passed at 400 Hz). The role comes from the name, else from the sound. Read-only.

        Args:
            tracks: track names (default: every regular track).
            seconds: listening window while the song plays (2..20) — loop a section where all
                parts play.
            roles: correct a guessed role: `{"Audio 3": "hats"}` (kick, snare, hats, perc,
                drums, sub, bass, pad, lead, keys, vocal, guitar, fx).

        Returns:
            `{playing, tracks:[{name, type, role, role_from, signal: ok|quiet|silent|unknown,
            lufs?, peak_db?, health:[...], filters:[{device, parameter, hz, type, caps_top,
            below_floor?}], problems:[...]}], order (loudest first), notes}`.

        Gotchas: levels need LiveBridge Engine devices at the end of the tracks (pre-fader);
        without them the tool still checks chains and filters, and reads Live's momentary
        meters for signal. A silent track: never raise its fader first — the `problems` list
        says where to look.
        """
        cmd = "mix.sound_check"
        if not 2 <= seconds <= 20:
            return tool_error("seconds must be 2..20", cmd=cmd)
        bad = [r for r in (roles or {}).values() if r not in mix.ROLES]
        if bad:
            return tool_error(f"unknown role(s) {bad}; use one of {', '.join(mix.ROLES)}",
                              cmd=cmd)
        listing = bridge_call(bridge, "tracks.list", {"include_returns": False,
                                                      "include_master": False,
                                                      "detail": "summary"})
        if _is_error(listing) or not isinstance(listing, dict):
            return listing
        items = [t for t in listing.get("tracks") or [] if t.get("type") in ("midi", "audio")]
        if tracks:
            wanted = {n.lower() for n in tracks}
            items = [t for t in items if str(t.get("name", "")).lower() in wanted]
            if not items:
                return tool_error(f"none of {tracks} is a regular track", type="not_found",
                                  cmd=cmd)
        is_playing = playing()
        notes: list[str] = []
        state = bridge_call(bridge, "transport.get")
        if isinstance(state, dict) and state.get("back_to_arranger"):
            notes.append("'Back to Arrangement' is lit: tracks where a session clip was fired "
                         "ignore their arrangement clips — live_transport_set("
                         "back_to_arranger=false) before playing the arrangement.")
        soloed = [t["name"] for t in items if t.get("solo")]
        readings: dict[str, dict[str, Any]] = {}
        try:
            devices = [d for d in engine.devices() if d.get("role") == "track"]
        except engine.EngineError:
            devices = []
        names = {str(t.get("name", "")).lower() for t in items}
        devices = [d for d in devices if str(d.get("track") or "").lower() in names]
        if devices and is_playing:
            for device, result in zip(devices, listen_all(devices, seconds)):
                if not isinstance(result, engine.EngineError):
                    readings[str(device.get("track")).lower()] = result
        meters: dict[str, float] = {}
        if is_playing and len(readings) < len(items):
            end = time.time() + min(seconds, 4.0)
            while True:
                snap = bridge_call(bridge, "mixer.meters", {"include_cpu": False})
                entries = snap.get("tracks") if isinstance(snap, dict) else None
                for entry in entries if isinstance(entries, list) else []:
                    level = _num(entry.get("peak")) or _num(entry.get("level")) or 0.0
                    key = str(entry.get("name", "")).lower()
                    meters[key] = max(meters.get(key, 0.0), level)
                if time.time() >= end:
                    break
                time.sleep(0.25)
        if not is_playing:
            notes.append("The transport is stopped: levels were not measured. Loop a section "
                         "where every part plays (live_transport_play) and check again.")
        elif not devices:
            notes.append("No LiveBridge Engine on these tracks: signal from Live's meters only "
                         "(no level, balance or patch health). " + _HOW_TO_ADD)
        results, order = [], []
        for track in items:
            name = str(track.get("name", ""))
            key = name.lower()
            reading = readings.get(key)
            role_info = mix.guess_role(name, reading)
            role = (roles or {}).get(name) or role_info["role"]
            entry: dict[str, Any] = {"name": name, "type": track.get("type"), "role": role,
                                     "role_from": "you" if (roles or {}).get(name)
                                     else role_info["from"]}
            problems: list[str] = []
            devs = track.get("devices") or []
            if track.get("type") == "midi" and not any(
                    d.get("type") in ("instrument",) or d.get("class_name") in (
                        "InstrumentGroupDevice", "DrumGroupDevice") for d in devs):
                problems.append("MIDI track without an instrument: it makes no sound.")
            if track.get("mute"):
                problems.append("The track is muted.")
            if soloed and not track.get("solo"):
                problems.append(f"Other tracks are soloed ({', '.join(soloed[:3])}): this one "
                                "is silenced by solo.")
            if any(d.get("class_name") == "DrumGroupDevice" for d in devs):
                problems.append("Drum Rack: notes on empty pads are silent — write only pads "
                                "the kit fills (live_drumrack_overview).")
            if reading is not None:
                loud = reading.get("loudness") or {}
                entry["lufs"] = loud.get("lufs")
                entry["peak_db"] = loud.get("peak_db")
                if reading.get("silent") or loud.get("lufs") is None:
                    entry["signal"] = "silent"
                elif loud["lufs"] < -40:
                    entry["signal"] = "quiet"
                else:
                    entry["signal"] = "ok"
                    order.append((loud["lufs"], name, role))
                entry["health"] = mix.patch_health(reading, role) \
                    if entry["signal"] != "silent" else []
            elif key in meters:
                entry["signal"] = "ok" if meters[key] > 0.02 else "silent"
                entry["health"] = []
            else:
                entry["signal"] = "unknown"
                entry["health"] = []
            if entry["signal"] == "silent" and is_playing:
                problems.append("Silent while the song plays: is a clip playing on this track "
                                "in this section (live_arrangement_clips / the session slot)? "
                                "Then the instrument, the drum pads, a closed filter, the "
                                "track's input/output routing.")
            elif entry["signal"] == "quiet":
                problems.append("Very quiet (below -40 LUFS): solo it and check — raise the "
                                "note velocities or the device output before the fader.")
            filters: list[dict[str, Any]] = []
            for device in devs[:12]:
                params: list[dict[str, Any]] = []
                for word in ("freq", "cutoff", "type"):
                    page = bridge_call(bridge, "devices.parameters",
                                       {"track": track.get("path") or name,
                                        "device": device.get("index", device.get("path")),
                                        "filter": word, "limit": 64, "detail": "minimal"})
                    if isinstance(page, dict) and not _is_error(page):
                        params += [p for p in page.get("parameters") or [] if p not in params]
                filters += mix.filter_cutoffs(str(device.get("name")), params, role)
            low = [f for f in filters if f.get("below_floor")]
            if low:
                worst = min(low, key=lambda f: f["hz"])
                problems.append(f"{worst['device']} '{worst['parameter']}' at {worst['hz']:g} "
                                f"Hz: below the ~{mix.FILTER_FLOORS[role]:g} Hz floor for a "
                                f"{role} — the sound is reduced to its fundamental. The lowest "
                                "low-pass in the chain wins: opening another filter won't fix "
                                "it.")
            entry["filters"] = filters
            entry["problems"] = problems
            results.append(entry)
        order.sort(key=lambda item: -item[0])
        answer: dict[str, Any] = {"playing": is_playing, "tracks": results,
                                  "order": [f"{n} ({r or '?'}) {l} LUFS" for l, n, r in order],
                                  "notes": notes}
        if order:
            ranks = {r: i for i, (_l, _n, r) in enumerate(order)}
            if "kick" in ranks and any(r in ranks and ranks[r] < ranks["kick"]
                                       for r in ("pad", "hats", "perc", "fx", "lead")):
                answer["notes"].append("Pre-fader, a pad/hats/lead/fx part is louder than the "
                                       "kick: expect a balance problem (live_mix_balance).")
        return answer

    # ------------------------------------------------------------------ balance
    @mcp.tool()
    @_background("live_mix_balance")
    @looped
    def live_mix_balance(
        genre: str | None = None,
        tracks: list[str] | None = None,
        roles: dict[str, str] | None = None,
        anchor: str | None = None,
        seconds: float = 8.0,
        apply: bool = True,
        passes: int = 2,
        headroom_db: float = 6.0,
        max_move_db: float = 6.0,
        min_active: float = 0.35,
        max_raise_db: float = 3.0,
        tolerance_db: float = 3.0,
        protect: list[str] | None = None,
        section_bars: list[int] | None = None,
        one_shots: list[str] | None = None,
    ) -> Any:
        """Balance the mix by measurement: listens to every track with a LiveBridge Engine at
        its end over the same window, gives each part its role's level (kick on top, then sub
        and bass, snare, vocal, lead, keys, percussion, pads, hats, fx — or a genre's levels),
        sets the faders in real dB (one undo step per pass), measures again until the levels
        settle, then trims everything together so the master keeps `headroom_db` of headroom.
        With an Engine on the master it also says where each spectral problem belongs: on the
        master/bus EQ when 3+ tracks share it, on a track when 1-2 own it.

        Args:
            genre: role levels and band balance of a genre (techno, house, melodic_house —
                leads/pads/keys close to the kick, for melodic house/techno and progressive —,
                trance, dnb, dubstep, hip_hop, trap, pop, rnb, lo_fi, ambient, breaks, funk,
                jazz, metal, country, reggaeton, classical, balanced). An unknown genre is
                refused; the answer names the profile used (`genre`, `genre_label`).
            tracks: only these tracks (default: every track device).
            roles: correct guessed roles: `{"Audio 3": "hats"}`.
            anchor: the track that keeps its level (default: the kick, else the loudest role).
            seconds: listening window (3..30) — loop the fullest section (the drop).
            apply: false = only report the plan.
            passes: measure/adjust rounds (1..4).
            headroom_db: wanted master peak below 0 dBFS (0..12; needs an Engine on the master).
            max_move_db: the most the balance moves any fader (1..24 dB), all passes and runs
                together while the fader stays where LiveBridge left it (moving it by hand
                starts over) — keeps a wrong role or an odd reading from throwing a fader, and
                a capped part from moving again on every rerun.
            min_active: parts that play less than this share of the window (0..1; one-shots,
                fills, a tom twice in 10 s) read too quiet on average: their faders are left
                alone and listed in the notes.
            max_raise_db: the most a fader goes UP (0..12), counted over runs like
                `max_move_db` — a part reading quiet is more often a bad window than a quiet
                part.
            tolerance_db: parts within this many dB of their role's level stay put (0..6): only
                the outliers move, and only to just inside the tolerance (not all the way to
                the role's level), so a second run changes nothing.
            protect: parts whose faders never move (the lead, the vocal); the others are
                centred as usual, so the mix as a whole keeps its level around them.
            section_bars: `[first, last]` bar (1-based) to loop and play while measuring — the tool
                starts the transport there and stops it again afterwards. Default: the bars
                playing now (or the arrangement loop when it is on and the song is stopped).
            one_shots: parts known to play only now and then in this section (left alone like
                a part under `min_active`) — `live_mix_auto` passes the parts its own listen
                found silent: one window missing a part and the next one catching it is a
                one-shot, whatever the second window's share says.

        Returns:
            `{genre?, applied, passes, parts:[{name, role, role_from, lufs (pre-fader),
            fader_db, post_lufs}], plan:{anchor, moves:[{name, role, offset_lu, now_lufs,
            target_lufs, fader_db, new_fader_db, move_db, passes}], unchanged}, master?:{lufs, peak_db,
            crest_db}, headroom_trim_db?, bands?:[{band, hz, delta_db, fix: master|tracks,
            tracks, advice}], notes}`.

        Gotchas: start playback first and loop the section to balance. Levels are integrated
        over the window (a part that plays only half of it reads ~3 dB low). Silent and muted
        tracks are left alone. Taste beats targets: say what moved and let the user listen.
        """
        cmd = "mix.balance"
        if not 3 <= seconds <= 30:
            return tool_error("seconds must be 3..30", cmd=cmd)
        if not 1 <= passes <= _MAX_PASSES:
            return tool_error(f"passes must be 1..{_MAX_PASSES}", cmd=cmd)
        if not 0 <= headroom_db <= 12:
            return tool_error("headroom_db must be 0..12", cmd=cmd)
        if not 1 <= max_move_db <= 24:
            return tool_error("max_move_db must be 1..24", cmd=cmd)
        if not 0 <= min_active <= 1:
            return tool_error("min_active must be 0..1", cmd=cmd)
        if not 0 <= max_raise_db <= 12:
            return tool_error("max_raise_db must be 0..12", cmd=cmd)
        if not 0 <= tolerance_db <= 6:
            return tool_error("tolerance_db must be 0..6", cmd=cmd)
        key = None
        if genre is not None:
            key = mix.genre_key(genre)
            if key is None:
                return tool_error("unknown genre; use one of " + ", ".join(mix.GENRES), cmd=cmd)
        bad = [r for r in (roles or {}).values() if r not in mix.ROLES]
        if bad:
            return tool_error(f"unknown role(s) {bad}; use one of {', '.join(mix.ROLES)}",
                              cmd=cmd)
        try:
            found = engine.devices()
            chosen = [d for d in engine.select(found, tracks, master=False)
                      if d.get("role") != "master"]
        except engine.EngineError as exc:
            return _engine_failure(exc, cmd)
        master = next((d for d in found if d.get("role") == "master"), None)
        if len(chosen) < 2:
            return tool_error("balancing needs LiveBridge Engine devices at the end of at "
                              "least 2 tracks. " + _HOW_TO_ADD, type="not_found", cmd=cmd)
        if playing() is False:
            return tool_error("the transport is stopped: pass section_bars=[first, last] of the "
                              "fullest section (the tool plays it), or loop it and start "
                              "playback (live_transport_play)", type="invalid_state", cmd=cmd)
        notes: list[str] = []
        names = [str(d.get("track") or "") for d in chosen]
        state = faders(names)
        active = []
        for device, name in zip(chosen, names):
            info = state.get(name)
            if info is None:
                notes.append(f"{name}: fader not readable — left out.")
            elif info["mute"]:
                notes.append(f"{name} is muted — left out.")
            elif info["volume"] is None:
                notes.append(f"{name} is at -inf dB — left out.")
            else:
                active.append((device, name))
        if len(active) < 2:
            return tool_error("fewer than 2 unmuted tracks to balance. " + " ".join(notes),
                              type="invalid_state", cmd=cmd)
        parts: list[dict[str, Any]] = []
        plan: dict[str, Any] = {"anchor": None, "moves": [], "unchanged": []}
        done = 0
        readings: dict[str, dict[str, Any]] = {}
        raised: dict[str, float] = {}
        moved: dict[str, float] = {}
        # what earlier runs did to these faders (same set, fader still where they left it)
        set_key = current_set()
        memory = library.read_json(library.library_dir() / _BALANCE_FILE, {})
        memory = memory if isinstance(memory, dict) else {}
        earlier: dict[str, dict[str, Any]] = {}
        for name, entry in ((memory.get(set_key) or {}) if isinstance(
                memory.get(set_key), dict) else {}).items():
            now = (state.get(name) or {}).get("volume")
            if isinstance(entry, dict) and now is not None and \
                    abs(float(entry.get("fader_db", 999.0)) - now) <= 0.15:
                earlier[name] = entry
        raised.update({n: float(e.get("raised_db") or 0.0) for n, e in earlier.items()})
        moved.update({n: float(e.get("moved_db") or 0.0) for n, e in earlier.items()})
        counted: dict[str, bool] = {}
        # every move of the run, per part: the report shows the whole run, not the last pass
        total: dict[str, dict[str, Any]] = {}
        # parts left alone in the first pass stay alone: a later pass hearing a one-shot a
        # bit longer must not move a fader the report says was left alone
        left_out: set[str] = set()
        why_left: dict[str, str] = {}
        for _round in range(passes):
            jobs.note(f"balancing the faders: pass {_round + 1} of {passes}")
            done += 1
            results = listen_all([d for d, _n in active], seconds)
            state = faders([n for _d, n in active])
            parts = []
            for (device, name), result in zip(active, results):
                if name in left_out:
                    continue
                # what the first pass leaves alone stays alone (and is said once); a later
                # pass that cannot use a part it moved before just does not move it again
                first = _round == 0
                if isinstance(result, engine.EngineError):
                    if first:
                        left_out.add(name)
                        why_left[name] = "not measured"
                        notes.append(f"{name}: not measured ({result}).")
                    continue
                readings[name] = result
                lufs = (result.get("loudness") or {}).get("lufs")
                if result.get("silent") or lufs is None:
                    if first:
                        left_out.add(name)
                        why_left[name] = "silent in this window"
                        notes.append(f"{name} is silent in this window — fader left alone "
                                     "(live_sound_check says why).")
                    continue
                if first and name in (one_shots or ()):
                    left_out.add(name)
                    counted[name] = False
                    why_left[name] = "one-shot: silent in part of the section"
                    notes.append(f"{name} plays only now and then in this section (silent in "
                                 "another window): fader left alone — set it by ear.")
                    continue
                share = (result.get("loudness") or {}).get("active_share")
                limit = min_active
                if first and name in earlier and "used" in earlier[name]:
                    # the last run's answer holds unless the reading is clearly different
                    limit += -_ACTIVE_MARGIN if earlier[name]["used"] else _ACTIVE_MARGIN
                if first:
                    counted[name] = not (share is not None and share < limit)
                if share is not None and share < limit:
                    if first:
                        left_out.add(name)
                        why_left[name] = f"one-shot: plays {round(share * 100)} % of the window"
                        notes.append(f"{name} plays only {round(share * 100)} % of the window "
                                     "(a one-shot or fill reads too quiet on average): fader "
                                     "left alone — set it by ear, or loop a section where it "
                                     "plays throughout.")
                    continue
                guess = mix.guess_role(name, result)
                role = (roles or {}).get(name) or guess["role"]
                parts.append({"name": name, "role": role,
                              "role_from": "you" if (roles or {}).get(name) else guess["from"],
                              "lufs": float(lufs),
                              "fader_db": state.get(name, {}).get("volume") or 0.0})
            plan = mix.balance_plan(
                [dict(p) for p in parts], key, anchor, min_move=tolerance_db,
                raise_left={p["name"]: max_raise_db - raised.get(p["name"], 0.0)
                            for p in parts},
                move_left={p["name"]: max_move_db - moved.get(p["name"], 0.0) for p in parts},
                protect=protect or ())
            for move in plan["moves"]:
                moved[move["name"]] = moved.get(move["name"], 0.0) + abs(move["move_db"])
                if move["move_db"] > 0:
                    raised[move["name"]] = raised.get(move["name"], 0.0) + move["move_db"]
                run = total.get(move["name"])
                if run is None:
                    total[move["name"]] = dict(move, passes=1)
                else:
                    flags = {k: True for k in ("capped", "budget_capped", "raise_capped",
                                               "at_max") if move.get(k) or run.get(k)}
                    first = run["fader_db"]
                    run.update(move, **flags)
                    run["fader_db"] = first
                    run["move_db"] = round(run["new_fader_db"] - first, 1)
                    run["passes"] += 1
            if not plan["moves"] or not apply:
                break
            answer = bridge_call(bridge, "mixer.set_many", {"settings": [
                {"track": m["name"], "volume": f"{m['new_fader_db']:.1f} dB"}
                for m in plan["moves"]]})
            if _is_error(answer):
                return answer
            if _round + 1 < passes:
                time.sleep(_SETTLE_S)
        result_parts = [{"name": p["name"], "role": p["role"], "role_from": p["role_from"],
                         "lufs": round(p["lufs"], 1), "fader_db": round(p["fader_db"], 1),
                         "post_lufs": round(p["lufs"] + p["fader_db"], 1)} for p in parts]
        unknown = [p["name"] for p in parts if not p["role"]]
        if unknown:
            notes.append(f"Role unknown for {', '.join(unknown)} (balanced as a mid-level part): "
                         "pass roles={...} to place them.")
        measured = {p["name"] for p in parts}
        unmeasured = [n for _d, n in active
                      if n not in measured and n not in left_out and n not in total]
        if unmeasured:
            notes.append("Not measured in this window, faders left where they were: "
                         + ", ".join(unmeasured) + ". The balance keeps the mix's overall "
                         "level, so they keep their place — check them by ear where they "
                         "play (or balance a section where they do).")
        protected = set(plan.get("protected") or ())
        for item in plan.get("held") or []:
            why = "Live's +6 dB fader maximum" if item.get("at_max") else \
                "the raise limit (max_raise_db)" if item.get("raise_capped") else \
                "the move limit (max_move_db, counted over runs)"
            fix = (f"lower it by hand, or live_mix_balance(max_move_db=...) higher than "
                   f"{max_move_db:g}") if item["off_db"] > 0 else \
                ("raise it at the source (the instrument, sample gain or the Channel's Trim), "
                 f"or live_mix_balance(max_raise_db=...) higher than {max_raise_db:g}")
            notes.append(f"{item['name']} is held {abs(item['off_db']):g} dB "
                         f"{'above' if item['off_db'] > 0 else 'below'} its role's level by "
                         f"{why}: {fix}.")
        plan = dict(plan, moves=list(total.values()),
                    unchanged=[p["name"] for p in parts
                               if p["name"] not in total and p["name"] not in protected])
        answer: dict[str, Any] = {"applied": bool(apply), "passes": done,
                                  "parts": result_parts, "plan": plan, "notes": notes}
        if why_left:
            answer["left_out"] = why_left
        if key:
            answer["genre"] = key
            answer["genre_label"] = mix.GENRES[key]["label"]
        if master is not None:
            try:
                reading = engine.listen(master, min(seconds, 6.0))
            except engine.EngineError as exc:
                notes.append(f"master Engine not read ({exc}).")
                reading = None
            if reading is not None and not reading.get("silent"):
                answer["master"] = _reading(reading)
                peak = (reading.get("loudness") or {}).get("peak_db")
                excess = (peak + headroom_db) if peak is not None else 0.0
                chain_ahead = master_chain_before_engine()
                if chain_ahead and excess > 0.5:
                    notes.append(f"Master peaks at {peak} dB, but that is after "
                                 f"{', '.join(chain_ahead)} on the master (a limiter's output, "
                                 "not the mix's headroom): faders not trimmed — "
                                 "live_master_auto sets the headroom in front of its chain.")
                elif excess > 0.5:
                    answer["headroom_trim_db"] = round(-excess, 1)
                    if apply:
                        # every track, measured or not: the balance between them stays
                        listing = bridge_call(bridge, "mixer.get", {"detail": "minimal"})
                        settings = []
                        for entry in (listing.get("tracks") or []) \
                                if isinstance(listing, dict) else []:
                            volume = _num(entry.get("volume")) \
                                if isinstance(entry, dict) else None
                            if volume is not None and (entry.get("path") or entry.get("name")):
                                settings.append({"track": entry.get("path") or entry["name"],
                                                 "volume": f"{max(volume - excess, -70.0):.1f}"
                                                           " dB"})
                        trimmed = bridge_call(bridge, "mixer.set_many", {"settings": settings})
                        if _is_error(trimmed):
                            notes.append(f"headroom trim failed: {trimmed.get('error')}")
                        else:
                            notes.append(f"Master peaked at {peak} dB: every track fader "
                                         f"went down {excess:.1f} dB for {headroom_db:g} dB "
                                         "headroom (the balance is kept).")
                    else:
                        notes.append(f"Master peaks at {peak} dB: lower every fader "
                                     f"{excess:.1f} dB for {headroom_db:g} dB headroom.")
                tracks_post = [{"name": p["name"], "role": p["role"],
                                "bands_db": (readings[p["name"]].get("spectrum") or {})
                                .get("bands_db") or {},
                                "post_lufs": p["lufs"] + p["fader_db"]} for p in parts]
                bands = (reading.get("spectrum") or {}).get("bands_db") or {}
                if bands and tracks_post:
                    answer["bands"] = mix.band_decisions(bands, tracks_post, key)
        else:
            notes.append("No Engine on the master: headroom and the track-or-master band check "
                         "were skipped.")
        if apply and counted:
            now = faders(list(counted))
            kept = dict(memory.get(set_key) or {}) if isinstance(memory.get(set_key), dict) \
                else {}
            for name, used in counted.items():
                volume = (now.get(name) or {}).get("volume")
                if volume is not None:
                    kept[name] = {"fader_db": round(volume, 2), "used": used,
                                  "raised_db": round(raised.get(name, 0.0), 2),
                                  "moved_db": round(moved.get(name, 0.0), 2)}
            memory[set_key] = kept
            try:
                library.write_json(library.library_dir() / _BALANCE_FILE, memory)
            except OSError as exc:
                notes.append(f"could not remember this balance ({exc}): a rerun may move "
                             "capped parts again.")
        return answer

    # ------------------------------------------------------------------ master
    @mcp.tool()
    @_background("live_master_auto")
    @looped
    def live_master_auto(
        destination: str = "streaming",
        lufs: float | None = None,
        ceiling: float | None = None,
        genre: str | None = None,
        seconds: float = 6.0,
        apply: bool = True,
        loud_bar: float | None = None,
        quiet_bar: float | None = None,
        max_gain_db: float = 18.0,
        chain: str = "native",
        place_devices: bool = True,
        eq_bands: dict[str, float] | None = None,
    ) -> Any:
        """Master the song to a loudness target by measurement: uses (or builds) EQ Eight ->
        Glue Compressor -> Limiter (or the LiveBridge Master device, `chain="livebridge"`) on
        the master, ahead of the LiveBridge Engine, sets the
        limiter ceiling, then raises the limiter gain step by step, measuring integrated LUFS,
        peak and crest factor on the master each time, until the target is reached. Stops early
        when the punch collapses (crest factor below the destination's minimum) and, with
        `loud_bar`/`quiet_bar`, when the drop and the breakdown are being squashed together.
        Never stacks a second limiter on a chain that already has one from a plug-in.

        Args:
            destination: "streaming"/"spotify"/"youtube" (-14 LUFS, -1 dB ceiling), "apple"
                (-16), "soundcloud" (-10, -0.3), "club" (-8, -0.3), "demo" (-12).
            lufs: explicit integrated target in LUFS (overrides the destination's).
            ceiling: limiter ceiling in dB (default from the destination).
            genre: with destination="genre": master to that genre's typical loudness
                (techno -8, trance -8.5, pop -12, lo_fi -14, ambient -18 ...).
            seconds: listening window per step (3..20) — loop the loudest section.
            apply: false = measure and report the plan only.
            loud_bar, quiet_bar: bars of the loudest and quietest sections (1-based): the
                tool plays each before and after, and backs the limiter off when their
                difference shrinks by more than 2 LU.
            max_gain_db: the most Limit Gain / Live Limiter gain it may use (0..24). With
                `chain="livebridge"` the device's Comp Makeup (in front of the limiter) can add
                up to 12 dB more on top, so the drive reaches max_gain_db + 12 (`makeup_db`).
            chain: "native" (EQ Eight, Glue Compressor, Limiter) or "livebridge": one
                LiveBridge Master device (Ableton's Channel EQ, Compressor, Limiter and Utility
                DSP in a single Max device, Max 9 / Live 12.2+: EQ Low Cut, Comp Ratio/Attack,
                Limit Gain/Ceiling/Mode, Width, Bass Mono... all Live parameters).
            place_devices: put a LiveBridge Engine on the master first when there is none (default).
            eq_bands: master bands to correct, {band: dB too much (+) or too little (-)} —
                live_mix_balance's `bands` with fix "master" (live_mix_auto passes them).
                LiveBridge Master's EQ takes half of each (at most 4 dB; sub/bass -> EQ Low,
                low_mid..high_mid -> EQ Mid at the band, high/air -> EQ High). The bands are
                measured after that EQ, so a cut already as deep as the reading asks for
                stays (a rerun does not deepen it); with the native chain they become advice
                in `manual`.

        Returns:
            `{destination, target_lufs, ceiling_db, applied, chain, inserted, reset?,
            before:{lufs, peak_db, crest_db}, after:{...}, limiter_gain_db, makeup_db?,
            limiting_db, peak_reduction_db, eq?, settings, steps:[{gain_db (the drive),
            lufs, peak_db, crest_db} | {eq} | {faders_db}], device_check?, contrast?:{before_lu,
            after_lu}, stopped?, notes, manual}`. crest_db is peak minus the unweighted RMS,
            not minus the K-weighted LUFS (so it reads ~3-4 dB above peak - LUFS on a mix);
            limiting_db is the drive minus the loudness gained, peak_reduction_db the drive
            minus the peak gained — the crest drop is about their difference, not exactly.

        Gotchas: needs an Engine on the master (it measures after the limiter) and the song
        playing (or loud_bar to start it). One master can't serve both worlds: a club master
        gets turned down on Spotify with its transients crushed — run it once per destination.
        Final judgement needs ears; the tool reports numbers, not taste.
        """
        cmd = "mix.master_auto"
        dest = destination.strip().lower()
        if dest == "genre":
            key = mix.genre_key(genre)
            if key is None:
                return tool_error("destination='genre' needs genre= (one of "
                                  + ", ".join(mix.GENRES) + ")", cmd=cmd)
            target, ceil, min_crest = mix.GENRES[key]["lufs"], -0.3, 7.0
            if target <= -12:
                ceil, min_crest = -1.0, 9.0
        elif dest in mix.DESTINATIONS:
            target, ceil, min_crest = mix.DESTINATIONS[dest]
        else:
            return tool_error("destination must be one of " + ", ".join(mix.DESTINATIONS)
                              + " or 'genre'", cmd=cmd)
        if lufs is not None:
            if not -30 <= lufs <= -4:
                return tool_error("lufs must be -30..-4", cmd=cmd)
            target = float(lufs)
        if ceiling is not None:
            if not -6 <= ceiling <= 0:
                return tool_error("ceiling must be -6..0 dB", cmd=cmd)
            ceil = float(ceiling)
        if not 3 <= seconds <= 20:
            return tool_error("seconds must be 3..20", cmd=cmd)
        if not 0 <= max_gain_db <= 24:
            return tool_error("max_gain_db must be 0..24", cmd=cmd)
        if chain not in ("native", "livebridge"):
            return tool_error("chain must be 'native' or 'livebridge'", cmd=cmd)
        chain_kind = chain
        if (loud_bar is None) != (quiet_bar is None):
            return tool_error("pass both loud_bar and quiet_bar (or neither)", cmd=cmd)
        for bar in (loud_bar, quiet_bar):
            if bar is not None and bar < 1:
                return tool_error("bars are 1-based (>= 1)", cmd=cmd)
        if place_devices and apply:
            placed = place_meters(master=True, include_tracks=False)
            if placed.get("error"):
                return placed["error"]
        try:
            device = engine.choose(engine.devices(), "master")
        except engine.EngineError as exc:
            return _engine_failure(exc, cmd)
        if device.get("role") != "master":
            return tool_error("no LiveBridge Engine on the master track. " + _HOW_TO_ADD,
                              type="not_found", cmd=cmd)
        state = bridge_call(bridge, "transport.get")
        if _is_error(state):
            return state
        was_playing = bool(state.get("is_playing"))
        beats_per_bar = 4.0
        match = re.match(r"^(\d+)/(\d+)$", str(state.get("signature", "")))
        if match and int(match.group(2)):
            beats_per_bar = int(match.group(1)) * 4.0 / int(match.group(2))
        if not was_playing and loud_bar is None:
            return tool_error("the transport is stopped: loop the loudest section and start "
                              "playback (or pass loud_bar/quiet_bar)", type="invalid_state",
                              cmd=cmd)
        notes: list[str] = []
        manual: list[str] = []

        def measure(bar: float | None = None) -> dict[str, Any]:
            if bar is not None:
                bridge_call(bridge, "transport.play",
                            {"position": (bar - 1.0) * beats_per_bar})
                time.sleep(_SETTLE_S)
            else:
                rewind()                    # every step hears the same bars as 'before'
            return engine.listen(device, seconds, kind="mix")

        sections: dict[str, dict[str, Any]] = {}

        def contrast() -> float | None:
            if loud_bar is None or quiet_bar is None:
                return None
            loud = _reading(measure(loud_bar))["lufs"]
            quiet = _reading(measure(quiet_bar))["lufs"]
            sections["before" if "before" not in sections else "after"] = {
                "loud_lufs": loud, "quiet_lufs": quiet}
            if loud is None or quiet is None:
                return None
            return round(loud - quiet, 1)

        def chain() -> list[dict[str, Any]]:
            listing = bridge_call(bridge, "devices.list", {"track": "master"})
            return list(listing.get("devices") or []) if isinstance(listing, dict) and \
                not _is_error(listing) else []

        def find(devices: list[dict[str, Any]], class_name: str) -> dict[str, Any] | None:
            if class_name == _MASTER_DEVICE:
                return next((d for d in devices
                             if _MASTER_DEVICE.lower() in str(d.get("name", "")).lower()), None)
            return next((d for d in devices if d.get("class_name") == class_name), None)

        def load_master_device(at: int) -> Any:
            """LiveBridge Master from the User Library onto the master, moved to ``at``."""
            loaded = bridge_call(bridge, "browser.load", {
                "query": _MASTER_DEVICE, "category": "audio_effect", "track": "master"},
                timeout=60.0)
            if _is_error(loaded):
                if loaded.get("type") == "not_found":
                    loaded["error"] += (" — is the LiveBridge Master device installed (the "
                                        "installer puts it in the User Library) and Max for "
                                        "Live (Max 9) available?")
                return loaded
            if at >= 0:
                placed = find(chain(), _MASTER_DEVICE)
                if placed is not None and placed.get("index") != at:
                    moved = bridge_call(bridge, "devices.move", {
                        "track": "master", "device": placed["index"], "position": at})
                    if _is_error(moved):
                        return moved
            return loaded

        devices = chain()
        limiter = find(devices, "Limiter" if chain_kind == "native" else _MASTER_DEVICE)
        foreign = [d for d in devices if d.get("type") == "audio_effect" and
                   d.get("class_name") in ("PluginDevice", "AuPluginDevice") and
                   _PLUGIN_LIMITER_RE.search(str(d.get("name")))]
        if chain_kind == "livebridge" and limiter is None and find(devices, "Limiter"):
            return tool_error("the master already has Live's Limiter: use chain='native' (or "
                              "remove it) — two limiters would fight", type="invalid_state",
                              cmd=cmd)
        gain_name, ceiling_name = ("Gain", "Ceiling") if chain_kind == "native" else (
            "Limit Gain", "Limit Ceiling")
        # the gain stages this tool drives: they start at 0 dB so "before" is the mix itself
        # (a rerun read the last run's +18 dB as the mix and could not tell it did nothing)
        drive_names = (gain_name,) if chain_kind == "native" else (gain_name, "Comp Makeup")

        def db(value: float) -> Any:
            """A dB setting for the chain: LiveBridge Master's dB dials are linear (Live's raw
            value is the dB), so they take the number — as text Live matched it against a
            display that rounds to whole dB above 10 (11.1 landed as 11.5, "12 dB")."""
            return round(value, 2) if chain_kind == "livebridge" else f"{value:.1f} dB"

        def read_param(index: int, name: str) -> tuple[float | None, str | None]:
            got = bridge_call(bridge, "devices.get_parameter",
                              {"track": "master", "device": index, "parameter": name})
            if isinstance(got, dict) and not _is_error(got):
                display = str(got.get("display", ""))
                if chain_kind == "livebridge" and name not in _HZ_PARAMS and \
                        _num(got.get("value")) is not None:
                    return _num(got.get("value")), display
                parsed = re.match(r"^\s*(-?\d+(?:\.\d+)?)", display)
                return (float(parsed.group(1)) if parsed else _num(got.get("value"))), display
            return None, None

        reset: dict[str, float] = {}
        if limiter is not None and not limiter.get("is_active", True):
            return tool_error(f"{limiter.get('name')} on the master is switched off (Device "
                              "On): switch it on (live_device_set active=true) and run again",
                              type="invalid_state", cmd=cmd)
        if apply and limiter is not None:
            for name in drive_names:
                value, _display = read_param(limiter["index"], name)
                if value is not None and abs(value) >= 0.1:
                    reset[name] = value
            if reset:
                answer = bridge_call(bridge, "devices.set_parameters", {
                    "track": "master", "device": limiter["index"],
                    "values": {name: db(0.0) for name in reset}})
                if _is_error(answer):
                    return answer
                time.sleep(_SETTLE_S)
        try:
            before_contrast = contrast()
            before = measure(loud_bar)
        except engine.EngineError as exc:
            return _engine_failure(exc, cmd)
        if before.get("silent"):
            return tool_error("the master is silent in this window: play the loudest section",
                              type="invalid_state", cmd=cmd)
        result: dict[str, Any] = {"destination": dest, "target_lufs": target,
                                  "ceiling_db": ceil, "applied": False,
                                  "chain": [d.get("name") for d in devices], "inserted": [],
                                  "before": _reading(before), "notes": notes,
                                  "manual": manual}
        if reset:
            result["reset"] = {k: round(v, 1) for k, v in reset.items()}
            notes.append("Before measured with " + ", ".join(
                f"{k} {v:+.1f} -> 0 dB" for k, v in reset.items()) + " (the last run's "
                "settings): 'before' is the mix itself, the run sets them again.")
        if foreign and limiter is None:
            notes.append(f"The master already has a plug-in limiter ({foreign[0]['name']}): "
                         "no second limiter added. Raise its gain until the master reads "
                         f"{target} LUFS (live_engine_listen) with its ceiling at {ceil} dB.")
            result["needed_gain_db"] = round(target - (before["loudness"]["lufs"] or target), 1)
            return result
        lufs_now = (before.get("loudness") or {}).get("lufs")
        if not apply:
            result["needed_gain_db"] = round(target - lufs_now, 1) if lufs_now is not None \
                else None
            missing = [n for n, c in _chain_devices(chain_kind) if not find(devices, c)]
            if missing:
                notes.append("Would insert " + ", ".join(missing) + " before the Engine.")
            if eq_bands:
                notes.append("Would set the master EQ for: " + ", ".join(
                    f"{b} {d:+g} dB" for b, d in eq_bands.items()) + ".")
            return result

        def master_eq(index: int | None, bands: dict[str, float]) -> dict[str, str]:
            """Half of each shared band excess (at most 4 dB, +-6 dB with what is there) on
            LiveBridge Master's EQ; Live's chain gets the advice instead."""
            wanted: dict[str, tuple[float, float | None]] = {}
            for band, delta in bands.items():
                target_param = _MASTER_EQ_BANDS.get(str(band))
                if target_param is None or not isinstance(delta, (int, float)) or \
                        abs(delta) < 1.0:
                    continue
                param, freq = target_param
                if param not in wanted or abs(delta) > abs(wanted[param][0]):
                    wanted[param] = (float(delta), freq)
            if not wanted:
                return {}
            if index is None:
                manual.append("Master EQ (EQ Eight): " + "; ".join(
                    f"{'cut' if d > 0 else 'boost'} {p.replace('EQ ', '').lower()}"
                    f"{f' around {f:g} Hz' if f else ''} about {min(abs(d) / 2, 4):.1f} dB"
                    for p, (d, f) in wanted.items()) + " (shared by many tracks).")
                return {}
            values: dict[str, str] = {}
            was: dict[str, float] = {}
            for param, (delta, freq) in wanted.items():
                current = read_param(index, param)[0] or 0.0
                setting = -math.copysign(min(abs(delta) / 2.0, 4.0), delta)
                # the bands are measured after this EQ: a cut already in place that is as
                # deep as the excess asks for stays (adding to it on every run walked an
                # unchanged mix's mid to the -6 dB cap); the opposite reading backs it off
                if current and math.copysign(1.0, current) == math.copysign(1.0, setting):
                    if abs(setting) <= abs(current) + 0.05:
                        if abs(current) >= 5.9:
                            notes.append(
                                f"{param} already at {current:+.1f} dB and the master still "
                                f"reads {delta:+.1f} dB in that band: more master EQ would "
                                "thin the whole mix — the rest is in the parts (the balance's "
                                "`bands` names the tracks that carry it), fix it there.")
                        else:
                            notes.append(
                                f"{param} kept at {current:+.1f} dB: the band still reads "
                                f"{delta:+.1f} dB with it in place, and a run deepens a cut "
                                f"only when half the reading ({abs(setting):.1f} dB) is more "
                                "than the cut there — a bell cut moves a wide band little, so "
                                "adding up runs would walk it to the -6 dB limit. The "
                                "balance's band advice does not know about this cut.")
                        continue
                elif current:
                    setting = current + setting
                was[param] = current
                values[param] = db(max(-6.0, min(6.0, setting)))
                if freq is not None:
                    values["EQ Mid Freq"] = f"{freq:g} Hz"
            if not values:
                return {}
            answer = bridge_call(bridge, "devices.set_parameters",
                                 {"track": "master", "device": index, "values": values})
            if _is_error(answer):
                notes.append(f"master EQ not set: {answer.get('error')}")
                return {}
            done = {}
            for param in values:
                shown = read_param(index, param)[1]
                done[param] = (f"{_fmt(was[param])} -> {shown}" if param in was else
                               str(shown))
            return done

        def device_check(index: int, step: float, heard: float,
                         bar: float | None) -> dict[str, Any]:
            """The limiter's drive did nothing: is the device processing at all? Turns its
            last stage (Output) down 6 dB for one listen and back."""
            check: dict[str, Any] = {"drive_step_db": round(step, 1),
                                     "heard_lu": round(heard, 1)}
            if chain_kind == "native":
                check["verdict"] = (f"{step:+.1f} dB of Limiter gain changed the master by "
                                    f"{heard:+.1f} LU only: the Limiter does not process (is "
                                    "it on, and is the Engine after it?). Gain set back.")
                return check
            out, _shown = read_param(index, "Output")
            out = out or 0.0
            first = _reading(measure(bar))["lufs"]
            set_limiter({"Output": db(out - 6.0)})
            time.sleep(_SETTLE_S)
            probed = _reading(measure(bar))["lufs"]
            set_limiter({"Output": db(out)})
            time.sleep(_SETTLE_S)
            if first is None or probed is None:
                check["verdict"] = "the device check could not measure the master"
                return check
            check["output_probe_lu"] = round(probed - first, 1)
            if abs(probed - first) < 2.0:
                check["verdict"] = (
                    f"LiveBridge Master passes the sound untouched: {step:+.1f} dB of Limit "
                    f"Gain and -6 dB of Output changed the master by {heard:+.1f} / "
                    f"{probed - first:+.1f} LU. Its DSP is not running — open the device "
                    "(Max editor) and look for red/broken boxes or errors in the Max Console "
                    "(the abl.device objects need Max 9 / Live 12.2+); reinstall it with the "
                    "installer. The drive is back where this run started.")
            else:
                check["verdict"] = (
                    f"Output works ({probed - first:+.1f} LU for -6 dB) but {step:+.1f} dB of "
                    f"Limit Gain changed the master by {heard:+.1f} LU only: the limiter "
                    "stage does not take its gain — reinstall LiveBridge Master (installer) "
                    "and replace the one on the master. The drive is back where this run "
                    "started.")
            return check

        # ------------------------------------------------------------- build the chain
        engine_at = next((i for i, d in enumerate(devices)
                          if "livebridge engine" in str(d.get("name", "")).lower()), None)
        if engine_at is not None and engine_at != len(devices) - 1:
            notes.append("The Engine is not last on the master: it measures before the "
                         "devices after it.")
        for name, class_name in _chain_devices(chain_kind):
            if find(devices, class_name):
                continue
            if class_name not in ("Limiter", _MASTER_DEVICE) and limiter is not None:
                continue                      # the user's own chain: keep it as it is
            at = engine_at if engine_at is not None else -1
            if class_name == _MASTER_DEVICE:
                added = load_master_device(at)
            else:
                added = bridge_call(bridge, "devices.insert", {"name": name, "track": "master",
                                                               "index": at})
            if _is_error(added):
                return added
            result["inserted"].append(name)
            if engine_at is not None:
                engine_at += 1
            devices = chain()
            if class_name == "Eq8":
                low_cut = bridge_call(bridge, "devices.set_parameters", {
                    "track": "master", "device": find(devices, "Eq8")["index"],
                    "values": {"1 Filter On A": "On", "1 Filter Type A": "Low Cut 48",
                               "1 Frequency A": "25 Hz"}})
                if _is_error(low_cut):
                    manual.append("EQ Eight: band 1 Low Cut 48 at 25 Hz (not set: "
                                  f"{low_cut.get('error')}).")
            elif class_name == "GlueCompressor":
                glue = bridge_call(bridge, "devices.set_parameters", {
                    "track": "master", "device": find(devices, "GlueCompressor")["index"],
                    "values": {"Ratio": "2", "Attack": "10", "Release": "Auto"}})
                if _is_error(glue):
                    manual.append("Glue Compressor: ratio 2:1, attack 10 ms, release Auto "
                                  f"(not set: {glue.get('error')}).")
                manual.append("Glue Compressor: lower Threshold until it shows 1-3 dB of gain "
                              "reduction on the loudest section (the meter is not readable "
                              "through Live's API).")
            elif class_name == _MASTER_DEVICE:
                ready = bridge_call(bridge, "devices.set_parameters", {
                    "track": "master", "device": find(devices, _MASTER_DEVICE)["index"],
                    "values": {"EQ Low Cut": 1.0, "Comp Ratio": 2.0,
                               "Limit Mode": "True peak"}})
                if _is_error(ready):
                    manual.append("LiveBridge Master: EQ Low Cut on, Comp Ratio 2, Limit Mode "
                                  f"True peak (not set: {ready.get('error')}).")
                manual.append("LiveBridge Master: lower Comp Threshold until the loudest "
                              "section shows 1-3 dB of gain reduction (open the device; the "
                              "meter is not readable through Live's API).")
        result["chain"] = [d.get("name") for d in devices]
        limiter = find(devices, "Limiter" if chain_kind == "native" else _MASTER_DEVICE)
        if limiter is None:
            return tool_error("no limiter on the master after inserting it", type="internal",
                              cmd=cmd)
        index = limiter["index"]
        steps: list[dict[str, Any]] = []

        def set_limiter(values: dict[str, str]) -> Any:
            return bridge_call(bridge, "devices.set_parameters",
                               {"track": "master", "device": index, "values": values})

        # the drive into the limiter: its own gain first, then (LiveBridge Master) the
        # compressor's makeup in front of it — no fader moves
        low = 0.0 if chain_kind == "native" else -24.0
        makeup = chain_kind == "livebridge" and read_param(index, "Comp Makeup")[0] is not None
        high = max_gain_db + (_MAKEUP_MAX_DB if makeup else 0.0)
        drive_names = (gain_name, "Comp Makeup") if makeup else (gain_name,)

        def split(drive: float) -> dict[str, float]:
            if not makeup:
                return {gain_name: drive}
            gain_part = min(drive, max_gain_db)
            return {gain_name: gain_part, "Comp Makeup": max(0.0, drive - gain_part)}

        def set_drive(drive: float) -> Any:
            return set_limiter({k: db(v) for k, v in split(drive).items()})

        answer = set_limiter({ceiling_name: db(ceil)})
        if _is_error(answer):
            return answer
        result["applied"] = True
        drive = sum(v for v in (read_param(index, n)[0] for n in drive_names) if v is not None)
        stopped = None
        current = before if loud_bar is None else measure(loud_bar)
        start_crest = _reading(current)["crest_db"]
        now = _reading(current)
        # a mix already louder than the target: Live's Limiter has no negative gain, so the
        # faders come down together (listed in steps); LiveBridge Master turns its gain down
        if chain_kind == "native" and drive < 0.1 and now["lufs"] is not None and \
                now["lufs"] > target + 0.5:
            trim = now["lufs"] - target + 2.0
            listing = bridge_call(bridge, "mixer.get", {"detail": "minimal"})
            settings = []
            if isinstance(listing, dict):
                for entry in listing.get("tracks") or []:
                    volume = _num(entry.get("volume")) if isinstance(entry, dict) else None
                    if volume is not None and (entry.get("path") or entry.get("name")):
                        settings.append({"track": entry.get("path") or entry["name"],
                                         "volume": f"{max(volume - trim, -70.0):.1f} dB"})
            if settings:
                trimmed = bridge_call(bridge, "mixer.set_many", {"settings": settings})
                if _is_error(trimmed):
                    return trimmed
                result["headroom_trim_db"] = round(-trim, 1)
                steps.append({"faders_db": round(-trim, 1), "tracks": len(settings)})
                notes.append(f"The mix read {now['lufs']} LUFS, louder than the target with "
                             f"Live's Limiter idle: every track fader went down {trim:.1f} dB "
                             "(balance kept).")
                time.sleep(_SETTLE_S)
                current = measure(None if loud_bar is None else loud_bar)
        # the master band fixes of live_mix_balance (shared by many tracks): on the device's EQ
        if eq_bands:
            eq = master_eq(index if chain_kind == "livebridge" else None, eq_bands)
            if eq:
                result["eq"] = eq
                steps.append({"eq": eq})
                time.sleep(_SETTLE_S)
                current = measure(None if loud_bar is None else loud_bar)
        checked = False
        for _step in range(_MAX_STEPS):
            now = _reading(current)
            if now["lufs"] is None:
                stopped = "the master went silent while measuring"
                break
            delta = target - now["lufs"]
            if abs(delta) <= 0.5:
                break
            new_drive = max(low, min(high, drive + max(-6.0, min(6.0, delta))))
            if abs(new_drive - drive) < 0.1:
                if delta > 0:
                    result["missing_gain_db"] = round(delta, 1)
                    stopped = (f"the drive into the limiter is at its limit ({high:g} dB) and "
                               f"still {delta:.1f} dB under the target — that much limiting "
                               "would flatten the mix; raise the parts at their source or "
                               "choose a quieter destination" if chain_kind == "livebridge"
                               else f"limiter gain at its limit ({max_gain_db:g} dB) and still "
                               f"{delta:.1f} dB under the target — raise the mix about "
                               f"{delta:.1f} dB before the master (all faders together, or a "
                               "Utility on the master in front of the limiter), or use "
                               "chain='livebridge' (it can drive further)")
                else:
                    stopped = (f"already louder than the target with the limiter's gain at "
                               f"{low:g} dB: turn the mix down")
                break
            jobs.note(f"limiter drive {new_drive:.1f} dB (step {len(steps) + 1}), measuring")
            answer = set_drive(new_drive)
            if _is_error(answer):
                return answer
            old_drive, drive = drive, new_drive
            time.sleep(_SETTLE_S)
            try:
                current = measure(None if loud_bar is None else loud_bar)
            except engine.EngineError as exc:
                return _engine_failure(exc, cmd)
            reading = _reading(current)
            steps.append({"gain_db": round(drive, 1), **{
                k: round(v, 1) for k, v in split(drive).items() if chain_kind == "livebridge"},
                **reading})
            # the first real step must be heard: more drive with the peaks under the ceiling
            # means a louder master — if not, the device passes the sound untouched
            if not checked and abs(drive - old_drive) >= 2.0 and now["lufs"] is not None \
                    and reading["lufs"] is not None and reading["peak_db"] is not None:
                checked = True
                heard = reading["lufs"] - now["lufs"]
                if abs(heard) < 0.25 * abs(drive - old_drive) and \
                        reading["peak_db"] < ceil - 1.0:
                    set_drive(old_drive)
                    drive = old_drive
                    check = device_check(index, new_drive - old_drive, heard, loud_bar)
                    result["device_check"] = check
                    stopped = check["verdict"]
                    current = measure(None if loud_bar is None else loud_bar)
                    break
            # punch collapses: below the destination's minimum AND clearly below what the mix
            # had without limiting (a mix that is dense by nature is not the limiter's fault)
            if reading["crest_db"] is not None and reading["crest_db"] < min_crest and (
                    start_crest is None or reading["crest_db"] < start_crest - 1.5):
                back = max(low, drive - 1.5)
                set_drive(back)
                drive = back
                stopped = (f"crest factor fell to {reading['crest_db']} dB (below "
                           f"{min_crest:g} for {dest}): the limiter was backed off 1.5 dB — "
                           "more loudness would cost the punch. Fix it in the mix (kick/bass "
                           "balance, fewer layers in the loud section).")
                time.sleep(_SETTLE_S)
                current = measure(None if loud_bar is None else loud_bar)
                break
        parts_now = split(drive)
        result["limiter_gain_db"] = round(parts_now[gain_name], 1)
        if parts_now.get("Comp Makeup"):
            result["makeup_db"] = round(parts_now["Comp Makeup"], 1)
        if stopped:
            result["stopped"] = stopped
        result["steps"] = steps
        if before_contrast is not None:
            after_contrast = contrast()
            result["contrast"] = {"before_lu": before_contrast, "after_lu": after_contrast,
                                  **{f"{when}_{k}": v for when, levels in sections.items()
                                     for k, v in levels.items()}}
            if before_contrast <= 0:
                notes.append(f"The 'quiet' bar ({quiet_bar:g}) is as loud as or louder than the "
                             f"'loud' bar ({loud_bar:g}) — before mastering: a mix problem "
                             "(or the bars are swapped). Fix it in the mix first.")
            if after_contrast is not None and after_contrast < before_contrast - 2.0:
                shrink = before_contrast - after_contrast
                back = max(low, drive - shrink)
                set_drive(back)
                drive = back
                result["limiter_gain_db"] = round(split(back)[gain_name], 1)
                notes.append(f"The drop and the quiet section moved {shrink:.1f} LU closer: the "
                             f"limiter was backed off to {back:.1f} dB so the drop still "
                             "lands.")
                current = measure(loud_bar)
        result["after"] = _reading(current)
        # what the device holds now, read back from Live (not what the tool meant to set)
        held = {name: read_param(index, name) for name in (
            drive_names + (ceiling_name,) + (("EQ Low", "EQ Mid", "EQ Mid Freq", "EQ High")
                                             if chain_kind == "livebridge" else ()))}
        result["settings"] = {name: shown for name, (_value, shown) in held.items()}
        # the report's gain is what Live holds, not what the tool asked for
        if held[gain_name][0] is not None:
            result["limiter_gain_db"] = round(held[gain_name][0], 1)
        if "Comp Makeup" in drive_names and held["Comp Makeup"][0] is not None:
            result["makeup_db"] = round(held["Comp Makeup"][0], 1)
            if not result["makeup_db"]:
                del result["makeup_db"]
        before_lufs, after_lufs = result["before"]["lufs"], result["after"]["lufs"]
        before_peak, after_peak = result["before"]["peak_db"], result["after"]["peak_db"]
        if before_lufs is not None and after_lufs is not None and "device_check" not in result:
            # drive minus loudness gained: what the limiter costs in loudness, on average
            result["limiting_db"] = round(max(0.0, drive - (after_lufs - before_lufs)), 1)
            if before_peak is not None and after_peak is not None:
                # what it shaves off the loudest peaks (the crest factor's loss)
                result["peak_reduction_db"] = round(max(0.0, before_peak + drive - after_peak),
                                                    1)
            if drive > _SQUASH_DB:
                notes.append(
                    f"{drive:.1f} dB of drive into the limiter: the loudest peaks lose "
                    f"{result.get('peak_reduction_db', '?')} dB (crest "
                    f"{result['before']['crest_db']} -> {result['after']['crest_db']} dB) — "
                    "the transients are being flattened. Where the gain comes from does not "
                    "change that (faders or drive are the same gain in 32-bit float); a mix "
                    f"with a crest of {result['before']['crest_db']} dB has to lose that much "
                    f"to reach {target:g} LUFS. Listen to the kick and snare; for more punch "
                    "use a quieter destination, or tame the peaks in the mix first (the "
                    "Comp Threshold on the loud parts, shorter transients).")
            if before_lufs < target - 20.0:
                notes.append(
                    f"The mix reaches the master at {before_lufs} LUFS ({target - before_lufs:.0f}"
                    " dB under the target): harmless in itself, but it leaves little room "
                    "on the faders and meters. Raising every fader together keeps the balance "
                    "(live_mixer_set_many with every track) — the master "
                    "then needs that much less drive, with the same sound.")
        if not was_playing:
            bridge_call(bridge, "transport.stop")
        if after_lufs is not None and abs(after_lufs - target) > 1.0 and "stopped" not in \
                result:
            notes.append(f"Ended at {after_lufs} LUFS, {round(after_lufs - target, 1)} from the "
                         "target.")
        manual.append("Export is yours: File > Export Audio/Video, 24-bit WAV (dither only for "
                      "16-bit).")
        return result

    # ------------------------------------------------------------------ channel strips
    @mcp.tool()
    @_background("live_mix_auto")
    @looped
    def live_mix_auto(
        genre: str | None = None,
        tracks: list[str] | None = None,
        roles: dict[str, str] | None = None,
        seconds: float = 6.0,
        apply: bool = True,
        balance: bool = True,
        master: str | None = None,
        master_chain: str = "livebridge",
        place_devices: bool = True,
        low_end: bool = False,
        keep_manual: bool = True,
        protect: list[str] | None = None,
        detail: str = "summary",
        section_bars: list[int] | None = None,
    ) -> Any:
        """Mix (and master) the song by measurement in one call — the devices it needs are put
        in place by the tool itself. On each track with a LiveBridge Channel (a channel
        strip from Ableton's DSP in front of the Engine's meters: high-pass, EQ, compressor,
        saturation, width/bass mono, trim — all Live parameters), it sets the strip to rest,
        listens to the raw part, guesses its role, then sets the strip for that role: high-pass,
        mud / harshness cuts and air from the measured bands, compression to the role's crest
        factor, bass mono on the low end, a little saturation on a dull bass or lead. Then it
        listens again and reports before/after with the reason for every move. With `balance`
        it then sets the faders (`live_mix_balance`), with `master` it masters
        (`live_master_auto`).

        Args:
            genre: for the balance and master steps: techno, house, melodic_house (leads, pads
                and keys close to the kick), trance, dnb, dubstep, hip_hop, trap, pop, rnb, lo_fi,
                ambient, breaks, funk, jazz, metal, country, reggaeton, classical, balanced
                (aliases: tech/deep/afro house, melodic/progressive/organic, ...). Unknown
                genres are refused.
            tracks: only these tracks (default: every track with a Channel).
            roles: correct a guessed role: `{"Audio 3": "hats"}` (kick, snare, hats, perc,
                drums, sub, bass, pad, lead, keys, vocal, guitar, fx).
            seconds: listening window (3..20) — loop the fullest section (the drop).
            apply: false = listen to the strips as they are and report the plan only.
            balance: then balance the faders by role (needs 2+ Engines/Channels).
            master: then master to this destination ("streaming", "club", "apple", "genre"
                ... see live_master_auto; needs an Engine on the master). None = skip.
            master_chain: "livebridge" (default: the LiveBridge Master device before the
                master's Engine) or "native" (EQ Eight -> Glue Compressor -> Limiter).
            place_devices: put the devices in place first (default): a LiveBridge Channel at the end of
                every track that makes sound (it replaces a LiveBridge Engine there) and an
                Engine on the master. false = only the Channels already there.
            keep_manual: keep strip controls set by hand (default): LiveBridge remembers what
                it set on each Channel; a control that differs from that and from rest is the
                user's and stays as it is (`kept` in the report). false = reset every control.
            low_end: opt-in: when there is a kick and a bass/sub, align the bass's phase
                against it (`live_mix_phase`) and put a LiveBridge Unmask on the bass that cuts
                its clashing band while the kick plays (`live_mix_unmask`) — both devices end up
                on the bass track (a Phase device that changed nothing is removed again).
            protect: parts the balance never moves (the lead, the vocal).
            detail: "summary" (default: per part the moves and the measured change, the
                balance moves, the master result) or "full" (every reading and reason).
            section_bars: `[first, last]` bar (1-based) of the section to mix on (the drop, e.g.
                `[113, 128]`): the tool loops it, starts playback there and stops again at
                the end, giving your loop back. Default: the bars playing now, or the
                arrangement loop when it is on and the song is stopped.

        Returns:
            `{applied, placed?:{placed, replaced, skipped}, tracks:[{name, role, role_from,
            before:{lufs, peak_db, crest_db}, after?, values:{parameter: value},
            changes:{parameter: "before -> after"} (only what this run changed: a rerun on
            a mixed song shows few or none), reasons:[...]}], low_end?:{phase, unmask}, balance?, master?, notes}`.

        Gotchas: the song must play the fullest section — or pass `section_bars` and the tool plays it
        (and stops after); a run started on a playing song keeps it playing. A big set takes minutes: past ~40 s the call answers `{job, status:
        "running"}` and the run goes on — `live_mix_job(job=..., wait_seconds=30)` returns the full
        report. Placing ~20 devices takes a while (each one loads and starts). The Channel replaces the Engine on a track (it has
        the meters) — not both. Rules of thumb, not taste: report
        the reasons, let the user listen, and undo or set a single parameter
        (`live_device_set_parameter(device="LiveBridge Channel", ...)`) where they disagree.
        Needs Max 9 (Ableton DSP objects).
        """
        cmd = "mix.auto"
        if detail not in ("summary", "full"):
            return tool_error("detail must be 'summary' or 'full'", cmd=cmd)
        if genre is not None and mix.genre_key(genre) is None:
            return tool_error("unknown genre; use one of " + ", ".join(mix.GENRES), cmd=cmd)
        if not 3 <= seconds <= 20:
            return tool_error("seconds must be 3..20", cmd=cmd)
        if master_chain not in ("native", "livebridge"):
            return tool_error("master_chain must be 'native' or 'livebridge'", cmd=cmd)
        bad = [r for r in (roles or {}).values() if r not in mix.ROLES]
        if bad:
            return tool_error(f"unknown role(s) {bad}; use one of {', '.join(mix.ROLES)}",
                              cmd=cmd)
        placed = None
        if place_devices and apply:
            jobs.note("placing LiveBridge Channels and the master's Engine")
            placed = place_meters(tracks, master=bool(master))
            if placed.get("error"):
                return placed["error"]
        try:
            found = engine.devices()
        except engine.EngineError as exc:
            return _engine_failure(exc, cmd)
        channels = [d for d in found if d.get("strip") and d.get("role") == "track"]
        if tracks:
            wanted = {t.lower() for t in tracks}
            channels = [d for d in channels if str(d.get("track") or "").lower() in wanted]
        if not channels:
            return tool_error(
                "no LiveBridge Channel on " + ("these tracks" if tracks else "any track")
                + ": live_browser_load(query=\"LiveBridge Channel\", category=\"audio_effect\", "
                "track=<name>) on each part (it ends the chain; remove a LiveBridge Engine "
                "there — the Channel has the meters). Needs Max 9.", type="not_found", cmd=cmd)
        if playing() is False:
            return tool_error("the transport is stopped: pass section_bars=[first, last] of the "
                              "fullest section (the tool plays it), or loop it and start "
                              "playback (live_transport_play)", type="invalid_state", cmd=cmd)
        notes: list[str] = []
        names = [str(d.get("track") or "") for d in channels]

        def strip_index(name: str) -> int | None:
            listing = bridge_call(bridge, "devices.list", {"track": name})
            for device in (listing.get("devices") or []) if isinstance(listing, dict) else []:
                if "livebridge channel" in str(device.get("name", "")).lower():
                    return device.get("index")
            return None

        def set_strip(name: str, index: int, values: dict[str, float]) -> Any:
            return bridge_call(bridge, "devices.set_parameters",
                               {"track": name, "device": index, "values": _for_live(values)})

        indexes = {name: strip_index(name) for name in names}
        missing = [n for n, i in indexes.items() if i is None]
        if missing:
            notes.append("Channel not found in the device list of " + ", ".join(missing)
                         + " (renamed?): left out.")
        pairs = [(d, n) for d, n in zip(channels, names) if indexes[n] is not None]
        # a muted track whose mute is not automated never plays: its strip stays as it is
        # (a track muted now but switched on by automation elsewhere does get its strip)
        state = faders([n for _d, n in pairs])
        muted = [n for _d, n in pairs if (state.get(n) or {}).get("mute")]
        if muted:
            automated = bridge_call(bridge, "automation.state", {})
            switching = {str(t.get("name")) for t in (automated.get("tracks") or [])
                         if any(str(r.get("path") or "").endswith("track_activator")
                                for r in t.get("automated") or [])} \
                if isinstance(automated, dict) and not _is_error(automated) else set()
            off = [n for n in muted if n not in switching]
            if off:
                notes.append("Muted with no mute automation (never plays), strip left as it "
                             "is: " + ", ".join(off) + ".")
                pairs = [(d, n) for d, n in pairs if n not in off]
        # per set, like the balance's memory: a "Kick" in another set is another track
        strips_file = library.read_json(library.library_dir() / _STRIPS_FILE, {})
        # an entry holding Channel controls is the old memory without sets: unknown set, dropped
        strips_file = {k: v for k, v in strips_file.items() if isinstance(v, dict) and not (
            set(v) & set(mix.CHANNEL_NEUTRAL))} if isinstance(strips_file, dict) else {}
        strip_set = current_set()
        remembered = dict(strips_file.get(strip_set) or {})
        kept: dict[str, dict[str, str]] = {}
        #: each strip's controls before this run, for the before -> after report
        was: dict[str, dict[str, float | None]] = {}
        if apply:
            for _device, name in pairs:          # at rest: measure the raw part
                rest = dict(mix.CHANNEL_NEUTRAL)
                page = bridge_call(bridge, "devices.parameters", {
                    "track": name, "device": indexes[name], "detail": "minimal", "limit": 64})
                last = remembered.get(name) or {}
                for param in (page.get("parameters") or []) if isinstance(page, dict) \
                        else []:
                    pname = param.get("name")
                    if pname not in rest:
                        continue
                    now = _strip_number(param)
                    was.setdefault(name, {})[pname] = now
                    if keep_manual:
                        if _same(now, rest[pname]):
                            continue
                        if pname in last and _same(now, last[pname]):
                            continue            # LiveBridge's own last setting
                        kept.setdefault(name, {})[pname] = str(param.get("display"))
                        del rest[pname]         # the user's: measure with it in place
                answer = set_strip(name, indexes[name], rest)
                if _is_error(answer):
                    return answer
            time.sleep(_SETTLE_S)
        else:
            notes.append("apply=false: the plan is based on the strips as they are now — if "
                         "they already process, it is not a plan for the raw parts.")
        jobs.note(f"listening to {len(pairs)} parts at rest")
        before = listen_all([d for d, _n in pairs], seconds)
        results: list[dict[str, Any]] = []
        for (device, name), reading in zip(pairs, before):
            if isinstance(reading, engine.EngineError):
                results.append({"name": name, "error": str(reading)})
                continue
            role_info = mix.guess_role(name, reading)
            role = (roles or {}).get(name) or role_info["role"]
            entry: dict[str, Any] = {"name": name, "role": role,
                                     "role_from": "you" if (roles or {}).get(name)
                                     else role_info["from"], "before": _reading(reading)}
            if reading.get("silent"):
                entry.update(values={}, reasons=["silent in this window: left at rest"])
            else:
                plan = mix.channel_plan(role, reading)
                own = kept.get(name) or {}
                values = {k: v for k, v in plan["values"].items() if k not in own}
                reasons = list(plan["reasons"])
                if own:
                    entry["kept"] = own
                    reasons.append("kept what you set by hand: " + ", ".join(
                        f"{k} {v}" for k, v in own.items()))
                entry.update(values=values, reasons=reasons)
                if apply and values:
                    answer = set_strip(name, indexes[name], values)
                    if _is_error(answer):
                        entry["error"] = answer.get("error")
                if apply and "error" not in entry:
                    remembered[name] = {k: v for k, v in {**mix.CHANNEL_NEUTRAL,
                                                          **values}.items() if k not in own}
                    # what this run changed on the strip (rest counts: a control LiveBridge
                    # set before and does not want now goes back to rest)
                    before_values = was.get(name) or {}
                    changes = {k: f"{_fmt(before_values.get(k))} -> {_fmt(v)}"
                               for k, v in remembered[name].items()
                               if k in before_values and not _same(before_values.get(k), v)}
                    entry["changes"] = changes
            results.append(entry)
        result: dict[str, Any] = {"applied": bool(apply), "tracks": results, "notes": notes}
        if apply:
            try:
                strips_file[strip_set] = remembered
                library.write_json(library.library_dir() / _STRIPS_FILE, strips_file)
            except OSError as exc:
                notes.append(f"could not remember the strip settings ({exc}): a rerun treats "
                             "them as set by hand.")
        if placed is not None and (placed["placed"] or placed["replaced"] or placed["skipped"]):
            result["placed"] = placed
        if apply:
            jobs.note("listening again with the strips set")
            time.sleep(_SETTLE_S)
            live = [(d, n) for d, n in pairs
                    if any(r.get("name") == n and "error" not in r for r in results)]
            for (_device, name), reading in zip(live, listen_all([d for d, _n in live],
                                                                   seconds)):
                if not isinstance(reading, engine.EngineError):
                    entry = next(r for r in results if r.get("name") == name)
                    entry["after"] = _reading(reading)
                    crest_before = (entry.get("before") or {}).get("crest_db")
                    crest_after = entry["after"].get("crest_db")
                    if "Comp Ratio" in (entry.get("values") or {}) and \
                            crest_before is not None and crest_after is not None:
                        change = round(crest_before - crest_after, 1)
                        entry["measured"] = (f"crest {crest_before} -> {crest_after} dB "
                                             f"({change:+g} dB off the peaks)")
                        if change < 0.5:
                            entry["measured"] += (" — the compressor barely acts here: its "
                                                  "threshold may sit above the peaks it hears")
            kicks = [r["name"] for r in results if r.get("role") == "kick" and "error" not in r]
            basses = [r["name"] for r in results if r.get("role") in ("bass", "sub")
                      and "error" not in r]
            if low_end and kicks and basses:
                jobs.note(f"low end: {basses[0]} against {kicks[0]} (phase, then unmask)")
                result["low_end"] = {
                    "phase": live_mix_phase(track=basses[0], seconds=2.0),
                    "unmask": live_mix_unmask(track=basses[0], against=kicks[0],
                                              seconds=max(3.0, min(seconds, 8.0)))}
            if balance and len(pairs) >= 2:
                jobs.note("balancing the faders")
                result["balance"] = live_mix_balance(
                    genre=genre, tracks=None if not tracks else names,
                    roles={r["name"]: r["role"] for r in results if r.get("role")},
                    seconds=max(3.0, seconds), protect=protect,
                    one_shots=[r["name"] for r in results
                               if (r.get("before") or {}).get("lufs") is None
                               and "error" not in r] or None)
            balanced = result.get("balance")
            if isinstance(balanced, dict) and "plan" in balanced:
                # one verdict per part: the per-track readings and the balance must agree
                plan = balanced.get("plan") or {}
                moves = {m["name"]: m for m in plan.get("moves") or []}
                for entry in results:
                    name = entry.get("name")
                    if name in moves:
                        entry["balance"] = f"fader {moves[name]['move_db']:+g} dB"
                    elif name in (balanced.get("left_out") or {}):
                        entry["balance"] = "left alone: " + balanced["left_out"][name]
                    elif name in (plan.get("protected") or ()):
                        entry["balance"] = "protected"
                    elif name in (plan.get("unchanged") or ()):
                        entry["balance"] = "unchanged (within the tolerance)"
            if master:
                jobs.note(f"mastering for {master}")
                shared = {b["band"]: b["delta_db"]
                          for b in ((result.get("balance") or {}).get("bands") or [])
                          if isinstance(b, dict) and b.get("fix") == "master"}
                result["master"] = live_master_auto(destination=master, genre=genre,
                                                    seconds=max(3.0, seconds),
                                                    chain=master_chain,
                                                    eq_bands=shared or None)
        jobs.note("done")
        return result if detail == "full" else _summary(result)

    # ------------------------------------------------------------------ phase
    @mcp.tool()
    @_background("live_mix_phase")
    @looped
    def live_mix_phase(
        track: str,
        seconds: float = 2.0,
        frequencies: list[float] | None = None,
        apply: bool = True,
        place_devices: bool = True,
        section_bars: list[int] | None = None,
    ) -> Any:
        """Phase-align a part against the rest of the low end (bass against kick, a layered
        kick against its partner) by measurement, like a phase-rotation plug-in's "analyze":
        puts a LiveBridge Phase device on `track` (polarity, a short delay, all-pass phase
        rotation at a frequency — the level never changes), then tries polarity normal/flipped
        and an all-pass at each frequency while the song loops, reads how much low end (below
        120 Hz) the master keeps each time, and keeps the setting that cancels least — only
        when it beats the untouched sound by more than the measuring noise.

        Args:
            track: the part to rotate (the "submissive" one: usually the bass, not the kick).
            seconds: listening per setting (1..6) — loop one bar where both parts play.
            frequencies: all-pass frequencies to try in Hz (default 40, 55, 70, 90, 120, 160,
                220; each with normal and flipped polarity).
            apply: false = measure every setting, then put the device back to neutral.
            place_devices: put a LiveBridge Engine on the master first when there is none (default).
            section_bars: `[first, last]` bar (1-based) to loop and play while it listens — the
                tool starts the transport there and stops it again afterwards (default: the
                bars playing now). A bar or two where both parts play is enough.

        Returns:
            `{track, best:{invert, frequency?, low_db}, baseline_low_db, gain_db, noise_db,
            applied, tried:[{invert, frequency?, low_db}], notes}`.

        Gotchas: needs a LiveBridge Engine (or Channel) on the master and the song playing a
        short loop where both parts sound; ~2 x 8 settings x `seconds`. A gain under ~1 dB is
        not worth keeping. Needs Max for Live (any version).
        """
        cmd = "mix.phase"
        if not 1 <= seconds <= 6:
            return tool_error("seconds must be 1..6", cmd=cmd)
        freqs = list(frequencies or (40.0, 55.0, 70.0, 90.0, 120.0, 160.0, 220.0))
        if not 1 <= len(freqs) <= 12 or any(not 20 <= f <= 2000 for f in freqs):
            return tool_error("frequencies: 1..12 values of 20..2000 Hz", cmd=cmd)
        if place_devices:
            placed = place_meters(master=True, include_tracks=False)
            if placed.get("error"):
                return placed["error"]
        try:
            device = engine.choose(engine.devices(), "master")
        except engine.EngineError as exc:
            return _engine_failure(exc, cmd)
        if device.get("role") != "master":
            return tool_error("no LiveBridge Engine on the master track. " + _HOW_TO_ADD,
                              type="not_found", cmd=cmd)
        if playing() is False:
            return tool_error("the transport is stopped: pass section_bars=[first, last] (a "
                              "bar where both parts play), or loop one and start playback",
                              type="invalid_state", cmd=cmd)

        def phase_index() -> int | None:
            listing = bridge_call(bridge, "devices.list", {"track": track})
            for d in (listing.get("devices") or []) if isinstance(listing, dict) else []:
                if _PHASE_DEVICE.lower() in str(d.get("name", "")).lower():
                    return d.get("index")
            return None

        notes: list[str] = []
        index = phase_index()
        loaded_here = index is None
        if index is None:
            loaded = bridge_call(bridge, "browser.load", {
                "query": _PHASE_DEVICE, "category": "audio_effect", "track": track},
                timeout=60.0)
            if _is_error(loaded):
                if loaded.get("type") == "not_found":
                    loaded["error"] += (" — is the LiveBridge Phase device installed (the "
                                        "installer puts it in the User Library)?")
                return loaded
            end = time.time() + 10.0
            while index is None and time.time() < end:
                time.sleep(0.3)
                index = phase_index()
            if index is None:
                return tool_error("LiveBridge Phase did not appear on the track",
                                  type="timeout", cmd=cmd)
            notes.append(f"Loaded LiveBridge Phase on {track}.")
        # in front of the Channel: its compressor, saturation and meter work on the
        # phase-corrected part (one loaded after the Channel by an earlier version moves too)
        moved = before_meter(track, index)
        if _is_error(moved):
            return moved
        if moved != index:
            notes.append(f"LiveBridge Phase moved in front of {track}'s Channel.")
            index = phase_index()

        def setting(invert: int, frequency: float | None) -> Any:
            values: dict[str, Any] = {"Invert": float(invert), "Delay": 0.0,
                                      "Phase Stages": 1.0 if frequency else 0.0}
            if frequency:
                values["Phase Freq"] = float(frequency)
            return bridge_call(bridge, "devices.set_parameters",
                               {"track": track, "device": index, "values": _for_live(values)})

        def low_db() -> float | None:
            reading = engine.listen(device, seconds, kind="mix")
            loud = (reading or {}).get("loudness") or {}
            bands = ((reading or {}).get("spectrum") or {}).get("bands_db") or {}
            if loud.get("lufs") is None or not bands:
                return None
            share = sum(10 ** (float(bands.get(b, -90.0)) / 10) for b in ("sub", "bass"))
            return round(float(loud["lufs"]) + 10 * math.log10(max(share, 1e-9)), 2)

        tried: list[dict[str, Any]] = []
        try:
            candidates = [(0, None), (1, None)] + [(i, f) for f in freqs for i in (0, 1)]
            for invert, frequency in candidates:
                answer = setting(invert, frequency)
                if _is_error(answer):
                    return answer
                time.sleep(0.4)                     # the glide settles in ~10 ms; be safe
                level = low_db()
                entry: dict[str, Any] = {"invert": bool(invert), "low_db": level}
                if frequency:
                    entry["frequency"] = frequency
                tried.append(entry)
            setting(0, None)
            time.sleep(0.4)
            again = low_db()
        except engine.EngineError as exc:
            setting(0, None)
            return _engine_failure(exc, cmd)
        valid = [t for t in tried if t["low_db"] is not None]
        if not valid or tried[0]["low_db"] is None:
            return tool_error("the master was silent while measuring: loop a bar where the "
                              "parts play", type="invalid_state", cmd=cmd)
        baseline = tried[0]["low_db"]
        noise = abs((again if again is not None else baseline) - baseline)
        best = max(valid, key=lambda t: t["low_db"])
        gain = round(best["low_db"] - baseline, 2)
        keep = apply and best is not tried[0] and gain > max(1.0, 2 * noise)
        if keep:
            answer = setting(1 if best["invert"] else 0, best.get("frequency"))
            if _is_error(answer):
                return answer
            notes.append(f"Kept {'flipped polarity' if best['invert'] else 'normal polarity'}"
                         + (f" with an all-pass at {best['frequency']:g} Hz"
                            if best.get("frequency") else "")
                         + f": +{gain:g} dB of low end on the master.")
        elif gain <= max(1.0, 2 * noise):
            notes.append(f"No setting gained enough to keep: the best was {gain:+g} dB of low "
                         f"end, the bar is {max(1.0, 2 * noise):g} dB (1 dB, or twice the "
                         f"measuring noise of ±{noise:g} dB when that is more): left neutral. "
                         "The parts were not fighting in phase — look at levels or EQ "
                         "instead.")
        if not keep and loaded_here and phase_index() is not None:
            gone = bridge_call(bridge, "devices.delete", {"track": track,
                                                          "device": phase_index()})
            if not _is_error(gone):
                notes.append(f"Removed the LiveBridge Phase it had loaded on {track} (nothing "
                             "to keep).")
        return {"track": track, "best": best, "baseline_low_db": baseline, "gain_db": gain,
                "noise_db": round(noise, 2), "applied": bool(keep), "tried": tried,
                "notes": notes}


    # ------------------------------------------------------------------ unmask
    @mcp.tool()
    @_background("live_mix_unmask")
    @looped
    def live_mix_unmask(
        track: str,
        against: str,
        frequency: float | None = None,
        q: float | None = None,
        depth_db: float = 6.0,
        seconds: float = 4.0,
        apply: bool = True,
        place_devices: bool = True,
        section_bars: list[int] | None = None,
    ) -> Any:
        """Stop two parts fighting over a band — the way a side-chained dynamic EQ does it: puts
        a LiveBridge Unmask device on `track` (the part that gives way: the bass against the
        kick, the pad against the vocal), feeds it `against` as side-chain, and cuts `track`'s
        clashing band only while `against` plays in it (1 dB per dB over the threshold, at most
        `depth_db`) — nothing changes between the hits. Without `frequency` it measures both
        parts and picks the band where both are strong.

        Args:
            track: the part that gives way.
            against: the part that wins (its signal drives the cut).
            frequency, q: the band to cut (default: the measured clash, its centre and width).
            depth_db: the most it cuts (0..18 dB; 3-6 is transparent, 8+ is an effect).
            seconds: listening window (2..15) — loop a section where both play.
            apply: false = measure and report the band only.
            place_devices: put a LiveBridge Channel on both tracks first when they have no meter
                (default).
            section_bars: `[first, last]` bar (1-based) to loop and play while it listens — the
                tool starts the transport there and stops it again afterwards (default: the
                bars playing now). A bar or two where both parts play is enough.

        Returns:
            `{track, against, band:{band, hz, q, track_db, against_db}, threshold_db,
            depth_db, attack_ms, release_ms, applied, routing?, before:{track_band_db},
            after?:{track_band_db}, notes}`.

        Gotchas: both tracks need a LiveBridge Channel or Engine (to be measured), the song
        must play. Needs Live 12 (a Max device's side-chain input). The cut shows on the
        device ("cut dB"). Run it per pair, the dominant part first (kick > bass, vocal > pad).
        """
        cmd = "mix.unmask"
        if not 0 <= depth_db <= 18:
            return tool_error("depth_db must be 0..18", cmd=cmd)
        if not 2 <= seconds <= 15:
            return tool_error("seconds must be 2..15", cmd=cmd)
        if frequency is not None and not 20 <= frequency <= 8000:
            return tool_error("frequency must be 20..8000 Hz", cmd=cmd)
        if q is not None and not 0.3 <= q <= 8:
            return tool_error("q must be 0.3..8", cmd=cmd)
        if track.strip().lower() == against.strip().lower():
            return tool_error("track and against must be two different tracks", cmd=cmd)
        if place_devices:
            placed = place_meters([track, against], master=False)
            if placed.get("error"):
                return placed["error"]
        try:
            found = engine.devices()
        except engine.EngineError as exc:
            return _engine_failure(exc, cmd)
        meters = {str(d.get("track") or "").lower(): d for d in found
                  if d.get("role") == "track"}
        missing = [n for n in (track, against) if n.strip().lower() not in meters]
        if missing:
            return tool_error("no LiveBridge Channel or Engine on " + " and ".join(missing)
                              + " (it measures the parts). " + _HOW_TO_ADD,
                              type="not_found", cmd=cmd)
        if playing() is False:
            return tool_error("the transport is stopped: pass section_bars=[first, last] "
                              "where both parts play, or loop it and start playback",
                              type="invalid_state", cmd=cmd)
        pair = [meters[track.strip().lower()], meters[against.strip().lower()]]
        mine, theirs = listen_all(pair, seconds)
        for reading in (mine, theirs):
            if isinstance(reading, engine.EngineError):
                return _engine_failure(reading, cmd)
        notes: list[str] = []
        clash = mix.clash_band(mine, theirs)
        if frequency is not None:
            name = next((n for n, lo, hi in mix.BAND_EDGES if lo <= frequency < hi), "air")
            clash = {"band": name, "hz": float(frequency), "q": float(q or 1.0),
                     "track_db": ((mine.get("spectrum") or {}).get("bands_db") or {}).get(name),
                     "against_db": ((theirs.get("spectrum") or {}).get("bands_db")
                                    or {}).get(name)}
        elif clash is None:
            return {"track": track, "against": against, "band": None, "applied": False,
                    "notes": ["The two parts share no strong band (each within 15 dB of its "
                              "own total): nothing to unmask. Check levels instead "
                              "(live_mix_balance)."]}
        if q is not None:
            clash["q"] = float(q)
        loud = (theirs.get("loudness") or {}).get("lufs")
        band_level = (loud if loud is not None else -20.0) + float(clash["against_db"] or 0.0)
        threshold = round(max(-60.0, min(0.0, band_level - 9.0)), 1)
        role = mix.guess_role(against, theirs)["role"]
        attack, release = _UNMASK_TIMING.get(role or "", (5.0, 150.0))
        result: dict[str, Any] = {
            "track": track, "against": against, "band": clash, "threshold_db": threshold,
            "depth_db": depth_db, "attack_ms": attack, "release_ms": release,
            "applied": False, "before": {"track_band_db": clash.get("track_db")},
            "notes": notes}
        if not apply:
            return result

        def unmask_index() -> int | None:
            listing = bridge_call(bridge, "devices.list", {"track": track})
            for d in (listing.get("devices") or []) if isinstance(listing, dict) else []:
                if _UNMASK_DEVICE.lower() in str(d.get("name", "")).lower():
                    return d.get("index")
            return None

        index = unmask_index()
        if index is None:
            loaded = bridge_call(bridge, "browser.load", {
                "query": _UNMASK_DEVICE, "category": "audio_effect", "track": track},
                timeout=60.0)
            if _is_error(loaded):
                if loaded.get("type") == "not_found":
                    loaded["error"] += (" — is the LiveBridge Unmask device installed (the "
                                        "installer puts it in the User Library)?")
                return loaded
            end = time.time() + 10.0
            while index is None and time.time() < end:
                time.sleep(0.3)
                index = unmask_index()
            if index is None:
                return tool_error("LiveBridge Unmask did not appear on the track",
                                  type="timeout", cmd=cmd)
            # in front of the track's meter (Channel / Engine), so the meter hears the cut
            moved = before_meter(track, index)
            if _is_error(moved):
                return moved
            index = unmask_index()
            notes.append(f"Loaded LiveBridge Unmask on {track}.")
        answer = bridge_call(bridge, "devices.set_parameters", {
            "track": track, "device": index, "values": _for_live({
                "Freq": float(clash["hz"]), "Q": float(clash["q"]), "Depth": float(depth_db),
                "Threshold": threshold, "Attack": attack, "Release": release})})
        if _is_error(answer):
            return answer
        routed = bridge_call(bridge, "routing.route", {
            "source": against, "destination": track, "method": "sidechain", "device": index})
        if _is_error(routed):
            routed["error"] = ("could not feed the side-chain (Live 12 is needed for a Max "
                               "device's side-chain input): " + str(routed.get("error")))
            return routed
        result["routing"] = routed.get("routing")
        result["applied"] = True
        time.sleep(_SETTLE_S)
        after = engine.listen(pair[0], seconds)
        bands = ((after.get("spectrum") or {}).get("bands_db")) or {}
        result["after"] = {"track_band_db": bands.get(clash["band"])}
        if bands.get(clash["band"]) is not None and clash.get("track_db") is not None:
            notes.append(f"{track}'s {clash['band']} band: {clash['track_db']} -> "
                         f"{round(bands[clash['band']], 1)} dB of its total (averaged over "
                         "the window; the cut itself only happens on the hits).")
        return result


    # ------------------------------------------------------------------ jobs
    @mcp.tool()
    def live_mix_job(job: int | None = None, wait_seconds: float = 0.0) -> Any:
        """The report of a mix or master run that outlived its call. `live_mix_auto`,
        `live_mix_balance`, `live_master_auto`, `live_mix_phase` and `live_mix_unmask` run as
        background jobs: when one takes longer than ~40 s, its call answers `{job, status:
        "running", phase}` while the work goes on in Live — ask here (with `wait_seconds`) until
        `status` is "done" and read `result`, the tool's full report. Read-only.

        Args:
            job: the job id from the "running" answer (default: the newest job).
            wait_seconds: wait up to this many seconds for it to finish (0..40).

        Returns:
            `{job, tool, status: running|done|failed, elapsed_s, phase?, result?}` — result is
            the tool's own report (or its error).

        Gotchas: only one mix/master run at a time — another one is refused as `busy` until
        this one ends (it would fight over the same devices). The transport is left as the
        run left it (playing, or stopped again when it was started for loud_bar/quiet_bar).
        """
        cmd = "mix.job"
        if not 0 <= wait_seconds <= 40:
            return tool_error("wait_seconds must be 0..40", cmd=cmd)
        found = jobs.get(job, wait_seconds)
        if found is None:
            return tool_error("no mix/master job" + (f" {job}" if job is not None else "")
                              + " — none ran since the server started (or it is too old)",
                              type="not_found", cmd=cmd)
        return found
