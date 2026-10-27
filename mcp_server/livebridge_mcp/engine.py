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
import re
import socket
import threading
import time
from pathlib import Path
from typing import Any

from .audio_analysis import BANDS, _flags
from .config import config_dir

__all__ = ["EngineError", "capture", "capture_dir", "devices", "engine_dir", "listen",
           "lom_to_max", "parse_rate", "request", "select", "summarize"]

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


def capture_dir() -> Path:
    """Where the devices write captures (``LIVEBRIDGE_CAPTURE_DIR`` moves it; engine.js reads
    the same variable)."""
    override = os.environ.get("LIVEBRIDGE_CAPTURE_DIR", "").strip()
    return Path(override).expanduser() if override else config_dir() / "captures"


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


def devices(timeout: float = 1.5, clean: bool = True,
            kind: str | None = "engine") -> list[dict[str, Any]]:
    """Every running device of ``kind`` ("engine" = LiveBridge Engine, "midi" = LiveBridge
    MIDI, None = both): its registry entry merged with its ``hello`` answer. Registry files of
    devices that no longer answer are removed (``clean``)."""
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
        if kind is not None and str(entry.get("kind") or "engine") != kind:
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


def choose(found: list[dict[str, Any]], track: str | None = None,
           what: str = "LiveBridge Engine") -> dict[str, Any]:
    """The device on ``track`` (name, case-insensitive; "master"), default the master's."""
    if not found:
        raise EngineError("not_found", f"no {what} is running")
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


def select(found: list[dict[str, Any]], tracks: Any = None,
           master: bool = True) -> list[dict[str, Any]]:
    """Several devices: ``tracks`` None / "all" = every one (without the master's when
    ``master`` is false), else a list of track names ("master" for the master's)."""
    if not found:
        raise EngineError("not_found", "no LiveBridge Engine is running")
    if tracks is None or tracks == "all":
        chosen = [d for d in found if master or d.get("role") != "master"]
        if not chosen:
            raise EngineError("not_found", "only the master has an Engine — put one at the end "
                              "of each track to compare")
        return chosen
    names = [tracks] if isinstance(tracks, str) else list(tracks)
    chosen = []
    for name in names:
        device = choose(found, str(name))
        if device not in chosen:
            chosen.append(device)
    return chosen


def each(chosen: list[dict[str, Any]], call) -> list[Any]:
    """``call(device)`` for every device at the same time; an EngineError becomes the
    result for that device."""
    results: list[Any] = [None] * len(chosen)

    def run(i: int, device: dict[str, Any]) -> None:
        try:
            results[i] = call(device)
        except EngineError as exc:
            results[i] = exc

    threads = [threading.Thread(target=run, args=(i, d), daemon=True)
               for i, d in enumerate(chosen)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


_KEEP_CAPTURES_S = 7 * 24 * 3600


def _file_label(device: dict[str, Any]) -> str:
    text = "master" if device.get("role") == "master" else str(device.get("track") or "track")
    return re.sub(r"[^A-Za-z0-9_-]+", "-", text).strip("-")[:40] or "track"


def prune_captures(now: float | None = None) -> None:
    """Delete captures older than a week (only files this module named)."""
    now = time.time() if now is None else now
    try:
        for path in capture_dir().glob("lb-*.wav"):
            try:
                if now - path.stat().st_mtime > _KEEP_CAPTURES_S:
                    path.unlink()
            except OSError:
                pass
    except OSError:
        pass


def capture(chosen: list[dict[str, Any]], seconds: float,
            lead_ms: int = 400) -> list[dict[str, Any] | EngineError]:
    """Record ``seconds`` on every device in ``chosen``, all starting at the same moment
    (``lead_ms`` from now). Returns each device's answer ({file_path, seconds, role, track,
    started}) or its EngineError."""
    prune_captures()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    at = int(time.time() * 1000) + lead_ms
    names: set[str] = set()

    def name_for(device: dict[str, Any]) -> str:
        base = f"lb-{stamp}-{_file_label(device)}"
        name, n = base + ".wav", 2
        while name in names or (capture_dir() / name).exists():
            name, n = f"{base}-{n}.wav", n + 1
        names.add(name)
        return name

    plan = [(device, name_for(device)) for device in chosen]
    by_device = {id(device): name for device, name in plan}
    return each(chosen, lambda device: request(
        device, "capture", {"seconds": seconds, "name": by_device[id(device)], "at": at},
        timeout=seconds + lead_ms / 1000.0 + 10.0))


# ----------------------------------------------------------------------------- modulation

SHAPES = ("sine", "triangle", "saw", "ramp_down", "square", "random", "smooth_random", "steps")
_LOM_PART = re.compile(r"^([a-z_]+)(?:\[(\d+)\])?$")
_RATE = re.compile(r"^\s*(?:(\d+(?:\.\d+)?)\s*(bars?|beats?)|(\d+)\s*/\s*(\d+)\s*([dt])?"
                   r"|(\d+(?:\.\d+)?)\s*hz)\s*$", re.IGNORECASE)


def lom_to_max(path: str) -> str:
    """LiveBridge's LOM path (``song.tracks[0].devices[1].parameters[3]``) as Max's LiveAPI
    path (``live_set tracks 0 devices 1 parameters 3``)."""
    parts = str(path).strip().split(".")
    if not parts or parts[0] != "song" or len(parts) < 2:
        raise ValueError(f"not a Live object path: {path!r}")
    words = ["live_set"]
    for part in parts[1:]:
        match = _LOM_PART.match(part)
        if not match:
            raise ValueError(f"not a Live object path: {path!r}")
        words.append(match.group(1))
        if match.group(2) is not None:
            words.append(match.group(2))
    return " ".join(words)


def parse_rate(rate: Any, beats_per_bar: float = 4.0) -> dict[str, float]:
    """``"1 bar"``, ``"2 bars"``, ``"3 beats"``, ``"1/4"``, ``"1/8d"`` (dotted), ``"1/16t"``
    (triplet) or ``"0.5 Hz"`` -> ``{"beats": n}`` or ``{"hz": f}``. A plain number is in
    beats."""
    if isinstance(rate, bool):
        raise ValueError("rate: give e.g. '1 bar', '1/4', '1/8d', '1/16t' or '0.5 Hz'")
    if isinstance(rate, (int, float)):
        return {"beats": float(rate)}
    match = _RATE.match(str(rate))
    if not match:
        raise ValueError(f"rate {rate!r}: give e.g. '1 bar', '2 bars', '1/4', '1/8d', '1/16t' "
                         "or '0.5 Hz'")
    count, unit, num, den, mod, hz = match.groups()
    if hz is not None:
        return {"hz": float(hz)}
    if count is not None:
        value = float(count)
        return {"beats": value * (beats_per_bar if unit.lower().startswith("bar") else 1.0)}
    if int(den) == 0:
        raise ValueError(f"rate {rate!r}: division by zero")
    beats = 4.0 * int(num) / int(den)            # a whole note is 4 beats
    if mod and mod.lower() == "d":
        beats *= 1.5
    elif mod and mod.lower() == "t":
        beats *= 2.0 / 3.0
    return {"beats": beats}


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


def listen(device: dict[str, Any], seconds: float, kind: str = "auto",
           history: bool = False) -> dict[str, Any]:
    """One device's reading over ``seconds`` (:func:`summarize` plus ``device``)."""
    answer = request(device, "frames", {"seconds": seconds, "wait": not history},
                     timeout=seconds + 10.0)
    if not isinstance(answer, dict):
        raise EngineError("protocol", "the device sent an unexpected answer")
    role = answer.get("role") or device.get("role")
    chosen = kind if kind != "auto" else ("mix" if role == "master" else "stem")
    result = summarize(answer.get("frames") or [], answer.get("fields") or FIELDS, kind=chosen)
    return {"device": {"role": role, "track": answer.get("track") or device.get("track")},
            **result}


def wait_for(predicate, timeout: float, interval: float = 0.05) -> bool:
    """Poll ``predicate`` until it is true or ``timeout`` passes (tests and tools)."""
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()
