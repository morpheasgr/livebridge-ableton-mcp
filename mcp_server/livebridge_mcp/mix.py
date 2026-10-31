"""Mix decisions on measured readings (pure Python, no dependencies).

Used by ``live_mix_balance``, ``live_master_auto``, ``live_sound_check`` and the ``genre=``
option of ``live_audio_analyze``. Readings are what :func:`engine.summarize` and
:func:`audio_analysis.analyze_file` return: ``{loudness:{lufs, peak_db, crest_db},
spectrum:{bands_db (8 bands, dB of the total)}, stereo:{width}}``.

- **Roles** (:func:`guess_role`): from the track name first, otherwise from the sound — where the
  energy sits in the 8 bands, the crest factor and the width (a fixed rule set, no model).
- **Genre targets** (:data:`GENRES`): target loudness, a 6-band balance and level offsets per
  role for 20 genres (house and melodic_house are LiveBridge's), adapted from HighVoltSound's ableton-auto-mix-mcp style profiles (MIT,
  Copyright (c) 2026 HighVoltSound). They are rules of thumb, not measurements of releases.
- **Track or master** (:func:`band_decisions`): a band that is off on the master is a master
  EQ move when the excess is spread over 3+ tracks, a track EQ move when 1-2 tracks own it —
  the planner idea of the same project.
- **Channel strips** (:func:`channel_plan`): what the LiveBridge Channel device (a channel
  strip from Ableton's DSP in front of the Engine's meters) should do for a part — high-pass
  by role, mud / harshness cuts and air from the measured bands, compression to the role's
  crest factor, bass mono, a little saturation on a dull bass or lead. Rules of thumb on a
  reading of the raw part, not a trained model.
- **Patch health** (:func:`patch_health`, :func:`filter_cutoffs`): a sound strangled by its
  filter (one band holds nearly all the energy, no harmonics above 2 kHz, a low-pass far below
  the working floor of its role) — adapted from hellyee's preset audit (MIT, Copyright (c)
  2026 Güven Seçkin).
"""

from __future__ import annotations

import math
import re
from typing import Any, Iterable

__all__ = ["CHANNEL_NEUTRAL", "DESTINATIONS", "GENRES", "ROLE_OFFSETS", "STRIP_ROLES",
           "band_decisions", "balance_plan", "channel_plan", "clash_band",
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
    # sub and bass before drums: "BS Break Subbass" is a sub, not a breakbeat
    ("sub", ("sub", "808")),
    ("bass", ("bass", "reese", "wobble")),
    ("drums", ("drum", "beat", "break", "kit", "groove")),
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
    "house": {"label": "House / Tech House", "lufs": -8.5,
              "bands": (0.0, -2.0, -7.0, -9.0, -8.0, -11.0),
              "roles": {"kick": 0.0, "sub": -1.0, "bass": -3.0, "snare": -5.0, "vocal": -2.0,
                        "lead": -6.0, "keys": -6.0, "perc": -8.0, "hats": -9.0, "pad": -8.0,
                        "fx": -12.0}},
    "melodic_house": {"label": "Melodic House / Techno, Progressive", "lufs": -9.0,
                      # measured, not a rule of thumb: two released masters in the style
                      # (one stem-, one mix-mastered) put 2-5 kHz 6-10 dB and 5-20 kHz 4-5 dB
                      # under the old profile (-7, -10), the mids ~2 dB above it
                      "bands": (-1.0, -2.5, -6.0, -5.0, -15.0, -14.5), "measured": True,
                      # the melody carries the track: leads, pads and keys sit close to the
                      # kick instead of 6-10 LU under it
                      "roles": {"kick": 0.0, "sub": -2.0, "bass": -3.0, "lead": -2.0,
                                "pad": -5.0, "keys": -4.0, "vocal": -2.0, "guitar": -4.0,
                                "perc": -9.0, "hats": -10.0, "snare": -6.0, "fx": -10.0}},
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
                  "latin": "reggaeton", "tech house": "house", "deep house": "house",
                  "afro house": "house", "melodic": "melodic_house",
                  "melodic techno": "melodic_house", "progressive house": "melodic_house",
                  "progressive": "melodic_house", "organic house": "melodic_house", "phonk": "trap", "rock": "metal",
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
            "caveat": ("Measured from released masters in the style (two tracks): a "
                       "starting point, not a rule — compare with a reference track for real "
                       "targets." if GENRES[genre].get("measured") else
                       "Genre balances are rules of thumb (HighVoltSound's style profiles), not "
                       "measured releases: compare with a reference track for real targets.")}


# ----------------------------------------------------------------------------- balance

def balance_plan(parts: list[dict[str, Any]], genre: str | None = None,
                 anchor: str | None = None, max_db: float = 6.0, min_move: float = 2.0,
                 max_move: float | None = None,
                 raise_left: dict[str, float] | None = None,
                 move_left: dict[str, float] | None = None,
                 protect: Iterable[str] = (),
                 settled: Iterable[str] = ()) -> dict[str, Any]:
    """Fader moves that bring the parts toward their roles' levels WITHOUT moving the mix as a
    whole: the moves are centred (median 0) so the overall level stays where the user had it —
    only the parts that stand out move. (Anchoring on the kick and lowering everything when one
    quiet part could not reach its target took a whole 23-track mix down 10-14 dB.)

    Args:
        parts: ``[{name, role, lufs (pre-fader), fader_db}]`` — parts without lufs are left
            out by the caller.
        genre: a GENRES key for the role offsets.
        anchor: a part that keeps its level, the others placed around it (default: none — the
            level-neutral centring).
        max_db: highest fader value (Live's +6 dB): a part that would need more stops there
            (``at_max``) — the others do not come down for it.
        min_move: tolerance — a part within this many dB of its target is left alone, and a
            part outside it moves only to just inside it (``margin`` dB in), not to the
            target: a second run then finds it within the tolerance and changes nothing.
        max_move: cap one move to this many dB.
        raise_left: per part, how many dB it may still go UP in this run (``raise_capped``).
        move_left: per part, how many dB it may still move in this run, either way
            (``budget_capped``).
        protect: parts that never move (the lead, the vocal); they still count in the
            centring.
        settled: parts whose faders are still where the last run left them: they get 1 dB
            more tolerance (a level-neutral target moves a little whenever another part's
            level changes — an unchanged piano bass crossed the line and moved 1.6 dB).

    Returns:
        ``{anchor?, level_shift_db, moves:[{name, role, offset_lu, now_lufs, target_lufs,
        fader_db, new_fader_db, move_db, capped?, ...}], unchanged:[name], protected:[name],
        held?:[{name, role, off_db (+ = too loud), budget_capped?|raise_capped?|at_max?}]}``
        — ``held`` parts are in ``unchanged`` too: off target, but a limit stops the move;
        loudness values are post-fader LUFS.
    """
    if not parts:
        return {"anchor": None, "level_shift_db": 0.0, "moves": [], "unchanged": [],
                "protected": []}
    keep = {str(n).lower() for n in protect}
    calm = {str(n).lower() for n in settled}
    for part in parts:
        part["offset"] = role_offset(part.get("role"), genre)
        part["post"] = float(part["lufs"]) + float(part["fader_db"])
    chosen = None
    if anchor:
        chosen = next((p for p in parts if p["name"].lower() == anchor.lower()), None)
    # protected parts count in the centring like any other (they just do not move): the mix
    # keeps its level around them — anchoring on them pulled everything else up to the lead
    if chosen is not None:
        level = chosen["post"] - chosen["offset"]           # loudness of offset 0
    else:
        wishes = sorted(p["post"] - p["offset"] for p in parts)
        middle = len(wishes) // 2
        level = wishes[middle] if len(wishes) % 2 else (wishes[middle - 1] + wishes[middle]) / 2
    moves, unchanged, protected, applied, held = [], [], [], [], []
    for part in parts:
        target = level + part["offset"]
        if part["name"].lower() in keep or (chosen is not None and part is chosen):
            (protected if part["name"].lower() in keep else unchanged).append(part["name"])
            continue
        move = target - part["post"]
        wanted = move
        flags = {}
        if abs(move) < min_move + (1.0 if part["name"].lower() in calm else 0.0):
            unchanged.append(part["name"])
            continue
        if min_move > 0:                    # to just inside the tolerance, not the target
            margin = min(1.5, min_move / 2.0)
            move = math.copysign(abs(move) - (min_move - margin), move)
        if max_move is not None and abs(move) > max_move:
            move, flags["capped"] = math.copysign(max_move, move), True
        if move_left is not None and abs(move) > max(0.0, move_left.get(part["name"], abs(move))):
            move = math.copysign(max(0.0, move_left[part["name"]]), move)
            flags["budget_capped"] = True
        if raise_left is not None and move > 0:
            left = max(0.0, raise_left.get(part["name"], move))
            if move > left:
                move, flags["raise_capped"] = left, True
        new_fader = float(part["fader_db"]) + move
        if new_fader > max_db:
            new_fader, flags["at_max"] = max_db, True
            move = new_fader - float(part["fader_db"])
        if abs(move) < 0.5:
            unchanged.append(part["name"])
            if flags:                  # off target, but a limit stops it: say so
                held.append({"name": part["name"], "role": part.get("role"),
                             "off_db": round(-wanted, 1), **flags})
            continue
        applied.append(move)
        moves.append({"name": part["name"], "role": part.get("role"),
                      "offset_lu": part["offset"], "now_lufs": round(part["post"], 1),
                      "target_lufs": round(target, 1), "fader_db": round(part["fader_db"], 1),
                      "new_fader_db": round(new_fader, 1), "move_db": round(move, 1), **flags})
    plan: dict[str, Any] = {"level_shift_db": round(sum(applied) / len(parts), 1),
                            "moves": moves, "unchanged": unchanged, "protected": protected}
    if held:
        plan["held"] = held
    if chosen is not None:
        plan["anchor"] = chosen["name"]
    return plan


def band_decisions(master_bands: dict[str, float], tracks: list[dict[str, Any]],
                   genre: str | None = None, tolerance: float = 3.0,
                   previous: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Where to fix each band the master has too much / too little of.

    Args:
        master_bands: the master's 8 bands (dB of the total).
        tracks: ``[{name, role, bands_db, post_lufs}]`` (post-fader loudness).
        genre: compare with the genre's balance; default the mix targets of
            ``live_audio_analyze``.
        previous: the last run's ``{band: fix}`` on this set: near the line (top share
            33-47 %, 8-12 % for a part to count) a band keeps it — a share moving 34 -> 41 %
            between runs flipped the verdict, and the master EQ with it.

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
        last = (previous or {}).get(name)
        count_at, top_below = (0.08, 0.47) if last == "master" else \
            (0.12, 0.33) if last == "tracks" else (0.1, 0.4)
        spread = len([s for s, _t in ranked if s >= count_at]) >= 3 and bool(ranked) and \
            ranked[0][0] < top_below
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


# ----------------------------------------------------------------------------- channel strips

#: The LiveBridge Channel's parameters at rest (max_for_live/build.py CHANNEL_PARAMS): a
#: fresh or reset Channel leaves the sound alone.
CHANNEL_NEUTRAL: dict[str, float] = {
    "HP Freq": 20.0, "Low": 0.0, "Mid": 0.0, "Mid Freq": 1000.0, "High": 0.0,
    "Comp Threshold": 0.0, "Comp Ratio": 1.0, "Comp Attack": 10.0, "Comp Release": 150.0,
    "Comp Makeup": 0.0, "Saturation": 0.0, "Drive": 6.0, "Width": 100.0, "Bass Mono": 0.0,
    "Bass Mono Freq": 120.0, "Trim": 0.0}

#: role -> (high-pass Hz, crest target dB or None = leave the dynamics, ratio, attack ms,
#: release ms, bass mono). Attack lets the role's transient through (kick 20 ms), release
#: follows its decay.
STRIP_ROLES: dict[str | None, tuple[float, float | None, float, float, float, bool]] = {
    # kick and sub keep everything down to 20 Hz, bass and drums from 25 Hz: in this music the
    # 30-60 Hz weight is the point (a 30 Hz high-pass cost two released-master comparisons
    # ~1.7 dB of sub)
    "kick": (20.0, 11.0, 4.0, 20.0, 80.0, True),
    "snare": (100.0, 10.0, 4.0, 5.0, 120.0, False),
    "hats": (300.0, None, 2.0, 1.0, 60.0, False),
    "perc": (150.0, 11.0, 3.0, 5.0, 80.0, False),
    "drums": (25.0, 10.0, 3.0, 10.0, 100.0, True),
    "sub": (20.0, 6.0, 4.0, 10.0, 150.0, True),
    "bass": (25.0, 7.0, 4.0, 10.0, 120.0, True),
    "pad": (150.0, None, 2.0, 30.0, 300.0, False),
    "lead": (150.0, 9.0, 3.0, 10.0, 150.0, False),
    "keys": (120.0, 10.0, 2.5, 10.0, 150.0, False),
    "vocal": (90.0, 8.0, 4.0, 5.0, 100.0, False),
    "guitar": (90.0, 9.0, 3.0, 10.0, 120.0, False),
    "fx": (200.0, None, 2.0, 10.0, 200.0, False),
    None: (40.0, None, 2.0, 10.0, 150.0, False),
}
_MUDDY_ROLES = ("pad", "lead", "keys", "vocal", "guitar", "snare", "perc", "fx")
_AIRY_ROLES = ("vocal", "hats", "lead")


def channel_plan(role: str | None, reading: dict[str, Any] | None,
                 previous: dict[str, float] | None = None) -> dict[str, Any]:
    """``{values: {parameter: value}, reasons: [...]}`` for a LiveBridge Channel on a part.

    ``reading`` is the raw part (the Channel at rest): its bands, peak and crest decide the EQ
    and the compression; ``role`` decides the high-pass, timing and bass mono. Only parameters
    that move from :data:`CHANNEL_NEUTRAL` are in ``values``. ``previous`` is what LiveBridge
    set on this strip last time: each decision has hysteresis around it (a move that is on
    stays on unless the reading clearly leaves its range, one that is off needs a clear case),
    and a value within its jitter of the last one is kept — a rerun on the same mix changes
    nothing (a lead at crest 10.5 vs 10.9 around a 10.5 limit gained and lost a compressor;
    a threshold follows the window's peak, which swings up to 2 dB between runs).
    """
    hp, crest_target, ratio, attack, release, mono = STRIP_ROLES.get(role, STRIP_ROLES[None])
    last = previous or {}

    def was(name: str) -> bool:
        return name in last and last[name] != CHANNEL_NEUTRAL.get(name)

    def edge(base: float, on: bool, step: float = 1.0) -> float:
        """A threshold moved away from the last decision: easier to keep than to start."""
        return base - step if on else base + step

    def steady(name: str, value: float, jitter: float) -> float:
        # a threshold at the dial's 0 dB end is still a set compressor (it equals rest only
        # in number): whether the compressor was on is what counts
        on = was("Comp Ratio") if name == "Comp Threshold" else was(name)
        return float(last[name]) if on and name in last and \
            abs(float(last[name]) - value) < jitter else value

    values: dict[str, float] = {}
    reasons: list[str] = []
    label = role or "part"
    if hp > 20.0:
        values["HP Freq"] = hp
        reasons.append(f"high-pass at {hp:g} Hz: nothing a {label} needs lives below it")
    bands = ((reading or {}).get("spectrum") or {}).get("bands_db") or {}
    loud = (reading or {}).get("loudness") or {}
    if bands:
        mud = float(bands.get("mud", -90.0))
        presence = float(bands.get("presence", -90.0))
        boxy = max(float(bands.get("low_mid", -90.0)), mud)
        mid_at = float(last.get("Mid Freq", 0.0)) if was("Mid") else None
        if role in _MUDDY_ROLES and mud > edge(-6.0, mid_at == 350.0):
            cut = steady("Mid", -round(min(4.0, 1.5 + max(0.0, mud + 6.0) * 0.6), 1), 1.0)
            values.update({"Mid Freq": 350.0, "Mid": cut})
            reasons.append(f"{-mud:.0f} dB below total in 250-500 Hz is a lot for a {label}: "
                           f"{cut:g} dB at 350 Hz (mud)")
        elif role == "kick" and boxy > edge(-8.0, mid_at == 300.0):
            values.update({"Mid Freq": 300.0, "Mid": -3.0})
            reasons.append("boxy kick (strong 120-500 Hz): -3 dB at 300 Hz")
        elif role not in ("hats", "fx") and presence > edge(-4.0, mid_at == 3500.0):
            values.update({"Mid Freq": 3500.0, "Mid": -2.0})
            reasons.append("harsh 2-5 kHz: -2 dB at 3.5 kHz")
        if role in _AIRY_ROLES and float(bands.get("air", -90.0)) < -edge(25.0, was("High")) \
                and float(bands.get("high", -90.0)) < -edge(18.0, was("High")):
            values["High"] = 2.0
            reasons.append(f"dull top for a {label}: +2 dB high shelf (air)")
        if role in ("bass", "lead") and _share(bands, "presence", "high", "air") < (
                0.03 if was("Saturation") else 0.015):
            values["Saturation"] = 20.0
            reasons.append(f"almost nothing above 2 kHz: 20 % saturation so the {label} "
                           "speaks on small speakers")
    crest, peak = loud.get("crest_db"), loud.get("peak_db")
    if crest_target is not None and crest is not None and peak is not None and \
            crest > crest_target + (0.5 if was("Comp Ratio") else 2.5):
        reduce = max(0.5, min(crest - crest_target, 8.0))
        wanted = peak - reduce / (1.0 - 1.0 / ratio)
        threshold = steady("Comp Threshold", max(-40.0, min(0.0, wanted)), 2.0)
        # the dial ends at 0 dB: a part peaking far above 0 dBFS before its fader (fine in
        # floating point) gets its threshold below where it should be — more reduction than
        # planned. Say so, and how much
        clamped = None
        planned = reduce
        if wanted > 0.5 and threshold >= 0.0:
            got = max(0.0, peak - threshold) * (1.0 - 1.0 / ratio)
            if got > reduce + 0.5:
                clamped = got
                reduce = got
        values.update({"Comp Threshold": round(threshold, 1), "Comp Ratio": ratio,
                       "Comp Attack": attack, "Comp Release": release})
        # makeup only when the attack catches the peaks: a slow attack lets the transient
        # through, and makeup would push that peak higher (the faders restore the level)
        makeup = steady("Comp Makeup", round(reduce / 2.0, 1), 0.5) if attack <= 5.0 else 0.0
        if makeup:
            values["Comp Makeup"] = makeup
        reasons.append(f"crest {crest:g} dB vs ~{crest_target:g} for a {label}: {ratio:g}:1 "
                       f"from {threshold:.1f} dB (about {reduce:.1f} dB off the peaks), "
                       f"attack {attack:g} ms, release {release:g} ms"
                       + (f", makeup +{makeup:g} dB" if makeup else
                          " — no makeup: the attack lets the transient through, the fader "
                          "restores the level")
                       + (f". It peaks at {peak:+.1f} dBFS before its fader: the threshold "
                          f"stops at 0 dB, so about {clamped:.1f} dB comes off instead of "
                          f"{planned:.1f} — lower the instrument's output (or the clip gain) "
                          "until it peaks near 0 dBFS and run again" if clamped is not None
                          else ""))
    if mono:
        values["Bass Mono"] = 1.0
        reasons.append("bass mono below 120 Hz: the low end stays centred")
        if role == "sub":
            values["Width"] = 0.0
            reasons.append("sub fully mono")
    return {"values": values, "reasons": reasons}


# ----------------------------------------------------------------------------- clashes

#: (band, low Hz, high Hz) — the Engine's 8 bands.
BAND_EDGES = (("sub", 20, 60), ("bass", 60, 120), ("low_mid", 120, 250), ("mud", 250, 500),
              ("mid", 500, 2000), ("presence", 2000, 5000), ("high", 5000, 10000),
              ("air", 10000, 20000))


def clash_band(track: dict[str, Any], against: dict[str, Any], floor_db: float = -15.0
               ) -> dict[str, Any] | None:
    """The band where both parts are strong (each within ``floor_db`` of its own total):
    ``{band, hz (centre), q, track_db, against_db}`` — the louder of the two shares weakest —
    or None when they share nothing."""
    a = ((track.get("spectrum") or {}).get("bands_db")) or {}
    b = ((against.get("spectrum") or {}).get("bands_db")) or {}
    best = None
    for name, low, high in BAND_EDGES:
        if name not in a or name not in b:
            continue
        mine, theirs = float(a[name]), float(b[name])
        if mine < floor_db or theirs < floor_db:
            continue
        score = min(mine, theirs)
        if best is None or score > best[0]:
            centre = math.sqrt(low * high)
            best = (score, {"band": name, "hz": round(centre, 1),
                            "q": round(max(0.5, min(4.0, centre / (high - low))), 2),
                            "track_db": round(mine, 1), "against_db": round(theirs, 1)})
    return best[1] if best else None
