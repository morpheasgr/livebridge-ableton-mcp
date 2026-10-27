"""LiveBridge MIDI tools — the LiveBridge MIDI device (a Max for Live MIDI effect) hears what
reaches it on its track and plays notes into the instrument after it:
`live_midi_played` returns what was just played (your keyboard on an armed / monitored track,
or the track's clips) as clip-ready notes, `live_midi_play` plays a phrase live in time with
the song (no clip written), `live_midi_stop` stops it.

The device is optional and talks to the MCP server like the LiveBridge Engine (registry files in
``~/.livebridge/engine`` with kind "midi", see :mod:`livebridge_mcp.engine`).
"""

from __future__ import annotations

import math
from typing import Any

from . import bridge_call, tool_error
from .. import engine
from ..client import BridgeClient

_WHAT = "LiveBridge MIDI device"
_HOW_TO_ADD = ("Add it yourself: live_browser_load(query=\"LiveBridge MIDI\", category="
               "\"midi_effect\", track=\"<MIDI track>\") — it goes before the instrument — then "
               "wait until live_engine_status lists it under midi_devices (~2 s). By hand: "
               "User Library > Presets > MIDI Effects > Max MIDI Effect > LiveBridge MIDI. "
               "It needs Max for Live; the LiveBridge installer puts it there.")
_STARTS = ("now", "next_beat", "next_bar")


def _is_error(value: Any) -> bool:
    return isinstance(value, dict) and "error" in value and "type" in value


def _failure(exc: engine.EngineError, cmd: str) -> dict[str, Any]:
    message = str(exc)
    if exc.type == "not_found":
        missing = message.startswith(("no " + _WHAT, "no Engine on"))
        return tool_error(message + (". " + _HOW_TO_ADD if missing else ""), type="not_found",
                          cmd=cmd)
    if exc.type == "bad_args":
        return tool_error(message, cmd=cmd)
    return tool_error(message, type="connection", cmd=cmd)


def _grid(value: Any) -> float:
    """A grid in beats from a note value ("1/16") or a number of beats (0.25)."""
    timing = engine.parse_rate(value)
    if "hz" in timing or not 1 / 64 <= timing["beats"] <= 4:
        raise ValueError("quantize: a note value such as '1/16', '1/8', '1/8T' or '1/4'")
    return timing["beats"]


def notes_from(answer: dict[str, Any], beats_per_bar: float,
               quantize: float | None = None) -> dict[str, Any]:
    """Clip-ready notes from a device's ``played`` answer: starts in beats from the bar the
    first note falls in, durations in beats (held notes end now and are listed in ``held``)."""
    tempo = float(answer.get("tempo") or 120.0)
    now_ms = float(answer.get("now") or 0.0)
    now_beat = answer.get("now_beat")
    raw = answer.get("notes") or []

    def beat_of(ms: float | None, beat: Any) -> float:
        if isinstance(beat, (int, float)):
            return float(beat)
        moment = now_ms if ms is None else float(ms)
        base = float(now_beat) if isinstance(now_beat, (int, float)) else now_ms / 60000 * tempo
        return base - (now_ms - moment) / 60000.0 * tempo

    events = []
    for note in raw:
        start = beat_of(note.get("ms"), note.get("beat"))
        end = beat_of(note.get("end_ms") if note.get("end_ms") is not None else None,
                      note.get("end_beat") if note.get("end_ms") is not None else None)
        events.append((start, max(end, start), note))
    if not events:
        return {"notes": [], "origin_beat": None, "bars": 0}
    first = min(e[0] for e in events)
    origin = math.floor(first / beats_per_bar + 1e-9) * beats_per_bar
    notes, held = [], []
    for start, end, note in events:
        rel, length = start - origin, end - start
        if quantize:
            snapped = round(rel / quantize) * quantize
            length = max(quantize, round(length / quantize) * quantize)
            rel = snapped
        item = {"pitch": int(note["pitch"]), "start": round(max(rel, 0.0), 4),
                "duration": round(max(length, 1 / 64), 4),
                "velocity": int(note.get("velocity") or 100)}
        if note.get("held"):
            held.append(item["pitch"])
        notes.append(item)
    notes.sort(key=lambda n: (n["start"], n["pitch"]))
    end = max(n["start"] + n["duration"] for n in notes)
    result = {"notes": notes, "origin_beat": round(origin, 4),
              "bars": max(1, math.ceil(end / beats_per_bar - 1e-9))}
    if held:
        result["held"] = sorted(set(held))           # still down: they end "now"
    return result


def register(mcp: Any, bridge: BridgeClient) -> None:
    """Register the LiveBridge MIDI tools on the MCP app."""

    def beats_per_bar() -> float:
        state = bridge_call(bridge, "transport.get")
        try:
            numerator, denominator = (float(v) for v in str(
                state.get("signature") or "4/4").split("/"))
            return numerator * 4.0 / denominator
        except (AttributeError, ValueError):
            return 4.0

    def device_for(track: Any) -> dict[str, Any]:
        found = engine.devices(kind="midi")
        return engine.choose(found, None if track is None else str(track), what=_WHAT)

    @mcp.tool()
    def live_midi_played(
        seconds: float = 30.0,
        track: str | None = None,
        quantize: str | float | None = None,
    ) -> Any:
        """What was just played on a MIDI track, as notes ready for `live_clip_add_notes` —
        "I just played a riff, turn it into a clip", "harmonize what I played", "what key was
        that?" (then `live_theory_analyze`). The LiveBridge MIDI device hears everything that
        reaches it: your keyboard while the track is armed or monitored, and the track's own
        clips. It keeps the last 10 minutes. Read-only.

        Args:
            seconds: How far back (up to 600).
            track: The track whose LiveBridge MIDI device to read (default: the only / first).
            quantize: Snap starts and lengths to a grid ("1/16", "1/8", "1/8T" ... or beats,
                0.25); default as played.

        Returns:
            `{device:{track}, notes:[{pitch, start, duration, velocity}], bars, origin_beat,
            held?:[pitches still down], controllers:[{cc, values, last}], tempo, playing}` —
            starts are beats from the bar the first note fell in (origin_beat = that bar's
            song position).

        Gotchas: timing comes from Live's song position while playing (free timing when
        stopped, measured at the song tempo). Clips playing on the track are captured too.
        """
        cmd = "midi.played"
        if not 0 < seconds <= 600:
            return tool_error("seconds must be > 0 and <= 600", cmd=cmd)
        try:
            grid = _grid(quantize) if quantize is not None else None
        except ValueError as exc:
            return tool_error(str(exc), cmd=cmd)
        try:
            device = device_for(track)
            answer = engine.request(device, "played", {"seconds": seconds})
        except engine.EngineError as exc:
            return _failure(exc, cmd)
        if not isinstance(answer, dict):
            return tool_error("unexpected answer from the device", type="internal", cmd=cmd)
        result = notes_from(answer, beats_per_bar(), grid)
        controllers: dict[int, list[int]] = {}
        for change in answer.get("controllers") or []:
            controllers.setdefault(int(change["cc"]), []).append(int(change["value"]))
        return {"device": {"track": device.get("track")}, **result,
                "controllers": [{"cc": cc, "values": len(values), "last": values[-1]}
                                for cc, values in sorted(controllers.items())],
                "tempo": answer.get("tempo"), "playing": answer.get("playing")}

    @mcp.tool()
    def live_midi_play(
        notes: list[dict[str, Any]],
        track: str | None = None,
        start: str = "next_bar",
        repeat: int = 1,
        loop_beats: float | None = None,
    ) -> Any:
        """Play notes live into a track's instrument, in time with the song — audition a
        melody, chord voicing or bass line before writing it, or jam along — through the
        LiveBridge MIDI device. Nothing is written (no clip); `live_midi_stop` ends it.

        Args:
            notes: `[{pitch, start, duration, velocity?}]` — beats from the start, the same
                shape as `live_clip_add_notes` (up to 2000).
            track: The track whose LiveBridge MIDI device plays (default: the only / first).
            start: "next_bar" (default), "next_beat" or "now".
            repeat: Play it this many times (1..256), every `loop_beats`.
            loop_beats: Length of one pass (default: the notes' end rounded up to a beat).

        Returns:
            `{playing:{notes, repeat, loop_beats, starts_in_beats, starts_at_beat, seconds,
            tempo}, device:{track}}`.

        Gotchas: timed by the device's clock at the current tempo (a tempo change mid-phrase
        is not followed) — fine for auditioning, a clip is tighter. Playing again replaces
        what plays. The track's instrument must come after the device.
        """
        cmd = "midi.play"
        if start not in _STARTS:
            return tool_error(f"start must be one of {', '.join(_STARTS)}", cmd=cmd)
        if not 1 <= repeat <= 256:
            return tool_error("repeat must be 1..256", cmd=cmd)
        if loop_beats is not None and not 0 < loop_beats <= 1024:
            return tool_error("loop_beats must be > 0 and <= 1024", cmd=cmd)
        if not notes or len(notes) > 2000:
            return tool_error("notes: 1..2000 notes", cmd=cmd)
        args: dict[str, Any] = {"notes": notes, "start": start, "repeat": repeat,
                                "beats_per_bar": beats_per_bar()}
        if loop_beats is not None:
            args["loop_beats"] = loop_beats
        try:
            device = device_for(track)
            answer = engine.request(device, "play", args, timeout=8.0)
        except engine.EngineError as exc:
            return _failure(exc, cmd)
        return {"playing": answer, "device": {"track": device.get("track")}}

    @mcp.tool()
    def live_midi_stop(track: str | None = None) -> Any:
        """Stop what `live_midi_play` plays (every LiveBridge MIDI device, or the one on
        `track`); sounding notes are released.

        Returns:
            `{stopped:[{track, was}]}`.
        """
        cmd = "midi.stop"
        try:
            found = engine.devices(kind="midi")
            chosen = [engine.choose(found, str(track), what=_WHAT)] if track is not None \
                else found
            if not chosen:
                raise engine.EngineError("not_found", f"no {_WHAT} is running")
        except engine.EngineError as exc:
            return _failure(exc, cmd)
        stopped = []
        for device, answer in zip(chosen, engine.each(
                chosen, lambda d: engine.request(d, "stop"))):
            if isinstance(answer, dict):
                stopped.append({"track": device.get("track"), "was": answer.get("stopped")})
        return {"stopped": stopped}
