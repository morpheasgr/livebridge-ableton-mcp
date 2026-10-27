"""Music-theory analysis of MIDI notes (pure Python, no dependencies).

Used by ``live_theory_analyze`` and ``live_theory_melody``: key detection (Krumhansl-Schmuckler profiles plus a mode hint),
chord naming per segment with Roman numerals, out-of-key notes, clashes between parts
(semitone rubs, low-register mud) and per-part statistics with plain-language suggestions; :func:`melody_check` measures a melody
against the craft checklist (adapted from hellyee's melody-craft and emotion-to-notes skills,
MIT, Copyright (c) 2026 Güven Seçkin) and optionally against a mood.

Notes are rows ``[pitch, start, duration, velocity, mute, ...]`` as ``notes.get`` returns them
(beats; C3 = 60). A *part* is ``{"name": str, "notes": rows, "length": beats, "drums": bool}``.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Sequence

__all__ = ["MOODS", "analyze_parts", "detect_key", "melody_check", "name_chord", "pitch_name",
           "SCALES"]

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_FLAT_NAMES = ("C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B")
#: Keys conventionally spelled with flats: (tonic pitch class, mode).
_FLAT_KEYS = {(5, "major"), (10, "major"), (3, "major"), (8, "major"), (1, "major"),
              (2, "minor"), (7, "minor"), (0, "minor"), (5, "minor"), (10, "minor"),
              (3, "minor")}

# Krumhansl-Kessler key profiles.
_MAJOR_PROFILE = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
_MINOR_PROFILE = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)

SCALES: dict[str, tuple[int, ...]] = {
    "major": (0, 2, 4, 5, 7, 9, 11),
    "minor": (0, 2, 3, 5, 7, 8, 10),
    "dorian": (0, 2, 3, 5, 7, 9, 10),
    "phrygian": (0, 1, 3, 5, 7, 8, 10),
    "lydian": (0, 2, 4, 6, 7, 9, 11),
    "mixolydian": (0, 2, 4, 5, 7, 9, 10),
    "harmonic minor": (0, 2, 3, 5, 7, 8, 11),
}
_MINOR_FAMILY = ("minor", "dorian", "phrygian", "harmonic minor")
_MAJOR_FAMILY = ("major", "lydian", "mixolydian")

#: Chord templates: suffix -> intervals from the root. Order = preference on ties (simple first).
_CHORDS: tuple[tuple[str, tuple[int, ...]], ...] = (
    ("", (0, 4, 7)), ("m", (0, 3, 7)), ("5", (0, 7)),
    ("7", (0, 4, 7, 10)), ("maj7", (0, 4, 7, 11)), ("m7", (0, 3, 7, 10)),
    ("sus4", (0, 5, 7)), ("sus2", (0, 2, 7)), ("dim", (0, 3, 6)), ("aug", (0, 4, 8)),
    ("m7b5", (0, 3, 6, 10)), ("dim7", (0, 3, 6, 9)), ("6", (0, 4, 7, 9)), ("m6", (0, 3, 7, 9)),
    ("add9", (0, 2, 4, 7)), ("m(add9)", (0, 2, 3, 7)), ("maj9", (0, 2, 4, 7, 11)),
    ("m9", (0, 2, 3, 7, 10)), ("9", (0, 2, 4, 7, 10)), ("mM7", (0, 3, 7, 11)),
)
_ROMAN = ("I", "II", "III", "IV", "V", "VI", "VII")

#: Below this pitch (E2 in Live's naming = 52) close intervals turn to mud.
_LOW_LIMIT = 52
_MIN_OVERLAP = 0.125   # beats two notes must sound together to count as simultaneous


def pitch_name(pitch: int, flats: bool = False) -> str:
    """"F#2"-style name in Live's octave numbering (60 = C3)."""
    names = _FLAT_NAMES if flats else NOTE_NAMES
    return f"{names[pitch % 12]}{pitch // 12 - 2}"


def _pc_name(pc: int, flats: bool) -> str:
    return (_FLAT_NAMES if flats else NOTE_NAMES)[pc % 12]


def _rows(notes: Iterable[Sequence[Any]]) -> list[tuple[int, float, float, int]]:
    """``(pitch, start, duration, velocity)`` of the unmuted notes, sorted by start."""
    rows = []
    for row in notes or ():
        if len(row) < 3:
            continue
        if len(row) > 4 and row[4] is True:
            continue
        velocity = int(row[3]) if len(row) > 3 else 100
        rows.append((int(row[0]), float(row[1]), max(float(row[2]), 0.0), velocity))
    rows.sort(key=lambda r: (r[1], r[0]))
    return rows


def _histogram(rows: Iterable[tuple[int, float, float, int]]) -> list[float]:
    """Pitch-class weights: duration (capped, so one pad note does not drown a melody)."""
    weights = [0.0] * 12
    for pitch, _start, duration, _velocity in rows:
        weights[pitch % 12] += min(max(duration, 0.125), 8.0)
    return weights


def _correlation(a: Sequence[float], b: Sequence[float]) -> float:
    mean_a, mean_b = sum(a) / len(a), sum(b) / len(b)
    num = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
    den = math.sqrt(sum((x - mean_a) ** 2 for x in a) * sum((y - mean_b) ** 2 for y in b))
    return num / den if den else 0.0


def detect_key(weights: Sequence[float], bass_weights: Sequence[float] | None = None,
               first_pc: int | None = None) -> list[dict[str, Any]]:
    """Rank the 24 major/minor keys for a pitch-class histogram (best first).

    ``bass_weights`` (histogram of the lowest part) nudges the ranking towards keys whose tonic
    the bass actually plays, and ``first_pc`` (the lowest note the material opens with) adds a
    little more: loops usually start on the tonic. Together they separate relative major/minor,
    which share all their notes.
    """
    if sum(weights) <= 0:
        return []
    bass_total = sum(bass_weights) if bass_weights else 0.0
    ranked = []
    for tonic in range(12):
        rotated = [weights[(tonic + i) % 12] for i in range(12)]
        for mode, profile in (("major", _MAJOR_PROFILE), ("minor", _MINOR_PROFILE)):
            score = _correlation(rotated, profile)
            if bass_total:
                score += 0.15 * (bass_weights[tonic] / bass_total)  # type: ignore[index]
            if first_pc == tonic:
                score += 0.2
            ranked.append((score, tonic, mode))
    ranked.sort(key=lambda item: -item[0])
    return [{"tonic": tonic, "mode": mode, "score": round(score, 3)}
            for score, tonic, mode in ranked]


def _best_mode(weights: Sequence[float], tonic: int, family: Sequence[str]) -> tuple[str, float]:
    """The scale of ``family`` that covers most of the material on ``tonic``."""
    total = sum(weights) or 1.0
    best, best_cover = family[0], -1.0
    for mode in family:
        cover = sum(weights[(tonic + step) % 12] for step in SCALES[mode]) / total
        if cover > best_cover + 0.02:        # prefer the plain scale unless clearly better
            best, best_cover = mode, cover
    return best, best_cover


def name_chord(weights: Sequence[float], bass_pc: int | None = None,
               flats: bool = False) -> dict[str, Any] | None:
    """Name the chord in a pitch-class weight vector.

    Returns ``{"name", "root", "suffix", "tones", "extra", "confidence"}`` or None for
    fewer than two pitch classes.
    """
    total = sum(weights)
    present = [pc for pc in range(12) if weights[pc] > 0.06 * total] if total else []
    if len(present) < 2:
        return None
    best: tuple[float, int, str, tuple[int, ...]] | None = None
    for root in present:
        for order, (suffix, intervals) in enumerate(_CHORDS):
            tones = {(root + i) % 12 for i in intervals}
            inside = sum(weights[pc] for pc in tones) / total
            missing = sum(1 for pc in tones if pc not in present)
            outside = sum(weights[pc] for pc in present if pc not in tones) / total
            score = inside - 0.35 * missing - 0.6 * outside - 0.004 * order
            if bass_pc is not None and root == bass_pc:
                score += 0.12
            if best is None or score > best[0]:
                best = (score, root, suffix, tuple(sorted(tones)))
    if best is None:
        return None
    score, root, suffix, tones = best
    name = _pc_name(root, flats) + suffix
    if bass_pc is not None and bass_pc != root and bass_pc in tones:
        name += "/" + _pc_name(bass_pc, flats)
    extra = [_pc_name(pc, flats) for pc in present if pc not in tones]
    return {"name": name, "root": root, "suffix": suffix,
            "tones": [_pc_name(pc, flats) for pc in tones], "extra": extra,
            "confidence": round(max(0.0, min(1.0, score)), 2)}


def _roman(root: int, suffix: str, tonic: int, scale: Sequence[int]) -> str:
    """Roman numeral of a chord root relative to the key's own scale ("i", "VI", "iv7", "bII")."""
    interval = (root - tonic) % 12
    accidental = ""
    if interval in scale:
        # scale degrees carry no accidental — the same convention live_clip_write_chords reads
        # ("i VI III VII" in a minor key)
        degree = list(scale).index(interval)
    else:
        degree = min(range(7), key=lambda d: abs(scale[d] - interval))
        accidental = "b" if interval < scale[degree] else "#"
    numeral = _ROMAN[degree]
    minorish = suffix.startswith("m") and not suffix.startswith("maj") or suffix.startswith("dim")
    if minorish:
        numeral = numeral.lower()
    tail = suffix
    if suffix == "m":
        tail = ""
    elif suffix.startswith("m") and not suffix.startswith("maj"):
        tail = suffix[1:]
    elif suffix.startswith("dim"):
        tail = "°" + suffix[3:]
    return accidental + numeral + tail


def _segment_weights(rows: Sequence[tuple[int, float, float, int]], start: float, end: float
                     ) -> tuple[list[float], int | None]:
    """Pitch-class weights and the lowest sounding pitch inside [start, end)."""
    weights = [0.0] * 12
    lowest: int | None = None
    for pitch, note_start, duration, _velocity in rows:
        overlap = min(note_start + duration, end) - max(note_start, start)
        if overlap <= 0.01:
            continue
        weights[pitch % 12] += overlap
        if overlap >= 0.2 * (end - start) or lowest is None:
            lowest = pitch if lowest is None else min(lowest, pitch)
    return weights, lowest


def _part_stats(name: str, rows: Sequence[tuple[int, float, float, int]], length: float,
                beats_per_bar: float, flats: bool) -> dict[str, Any]:
    pitches = [r[0] for r in rows]
    velocities = [r[3] for r in rows]
    events = sorted([(r[1], 1) for r in rows] + [(r[1] + r[2], -1) for r in rows],
                    key=lambda e: (e[0], e[1]))
    sounding = polyphony = 0
    for _time, delta in events:
        sounding += delta
        polyphony = max(polyphony, sounding)
    bars = max(length / beats_per_bar, 1e-9) if length else 0
    stats = {
        "name": name, "notes": len(rows),
        "range": [pitch_name(min(pitches), flats), pitch_name(max(pitches), flats)],
        "polyphony": polyphony,
        "velocity": [min(velocities), max(velocities)],
    }
    if bars:
        stats["notes_per_bar"] = round(len(rows) / bars, 1)
    return stats


def _clashes(parts: Sequence[dict[str, Any]], flats: bool, limit: int,
             scale_pcs: set[int]) -> list[dict[str, Any]]:
    """Semitone rubs between parts and close intervals in the low register.

    A minor 2nd / minor 9th always counts; a major 7th (the colour of a maj7 chord) only when
    one of the two notes is outside the key."""
    found: dict[tuple[Any, ...], dict[str, Any]] = {}
    for i, first in enumerate(parts):
        for second in parts[i:]:
            same = first is second
            for a in first["rows"]:
                a_end = a[1] + a[2]
                for b in second["rows"]:
                    if b[1] >= a_end:
                        break
                    if same and (b[0] <= a[0]):
                        continue
                    overlap = min(a_end, b[1] + b[2]) - max(a[1], b[1])
                    if overlap < _MIN_OVERLAP:
                        continue
                    low, high = sorted((a[0], b[0]))
                    distance = high - low
                    kind = None
                    in_key = low % 12 in scale_pcs and high % 12 in scale_pcs
                    if not same and (distance % 12 == 1 or
                                     (distance % 12 == 11 and not in_key)):
                        kind = "semitone"
                    elif 0 < distance < 7 and high < _LOW_LIMIT:
                        kind = "low_mud"
                    if kind is None:
                        continue
                    key = (kind, first["name"], second["name"], low % 12, high % 12)
                    entry = found.setdefault(key, {
                        "kind": kind, "parts": sorted({first["name"], second["name"]}),
                        "notes": [pitch_name(low, flats), pitch_name(high, flats)],
                        "at": [], "beats": 0.0})
                    entry["beats"] = round(entry["beats"] + overlap, 3)
                    if len(entry["at"]) < 4:
                        entry["at"].append(round(max(a[1], b[1]), 3))
    ordered = sorted(found.values(), key=lambda e: -e["beats"])
    return ordered[:limit]


def analyze_parts(parts: Sequence[dict[str, Any]], beats_per_bar: float = 4.0,
                  segment: float | None = None, key: dict[str, Any] | None = None,
                  max_chords: int = 64, max_items: int = 12) -> dict[str, Any]:
    """Analyse one or more parts together.

    Args:
        parts: ``[{"name", "notes", "length"?, "drums"?}]``; drum parts only get statistics.
        beats_per_bar: bar length in beats (4 for 4/4, 3 for 3/4, 3.5 for 7/8 ...).
        segment: chord window in beats (default: one bar; half a bar when that names more).
        key: force the key — ``{"tonic": 0-11, "mode": <SCALES name>}`` — instead of detecting.

    Returns:
        ``{key, key_candidates, scale_notes, chords, progression, out_of_key, clashes, parts,
        suggestions}`` — see ``live_theory_analyze``.
    """
    prepared = []
    for part in parts:
        rows = _rows(part.get("notes") or ())
        if rows:
            prepared.append({"name": str(part.get("name") or f"part {len(prepared) + 1}"),
                             "rows": rows, "drums": bool(part.get("drums")),
                             "length": float(part.get("length") or 0.0)})
    tonal = [p for p in prepared if not p["drums"]]
    result: dict[str, Any] = {}
    suggestions: list[str] = []
    if not tonal:
        result["key"] = None
        suggestions.append("No pitched notes to analyse (only drums or empty clips).")
    all_rows = sorted((r for p in tonal for r in p["rows"]), key=lambda r: (r[1], r[0]))
    weights = _histogram(all_rows)

    flats = False
    scale_pcs: set[int] = set()
    tonic = 0
    mode = "major"
    if tonal:
        lowest_part = min(tonal, key=lambda p: sum(r[0] for r in p["rows"]) / len(p["rows"]))
        opening = [r for r in all_rows if r[1] <= all_rows[0][1] + 0.01]
        candidates = detect_key(weights, _histogram(lowest_part["rows"]),
                                min(opening)[0] % 12)
        if key is not None:
            tonic, mode = int(key["tonic"]) % 12, str(key["mode"])
            confidence = None
        else:
            top = candidates[0]
            tonic = top["tonic"]
            family = _MINOR_FAMILY if top["mode"] == "minor" else _MAJOR_FAMILY
            mode, _cover = _best_mode(weights, tonic, family)
            margin = top["score"] - candidates[1]["score"] if len(candidates) > 1 else 1.0
            confidence = round(max(0.0, min(1.0, 0.5 * top["score"] + 2.5 * margin)), 2)
        flats = (tonic, "minor" if mode in _MINOR_FAMILY else "major") in _FLAT_KEYS
        scale = SCALES.get(mode, SCALES["major"])
        scale_pcs = {(tonic + step) % 12 for step in scale}
        result["key"] = {"name": f"{_pc_name(tonic, flats)} {mode}", "tonic": tonic,
                         "mode": mode}
        if confidence is not None:
            result["key"]["confidence"] = confidence
            result["key_candidates"] = [
                {"name": f"{_pc_name(c['tonic'], flats)} {c['mode']}", "score": c["score"]}
                for c in candidates[:3]]
            if confidence < 0.35:
                suggestions.append(
                    "The key is ambiguous (few distinct notes or relative major/minor): trust "
                    "the bass note of the first/last chord, or pass key= to fix it.")
        result["scale_notes"] = [_pc_name((tonic + step) % 12, flats) for step in scale]

        # ------------------------------------------------------------------ chords
        span = max([p["length"] for p in tonal] + [max(r[1] + r[2] for r in all_rows)])
        windows = [segment] if segment else [beats_per_bar, beats_per_bar / 2.0]
        chosen: list[dict[str, Any]] = []
        for window in windows:
            chords = []
            position = 0.0
            while position < span - 0.01 and len(chords) < max_chords * 2:
                seg_weights, lowest = _segment_weights(all_rows, position, position + window)
                chord = name_chord(seg_weights, None if lowest is None else lowest % 12, flats)
                chords.append({"at": round(position, 3), "chord": chord})
                position += window
            names = [c["chord"]["name"] if c["chord"] else None for c in chords]
            # half-bar windows only when they really show chords the bar windows hide
            if not chosen or (len(set(filter(None, names))) >
                              len({c["name"] for c in chosen if c.get("name")}) + 1):
                chosen = [{"at": c["at"], "bar": round(c["at"] / beats_per_bar + 1, 2),
                           "name": c["chord"]["name"],
                           "roman": _roman(c["chord"]["root"], c["chord"]["suffix"], tonic,
                                           scale),
                           "confidence": c["chord"]["confidence"],
                           **({"extra": c["chord"]["extra"]} if c["chord"]["extra"] else {})}
                          if c["chord"] else {"at": c["at"],
                                              "bar": round(c["at"] / beats_per_bar + 1, 2),
                                              "name": None}
                          for c in chords]
        merged: list[dict[str, Any]] = []
        for chord in chosen:
            if merged and merged[-1].get("name") == chord.get("name"):
                continue
            merged.append(chord)
        result["chords"] = merged[:max_chords]
        romans = [c["roman"] for c in merged if c.get("name")]
        loop = romans
        for size in range(1, len(romans) // 2 + 1):     # shortest repeating unit
            if len(romans) % size == 0 and romans == romans[:size] * (len(romans) // size):
                loop = romans[:size]
                break
        result["progression"] = " ".join(loop)

        # ------------------------------------------------------------------ out of key
        outside: dict[tuple[str, int], dict[str, Any]] = {}
        for part in tonal:
            for pitch, start, duration, _velocity in part["rows"]:
                if pitch % 12 in scale_pcs:
                    continue
                entry = outside.setdefault((part["name"], pitch % 12), {
                    "part": part["name"], "note": _pc_name(pitch % 12, flats), "count": 0,
                    "beats": 0.0, "at": []})
                entry["count"] += 1
                entry["beats"] = round(entry["beats"] + duration, 3)
                if len(entry["at"]) < 4:
                    entry["at"].append(round(start, 3))
        result["out_of_key"] = sorted(outside.values(), key=lambda e: -e["beats"])[:max_items]
        total_beats = sum(r[2] for r in all_rows) or 1.0
        outside_share = sum(e["beats"] for e in outside.values()) / total_beats
        if outside_share > 0.12:
            suggestions.append(
                f"{round(outside_share * 100)}% of the note time is outside "
                f"{result['key']['name']}: either the key is different (see key_candidates) or "
                "these are chromatic/borrowed notes — check them by ear or fit them with "
                "live_clip_transform_notes(fit_scale=true).")

        clashes = _clashes(tonal, flats, max_items, scale_pcs)
        result["clashes"] = clashes
        for clash in clashes[:3]:
            where = " + ".join(clash["parts"])
            if clash["kind"] == "semitone":
                suggestions.append(
                    f"{clash['notes'][0]} against {clash['notes'][1]} ({where}) is a semitone "
                    f"rub sounding {clash['beats']} beats (first at beat {clash['at'][0]}): "
                    "move one of them to a chord tone unless the tension is intended.")
            else:
                suggestions.append(
                    f"{clash['notes'][0]} + {clash['notes'][1]} ({where}) sit closer than a "
                    "fifth below E2 — that turns to mud: keep the low end to one note at a "
                    "time or move the upper note an octave up.")

    # ---------------------------------------------------------------------- parts
    stats = []
    for part in prepared:
        entry = _part_stats(part["name"], part["rows"], part["length"], beats_per_bar, flats)
        if part["drums"]:
            entry["drums"] = True
        low, high = entry["velocity"]
        if entry["notes"] >= 8 and high - low <= 4:
            suggestions.append(
                f"{part['name']}: every note has (almost) the same velocity ({low}-{high}) — "
                "accent the strong beats or humanize (live_clip_transform_notes("
                "humanize_velocity=...)) for movement.")
        stats.append(entry)
    result["parts"] = stats
    result["suggestions"] = suggestions
    return result



# ----------------------------------------------------------------------------- melody check

#: mood -> (scales that suit it, contour: "down" | "up" | "level" | "any",
#: velocity range, notes per bar (low, high)) — the emotion-to-notes table, as numbers.
MOODS: dict[str, dict[str, Any]] = {
    "melancholy": {"scales": ("minor", "dorian"), "contour": "down", "velocity": (40, 75),
                   "density": (1.0, 4.0), "tempo": (60, 85)},
    "anger": {"scales": ("phrygian", "harmonic minor", "minor"), "contour": "any",
              "velocity": (95, 127), "density": (6.0, 16.0), "tempo": (130, 155)},
    "calm": {"scales": ("major", "lydian"), "contour": "level", "velocity": (50, 85),
             "density": (1.0, 4.0), "tempo": (65, 90)},
    "euphoria": {"scales": ("mixolydian", "major"), "contour": "up", "velocity": (90, 122),
                 "density": (5.0, 12.0), "tempo": (122, 145)},
    "tension": {"scales": ("phrygian", "harmonic minor", "minor"), "contour": "level",
                "velocity": (50, 110), "density": (2.0, 8.0), "tempo": None},
    "nostalgia": {"scales": ("dorian", "mixolydian", "major"), "contour": "down",
                  "velocity": (55, 80), "density": (2.0, 6.0), "tempo": (70, 95)},
    "hope": {"scales": ("lydian", "major"), "contour": "up", "velocity": (65, 110),
             "density": (2.0, 7.0), "tempo": (90, 115)},
    "loneliness": {"scales": ("minor",), "contour": "down", "velocity": (30, 65),
                   "density": (0.5, 2.5), "tempo": (55, 75)},
    "triumph": {"scales": ("major", "mixolydian"), "contour": "up", "velocity": (95, 127),
                "density": (3.0, 8.0), "tempo": (100, 130)},
}
_MOOD_ALIASES = {"sad": "melancholy", "melancholic": "melancholy", "grief": "melancholy",
                 "angry": "anger", "aggressive": "anger", "peaceful": "calm", "chill": "calm",
                 "euphoric": "euphoria", "uplifting": "euphoria", "happy": "euphoria",
                 "tense": "tension", "suspense": "tension", "dark": "tension",
                 "nostalgic": "nostalgia", "hopeful": "hope", "lonely": "loneliness",
                 "triumphant": "triumph", "epic": "triumph"}


def mood_name(text: str) -> str | None:
    word = str(text or "").strip().lower()
    word = _MOOD_ALIASES.get(word, word)
    return word if word in MOODS else None


def _top_line(rows: Sequence[tuple[int, float, float, int]]) -> list[tuple[int, float, float, int]]:
    """The highest note of every onset: a chordal part is checked as its top voice."""
    line: dict[float, tuple[int, float, float, int]] = {}
    for row in rows:
        at = round(row[1], 3)
        if at not in line or row[0] > line[at][0]:
            line[at] = row
    return [line[at] for at in sorted(line)]


def _bar_shape(rows: Sequence[tuple[int, float, float, int]], bar: int, beats_per_bar: float
               ) -> tuple[tuple[float, ...], tuple[int, ...], tuple[int, ...]] | None:
    """(rhythm, pitches, intervals) of one bar, positions relative to the bar."""
    start = bar * beats_per_bar
    inside = [r for r in rows if start - 1e-6 <= r[1] < start + beats_per_bar - 1e-6]
    if not inside:
        return None
    rhythm = tuple(round(r[1] - start, 3) for r in inside)
    pitches = tuple(r[0] for r in inside)
    intervals = tuple(b - a for a, b in zip(pitches, pitches[1:]))
    return rhythm, pitches, intervals


def melody_check(notes: Iterable[Sequence[Any]], beats_per_bar: float = 4.0,
                 length: float | None = None, harmony: Iterable[Sequence[Any]] | None = None,
                 key: dict[str, Any] | None = None, mood: str | None = None) -> dict[str, Any]:
    """Measure a melody against the craft checklist ("seven traps" of generated melodies).

    Args:
        notes: the melody's rows (a chordal part is reduced to its top voice).
        beats_per_bar: bar length in beats.
        length: the phrase length in beats (default: up to the end of the last note, whole
            bars).
        harmony: notes of the chords/bass under it: enables the tension-note check
            (non-chord tones resolved by step) and better key detection.
        key: force ``{"tonic", "mode"}`` instead of detecting it.
        mood: a ``MOODS`` name (or alias): also compare contour, velocity and density.

    Returns:
        ``{key, notes, bars, checks:[{check, ok, detail}], passed, total, profile:{contour,
        notes_per_bar, velocity, range, climax}, mood?:{name, checks}, suggestions}``.
    """
    rows = _top_line(_rows(notes))
    if len(rows) < 3:
        return {"notes": len(rows), "checks": [], "passed": 0, "total": 0,
                "suggestions": ["Fewer than 3 notes: nothing to check yet."]}
    harmony_rows = _rows(harmony or ())
    end = max(r[1] + r[2] for r in rows)
    if length is None or length <= 0:
        length = math.ceil(end / beats_per_bar - 1e-6) * beats_per_bar
    bars = max(1, int(math.ceil(length / beats_per_bar - 1e-6)))

    # key: forced, else from melody + harmony
    weights = _histogram(rows + harmony_rows)
    if key is not None:
        tonic, mode = int(key["tonic"]) % 12, str(key["mode"])
    else:
        bass = harmony_rows or rows
        ranked = detect_key(weights, _histogram(bass), min(bass, key=lambda r: r[1])[0] % 12)
        tonic = ranked[0]["tonic"]
        family = _MINOR_FAMILY if ranked[0]["mode"] == "minor" else _MAJOR_FAMILY
        mode, _cover = _best_mode(weights, tonic, family)
    flats = (tonic, "minor" if mode in _MINOR_FAMILY else "major") in _FLAT_KEYS
    scale = SCALES.get(mode, SCALES["major"])
    third = (tonic + scale[2]) % 12

    checks: list[dict[str, Any]] = []
    suggestions: list[str] = []

    def check(name: str, ok: bool, detail: str, fix: str) -> None:
        checks.append({"check": name, "ok": ok, "detail": detail})
        if not ok:
            suggestions.append(fix)

    # 1. grid marching
    off = [r for r in rows if abs(r[1] - round(r[1])) > 0.01]
    steps = {round(b[1] - a[1], 3) for a, b in zip(rows, rows[1:])}
    check("off_grid", len(off) >= 2 and len(steps) > 1,
          f"{len(off)} of {len(rows)} notes start off the quarter-note grid"
          + ("; every onset is the same distance apart" if len(steps) <= 1 else ""),
          "Grid marching: start a phrase on the 'and' (x.5), anticipate a bar with a pickup "
          "at beat 3.5, let a downbeat pass silently.")
    # 2. durations
    lengths = sorted({round(r[2] * 16) / 16 for r in rows})
    long_notes = [r for r in rows if r[2] >= 1.5]
    check("durations", len(lengths) >= 3 and bool(long_notes),
          f"{len(lengths)} distinct lengths ({', '.join(f'{v:g}' for v in lengths[:6])}), "
          f"{len(long_notes)} note(s) of 1.5 beats or more",
          "Uniform durations: mix ornaments (0.25), motion (0.5-0.75), anchors (1.5-2) and "
          "one long arrival (2.5-4 beats).")
    # 3. rests (gaps in the line of at least one beat), one per 4 bars
    gaps, reach = 0, rows[0][1]
    if rows[0][1] >= 1.0:
        gaps += 1
    for r in rows:
        if r[1] - reach >= 1.0 - 1e-6:
            gaps += 1
        reach = max(reach, r[1] + r[2])
    if length - reach >= 1.0 - 1e-6:
        gaps += 1
    needed = max(1, bars // 4)
    check("rests", gaps >= needed, f"{gaps} rest(s) of a beat or more over {bars} bar(s) "
          f"(want {needed}+)", "No rests: end phrases a beat early and leave at least one "
          "beat of silence every 4 bars — the listener breathes there.")
    # 4. velocity arc
    velocities = [r[3] for r in rows]
    span = max(velocities) - min(velocities)
    top_pitch = max(r[0] for r in rows)
    climax_rows = [r for r in rows if r[0] == top_pitch]
    climax = climax_rows[0]
    aligned = climax[3] >= max(velocities) - 6
    check("velocity", span >= 25 and aligned,
          f"velocity {min(velocities)}-{max(velocities)} (span {span}); climax note at "
          f"velocity {climax[3]}",
          "Flat velocity: spread 25+ (e.g. 62 -> 74 -> 88 -> 106) shaped toward the climax "
          "— the highest note gets the highest velocity; ghost ornaments -20.")
    # 5. one climax at 60-75 % (accepted 55-80 %)
    position = (climax[1] + climax[2] / 2) / length if length else 0.0
    single = len({round(r[1], 3) for r in climax_rows}) == 1
    check("climax", single and 0.55 <= position <= 0.8,
          f"highest note {pitch_name(top_pitch, flats)} "
          f"{'once' if single else f'{len(climax_rows)} times'}, at "
          f"{round(position * 100)}% of the phrase",
          "Climax: one highest note, about 60-75% through the phrase (bar 5-6 of 8), reached "
          "by a build and left by a descent — two climaxes are no climax.")
    # 6. leaps recovered by contrary step
    leaps = recovered = 0
    for a, b, c in zip(rows, rows[1:], rows[2:]):
        jump = b[0] - a[0]
        if abs(jump) >= 7:
            leaps += 1
            after = c[0] - b[0]
            if after and (after > 0) != (jump > 0) and abs(after) <= 2:
                recovered += 1
    if len(rows) >= 2 and abs(rows[-1][0] - rows[-2][0]) >= 7:
        leaps += 1
    check("leaps", leaps >= 1 and recovered >= 1,
          f"{leaps} leap(s) of a fifth or more, {recovered} answered by a step back",
          "Scale-walking: put one leap of a 5th-6th before the climax, then move stepwise "
          "in the opposite direction — leaps carry the emotion.")
    # 7. tension notes (needs harmony)
    if harmony_rows:
        tensions = 0
        for this, nxt in zip(rows, rows[1:]):
            weights_here, _low = _segment_weights(harmony_rows, this[1], this[1] + max(this[2],
                                                                                     0.25))
            chord = name_chord(weights_here)
            if not chord:
                continue
            tones = {(chord["root"] + i) % 12 for i in dict(_CHORDS)[chord["suffix"]]}
            if this[0] % 12 not in tones and abs(nxt[0] - this[0]) <= 2 and \
                    nxt[0] % 12 in tones:
                tensions += 1
        wanted = max(1, bars // 2)
        check("tension", tensions >= wanted,
              f"{tensions} non-chord tone(s) resolved by step (want {wanted}+, one per 2 "
              "bars)", "Only chord tones: add an appoggiatura on a strong beat (a step above "
              "the chord tone, resolving down), a suspension over a chord change or a passing "
              "tone — and resolve each by step.")
    # 8. motif developed, not pasted
    shapes = [s for s in (_bar_shape(rows, b, beats_per_bar) for b in range(bars)) if s]
    pasted = sum(1 for i, s in enumerate(shapes) if any(s == o for o in shapes[:i]))
    varied = sum(1 for i, s in enumerate(shapes)
                 if any(s != o and (s[0] == o[0] or (s[2] and s[2] == o[2]))
                        for o in shapes[:i]))
    if len(shapes) >= 2:
        check("motif", varied >= 1 and pasted <= len(shapes) // 4,
              f"{varied} bar(s) repeat an earlier rhythm or shape with changes, {pasted} "
              "pasted identically",
              "Copy-paste repetition: keep the motif's rhythm or shape but change the detail "
              "— sequence it a step higher, invert it, fragment its tail.")
    # 9. question and answer
    if bars >= 2:
        half = length / 2
        first = [r for r in rows if r[1] < half]
        second = [r for r in rows if r[1] >= half]
        if first and second:
            asks = first[-1][0] % 12 != tonic
            answers = second[-1][0] % 12 in (tonic, third)
            check("question_answer", asks and answers,
                  f"first half ends on {_pc_name(first[-1][0] % 12, flats)}, second on "
                  f"{_pc_name(second[-1][0] % 12, flats)}",
                  "Question and answer: end the first half away from the tonic (2nd, 5th or "
                  "7th), land the second half on the tonic or the third.")

    ups = sum(1 for a, b in zip(rows, rows[1:]) if b[0] > a[0])
    downs = sum(1 for a, b in zip(rows, rows[1:]) if b[0] < a[0])
    moves = max(ups + downs, 1)
    contour = "up" if ups / moves >= 0.6 else "down" if downs / moves >= 0.6 else "level"
    density = round(len(rows) / bars, 1)
    profile = {"contour": contour, "ups": ups, "downs": downs, "notes_per_bar": density,
               "velocity": {"min": min(velocities), "max": max(velocities),
                            "mean": round(sum(velocities) / len(velocities))},
               "range": [pitch_name(min(r[0] for r in rows), flats),
                         pitch_name(top_pitch, flats)],
               "climax": {"note": pitch_name(top_pitch, flats), "beat": round(climax[1], 3)}}
    result: dict[str, Any] = {
        "key": {"name": f"{_pc_name(tonic, flats)} {mode}", "tonic": tonic, "mode": mode},
        "notes": len(rows), "bars": bars, "checks": checks,
        "passed": sum(1 for c in checks if c["ok"]), "total": len(checks), "profile": profile}
    if mood is not None:
        name = mood_name(mood)
        if name is not None:
            target = MOODS[name]
            mood_checks = []
            low, high = target["velocity"]
            mean = profile["velocity"]["mean"]
            mood_checks.append({"check": "velocity", "ok": low - 5 <= mean <= high + 5,
                                "detail": f"mean velocity {mean}, {name} wants {low}-{high}"})
            dlow, dhigh = target["density"]
            mood_checks.append({"check": "density", "ok": dlow <= density <= dhigh,
                                "detail": f"{density} notes per bar, {name} wants "
                                          f"{dlow:g}-{dhigh:g}"})
            if target["contour"] != "any":
                mood_checks.append({"check": "contour", "ok": contour == target["contour"],
                                    "detail": f"contour {contour} ({ups} up / {downs} down), "
                                              f"{name} wants {target['contour']}"})
            mood_checks.append({"check": "scale", "ok": mode in target["scales"],
                                "detail": f"{mode}, {name} suits "
                                          f"{', '.join(target['scales'])}"})
            result["mood"] = {"name": name, "checks": mood_checks,
                              **({"tempo": list(target["tempo"])} if target["tempo"] else {})}
            for item in mood_checks:
                if not item["ok"]:
                    suggestions.append(f"Mood ({name}): {item['detail']}.")
    if not suggestions:
        suggestions.append("Every check passes. Whether it moves anyone is for ears to "
                           "decide: play it.")
    result["suggestions"] = suggestions
    return result
