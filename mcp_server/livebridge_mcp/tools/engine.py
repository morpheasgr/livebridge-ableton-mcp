"""LiveBridge Engine tools — real-time listening through the LiveBridge Engine Max for Live
device: `live_engine_status` lists the running devices, `live_engine_listen` reads what is
playing right now (loudness, peaks, spectral balance, stereo) without bouncing anything,
`live_engine_compare` is the A/B check of a change, `live_engine_capture` records what the
devices hear to WAV files and `live_engine_masking` finds the parts that fight for the same
frequencies while the song plays.

The device is optional: drop "LiveBridge Engine" (User Library > Presets > Audio Effects >
Max Audio Effect) on the master track and at the end of the tracks to compare. Without it these
tools say how to add it; everything else in LiveBridge works as before. See
:mod:`livebridge_mcp.engine`.
"""

from __future__ import annotations

import math
import time
from typing import Any

from . import bridge_call, tool_error
from .. import audio_analysis, engine
from ..client import BridgeClient

_KINDS = ("auto", "mix", "stem")
_HOW_TO_ADD = ("Add the LiveBridge Engine device: in Live's browser open User Library > "
               "Presets > Audio Effects > Max Audio Effect > LiveBridge Engine and drag it onto "
               "the master track (it needs Max for Live: Live Suite or the add-on). The "
               "LiveBridge installer puts it there.")


_MAX_SAVED = 32
_MAX_MASKING_PARTS = 8
_READING_KEYS = ("device", "seconds", "loudness", "spectrum", "stereo", "flags")


def _is_error(value: Any) -> bool:
    return isinstance(value, dict) and "error" in value and "type" in value


def _engine_failure(exc: engine.EngineError, cmd: str) -> dict[str, Any]:
    message = str(exc)
    if exc.type == "not_found":
        return tool_error(message + ". " + _HOW_TO_ADD, type="not_found", cmd=cmd)
    if exc.type in ("bad_args", "busy", "unsupported", "capture_failed", "io"):
        kind = {"bad_args": "bad_args", "unsupported": "unsupported"}.get(exc.type,
                                                                          "invalid_state")
        return tool_error(message, type=kind, cmd=cmd)
    return tool_error(message, type="connection", cmd=cmd)


def _where(device: dict[str, Any]) -> dict[str, Any]:
    return {"role": device.get("role"), "track": device.get("track")}


def _name(device: dict[str, Any]) -> str:
    return "master" if device.get("role") == "master" else str(device.get("track") or "track")


def register(mcp: Any, bridge: BridgeClient) -> None:
    """Register the Engine tools on the MCP app."""

    #: live_engine_listen(save_as=...) readings for live_engine_compare, newest last
    saved: dict[str, dict[str, Any]] = {}

    def playing() -> bool | None:
        state = bridge_call(bridge, "transport.get")
        if isinstance(state, dict) and not _is_error(state) and "is_playing" in state:
            return bool(state["is_playing"])
        return None

    def read(device: dict[str, Any], seconds: float, history: bool, timeline: bool,
             kind: str) -> dict[str, Any]:
        answer = engine.request(device, "frames", {"seconds": seconds, "wait": not history},
                                timeout=seconds + 10.0)
        if not isinstance(answer, dict):
            raise engine.EngineError("protocol", "the device sent an unexpected answer")
        role = answer.get("role") or device.get("role")
        chosen = kind if kind != "auto" else ("mix" if role == "master" else "stem")
        result = engine.summarize(answer.get("frames") or [],
                                  answer.get("fields") or engine.FIELDS, kind=chosen,
                                  timeline=timeline)
        where = {"role": role, "track": answer.get("track") or device.get("track")}
        return {"device": where, **result}

    def stopped_note(result: dict[str, Any], is_playing: bool | None) -> None:
        if is_playing is not None:
            result["playing"] = is_playing
            if result.get("silent") and not is_playing:
                result["flags"] = ["Silence: Live's transport is stopped — start playback "
                                   "(live_transport_play) and listen again."]

    def remember(label: str, reading: dict[str, Any]) -> None:
        saved.pop(label, None)
        saved[label] = {k: reading[k] for k in _READING_KEYS if k in reading}
        saved[label]["saved_at"] = time.time()
        while len(saved) > _MAX_SAVED:
            saved.pop(next(iter(saved)))

    @mcp.tool()
    def live_engine_status() -> Any:
        """Which LiveBridge Engine devices are running (the Max for Live device that gives
        LiveBridge real-time ears): where each sits (master or a track), its version and
        whether audio frames are arriving. Read-only.

        Returns:
            `{devices:[{role ("master"|"track"), track, version, frames_buffered,
            frame_interval_ms, started}], hint?}`.
        """
        found = engine.devices()
        keep = ("role", "track", "version", "frames_buffered", "frame_interval_ms",
                "started", "instance")
        result: dict[str, Any] = {"devices": [{k: d.get(k) for k in keep} for d in found]}
        if not found:
            result["hint"] = _HOW_TO_ADD
        elif not any(d.get("frames_buffered") for d in found):
            result["hint"] = ("The device runs but no audio frames arrive yet: check that "
                              "Live's audio engine is on and the device is not switched off.")
        return result

    @mcp.tool()
    def live_engine_listen(
        seconds: float = 3.0,
        track: str | None = None,
        history: bool = False,
        timeline: bool = False,
        kind: str = "auto",
        save_as: str | None = None,
    ) -> Any:
        """Listen to what is playing right now, through the LiveBridge Engine device: loudness
        (momentary, short-term and over the window, in LUFS), peak and RMS level, crest factor,
        the balance of 8 frequency bands, stereo correlation and width (and the width below
        120 Hz), plus plain-language mix flags — the same readings as `live_audio_analyze`, but
        live, with no bounce. Use it to check a change while the track plays ("is the drop
        louder now?", "is the low end mono?"). Read-only.

        Args:
            seconds: How long to listen (0.2..30); with history=true, how far back to look
                (up to 60).
            track: Which device: omitted / "master" = the one on the master track, a track
                name for a device on that track, or "all" = every device over the same window
                (compare the tracks' levels and balance).
            history: Answer at once from what already played (the device keeps the last 60 s)
                instead of listening from now on.
            timeline: Also return one reading per second (momentary loudness and peak).
            kind: "mix" (full-mix targets for the flags, default on the master), "stem"
                (no spectral targets, default on tracks) or "auto".
            save_as: Keep this reading under a name ("before") for `live_engine_compare`
                (one device only).

        Returns:
            `{device:{role, track}, frames, seconds, silent, playing?, loudness:{
            momentary_lufs, short_term_lufs, lufs, max_momentary_lufs, peak_db, rms_db,
            rms_left_db, rms_right_db, crest_db}, spectrum:{bands_db}, stereo:{correlation,
            width, low_end_width}, flags, timeline?}`; with track="all" `{devices:[...those],
            playing?}`.

        Gotchas: listening takes real time (the call returns after `seconds`). Make sure the
        song is playing — `live_transport_play` — or use history=true right after it played.
        LUFS here is ungated (silence excluded) and the peak is a sample peak, so values can
        differ from a mastering meter by a fraction of a dB.
        """
        cmd = "engine.listen"
        if kind not in _KINDS:
            return tool_error(f"kind must be one of {', '.join(_KINDS)}", cmd=cmd)
        limit = 60.0 if history else 30.0
        if not 0.2 <= seconds <= limit:
            return tool_error(f"seconds must be 0.2..{limit:g}", cmd=cmd)
        everyone = isinstance(track, str) and track.strip().lower() == "all"
        if save_as is not None and (everyone or not save_as.strip()):
            return tool_error("save_as needs a name and a single device (not track='all')",
                              cmd=cmd)
        try:
            found = engine.devices()
            if everyone:
                chosen = engine.select(found)
                readings = engine.each(chosen, lambda d: read(d, seconds, history, timeline,
                                                              kind))
            else:
                result = read(engine.choose(found, track), seconds, history, timeline, kind)
        except engine.EngineError as exc:
            return _engine_failure(exc, cmd)
        is_playing = playing()
        if everyone:
            items = []
            for device, reading in zip(chosen, readings):
                if isinstance(reading, engine.EngineError):
                    items.append({"device": _where(device), "error": str(reading)})
                else:
                    stopped_note(reading, None)
                    items.append(reading)
            answer: dict[str, Any] = {"devices": items}
            if is_playing is not None:
                answer["playing"] = is_playing
            return answer
        stopped_note(result, is_playing)
        if save_as is not None:
            remember(save_as.strip(), result)
            result["saved_as"] = save_as.strip()
        return result

    @mcp.tool()
    def live_engine_compare(
        before: str,
        after: str | None = None,
        seconds: float = 4.0,
        history: bool = False,
    ) -> Any:
        """A/B check while the song plays: did the change help? Compares a reading saved with
        `live_engine_listen(save_as=...)` with a second saved one or with what plays now (same
        device), and reports what changed — loudness, peak, punch (crest), the 8 bands, stereo
        and low-end width, flags that went away or appeared — with better/worse verdicts.
        Read-only.

        Typical: loop the section, `live_engine_listen(seconds=4, save_as="before")`, change
        the mix (EQ, compressor, Serum patch, fader), then `live_engine_compare(before=
        "before")`.

        Args:
            before: Name of a saved reading.
            after: Name of another saved reading; omitted = listen now on the same device.
            seconds: How long to listen for `after` (0.2..30; with history=true up to 60).
            history: Read `after` from what already played instead of listening from now on.

        Returns:
            `{before:{device, loudness, spectrum, stereo, flags}, after:{...},
            comparison:{delta, changes, resolved_flags, new_flags, notes}, saved}`.

        Gotchas: compare the same music — loop the section, or listen at the same spot. Saved
        readings live until the MCP server restarts (newest 32).
        """
        cmd = "engine.compare"
        first = saved.get(before)
        if first is None:
            return tool_error(f"no saved reading {before!r} — saved: {sorted(saved) or 'none'}"
                              " (live_engine_listen(save_as=...))", type="not_found", cmd=cmd)
        if after is not None:
            second = saved.get(after)
            if second is None:
                return tool_error(f"no saved reading {after!r} — saved: {sorted(saved)}",
                                  type="not_found", cmd=cmd)
        else:
            limit = 60.0 if history else 30.0
            if not 0.2 <= seconds <= limit:
                return tool_error(f"seconds must be 0.2..{limit:g}", cmd=cmd)
            where = first.get("device") or {}
            target = "master" if where.get("role") == "master" else where.get("track")
            try:
                device = engine.choose(engine.devices(), target or None)
                second = read(device, seconds, history, False, "auto")
            except engine.EngineError as exc:
                return _engine_failure(exc, cmd)
            second = {k: second[k] for k in _READING_KEYS if k in second}
            if second.get("loudness") is None:
                return tool_error("nothing played during the second listen — start playback "
                                  "(live_transport_play) and compare again",
                                  type="invalid_state", cmd=cmd)
        keep = ("device", "loudness", "spectrum", "stereo", "flags")
        return {"before": {k: first[k] for k in keep if k in first},
                "after": {k: second[k] for k in keep if k in second},
                "comparison": audio_analysis.compare_versions(first, second),
                "saved": sorted(saved)}

    @mcp.tool()
    def live_engine_capture(
        seconds: float = 4.0,
        tracks: list[str] | None = None,
    ) -> Any:
        """Record what the LiveBridge Engine devices hear to WAV files, all starting at the
        same moment — a bounce without bouncing: the master's mix, or every track that has
        an Engine at its end. The files work with `live_audio_analyze` (tempo, key, energy),
        `live_audio_compare`, `live_audio_masking` and `live_sample_import` /
        `live_drumrack_set_pad` (resample a part). Writes only files in ~/.livebridge/captures.

        Args:
            seconds: Length (0.5..60). Recording runs in real time while the song plays.
            tracks: Which devices: omitted = all, else track names ("master" for the master's).

        Returns:
            `{captures:[{device:{role, track}, file_path, seconds}], errors?, playing?}`.

        Gotchas: start playback first (`live_transport_play`). A device on a track records
        the track's sound before its fader, pan and mute (put it last in the chain). Files
        older than a week are deleted.
        """
        cmd = "engine.capture"
        if not 0.5 <= seconds <= 60:
            return tool_error("seconds must be 0.5..60", cmd=cmd)
        try:
            chosen = engine.select(engine.devices(), tracks)
        except engine.EngineError as exc:
            return _engine_failure(exc, cmd)
        results = engine.capture(chosen, seconds)
        captures, errors = [], []
        for device, answer in zip(chosen, results):
            if isinstance(answer, engine.EngineError):
                errors.append({"device": _where(device), "error": str(answer),
                               "type": answer.type})
            elif isinstance(answer, dict) and answer.get("file_path"):
                captures.append({"device": {"role": answer.get("role") or device.get("role"),
                                            "track": answer.get("track") or
                                            device.get("track")},
                                 "file_path": answer["file_path"],
                                 "seconds": round(float(answer.get("seconds") or seconds), 2)})
        if not captures:
            first = next((r for r in results if isinstance(r, engine.EngineError)), None)
            if first is not None:
                return _engine_failure(first, cmd)
            return tool_error("no device recorded anything", type="invalid_state", cmd=cmd)
        result: dict[str, Any] = {"captures": captures}
        if errors:
            result["errors"] = errors
        is_playing = playing()
        if is_playing is not None:
            result["playing"] = is_playing
            if not is_playing:
                result["note"] = ("Live's transport was stopped: the files may hold only "
                                  "silence or tails.")
        return result

    @mcp.tool()
    def live_engine_masking(
        tracks: list[str] | None = None,
        seconds: float = 8.0,
        top: int = 3,
    ) -> Any:
        """Which parts fight for the same frequencies, measured live: records every track
        that has a LiveBridge Engine at its end over the same seconds while the song plays,
        weighs each by its fader level and reports per pair the bands where both are active
        and close in level, which one is louder and what to do — `live_audio_masking`
        without bouncing each part. Read-only (writes temporary captures only).

        Args:
            tracks: 2..8 track names with an Engine (omitted = every track device; the
                master's is never part of it).
            seconds: Window to record (2..30) — loop the busiest section (the drop).
            top: Bands reported per pair (1..8).

        Returns:
            `{pairs:[{parts, score (0-100, worst first), bands:[{band, hz, region, overlap,
            louder, level_db}], advice}], parts, notes, captures:[file paths]}`.

        Gotchas: start playback first. Muted tracks and tracks at -inf are left out. Needs the
        `audio` extra (numpy/scipy/soundfile). Scores above ~30 deserve a look.
        """
        cmd = "engine.masking"
        if not 2 <= seconds <= 30:
            return tool_error("seconds must be 2..30", cmd=cmd)
        if not 1 <= top <= 8:
            return tool_error("top must be 1..8", cmd=cmd)
        if tracks is not None and not 2 <= len(tracks) <= _MAX_MASKING_PARTS:
            return tool_error(f"tracks must list 2..{_MAX_MASKING_PARTS} names", cmd=cmd)
        try:
            chosen = [d for d in engine.select(engine.devices(), tracks, master=False)
                      if d.get("role") != "master"]
        except engine.EngineError as exc:
            return _engine_failure(exc, cmd)
        notes: list[str] = []
        gains: dict[str, float] = {}
        names = [str(d.get("track") or "") for d in chosen]
        mixer = bridge_call(bridge, "mixer.get", {"track": names, "detail": "minimal"}) \
            if names else None
        if isinstance(mixer, dict) and isinstance(mixer.get("tracks"), list):
            keep = []
            for device, state in zip(chosen, mixer["tracks"]):
                name = _name(device)
                volume = state.get("volume") if isinstance(state, dict) else None
                if isinstance(state, dict) and state.get("mute"):
                    notes.append(f"{name} is muted — left out.")
                    continue
                if volume == "-inf" or (isinstance(volume, (int, float))
                                        and math.isinf(volume)):
                    notes.append(f"{name} is at -inf dB — left out.")
                    continue
                if isinstance(volume, (int, float)):
                    gains[name] = float(volume)
                keep.append(device)
            chosen = keep
        else:
            notes.append("Fader levels unknown (Live not reachable): parts compared at their "
                         "pre-fader level.")
        if len(chosen) < 2:
            return tool_error("masking needs at least 2 tracks with a LiveBridge Engine at the "
                              "end of their chain (unmuted). " + " ".join(notes),
                              type="not_found", cmd=cmd)
        chosen = chosen[:_MAX_MASKING_PARTS]
        results = engine.capture(chosen, seconds)
        sources, files, seen = [], [], set()
        for device, answer in zip(chosen, results):
            if isinstance(answer, engine.EngineError) or not isinstance(answer, dict):
                notes.append(f"{_name(device)}: not recorded ({answer}).")
                continue
            name = _name(device)
            while name in seen:
                name += "'"
            seen.add(name)
            files.append(answer["file_path"])
            sources.append({"name": name, "path": answer["file_path"],
                            "gain_db": gains.get(_name(device), 0.0)})
        if len(sources) < 2:
            return tool_error("fewer than 2 parts were recorded. " + " ".join(notes),
                              type="invalid_state", cmd=cmd)
        try:
            result = audio_analysis.masking(sources, top=top)
        except audio_analysis.MissingDependency as exc:
            return tool_error(str(exc), type="unsupported", cmd=cmd)
        except Exception as exc:  # unreadable capture
            return tool_error(f"could not analyse the captures: {exc}", type="invalid_state",
                              cmd=cmd)
        result["notes"] = notes + list(result.get("notes") or [])
        result["captures"] = files
        is_playing = playing()
        if is_playing is False:
            result["notes"].append("Live's transport was stopped while recording.")
        return result
