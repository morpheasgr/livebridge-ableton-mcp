# 9 · Mix and master by measurement

> **You:** Mix and master this track for club playback. It's melodic techno; the fullest part is
> the drop at bars 113-128. Don't touch the lead's level. Show me the plan first.

|  |  |
|---|---|
| **Shows** | channel strips set per role, faders balanced by role, a master driven to a loudness target, a plan before any change, protected parts, a rerun that changes nothing, a comparison with a released master |
| **Needs** | Max for Live with **Max 9** (Live 12.2+) for the LiveBridge Channel and Master devices · the audio analysis libraries for the comparison |
| **Tool calls** | 4-6 (each mix run is one call; long runs answer as a background job) |

Claude doesn't guess levels. It plays the section, measures every part and the master with the
LiveBridge devices, decides by role, sets everything, and measures again. Every move comes with
its reason, and a second run on a settled mix changes nothing.

## What Claude does

**1. Plan without changing anything.** The tool puts its devices in place, a LiveBridge Channel
at the end of each track (a channel strip, neutral at rest) and an Engine on the master, plays the
drop, and reports what it would do.

```python
live_mix_auto(genre="melodic techno", section_bars=[113, 128], protect=["Lead KEY"],
              apply=false, detail="full")
```

```json
{"applied": false,
 "placed": {"placed": ["Kick", "Bongos", "BS Bass", "PD Pad", "Lead KEY", "...15 tracks"],
            "replaced": [], "skipped": []},
 "tracks": [{"name": "FX SnareRiser", "role": "snare", "role_from": "name"},
            {"name": "Lead KEY", "role": "keys", "role_from": "name"}, "..."],
 "balance": {"applied": false, "genre": "melodic_house",
             "plan": {"moves": ["...4 moves, each with from -> to"]},
             "left_out": {"DR SN FX": "one-shot: plays 27 % of the window"},
             "bands": [{"band": "mid", "delta_db": 6.4, "fix": "tracks",
                        "tracks": [{"name": "Lead KEY"}, {"name": "PD Pad"}]}]},
 "notes": ["apply=false: the balance plan is for the strips as they are now — setting the "
           "strips changes the parts' levels, so a real run's moves differ."]}
```

Roles come from the names first. Two were wrong here: "FX SnareRiser" is a riser, not a snare,
and "Lead KEY" is the lead, not keys. You correct them once, in the next call.

**2. Mix and master.**

```python
live_mix_auto(genre="melodic techno", section_bars=[113, 128], protect=["Lead KEY"],
              roles={"FX SnareRiser": "fx", "Lead KEY": "lead", "PL Pluick Arp": "keys"},
              master="club", master_chain="livebridge", detail="full")
```

```json
{"applied": true,
 "tracks": [
   {"name": "Kick", "role": "kick", "changes": {"Comp Ratio": "1 -> 4", "...": "..."},
    "reasons": ["crest 14.4 dB vs ~11 for a kick: 4:1 ..."],
    "balance": "unchanged (within the tolerance)"},
   {"name": "Lead KEY", "role": "lead", "changes": {"Trim": "0 -> 2.2"},
    "after": {"lufs": -13.1, "before_trim": true, "lufs_with_trim": -10.9},
    "balance": "protected"},
   "..."],
 "balance": {"plan": {"moves": [
   {"name": "Bongos", "fader_db": -16.0, "new_fader_db": -18.0},
   {"name": "Shaker", "fader_db": -17.0, "new_fader_db": -23.0, "budget_capped": true},
   "...5 moves"]}},
 "master": {"inserted": ["LiveBridge Master"], "before": {"lufs": -21.5, "crest_db": 13.1},
            "after": {"lufs": -8.4, "peak_db": -0.4, "crest_db": 10.6},
            "limiter_gain_db": 14.2, "peak_reduction_db": 3.8,
            "notes": ["14.2 dB of drive into the limiter: the loudest peaks lose 3.8 dB ..."]},
 "notes": ["Lead KEY is protected: Trim +2.2 dB keeps its level where it was (-10.9 LUFS) ..."]}
```

The protected lead keeps its fader and its level: its strip's Trim gives back what the
high-pass and EQ took. The master went from -21.5 to -8.4 LUFS with the peaks just under the
-0.3 dB ceiling.

**3. Run it again.** Nothing should change on a settled mix, and nothing does:

```python
live_mix_auto(genre="melodic techno", section_bars=[113, 128], protect=["Lead KEY"],
              roles={"FX SnareRiser": "fx", "Lead KEY": "lead", "PL Pluick Arp": "keys"},
              master="club", master_chain="livebridge")
```

```json
{"tracks": [{"name": "Kick", "changes": {}}, "..."],
 "balance": {"moves": []},
 "master": {"before": {"lufs": -22.6}, "after": {"lufs": -7.9}, "limiter_gain_db": 15.7,
            "steps": [{"gain_db": 15.7, "lufs": -7.9}]}}
```

The master starts its search where the last run ended: one step.

**4. Compare with a released master.** Export the drop (bars 113-128), then measure it against
the same 32 seconds of a professionally mastered version:

```python
live_audio_analyze(file_path="~/Music/drop_bounce.wav", reference="~/Music/release.wav",
                   reference_start=224, reference_duration=32, genre="melodic_house")
```

```json
{"loudness": {"lufs": -7.8, "crest_db": 9.6},
 "reference": {"loudness": {"lufs": -6.7, "crest_db": 7.2}},
 "comparison": {"lufs": -1.1, "notes": [
   "mid (500-2k Hz) is 6.9 dB louder than the reference.",
   "sub (20-60 Hz) is 6.0 dB quieter than the reference."]}}
```

What's left is the mix itself: the protected lead carries most of the mid, and the release has
more sub. Those are your calls; the tool reports them (`bands` with `fix: "tracks"`) instead of
overriding your choices.

## Variations

- **Your own plug-ins on the tracks:** `live_mix_auto(..., strips="measure")` places no channel
  strips. It only measures, balances the faders and masters.
- **Streaming instead of club:** `master="streaming"` (-14 LUFS, -1 dB ceiling); one master per
  destination.
- **Only the faders:** `live_mix_balance(genre="melodic techno", section_bars=[113, 128])`.
- **Kick and bass fighting:** `live_mix_phase(track="BS Bass", section_bars=[113, 113])` and
  `live_mix_unmask(track="BS Bass", against="Kick", section_bars=[113, 128])`.

These calls were run against Ableton Live 12.4.16 Suite on Windows (October 2026), on a finished
16-track melodic techno project, over several runs while the mix tools were being tuned; the JSON
is real output from those runs, shortened, and the numbers differ a little between runs (a few
tenths of a dB is normal). The release comparison used the track's professionally mastered
version.
