"""LiveBridge Engine tools — real-time listening through the LiveBridge Engine Max for Live
device: `live_engine_status` lists the running devices, `live_engine_listen` reads what is
playing right now (loudness, peaks, spectral balance, stereo) without bouncing anything.

The device is optional: drop "LiveBridge Engine" (User Library > Presets > Audio Effects >
Max Audio Effect) on the master track (and later on other tracks). Without it these tools say
how to add it; everything else in LiveBridge works as before. See :mod:`livebridge_mcp.engine`.
"""

from __future__ import annotations

from typing import Any

from . import bridge_call, tool_error
from .. import engine
from ..client import BridgeClient

_KINDS = ("auto", "mix", "stem")
_HOW_TO_ADD = ("Add the LiveBridge Engine device: in Live's browser open User Library > "
               "Presets > Audio Effects > Max Audio Effect > LiveBridge Engine and drag it onto "
               "the master track (it needs Max for Live: Live Suite or the add-on). The "
               "LiveBridge installer puts it there.")


def _is_error(value: Any) -> bool:
    return isinstance(value, dict) and "error" in value and "type" in value


def register(mcp: Any, bridge: BridgeClient) -> None:
    """Register the Engine tools on the MCP app."""

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
            track: Which device: omitted / "master" = the one on the master track, or a track
                name for a device on that track.
            history: Answer at once from what already played (the device keeps the last 60 s)
                instead of listening from now on.
            timeline: Also return one reading per second (momentary loudness and peak).
            kind: "mix" (full-mix targets for the flags, default on the master), "stem"
                (no spectral targets, default on tracks) or "auto".

        Returns:
            `{device:{role, track}, frames, seconds, silent, playing?, loudness:{
            momentary_lufs, short_term_lufs, lufs, max_momentary_lufs, peak_db, rms_db,
            rms_left_db, rms_right_db, crest_db}, spectrum:{bands_db}, stereo:{correlation,
            width, low_end_width}, flags, timeline?}`.

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
        try:
            device = engine.choose(engine.devices(), track)
            answer = engine.request(device, "frames", {"seconds": seconds, "wait": not history},
                                    timeout=seconds + 10.0)
        except engine.EngineError as exc:
            message = str(exc)
            if exc.type == "not_found":
                message += ". " + _HOW_TO_ADD
            return tool_error(message, type="not_found" if exc.type == "not_found"
                              else "connection", cmd=cmd)
        if not isinstance(answer, dict):
            return tool_error("the device sent an unexpected answer", type="internal", cmd=cmd)
        role = answer.get("role") or device.get("role")
        chosen = kind if kind != "auto" else ("mix" if role == "master" else "stem")
        result = engine.summarize(answer.get("frames") or [],
                                  answer.get("fields") or engine.FIELDS, kind=chosen,
                                  timeline=timeline)
        result = {"device": {"role": role, "track": answer.get("track") or device.get("track")},
                  **result}
        state = bridge_call(bridge, "transport.get")
        if isinstance(state, dict) and not _is_error(state) and "is_playing" in state:
            result["playing"] = bool(state["is_playing"])
            if result.get("silent") and not state["is_playing"]:
                result["flags"] = ["Silence: Live's transport is stopped — start playback "
                                   "(live_transport_play) and listen again."]
        return result
