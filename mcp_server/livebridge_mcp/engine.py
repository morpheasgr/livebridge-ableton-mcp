"""Client for the LiveBridge Engine Max for Live device (``max_for_live/LiveBridge Engine``).

The device measures the audio passing through it in real time and keeps the last minute of
"frames" (18 meter values every ~100 ms). Each running device writes a registry file
``~/.livebridge/engine/<instance>.json`` with its local TCP port and a token
(``LIVEBRIDGE_ENGINE_DIR`` moves the folder); requests are JSON lines:
``{"id", "token", "cmd": "hello" | "frames", "args"}`` -> ``{"id", "ok", "result" | "error"}``.

:func:`summarize` turns frames into what a producer reads off meters: loudness (momentary,
short-term, over the window — BS.1770 K-weighted, ungated except for silence), peaks, RMS,
crest factor, the 8 spectral bands of :mod:`audio_analysis` (dB relative to their sum), stereo
correlation/width and the width below 120 Hz, plus the same plain-language mix flags as
``live_audio_analyze``.
"""

from __future__ import annotations

import json
import math
import os
import socket
import time
from pathlib import Path
from typing import Any

from .audio_analysis import BANDS, _flags
from .config import config_dir

__all__ = ["EngineError", "devices", "engine_dir", "request", "summarize"]

FIELDS = ("ms_left", "ms_right", "peak_left", "peak_right", "ms_k",
          "band_sub", "band_bass", "band_low_mid", "band_mud", "band_mid", "band_presence",
          "band_high", "band_air", "ms_mid", "ms_side", "mean_lr", "ms_mid_low", "ms_side_low")
_SILENT = 1e-10                       # mean square below -100 dB
_SHORT_TERM_S = 3.0
_REGISTRY_KEYS = {"port", "token", "instance"}


class EngineError(RuntimeError):
    """A device could not be reached or refused a request."""

    def __init__(self, type: str, message: str):
        super().__init__(message)
        self.type = type


def engine_dir() -> Path:
    override = os.environ.get("LIVEBRIDGE_ENGINE_DIR", "").strip()
    return Path(override).expanduser() if override else config_dir() / "engine"


def request(device: dict[str, Any], cmd: str, args: dict[str, Any] | None = None,
            timeout: float = 5.0) -> Any:
    """One request to a device; returns its result or raises :class:`EngineError`."""
    payload = {"id": 1, "token": device.get("token"), "cmd": cmd, "args": args or {}}
    try:
        with socket.create_connection((device.get("host") or "127.0.0.1",
                                       int(device["port"])), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
            buffer = b""
            while b"\n" not in buffer:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                buffer += chunk
    except ConnectionRefusedError as exc:
        raise EngineError("connection", f"the device is not running ({exc})") from exc
    except (OSError, ValueError) as exc:
        raise EngineError("connection", f"could not reach the device: {exc}") from exc
    if b"\n" not in buffer:
        raise EngineError("connection", "the device closed the connection without answering")
    try:
        answer = json.loads(buffer.split(b"\n", 1)[0].decode("utf-8"))
    except ValueError as exc:
        raise EngineError("protocol", f"unreadable answer from the device: {exc}") from exc
    if not answer.get("ok"):
        error = answer.get("error") or {}
        raise EngineError(str(error.get("type") or "error"), str(error.get("message") or answer))
    return answer.get("result")


def devices(timeout: float = 1.5, clean: bool = True) -> list[dict[str, Any]]:
    """Every running Engine: its registry entry merged with its ``hello`` answer. Registry
    files of devices that no longer answer are removed (``clean``)."""
    found = []
    folder = engine_dir()
    try:
        paths = sorted(folder.glob("*.json"))
    except OSError:
        return []
    for path in paths:
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(entry, dict) or not _REGISTRY_KEYS <= set(entry):
            continue
        try:
            hello = request(entry, "hello", timeout=timeout)
        except EngineError as exc:
            if clean and exc.type == "connection":
                try:
                    path.unlink()
                except OSError:
                    pass
            continue
        found.append({**entry, **(hello if isinstance(hello, dict) else {})})
    found.sort(key=lambda d: (d.get("role") != "master", str(d.get("track") or "")))
    return found


def choose(found: list[dict[str, Any]], track: str | None = None) -> dict[str, Any]:
    """The device on ``track`` (name, case-insensitive; "master"), default the master's."""
    if not found:
        raise EngineError("not_found", "no LiveBridge Engine is running")
    if track is None or track.strip().lower() in ("master", "main"):
        masters = [d for d in found if d.get("role") == "master"]
        if masters:
            return masters[0]
        if track is not None:
            raise EngineError("not_found", "no Engine on the master track — running on: "
                              + ", ".join(_label(d) for d in found))
        return found[0]
    wanted = track.strip().lower()
    for device in found:
        if str(device.get("track") or "").strip().lower() == wanted:
            return device
    raise EngineError("not_found", f"no Engine on track {track!r} — running on: "
                      + ", ".join(_label(d) for d in found))


def _label(device: dict[str, Any]) -> str:
    return "master" if device.get("role") == "master" else repr(device.get("track") or "?")


# ----------------------------------------------------------------------------- metering

def _db(power: float, floor: float = -120.0) -> float:
    return round(max(10.0 * math.log10(power), floor), 1) if power > 0 else floor


def _lufs(power: float) -> float | None:
    return round(-0.691 + 10.0 * math.log10(power), 1) if power > _SILENT else None


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def summarize(frames: list[list[float]], fields: list[str] | tuple[str, ...] = FIELDS,
              kind: str = "mix", timeline: bool = False) -> dict[str, Any]:
    """Meter readings over ``frames`` (``[timestamp_ms, *values]`` in ``fields`` order)."""
    if not frames:
        return {"frames": 0, "seconds": 0.0, "silent": True,
                "flags": ["No audio frames arrived — is the device switched on and Live's "
                          "audio engine running?"]}
    index = {name: i + 1 for i, name in enumerate(fields)}

    def column(name: str) -> list[float]:
        at = index[name]
        return [float(frame[at]) for frame in frames]

    seconds = round((frames[-1][0] - frames[0][0]) / 1000.0, 2) if len(frames) > 1 else 0.0
    step = seconds / (len(frames) - 1) if len(frames) > 1 else 0.1
    ms_left, ms_right = _mean(column("ms_left")), _mean(column("ms_right"))
    peak = max(max(column("peak_left")), max(column("peak_right")))
    k = column("ms_k")
    short = k[-max(1, int(round(_SHORT_TERM_S / step))):] if step > 0 else k[-30:]
    audible = [v for v in k if (_lufs(v) or -999) > -70.0]
    loudness = {
        "momentary_lufs": _lufs(k[-1]),
        "short_term_lufs": _lufs(_mean(short)),
        "lufs": _lufs(_mean(audible)) if audible else None,
        "max_momentary_lufs": _lufs(max(k)),
        "peak_db": _db(peak * peak),
        "rms_db": _db((ms_left + ms_right) / 2),
        "rms_left_db": _db(ms_left), "rms_right_db": _db(ms_right),
    }
    loudness["true_peak_db"] = loudness["peak_db"]      # sample peak: what the flags compare
    if peak > 0 and ms_left + ms_right > 0:
        loudness["crest_db"] = round(loudness["peak_db"] - loudness["rms_db"], 1)
    band_power = {name: _mean(column("band_" + name)) for name, _low, _high, _target in BANDS}
    total = sum(band_power.values())
    spectrum = {"bands_db": {name: (_db(power / total) if total > _SILENT else -90.0)
                             for name, power in band_power.items()}}
    mid, side, lr = _mean(column("ms_mid")), _mean(column("ms_side")), _mean(column("mean_lr"))
    mid_low, side_low = _mean(column("ms_mid_low")), _mean(column("ms_side_low"))
    stereo: dict[str, Any] = {}
    if ms_left > _SILENT and ms_right > _SILENT:
        stereo["correlation"] = round(max(-1.0, min(1.0, lr / math.sqrt(ms_left * ms_right))), 2)
    if mid + side > _SILENT:
        stereo["width"] = round(side / (mid + side), 2)
    if mid_low + side_low > _SILENT:
        stereo["low_end_width"] = round(side_low / (mid_low + side_low), 2)
    silent = peak * peak < _SILENT
    result: dict[str, Any] = {"frames": len(frames), "seconds": seconds, "silent": silent,
                              "loudness": loudness, "spectrum": spectrum, "stereo": stereo}
    if silent:
        result["flags"] = ["Silence: nothing played through the device in this window."]
    else:
        result["flags"] = _flags({"loudness": dict(loudness, lufs=loudness["lufs"]),
                                  "spectrum": spectrum, "stereo": stereo}, kind)
    if timeline:
        per_second = max(1, int(round(1.0 / step))) if step > 0 else 10
        start = frames[0][0]
        entries = []
        for i in range(0, len(frames), per_second):
            chunk = frames[i:i + per_second]
            chunk_peak = max(max(float(f[index["peak_left"]]), float(f[index["peak_right"]]))
                             for f in chunk)
            entries.append({"t": round((chunk[0][0] - start) / 1000.0, 1),
                            "momentary_lufs": _lufs(float(chunk[-1][index["ms_k"]])),
                            "peak_db": _db(chunk_peak * chunk_peak)})
        result["timeline"] = entries
    return result


def wait_for(predicate, timeout: float, interval: float = 0.05) -> bool:
    """Poll ``predicate`` until it is true or ``timeout`` passes (tests and tools)."""
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()
