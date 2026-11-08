# Surge XT sound design for agents

Companion to `SKILL.md` for Surge XT (the free, open-source synth by the Surge Synth Team).
Everything here was worked out from patch files and checked in Live on the user's Surge XT
1.4.0 (Windows); `docs/SURGE_FORMAT.md` has the file format.

## Two routes, used together

| What | How |
|---|---|
| Routings (any source onto any knob), step sequencers, tempo sync, wavetables, effects in any slot, oscillator / filter / FX types, any patch value | write a patch: `live_surge_write` |
| Tweaking while listening, automation, macros, undo | expose knobs in Live: `live_plugin_expose(plugin="Surge XT", preset_file=...)`, then `live_device_set_parameters` |

Typical run: write the patch, load its `.vstpreset` with the knobs you want to play with, tweak,
listen with the Engine. Modulation routings, step sequences and MSEGs are never host
parameters: they only exist in the patch.

## Writing a patch (`live_surge_write`)

```python
live_surge_write(
    name="LB Stepped Acid",
    parameters={"a_filter1_type": "LP 24 dB", "a_filter1_subtype": "Driven",
                "a_filter1_cutoff": -12.0, "a_filter1_resonance": 0.6},
    routings=[{"target": "a_filter1_cutoff", "source": "lfo1", "depth": 24}],
    step_sequences=[{"lfo": 1, "steps": [1, -1, 0.5, -1, 1, -1, 0.2, -1],
                     "loop": [1, 8], "rate": "1/16"}],
)
```

- Starts from Surge's **Init Saw** (or `template=`, any `.fxp` or Surge `.vstpreset`). Writes
  `Patches/LiveBridge/<name>.fxp` (Surge's browser) and `<name>.vstpreset` beside it (Live).
- **Names** are the patch's: `a_` scene A, `b_` scene B; `a_osc1_*`, `a_filter1_*`, `a_env1_*`
  (Amp EG) / `a_env2_*` (Filter EG), `a_lfo0..5_*` = LFO 1-6, `a_lfo6..11_*` = scene LFO 1-6,
  `fx1..16_*` FX slots, `volume` global.
- **Units** are the patch's: cutoff and highpass in semitones from 440 Hz (-60..70; -12 =
  220 Hz, 3 = 523 Hz), LFO rate log2 Hz (0 = 1 Hz), envelope times log2 seconds (-8..5),
  pitch in semitones, levels and percentages as 0..1 (bipolar -1..1).
- **Types by name**: filter types `"LP 12 dB"`, `"LP 24 dB"`, `"LP Vintage Ladder"`,
  `"LP OB-Xd 24 dB"`, `"LP K35"`, `"LP Diode Ladder"`, `"HP 24 dB"`, `"BP 12 dB"`,
  `"N 12 dB"`, `"MULTI Fast SVF"` ... with subtypes (`"Standard"`, `"Driven"`, `"Clean"` ...);
  oscillator types `"Classic"`, `"Sine"`, `"Wavetable"`, `"Window"`, `"Modern"`, `"FM2"`,
  `"FM3"`, `"String"`, `"Twist"`, `"Alias"`; FX types `"Reverb 2"`, `"Delay"`, `"Chorus"`,
  `"Distortion"`, `"Waveshaper"`, `"Phaser"`, `"Flanger"` ... (32). Types apply first.
- **Oscillator type switch**: the oscillator's seven controls start from Surge's own values for
  that type (String, Twist and Alias keep the previous ones: set them in Live).
- **FX**: any slot, empty or not, with `fx=` (next section).

### Effects (`fx=`)

```python
live_surge_write(name="LB Wet Pad", fx=[
    {"slot": "A1", "type": "Chorus", "values": {"Rate": "0.5 Hz", "Depth": "40 %", "Mix": "50 %"}},
    {"slot": "A2", "type": "Reverb 2", "values": {"Decay Time": "2 s", "Mix": "30 %"}},
], parameters={...})
```

- **Slots**: `A1`-`A4` scene A inserts, `B1`-`B4` scene B, `S1`-`S4` sends, `G1`-`G4` global
  (master). A send only sounds when the scene sends to it: `a_send_fx_1`..`4` for S1-S4 (0..1.59,
  1 = 0 dB, Init Saw sends nothing; `warnings` says so). Prefer the inserts.
- **A new type starts from Surge's own values** for it (measured in Surge XT 1.4.0); the
  same type again keeps the slot's values; `"type": "Off"` clears it. `values` alone changes
  the slot's current effect.
- **Control names** are Surge's own labels (read in Live by local Claude): what
  `live_surge_routings` lists under `effects`, the table below, or the label as Live shows it
  ("FX A1 Delay Time - Left" -> `"Left"` or `"Delay Time - Left"`); `"p0"`..`"p11"` always
  work, and a wrong name gets the list in the error.
- **Give values with their unit** — `"2 s"`, `"250 ms"`, `"4 Hz"`, `"1.5 kHz"`, `"30 %"`,
  `"-6 dB"`, `"10 cents"` — and the writer converts them. A bare number is the patch's own
  unit, which is easy to get wrong: Decay Time `2.0` is 2^2 = **4 s**.
- **Patch units** (bare numbers): Hz controls (low/high cut, EQ freq, filters) in semitones from 440
  Hz (-60..70), times log2 seconds (Delay `Left` -1 = 0.5 s, Reverb 2 `Decay Time` 1 = 2 s,
  `Pre-Delay` -4 = 62 ms), rates log2 Hz, % as 0..1 (bipolar -1..1), dB as dB, EQ bandwidth in
  octaves, Delay `Width` / Reverb `Width` in dB.
- **Modulate** an effect with a routing onto `fx<N>_p<k>`: N is the slot in patch order
  (A1=1, A2=2, B1=3, B2=4, S1=5, S2=6, G1=7, G2=8, A3=9, A4=10, B3=11, B4=12, S3=13, S4=14,
  G3=15, G4=16), k the control (Reverb 2 Mix = p9).
- Airwindows' effect is chosen by number (`"FX"`), its controls are `Param 0`-`Param 9`;
  Convolution needs an impulse response loaded in Surge.

| Type | Controls (p0, p1, ...; `-` unused) |
|---|---|
| Delay | Left, Right, Feedback, Crossfeed, Low Cut, High Cut, Rate, Depth, Channel, -, Mix, Width |
| Reverb 1 | Pre-Delay, Room Shape, Size, Decay Time, HF Damping, Low Cut, Peak Freq, Peak Gain, High Cut, Mix, Width |
| Phaser | Center, Feedback, Sharpness, Rate, Depth, Stereo, Mix, Width, Count, Spread, Waveform, Tone |
| Rotary | Horn Rate, Doppler, Tremolo, Rotor Rate, Drive, Model, Width, Mix |
| Distortion | Pre-EQ Gain, Pre-EQ Frequency, Pre-EQ Bandwidth, Pre-EQ High Cut, Drive, Feedback, Post-EQ Gain, Post-EQ Frequency, Post-EQ Bandwidth, Post-EQ High Cut, Output Gain, Model |
| EQ | Gain 1, Frequency 1, Bandwidth 1, Gain 2, Frequency 2, Bandwidth 2, Gain 3, Frequency 3, Bandwidth 3, Gain, Mix |
| Freq Shift | Left, Right, Time, Feedback, Mix |
| Conditioner | Bass, Treble, Width, Balance, Threshold, Attack Rate, Release Rate, Gain, Side Low Cut |
| Chorus | Time, Rate, Depth, Feedback, Low Cut, High Cut, Mix, Width |
| Vocoder | Gain, Gate, Env Follow, Q, -, Bands, Min Frequency, Max Frequency, Input, Range, Center, Mix |
| Reverb 2 | Pre-Delay, Room Size, Decay Time, Diffusion, Buildup, Modulation, LF Damping, HF Damping, Width, Mix |
| Flanger | Mode, Waveform, Rate, Depth, Count, Base Pitch, Spacing, Feedback, HF Damping, Width, Mix |
| Ring Mod | Shape, Frequency, Unison Detune, Unison Voices, Forward Bias, Linear Region, Low Cut, High Cut, Mix |
| Airwindows | FX, Param 0, Param 1, Param 2, Param 3, Param 4, Param 5, Param 6, Param 7, Param 8, Param 9 |
| Neuron | Drive, Squash, Stab, Asymmetry, Bias, Comb Frequency, Comb Separation, Waveform, Rate, Depth, Width, Output Gain |
| Graphic EQ | 30 Hz, 60 Hz, 120 Hz, 250 Hz, 500 Hz, 1 kHz, 2 kHz, 4 kHz, 8 kHz, 12 kHz, 16 kHz, Gain |
| Resonator | Frequency 1, Resonance 1, Gain 1, Frequency 2, Resonance 2, Gain 2, Frequency 3, Resonance 3, Gain 3, Mode, Gain, Mix |
| CHOW | Threshold, Ratio, Flip, Mix |
| Exciter | Drive, Tone, Attack, Release, Mix |
| Ensemble | Anti-Alias Filter, Frequency 1, Depth 1, Frequency 2, Depth 2, Delay Type, Clock Rate, Saturation, Feedback, Width, Mix, Reconstruction Filter |
| Combulator | Extra Noise, Center, Offset 2, Offset 3, Feedback, Tone, Comb 1, Comb 2, Comb 3, Pan 2, Pan 3, Mix |
| Nimbus | Mode, Quality, Position, Size, Pitch, Density, Texture, Spread, Freeze, Feedback, Reverb, Mix |
| Tape | Drive, Saturation, Bias, Tone, Speed, Gap, Spacing, Thickness, Depth, Amount, Variance, Mix |
| Treemonster | Threshold, Speed, High Cut, Low Cut, Pitch, Ring Modulation, Width, Mix |
| Waveshaper | Pre Low Cut, Pre High Cut, Shape, Bias, Drive, Post Low Cut, Post High Cut, Gain, Mix |
| Mid-Side Tool | Matrix, Mid Filter Low Cut, Mid Filter Gain, Mid Filter Frequency, Mid Filter High Cut, Side Filter Low Cut, Side Filter Gain, Side Filter Frequency, Side Filter High Cut, Mid Gain, Side Gain, Balance |
| Spring Reverb | Size, Decay, Reflections, HF Damping, Spin, Chaos, Knock, Mix |
| Bonsai | Input Gain, Bass Boost, Bass Distort, Bias Filter, Saturation Type, Saturation, Noise Sensitivity, Noise Gain, Dull, Output Gain, Mix |
| Audio In | Audio Input Channel, Audio Input Pan, Audio Input Level, Effect Input Channel, Effect Input Pan, Effect Input Level, Scene Input Channel, Scene Input Pan, Scene Input Level, Width, Mix |
| Floaty Delay | Time, Playrate, Feedback, Cutoff, Resonance, Warp Rate, Warp Width, Pitch Depth, Filter Depth, Low Cut, Mix |
| Convolution | Pre-Delay, Size, Start, Reverse From, Tilt Center, Tilt Slope, Low Cut, High Cut, Mix |

Ensemble's Delay Type takes its entry by name: `"Digital Delay"`, `"BBD 128 Stages"` ...
`"BBD 4096 Stages"`.

Airwindows' Param 0-9 are the chosen Airwindows effect's controls (AD Clip: Boost, Soften,
Enhance, Mode); Surge relabels them per effect.

### Routings

`{"target": "<patch name>", "source": "...", "depth": ...}`; depth in the target's units
(semitones for pitch and cutoff, 1.0 = 100 % for levels), `"remove": true` deletes one; a
depth of 0 is refused (Surge drops such routings). Sources: `velocity`, `keytrack`,
`polyat`, `aftertouch`, `pitchbend`, `modwheel`, `macro1`-`macro8`, `ampeg`, `filtereg`,
`lfo1`-`lfo6`, `slfo1`-`slfo6` (scene LFOs), `releasevelocity`, `lowestkey`, `highestkey`.

### Step sequencers

`{"lfo": 1, "steps": [...], "loop": [first, last], "rate": "1/16", "shuffle": 0.2}` turns
LFO 1-6 (or 7-12 = scene LFO 1-6) into a step sequencer: up to 16 steps from -1 to 1 (0 =
middle), the loop, the tempo-synced rate (`"1/16"`, dotted `"1/8d"`, triplet `"1/8t"`).
**Always give a rate**: free-running it moves one step per LFO cycle (1 Hz = one step a
second). Route the LFO somewhere to hear it, and make sure that filter is not **Off** (Init
Saw's filters are Off — the result's `warnings` say so).

### Wavetables

`wavetables=[{"osc": 1, "file": "C:/ProgramData/Surge XT/wavetables/Basic/Sine To Sawtooth.wt"}]`
embeds a Surge `.wt` file and makes that oscillator a Wavetable oscillator (`"window": true`
for Window). Surge's factory tables: `C:\ProgramData\Surge XT\wavetables` (Windows),
`/Library/Application Support/Surge XT/wavetables` (macOS). The position is the oscillator's
first control (`A Osc 1 Shape` in the map; Surge calls it Morph).

## Loading and tweaking in Live

```python
live_plugin_expose(plugin="Surge XT", preset_file="<vstpreset_path>",
                   parameters=["sound_design"], new_track=True, track_name="Acid")
```

- **Groups**: `sound_design` (121 controls of scene A), `sound_design b`, and per scene
  `osc a`, `filter a`, `env a`, `lfo a`, `scene lfo a`, `mixer a`, `voice a` (and `... b`),
  `fx a1` ... `fx g4`, `macros`, `global`.
- **Restore**: Surge applies a preset after Live has read the exposed knobs, so LiveBridge
  sets them again from the preset (result `restored: N`). A knob it cannot convert is named in
  `warning` (rare oscillator controls: Sine feedback, Window shape, FM3's first ratio, String
  exciter, Twist engine, Alias shape; a few FX menus, Airwindows' controls) — unexposed controls always keep the preset's values.
  FX controls convert per FX type.
- **Knob names follow the oscillator type**: Surge renames `A Osc 1 Shape` to `A Osc 1 M1
  Amount` (FM2), `A Osc 1 Morph` (Wavetable) ... Expose by either name; afterwards address
  knobs by the index the expose result lists.
- **Setting values**: display text works — `"220 Hz"`, `"1.5 kHz"`, `"50 %"`, `"+5 st"`,
  `"4 Hz"`, `"1.2 s"`, `"LP 24 dB"`. Osc pitch spans only +/-7 semitones: use the octave for
  more. LFO rates show in Hz in Live; tempo sync is set by the writer (`rate=`).
- **Macros** `M1`-`M8`: after a set the display lags one read (read twice).
- The patch name gains a `*` after loading (LiveBridge set values): harmless.

## Recipes

Each is one `live_surge_write` call plus a few knobs in Live.

- **Stepped filter (acid / trance gate)**: the example above. Resonance 0.5-0.7, cutoff
  -24..-12, depth 24-36, rate 1/16; for a gate route the step LFO to `a_volume` instead and use
  steps of 1 and -1. Add `fx=[{"slot": "A1", "type": "Delay", "values": {"Mix": 0.25}}]`.
- **Wavetable pad**: wavetable on osc 1 and 2 (different tables), `a_osc2_pitch` 0.1 (slight
  detune), Amp EG attack 0..1 (1-2 s), release 1..2, slow LFO (`a_lfo0_rate` -3 ≈ 0.125 Hz)
  on `a_osc1_param0` (position) depth 0.5, filter `"LP 12 dB"` cutoff 20..30, FX Chorus on A1
  and Reverb 2 on A2 (`Decay Time` 2-3, `Mix` 0.3-0.4).
- **FM bass**: `a_osc1_type` `"FM2"`, `a_osc1_param0` 0.3-0.5 (M1 amount), Filter EG
  (`filtereg`) onto `a_osc1_param0` depth 0.4 with a short decay (`a_env2_decay` -4), mono
  (`a_polymode` in Live: Mono), octave -1.
- **Reese**: osc 1 and 2 Classic saws (`a_osc1_param0` 0 = saw), `a_osc2_pitch` 0.15,
  `a_mute_o2` 0, filter `"LP 24 dB"` cutoff -12, slow LFO onto cutoff depth 6, unison voices
  3-5 (`a_osc1_param6`).
- **Pluck**: Amp EG attack -8, decay -2, sustain 0; Filter EG decay -3 onto cutoff depth 36
  (`filtereg`), cutoff -36, filter `"LP 24 dB"`.
- **Vibrato lead**: scene LFO 1 (`slfo1`) onto `a_osc1_pitch` depth 0.3, LFO rate 2.5 (≈ 5.6
  Hz), modwheel onto that routing's LFO amplitude for expression.

## When something sounds wrong

- **An effect not heard**: a send slot (S1-S4) with the scene's send at 0; Mix at 0.
- **Silence / no movement**: a filter that is Off, an LFO at 1 Hz free-running, an
  oscillator muted (`a_mute_o2` = 1 in Init Saw for osc 2 and 3), a routing with a tiny depth.
- **Exposed knob back to a default**: it was not converted on load (see `warning`); set it
  again, or leave it unexposed.
- **A written patch not in Surge's browser**: refresh the browser; patches are in
  `Documents\Surge Synth Team\Surge XT\Patches\LiveBridge`.
- **Nightly builds**: the writer refuses patch revisions newer than it knows; the
  arpeggiator (nightlies) is kept but not written.
