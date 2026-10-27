"""Mix decisions on measured readings (pure Python, no dependencies).

Used by ``live_mix_balance``, ``live_master_auto``, ``live_sound_check`` and the ``genre=``
option of ``live_audio_analyze``. Readings are what :func:`engine.summarize` and
:func:`audio_analysis.analyze_file` return: ``{loudness:{lufs, peak_db, crest_db},
spectrum:{bands_db (8 bands, dB of the total)}, stereo:{width}}``.

- **Roles** (:func:`guess_role`): from the track name first, otherwise from the sound — where the
  energy sits in the 8 bands, the crest factor and the width (a fixed rule set, no model).
- **Genre targets** (:data:`GENRES`): target loudness, a 6-band balance and level offsets per
  role for 18 genres, adapted from HighVoltSound's ableton-auto-mix-mcp style profiles (MIT,
  Copyright (c) 2026 HighVoltSound). They are rules of thumb, not measurements of releases.
- **Track or master** (:func:`band_decisions`): a band that is off on the master is a master
  EQ move when the excess is spread over 3+ tracks, a track EQ move when 1-2 tracks own it —
  the planner idea of the same project.
- **Patch health** (:func:`patch_health`, :func:`filter_cutoffs`): a sound strangled by its
  filter (one band holds nearly all the energy, no harmonics above 2 kHz, a low-pass far below
  the working floor of its role) — adapted from hellyee's preset audit (MIT, Copyright (c)
  2026 Güven Seçkin).
"""

from __future__ import annotations

import math
import re
from typing import Any, Iterable

__all__ = ["DESTINATIONS", "GENRES", "ROLE_OFFSETS", "band_decisions", "balance_plan",
           "filter_cutoffs", "genre_balance", "genre_key", "guess_role", "parse_hz",
           "patch_health", "role_from_name", "role_from_sound"]

ROLES = ("kick", "snare", "hats", "perc", "drums", "sub", "bass", "pad", "lead", "keys",
         "vocal", "guitar", "fx")

#: name words -> role; checked in order (so "bass drum" is a kick, "sub bass" a sub)
_ROLE_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("kick", ("kick", "bd", "bass drum", "kck")),
    ("snare", ("snare", "clap", "rim", "snr")),
    ("hats", ("hat", "hh", "cymbal", "ride", "crash", "shaker")),
    ("perc", ("perc", "tom", "conga", "bongo", "tamb", "cowbell")),
    ("drums", ("drum", "beat", "break", "kit", "groove")),
    ("sub", ("sub", "808")),
    ("bass", ("bass", "reese", "wobble")),
    ("vocal", ("vocal", "vox", "voice", "acapella", "chop", "choir")),
    ("pad", ("pad", "string", "atmo", "drone", "texture", "ambience")),
    ("keys", ("key", "piano", "rhodes", "organ", "chord", "stab")),
    ("guitar", ("guitar", "gtr")),
    ("fx", ("fx", "sfx", "riser", "sweep", "impact", "noise", "uplifter", "downlifter")),
    ("lead", ("lead", "synth", "pluck", "arp", "hook", "melody", "bell", "horn", "sax",
              "flute", "trumpet", "violin")),
)
_WORD_RE = re.compile(r"[a-z0-9]+")

#: loudness of each role relative to the loudest part (LU) when no genre says otherwise:
#: the dance-mix order "kick on top, bass under it, lead below, then percussion, then pads"
ROLE_OFFSETS: dict[str, float] = {
    "kick": 0.0, "drums": 0.0, "sub": -1.0, "bass": -3.0, "snare": -3.0, "vocal": -2.0,
    "lead": -6.0, "guitar": -6.0, "keys": -8.0, "perc": -9.0, "pad": -10.0, "hats": -11.0,
    "fx": -13.0,
}
_DEFAULT_OFFSET = -8.0

#: genre -> target LUFS, 6-band balance (dB, sub / bass / low-mid / mid / high-mid / high) and
#: role offsets (LU below the loudest role). From HighVoltSound's style profiles (MIT).
GENRES: dict[str, dict[str, Any]] = {
    "ambient": {"label": "Ambient / Cinematic", "lufs": -18.0,
                "bands": (-8.0, -8.0, -6.0, -4.0, -7.0, -11.0),
                "roles": {"pad": 0.0, "fx": -4.0, "lead": -3.0, "bass": -4.0}},
    "balanced": {"label": "Neutral / Balanced", "lufs": -13.0,
                 "bands": (-5.0, -5.0, -6.0, -6.0, -7.0, -11.0), "roles": {}},
    "breaks": {"label": "Breaks / Breakbeat", "lufs": -11.0,
               "bands": (-2.0, -4.0, -6.0, -7.0, -7.0, -11.0),
               "roles": {"drums": 0.0, "kick": -1.0, "snare": 0.0, "sub": -2.0, "bass": -3.0,
                         "hats": -8.0, "perc": -7.0, "pad": -9.0, "lead": -3.5, "fx": -11.0,
                         "vocal": -4.0}},
    "classical": {"label": "Classical / Orchestral", "lufs": -18.0,
                  "bands": (-6.0, 0.0, -2.0, 0.0, -1.0, -3.0),
                  "roles": {"lead": -2.0, "pad": -1.0, "bass": 0.0, "perc": -4.0}},
    "country": {"label": "Country / Americana", "lufs": -12.0,
                "bands": (-8.0, -2.0, 0.0, -3.0, -2.0, -5.0),
                "roles": {"kick": -1.0, "bass": 0.0, "snare": -2.0, "guitar": -2.0,
                          "lead": -5.0, "vocal": 0.0}},
    "drum_n_bass": {"label": "Drum & Bass / Jungle", "lufs": -7.5,
                    "bands": (-2.0, -5.0, -6.0, -7.0, -7.0, -11.0),
                    "roles": {"drums": -1.0, "kick": -2.0, "snare": -2.0, "sub": 0.0,
                              "bass": -5.0, "hats": -8.0, "pad": -9.0, "fx": -11.0}},
    "dubstep": {"label": "Dubstep / Brostep", "lufs": -7.0,
                "bands": (-1.0, -4.0, -5.0, -6.0, -8.0, -12.0),
                "roles": {"sub": 0.0, "kick": -2.0, "bass": -4.0, "snare": -4.0, "perc": -9.0,
                          "hats": -10.0, "fx": -11.0}},
    "funk": {"label": "Funk / Groove", "lufs": -11.0,
             "bands": (-4.0, 0.0, -3.0, -5.0, -4.0, -8.0),
             "roles": {"kick": -2.0, "bass": 0.0, "snare": -4.0, "hats": -10.0, "guitar": -6.0,
                       "keys": -8.0, "vocal": -4.0, "lead": -8.0, "perc": -12.0}},
    "hip_hop": {"label": "Hip-Hop", "lufs": -9.0,
                "bands": (0.0, -3.0, -6.0, -8.0, -7.0, -11.0),
                "roles": {"sub": 0.0, "kick": -1.0, "snare": -5.0, "perc": -7.0, "hats": -9.0,
                          "vocal": -3.0, "lead": -7.0, "fx": -12.0}},
    "jazz": {"label": "Jazz / Acoustic", "lufs": -16.0,
             "bands": (-10.0, -3.0, 0.0, -2.0, -3.0, -6.0),
             "roles": {"bass": 0.0, "keys": -2.0, "lead": 0.0, "drums": -4.0, "vocal": 0.0,
                       "guitar": -3.0}},
    "lo_fi": {"label": "Lo-Fi / Chill", "lufs": -14.0,
              "bands": (-6.0, -5.0, -4.0, -5.0, -8.0, -14.0),
              "roles": {"lead": 0.0, "bass": -2.0, "kick": -1.0, "snare": -3.0, "hats": -6.0,
                        "fx": -10.0}},
    "metal": {"label": "Metal / Hard Rock", "lufs": -7.0,
              "bands": (-2.0, 0.0, -4.0, -6.0, -2.0, -5.0),
              "roles": {"kick": -1.0, "bass": 0.0, "snare": -3.0, "guitar": -4.0,
                        "hats": -10.0, "vocal": -3.0}},
    "pop": {"label": "Pop / Top 40", "lufs": -12.0,
            "bands": (-4.0, -4.0, -7.0, -7.0, -6.0, -10.0),
            "roles": {"vocal": 0.0, "bass": -3.0, "kick": -2.0, "snare": -4.0, "lead": -6.0,
                      "pad": -9.0, "perc": -8.0, "fx": -11.0}},
    "reggaeton": {"label": "Reggaeton / Latin", "lufs": -9.0,
                  "bands": (0.0, -2.0, -6.0, -8.0, -5.0, -10.0),
                  "roles": {"kick": -1.0, "sub": 0.0, "snare": -4.0, "hats": -12.0,
                            "vocal": -3.0, "lead": -8.0, "perc": -10.0}},
    "rnb": {"label": "R&B / Soul", "lufs": -10.0,
            "bands": (0.0, -1.0, -5.0, -6.0, -4.0, -7.0),
            "roles": {"kick": -2.0, "sub": 0.0, "snare": -5.0, "hats": -12.0, "vocal": -2.0,
                      "keys": -7.0, "pad": -10.0, "guitar": -8.0}},
    "techno": {"label": "Techno / Club", "lufs": -8.0,
               "bands": (0.0, -2.0, -8.0, -10.0, -9.0, -12.0),
               "roles": {"kick": 0.0, "sub": -1.0, "bass": -3.0, "perc": -9.0, "hats": -11.0,
                         "pad": -10.0, "lead": -7.0, "fx": -13.0}},
    "trance": {"label": "Trance / Uplifting", "lufs": -8.5,
               "bands": (-3.0, -4.0, -7.0, -7.0, -8.0, -12.0),
               "roles": {"kick": 0.0, "sub": -3.0, "bass": -5.0, "lead": -4.0, "pad": -8.0,
                         "perc": -10.0, "hats": -11.0, "fx": -12.0}},
    "trap": {"label": "Trap / Phonk", "lufs": -8.5,
             "bands": (-1.0, -3.0, -7.0, -7.0, -6.0, -10.0),
             "roles": {"sub": 0.0, "kick": -2.0, "snare": -3.0, "hats": -8.0, "perc": -10.0,
                       "vocal": -3.0, "lead": -7.0, "fx": -11.0}},
}
_GENRE_ALIASES = {"dnb": "drum_n_bass", "d&b": "drum_n_bass", "drum and bass": "drum_n_bass",
                  "drum & bass": "drum_n_bass", "jungle": "drum_n_bass", "hiphop": "hip_hop",
                  "hip-hop": "hip_hop", "hip hop": "hip_hop", "rap": "hip_hop", "lofi": "lo_fi",
                  "lo-fi": "lo_fi", "lo fi": "lo_fi", "r&b": "rnb", "soul": "rnb",
                  "latin": "reggaeton", "house": "techno", "tech house": "techno",
                  "melodic techno": "trance", "phonk": "trap", "rock": "metal",
                  "orchestral": "classical", "cinematic": "ambient", "neutral": "balanced"}

#: the 6 bands of GENRES as unions of the 8 bands of live_audio_analyze
SIX_BANDS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("sub", "20-60 Hz", ("sub",)), ("bass", "60-120 Hz", ("bass",)),
    ("low_mid", "120-250 Hz", ("low_mid",)), ("mid", "250 Hz-2 kHz", ("mud", "mid")),
    ("high_mid", "2-5 kHz", ("presence",)), ("high", "5-20 kHz", ("high", "air")),
)

#: master loudness targets: destination -> (integrated LUFS, limiter ceiling dB, min crest dB)
DESTINATIONS: dict[str, tuple[float, float, float]] = {
    "streaming": (-14.0, -1.0, 9.0), "spotify": (-14.0, -1.0, 9.0),
    "youtube": (-14.0, -1.0, 9.0), "apple": (-16.0, -1.0, 9.0),
    "soundcloud": (-10.0, -0.3, 7.5), "club": (-8.0, -0.3, 7.0),
    "demo": (-12.0, -1.0, 8.0),
}

#: working floor of a low-pass for a role (Hz): below it the sound is reduced to its
#: fundamental (hellyee's measured floors)
FILTER_FLOORS: dict[str, float] = {"bass": 1500.0, "pad": 1500.0, "keys": 3000.0,
                                   "lead": 3000.0, "vocal": 3000.0, "guitar": 3000.0}


# ----------------------------------------------------------------------------- roles

def role_from_name(name: str) -> str | None:
    """The role a track name says ("Kick", "Sub Bass" -> sub, "Lead Synth" -> lead)."""
    tokens = _WORD_RE.findall(str(name or "").lower())
    if not tokens:
        return None
    padded = f" {' '.join(tokens)} "
    for role, words in _ROLE_WORDS:
        for word in words:
            if " " in word:
                hit = f" {word} " in padded
            elif len(word) <= 2:
                hit = word in tokens
            else:                           # "hats", "hihat", "rimshot", "keys"
                hit = any(t.startswith(word) or t.endswith(word) for t in tokens)
            if hit:
                return role
    return None


def _share(bands: dict[str, float], *names: str) -> float:
    return sum(10.0 ** (float(bands.get(n, -90.0)) / 10.0) for n in names)


def role_from_sound(reading: dict[str, Any]) -> tuple[str | None, float, str]:
    """``(role, confidence 0..1, why)`` from where the energy sits, the crest factor and width.

    Rough on purpose: it separates a kick from a sub, hats from a pad, not a lead from keys.
    """
    bands = ((reading.get("spectrum") or {}).get("bands_db")) or {}
    if not bands:
        return None, 0.0, "no spectrum"
    loud = reading.get("loudness") or {}
    crest = loud.get("crest_db")
    width = (reading.get("stereo") or {}).get("width")
    low = _share(bands, "sub", "bass")
    sub = _share(bands, "sub")
    lowmid = _share(bands, "low_mid", "mud")
    mid = _share(bands, "mid")
    upper = _share(bands, "presence")
    top = _share(bands, "high", "air")
    punchy = crest is not None and crest >= 12.0
    sustained = crest is not None and crest < 9.0
    if low >= 0.55:
        if punchy:
            return "kick", min(1.0, 0.5 + low / 2), f"{round(low * 100)}% below 120 Hz, crest " \
                f"{crest} dB (punchy)"
        role = "sub" if sub >= 0.4 else "bass"
        return role, min(1.0, 0.4 + low / 2), f"{round(low * 100)}% below 120 Hz, sustained"
    if top >= 0.35 and (crest is None or crest >= 9.0):
        return "hats", min(1.0, 0.4 + top), f"{round(top * 100)}% above 5 kHz"
    if top >= 0.25 and sustained:
        return "fx", 0.4, f"{round(top * 100)}% above 5 kHz, no transients (noise, riser)"
    if punchy and (upper + mid) >= 0.45:
        return ("snare" if lowmid + mid >= 0.3 else "perc"), 0.5, \
            f"short hits centred at 500 Hz-5 kHz (crest {crest} dB)"
    if lowmid + mid >= 0.5 and low >= 0.15:
        return "bass", 0.35, "low-mid heavy with some low end (mid bass, reese)"
    if sustained and width is not None and width >= 0.25:
        return "pad", 0.5, f"sustained and wide (width {width})"
    if sustained:
        return "pad", 0.35, "sustained"
    if upper + mid >= 0.45:
        return "lead", 0.4, "energy in 500 Hz-5 kHz with movement"
    return None, 0.0, "no clear profile"


def guess_role(name: str, reading: dict[str, Any] | None = None) -> dict[str, Any]:
    """``{role, confidence, from: "name"|"sound"|"none", why}``."""
    by_name = role_from_name(name)
    if by_name:
        return {"role": by_name, "confidence": 0.9, "from": "name",
                "why": f"track name {name!r}"}
    if reading:
        role, confidence, why = role_from_sound(reading)
        if role:
            return {"role": role, "confidence": round(confidence, 2), "from": "sound",
                    "why": why}
    return {"role": None, "confidence": 0.0, "from": "none", "why": "name and sound unclear"}


# ----------------------------------------------------------------------------- genres

def genre_key(text: str | None) -> str | None:
    word = " ".join(str(text or "").strip().lower().replace("_", " ").split())
    if not word:
        return None
    if word.replace(" ", "_") in GENRES:
        return word.replace(" ", "_")
    return _GENRE_ALIASES.get(word)


def role_offset(role: str | None, genre: str | None = None) -> float:
    if genre and role and role in GENRES[genre]["roles"]:
        return float(GENRES[genre]["roles"][role])
    if role and genre and role == "sub" and "bass" in GENRES[genre]["roles"]:
        return float(GENRES[genre]["roles"]["bass"]) + 1.0
    return ROLE_OFFSETS.get(role or "", _DEFAULT_OFFSET)


def _six_shares(bands_db: dict[str, float]) -> dict[str, float]:
    powers = {name: _share(bands_db, *parts) for name, _hz, parts in SIX_BANDS}
    total = sum(powers.values())
    return {name: (10.0 * math.log10(p / total) if p > 0 and total > 0 else -90.0)
            for name, p in powers.items()}


def genre_shares(genre: str) -> dict[str, float]:
    powers = [10.0 ** (t / 10.0) for t in GENRES[genre]["bands"]]
    total = sum(powers)
    return {name: 10.0 * math.log10(p / total)
            for (name, _hz, _parts), p in zip(SIX_BANDS, powers)}


def genre_balance(bands_db: dict[str, float], genre: str, tolerance: float = 4.0
                  ) -> dict[str, Any]:
    """A mix's 6-band balance against a genre's (both as dB of the total)."""
    measured, target = _six_shares(bands_db), genre_shares(genre)
    rows, notes = [], []
    for name, hz, _parts in SIX_BANDS:
        delta = round(measured[name] - target[name], 1)
        rows.append({"band": name, "hz": hz, "measured_db": round(measured[name], 1),
                     "target_db": round(target[name], 1), "delta_db": delta})
        if abs(delta) >= tolerance:
            notes.append(f"{name} ({hz}) is {abs(delta)} dB {'above' if delta > 0 else 'below'}"
                         f" a typical {GENRES[genre]['label']} balance.")
    return {"genre": genre, "label": GENRES[genre]["label"], "target_lufs": GENRES[genre]["lufs"],
            "bands": rows, "notes": notes or [f"Within {tolerance:g} dB of a typical "
                                              f"{GENRES[genre]['label']} balance in every band."],
            "caveat": "Genre balances are rules of thumb (HighVoltSound's style profiles), not "
                      "measured releases: compare with a reference track for real targets."}


# ----------------------------------------------------------------------------- balance

def balance_plan(parts: list[dict[str, Any]], genre: str | None = None,
                 anchor: str | None = None, max_db: float = 6.0, min_move: float = 0.5
                 ) -> dict[str, Any]:
    """Fader moves that put every part at its role's level.

    Args:
        parts: ``[{name, role, lufs (pre-fader), fader_db}]`` — parts without lufs are left
            out by the caller.
        genre: a GENRES key for the role offsets.
        anchor: the part that keeps its level (default: the kick or the loudest role).
        max_db: highest fader value allowed (Live's +6 dB).
        min_move: smaller moves are left out.

    Returns:
        ``{anchor, moves:[{name, role, offset_lu, now_db, target_db, fader_db, new_fader_db,
        move_db}], unchanged:[name]}``; loudness values are post-fader LUFS.
    """
    if not parts:
        return {"anchor": None, "moves": [], "unchanged": []}
    for part in parts:
        part["offset"] = role_offset(part.get("role"), genre)
        part["post"] = float(part["lufs"]) + float(part["fader_db"])
    chosen = None
    if anchor:
        chosen = next((p for p in parts if p["name"].lower() == anchor.lower()), None)
    if chosen is None:
        chosen = next((p for p in parts if p.get("role") == "kick"), None)
    if chosen is None:
        chosen = max(parts, key=lambda p: (p["offset"], p["post"]))
    level = chosen["post"] - chosen["offset"]          # loudness of offset 0
    # never ask a fader above max_db: lower everything together instead
    over = max(level + p["offset"] - p["lufs"] - max_db for p in parts)
    if over > 0:
        level -= over
    moves, unchanged = [], []
    for part in parts:
        target = level + part["offset"]
        new_fader = target - part["lufs"]
        move = new_fader - part["fader_db"]
        entry = {"name": part["name"], "role": part.get("role"), "offset_lu": part["offset"],
                 "now_lufs": round(part["post"], 1), "target_lufs": round(target, 1),
                 "fader_db": round(part["fader_db"], 1), "new_fader_db": round(new_fader, 1),
                 "move_db": round(move, 1)}
        if abs(move) < min_move:
            unchanged.append(part["name"])
        else:
            moves.append(entry)
    return {"anchor": chosen["name"], "lowered_for_headroom_db": round(max(over, 0.0), 1),
            "moves": moves, "unchanged": unchanged}


def band_decisions(master_bands: dict[str, float], tracks: list[dict[str, Any]],
                   genre: str | None = None, tolerance: float = 3.0) -> list[dict[str, Any]]:
    """Where to fix each band the master has too much / too little of.

    Args:
        master_bands: the master's 8 bands (dB of the total).
        tracks: ``[{name, role, bands_db, post_lufs}]`` (post-fader loudness).
        genre: compare with the genre's balance; default the mix targets of
            ``live_audio_analyze``.

    Returns:
        ``[{band, hz, delta_db, fix: "master"|"tracks", tracks:[{name, share}], advice}]``:
        "master" when 3+ tracks share the band's energy (no one above 40 %), "tracks" when 1-2
        tracks own it.
    """
    from .audio_analysis import BANDS
    if genre:
        target = genre_shares(genre)
        measured = _six_shares(master_bands)
        groups = [(name, hz, parts) for name, hz, parts in SIX_BANDS]
    else:
        target = {name: t for name, _l, _h, t in BANDS}
        measured = {name: float(master_bands.get(name, -90.0)) for name in target}
        groups = [(name, f"{low:g}-{high:g} Hz", (name,)) for name, low, high, _t in BANDS]
    owners_ok = {"sub": ("kick", "sub", "bass", "drums"), "bass": ("kick", "sub", "bass",
                                                                   "drums")}
    decisions = []
    for name, hz, parts in groups:
        delta = measured[name] - target[name]
        if abs(delta) < tolerance:
            continue
        energy = []
        for track in tracks:
            bands = track.get("bands_db") or {}
            power = _share(bands, *parts) * 10.0 ** (float(track["post_lufs"]) / 10.0)
            energy.append((power, track))
        total = sum(p for p, _t in energy) or 1.0
        ranked = sorted(((p / total, t) for p, t in energy), key=lambda x: -x[0])
        top = [(s, t) for s, t in ranked if s >= 0.15][:4]
        spread = len([s for s, _t in ranked if s >= 0.1]) >= 3 and ranked and ranked[0][0] < 0.4
        entry = {"band": name, "hz": hz, "delta_db": round(delta, 1),
                 "fix": "master" if spread else "tracks",
                 "tracks": [{"name": t["name"], "share": round(s, 2)} for s, t in top]}
        direction = "cut" if delta > 0 else "boost"
        amount = round(min(abs(delta) / 2.0, 4.0), 1)
        if spread:
            entry["advice"] = (f"{direction.capitalize()} {name} ({hz}) about {amount} dB on the "
                               "master/bus EQ: the excess is shared by "
                               f"{len([s for s, _t in ranked if s >= 0.1])} tracks."
                               if delta > 0 else
                               f"The mix lacks {name} ({hz}) across the board: a gentle "
                               f"{amount} dB master shelf/bell, or more of the parts that should "
                               "carry it.")
        else:
            names = ", ".join(t["name"] for _s, t in top[:2]) or "one track"
            owners = owners_ok.get(name, ())
            if delta > 0 and top and top[0][1].get("role") not in owners:
                entry["advice"] = (f"Cut {name} ({hz}) on {names} (EQ Eight, about {amount} dB) "
                                   "— it owns most of the excess; leave the master alone.")
            elif delta > 0:
                entry["advice"] = (f"{names} carries the {name} excess: lower it or tighten its "
                                   "EQ rather than EQ the whole mix.")
            else:
                entry["advice"] = (f"Little {name} ({hz}) and only {names} carries it: raise or "
                                   "boost that part, or add a part for this range.")
        decisions.append(entry)
    return decisions


# ----------------------------------------------------------------------------- patch health

def patch_health(reading: dict[str, Any], role: str | None = None) -> list[str]:
    """Warnings for a sound that is physically crippled (filter shut, no transients, no top).

    Says nothing about whether the sound is *good* — strangled, not boring.
    """
    bands = ((reading.get("spectrum") or {}).get("bands_db")) or {}
    notes: list[str] = []
    if not bands:
        return notes
    name, level = max(bands.items(), key=lambda item: item[1])
    if level >= 10.0 * math.log10(0.85):
        notes.append(f"{round(10 ** (level / 10) * 100)}% of the energy sits in one band "
                     f"({name}): the patch is filtered shut — read every filter cutoff in the "
                     "chain; the lowest low-pass wins.")
    crest = (reading.get("loudness") or {}).get("crest_db")
    if crest is not None and crest < 9.5 and role not in ("pad", "fx", "sub", "bass"):
        notes.append(f"Crest factor {crest} dB: almost no dynamics. Normal for a pad; a pluck, "
                     "drum or lead should have transients — check the amp envelope.")
    top = _share(bands, "presence", "high", "air")
    if top < 0.01 and role not in ("sub", "kick"):
        notes.append("Nearly nothing above 2 kHz: no harmonics, the sound is its fundamental and "
                     "vanishes on laptop speakers and phones.")
    return notes


_HZ_RE = re.compile(r"^\s*(-?\d+(?:[.,]\d+)?)\s*(k?)hz\s*$", re.I)
_FILTER_NAME_RE = re.compile(r"freq|cutoff", re.I)
_EQ_BAND_RE = re.compile(r"^\d+ (frequency|freq) [ab]$", re.I)
_TYPE_NAME_RE = re.compile(r"\btype\b", re.I)
_CAPS_TOP_RE = re.compile(r"low\s*-?pass|band\s*-?pass|\blp\b|\bbp\b|\blow\b", re.I)
_TAIL_RE = re.compile(r"\s*(freq(uency)?|cutoff)\s*$", re.I)


def parse_hz(display: Any) -> float | None:
    """"265 Hz" / "1.20 kHz" -> Hz; anything else None."""
    match = _HZ_RE.match(str(display or ""))
    if not match:
        return None
    value = float(match.group(1).replace(",", "."))
    return value * 1000.0 if match.group(2) else value


def filter_cutoffs(device: str, parameters: Iterable[dict[str, Any]],
                   role: str | None = None) -> list[dict[str, Any]]:
    """Filter cutoffs (in Hz) among a device's parameters ``[{name, display}]``.

    ``caps_top``: the filter takes the top off (a low-pass or band-pass, or an unknown type —
    most single filters are low-pass); ``below_floor``: below the role's working floor.
    """
    params = list(parameters)
    types = {_TYPE_NAME_RE.sub("", str(p.get("name"))).strip().lower(): str(p.get("display"))
             for p in params if _TYPE_NAME_RE.search(str(p.get("name", "")))}
    floor = FILTER_FLOORS.get(role or "")
    found = []
    for param in params:
        name = str(param.get("name", ""))
        if not _FILTER_NAME_RE.search(name) or _EQ_BAND_RE.match(name):
            continue
        hz = parse_hz(param.get("display"))
        if hz is None:
            continue
        prefix = _TAIL_RE.sub("", name).strip().lower()
        kind = types.get(prefix)
        if kind is None:
            kind = next((v for k, v in types.items() if "filter" in k), None)
        caps = bool(_CAPS_TOP_RE.search(kind)) if kind else True
        entry = {"device": device, "parameter": name, "hz": round(hz, 1), "type": kind,
                 "caps_top": caps}
        if floor is not None:
            entry["below_floor"] = caps and hz < floor
        found.append(entry)
    return found
