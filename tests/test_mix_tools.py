"""Mixing and mastering by measurement (mix.py, tools/mix.py), the melody check
(theory.melody_check, live_theory_melody) and genre balance in live_audio_analyze.

The LiveBridge Engine devices are faked at the request level: each track device reads a fixed
pre-fader signal, the master's reading is the sum of the tracks after their faders and the
stub Limiter (gain, ceiling) — so the tools' measure/adjust loops really converge.
"""

import math
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "mcp_server"))

from livebridge_mcp import engine, mix, theory  # noqa: E402
from livebridge_mcp.client import BridgeClient  # noqa: E402
from livebridge_mcp.server import create_app  # noqa: E402
from livebridge_mcp.tools import mix as mix_tools  # noqa: E402

from test_consistency import call_tool  # noqa: E402

from live_stub import factory  # noqa: E402

BANDS = ("sub", "bass", "low_mid", "mud", "mid", "presence", "high", "air")


def shares(**weights):
    total = sum(weights.values())
    return {b: weights.get(b, 0.0) / total for b in BANDS}


KICK = shares(sub=5, bass=4, low_mid=0.6, mid=0.3, presence=0.1)
BASS = shares(sub=3, bass=5, low_mid=1.5, mud=0.4, mid=0.2)
PAD = shares(low_mid=1, mud=2, mid=4, presence=2, high=1, air=0.3)
HATS = shares(presence=1, high=4, air=3)


def bands_db(share):
    return {b: (10 * math.log10(v) if v > 0 else -90.0) for b, v in share.items()}


def frames_for(lufs, crest_db, share, width=0.1, count=20):
    """Frames whose summary reads `lufs`, `crest_db` and the band shares."""
    if lufs is None:
        p, peak = 0.0, 0.0
    else:
        p = 10 ** ((lufs + 0.691) / 10)
        peak = math.sqrt(p) * 10 ** (crest_db / 20)
    values = {"ms_left": p, "ms_right": p, "peak_left": peak, "peak_right": peak, "ms_k": p,
              "ms_mid": p * (1 - width), "ms_side": p * width, "mean_lr": p * 0.9,
              "ms_mid_low": p * 0.5, "ms_side_low": 0.0}
    for band, value in share.items():
        values["band_" + band] = value * p
    return [[i * 100.0] + [values[f] for f in engine.FIELDS] for i in range(count)]


def fader_db(track):
    from LiveBridge.handlers.mixer import volume_to_db
    db = volume_to_db(track.mixer_device.volume.value)
    return -120.0 if db == "-inf" else float(db)


class FakeEngines:
    """Track devices with a fixed pre-fader signal; a master device summing them."""

    def __init__(self, song, parts, master=True):
        self.song = song
        self.parts = parts                    # name -> (lufs, crest_db, share)
        self.master = master
        self.requests = []
        self.quiet_from = None                # beats: from here on only the pad plays
        self.broken = False                   # a master device whose DSP does not run

    def devices(self, timeout=1.5, clean=True, kind="engine"):
        if kind != "engine":
            return []
        found = [{"role": "track", "track": name, "port": 1, "token": "t",
                  "instance": name} for name in self.parts]
        if self.master:
            found.insert(0, {"role": "master", "track": None, "port": 1, "token": "t",
                             "instance": "m"})
        return found

    def track(self, name):
        return next(t for t in self.song.tracks if t.name == name)

    def limiter(self):
        for device in self.song.master_track.devices:
            params = {p.name: p.value for p in device.parameters}
            if device.class_name == "Limiter" and "Gain" in params:
                return params["Gain"], params["Ceiling"]
            if device.name == "LiveBridge Master":
                if self.broken:
                    return 0.0, 0.0
                # the compressor's makeup drives the limiter like its own gain
                return params["Limit Gain"] + params.get("Comp Makeup", 0.0), \
                    params["Limit Ceiling"]
        return 0.0, 0.0

    def output(self):
        for device in self.song.master_track.devices:
            if device.name == "LiveBridge Master" and not self.broken:
                return {p.name: p.value for p in device.parameters}.get("Output", 0.0)
        return 0.0

    def master_reading(self):
        power = 0.0
        band_power = dict.fromkeys(BANDS, 0.0)
        crest_weighted = 0.0
        for name, (lufs, crest, share) in self.parts.items():
            track = self.track(name)
            if lufs is None or track.mute:
                continue
            if self.quiet_from is not None and \
                    self.song.current_song_time >= self.quiet_from and name != "Pad":
                continue
            p = 10 ** ((lufs + fader_db(track) + 0.691) / 10)
            power += p
            crest_weighted += p * crest
            for band in BANDS:
                band_power[band] += p * share[band]
        lufs = -0.691 + 10 * math.log10(power)
        crest = crest_weighted / power
        gain, ceiling = self.limiter()
        peak = lufs + crest + gain
        squash = max(0.0, peak - ceiling)           # what the limiter shaves off the peaks
        lufs += gain - 0.6 * squash
        crest = min(peak, ceiling) - lufs
        lufs += self.output()
        return lufs, crest, {b: v / power for b, v in band_power.items()}

    def request(self, device, cmd, args=None, timeout=5.0):
        self.requests.append((device.get("instance"), cmd))
        assert cmd == "frames"
        if device["role"] == "master":
            lufs, crest, share = self.master_reading()
            return {"role": "master", "frames": frames_for(lufs, crest, share, width=0.3)}
        lufs, crest, share = self.parts[device["track"]]
        return {"role": "track", "track": device["track"],
                "frames": frames_for(lufs, crest, share)}


@pytest.fixture
def mix_set(song, monkeypatch, tmp_path):
    """Kick, Sub, Pad, Hats (MIDI, each with an instrument) playing; Engines faked."""
    monkeypatch.setenv("LIVEBRIDGE_LIBRARY", str(tmp_path / "mix-library"))
    for name in ("Kick", "Sub", "Pad", "Hats"):
        track = factory.add_track(song, name, "midi")
        factory.add_device(track, "Operator", "Operator", kind="instrument")
    parts = {"Kick": (-14.0, 14.0, KICK), "Sub": (-12.0, 6.0, BASS),
             "Pad": (-10.0, 6.0, PAD), "Hats": (-16.0, 13.0, HATS)}
    for track in song.tracks:
        track.mixer_device.volume.value = 0.6               # -10 dB: a mix with headroom
    fake = FakeEngines(song, parts)
    monkeypatch.setattr(engine, "devices", fake.devices)
    monkeypatch.setattr(engine, "request", fake.request)
    monkeypatch.setattr(mix_tools, "_SETTLE_S", 0.0)
    song.is_playing = True
    return fake


@pytest.fixture
def app(tcp_bridge):
    return create_app(BridgeClient(host="127.0.0.1", port=tcp_bridge.port, timeout=10.0))


# ----------------------------------------------------------------------------- mix.py units

def test_roles_from_names_and_sound():
    assert [mix.role_from_name(n) for n in ("Kick", "Sub Bass", "HiHat Open", "Pad Chords",
                                            "Audio 3", "Vocal Chop", "BD 2")] == \
        ["kick", "sub", "hats", "pad", None, "vocal", "kick"]

    def reading(share, crest, width=0.1):
        return {"spectrum": {"bands_db": bands_db(share)}, "loudness": {"crest_db": crest},
                "stereo": {"width": width}}
    assert mix.role_from_sound(reading(KICK, 15))[0] == "kick"
    assert mix.role_from_sound(reading(BASS, 6))[0] in ("sub", "bass")
    assert mix.role_from_sound(reading(HATS, 14))[0] == "hats"
    assert mix.role_from_sound(reading(PAD, 5, width=0.4))[0] == "pad"
    guess = mix.guess_role("Audio 3", reading(KICK, 15))
    assert guess["role"] == "kick" and guess["from"] == "sound"
    assert mix.guess_role("Snare Top")["from"] == "name"


def test_genres_and_balance():
    assert mix.genre_key("DnB") == "drum_n_bass" and mix.genre_key("Hip Hop") == "hip_hop"
    assert mix.genre_key("polka") is None
    assert len(mix.GENRES) == 20 and mix.genre_key("house") == "house"
    assert mix.genre_key("melodic techno") == "melodic_house"
    shares6 = mix.genre_shares("techno")
    assert abs(sum(10 ** (v / 10) for v in shares6.values()) - 1) < 1e-9
    report = mix.genre_balance(bands_db(PAD), "techno")
    sub = next(b for b in report["bands"] if b["band"] == "sub")
    assert sub["delta_db"] < -20 and any("sub" in n for n in report["notes"])


def test_balance_plan_follows_role_offsets():
    parts = [{"name": "Kick", "role": "kick", "lufs": -14.0, "fader_db": 0.0},
             {"name": "Pad", "role": "pad", "lufs": -10.0, "fader_db": 0.0},
             {"name": "Hats", "role": "hats", "lufs": -16.0, "fader_db": -3.0}]
    plan = mix.balance_plan(parts, anchor="Kick", min_move=0.0)
    moves = {m["name"]: m for m in plan["moves"]}
    assert plan["anchor"] == "Kick" and "Kick" in plan["unchanged"]
    assert moves["Pad"]["target_lufs"] == -24.0 and moves["Pad"]["new_fader_db"] == -14.0
    assert moves["Hats"]["target_lufs"] == -25.0
    # a part that would need its fader above +6 dB stops there — nothing else comes down
    loud = mix.balance_plan([{"name": "Kick", "role": "kick", "lufs": -30.0, "fader_db": 0.0},
                             {"name": "Bass", "role": "bass", "lufs": -45.0,
                              "fader_db": 0.0}], anchor="Kick")
    bass = loud["moves"][0]
    assert bass["new_fader_db"] == 6.0 and bass["at_max"] and "Kick" in loud["unchanged"]


def test_balance_plan_is_level_neutral_and_idempotent():
    """No anchor: the moves are centred, so the mix keeps its level; only outliers move."""
    parts = [{"name": n, "role": r, "lufs": l, "fader_db": 0.0} for n, r, l in (
        ("Kick", "kick", -12.0), ("Bass", "bass", -15.0), ("Lead", "lead", -17.5),
        ("Pad", "pad", -22.5), ("Hats", "hats", -12.0))]            # the hats are 11 LU hot
    plan = mix.balance_plan(parts, genre=None)
    moves = {m["name"]: m["move_db"] for m in plan["moves"]}
    assert set(moves) == {"Hats"} and moves["Hats"] < -5             # only the outlier
    assert {"Kick", "Bass", "Lead", "Pad"} <= set(plan["unchanged"])   # the rest stays
    again = [dict(p, fader_db=moves.get(p["name"], 0.0)) for p in parts]
    assert mix.balance_plan(again)["moves"] == []                     # a second run: nothing
    kept = mix.balance_plan(parts, protect=["Hats"])
    assert "Hats" in kept["protected"] and all(m["name"] != "Hats" for m in kept["moves"])


def test_band_decisions_master_or_tracks():
    mud = shares(mud=1)
    tracks = [{"name": n, "role": "pad", "bands_db": bands_db(shares(mud=1, mid=1)),
               "post_lufs": -20.0} for n in ("A", "B", "C")]
    master = dict(bands_db(shares(sub=1, bass=2, low_mid=1, mud=3, mid=2, presence=0.5,
                                  high=0.3, air=0.1)))
    (shared,) = [d for d in mix.band_decisions(master, tracks) if d["band"] == "mud"]
    assert shared["fix"] == "master" and "master" in shared["advice"]
    one = tracks[:1] + [{"name": "Kick", "role": "kick", "bands_db": bands_db(KICK),
                         "post_lufs": -14.0}]
    (owned,) = [d for d in mix.band_decisions(master, one) if d["band"] == "mud"]
    assert owned["fix"] == "tracks" and owned["tracks"][0]["name"] == "A"
    assert "A" in owned["advice"] and mud


def test_patch_health_and_filter_cutoffs():
    strangled = {"spectrum": {"bands_db": bands_db(shares(bass=95, low_mid=3, mid=2))},
                 "loudness": {"crest_db": 4.0}}
    notes = mix.patch_health(strangled, "lead")
    assert len(notes) == 3 and "filtered shut" in notes[0]
    healthy = {"spectrum": {"bands_db": bands_db(shares(low_mid=1, mid=4, presence=3,
                                                         high=1))},
               "loudness": {"crest_db": 16.0}}
    assert mix.patch_health(healthy, "lead") == []
    params = [{"name": "Filter Freq", "display": "420 Hz"},
              {"name": "Filter Type", "display": "Lowpass"},
              {"name": "1 Frequency A", "display": "120 Hz"},
              {"name": "LFO Freq", "display": "2.00 Hz"}]
    found = mix.filter_cutoffs("Wavetable", params, "lead")
    assert [f["parameter"] for f in found] == ["Filter Freq", "LFO Freq"]
    assert found[0]["below_floor"] and found[0]["type"] == "Lowpass"
    highpass = mix.filter_cutoffs("Auto Filter", [{"name": "Frequency", "display": "300 Hz"},
                                                  {"name": "Filter Type",
                                                   "display": "Highpass"}], "pad")
    assert highpass[0]["caps_top"] is False and highpass[0]["below_floor"] is False
    assert mix.parse_hz("1.5 kHz") == 1500.0 and mix.parse_hz("-6 dB") is None


# ----------------------------------------------------------------------------- melody

GOOD = [[69, 0.5, 0.5, 62], [72, 1, 1.5, 70], [71, 2.5, 0.25, 60], [69, 2.75, 1.25, 74],
        [64, 4.5, 0.5, 70], [67, 5, 0.75, 78], [69, 5.75, 2, 84],
        [76, 8.5, 0.5, 88], [74, 9, 0.25, 70], [72, 9.25, 1.75, 92],
        [69, 12.5, 0.5, 80], [72, 13, 0.5, 86], [76, 13.5, 0.5, 92], [77, 14, 0.5, 96],
        [81, 18.5, 0.75, 110], [79, 19.25, 0.5, 96], [77, 19.75, 1, 90], [76, 21, 0.5, 80],
        [74, 22.5, 0.5, 72], [72, 23, 0.5, 70], [69, 24, 4, 66]]
CHORDS = [[57, 0, 8, 90], [60, 0, 8, 90], [64, 0, 8, 90], [53, 8, 8, 90], [57, 8, 8, 90],
          [60, 8, 8, 90], [55, 16, 8, 90], [59, 16, 8, 90], [62, 16, 8, 90],
          [57, 24, 8, 90], [60, 24, 8, 90], [64, 24, 8, 90]]


def test_melody_check_flags_the_traps():
    flat = [[60 + (i % 4) * 2, i * 0.5, 0.5, 100] for i in range(16)]
    result = theory.melody_check(flat, mood="sad")
    failed = {c["check"] for c in result["checks"] if not c["ok"]}
    assert {"off_grid", "durations", "rests", "velocity", "climax", "leaps", "motif"} <= failed
    assert result["mood"]["name"] == "melancholy"
    assert not all(c["ok"] for c in result["mood"]["checks"])
    assert len(result["suggestions"]) >= 7


def test_melody_check_passes_a_crafted_phrase():
    result = theory.melody_check(GOOD, length=32, harmony=CHORDS)
    checks = {c["check"]: c for c in result["checks"]}
    assert result["key"]["name"] == "A minor"
    for name in ("off_grid", "durations", "rests", "velocity", "leaps", "tension",
                 "question_answer"):
        assert checks[name]["ok"], checks[name]
    assert result["profile"]["climax"]["note"] == "A4"
    assert theory.melody_check([[60, 0, 1, 90]])["total"] == 0
    assert theory.mood_name("Euphoric") == "euphoria" and theory.mood_name("x") is None


def test_melody_tool(app, song):
    result = call_tool(app, "live_theory_melody", {"track": "Bass", "slot": 0, "mood": "dark",
                                                   "harmony": [{"track": "Bass",
                                                                "slot": 1}]})
    assert result["notes"] == 4 and result["mood"]["name"] == "tension"
    assert result["clip"].startswith("song.tracks[0]")
    for bad in ({"track": "Bass", "slot": 0, "mood": "grumpy"},
                {"track": "Bass", "slot": 0, "length": 0},
                {"track": "Bass", "slot": 0, "key": "H minor"},
                {"track": "Bass", "slot": 0, "harmony": [5]}):
        assert call_tool(app, "live_theory_melody", bad)["type"] == "bad_args", bad


# ----------------------------------------------------------------------------- tools

def test_sound_check(app, song, mix_set):
    filt = factory.add_device(song.tracks[5], "Auto Filter", "AutoFilter")
    factory.add_parameter(filt, "Frequency", 400.0, 20.0, 20000.0, unit="Hz")
    mix_set.parts["Pad"] = (-10.0, 4.0, shares(mud=95, mid=5))      # strangled pad
    mix_set.parts["Hats"] = (None, 0.0, HATS)                         # silent
    song.tracks[0].mute = True
    result = call_tool(app, "live_sound_check", {"seconds": 2})
    by_name = {t["name"]: t for t in result["tracks"]}
    assert by_name["Kick"]["signal"] == "ok" and by_name["Kick"]["role"] == "kick"
    assert by_name["Hats"]["signal"] == "silent"
    assert any("Silent" in p for p in by_name["Hats"]["problems"])
    assert any("filtered shut" in h for h in by_name["Pad"]["health"])
    assert any("below the ~1500 Hz floor" in p for p in by_name["Pad"]["problems"])
    assert "The track is muted." in by_name["Bass"]["problems"]
    assert result["order"][0].startswith("Pad")
    assert any("louder than the kick" in n for n in result["notes"])
    assert call_tool(app, "live_sound_check", {"seconds": 1})["type"] == "bad_args"
    assert call_tool(app, "live_sound_check", {"roles": {"Kick": "tuba"}})["type"] == \
        "bad_args"


def test_mix_balance_sets_faders_and_keeps_headroom(app, song, mix_set):
    result = call_tool(app, "live_mix_balance", {"max_move_db": 24, "anchor": "Kick",
                                                  "tolerance_db": 0, "max_raise_db": 12,
                                                  "tracks": ["Kick", "Sub", "Pad", "Hats"],
                                                 "seconds": 3})
    assert result["applied"] and result["plan"]["anchor"] == "Kick"
    post = {}
    for name in ("Kick", "Sub", "Pad", "Hats"):
        lufs = mix_set.parts[name][0]
        post[name] = lufs + fader_db(mix_set.track(name))
    # role order kept: kick 0, sub -1, pad -10, hats -11 (headroom trim moves all together)
    assert post["Kick"] - post["Sub"] == pytest.approx(1.0, abs=0.4)
    assert post["Kick"] - post["Pad"] == pytest.approx(10.0, abs=0.4)
    assert post["Kick"] - post["Hats"] == pytest.approx(11.0, abs=0.4)
    assert "master" in result and isinstance(result.get("bands"), list)
    again = call_tool(app, "live_mix_balance", {"seconds": 3, "apply": False})
    assert all(abs(m["move_db"]) < 1.0 for m in again["plan"]["moves"])

    for bad in ({"seconds": 1}, {"passes": 9}, {"genre": "polka"},
                {"roles": {"Kick": "tuba"}}, {"headroom_db": 20}):
        assert call_tool(app, "live_mix_balance", bad)["type"] == "bad_args", bad
    song.is_playing = False
    assert call_tool(app, "live_mix_balance", {})["type"] == "invalid_state"


def test_mix_balance_genre_and_roles(app, song, mix_set):
    result = call_tool(app, "live_mix_balance", {"genre": "dubstep", "apply": False, "anchor": "Kick", "tolerance_db": 0,
                                                 "roles": {"Pad": "fx"}, "seconds": 3})
    roles = {p["name"]: p["role"] for p in result["parts"]}
    assert roles["Pad"] == "fx" and result["genre"] == "dubstep"
    assert result["plan"]["anchor"] == "Kick"
    targets = {m["name"]: m["offset_lu"] for m in result["plan"]["moves"]}
    assert targets.get("Pad") == -11.0                     # dubstep fx


def test_master_auto_reaches_the_target(app, song, mix_set):
    song.master_track  # noqa: B018
    result = call_tool(app, "live_master_auto", {"destination": "club", "seconds": 3})
    assert result["applied"] and result["inserted"] == ["EQ Eight", "Glue Compressor",
                                                        "Limiter"]
    assert [d.name for d in song.master_track.devices][-1] == "Limiter"
    assert result["ceiling_db"] == -0.3
    gain, ceiling = mix_set.limiter()
    assert ceiling == pytest.approx(-0.3, abs=0.01) and gain > 0
    after = result["after"]
    if "stopped" not in result:
        assert after["lufs"] == pytest.approx(-8.0, abs=0.6)
    else:
        assert "crest" in result["stopped"] or "limit" in result["stopped"]
    assert result["steps"] and result["before"]["lufs"] < after["lufs"]
    # a second run reuses the chain
    again = call_tool(app, "live_master_auto", {"destination": "streaming", "seconds": 3})
    assert again["inserted"] == [] and again["after"]["lufs"] == pytest.approx(-14, abs=0.6)
    plan = call_tool(app, "live_master_auto", {"destination": "apple", "apply": False,
                                               "seconds": 3})
    assert plan["applied"] is False and "needed_gain_db" in plan

    for bad in ({"destination": "radio"}, {"lufs": 0}, {"ceiling": 2}, {"seconds": 1},
                {"loud_bar": 9}, {"destination": "genre"}):
        assert call_tool(app, "live_master_auto", bad)["type"] == "bad_args", bad


def test_master_auto_with_the_livebridge_master_device(app, song, mix_set):
    """chain='livebridge': one Max device (Ableton's DSP) instead of EQ Eight/Glue/Limiter."""
    assert call_tool(app, "live_master_auto", {"chain": "rack"})["type"] == "bad_args"
    missing = call_tool(app, "live_master_auto", {"destination": "club", "seconds": 3,
                                                  "chain": "livebridge"})
    assert missing["type"] == "not_found" and "LiveBridge Master" in missing["error"]
    factory.add_device(song.master_track, "LiveBridge Master", "MxDeviceAudioEffect", params=[
        {"name": "EQ Low Cut", "value": 0, "min": 0, "max": 1, "is_quantized": True,
         "value_items": ("off", "on")},
        {"name": "Comp Ratio", "value": 2.0, "min": 1.0, "max": 10.0},
        {"name": "Limit Gain", "value": 0.0, "min": -24.0, "max": 24.0, "unit": "dB"},
        {"name": "Limit Ceiling", "value": -1.0, "min": -24.0, "max": 0.0, "unit": "dB"}])
    result = call_tool(app, "live_master_auto", {"destination": "club", "seconds": 3,
                                                 "chain": "livebridge"})
    assert result["applied"] and result["inserted"] == [], result
    assert not any(d.class_name == "Limiter" for d in song.master_track.devices)
    gain, ceiling = mix_set.limiter()
    assert ceiling == pytest.approx(-0.3, abs=0.05) and gain > 0
    # Live's own Limiter next to it: refused rather than stacking two limiters
    factory.add_device(song.master_track, "Limiter", "Limiter")
    song.master_track._devices = [d for d in song.master_track.devices
                                  if d.name != "LiveBridge Master"]
    clash = call_tool(app, "live_master_auto", {"destination": "club", "seconds": 3,
                                                "chain": "livebridge"})
    assert clash["type"] == "invalid_state" and "two limiters" in clash["error"]


def add_master_device(song, limit_gain=0.0):
    """The LiveBridge Master device with the controls live_master_auto drives."""
    def db(name, value, low, high):
        return {"name": name, "value": value, "min": low, "max": high, "unit": "dB"}
    factory.add_device(song.master_track, "LiveBridge Master", "MxDeviceAudioEffect", params=[
        {"name": "EQ Low Cut", "value": 0, "min": 0, "max": 1, "is_quantized": True,
         "value_items": ("Off", "On")},
        db("EQ Low", 0.0, -15.0, 15.0), db("EQ Mid", 0.0, -12.0, 12.0),
        {"name": "EQ Mid Freq", "value": 1000.0, "min": 120.0, "max": 7500.0, "unit": "Hz"},
        db("EQ High", 0.0, -15.0, 15.0),
        {"name": "Comp Ratio", "value": 2.0, "min": 1.0, "max": 10.0},
        db("Comp Makeup", 0.0, -12.0, 24.0), db("Limit Gain", limit_gain, -24.0, 24.0),
        db("Limit Ceiling", -1.0, -24.0, 0.0), db("Output", 0.0, -24.0, 12.0)])
    return next(d for d in song.master_track.devices if d.name == "LiveBridge Master")


def master_params(device):
    return {p.name: p.value for p in device.parameters}


def test_master_auto_measures_before_at_neutral_and_sets_the_eq(app, song, mix_set):
    """A rerun: the last run's +18 dB is set back to 0 for 'before'; the shared band excess
    goes on the device's EQ; past Limit Gain's limit the compressor's makeup drives on."""
    device = add_master_device(song, limit_gain=18.0)
    result = call_tool(app, "live_master_auto", {
        "destination": "club", "seconds": 3, "chain": "livebridge",
        "eq_bands": {"mid": 7.0, "air": 0.4}})
    assert result["reset"] == {"Limit Gain": 18.0}, result
    assert result["before"]["lufs"] < result["after"]["lufs"] - 6
    params = master_params(device)
    assert params["EQ Mid"] == pytest.approx(-3.5) and params["EQ Mid Freq"] == pytest.approx(700)
    assert result["eq"]["EQ Mid"].startswith("0 -> -3.5") and result["steps"][0] == \
        {"eq": result["eq"]}
    assert "EQ High" not in result["eq"]                      # 0.4 dB: left alone
    assert result["settings"]["Limit Gain"].startswith(f"{params['Limit Gain']:.1f}")
    assert not result.get("headroom_trim_db")                 # no fader moves
    assert result["after"]["lufs"] == pytest.approx(-8.0, abs=0.6) or "stopped" in result
    # the EQ adds up over runs (a second run still hearing too much mid cuts again)
    again = call_tool(app, "live_master_auto", {
        "destination": "club", "seconds": 3, "chain": "livebridge", "eq_bands": {"mid": 4.0}})
    assert master_params(device)["EQ Mid"] == pytest.approx(-5.5)
    assert set(again["reset"]) <= {"Limit Gain", "Comp Makeup"} and again["reset"]
    # Limit Gain capped at 6 dB: the compressor's makeup drives on to the target
    pushed = call_tool(app, "live_master_auto", {"destination": "club", "seconds": 3,
                                                 "chain": "livebridge", "max_gain_db": 6})
    assert pushed["limiter_gain_db"] == 6 and pushed["makeup_db"] > 0, pushed
    assert pushed["after"]["lufs"] == pytest.approx(-8.0, abs=0.6)
    assert master_params(device)["Comp Makeup"] == pytest.approx(pushed["makeup_db"], abs=0.05)


def test_master_auto_finds_a_device_that_does_not_process(app, song, mix_set):
    device = add_master_device(song)
    mix_set.broken = True
    result = call_tool(app, "live_master_auto", {"destination": "club", "seconds": 3,
                                                 "chain": "livebridge"})
    check = result["device_check"]
    assert "passes the sound untouched" in result["stopped"] and check["heard_lu"] == 0
    assert check["output_probe_lu"] == 0 and "limiting_db" not in result
    params = master_params(device)
    assert params["Limit Gain"] == 0 and params["Output"] == 0       # nothing left changed
    # switched off (Device On): refused before anything is touched
    mix_set.broken = False
    device.parameters[0].value = 0
    off = call_tool(app, "live_master_auto", {"destination": "club", "seconds": 3,
                                              "chain": "livebridge"})
    assert off["type"] == "invalid_state" and "switched off" in off["error"]


def test_master_auto_trims_a_hot_mix_first(app, song, mix_set):
    for track in song.tracks:
        track.mixer_device.volume.value = 0.85              # 0 dB: peaks near full scale
    result = call_tool(app, "live_master_auto", {"destination": "streaming", "seconds": 3})
    assert result["headroom_trim_db"] < -3 and fader_db(mix_set.track("Kick")) < -3
    assert result["steps"][0]["faders_db"] == result["headroom_trim_db"]   # listed
    assert result["after"]["lufs"] == pytest.approx(-14.0, abs=0.6)


def test_master_auto_stops_when_punch_collapses(app, song, mix_set):
    result = call_tool(app, "live_master_auto", {"lufs": -5, "ceiling": -1, "seconds": 3})
    assert "crest factor" in result["stopped"]


def test_tools_without_engines(app, song, monkeypatch, tmp_path):
    monkeypatch.setenv("LIVEBRIDGE_ENGINE_DIR", str(tmp_path / "none"))
    assert call_tool(app, "live_mix_balance", {})["type"] == "not_found"
    assert call_tool(app, "live_master_auto", {})["type"] == "not_found"
    checked = call_tool(app, "live_sound_check", {"seconds": 2})
    assert {t["name"] for t in checked["tracks"]} >= {"Bass", "Drums"}


def test_analyze_with_genre(app, tmp_path):
    pytest.importorskip("numpy")
    pytest.importorskip("soundfile")
    pytest.importorskip("pyloudnorm")
    from test_analysis import write_loop
    path = write_loop(tmp_path / "loop.wav", seconds=25.0)
    result = call_tool(app, "live_audio_analyze", {"file_path": path, "genre": "techno"})
    balance = result["genre_balance"]
    assert balance["genre"] == "techno" and len(balance["bands"]) == 6
    assert call_tool(app, "live_audio_analyze", {"file_path": path, "genre": "polka"})[
        "type"] == "bad_args"


def test_master_auto_keeps_the_drop_bigger_than_the_breakdown(app, song, mix_set):
    mix_set.quiet_from = 64.0                                  # bar 17: breakdown
    song.is_playing = False
    result = call_tool(app, "live_master_auto", {"destination": "club", "seconds": 3,
                                                 "loud_bar": 1, "quiet_bar": 17})
    contrast = result["contrast"]
    assert contrast["before_lu"] > 3 and contrast["after_lu"] is not None
    if contrast["after_lu"] < contrast["before_lu"] - 2:
        assert any("backed off" in n for n in result["notes"])
    assert song.is_playing is False                           # stopped again afterwards


# ----------------------------------------------------------------------------- channel strips

def test_channel_plan_rules():
    raw = {"loudness": {"lufs": -14.0, "peak_db": -3.0, "crest_db": 14.0},
           "spectrum": {"bands_db": {"sub": -20, "bass": -12, "low_mid": -8, "mud": -4,
                                     "mid": -5, "presence": -9, "high": -20, "air": -30}}}
    vocal = mix.channel_plan("vocal", raw)["values"]
    assert vocal["HP Freq"] == 90.0 and vocal["Mid Freq"] == 350.0 and vocal["Mid"] < 0
    assert vocal["High"] == 2.0 and vocal["Comp Ratio"] == 4.0
    # 4:1 taking ~6 dB off a -3 dB peak: threshold -3 - 6 / 0.75 = -11
    assert vocal["Comp Threshold"] == pytest.approx(-11.0) and vocal["Comp Makeup"] == 3.0
    sub = mix.channel_plan("sub", {"loudness": {"crest_db": 6.5, "peak_db": -6.0},
                                   "spectrum": {"bands_db": {"sub": -1.0}}})["values"]
    assert sub == {"Bass Mono": 1.0, "Width": 0.0}       # no high-pass, dynamics fine
    pad = mix.channel_plan("pad", {"loudness": {"crest_db": 20, "peak_db": -1},
                                   "spectrum": {"bands_db": {"mud": -10, "presence": -10}}})
    assert "Comp Ratio" not in pad["values"] and pad["values"]["HP Freq"] == 150.0
    dull_bass = mix.channel_plan("bass", {"spectrum": {"bands_db": {
        "sub": -3, "bass": -3, "presence": -30, "high": -40, "air": -50}}})["values"]
    assert dull_bass["Saturation"] == 20.0 and dull_bass["Bass Mono"] == 1.0
    assert set(mix.channel_plan(None, None)["values"]) == {"HP Freq"}


def test_mix_auto_sets_the_channel_strips(app, song, mix_set, monkeypatch):
    real = mix_set.devices

    def with_strips(*a, **k):
        found = real(*a, **k)
        for device in found:
            if device["role"] == "track":
                device["strip"] = True
        return found
    monkeypatch.setattr(engine, "devices", with_strips)
    for name in ("Kick", "Sub", "Pad", "Hats"):
        factory.add_device(mix_set.track(name), "LiveBridge Channel", "MxDeviceAudioEffect",
                           params=[{"name": n, "value": v, "min": min(v, -40.0),
                                    "max": max(v, 1000.0),
                                    **({"unit": "Hz", "min": 20.0, "max": 8000.0}
                                       if "Freq" in n else {})}
                                   for n, v in mix.CHANNEL_NEUTRAL.items()])
    plan = call_tool(app, "live_mix_auto", {"apply": False, "seconds": 3, "place_devices": False})
    assert plan["applied"] is False and {t["name"] for t in plan["tracks"]} == {
        "Kick", "Sub", "Pad", "Hats"}
    result = call_tool(app, "live_mix_auto", {"seconds": 3, "balance": True, "place_devices": False, "detail": "full"})
    assert result["applied"]
    by_name = {t["name"]: t for t in result["tracks"]}
    assert by_name["Hats"]["role"] == "hats" and by_name["Hats"]["values"]["HP Freq"] == 300.0
    assert by_name["Sub"]["values"]["Bass Mono"] == 1.0 and "after" in by_name["Sub"]
    strip = next(d for d in mix_set.track("Hats").devices if d.name == "LiveBridge Channel")
    assert {p.name: p.value for p in strip.parameters}["HP Freq"] == pytest.approx(300, rel=0.01)
    assert "parts" in result["balance"] or "plan" in result["balance"]
    # every part carries the balance's verdict, so the per-track section and it agree
    assert all(t.get("balance") for t in result["tracks"]), result["tracks"]
    for bad in ({"seconds": 1}, {"master_chain": "rack"}, {"roles": {"Kick": "tuba"}}):
        assert call_tool(app, "live_mix_auto", bad)["type"] == "bad_args", bad


def test_mix_auto_without_channels(app, song, mix_set):
    answer = call_tool(app, "live_mix_auto", {})
    assert answer["type"] == "not_found" and "LiveBridge Channel" in answer["error"]


def _phase_device(track):
    return factory.add_device(track, "LiveBridge Phase", "MxDeviceAudioEffect", params=[
        {"name": "Invert", "value": 0, "min": 0, "max": 1, "is_quantized": True,
         "value_items": ("off", "on")},
        {"name": "Phase Freq", "value": 100.0, "min": 20.0, "max": 2000.0, "unit": "Hz"},
        {"name": "Phase Q", "value": 0.7, "min": 0.1, "max": 10.0},
        {"name": "Phase Stages", "value": 0, "min": 0, "max": 4, "is_quantized": True,
         "value_items": ("Off", "1", "2", "3", "4")},
        {"name": "Delay", "value": 0.0, "min": 0.0, "max": 10.0, "unit": "ms"}])


def test_mix_phase_keeps_the_least_cancelling_setting(app, song, mix_set, monkeypatch):
    phase = _phase_device(mix_set.track("Sub"))
    plain = mix_set.master_reading

    def reading():
        lufs, crest, share = plain()
        p = {q.name: q.value for q in phase.parameters}
        # Sub flipped and rotated at 70 Hz lines up with the kick: 3 dB more low end
        if p["Invert"] == 1 and p["Phase Stages"] == 1 and abs(p["Phase Freq"] - 70) < 1:
            lufs += 3.0
        elif p["Invert"] == 1:
            lufs += 1.0
        return lufs, crest, share
    monkeypatch.setattr(mix_set, "master_reading", reading)
    result = call_tool(app, "live_mix_phase", {"track": "Sub", "seconds": 1})
    assert result["applied"] and result["best"] == {"invert": True, "frequency": 70.0,
                                                    "low_db": result["best"]["low_db"]}
    assert result["gain_db"] == pytest.approx(3.0, abs=0.3)
    assert len(result["tried"]) == 2 + 2 * 7
    p = {q.name: q.value for q in phase.parameters}
    assert p["Invert"] == 1 and p["Phase Stages"] == 1
    assert p["Phase Freq"] == pytest.approx(70.0, rel=0.01)
    # nothing to gain: left neutral
    monkeypatch.setattr(mix_set, "master_reading", plain)
    flat = call_tool(app, "live_mix_phase", {"track": "Sub", "seconds": 1,
                                             "frequencies": [60, 120]})
    assert flat["applied"] is False and any("neutral" in n for n in flat["notes"])
    assert {q.name: q.value for q in phase.parameters}["Phase Stages"] == 0
    for bad in ({"track": "Sub", "seconds": 9}, {"track": "Sub", "frequencies": [5]}):
        assert call_tool(app, "live_mix_phase", bad)["type"] == "bad_args"


def test_mix_phase_needs_the_device(app, song, mix_set):
    answer = call_tool(app, "live_mix_phase", {"track": "Sub", "seconds": 1})
    assert answer["type"] == "not_found" and "LiveBridge Phase" in answer["error"]



def test_clash_band():
    kick = {"spectrum": {"bands_db": {"sub": -3, "bass": -4, "low_mid": -12, "mid": -20}}}
    bass = {"spectrum": {"bands_db": {"sub": -8, "bass": -2, "low_mid": -6, "mid": -14}}}
    clash = mix.clash_band(bass, kick)
    assert clash["band"] == "bass" and clash["hz"] == pytest.approx(84.9, abs=0.1)
    assert 0.5 <= clash["q"] <= 4
    hats = {"spectrum": {"bands_db": {"high": -3, "air": -4, "sub": -40}}}
    assert mix.clash_band(bass, hats) is None


def test_mix_unmask_routes_the_side_chain_and_sets_the_band(app, song, mix_set):
    from live_stub_ext import routing as routing_ext
    sub = mix_set.track("Sub")
    unmask = routing_ext.add_max_sidechain_device(sub, params=[
        {"name": n, "value": v, "min": lo, "max": hi} for n, v, lo, hi in (
            ("Q", 1.0, 0.3, 8.0), ("Depth", 6.0, 0.0, 18.0),
            ("Threshold", -30.0, -60.0, 0.0), ("Attack", 5.0, 0.5, 100.0),
            ("Release", 120.0, 10.0, 1000.0))] + [
        {"name": "Freq", "value": 80.0, "min": 20.0, "max": 8000.0, "unit": "Hz"}])
    plan = call_tool(app, "live_mix_unmask", {"track": "Sub", "against": "Kick",
                                              "apply": False, "seconds": 2, "place_devices": False})
    assert plan["applied"] is False and plan["band"]["band"] in ("sub", "bass")
    assert (plan["attack_ms"], plan["release_ms"]) == (2.0, 100.0)       # a kick: fast
    done = call_tool(app, "live_mix_unmask", {"track": "Sub", "against": "Kick",
                                              "seconds": 2, "depth_db": 4, "place_devices": False})
    assert done["applied"] and done["routing"]["input"]["type"] == "Kick"
    assert unmask.audio_inputs[1].routing_type.display_name == "Kick"
    p = {q.name: q.value for q in unmask.parameters}
    assert p["Freq"] == pytest.approx(done["band"]["hz"], rel=0.01) and p["Depth"] == 4.0
    assert p["Threshold"] == done["threshold_db"] and "after" in done
    for bad in ({"track": "Sub", "against": "Sub"}, {"track": "Sub", "against": "Kick",
                                                     "depth_db": 30},
                {"track": "Sub", "against": "Kick", "frequency": 5}):
        assert call_tool(app, "live_mix_unmask", bad)["type"] == "bad_args", bad


def test_mix_unmask_needs_meters_on_both(app, song, mix_set):
    answer = call_tool(app, "live_mix_unmask", {"track": "Sub", "against": "Nowhere",
                                                "place_devices": False})
    assert answer["type"] == "not_found" and "Nowhere" in answer["error"]


def test_mix_auto_places_the_devices_itself(app, song, mix_set, monkeypatch):
    """No Channel anywhere: live_mix_auto loads one at the end of every sounding track
    (an Engine there is replaced, a MIDI track without instrument is skipped)."""
    import Live
    browser = Live.Application.get_application().browser
    factory.add_browser_item(browser.audio_effects, "LiveBridge Channel",
                             uri="query:AudioFx#LiveBridge%20Channel", is_device=True,
                             load_kind="device")
    from Live import _model
    plain_load = _model.Browser._load_device

    def load_channel(self, item, track):          # the stub loads a generic device: give it
        device = plain_load(self, item, track)     # the Channel's parameters
        if item.name == "LiveBridge Channel":
            device._parameters = list(device._parameters[:1]) + [
                _model.DeviceParameter(n, v, min(v, -40.0), max(v, 1000.0),
                                       canonical_parent=device)
                if "Freq" not in n else
                _model.DeviceParameter(n, v, 20.0, 8000.0, unit="Hz", canonical_parent=device)
                for n, v in mix.CHANNEL_NEUTRAL.items()]
        return device
    monkeypatch.setattr(_model.Browser, "_load_device", load_channel)
    empty = factory.add_track(song, "Empty MIDI", "midi")                   # no instrument
    old = factory.add_device(mix_set.track("Pad"), "LiveBridge Engine", "MxDeviceAudioEffect")
    real = mix_set.devices

    def with_strips(*a, **k):
        found = real(*a, **k)
        for device in found:
            if device["role"] == "track":
                track = mix_set.track(device["track"])
                device["strip"] = any(d.name == "LiveBridge Channel" for d in track.devices)
        return found
    monkeypatch.setattr(engine, "devices", with_strips)
    result = call_tool(app, "live_mix_auto", {"seconds": 3, "balance": False,
                                              "low_end": False})
    placed = result["placed"]
    assert set(placed["placed"]) >= {"Kick", "Sub", "Pad", "Hats"}   # + the set's others
    assert placed["replaced"] == ["Pad"] and old not in mix_set.track("Pad").devices
    assert any("Empty MIDI" in s for s in placed["skipped"])
    assert not any(d.name == "LiveBridge Channel" for d in empty.devices)
    for name in ("Kick", "Sub", "Pad", "Hats"):
        assert mix_set.track(name).devices[-1].name == "LiveBridge Channel"
    assert {t["name"] for t in result["tracks"]} == {"Kick", "Sub", "Pad", "Hats"}
    # a second run finds them in place
    again = call_tool(app, "live_mix_auto", {"seconds": 3, "balance": False,
                                             "low_end": False})
    assert again.get("placed", {}).get("placed", []) == []           # nothing new to place



def test_balance_caps_each_move_and_skips_sparse_parts(app, song, mix_set, monkeypatch):
    plan = mix.balance_plan([{"name": "Kick", "role": "kick", "lufs": -10.0, "fader_db": 0.0},
                             {"name": "Pad", "role": "pad", "lufs": -6.0, "fader_db": 0.0}],
                            max_move=6.0, min_move=0.0)
    pad = next(m for m in plan["moves"] if m["name"] == "Pad")
    assert pad["move_db"] == -6.0 and pad["capped"] is True        # wanted -14, capped
    # a one-shot that plays in 2 frames of 20: left alone, said in the notes
    plain = frames_for

    def sparse(lufs, crest_db, share, width=0.1, count=20):
        frames = plain(lufs, crest_db, share, width, count)
        if lufs is not None and abs(lufs - (-16.0)) < 1e-9:       # the Hats part
            k = 1 + engine.FIELDS.index("ms_k")
            for frame in frames[2:]:
                frame[k] = frame[k] * 1e-4
        return frames
    monkeypatch.setattr(sys.modules[__name__], "frames_for", sparse)
    result = call_tool(app, "live_mix_balance", {"seconds": 3, "apply": False})
    assert "Hats" not in [p["name"] for p in result["parts"]]
    assert any("Hats plays only" in n for n in result["notes"])


def _strips(mix_set, monkeypatch):
    real = mix_set.devices

    def with_strips(*a, **k):
        found = real(*a, **k)
        for device in found:
            if device["role"] == "track":
                device["strip"] = True
        return found
    monkeypatch.setattr(engine, "devices", with_strips)
    for name in ("Kick", "Sub", "Pad", "Hats"):
        factory.add_device(mix_set.track(name), "LiveBridge Channel", "MxDeviceAudioEffect",
                           params=[{"name": n, "value": v, "min": min(v, -40.0),
                                    "max": max(v, 1000.0),
                                    **({"unit": "Hz", "min": 20.0, "max": 8000.0}
                                       if "Freq" in n else {"unit": "dB"} if n == "High"
                                       else {})}
                                   for n, v in mix.CHANNEL_NEUTRAL.items()])


def test_mix_auto_keeps_hand_set_controls_and_holds_the_loop(app, song, mix_set, monkeypatch,
                                                             tmp_path):
    monkeypatch.setenv("LIVEBRIDGE_LIBRARY", str(tmp_path / "library"))
    _strips(mix_set, monkeypatch)
    song.loop = False
    args = {"seconds": 3, "balance": False, "place_devices": False, "detail": "full"}
    first = call_tool(app, "live_mix_auto", args)
    assert any("Looped bars" in n for n in first["notes"]) and song.loop is False
    hats = next(d for d in mix_set.track("Hats").devices if d.name == "LiveBridge Channel")
    high = next(p for p in hats.parameters if p.name == "High")
    high.value = -6.0                                  # the user's own shelf
    again = call_tool(app, "live_mix_auto", args)
    entry = next(t for t in again["tracks"] if t["name"] == "Hats")
    assert "High" in entry["kept"] and high.value == pytest.approx(-6.0)
    assert any("kept what you set by hand" in r for r in entry["reasons"])
    # LiveBridge's own settings are not "kept": its high-pass is set again, not frozen
    assert "HP Freq" not in entry["kept"] and entry["values"]["HP Freq"] == 300.0
    full = call_tool(app, "live_mix_auto", dict(args, keep_manual=False))
    assert "kept" not in next(t for t in full["tracks"] if t["name"] == "Hats")
    assert high.value == pytest.approx(0.0)


def test_balance_never_raises_more_than_the_cap():
    parts = [{"name": "Kick", "role": "kick", "lufs": -10.0, "fader_db": 0.0},
             {"name": "Tom", "role": "perc", "lufs": -40.0, "fader_db": 0.0}]
    plan = mix.balance_plan(parts, max_move=6.0, raise_left={"Kick": 3.0, "Tom": 3.0})
    tom = next(m for m in plan["moves"] if m["name"] == "Tom")
    assert tom["move_db"] == 3.0 and tom["raise_capped"] is True


def test_balance_reports_the_moves_of_every_pass(app, song, mix_set):
    """The second pass finds nothing left to do: the report still lists the first pass's
    moves (it showed moves=[] while 6 faders had moved)."""
    result = call_tool(app, "live_mix_balance", {"anchor": "Kick", "tolerance_db": 0,
                                                  "max_move_db": 24, "max_raise_db": 12,
                                                  "passes": 2, "seconds": 3})
    moves = {m["name"]: m for m in result["plan"]["moves"]}
    assert result["passes"] == 2 and set(moves) == {"Sub", "Pad", "Hats"}
    assert moves["Pad"]["fader_db"] == -10.0 and moves["Pad"]["passes"] == 1
    assert moves["Pad"]["move_db"] == pytest.approx(moves["Pad"]["new_fader_db"] + 10.0, abs=0.1)


def test_balance_twice_moves_nothing_the_second_time(app, song, mix_set):
    args = {"max_move_db": 24, "max_raise_db": 12, "seconds": 3}
    first = call_tool(app, "live_mix_balance", args)
    assert first["plan"]["moves"]
    before = {n: fader_db(mix_set.track(n)) for n in mix_set.parts}
    again = call_tool(app, "live_mix_balance", args)
    assert again["plan"]["moves"] == []
    assert all(fader_db(mix_set.track(n)) == pytest.approx(v, abs=0.05)
               for n, v in before.items())


def test_balance_leaves_the_headroom_behind_a_master_chain_alone(app, song, mix_set):
    factory.add_device(song.master_track, "Limiter", "Limiter")
    factory.add_device(song.master_track, "LiveBridge Engine", "MxDeviceAudioEffect")
    for track in song.tracks:
        track.mixer_device.volume.value = 0.85              # 0 dB: the master peaks hot
    result = call_tool(app, "live_mix_balance", {"seconds": 3,
                                                  "protect": list(mix_set.parts)})
    assert "headroom_trim_db" not in result
    assert any("after Limiter" in n for n in result["notes"])
    assert all(fader_db(t) == pytest.approx(0.0, abs=0.05) for t in song.tracks)


def test_sub_named_with_break_is_a_sub():
    assert mix.guess_role("BS Break Subbass")["role"] == "sub"
    assert mix.guess_role("Break Loop")["role"] == "drums"


def test_mix_auto_skips_muted_strips_and_reports_changes(app, song, mix_set, monkeypatch,
                                                          tmp_path):
    monkeypatch.setenv("LIVEBRIDGE_LIBRARY", str(tmp_path / "library"))
    _strips(mix_set, monkeypatch)
    mix_set.track("Hats").mute = True                  # muted, no mute automation
    args = {"seconds": 3, "balance": False, "place_devices": False}
    first = call_tool(app, "live_mix_auto", args)
    assert "Hats" not in [t["name"] for t in first["tracks"]]
    assert any("Muted with no mute automation" in n and "Hats" in n for n in first["notes"])
    kick = next(t for t in first["tracks"] if t["name"] == "Kick")
    assert kick["changes"] and all(" -> " in v for v in kick["changes"].values())
    again = call_tool(app, "live_mix_auto", args)       # the same strips: nothing changes
    assert all(t["changes"] == {} for t in again["tracks"])


def test_bars_plays_the_section_and_stops_again(app, song, mix_set):
    song.is_playing = False
    song.loop = False
    result = call_tool(app, "live_mix_balance", {"seconds": 3, "apply": False,
                                                  "section_bars": [1, 4]})
    assert "error" not in result, result
    assert any("Played bars 1-4" in n and "stopped again" in n for n in result["notes"])
    assert song.is_playing is False and song.loop is False
    bad = call_tool(app, "live_mix_balance", {"seconds": 3, "section_bars": [4, 1]})
    assert bad["type"] == "bad_args"


def test_a_capped_move_is_final_until_the_fader_is_touched(app, song, mix_set):
    """A part that keeps reading quiet got +3 dB on every rerun (DR Tom +3, +3, ...): the raise
    limit counts over runs while the fader stays where the balance left it."""
    lufs, crest, share = mix_set.parts["Hats"]
    mix_set.parts["Hats"] = (-45.0, crest, share)            # far too quiet for its role
    first = call_tool(app, "live_mix_balance", {"seconds": 3})
    hats = next(m for m in first["plan"]["moves"] if m["name"] == "Hats")
    assert hats["move_db"] == 3.0 and hats.get("raise_capped")
    again = call_tool(app, "live_mix_balance", {"seconds": 3})
    assert all(m["name"] != "Hats" for m in again["plan"]["moves"])
    held = next(h for h in again["plan"]["held"] if h["name"] == "Hats")   # still off: said
    assert held["off_db"] < -3 and held.get("raise_capped") and "Hats" in again["plan"]["unchanged"]
    assert any("Hats is held" in n and "below" in n for n in again["notes"])
    mix_set.track("Hats").mixer_device.volume.value = 0.5    # the user moves it: start over
    third = call_tool(app, "live_mix_balance", {"seconds": 3})
    assert any(m["name"] == "Hats" and m["move_db"] > 0 for m in third["plan"]["moves"])


def test_protect_keeps_the_fader_and_the_mix_level():
    parts = [{"name": n, "role": r, "lufs": l, "fader_db": 0.0} for n, r, l in (
        ("Kick", "kick", -16.0), ("Bass", "bass", -19.0), ("Lead", "lead", -12.0),
        ("Pad", "pad", -26.0), ("Hats", "hats", -27.0))]
    free = mix.balance_plan([dict(p) for p in parts], min_move=0.0)
    kept = mix.balance_plan([dict(p) for p in parts], min_move=0.0, protect=["Lead"])
    assert kept["protected"] == ["Lead"]
    # the others are centred as without protect: they are not pulled up to the hot lead
    assert {m["name"]: m["move_db"] for m in kept["moves"]} == \
        {m["name"]: m["move_db"] for m in free["moves"] if m["name"] != "Lead"}
