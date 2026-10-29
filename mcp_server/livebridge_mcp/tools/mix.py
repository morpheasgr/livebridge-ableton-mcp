"""Mixing and mastering by measurement: `live_sound_check` (does every track make a healthy
sound before you build on it), `live_mix_balance` (faders to role levels, measured with the
LiveBridge Engine, plus where each band problem belongs: a track or the master) and
`live_master_auto` (a master chain driven to a loudness target with guardrails).

The listening comes from LiveBridge Engine devices (:mod:`livebridge_mcp.engine`): one on the
master and one at the end of each track to balance. The decisions are in
:mod:`livebridge_mcp.mix`.
"""

from __future__ import annotations

import math
import re
import time
from typing import Any

from . import bridge_call, tool_error
from .. import engine, mix
from ..client import BridgeClient
from .engine import _HOW_TO_ADD, _engine_failure

_MAX_PASSES = 4
_MAX_STEPS = 8
_SETTLE_S = 1.0
_PLUGIN_LIMITER_RE = re.compile(r"limit|maxim|pro-l|ozone|\bl[123]\b|elevate|loudmax", re.I)


def _is_error(value: Any) -> bool:
    return isinstance(value, dict) and "error" in value and "type" in value


def _num(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def _reading(result: Any) -> dict[str, Any]:
    loud = (result or {}).get("loudness") or {}
    return {"lufs": loud.get("lufs"), "peak_db": loud.get("peak_db"),
            "crest_db": loud.get("crest_db")}


#: The Max device of chain="livebridge" (max_for_live/LiveBridge Master).
_MASTER_DEVICE = "LiveBridge Master"


def _chain_devices(kind: str) -> tuple[tuple[str, str], ...]:
    """(device name, class name or _MASTER_DEVICE) of a master chain, in order."""
    if kind == "livebridge":
        return ((_MASTER_DEVICE, _MASTER_DEVICE),)
    return (("EQ Eight", "Eq8"), ("Glue Compressor", "GlueCompressor"), ("Limiter", "Limiter"))


def register(mcp: Any, bridge: BridgeClient) -> None:
    """Register the mix tools on the MCP app."""

    def playing() -> bool | None:
        state = bridge_call(bridge, "transport.get")
        if isinstance(state, dict) and not _is_error(state):
            return bool(state.get("is_playing"))
        return None

    def faders(names: list[str]) -> dict[str, dict[str, Any]]:
        """name -> {volume (dB or None at -inf), mute} for the named tracks."""
        state = bridge_call(bridge, "mixer.get", {"track": names, "detail": "minimal"})
        found: dict[str, dict[str, Any]] = {}
        if isinstance(state, dict) and isinstance(state.get("tracks"), list):
            for name, entry in zip(names, state["tracks"]):
                if isinstance(entry, dict):
                    found[name] = {"volume": _num(entry.get("volume")),
                                   "mute": bool(entry.get("mute"))}
        return found

    def listen_all(devices: list[dict[str, Any]], seconds: float) -> list[Any]:
        return engine.each(devices, lambda d: engine.listen(d, seconds))

    # ------------------------------------------------------------------ sound check
    @mcp.tool()
    def live_sound_check(
        tracks: list[str] | None = None,
        seconds: float = 6.0,
        roles: dict[str, str] | None = None,
    ) -> Any:
        """Pre-flight before building an arrangement on the parts (and whenever something is
        missing, thin or inaudible): for every track, does it make sound, and is the sound
        healthy? Checks the chain (a MIDI track without an instrument, a muted track, another
        track soloed), each part's level while the song plays, a patch strangled by its filter
        (one band holding nearly all the energy, nothing above 2 kHz, no transients where there
        should be) and every filter cutoff against the working floor of the part's role (a lead
        low-passed at 400 Hz). The role comes from the name, else from the sound. Read-only.

        Args:
            tracks: track names (default: every regular track).
            seconds: listening window while the song plays (2..20) — loop a section where all
                parts play.
            roles: correct a guessed role: `{"Audio 3": "hats"}` (kick, snare, hats, perc,
                drums, sub, bass, pad, lead, keys, vocal, guitar, fx).

        Returns:
            `{playing, tracks:[{name, type, role, role_from, signal: ok|quiet|silent|unknown,
            lufs?, peak_db?, health:[...], filters:[{device, parameter, hz, type, caps_top,
            below_floor?}], problems:[...]}], order (loudest first), notes}`.

        Gotchas: levels need LiveBridge Engine devices at the end of the tracks (pre-fader);
        without them the tool still checks chains and filters, and reads Live's momentary
        meters for signal. A silent track: never raise its fader first — the `problems` list
        says where to look.
        """
        cmd = "mix.sound_check"
        if not 2 <= seconds <= 20:
            return tool_error("seconds must be 2..20", cmd=cmd)
        bad = [r for r in (roles or {}).values() if r not in mix.ROLES]
        if bad:
            return tool_error(f"unknown role(s) {bad}; use one of {', '.join(mix.ROLES)}",
                              cmd=cmd)
        listing = bridge_call(bridge, "tracks.list", {"include_returns": False,
                                                      "include_master": False,
                                                      "detail": "summary"})
        if _is_error(listing) or not isinstance(listing, dict):
            return listing
        items = [t for t in listing.get("tracks") or [] if t.get("type") in ("midi", "audio")]
        if tracks:
            wanted = {n.lower() for n in tracks}
            items = [t for t in items if str(t.get("name", "")).lower() in wanted]
            if not items:
                return tool_error(f"none of {tracks} is a regular track", type="not_found",
                                  cmd=cmd)
        is_playing = playing()
        notes: list[str] = []
        state = bridge_call(bridge, "transport.get")
        if isinstance(state, dict) and state.get("back_to_arranger"):
            notes.append("'Back to Arrangement' is lit: tracks where a session clip was fired "
                         "ignore their arrangement clips — live_transport_set("
                         "back_to_arranger=false) before playing the arrangement.")
        soloed = [t["name"] for t in items if t.get("solo")]
        readings: dict[str, dict[str, Any]] = {}
        try:
            devices = [d for d in engine.devices() if d.get("role") == "track"]
        except engine.EngineError:
            devices = []
        names = {str(t.get("name", "")).lower() for t in items}
        devices = [d for d in devices if str(d.get("track") or "").lower() in names]
        if devices and is_playing:
            for device, result in zip(devices, listen_all(devices, seconds)):
                if not isinstance(result, engine.EngineError):
                    readings[str(device.get("track")).lower()] = result
        meters: dict[str, float] = {}
        if is_playing and len(readings) < len(items):
            end = time.time() + min(seconds, 4.0)
            while True:
                snap = bridge_call(bridge, "mixer.meters", {"include_cpu": False})
                entries = snap.get("tracks") if isinstance(snap, dict) else None
                for entry in entries if isinstance(entries, list) else []:
                    level = _num(entry.get("peak")) or _num(entry.get("level")) or 0.0
                    key = str(entry.get("name", "")).lower()
                    meters[key] = max(meters.get(key, 0.0), level)
                if time.time() >= end:
                    break
                time.sleep(0.25)
        if not is_playing:
            notes.append("The transport is stopped: levels were not measured. Loop a section "
                         "where every part plays (live_transport_play) and check again.")
        elif not devices:
            notes.append("No LiveBridge Engine on these tracks: signal from Live's meters only "
                         "(no level, balance or patch health). " + _HOW_TO_ADD)
        results, order = [], []
        for track in items:
            name = str(track.get("name", ""))
            key = name.lower()
            reading = readings.get(key)
            role_info = mix.guess_role(name, reading)
            role = (roles or {}).get(name) or role_info["role"]
            entry: dict[str, Any] = {"name": name, "type": track.get("type"), "role": role,
                                     "role_from": "you" if (roles or {}).get(name)
                                     else role_info["from"]}
            problems: list[str] = []
            devs = track.get("devices") or []
            if track.get("type") == "midi" and not any(
                    d.get("type") in ("instrument",) or d.get("class_name") in (
                        "InstrumentGroupDevice", "DrumGroupDevice") for d in devs):
                problems.append("MIDI track without an instrument: it makes no sound.")
            if track.get("mute"):
                problems.append("The track is muted.")
            if soloed and not track.get("solo"):
                problems.append(f"Other tracks are soloed ({', '.join(soloed[:3])}): this one "
                                "is silenced by solo.")
            if any(d.get("class_name") == "DrumGroupDevice" for d in devs):
                problems.append("Drum Rack: notes on empty pads are silent — write only pads "
                                "the kit fills (live_drumrack_overview).")
            if reading is not None:
                loud = reading.get("loudness") or {}
                entry["lufs"] = loud.get("lufs")
                entry["peak_db"] = loud.get("peak_db")
                if reading.get("silent") or loud.get("lufs") is None:
                    entry["signal"] = "silent"
                elif loud["lufs"] < -40:
                    entry["signal"] = "quiet"
                else:
                    entry["signal"] = "ok"
                    order.append((loud["lufs"], name, role))
                entry["health"] = mix.patch_health(reading, role) \
                    if entry["signal"] != "silent" else []
            elif key in meters:
                entry["signal"] = "ok" if meters[key] > 0.02 else "silent"
                entry["health"] = []
            else:
                entry["signal"] = "unknown"
                entry["health"] = []
            if entry["signal"] == "silent" and is_playing:
                problems.append("Silent while the song plays: is a clip playing on this track "
                                "in this section (live_arrangement_clips / the session slot)? "
                                "Then the instrument, the drum pads, a closed filter, the "
                                "track's input/output routing.")
            elif entry["signal"] == "quiet":
                problems.append("Very quiet (below -40 LUFS): solo it and check — raise the "
                                "note velocities or the device output before the fader.")
            filters: list[dict[str, Any]] = []
            for device in devs[:12]:
                params: list[dict[str, Any]] = []
                for word in ("freq", "cutoff", "type"):
                    page = bridge_call(bridge, "devices.parameters",
                                       {"track": track.get("path") or name,
                                        "device": device.get("index", device.get("path")),
                                        "filter": word, "limit": 64, "detail": "minimal"})
                    if isinstance(page, dict) and not _is_error(page):
                        params += [p for p in page.get("parameters") or [] if p not in params]
                filters += mix.filter_cutoffs(str(device.get("name")), params, role)
            low = [f for f in filters if f.get("below_floor")]
            if low:
                worst = min(low, key=lambda f: f["hz"])
                problems.append(f"{worst['device']} '{worst['parameter']}' at {worst['hz']:g} "
                                f"Hz: below the ~{mix.FILTER_FLOORS[role]:g} Hz floor for a "
                                f"{role} — the sound is reduced to its fundamental. The lowest "
                                "low-pass in the chain wins: opening another filter won't fix "
                                "it.")
            entry["filters"] = filters
            entry["problems"] = problems
            results.append(entry)
        order.sort(key=lambda item: -item[0])
        answer: dict[str, Any] = {"playing": is_playing, "tracks": results,
                                  "order": [f"{n} ({r or '?'}) {l} LUFS" for l, n, r in order],
                                  "notes": notes}
        if order:
            ranks = {r: i for i, (_l, _n, r) in enumerate(order)}
            if "kick" in ranks and any(r in ranks and ranks[r] < ranks["kick"]
                                       for r in ("pad", "hats", "perc", "fx", "lead")):
                answer["notes"].append("Pre-fader, a pad/hats/lead/fx part is louder than the "
                                       "kick: expect a balance problem (live_mix_balance).")
        return answer

    # ------------------------------------------------------------------ balance
    @mcp.tool()
    def live_mix_balance(
        genre: str | None = None,
        tracks: list[str] | None = None,
        roles: dict[str, str] | None = None,
        anchor: str | None = None,
        seconds: float = 8.0,
        apply: bool = True,
        passes: int = 2,
        headroom_db: float = 6.0,
    ) -> Any:
        """Balance the mix by measurement: listens to every track with a LiveBridge Engine at
        its end over the same window, gives each part its role's level (kick on top, then sub
        and bass, snare, vocal, lead, keys, percussion, pads, hats, fx — or a genre's levels),
        sets the faders in real dB (one undo step per pass), measures again until the levels
        settle, then trims everything together so the master keeps `headroom_db` of headroom.
        With an Engine on the master it also says where each spectral problem belongs: on the
        master/bus EQ when 3+ tracks share it, on a track when 1-2 own it.

        Args:
            genre: role levels and band balance of a genre (techno, house, trance, dnb,
                dubstep, hip_hop, trap, pop, rnb, lo_fi, ambient, breaks, funk, jazz, metal,
                country, reggaeton, classical, balanced).
            tracks: only these tracks (default: every track device).
            roles: correct guessed roles: `{"Audio 3": "hats"}`.
            anchor: the track that keeps its level (default: the kick, else the loudest role).
            seconds: listening window (3..30) — loop the fullest section (the drop).
            apply: false = only report the plan.
            passes: measure/adjust rounds (1..4).
            headroom_db: wanted master peak below 0 dBFS (0..12; needs an Engine on the master).

        Returns:
            `{genre?, applied, passes, parts:[{name, role, role_from, lufs (pre-fader),
            fader_db, post_lufs}], plan:{anchor, moves:[{name, role, offset_lu, now_lufs,
            target_lufs, fader_db, new_fader_db, move_db}], unchanged}, master?:{lufs, peak_db,
            crest_db}, headroom_trim_db?, bands?:[{band, hz, delta_db, fix: master|tracks,
            tracks, advice}], notes}`.

        Gotchas: start playback first and loop the section to balance. Levels are integrated
        over the window (a part that plays only half of it reads ~3 dB low). Silent and muted
        tracks are left alone. Taste beats targets: say what moved and let the user listen.
        """
        cmd = "mix.balance"
        if not 3 <= seconds <= 30:
            return tool_error("seconds must be 3..30", cmd=cmd)
        if not 1 <= passes <= _MAX_PASSES:
            return tool_error(f"passes must be 1..{_MAX_PASSES}", cmd=cmd)
        if not 0 <= headroom_db <= 12:
            return tool_error("headroom_db must be 0..12", cmd=cmd)
        key = None
        if genre is not None:
            key = mix.genre_key(genre)
            if key is None:
                return tool_error("unknown genre; use one of " + ", ".join(mix.GENRES), cmd=cmd)
        bad = [r for r in (roles or {}).values() if r not in mix.ROLES]
        if bad:
            return tool_error(f"unknown role(s) {bad}; use one of {', '.join(mix.ROLES)}",
                              cmd=cmd)
        try:
            found = engine.devices()
            chosen = [d for d in engine.select(found, tracks, master=False)
                      if d.get("role") != "master"]
        except engine.EngineError as exc:
            return _engine_failure(exc, cmd)
        master = next((d for d in found if d.get("role") == "master"), None)
        if len(chosen) < 2:
            return tool_error("balancing needs LiveBridge Engine devices at the end of at "
                              "least 2 tracks. " + _HOW_TO_ADD, type="not_found", cmd=cmd)
        if playing() is False:
            return tool_error("the transport is stopped: loop the fullest section and start "
                              "playback (live_transport_play), then balance",
                              type="invalid_state", cmd=cmd)
        notes: list[str] = []
        names = [str(d.get("track") or "") for d in chosen]
        state = faders(names)
        active = []
        for device, name in zip(chosen, names):
            info = state.get(name)
            if info is None:
                notes.append(f"{name}: fader not readable — left out.")
            elif info["mute"]:
                notes.append(f"{name} is muted — left out.")
            elif info["volume"] is None:
                notes.append(f"{name} is at -inf dB — left out.")
            else:
                active.append((device, name))
        if len(active) < 2:
            return tool_error("fewer than 2 unmuted tracks to balance. " + " ".join(notes),
                              type="invalid_state", cmd=cmd)
        parts: list[dict[str, Any]] = []
        plan: dict[str, Any] = {"anchor": None, "moves": [], "unchanged": []}
        done = 0
        readings: dict[str, dict[str, Any]] = {}
        for _round in range(passes):
            done += 1
            results = listen_all([d for d, _n in active], seconds)
            state = faders([n for _d, n in active])
            parts = []
            for (device, name), result in zip(active, results):
                if isinstance(result, engine.EngineError):
                    notes.append(f"{name}: not measured ({result}).")
                    continue
                readings[name] = result
                lufs = (result.get("loudness") or {}).get("lufs")
                if result.get("silent") or lufs is None:
                    if f"{name} is silent" not in " ".join(notes):
                        notes.append(f"{name} is silent in this window — fader left alone "
                                     "(live_sound_check says why).")
                    continue
                guess = mix.guess_role(name, result)
                role = (roles or {}).get(name) or guess["role"]
                parts.append({"name": name, "role": role,
                              "role_from": "you" if (roles or {}).get(name) else guess["from"],
                              "lufs": float(lufs),
                              "fader_db": state.get(name, {}).get("volume") or 0.0})
            plan = mix.balance_plan([dict(p) for p in parts], key, anchor)
            if not plan["moves"] or not apply:
                break
            answer = bridge_call(bridge, "mixer.set_many", {"settings": [
                {"track": m["name"], "volume": f"{m['new_fader_db']:.1f} dB"}
                for m in plan["moves"]]})
            if _is_error(answer):
                return answer
            if _round + 1 < passes:
                time.sleep(_SETTLE_S)
        result_parts = [{"name": p["name"], "role": p["role"], "role_from": p["role_from"],
                         "lufs": round(p["lufs"], 1), "fader_db": round(p["fader_db"], 1),
                         "post_lufs": round(p["lufs"] + p["fader_db"], 1)} for p in parts]
        unknown = [p["name"] for p in parts if not p["role"]]
        if unknown:
            notes.append(f"Role unknown for {', '.join(unknown)} (balanced as a mid-level part): "
                         "pass roles={...} to place them.")
        answer: dict[str, Any] = {"applied": bool(apply), "passes": done,
                                  "parts": result_parts, "plan": plan, "notes": notes}
        if key:
            answer["genre"] = key
        if master is not None:
            try:
                reading = engine.listen(master, min(seconds, 6.0))
            except engine.EngineError as exc:
                notes.append(f"master Engine not read ({exc}).")
                reading = None
            if reading is not None and not reading.get("silent"):
                answer["master"] = _reading(reading)
                peak = (reading.get("loudness") or {}).get("peak_db")
                excess = (peak + headroom_db) if peak is not None else 0.0
                if excess > 0.5:
                    answer["headroom_trim_db"] = round(-excess, 1)
                    if apply:
                        current = faders([n for _d, n in active])
                        settings = [{"track": n, "volume":
                                     f"{current[n]['volume'] - excess:.1f} dB"}
                                    for _d, n in active
                                    if current.get(n, {}).get("volume") is not None]
                        trimmed = bridge_call(bridge, "mixer.set_many", {"settings": settings})
                        if _is_error(trimmed):
                            notes.append(f"headroom trim failed: {trimmed.get('error')}")
                        else:
                            notes.append(f"Master peaked at {peak} dB: every balanced fader "
                                         f"went down {excess:.1f} dB for {headroom_db:g} dB "
                                         "headroom (the balance is kept).")
                    else:
                        notes.append(f"Master peaks at {peak} dB: lower every fader "
                                     f"{excess:.1f} dB for {headroom_db:g} dB headroom.")
                tracks_post = [{"name": p["name"], "role": p["role"],
                                "bands_db": (readings[p["name"]].get("spectrum") or {})
                                .get("bands_db") or {},
                                "post_lufs": p["lufs"] + p["fader_db"]} for p in parts]
                bands = (reading.get("spectrum") or {}).get("bands_db") or {}
                if bands and tracks_post:
                    answer["bands"] = mix.band_decisions(bands, tracks_post, key)
        else:
            notes.append("No Engine on the master: headroom and the track-or-master band check "
                         "were skipped.")
        return answer

    # ------------------------------------------------------------------ master
    @mcp.tool()
    def live_master_auto(
        destination: str = "streaming",
        lufs: float | None = None,
        ceiling: float | None = None,
        genre: str | None = None,
        seconds: float = 6.0,
        apply: bool = True,
        loud_bar: float | None = None,
        quiet_bar: float | None = None,
        max_gain_db: float = 18.0,
        chain: str = "native",
    ) -> Any:
        """Master the song to a loudness target by measurement: uses (or builds) EQ Eight ->
        Glue Compressor -> Limiter (or the LiveBridge Master device, `chain="livebridge"`) on
        the master, ahead of the LiveBridge Engine, sets the
        limiter ceiling, then raises the limiter gain step by step, measuring integrated LUFS,
        peak and crest factor on the master each time, until the target is reached. Stops early
        when the punch collapses (crest factor below the destination's minimum) and, with
        `loud_bar`/`quiet_bar`, when the drop and the breakdown are being squashed together.
        Never stacks a second limiter on a chain that already has one from a plug-in.

        Args:
            destination: "streaming"/"spotify"/"youtube" (-14 LUFS, -1 dB ceiling), "apple"
                (-16), "soundcloud" (-10, -0.3), "club" (-8, -0.3), "demo" (-12).
            lufs: explicit integrated target in LUFS (overrides the destination's).
            ceiling: limiter ceiling in dB (default from the destination).
            genre: with destination="genre": master to that genre's typical loudness
                (techno -8, trance -8.5, pop -12, lo_fi -14, ambient -18 ...).
            seconds: listening window per step (3..20) — loop the loudest section.
            apply: false = measure and report the plan only.
            loud_bar, quiet_bar: bars of the loudest and quietest sections (1-based): the
                tool plays each before and after, and backs the limiter off when their
                difference shrinks by more than 2 LU.
            max_gain_db: the most limiter gain it may use (0..24).
            chain: "native" (EQ Eight, Glue Compressor, Limiter) or "livebridge": one
                LiveBridge Master device (Ableton's Channel EQ, Compressor, Limiter and Utility
                DSP in a single Max device, Max 9 / Live 12.2+: EQ Low Cut, Comp Ratio/Attack,
                Limit Gain/Ceiling/Mode, Width, Bass Mono... all Live parameters).

        Returns:
            `{destination, target_lufs, ceiling_db, applied, chain, inserted, before:{lufs,
            peak_db, crest_db}, after:{...}, limiter_gain_db, steps:[{gain_db, lufs, peak_db,
            crest_db}], contrast?:{before_lu, after_lu}, stopped?, notes, manual}`.

        Gotchas: needs an Engine on the master (it measures after the limiter) and the song
        playing (or loud_bar to start it). One master can't serve both worlds: a club master
        gets turned down on Spotify with its transients crushed — run it once per destination.
        Final judgement needs ears; the tool reports numbers, not taste.
        """
        cmd = "mix.master_auto"
        dest = destination.strip().lower()
        if dest == "genre":
            key = mix.genre_key(genre)
            if key is None:
                return tool_error("destination='genre' needs genre= (one of "
                                  + ", ".join(mix.GENRES) + ")", cmd=cmd)
            target, ceil, min_crest = mix.GENRES[key]["lufs"], -0.3, 7.0
            if target <= -12:
                ceil, min_crest = -1.0, 9.0
        elif dest in mix.DESTINATIONS:
            target, ceil, min_crest = mix.DESTINATIONS[dest]
        else:
            return tool_error("destination must be one of " + ", ".join(mix.DESTINATIONS)
                              + " or 'genre'", cmd=cmd)
        if lufs is not None:
            if not -30 <= lufs <= -4:
                return tool_error("lufs must be -30..-4", cmd=cmd)
            target = float(lufs)
        if ceiling is not None:
            if not -6 <= ceiling <= 0:
                return tool_error("ceiling must be -6..0 dB", cmd=cmd)
            ceil = float(ceiling)
        if not 3 <= seconds <= 20:
            return tool_error("seconds must be 3..20", cmd=cmd)
        if not 0 <= max_gain_db <= 24:
            return tool_error("max_gain_db must be 0..24", cmd=cmd)
        if chain not in ("native", "livebridge"):
            return tool_error("chain must be 'native' or 'livebridge'", cmd=cmd)
        chain_kind = chain
        if (loud_bar is None) != (quiet_bar is None):
            return tool_error("pass both loud_bar and quiet_bar (or neither)", cmd=cmd)
        for bar in (loud_bar, quiet_bar):
            if bar is not None and bar < 1:
                return tool_error("bars are 1-based (>= 1)", cmd=cmd)
        try:
            device = engine.choose(engine.devices(), "master")
        except engine.EngineError as exc:
            return _engine_failure(exc, cmd)
        if device.get("role") != "master":
            return tool_error("no LiveBridge Engine on the master track. " + _HOW_TO_ADD,
                              type="not_found", cmd=cmd)
        state = bridge_call(bridge, "transport.get")
        if _is_error(state):
            return state
        was_playing = bool(state.get("is_playing"))
        beats_per_bar = 4.0
        match = re.match(r"^(\d+)/(\d+)$", str(state.get("signature", "")))
        if match and int(match.group(2)):
            beats_per_bar = int(match.group(1)) * 4.0 / int(match.group(2))
        if not was_playing and loud_bar is None:
            return tool_error("the transport is stopped: loop the loudest section and start "
                              "playback (or pass loud_bar/quiet_bar)", type="invalid_state",
                              cmd=cmd)
        notes: list[str] = []
        manual: list[str] = []

        def measure(bar: float | None = None) -> dict[str, Any]:
            if bar is not None:
                bridge_call(bridge, "transport.play",
                            {"position": (bar - 1.0) * beats_per_bar})
                time.sleep(_SETTLE_S)
            return engine.listen(device, seconds, kind="mix")

        def contrast() -> float | None:
            if loud_bar is None or quiet_bar is None:
                return None
            loud = _reading(measure(loud_bar))["lufs"]
            quiet = _reading(measure(quiet_bar))["lufs"]
            if loud is None or quiet is None:
                return None
            return round(loud - quiet, 1)

        def chain() -> list[dict[str, Any]]:
            listing = bridge_call(bridge, "devices.list", {"track": "master"})
            return list(listing.get("devices") or []) if isinstance(listing, dict) and \
                not _is_error(listing) else []

        def find(devices: list[dict[str, Any]], class_name: str) -> dict[str, Any] | None:
            if class_name == _MASTER_DEVICE:
                return next((d for d in devices
                             if _MASTER_DEVICE.lower() in str(d.get("name", "")).lower()), None)
            return next((d for d in devices if d.get("class_name") == class_name), None)

        def load_master_device(at: int) -> Any:
            """LiveBridge Master from the User Library onto the master, moved to ``at``."""
            loaded = bridge_call(bridge, "browser.load", {
                "query": _MASTER_DEVICE, "category": "audio_effect", "track": "master"},
                timeout=60.0)
            if _is_error(loaded):
                if loaded.get("type") == "not_found":
                    loaded["error"] += (" — is the LiveBridge Master device installed (the "
                                        "installer puts it in the User Library) and Max for "
                                        "Live (Max 9) available?")
                return loaded
            if at >= 0:
                placed = find(chain(), _MASTER_DEVICE)
                if placed is not None and placed.get("index") != at:
                    moved = bridge_call(bridge, "devices.move", {
                        "track": "master", "device": placed["index"], "position": at})
                    if _is_error(moved):
                        return moved
            return loaded

        try:
            before_contrast = contrast()
            before = measure(loud_bar)
        except engine.EngineError as exc:
            return _engine_failure(exc, cmd)
        if before.get("silent"):
            return tool_error("the master is silent in this window: play the loudest section",
                              type="invalid_state", cmd=cmd)
        devices = chain()
        limiter = find(devices, "Limiter" if chain_kind == "native" else _MASTER_DEVICE)
        foreign = [d for d in devices if d.get("type") == "audio_effect" and
                   d.get("class_name") in ("PluginDevice", "AuPluginDevice") and
                   _PLUGIN_LIMITER_RE.search(str(d.get("name")))]
        result: dict[str, Any] = {"destination": dest, "target_lufs": target,
                                  "ceiling_db": ceil, "applied": False,
                                  "chain": [d.get("name") for d in devices], "inserted": [],
                                  "before": _reading(before), "notes": notes,
                                  "manual": manual}
        if chain_kind == "livebridge" and limiter is None and find(devices, "Limiter"):
            return tool_error("the master already has Live's Limiter: use chain='native' (or "
                              "remove it) — two limiters would fight", type="invalid_state",
                              cmd=cmd)
        if foreign and limiter is None:
            notes.append(f"The master already has a plug-in limiter ({foreign[0]['name']}): "
                         "no second limiter added. Raise its gain until the master reads "
                         f"{target} LUFS (live_engine_listen) with its ceiling at {ceil} dB.")
            result["needed_gain_db"] = round(target - (before["loudness"]["lufs"] or target), 1)
            return result
        lufs_now = (before.get("loudness") or {}).get("lufs")
        if not apply:
            result["needed_gain_db"] = round(target - lufs_now, 1) if lufs_now is not None \
                else None
            missing = [n for n, c in _chain_devices(chain_kind) if not find(devices, c)]
            if missing:
                notes.append("Would insert " + ", ".join(missing) + " before the Engine.")
            return result

        # ------------------------------------------------------------- build the chain
        engine_at = next((i for i, d in enumerate(devices)
                          if "livebridge engine" in str(d.get("name", "")).lower()), None)
        if engine_at is not None and engine_at != len(devices) - 1:
            notes.append("The Engine is not last on the master: it measures before the "
                         "devices after it.")
        for name, class_name in _chain_devices(chain_kind):
            if find(devices, class_name):
                continue
            if class_name not in ("Limiter", _MASTER_DEVICE) and limiter is not None:
                continue                      # the user's own chain: keep it as it is
            at = engine_at if engine_at is not None else -1
            if class_name == _MASTER_DEVICE:
                added = load_master_device(at)
            else:
                added = bridge_call(bridge, "devices.insert", {"name": name, "track": "master",
                                                               "index": at})
            if _is_error(added):
                return added
            result["inserted"].append(name)
            if engine_at is not None:
                engine_at += 1
            devices = chain()
            if class_name == "Eq8":
                low_cut = bridge_call(bridge, "devices.set_parameters", {
                    "track": "master", "device": find(devices, "Eq8")["index"],
                    "values": {"1 Filter On A": "On", "1 Filter Type A": "Low Cut 48",
                               "1 Frequency A": "25 Hz"}})
                if _is_error(low_cut):
                    manual.append("EQ Eight: band 1 Low Cut 48 at 25 Hz (not set: "
                                  f"{low_cut.get('error')}).")
            elif class_name == "GlueCompressor":
                glue = bridge_call(bridge, "devices.set_parameters", {
                    "track": "master", "device": find(devices, "GlueCompressor")["index"],
                    "values": {"Ratio": "2", "Attack": "10", "Release": "Auto"}})
                if _is_error(glue):
                    manual.append("Glue Compressor: ratio 2:1, attack 10 ms, release Auto "
                                  f"(not set: {glue.get('error')}).")
                manual.append("Glue Compressor: lower Threshold until it shows 1-3 dB of gain "
                              "reduction on the loudest section (the meter is not readable "
                              "through Live's API).")
            elif class_name == _MASTER_DEVICE:
                ready = bridge_call(bridge, "devices.set_parameters", {
                    "track": "master", "device": find(devices, _MASTER_DEVICE)["index"],
                    "values": {"EQ Low Cut": "on", "Comp Ratio": 2.0,
                               "Comp Auto Release": "on", "Limit Mode": "True peak"}})
                if _is_error(ready):
                    manual.append("LiveBridge Master: EQ Low Cut on, Comp Ratio 2, Limit Mode "
                                  f"True peak (not set: {ready.get('error')}).")
                manual.append("LiveBridge Master: lower Comp Threshold until the loudest "
                              "section shows 1-3 dB of gain reduction (open the device; the "
                              "meter is not readable through Live's API).")
        result["chain"] = [d.get("name") for d in devices]
        limiter = find(devices, "Limiter" if chain_kind == "native" else _MASTER_DEVICE)
        if limiter is None:
            return tool_error("no limiter on the master after inserting it", type="internal",
                              cmd=cmd)
        index = limiter["index"]
        gain_name, ceiling_name = ("Gain", "Ceiling") if chain_kind == "native" else (
            "Limit Gain", "Limit Ceiling")

        def set_limiter(values: dict[str, str]) -> Any:
            return bridge_call(bridge, "devices.set_parameters",
                               {"track": "master", "device": index, "values": values})

        def limiter_gain() -> float:
            got = bridge_call(bridge, "devices.get_parameter",
                              {"track": "master", "device": index, "parameter": gain_name})
            if isinstance(got, dict) and not _is_error(got):
                parsed = re.match(r"^\s*(-?\d+(?:\.\d+)?)", str(got.get("display", "")))
                if parsed:
                    return float(parsed.group(1))
            return 0.0

        answer = set_limiter({ceiling_name: f"{ceil} dB"})
        if _is_error(answer):
            return answer
        result["applied"] = True
        gain = limiter_gain()
        steps: list[dict[str, Any]] = []
        stopped = None
        current = before if loud_bar is None else measure(loud_bar)
        start_crest = _reading(current)["crest_db"]
        # headroom first: with the limiter idle the mix should peak around -6 dBFS and sit
        # below the target, so the limiter (not the faders) makes the loudness
        now = _reading(current)
        if gain < 0.1 and now["peak_db"] is not None and now["lufs"] is not None:
            trim = max(now["peak_db"] + 6.0 if now["peak_db"] > -3.0 else 0.0,
                       now["lufs"] - target + 2.0 if now["lufs"] > target else 0.0)
            if trim > 0.5:
                listing = bridge_call(bridge, "mixer.get", {"detail": "minimal"})
                settings = []
                if isinstance(listing, dict):
                    for entry in listing.get("tracks") or []:
                        volume = _num(entry.get("volume")) if isinstance(entry, dict) else None
                        if volume is not None and (entry.get("path") or entry.get("name")):
                            settings.append({"track": entry.get("path") or entry["name"],
                                             "volume": f"{max(volume - trim, -70.0):.1f} dB"})
                if settings:
                    trimmed = bridge_call(bridge, "mixer.set_many", {"settings": settings})
                    if _is_error(trimmed):
                        return trimmed
                    result["headroom_trim_db"] = round(-trim, 1)
                    notes.append(f"The mix peaked at {now['peak_db']} dB / {now['lufs']} LUFS "
                                 f"before the limiter: every track fader went down "
                                 f"{trim:.1f} dB (balance kept) so the limiter has room.")
                    time.sleep(_SETTLE_S)
                    current = measure(None if loud_bar is None else loud_bar)
        for _step in range(_MAX_STEPS):
            now = _reading(current)
            if now["lufs"] is None:
                stopped = "the master went silent while measuring"
                break
            delta = target - now["lufs"]
            if abs(delta) <= 0.5:
                break
            new_gain = max(0.0, min(max_gain_db, gain + max(-6.0, min(6.0, delta))))
            if abs(new_gain - gain) < 0.1:
                stopped = (f"limiter gain at its limit ({max_gain_db:g} dB) — the mix needs "
                           "more level before the master" if delta > 0 else
                           "already louder than the target with the limiter at 0 dB gain: "
                           "turn the mix down")
                break
            answer = set_limiter({gain_name: f"{new_gain:.1f} dB"})
            if _is_error(answer):
                return answer
            gain = new_gain
            time.sleep(_SETTLE_S)
            try:
                current = measure(None if loud_bar is None else loud_bar)
            except engine.EngineError as exc:
                return _engine_failure(exc, cmd)
            reading = _reading(current)
            steps.append({"gain_db": round(gain, 1), **reading})
            # punch collapses: below the destination's minimum AND clearly below what the mix
            # had without limiting (a mix that is dense by nature is not the limiter's fault)
            if reading["crest_db"] is not None and reading["crest_db"] < min_crest and (
                    start_crest is None or reading["crest_db"] < start_crest - 1.5):
                back = max(0.0, gain - 1.5)
                set_limiter({gain_name: f"{back:.1f} dB"})
                gain = back
                stopped = (f"crest factor fell to {reading['crest_db']} dB (below "
                           f"{min_crest:g} for {dest}): the limiter was backed off 1.5 dB — "
                           "more loudness would cost the punch. Fix it in the mix (kick/bass "
                           "balance, fewer layers in the loud section).")
                time.sleep(_SETTLE_S)
                current = measure(None if loud_bar is None else loud_bar)
                break
        result["limiter_gain_db"] = round(gain, 1)
        if stopped:
            result["stopped"] = stopped
        result["steps"] = steps
        if before_contrast is not None:
            after_contrast = contrast()
            result["contrast"] = {"before_lu": before_contrast, "after_lu": after_contrast}
            if after_contrast is not None and after_contrast < before_contrast - 2.0:
                shrink = before_contrast - after_contrast
                back = max(0.0, gain - shrink)
                set_limiter({gain_name: f"{back:.1f} dB"})
                result["limiter_gain_db"] = round(back, 1)
                notes.append(f"The drop and the quiet section moved {shrink:.1f} LU closer: the "
                             f"limiter was backed off to {back:.1f} dB so the drop still "
                             "lands.")
                current = measure(loud_bar)
        result["after"] = _reading(current)
        if not was_playing:
            bridge_call(bridge, "transport.stop")
        after_lufs = result["after"]["lufs"]
        if after_lufs is not None and abs(after_lufs - target) > 1.0 and "stopped" not in \
                result:
            notes.append(f"Ended at {after_lufs} LUFS, {round(after_lufs - target, 1)} from the "
                         "target.")
        manual.append("Export is yours: File > Export Audio/Video, 24-bit WAV (dither only for "
                      "16-bit).")
        return result

    # ------------------------------------------------------------------ channel strips
    @mcp.tool()
    def live_mix_auto(
        genre: str | None = None,
        tracks: list[str] | None = None,
        roles: dict[str, str] | None = None,
        seconds: float = 6.0,
        apply: bool = True,
        balance: bool = True,
        master: str | None = None,
        master_chain: str = "native",
    ) -> Any:
        """Mix every part by measurement: on each track with a LiveBridge Channel (a channel
        strip from Ableton's DSP in front of the Engine's meters: high-pass, EQ, compressor,
        saturation, width/bass mono, trim — all Live parameters), it sets the strip to rest,
        listens to the raw part, guesses its role, then sets the strip for that role: high-pass,
        mud / harshness cuts and air from the measured bands, compression to the role's crest
        factor, bass mono on the low end, a little saturation on a dull bass or lead. Then it
        listens again and reports before/after with the reason for every move. With `balance`
        it then sets the faders (`live_mix_balance`), with `master` it masters
        (`live_master_auto`).

        Args:
            genre: for the balance and master steps (techno, house, pop ... see
                live_mix_balance).
            tracks: only these tracks (default: every track with a Channel).
            roles: correct a guessed role: `{"Audio 3": "hats"}` (kick, snare, hats, perc,
                drums, sub, bass, pad, lead, keys, vocal, guitar, fx).
            seconds: listening window (3..20) — loop the fullest section (the drop).
            apply: false = listen to the strips as they are and report the plan only.
            balance: then balance the faders by role (needs 2+ Engines/Channels).
            master: then master to this destination ("streaming", "club", "apple", "genre"
                ... see live_master_auto; needs an Engine on the master). None = skip.
            master_chain: "native" or "livebridge" (the LiveBridge Master device).

        Returns:
            `{applied, tracks:[{name, role, role_from, before:{lufs, peak_db, crest_db},
            after?, values:{parameter: value}, reasons:[...]}], balance?, master?, notes}`.

        Gotchas: the song must play (loop the fullest section). The Channel replaces the
        Engine on a track (it has the meters) — not both. Rules of thumb, not taste: report
        the reasons, let the user listen, and undo or set a single parameter
        (`live_device_set_parameter(device="LiveBridge Channel", ...)`) where they disagree.
        Needs Max 9 (Ableton DSP objects).
        """
        cmd = "mix.auto"
        if not 3 <= seconds <= 20:
            return tool_error("seconds must be 3..20", cmd=cmd)
        if master_chain not in ("native", "livebridge"):
            return tool_error("master_chain must be 'native' or 'livebridge'", cmd=cmd)
        bad = [r for r in (roles or {}).values() if r not in mix.ROLES]
        if bad:
            return tool_error(f"unknown role(s) {bad}; use one of {', '.join(mix.ROLES)}",
                              cmd=cmd)
        try:
            found = engine.devices()
        except engine.EngineError as exc:
            return _engine_failure(exc, cmd)
        channels = [d for d in found if d.get("strip") and d.get("role") == "track"]
        if tracks:
            wanted = {t.lower() for t in tracks}
            channels = [d for d in channels if str(d.get("track") or "").lower() in wanted]
        if not channels:
            return tool_error(
                "no LiveBridge Channel on " + ("these tracks" if tracks else "any track")
                + ": live_browser_load(query=\"LiveBridge Channel\", category=\"audio_effect\", "
                "track=<name>) on each part (it ends the chain; remove a LiveBridge Engine "
                "there — the Channel has the meters). Needs Max 9.", type="not_found", cmd=cmd)
        if playing() is False:
            return tool_error("the transport is stopped: loop the fullest section and start "
                              "playback (live_transport_play), then mix", type="invalid_state",
                              cmd=cmd)
        notes: list[str] = []
        names = [str(d.get("track") or "") for d in channels]

        def strip_index(name: str) -> int | None:
            listing = bridge_call(bridge, "devices.list", {"track": name})
            for device in (listing.get("devices") or []) if isinstance(listing, dict) else []:
                if "livebridge channel" in str(device.get("name", "")).lower():
                    return device.get("index")
            return None

        def set_strip(name: str, index: int, values: dict[str, float]) -> Any:
            return bridge_call(bridge, "devices.set_parameters",
                               {"track": name, "device": index, "values": values})

        indexes = {name: strip_index(name) for name in names}
        missing = [n for n, i in indexes.items() if i is None]
        if missing:
            notes.append("Channel not found in the device list of " + ", ".join(missing)
                         + " (renamed?): left out.")
        pairs = [(d, n) for d, n in zip(channels, names) if indexes[n] is not None]
        if apply:
            for _device, name in pairs:          # at rest: measure the raw part
                answer = set_strip(name, indexes[name], dict(mix.CHANNEL_NEUTRAL))
                if _is_error(answer):
                    return answer
            time.sleep(_SETTLE_S)
        else:
            notes.append("apply=false: the plan is based on the strips as they are now — if "
                         "they already process, it is not a plan for the raw parts.")
        before = listen_all([d for d, _n in pairs], seconds)
        results: list[dict[str, Any]] = []
        for (device, name), reading in zip(pairs, before):
            if isinstance(reading, engine.EngineError):
                results.append({"name": name, "error": str(reading)})
                continue
            role_info = mix.guess_role(name, reading)
            role = (roles or {}).get(name) or role_info["role"]
            entry: dict[str, Any] = {"name": name, "role": role,
                                     "role_from": "you" if (roles or {}).get(name)
                                     else role_info["from"], "before": _reading(reading)}
            if reading.get("silent"):
                entry.update(values={}, reasons=["silent in this window: left at rest"])
            else:
                plan = mix.channel_plan(role, reading)
                entry.update(values=plan["values"], reasons=plan["reasons"])
                if apply and plan["values"]:
                    answer = set_strip(name, indexes[name], plan["values"])
                    if _is_error(answer):
                        entry["error"] = answer.get("error")
            results.append(entry)
        result: dict[str, Any] = {"applied": bool(apply), "tracks": results, "notes": notes}
        if apply:
            time.sleep(_SETTLE_S)
            live = [(d, n) for d, n in pairs
                    if any(r.get("name") == n and "error" not in r for r in results)]
            for (_device, name), reading in zip(live, listen_all([d for d, _n in live],
                                                                   seconds)):
                if not isinstance(reading, engine.EngineError):
                    next(r for r in results if r.get("name") == name)["after"] = \
                        _reading(reading)
            if balance and len(pairs) >= 2:
                result["balance"] = live_mix_balance(
                    genre=genre, tracks=None if not tracks else names,
                    roles={r["name"]: r["role"] for r in results if r.get("role")},
                    seconds=max(3.0, seconds))
            if master:
                result["master"] = live_master_auto(destination=master, genre=genre,
                                                    seconds=max(3.0, seconds),
                                                    chain=master_chain)
        return result
