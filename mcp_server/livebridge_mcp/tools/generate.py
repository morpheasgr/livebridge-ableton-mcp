"""Generative note tools: `live_clip_write_euclid` writes Euclidean rhythms (hits spread as
evenly as possible over a cycle) and polymeters (rows with different cycle lengths drifting
against each other); `live_clip_note_chance` gives notes a play probability and velocity
variation so a loop changes every time it repeats (Live 11+ note probability).

Both are MCP-side: Euclid rows become step strings for the `notes.write_pattern` bridge command,
chance edits go through `notes.get` / `notes.modify`.
"""

from __future__ import annotations

import random
import re
from typing import Any

from . import bridge_call, tool_error
from ..client import BridgeClient
from .clips import address_args, clip_address
from .notes import _pitch_arg

Ref = int | str

_STEP_RE = re.compile(r"^\s*1\s*/\s*(\d+)\s*(t?)\s*$", re.I)
_SPEC_RE = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*(?:[+@r]\s*(-?\d+))?\s*$", re.I)
_MAX_STEPS = 1024
_NOTE_PAGE = 2000


def _is_error(value: Any) -> bool:
    return isinstance(value, dict) and "error" in value and "type" in value


def euclid(hits: int, steps: int, rotate: int = 0) -> list[bool]:
    """``hits`` onsets spread as evenly as possible over ``steps`` (the Euclidean rhythm),
    starting with a hit, then rotated ``rotate`` steps later (negative = earlier).

    ``euclid(3, 8)`` -> x..x..x. (tresillo), ``euclid(4, 16)`` -> four on the floor,
    ``euclid(5, 8)`` -> x.x.xx.x (cinquillo family).
    """
    if steps <= 0:
        return []
    hits = max(0, min(hits, steps))
    pattern = [(index * hits) % steps < hits for index in range(steps)]
    shift = rotate % steps
    return pattern[-shift:] + pattern[:-shift] if shift else pattern


def step_beats(step: float | str) -> float | None:
    """A step as beats: 0.25, "1/16" -> 0.25, "1/8T" -> 1/3. None when unreadable."""
    if isinstance(step, (int, float)) and not isinstance(step, bool):
        return float(step) if step > 0 else None
    match = _STEP_RE.match(str(step))
    if not match or not int(match.group(1)):
        return None
    beats = 4.0 / int(match.group(1))
    return beats * 2.0 / 3.0 if match.group(2) else beats


def _row_spec(row: str, spec: Any) -> dict[str, Any] | str:
    """Normalise one row spec to {hits, steps, rotate, velocity?, accent?} or an error text."""
    if isinstance(spec, str):
        match = _SPEC_RE.match(spec)
        if not match:
            return (f"row {row!r}: write it as 'hits/steps' or 'hits/steps+rotate' "
                    f"('3/8', '5/16+2'), got {spec!r}")
        spec = {"hits": int(match.group(1)), "steps": int(match.group(2)),
                "rotate": int(match.group(3) or 0)}
    if not isinstance(spec, dict):
        return f"row {row!r}: expected 'hits/steps' or {{'hits', 'steps', ...}}"
    unknown = set(spec) - {"hits", "steps", "rotate", "velocity", "accent"}
    if unknown:
        return f"row {row!r}: unknown keys {sorted(unknown)} (hits, steps, rotate, velocity, accent)"
    try:
        hits, steps = int(spec["hits"]), int(spec["steps"])
        rotate = int(spec.get("rotate", 0))
    except (KeyError, TypeError, ValueError):
        return f"row {row!r}: hits and steps must be whole numbers"
    if not 1 <= steps <= 64 or not 0 <= hits <= steps:
        return f"row {row!r}: steps must be 1..64 and hits 0..steps"
    velocity = spec.get("velocity")
    if velocity is not None and (isinstance(velocity, bool) or
                                 not isinstance(velocity, (int, float)) or
                                 not 1 <= velocity <= 127):
        return f"row {row!r}: velocity must be 1..127"
    return {"hits": hits, "steps": steps, "rotate": rotate, "velocity": velocity,
            "accent": bool(spec.get("accent", False))}


def row_string(spec: dict[str, Any], total: int) -> str:
    """The step string for one row over ``total`` steps (the cycle repeats: a polymeter when
    its length does not divide ``total``)."""
    cycle = euclid(spec["hits"], spec["steps"], spec["rotate"])
    if spec["velocity"] is None:
        hit = "x"
    else:
        hit = str(max(1, min(9, round(spec["velocity"] * 9 / 127.0))))
    chars = []
    for index in range(total):
        on = cycle[index % len(cycle)]
        first = index % len(cycle) == next((i for i, v in enumerate(cycle) if v), -1)
        chars.append(("X" if spec["accent"] and first else hit) if on else ".")
    return "".join(chars)


def register(mcp: Any, bridge: BridgeClient) -> None:
    """Register the generative note tools on the MCP app."""

    def beats_per_bar() -> float:
        state = bridge_call(bridge, "transport.get")
        if isinstance(state, dict) and not _is_error(state):
            match = re.match(r"^(\d+)/(\d+)$", str(state.get("signature", "")))
            if match and int(match.group(2)):
                return int(match.group(1)) * 4.0 / int(match.group(2))
        return 4.0

    @mcp.tool()
    def live_clip_write_euclid(
        rhythms: dict[str, Any],
        track: Ref | None = None,
        slot: Ref | None = None,
        clip: str | None = None,
        bars: float = 1,
        step: float | str = "1/16",
        velocity: float = 100,
        accent_velocity: float = 127,
        gate: float = 0.9,
        swing: float = 0.0,
        clear: bool = True,
        create: bool = True,
    ) -> Any:
        """Write Euclidean rhythms and polymeters into a MIDI clip: each row spreads N hits as
        evenly as possible over M steps (3/8 = tresillo, 5/8 = cinquillo, 7/16 = a rolling
        hat), and each row repeats its own cycle — rows of different lengths drift against the
        bar (polymeter: a 5-step percussion loop over 4/4 takes 5 bars to come round). Great
        for hypnotic techno percussion, afro/latin feels and generative hats.

        Args:
            rhythms: {row: spec}. Row = a drum name ("kick", "hat", "rim", "shaker" ... mapped
                onto the Drum Rack's pads like `live_clip_write_pattern`), a note name or a
                number. Spec = "hits/steps", "hits/steps+rotate" ("5/16+2" starts 2 steps
                later), or {"hits", "steps", "rotate"?, "velocity"? (1..127, per row),
                "accent"? (first hit of each cycle accented)}.
            track, slot / clip: Target clip (an empty slot gets a new clip).
            bars: Length to fill, in bars of the song's time signature. A row whose cycle does
                not divide the bar comes round only after several bars (a 5-step row over 16ths
                in 4/4 needs 5 bars); write that many, or loop a shorter clip and let it drift.
            step: Step length: "1/16" (default), "1/8", "1/32", "1/8T", or beats.
            velocity, accent_velocity, gate, swing: As in `live_clip_write_pattern`.
            clear: Remove existing notes of those rows in the range first (default).
            create: Create the clip when the slot is empty.

        Returns:
            The `live_clip_write_pattern` result plus `patterns` ({row: step string}) and
            `steps` (total steps written).
        """
        cmd = "notes.write_pattern"
        if clip is None and track is None:
            return clip_address(track, slot, clip, cmd)
        if not isinstance(rhythms, dict) or not rhythms:
            return tool_error("rhythms must be a non-empty {row: 'hits/steps'} object", cmd=cmd)
        beats = step_beats(step)
        if beats is None:
            return tool_error("step must be beats > 0 or a note value like '1/16', '1/8T'",
                              cmd=cmd)
        if not 0 < bars <= 64:
            return tool_error("bars must be in (0, 64]", cmd=cmd)
        for value, label, low, high in ((velocity, "velocity", 1, 127),
                                        (accent_velocity, "accent_velocity", 1, 127),
                                        (gate, "gate", 0.05, 1.0), (swing, "swing", 0.0, 1.0)):
            if not low <= value <= high:
                return tool_error(f"{label} must be between {low} and {high}", cmd=cmd)
        total = int(round(bars * beats_per_bar() / beats))
        if not 1 <= total <= _MAX_STEPS:
            return tool_error(f"bars x step gives {total} steps (1..{_MAX_STEPS})", cmd=cmd)
        patterns = {}
        for row, spec in rhythms.items():
            parsed = _row_spec(str(row), spec)
            if isinstance(parsed, str):
                return tool_error(parsed, cmd=cmd)
            patterns[str(row)] = row_string(parsed, total)
        args = address_args(track, slot, clip)
        args.update(pattern=patterns, step=beats, velocity=velocity,
                    accent_velocity=accent_velocity, gate=gate, swing=swing, clear=clear,
                    create=create)
        result = bridge_call(bridge, cmd, args)
        if isinstance(result, dict) and not _is_error(result):
            result = dict(result, patterns=patterns, steps=total)
        return result

    @mcp.tool()
    def live_clip_note_chance(
        probability: float | list[float] = 0.7,
        track: Ref | None = None,
        slot: Ref | None = None,
        clip: str | None = None,
        pitch: int | str | list[int | str] | None = None,
        start: float | None = None,
        end: float | None = None,
        velocity_deviation: float | None = None,
        keep_downbeats: bool = True,
        seed: int | None = None,
    ) -> Any:
        """Make a clip play differently every loop: give notes a play probability (Live 11+
        note chance) and optional velocity variation. Use it on hats, ghost notes, percussion
        and arps for movement without writing variations; keep kicks and main snares at 1.0.
        One undo step.

        Args:
            probability: Chance 0..1 for every matched note, or [low, high] for a random
                chance per note (seeded) — e.g. [0.4, 0.9].
            track, slot / clip: The MIDI clip.
            pitch: Only these pitches (number, name such as "F#1", or a list) — e.g. the hat
                row of a drum clip.
            start, end: Only notes starting in this beat range.
            velocity_deviation: Also set Live's per-note velocity deviation (-127..127; 10-20 is
                a natural spread). None leaves it.
            keep_downbeats: Leave notes on whole beats at probability 1 (the groove's anchor).
            seed: Seed for [low, high] (same seed = same chances).

        Returns:
            `{clip, changed, kept (downbeats), notes:[{note_id, pitch, start, probability}]
            (up to 64)}`.
        """
        cmd = "notes.modify"
        error = clip_address(track, slot, clip, cmd)
        if error:
            return error
        if isinstance(probability, list):
            if len(probability) != 2 or not all(isinstance(p, (int, float)) and 0 <= p <= 1
                                                for p in probability) \
                    or probability[0] > probability[1]:
                return tool_error("probability must be 0..1 or [low, high] with "
                                  "0 <= low <= high <= 1", cmd=cmd)
        elif not isinstance(probability, (int, float)) or not 0 <= probability <= 1:
            return tool_error("probability must be 0..1 or [low, high]", cmd=cmd)
        if velocity_deviation is not None and not -127 <= velocity_deviation <= 127:
            return tool_error("velocity_deviation must be -127..127", cmd=cmd)
        pitch_value, error = _pitch_arg(pitch, "pitch", cmd)
        if error:
            return error
        address = address_args(track, slot, clip)
        query: dict[str, Any] = dict(address, limit=_NOTE_PAGE)
        if pitch_value is not None:
            query["pitch"] = pitch_value
        for key, value in (("start", start), ("end", end)):
            if value is not None:
                query[key] = value
        notes: list[list[Any]] = []
        clip_path = None
        offset = 0
        while True:
            page = bridge_call(bridge, "notes.get", dict(query, offset=offset))
            if _is_error(page) or not isinstance(page, dict):
                return page
            clip_path = clip_path or page.get("clip")
            fields = page.get("fields") or ["pitch", "start", "duration", "velocity", "mute",
                                            "probability", "note_id"]
            notes.extend(dict(zip(fields, row)) for row in page.get("notes") or [])
            offset = page.get("next_offset")
            if not offset:
                break
        rng = random.Random(seed)
        changes, kept, report = [], 0, []
        for note in notes:
            if keep_downbeats and abs(note["start"] - round(note["start"])) < 1e-6:
                kept += 1
                continue
            chance = (round(rng.uniform(probability[0], probability[1]), 3)
                      if isinstance(probability, list) else float(probability))
            change: dict[str, Any] = {"note_id": note["note_id"], "probability": chance}
            if velocity_deviation is not None:
                change["velocity_deviation"] = velocity_deviation
            changes.append(change)
            if len(report) < 64:
                report.append({"note_id": note["note_id"], "pitch": note["pitch"],
                               "start": note["start"], "probability": chance})
        if not changes:
            return {"clip": clip_path, "changed": 0, "kept": kept, "notes": []}
        done = bridge_call(bridge, cmd, {"clip": clip_path, "changes": changes}
                           if clip_path else dict(address, changes=changes))
        if _is_error(done):
            return done
        return {"clip": clip_path, "changed": len(changes), "kept": kept, "notes": report}
