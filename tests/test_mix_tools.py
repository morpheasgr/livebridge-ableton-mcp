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
                return params["Limit Gain"], params["Limit Ceiling"]
        return 0.0, 0.0

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
def mix_set(song, monkeypatch):
    """Kick, Sub, Pad, Hats (MIDI, each with an instrument) playing; Engines faked."""
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
    assert len(mix.GENRES) == 18
    shares6 = mix.genre_shares("techno")
    assert abs(sum(10 ** (v / 10) for v in shares6.values()) - 1) < 1e-9
    report = mix.genre_balance(bands_db(PAD), "techno")
    sub = next(b for b in report["bands"] if b["band"] == "sub")
    assert sub["delta_db"] < -20 and any("sub" in n for n in report["notes"])


def test_balance_plan_follows_role_offsets():
    parts = [{"name": "Kick", "role": "kick", "lufs": -14.0, "fader_db": 0.0},
             {"name": "Pad", "role": "pad", "lufs": -10.0, "fader_db": 0.0},
             {"name": "Hats", "role": "hats", "lufs": -16.0, "fader_db": -3.0}]
    plan = mix.balance_plan(parts)
    moves = {m["name"]: m for m in plan["moves"]}
    assert plan["anchor"] == "Kick" and "Kick" in plan["unchanged"]
    assert moves["Pad"]["target_lufs"] == -24.0 and moves["Pad"]["new_fader_db"] == -14.0
    assert moves["Hats"]["target_lufs"] == -25.0
    # a quiet anchor would push faders above +6 dB: everything comes down together
    loud = mix.balance_plan([{"name": "Kick", "role": "kick", "lufs": -30.0, "fader_db": 0.0},
                             {"name": "Bass", "role": "bass", "lufs": -45.0,
                              "fader_db": 0.0}])
    assert loud["lowered_for_headroom_db"] > 0
    assert max(m["new_fader_db"] for m in loud["moves"]) <= 6.0


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
    result = call_tool(app, "live_mix_balance", {"tracks": ["Kick", "Sub", "Pad", "Hats"],
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
    result = call_tool(app, "live_mix_balance", {"genre": "dubstep", "apply": False,
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


def test_master_auto_trims_a_hot_mix_first(app, song, mix_set):
    for track in song.tracks:
        track.mixer_device.volume.value = 0.85              # 0 dB: peaks near full scale
    result = call_tool(app, "live_master_auto", {"destination": "streaming", "seconds": 3})
    assert result["headroom_trim_db"] < -3 and fader_db(mix_set.track("Kick")) < -3
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
                                    "max": max(v, 1000.0)}
                                   for n, v in mix.CHANNEL_NEUTRAL.items()])
    plan = call_tool(app, "live_mix_auto", {"apply": False, "seconds": 3})
    assert plan["applied"] is False and {t["name"] for t in plan["tracks"]} == {
        "Kick", "Sub", "Pad", "Hats"}
    result = call_tool(app, "live_mix_auto", {"seconds": 3, "balance": True})
    assert result["applied"]
    by_name = {t["name"]: t for t in result["tracks"]}
    assert by_name["Hats"]["role"] == "hats" and by_name["Hats"]["values"]["HP Freq"] == 300.0
    assert by_name["Sub"]["values"]["Bass Mono"] == 1.0 and "after" in by_name["Sub"]
    strip = next(d for d in mix_set.track("Hats").devices if d.name == "LiveBridge Channel")
    assert {p.name: p.value for p in strip.parameters}["HP Freq"] == 300.0
    assert "parts" in result["balance"] or "plan" in result["balance"]
    for bad in ({"seconds": 1}, {"master_chain": "rack"}, {"roles": {"Kick": "tuba"}}):
        assert call_tool(app, "live_mix_auto", bad)["type"] == "bad_args", bad


def test_mix_auto_without_channels(app, song, mix_set):
    answer = call_tool(app, "live_mix_auto", {})
    assert answer["type"] == "not_found" and "LiveBridge Channel" in answer["error"]
