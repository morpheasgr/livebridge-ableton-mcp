"""Audio file analysis for ``live_audio_analyze`` — the closest thing Claude has to ears.

Measures what a producer listens for: loudness (LUFS, peaks, dynamics, clipping), spectral
balance per band (sub ... air) with mix flags, stereo width / mono compatibility, tempo, key and
an energy curve over time (where the drops and breakdowns are). Optionally compares against a
reference track.

Needs the optional ``audio`` extra (``pip install "livebridge-mcp[audio]"``: numpy, scipy,
soundfile, pyloudnorm) — deliberately no librosa/numba, so it stays light on an 8 GB laptop and
installs the same way on Windows and macOS. Imports are lazy: the MCP server starts without them
and the tool answers with the install command.
"""

from __future__ import annotations

import math
import os
from typing import Any

from .theory import NOTE_NAMES, detect_key

__all__ = ["MissingDependency", "analyze_file", "compare", "compare_versions", "masking",
           "INSTALL_HINT"]

INSTALL_HINT = ('pip install "livebridge-mcp[audio]"  (or: pip install numpy scipy soundfile '
                'pyloudnorm) into the Python environment that runs the LiveBridge MCP server')

#: Longest stretch analysed in one go (seconds) — bounds memory (stereo float32 ~ 21 MB/min).
MAX_SECONDS = 600.0

#: Spectral bands (Hz) and what a balanced full mix roughly carries in each, in dB relative to
#: the total (pink-ish tilt, club/pop masters). Used for flags only — a single stem or a sparse
#: intro legitimately deviates.
BANDS: tuple[tuple[str, float, float, float], ...] = (
    ("sub", 20, 60, -9.0), ("bass", 60, 120, -6.5), ("low_mid", 120, 250, -8.5),
    ("mud", 250, 500, -11.0), ("mid", 500, 2000, -11.5), ("presence", 2000, 5000, -17.0),
    ("high", 5000, 10000, -21.0), ("air", 10000, 20000, -27.0),
)
_ANALYSIS_RATE = 22050


class MissingDependency(RuntimeError):
    """An optional audio library is not installed."""


def _deps() -> tuple[Any, Any, Any, Any]:
    try:
        import numpy as np
        import pyloudnorm
        import scipy.signal as signal
        import soundfile as sf
    except ImportError as exc:
        raise MissingDependency(f"audio analysis needs {exc.name}: {INSTALL_HINT}") from exc
    return np, signal, sf, pyloudnorm


def _db(value: float, floor: float = -120.0) -> float:
    return round(max(20.0 * math.log10(value), floor), 1) if value > 0 else floor


def _load(path: str, start: float, duration: float | None) -> tuple[Any, int, dict[str, Any]]:
    np, _signal, sf, _pyln = _deps()
    info = sf.info(path)
    rate = info.samplerate
    total = info.frames / float(rate) if rate else 0.0
    first = int(max(start, 0.0) * rate)
    wanted = min(duration or MAX_SECONDS, MAX_SECONDS)
    frames = max(min(int(wanted * rate), info.frames - first), 0)
    data = sf.read(path, start=first, frames=frames, dtype="float32", always_2d=True)[0]
    meta = {"path": path, "name": os.path.basename(path), "duration": round(total, 2),
            "sample_rate": rate, "channels": info.channels, "format": info.format,
            "subtype": info.subtype}
    if first or frames < info.frames - first:
        meta["analysed"] = [round(first / rate, 2), round((first + frames) / rate, 2)]
    return data, rate, meta


def _loudness(data: Any, rate: int) -> dict[str, Any]:
    np, signal, _sf, pyln = _deps()
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    rms = float(np.sqrt(np.mean(np.square(data, dtype=np.float64)))) if data.size else 0.0
    result: dict[str, Any] = {"peak_db": _db(peak), "rms_db": _db(rms)}
    if peak > 0:
        # true peak: 4x oversample one second around the loudest sample
        index = int(np.argmax(np.max(np.abs(data), axis=1)))
        lo, hi = max(index - rate // 2, 0), min(index + rate // 2, len(data))
        over = signal.resample_poly(data[lo:hi], 4, 1, axis=0)
        result["true_peak_db"] = _db(max(float(np.max(np.abs(over))), peak))
        result["crest_db"] = round(result["peak_db"] - result["rms_db"], 1)
    clipped = int(np.sum(np.abs(data) >= 0.999))
    if clipped:
        result["clipped_samples"] = clipped
    seconds = len(data) / float(rate)
    if seconds >= 0.5 and rms > 0:
        meter = pyln.Meter(rate)
        result["lufs"] = round(float(meter.integrated_loudness(data)), 1)
        if seconds >= 9.0:
            # short-term (3 s) loudness spread = how much the track breathes
            window, hop = 3 * rate, rate
            short = [float(meter.integrated_loudness(data[i:i + window]))
                     for i in range(0, len(data) - window + 1, hop)]
            short = [v for v in short if v > -70.0]
            if len(short) >= 4:
                low, high = np.percentile(short, [10, 95])
                result["loudness_range_lu"] = round(float(high - low), 1)
                result["short_term_max_lufs"] = round(max(short), 1)
    return result


def _spectrum(mono: Any, rate: int) -> dict[str, Any]:
    np, signal, _sf, _pyln = _deps()
    nper = min(8192, len(mono))
    if nper < 256:
        return {}
    freqs, psd = signal.welch(mono, fs=rate, nperseg=nper)
    total = float(np.sum(psd[(freqs >= 20) & (freqs <= 20000)]))
    if total <= 0:
        return {}
    bands = {}
    for name, low, high, _target in BANDS:
        if low >= rate / 2:
            continue
        part = float(np.sum(psd[(freqs >= low) & (freqs < high)]))
        bands[name] = round(10.0 * math.log10(part / total), 1) if part > 0 else -90.0
    audible = (freqs >= 20) & (freqs <= 20000)
    centroid = float(np.sum(freqs[audible] * psd[audible]) / total)
    return {"bands_db": bands, "centroid_hz": round(centroid)}


def _stereo(data: Any, rate: int) -> dict[str, Any]:
    np, signal, _sf, _pyln = _deps()
    if data.shape[1] < 2:
        return {"mono": True}
    left, right = data[:, 0].astype(np.float64), data[:, 1].astype(np.float64)
    mid, side = (left + right) / 2.0, (left - right) / 2.0
    mid_power, side_power = float(np.mean(mid ** 2)), float(np.mean(side ** 2))
    denom = math.sqrt(float(np.mean(left ** 2)) * float(np.mean(right ** 2)))
    result: dict[str, Any] = {
        "correlation": round(float(np.mean(left * right)) / denom, 2) if denom else 1.0,
        "width": round(side_power / (mid_power + side_power), 2)
        if mid_power + side_power > 0 else 0.0,
    }
    if len(mid) > 4096:
        sos = signal.butter(4, 120.0, btype="low", fs=rate, output="sos")
        low_mid = float(np.mean(signal.sosfilt(sos, mid) ** 2))
        low_side = float(np.mean(signal.sosfilt(sos, side) ** 2))
        if low_mid + low_side > 0:
            result["low_end_width"] = round(low_side / (low_mid + low_side), 2)
    return result


def _stft(mono: Any, rate: int, nperseg: int, hop: int) -> tuple[Any, Any]:
    np, signal, _sf, _pyln = _deps()
    freqs, _times, spec = signal.stft(mono, fs=rate, nperseg=nperseg, noverlap=nperseg - hop,
                                      boundary=None, padded=False)
    return freqs, np.abs(spec)


def _tempo(magnitudes: Any, rate: int, hop: int) -> dict[str, Any]:
    """Tempo from the spectral-flux onset envelope (autocorrelation, prior around 120 BPM)."""
    np, _signal, _sf, _pyln = _deps()
    if magnitudes.shape[1] < 64:
        return {}
    logmag = np.log1p(100.0 * magnitudes)
    flux = np.sum(np.maximum(np.diff(logmag, axis=1), 0.0), axis=0)
    flux = flux - np.mean(flux)
    if not np.any(flux):
        return {}
    frame_rate = rate / float(hop)
    corr = np.correlate(flux, flux, mode="full")[len(flux) - 1:]
    corr = corr / (corr[0] or 1.0)
    lags = np.arange(1, len(corr))
    bpms = 60.0 * frame_rate / lags
    valid = (bpms >= 60) & (bpms <= 200)
    if not np.any(valid):
        return {}
    prior = np.exp(-0.5 * (np.log2(bpms / 120.0) / 0.9) ** 2)
    score = corr[1:] * prior
    best = int(np.argmax(np.where(valid, score, -np.inf)))
    # parabolic refinement of the autocorrelation peak
    lag = float(lags[best])
    if 0 < best < len(score) - 1:
        a, b, c = corr[best], corr[best + 1], corr[best + 2]
        shift = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) else 0.0
        lag += max(min(shift, 0.5), -0.5)
    bpm = float(60.0 * frame_rate / lag)
    strength = float(corr[best + 1])
    result = {"bpm": round(bpm, 1), "confidence": round(max(0.0, min(1.0, strength * 2.5)), 2)}
    alternatives = [round(v, 1) for v in (bpm / 2.0, bpm * 2.0) if 50 <= v <= 220]
    if alternatives:
        result["also_possible"] = alternatives
    return result


def _key(freqs: Any, magnitudes: Any) -> dict[str, Any]:
    np, _signal, _sf, _pyln = _deps()
    usable = (freqs >= 55.0) & (freqs <= 2000.0)
    if not np.any(usable):
        return {}
    power = np.mean(magnitudes[usable] ** 2, axis=1)
    midi = 69.0 + 12.0 * np.log2(freqs[usable] / 440.0)
    classes = np.mod(np.rint(midi).astype(int), 12)
    chroma = [float(np.sum(power[classes == pc])) for pc in range(12)]
    low = (freqs >= 40.0) & (freqs <= 160.0)
    bass = None
    if np.any(low):
        low_power = np.mean(magnitudes[low] ** 2, axis=1)
        low_classes = np.mod(np.rint(69.0 + 12.0 * np.log2(freqs[low] / 440.0)).astype(int), 12)
        bass = [float(np.sum(low_power[low_classes == pc])) for pc in range(12)]
    ranked = detect_key(chroma, bass)
    if not ranked:
        return {}
    top = ranked[0]
    margin = top["score"] - ranked[1]["score"]
    return {"name": f"{NOTE_NAMES[top['tonic']]} {top['mode']}",
            "confidence": round(max(0.0, min(1.0, 0.4 * top["score"] + 3.0 * margin)), 2),
            "candidates": [f"{NOTE_NAMES[c['tonic']]} {c['mode']}" for c in ranked[:3]]}


def _energy_curve(mono: Any, rate: int, offset: float, points: int = 32) -> dict[str, Any]:
    """RMS over time in ``points`` equal steps (dB) — shows intro / build / drop / breakdown."""
    np, _signal, _sf, _pyln = _deps()
    seconds = len(mono) / float(rate)
    if seconds < 8.0:
        return {}
    count = int(min(points, seconds // 2))
    step = len(mono) // count
    values = [_db(float(np.sqrt(np.mean(mono[i * step:(i + 1) * step] ** 2)))) for i in range(count)]
    return {"step_s": round(step / float(rate), 2), "start_s": round(offset, 2),
            "rms_db": values}


def _flags(result: dict[str, Any], kind: str) -> list[str]:
    """Plain-language mix observations from the measurements."""
    flags: list[str] = []
    loud = result.get("loudness", {})
    lufs = loud.get("lufs")
    if loud.get("clipped_samples"):
        flags.append(f"{loud['clipped_samples']} samples at full scale — the file clips: lower "
                     "the level before the limiter / the master.")
    elif loud.get("true_peak_db", -99) > -0.3:
        flags.append(f"True peak {loud['true_peak_db']} dBTP leaves no headroom (aim for -1 dBTP "
                     "on a master, around -6 dB on a mix that still gets mastered).")
    if kind == "mix":
        if lufs is not None and lufs > -7.0:
            flags.append(f"{lufs} LUFS is very loud — expect pumping/distortion; club masters "
                         "sit around -8 to -6, streaming around -14 to -9.")
        elif lufs is not None and lufs < -18.0:
            flags.append(f"{lufs} LUFS is quiet for a finished track (fine for an unmastered mix "
                         "with headroom).")
        if loud.get("crest_db") is not None and loud["crest_db"] < 7.0:
            flags.append(f"Crest factor {loud['crest_db']} dB: heavily limited — transients "
                         "(kick, snare) lose punch below ~8 dB.")
        bands = result.get("spectrum", {}).get("bands_db", {})
        targets = {name: target for name, _l, _h, target in BANDS}
        notes = {
            ("mud", 1): "a build-up around 250-500 Hz (muddy/boxy): cut pads, chords and reverb "
                        "returns there, high-pass what is not kick or bass",
            ("low_mid", 1): "a lot of 120-250 Hz (boomy): bass and kick tail overlap or the "
                            "chords sit too low",
            ("sub", 1): "very heavy sub (20-60 Hz): check the kick/808 level and high-pass "
                        "everything else at 30 Hz+",
            ("sub", -1): "little sub energy below 60 Hz: the kick/bass lacks weight (or the "
                         "track is not meant to have sub)",
            ("bass", -1): "thin 60-120 Hz: the bass/kick body is weak",
            ("presence", 1): "a lot of 2-5 kHz (harsh/fatiguing): tame leads, vocals or hats",
            ("presence", -1): "little 2-5 kHz: the mix sounds distant — leads/vocals lack "
                              "presence",
            ("high", -1): "dull top (5-10 kHz): hats/air missing or too much low-pass",
            ("air", 1): "very bright above 10 kHz: hissy hats or excited highs",
        }
        for (name, sign), text in notes.items():
            if name in bands and (bands[name] - targets[name]) * sign > 5.0:
                flags.append(f"Spectrum: {text} ({name} {bands[name]} dB vs ~{targets[name]}).")
    stereo = result.get("stereo", {})
    if stereo.get("correlation", 1.0) < 0.1:
        flags.append(f"Stereo correlation {stereo['correlation']}: parts cancel in mono (club "
                     "systems, phones) — reduce widening or fix phase.")
    if stereo.get("low_end_width", 0.0) > 0.15:
        flags.append("The low end (<120 Hz) is wide: keep kick and bass mono (Utility -> Bass "
                     "Mono) for a solid centre.")
    return flags


#: the most leading silence counted as a file's gap before the music (a master's few hundred
#: milliseconds), and the level below which a sample is silence
LEAD_SILENCE_MAX_S = 2.0
_SILENCE_LEVEL = 10.0 ** (-60.0 / 20.0)


def lead_silence(path: str) -> float:
    """Seconds of silence before the first sound (at most :data:`LEAD_SILENCE_MAX_S`): some
    masters start with a short gap, which shifts every bar-based window."""
    np, _signal, sf, _pyln = _deps()
    info = sf.info(path)
    frames = min(int(LEAD_SILENCE_MAX_S * info.samplerate), info.frames)
    head = sf.read(path, start=0, frames=frames, dtype="float32", always_2d=True)[0]
    loud = np.nonzero(np.max(np.abs(head), axis=1) > _SILENCE_LEVEL)[0] if len(head) else []
    if not len(loud):
        return 0.0                  # silent for 2 s: a quiet intro, not a gap — no shift
    return round(int(loud[0]) / float(info.samplerate), 3)


def analyze_file(path: str, start: float = 0.0, duration: float | None = None,
                 kind: str = "auto", sections: bool = True,
                 from_sound: bool = False) -> dict[str, Any]:
    """Measure one audio file.

    Args:
        path: a file soundfile can read (WAV, AIFF, FLAC, OGG, MP3).
        start, duration: analyse a window in seconds (default: the first 10 minutes).
        kind: "mix" (full track: mix flags apply), "stem"/"loop"/"one_shot" (no spectral
            targets) or "auto" (one_shot below 2 s, loop below 20 s, else mix).
        sections: include the energy curve over time.
        from_sound: count ``start`` from the first sound, not from the file's first sample
            (a master's silent gap before the music, up to 2 s: ``file.lead_silence_s``).

    Raises:
        MissingDependency: the audio extra is not installed.
        OSError / RuntimeError: the file cannot be read.
    """
    np, signal, _sf, _pyln = _deps()
    gap = lead_silence(path) if from_sound else 0.0
    data, rate, meta = _load(path, start + gap, duration)
    if gap:
        meta["lead_silence_s"] = gap
    if not len(data):
        raise ValueError("the file (or the chosen window) contains no audio")
    seconds = len(data) / float(rate)
    if kind == "auto":
        kind = "one_shot" if seconds < 2.0 else "loop" if seconds < 20.0 else "mix"
    result: dict[str, Any] = {"file": meta, "kind": kind}
    mono = np.mean(data, axis=1, dtype=np.float64)
    if not np.any(mono) and not np.any(data):
        result["silent"] = True
        result["flags"] = ["The file is digital silence."]
        return result
    result["loudness"] = _loudness(data, rate)
    result["spectrum"] = _spectrum(mono, rate)
    result["stereo"] = _stereo(data, rate)
    if seconds >= 2.0:
        factor = math.gcd(rate, _ANALYSIS_RATE)
        small = signal.resample_poly(mono, _ANALYSIS_RATE // factor, rate // factor) \
            if rate != _ANALYSIS_RATE else mono
        if seconds >= 4.0:
            _freqs, mags = _stft(small, _ANALYSIS_RATE, 1024, 256)
            tempo = _tempo(mags[:, :int(90 * _ANALYSIS_RATE / 256)], _ANALYSIS_RATE, 256)
            if tempo:
                result["tempo"] = tempo
        freqs, mags = _stft(small, _ANALYSIS_RATE, 8192, 2048)
        key = _key(freqs, mags)
        if key:
            result["key"] = key
    if sections:
        curve = _energy_curve(mono, rate, max(start, 0.0))
        if curve:
            result["energy"] = curve
    result["flags"] = _flags(result, kind)
    return result


def compare(mix: dict[str, Any], reference: dict[str, Any]) -> dict[str, Any]:
    """Differences mix - reference (loudness, bands, width) with suggestions."""
    diff: dict[str, Any] = {}
    notes: list[str] = []
    for field in ("lufs", "crest_db", "loudness_range_lu"):
        a, b = mix.get("loudness", {}).get(field), reference.get("loudness", {}).get(field)
        if a is not None and b is not None:
            diff[field] = round(a - b, 1)
    bands_a = mix.get("spectrum", {}).get("bands_db", {})
    bands_b = reference.get("spectrum", {}).get("bands_db", {})
    ranges = {name: (low, high) for name, low, high, _t in BANDS}
    band_diff = {name: round(bands_a[name] - bands_b[name], 1)
                 for name in bands_a if name in bands_b}
    if band_diff:
        diff["bands_db"] = band_diff
        for name, delta in sorted(band_diff.items(), key=lambda item: -abs(item[1])):
            if abs(delta) >= 3.0:
                low, high = ranges[name]
                notes.append(f"{name} ({int(low)}-{int(high)} Hz) is {abs(delta)} dB "
                             f"{'louder' if delta > 0 else 'quieter'} than the reference.")
    a, b = mix.get("stereo", {}).get("width"), reference.get("stereo", {}).get("width")
    if a is not None and b is not None:
        diff["width"] = round(a - b, 2)
        if abs(a - b) >= 0.1:
            notes.append(f"The mix is {'wider' if a > b else 'narrower'} than the reference "
                         f"(width {a} vs {b}).")
    if "lufs" in diff and abs(diff["lufs"]) >= 2.0:
        notes.append(f"The mix is {abs(diff['lufs'])} LU "
                     f"{'louder' if diff['lufs'] > 0 else 'quieter'} than the reference — "
                     "level-match before judging the tone (band values are already relative).")
    diff["notes"] = notes
    return diff


# ----------------------------------------------------------------------------- masking

#: Third-octave band centres (Hz) used for masking: 31.5 Hz .. 16 kHz.
MASKING_CENTRES: tuple[float, ...] = (
    31.5, 40, 50, 63, 80, 100, 125, 160, 200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600,
    2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000, 12500, 16000)
_MASK_RATE = 44100
_MASK_HOP_S = 0.05
_MASK_CHUNK = 400       # STFT frames per chunk (~20 s): bounds memory on long files
_ACTIVE_DB = 20.0       # a band is "active" in a frame within this many dB of its own peak
_CLOSE_DB = 8.0         # two parts mask when both are active and this close in level
_FLOOR_DB = -70.0       # quieter than this (dBFS per band) never counts


def _band_matrix(path: str, start: float, duration: float | None) -> tuple[Any, float]:
    """(bands x frames) level matrix in dB and the file's length in seconds."""
    np, signal, _sf, _pyln = _deps()
    data, rate, meta = _load(path, start, duration)
    mono = np.mean(data, axis=1, dtype=np.float32)
    del data
    if rate != _MASK_RATE:
        factor = math.gcd(rate, _MASK_RATE)
        mono = signal.resample_poly(mono, _MASK_RATE // factor, rate // factor)
    nper = 8192
    hop = int(_MASK_HOP_S * _MASK_RATE)
    if len(mono) < nper:
        mono = np.pad(mono, (0, nper - len(mono)))
    # STFT in chunks of _MASK_CHUNK frames: one transform of 10 minutes would need ~1 GB
    freqs = np.fft.rfftfreq(nper, 1.0 / _MASK_RATE)
    selectors = [(freqs >= c / 2 ** (1 / 6)) & (freqs < c * 2 ** (1 / 6))
                 for c in MASKING_CENTRES]
    total_frames = (len(mono) - nper) // hop + 1
    columns = []
    for first in range(0, total_frames, _MASK_CHUNK):
        count = min(_MASK_CHUNK, total_frames - first)
        piece = mono[first * hop:first * hop + nper + (count - 1) * hop]
        _f, _t, spec = signal.stft(piece, fs=_MASK_RATE, nperseg=nper, noverlap=nper - hop,
                                   boundary=None, padded=False)
        power = np.abs(spec) ** 2
        columns.append(np.array([np.sum(power[sel], axis=0) for sel in selectors]))
    # scipy scales the STFT by the window sum: a sine of amplitude A reads about A/2
    levels = 10.0 * np.log10(np.maximum(np.concatenate(columns, axis=1), 1e-20))
    return levels, meta["duration"]


def _energy_share(levels: Any) -> Any:
    """Each band's share (0..1) of a part's total energy over time."""
    np, _signal, _sf, _pyln = _deps()
    power = np.mean(10.0 ** (levels / 10.0), axis=1)
    return power / max(float(np.sum(power)), 1e-20)


def _band_label(centre: float) -> str:
    return f"{centre / 1000:g} kHz" if centre >= 1000 else f"{centre:g} Hz"


def _region(centre: float) -> str:
    if centre < 63:
        return "sub"
    if centre < 160:
        return "bass"
    if centre < 315:
        return "low-mid"
    if centre < 630:
        return "mud"
    if centre < 2500:
        return "mid"
    if centre < 6300:
        return "presence"
    return "air"


def _masking_advice(region: str, louder: str, quieter: str, centre: float) -> str:
    where = _band_label(centre)
    if region in ("sub", "bass"):
        return (f"{louder} and {quieter} both own the low end around {where}: decide who owns "
                f"the sub — side-chain {quieter} to {louder} (or the other way round), or "
                f"high-pass the one that should not carry the sub")
    if region in ("low-mid", "mud"):
        return (f"cut {quieter} 2-4 dB with a wide bell around {where} (or high-pass it "
                f"higher); if {quieter} is the important part, cut {louder} instead")
    return (f"carve a pocket: a 2-3 dB bell cut around {where} on whichever part is less "
            f"important here, or pan/arrange them apart (play at different moments)")


def masking(sources: list[dict[str, Any]], start: float = 0.0,
            duration: float | None = None, top: int = 3) -> dict[str, Any]:
    """Where parts fight for the same frequencies at the same time.

    Args:
        sources: [{"name": label, "path": audio file, "gain_db"?}] — 2 to 8 bounces of the
            SAME time range (e.g. `live_record_resample` of each track over the same bars),
            aligned at 0 s. ``gain_db`` is added to a part's levels (a pre-fader capture
            heard at its fader level).
        start, duration: window in seconds.
        top: bands reported per pair.

    Returns:
        {"pairs": [{"parts": [a, b], "score" (0..100), "bands": [{"band", "hz", "region",
        "overlap" (share of time both are active and close in level, 0..1), "louder", "level_db":
        {a, b}}], "advice": [...]}], "parts": [{name, duration, loudest_band}], "notes": [...]}
        sorted by score (worst first).
    """
    np, _signal, _sf, _pyln = _deps()
    mats, parts, notes = [], [], []
    for source in sources:
        levels, seconds = _band_matrix(source["path"], start, duration)
        if source.get("gain_db"):
            levels = levels + float(source["gain_db"])
        mats.append(levels)
        loudest = int(np.argmax(np.max(levels, axis=1)))
        parts.append({"name": source["name"], "duration": seconds,
                      "loudest_band": _band_label(MASKING_CENTRES[loudest])})
    frames = min(m.shape[1] for m in mats)
    if max(m.shape[1] for m in mats) - frames > int(2.0 / _MASK_HOP_S):
        notes.append("The files differ in length by more than 2 s — bounce every part over "
                     "the same bars so they line up.")
    mats = [m[:, :frames] for m in mats]
    active = []
    for levels in mats:
        peak = np.max(levels, axis=1, keepdims=True)
        active.append((levels >= peak - _ACTIVE_DB) & (levels > _FLOOR_DB))
    pairs = []
    for i in range(len(mats)):
        for j in range(i + 1, len(mats)):
            a, b = mats[i], mats[j]
            both = active[i] & active[j] & (np.abs(a - b) <= _CLOSE_DB)
            overlap = both.mean(axis=1) if frames else np.zeros(len(MASKING_CENTRES))
            # weight bands by how much they matter to each part (share of its energy there)
            weight = np.sqrt(_energy_share(a) * _energy_share(b))
            score = float(np.sum(overlap * weight) / max(float(np.sum(weight)), 1e-20))
            ranked = sorted(range(len(MASKING_CENTRES)),
                            key=lambda k: -(overlap[k] * weight[k]))
            bands, advice = [], []
            for k in ranked[:top]:
                if overlap[k] < 0.05:
                    break
                mask = both[k]
                level_a = float(np.mean(a[k][mask])) if mask.any() else float(np.mean(a[k]))
                level_b = float(np.mean(b[k][mask])) if mask.any() else float(np.mean(b[k]))
                louder, quieter = ((parts[i]["name"], parts[j]["name"]) if level_a >= level_b
                                   else (parts[j]["name"], parts[i]["name"]))
                centre = MASKING_CENTRES[k]
                region = _region(centre)
                bands.append({"band": _band_label(centre), "hz": centre, "region": region,
                              "overlap": round(float(overlap[k]), 2), "louder": louder,
                              "level_db": {parts[i]["name"]: round(level_a, 1),
                                           parts[j]["name"]: round(level_b, 1)}})
                text = _masking_advice(region, louder, quieter, centre)
                if text not in advice:
                    advice.append(text)
            pairs.append({"parts": [parts[i]["name"], parts[j]["name"]],
                          "score": round(100.0 * score), "bands": bands, "advice": advice})
    pairs.sort(key=lambda pair: -pair["score"])
    if pairs and pairs[0]["score"] < 10:
        notes.append("Little masking: the parts mostly occupy different frequencies or "
                     "different moments.")
    return {"pairs": pairs, "parts": parts, "notes": notes}


# ------------------------------------------------------------------- before / after

def compare_versions(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """What changed between two analyses of the same music (``analyze_file`` results): the
    deltas after - before, mix flags that went away or appeared, and a plain verdict per
    dimension — the "did my change help?" check."""
    changes: list[str] = []
    delta: dict[str, Any] = {}
    loud_a, loud_b = before.get("loudness", {}), after.get("loudness", {})
    for field, label, good in (("lufs", "loudness (LUFS)", None),
                               ("true_peak_db", "true peak (dBTP)", -1),
                               ("crest_db", "crest factor (dB, punch)", 1),
                               ("loudness_range_lu", "loudness range (LU)", None)):
        a, b = loud_a.get(field), loud_b.get(field)
        if a is None or b is None:
            continue
        d = round(b - a, 1)
        delta[field] = d
        if abs(d) >= (0.5 if field != "true_peak_db" else 0.3):
            verdict = ""
            if good is not None:
                verdict = " (better)" if d * good > 0 else " (worse)"
            changes.append(f"{label} {'+' if d > 0 else ''}{d}{verdict}")
    bands_a = before.get("spectrum", {}).get("bands_db", {})
    bands_b = after.get("spectrum", {}).get("bands_db", {})
    band_delta = {name: round(bands_b[name] - bands_a[name], 1)
                  for name in bands_a if name in bands_b}
    if band_delta:
        delta["bands_db"] = band_delta
        ranges = {name: (low, high) for name, low, high, _t in BANDS}
        for name, d in sorted(band_delta.items(), key=lambda item: -abs(item[1])):
            if abs(d) >= 1.5:
                low, high = ranges[name]
                changes.append(f"{name} ({int(low)}-{int(high)} Hz) {'+' if d > 0 else ''}{d} dB")
    for field, label in (("width", "stereo width"), ("low_end_width", "low-end width"),
                         ("correlation", "mono compatibility (correlation)")):
        a = before.get("stereo", {}).get(field)
        b = after.get("stereo", {}).get(field)
        if a is None or b is None:
            continue
        d = round(b - a, 2)
        delta[field] = d
        if abs(d) >= 0.05:
            verdict = ""
            if field == "low_end_width":
                verdict = " (better)" if d < 0 else " (worse)"
            elif field == "correlation":
                verdict = " (better)" if d > 0 else " (worse)"
            changes.append(f"{label} {'+' if d > 0 else ''}{d}{verdict}")
    flags_a, flags_b = set(before.get("flags") or []), set(after.get("flags") or [])
    lufs_change = delta.get("lufs")
    notes = []
    if lufs_change is not None and abs(lufs_change) >= 1.0:
        notes.append(f"The versions differ by {abs(lufs_change)} LU: louder always sounds "
                     "better — judge tone at matched loudness (band values are already "
                     "relative to each version's total).")
    return {"delta": delta, "changes": changes or ["no measurable change"],
            "resolved_flags": sorted(flags_a - flags_b), "new_flags": sorted(flags_b - flags_a),
            "notes": notes}


# ------------------------------------------------------------------- technical problems

_NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_CLIP_LEVEL = 0.999            # |sample| at or above this counts as full scale
_CLIP_RUN = 3                  # consecutive full-scale samples = a clipped stretch
_DC_LIMIT_DB = -60.0           # DC offset above this (dBFS) is worth fixing
_POLARITY_CORR = -0.5          # L/R correlation below this: one side is upside down
_HUM_PROMINENCE_DB = 12.0      # hum line above its neighbourhood
_RES_PROMINENCE_DB = 9.0       # long-term spectral peak above the smoothed spectrum
_RES_PERSISTENCE = 0.6         # share of the time a resonance must be there
_RES_RANGE = (60.0, 14000.0)
_BLOCK_S = 0.05                # noise floor: 50 ms blocks


def _note_name(freq: float) -> str:
    """Live's note name (C3 = middle C) of the nearest note, with the cents off it."""
    midi = 69.0 + 12.0 * math.log2(freq / 440.0)
    nearest = int(round(midi))
    cents = int(round((midi - nearest) * 100))
    name = f"{_NOTE_NAMES[nearest % 12]}{nearest // 12 - 2}"
    return name if abs(cents) < 10 else f"{name} {cents:+d} ct"


def _runs(mask: Any) -> list[tuple[int, int]]:
    """(start, length) of the runs of True in a 1-D boolean array."""
    np, _signal, _sf, _pyln = _deps()
    edges = np.diff(np.concatenate(([0], mask.astype(np.int8), [0])))
    starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
    return list(zip(starts.tolist(), (ends - starts).tolist()))


def _clipping(data: Any, rate: int) -> dict[str, Any] | None:
    np, _signal, _sf, _pyln = _deps()
    stretches, worst, first = 0, 0, None
    for channel in range(data.shape[1]):
        for start, length in _runs(np.abs(data[:, channel]) >= _CLIP_LEVEL):
            if length >= _CLIP_RUN:
                stretches += 1
                worst = max(worst, length)
                first = start if first is None else min(first, start)
    if not stretches:
        return None
    return {"problem": "clipping", "severity": "significant" if stretches > 10 else "moderate",
            "stretches": stretches, "longest_samples": worst,
            "first_at_s": round(first / rate, 2),
            "detail": f"{stretches} stretch(es) of full-scale samples, the longest {worst} "
                      f"samples (first at {first / rate:.2f} s)",
            "fix": "Lower the gain before the clipping stage (track volume, the device that "
                   "drives it, or the master limiter's input) and bounce again; flattened "
                   "peaks cannot be restored."}


def _dc_offset(data: Any) -> dict[str, Any] | None:
    np, _signal, _sf, _pyln = _deps()
    offsets = [float(np.mean(data[:, c], dtype=np.float64)) for c in range(data.shape[1])]
    worst = max(abs(o) for o in offsets)
    if worst <= 0 or _db(worst) <= _DC_LIMIT_DB:
        return None
    return {"problem": "dc_offset", "severity": "moderate" if _db(worst) > -40 else "minor",
            "offset_db": _db(worst), "per_channel": [round(o, 6) for o in offsets],
            "detail": f"the waveform sits off zero by {_db(worst)} dBFS (wasted headroom, "
                      "clicks at cuts)",
            "fix": "A high-pass (EQ Eight band 1 at 20-30 Hz) or Utility's DC filter on the "
                   "track removes it."}


def _polarity(data: Any) -> dict[str, Any] | None:
    np, _signal, _sf, _pyln = _deps()
    if data.shape[1] < 2:
        return None
    left, right = data[:, 0].astype(np.float64), data[:, 1].astype(np.float64)
    energy = math.sqrt(float(np.dot(left, left)) * float(np.dot(right, right)))
    if energy <= 1e-12:
        return None
    corr = float(np.dot(left, right)) / energy
    if corr >= _POLARITY_CORR:
        return None
    return {"problem": "polarity", "severity": "significant", "correlation": round(corr, 2),
            "detail": f"left and right are mostly opposite (correlation {corr:.2f}): the part "
                      "cancels in mono — on club systems and phones",
            "fix": "Flip one side: Utility's phase invert on one channel (left or right), "
                   "then check correlation again."}


def _spectrum_db(mono: Any, rate: int, nperseg: int) -> tuple[Any, Any]:
    np, signal, _sf, _pyln = _deps()
    nperseg = min(nperseg, max(256, len(mono)))
    freqs, power = signal.welch(mono, fs=rate, nperseg=nperseg, noverlap=nperseg // 2)
    return freqs, 10.0 * np.log10(np.maximum(power, 1e-20))


def _hum(mono: Any, rate: int) -> dict[str, Any] | None:
    """Mains hum: narrow lines at 50 or 60 Hz AND their harmonics, standing out of their
    neighbourhood — measured on the quietest stretches, where the music hides it least."""
    np, _signal, _sf, _pyln = _deps()
    block = int(rate * 0.5)
    if len(mono) < block * 4:
        quiet = mono
    else:
        blocks = len(mono) // block
        levels = [float(np.mean(mono[i * block:(i + 1) * block] ** 2)) for i in range(blocks)]
        order = np.argsort(levels)[:max(4, blocks // 4)]
        quiet = np.concatenate([mono[i * block:(i + 1) * block] for i in sorted(order)])
    freqs, spec = _spectrum_db(quiet, rate, 1 << 15)
    best = None
    for base in (50.0, 60.0):
        lines = []
        for harmonic in (1, 2, 3):
            f = base * harmonic
            if f >= rate / 2:
                continue
            at = (freqs > f - 1.5) & (freqs < f + 1.5)
            around = ((freqs > f - 12) & (freqs < f - 4)) | ((freqs > f + 4) & (freqs < f + 12))
            if not at.any() or not around.any():
                continue
            prominence = float(np.max(spec[at]) - np.median(spec[around]))
            lines.append((f, prominence))
        strong = [f for f, p in lines if p >= _HUM_PROMINENCE_DB]
        if len(strong) >= 2 and (best is None or len(strong) > len(best[1])):
            best = (base, strong, max(p for _f, p in lines))
    if best is None:
        return None
    base, strong, prominence = best
    return {"problem": "hum", "severity": "moderate", "frequency_hz": base,
            "harmonics_hz": strong, "prominence_db": round(prominence, 1),
            "detail": f"narrow lines at {', '.join(f'{f:g}' for f in strong)} Hz — mains hum "
                      f"({base:g} Hz power), strongest where the music is quiet",
            "fix": f"Narrow EQ Eight notches (Q 30+) at {', '.join(f'{f:g}' for f in strong)} "
                   "Hz on the source track; better, re-record or use a ground-lift DI."}


def _noise_floor(mono: Any, rate: int) -> dict[str, Any] | None:
    np, _signal, _sf, _pyln = _deps()
    block = max(1, int(rate * _BLOCK_S))
    blocks = len(mono) // block
    if blocks < 20:
        return None
    rms = np.sqrt(np.mean(mono[:blocks * block].reshape(blocks, block) ** 2, axis=1))
    levels = 20.0 * np.log10(np.maximum(rms, 1e-10))
    low, high = float(np.percentile(levels, 10)), float(np.percentile(levels, 90))
    if high - low < 10.0 or low < -100.0:
        return None                      # no quiet passages to measure a floor in
    severity = "moderate" if low > -50.0 else "minor" if low > -60.0 else None
    if severity is None:
        return None
    return {"problem": "noise", "severity": severity, "floor_db": round(low, 1),
            "snr_db": round(high - low, 1),
            "detail": f"the quiet passages sit at {low:.1f} dBFS ({high - low:.1f} dB below "
                      "the loud ones): audible hiss or room noise",
            "fix": "A gate or expander on the track, or cleaner source material."}


def _resonances(mono: Any, rate: int, top: int = 5) -> list[dict[str, Any]]:
    """Narrow peaks of the long-term spectrum that stand out of the smoothed spectrum AND are
    there most of the time (a ringing resonance, not a passing note)."""
    np, signal, _sf, _pyln = _deps()
    freqs, spec = _spectrum_db(mono, rate, 8192)
    logf = np.log2(np.maximum(freqs, 1.0))
    smooth = np.empty_like(spec)
    for i in range(len(freqs)):                   # median over 1/3 octave around each bin
        window = np.abs(logf - logf[i]) <= 1.0 / 6.0
        smooth[i] = np.median(spec[window])
    residual = spec - smooth
    in_range = (freqs >= _RES_RANGE[0]) & (freqs <= min(_RES_RANGE[1], rate / 2 * 0.9))
    peaks, props = signal.find_peaks(np.where(in_range, residual, -99.0),
                                     height=_RES_PROMINENCE_DB, distance=4)
    if not len(peaks):
        return []
    # persistence: the peak bin above its neighbourhood in most short frames
    nper = 8192 if len(mono) >= 8192 else max(256, len(mono))
    _f, _t, frames = signal.stft(mono, fs=rate, nperseg=nper, noverlap=nper // 2)
    frame_db = 20.0 * np.log10(np.maximum(np.abs(frames), 1e-10))
    found = []
    for index, height in sorted(zip(peaks.tolist(), props["peak_heights"].tolist()),
                                key=lambda item: -item[1]):
        freq = float(freqs[index])
        if 0 < index < len(spec) - 1:                 # parabolic interpolation of the peak
            a0, a1, a2 = spec[index - 1], spec[index], spec[index + 1]
            denominator = a0 - 2 * a1 + a2
            if abs(denominator) > 1e-12:
                freq += float(0.5 * (a0 - a2) / denominator) * float(freqs[1] - freqs[0])
        neighbours = (np.abs(logf - logf[index]) <= 1.0 / 6.0) & \
            (np.abs(logf - logf[index]) > 1.0 / 24.0)
        f_bin = int(np.argmin(np.abs(_f - freq)))
        n_bins = (np.abs(np.log2(np.maximum(_f, 1.0)) - math.log2(freq)) <= 1.0 / 6.0) & \
            (np.abs(np.log2(np.maximum(_f, 1.0)) - math.log2(freq)) > 1.0 / 24.0)
        if not n_bins.any() or not neighbours.any():
            continue
        above = frame_db[f_bin] - np.median(frame_db[n_bins], axis=0)
        loud = frame_db[f_bin] > np.max(frame_db) - 60.0
        persistence = float(np.mean((above > _RES_PROMINENCE_DB / 2) & loud))
        if persistence < _RES_PERSISTENCE:
            continue
        # width at half the prominence -> Q
        half = height / 2.0
        lo = index
        while lo > 0 and residual[lo] > half:
            lo -= 1
        hi = index
        while hi < len(residual) - 1 and residual[hi] > half:
            hi += 1
        width = max(float(freqs[hi] - freqs[lo]), float(freqs[1] - freqs[0]))
        q = round(min(max(freq / width, 0.7), 18.0), 1)
        cut = round(-min(height / 2.0, 9.0), 1)
        found.append({"frequency_hz": round(freq, 1), "note": _note_name(freq),
                      "prominence_db": round(float(height), 1), "q": q,
                      "persistence": round(persistence, 2),
                      "eq": {"frequency_hz": round(freq, 1), "gain_db": cut, "q": q}})
        if len(found) >= top:
            break
    return found


def problems(path: str, start: float = 0.0, duration: float | None = None) -> dict[str, Any]:
    """Technical problems of an audio file: clipping, DC offset, polarity between the
    channels, mains hum, a high noise floor and persistent resonances (with an EQ suggestion
    each). Heuristics — they point at what to listen to, they do not judge intent."""
    np, signal, _sf, _pyln = _deps()
    data, rate, meta = _load(path, start, duration)
    if not data.size:
        return {"file": meta, "problems": [], "notes": ["the file is empty"]}
    mono = np.mean(data, axis=1, dtype=np.float32)
    left_right = data.astype(np.float32)
    if data.shape[1] > 1:
        # a stereo-cancelling part would vanish in the downmix: analyse the louder side
        side_energy = [float(np.mean(data[:, c] ** 2)) for c in range(data.shape[1])]
        if float(np.mean(mono ** 2)) < 0.1 * max(side_energy):
            mono = data[:, int(np.argmax(side_energy))].astype(np.float32)
    found = [item for item in (_clipping(left_right, rate), _dc_offset(data), _polarity(data),
                               _hum(mono, rate), _noise_floor(mono, rate)) if item]
    resonances = _resonances(mono, rate)
    if resonances:
        listed = ", ".join(f"{r['frequency_hz']:g} Hz ({r['note']})" for r in resonances[:3])
        found.append({"problem": "resonance", "severity": "minor" if all(
            r["prominence_db"] < 12 for r in resonances) else "moderate",
            "peaks": resonances,
            "detail": f"persistent narrow peaks at {listed}: a ringing resonance — or a note "
                      "that holds through the whole stretch (a drone, a pad's root): listen "
                      "before cutting",
            "fix": "EQ Eight bells with the suggested frequency / gain / Q (each peak's "
                   "`eq`), on the track that rings."})
    order = {"significant": 0, "moderate": 1, "minor": 2}
    found.sort(key=lambda item: order.get(item.get("severity"), 3))
    notes = ["Heuristics: they show where to listen; distortion, noise or a drone can be "
             "intentional."]
    if not found:
        notes.insert(0, "No technical problems found.")
    return {"file": meta, "problems": found, "notes": notes}


# ------------------------------------------------------------------- layer alignment

def _band_limited(mono: Any, rate: int, band: str) -> Any:
    np, signal, _sf, _pyln = _deps()
    if band == "low":
        sos = signal.butter(4, 200.0, btype="lowpass", fs=rate, output="sos")
        return signal.sosfiltfilt(sos, mono)
    if band == "mid":
        sos = signal.butter(4, [200.0, 4000.0], btype="bandpass", fs=rate, output="sos")
        return signal.sosfiltfilt(sos, mono)
    return mono


def align(path_a: str, path_b: str, band: str = "low", max_ms: float = 20.0,
          start: float = 0.0, duration: float | None = 30.0) -> dict[str, Any]:
    """Time offset and polarity of part B against part A (two bounces of the same range):
    the delay that best lines B up with A (cross-correlation with phase weighting), whether B
    is upside down, and how much the two sum louder once aligned (the low end lost to phase).

    ``band``: "low" (below 200 Hz — kick and bass), "mid" (200 Hz-4 kHz) or "full"."""
    np, signal, _sf, _pyln = _deps()
    a, rate_a, meta_a = _load(path_a, start, duration)
    b, rate_b, meta_b = _load(path_b, start, duration)
    if rate_a != rate_b:
        factor = math.gcd(rate_a, rate_b)
        b = signal.resample_poly(b, rate_a // factor, rate_b // factor, axis=0)
    rate = rate_a
    x = _band_limited(np.mean(a, axis=1, dtype=np.float64), rate, band)
    y = _band_limited(np.mean(b, axis=1, dtype=np.float64), rate, band)
    n = min(len(x), len(y))
    if n < rate // 10:
        raise ValueError("the parts are shorter than 0.1 s")
    x, y = x[:n], y[:n]
    ex, ey = float(np.dot(x, x)), float(np.dot(y, y))
    if ex <= 1e-12 or ey <= 1e-12:
        return {"a": meta_a, "b": meta_b, "band": band, "silent": True,
                "notes": ["one of the parts is silent in this band"]}
    size = 1 << int(math.ceil(math.log2(2 * n)))
    cross = np.fft.irfft(np.fft.rfft(x, size) * np.conj(np.fft.rfft(y, size)), size)
    norm = math.sqrt(ex * ey)
    limit = max(1, int(rate * max_ms / 1000.0))
    lags = np.arange(-limit, limit + 1)                # r[k] = sum x[i+k] y[i]
    corr = cross[lags % size] / norm
    strength = np.abs(corr)
    # the strongest match; among near-equal ones (a steady low note repeats every period, and
    # half a period looks like a polarity flip) the smallest shift
    peaks = [i for i in range(1, len(strength) - 1)
             if strength[i] >= strength[i - 1] and strength[i] >= strength[i + 1]]
    peaks = peaks or [int(np.argmax(strength))]
    top = max(strength[i] for i in peaks)
    best = min((i for i in peaks if strength[i] >= 0.9 * top), key=lambda i: abs(lags[i]))
    shift = 0.0
    if 0 < best < len(strength) - 1:                   # sub-sample: parabola through the peak
        a0, a1, a2 = strength[best - 1], strength[best], strength[best + 1]
        denominator = a0 - 2 * a1 + a2
        if abs(denominator) > 1e-12:
            shift = float(0.5 * (a0 - a2) / denominator)
    lag = int(lags[best])                              # x[i+lag] ~ y[i]: B late by -lag
    corr_best = float(corr[best])
    corr_zero = float(corr[limit])
    inverted = corr_best < 0
    offset_ms = -(lag + shift) * 1000.0 / rate         # >0: B is late by this much

    def summed(shift: int, sign: float) -> float:
        if shift >= 0:
            s = x[shift:] + sign * y[:n - shift]
        else:
            s = x[:n + shift] + sign * y[-shift:]
        return float(np.mean(s ** 2))

    before = summed(0, 1.0)
    after = summed(lag, -1.0 if inverted else 1.0)
    gain_db = round(10.0 * math.log10(max(after, 1e-20) / max(before, 1e-20)), 1)
    advice = []
    if inverted:
        advice.append("B is upside down against A: flip B's polarity (Utility, both phase "
                      "invert switches).")
    if abs(offset_ms) >= 0.2:
        direction = "late" if offset_ms > 0 else "early"
        advice.append(f"B is {abs(offset_ms):.2f} ms {direction} against A: move B "
                      f"{'earlier' if offset_ms > 0 else 'later'} by that much (MIDI: shift its "
                      "notes; audio: nudge the clip or use the track header's Track Delay).")
    if gain_db >= 1.0:
        advice.append(f"Aligned, the two sum {gain_db} dB louder in this band — that is what "
                      "phase currently cancels.")
    elif not advice:
        advice.append("Already aligned: no useful offset or polarity flip in this band.")
    return {"a": meta_a, "b": meta_b, "band": band, "offset_ms": round(offset_ms, 3),
            "offset_samples": -lag, "polarity": "inverted" if inverted else "same",
            "correlation": {"as_is": round(corr_zero, 2),
                            "aligned": round(abs(corr_best), 2)},
            "sum_gain_db": gain_db, "advice": advice}
