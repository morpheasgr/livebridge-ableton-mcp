# Surge XT sound design for agents

Companion to `SKILL.md` for Surge XT (the free, open-source synth by the Surge Synth Team).
Everything here was worked out from patch files and checked in Live on the user's Surge XT
1.4.0 (Windows); `docs/SURGE_FORMAT.md` has the file format.

## Two routes, used together

| What | How |
|---|---|
| Routings (any source onto any knob), step sequencers, tempo sync, wavetables, oscillator / filter / FX types, any patch value | write a patch: `live_surge_write` |
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
- **FX**: only slots the template already uses can be changed (Init Saw has none: empty slots
  are not in the file). Otherwise set the FX type in Live after loading (`FX A1 FX Type`).

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
  exciter, Twist engine, Alias shape) — unexposed controls always keep the preset's values.
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
  steps of 1 and -1.
- **Wavetable pad**: wavetable on osc 1 and 2 (different tables), `a_osc2_pitch` 0.1 (slight
  detune), Amp EG attack 0..1 (1-2 s), release 1..2, slow LFO (`a_lfo0_rate` -3 ≈ 0.125 Hz)
  on `a_osc1_param0` (position) depth 0.5, filter `"LP 12 dB"` cutoff 20..30, then FX Reverb 2
  in Live.
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

- **Silence / no movement**: a filter that is Off, an LFO at 1 Hz free-running, an
  oscillator muted (`a_mute_o2` = 1 in Init Saw for osc 2 and 3), a routing with a tiny depth.
- **Exposed knob back to a default**: it was not converted on load (see `warning`); set it
  again, or leave it unexposed.
- **A written patch not in Surge's browser**: refresh the browser; patches are in
  `Documents\Surge Synth Team\Surge XT\Patches\LiveBridge`.
- **Nightly builds**: the writer refuses patch revisions newer than it knows; the
  arpeggiator (nightlies) is kept but not written.
