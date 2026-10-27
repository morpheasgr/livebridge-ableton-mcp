# Sound design in Serum 2

> The 128 knobs below are what Live can reach. For the **whole patch** — wavetable selection, oscillator modes, matrix routes, FX order — design the preset file instead: `SERUM_PRESETS.md` (`live_preset_find`, `live_preset_edit`, `live_preset_load`); §6 below lists every Serum 2 control, knob or not.


Companion to `SKILL.md` (§5 explains the mechanics). This file is the *sound design*: which
Serum 2 parameters make which sound, recipes for the classic electronic-music patches, and the
limits of what Live's API lets you change. Read it before you build or tweak a Serum sound.
Exact names below are the ones `live_plugin_expose` exposes. The user's ears decide; describe
what you changed in synth terms ("opened the filter to 1.2 kHz, more unison detune").

## 1. The workflow

1. **Load it controllable**, once per sound:
   `live_plugin_expose(plugin="Serum 2", parameters=["sound_design", "Sub Shape", "Porta
   Always", "Mod 1 Amount", "Mod 2 Amount", "Mod 3 Amount", "Mod 4 Amount"], new_track=true,
   track_name="Bass", macros={"1": "Filter 1 Freq", "2": "A Uni Detune"}, editor_open=false)`.
   `sound_design` is 120 curated controls; the extra names here fill the rack to 126 of the 128
   allowed. Keep the returned `device` path: every later call uses it.
2. **Start from a sound**, not from silence, when the user has one in mind:
   `live_plugin_preset_files(plugin="Serum 2", filter="bass", embeddable_only=true)` ->
   `live_plugin_expose(..., preset_file=<path>)`. Only `.vstpreset` and Live presets embed;
   `.SerumPreset` files load only in Serum's own browser (`live_plugin_set(editor_open=true)`
   and ask the user to pick it). Without a preset the rack starts from Serum's **Init** patch.
3. **Read before you write**: `live_device_parameters(device=<path>, filter="Filter 1")` shows
   each parameter's display text, so you know its units.
4. **Set many at once** with `live_device_set_parameters(device=<path>, values={...})`: one undo
   step, all validated first. Values can be:
   - the text Serum shows: `"800 Hz"`, `"1.2 kHz"`, `"20 ms"`, `"2.5 s"`, `"35 %"`, `"-6 dB"`,
     `"-1 oct"`, `"15 cents"`, `"On"`, `"MG Low 24"`, `"1/8"`. Units are converted ("2500 ms" =
     "2.5 s").
   - a number from 0 to 1. Plug-in parameters are 0..1 inside Live, so a number always works;
     use it when you don't know the display format (detune, blend, levels).
   A wrong option name (e.g. a filter type) fails with the display range and examples in the
   message: read it and retry with a listed value.
5. **Iterate by ear**: after each change, tell the user what to listen for and ask. Small moves
   (one or two parameters) between listens beat a 30-parameter rewrite.
6. **Expose first, design second.** Re-exposing (or `replace=true`) restarts the sound from the
   preset or Init, because Live cannot read a plug-in's state. If you need parameters that are
   not exposed, add them *before* designing, or carry the sound over: `live_sound_save(name=...,
   device=<path>)`, re-expose, then `live_sound_load(name=..., device=<new path>)` (the knob
   values come back; wavetables and routings come from the preset as before).
7. **Keep what the user likes**: `live_sound_save(name="Dark Reese", device=<path>,
   tags=["bass", "serum"])`; next session `live_sound_list(tag="serum")` and `live_sound_load`
   onto a freshly exposed Serum 2.

## 2. What the API can and cannot change

Serum 2 has 541 mapped parameters (any 128 at a time). Everything with a knob is a parameter.
These are **not** parameters, so they come from the preset or from the user in Serum's window:

| Not controllable | What to do |
|---|---|
| **Which wavetable** an oscillator plays | Init's oscillators play Serum's default table (a bright saw-like wave; `A WT Pos` scans it). For Basic Shapes (sine/triangle/square), a vocal table, a growl table etc., start from a preset that has it, or ask the user to pick the table (one click in Serum). |
| **Mod matrix routings** (source -> destination) | `Mod n Amount` / `Mod n Out` set the depth of slot *n*, but cannot create a routing. Init has **no routings**, so Env 2-4, LFOs 1-10 and Serum's own Macros 1-8 do nothing until something is routed. Ask the user to drag the source onto the target once (e.g. Env 2 onto Filter 1 Cutoff); routings are numbered in the order they are made, so the first becomes `Mod 1 Amount`, which you can then set and automate. |
| **FX modules** (which effect sits in each FX slot) | `FX Main Param 1..16` drive the slots of whatever FX the patch has; Init has none. Put effects **after the rack with Live devices** instead (`live_device_insert`): Saturator, Roar, Multiband Dynamics (OTT-style), Chorus-Ensemble, Reverb, Delay. These are fully controllable and automatable. |
| **Saving a Serum preset** | Ask the user to save in Serum, or to save the rack (Live device save button). |

Rule of thumb: **Live's own effects and rack macros for anything you want to automate later;
Serum's knobs for the core tone.** Rack macros (`live_rack_macros(device="Serum 2 Rack",
values={"Filter 1 Freq": 60})`) are the cleanest automation targets: they are what the user
grabs on a controller, and `live_automation_shape(device="Serum 2 Rack", parameter=...)`
targets the macro rather than Serum's knob of the same name.

## 3. Parameter cheat sheet (the `sound_design` set)

| Area | Parameters | Notes |
|---|---|---|
| Global | `Main Vol`, `Main Tuning`, `Transpose`, `Mono Toggle`, `Legato`, `Porta Time` (+ `Porta Always` if exposed) | `Main Vol` shows "50% [-9.0 dB]"; set "-6 dB". Mono + Legato + Porta Time = glide; with `Porta Always` Off, glide happens only on **overlapping** notes (303-style slides). |
| Osc A / B / C | `A Enable`, `A Level`, `A Pan`, `A Octave`, `A Semi`, `A Fine`, `A WT Pos`, `A Warp`, `A Warp 2`, `A Unison`, `A Uni Detune`, `A Uni Blend` (same for B, C) | `A WT Pos` 0..1 scans the table. `A Warp` / `A Warp 2` are the two warp amounts (the warp *mode* is set in the patch). Unison "1".."16"; detune 0.1-0.3 is musical, 0.5+ is wild. Blend = how loud the detuned voices are vs the centre. |
| Sub / Noise | `Sub Enable`, `Sub Level`, `Sub Octave`, `Sub Coarse Pitch`, `Noise Enable`, `Noise Level`, `Noise Pitch`, `Noise Fine` (+ `Sub Shape`) | The sub is a clean sine (or `Sub Shape`) that follows the notes: the easiest way to a solid low end. |
| Filter routing | `A>Filter Balance`, `B>Filter Balance`, `C>Filter Balance`, `Noise>Filter Balance`, `Sub Osc>Filter Balance` | How much of each source goes through Filter 1 vs Filter 2. Keep the sub unfiltered on basses. |
| Filters 1 / 2 | `Filter 1 On`, `Filter 1 Type`, `Filter 1 Freq`, `Filter 1 Res`, `Filter 1 Drive`, `Filter 1 Var`, `Filter 1 Wet` | Init: Filter 1 off, 1011 Hz. Verified types: "MG Low 12", "MG Low 24" (smooth Moog-style low-pass); other types by name from the error list. Res above ~70 % starts to whistle. |
| Envelopes | `Env 1 Attack/Hold/Decay/Sustain/Release` ... `Env 4 ...` | **Env 1 is always the amp envelope.** Sustain shows in dB ("-6.0 dB"; 0 = silent). Env 2-4 need a mod routing (§2). |
| LFOs | `LFO 1 Rate` ... `LFO 4 Rate` | Synced display ("1/8", "1/4", "1 bar"). Need a routing to do anything. |
| Macros | `Macro 1` ... `Macro 8` | Serum's own macros: only useful if the preset maps them. |
| FX | `FX Main Param 1..16`, `Direct Vol`, `Bus 1 Vol`, `Bus 2 Vol` | Depend on the preset's FX (§2). |

Other groups for `parameters=`: `"osc a"`, `"filter 2"`, `"env"`, `"lfo"`, `"unison"` (Uni
Width, Stack, Span ...), `"mod"`, `"arp"`, `"routing"`, `"fx"`. Any name prefix works as a group.

## 4. Recipes

Start each from Init (or from a similar preset), apply the values, then shape by ear. Notes
and registers follow `PRODUCTION.md` §6; side-chain any bass to the kick
(`live_routing_route(method="sidechain")` after a Compressor).

**Sub bass** (house, techno, DnB, trap): pure weight, felt more than heard.
```python
live_device_set_parameters(device=SERUM, values={
    "A Enable": "Off", "Sub Enable": "On", "Sub Level": 0.8,
    "Mono Toggle": "On", "Legato": "On", "Porta Time": "20 ms",
    "Env 1 Attack": "2 ms", "Env 1 Sustain": "0 dB", "Env 1 Release": "60 ms",
    "Main Vol": "-6 dB"})
```
Notes C1-C2, long and monophonic. Add `Utility` (Bass Mono, or Width 0 %) after the rack.
For an 808: the same plus `"Porta Always": "On"`, `"Porta Time": "60 ms"` for slides between
every note, `Env 1 Release` 300-600 ms, and a `Saturator` after the rack for harmonics that
small speakers can hear.

**Reese** (DnB, neuro, dark techno): two detuned stacks beating against each other.
```python
live_device_set_parameters(device=SERUM, values={
    "A Unison": "4", "A Uni Detune": 0.2, "B Enable": "On", "B Fine": "25 cents",
    "B Unison": "4", "B Uni Detune": 0.25, "Sub Enable": "On", "Sub Level": 0.6,
    "Sub Osc>Filter Balance": 0, "Filter 1 On": "On", "Filter 1 Type": "MG Low 24",
    "Filter 1 Freq": "400 Hz", "Filter 1 Res": "20 %", "Filter 1 Drive": "30 %",
    "Mono Toggle": "On", "Legato": "On", "Porta Time": "120 ms",
    "Env 1 Attack": "5 ms", "Env 1 Release": "120 ms", "Main Vol": "-6 dB"})
```
Movement: macro 1 on `Filter 1 Freq`, then `live_automation_shape(shape="sine", period="2
bars", low=0.25, high=0.6, mode="events")`; detune (`B Fine` 15-35 cents) sets the beating
speed. Width only above the sub: Utility after the rack, or EQ Eight in M/S with the side
high-passed at 200 Hz. Darker: `Filter 1 Freq` 250 Hz; dirtier: `Roar` or `Saturator` after.

**Growl / neuro bass** (dubstep, bass music, 140-150 BPM): the growl is *movement* of
`A WT Pos`, `A Warp` and the filter. Best from a growl preset (vowel-like wavetable, FX
already set). From Init: `A Unison` "2", `A Warp` 0.4, `Filter 1 On` with a low-pass at
~800 Hz and `Filter 1 Drive` 50-70 %, mono. Then write per-note movement as clip envelopes:
`live_automation_shape(parameter="A WT Pos", shape="saw_up", period="1/8")`, a different period
on `A Warp` ("1/16" or "1/8T") and on `Filter 1 Freq`; changing the shapes per bar is the
"talking" quality. After the rack: `Roar` or `Saturator`, then `Multiband Dynamics` (OTT-style,
50-70 %), EQ cuts at harsh resonances, side-chain.

**Acid / 303 line** (acid house, acid techno): the filter is the instrument.
```python
live_device_set_parameters(device=SERUM, values={
    "Filter 1 On": "On", "Filter 1 Type": "MG Low 24", "Filter 1 Freq": "500 Hz",
    "Filter 1 Res": "65 %", "Filter 1 Drive": "40 %",
    "Mono Toggle": "On", "Legato": "On", "Porta Time": "80 ms", "Porta Always": "Off",
    "Env 1 Attack": "0 ms", "Env 1 Decay": "300 ms", "Env 1 Sustain": "-8 dB",
    "Env 1 Release": "60 ms"})
```
The squelch needs a filter envelope: ask the user to drag **Env 2 onto Filter 1 Cutoff** once
(it becomes `Mod 1`), then set `Env 2 Attack` "0 ms", `Env 2 Decay` "150 ms", `Env 2 Sustain`
0, `Mod 1 Amount` ~0.6. Without that routing, fake it with a clip envelope on `Filter 1 Freq`
(`shape="saw_down"`, `period="1/16"`) or use a 16th-step pattern of macro values. Slides =
overlapping notes; accents = velocity 110-127 (route velocity to cutoff in Serum for real
accent squelch). `Saturator` (drive 10 dB) after the rack is not optional.

**Supersaw** (trance, progressive, future bass chords and leads):
```python
live_device_set_parameters(device=SERUM, values={
    "A Unison": "7", "A Uni Detune": 0.25, "A Uni Blend": 0.75,
    "B Enable": "On", "B Octave": "+1 oct", "B Level": 0.5, "B Unison": "7",
    "B Uni Detune": 0.3, "Filter 1 On": "On", "Filter 1 Type": "MG Low 12",
    "Filter 1 Freq": "7 kHz", "Filter 1 Res": "10 %",
    "Env 1 Attack": "5 ms", "Env 1 Decay": "500 ms", "Env 1 Sustain": "-3 dB",
    "Env 1 Release": "300 ms", "Mono Toggle": "Off"})
```
Chords with spread voicings (`live_clip_write_chords(voicing="spread")` or `octave=3` with
`voice_leading=true`). After the rack: `Multiband Dynamics` (OTT-style, 50 %), a large reverb on
a return, EQ low cut at 150 Hz if a bass plays. Pads: `Env 1 Attack` 100-300 ms, `Release` 1-2 s.
Mono check: Utility Width 0 % for a moment; if the sound collapses, lower `A Uni Detune`.

**Pluck** (house, trance arps, melodic techno): short and bright.
```python
live_device_set_parameters(device=SERUM, values={
    "A Unison": "3", "A Uni Detune": 0.12, "Filter 1 On": "On",
    "Filter 1 Type": "MG Low 24", "Filter 1 Freq": "2 kHz", "Filter 1 Res": "25 %",
    "Env 1 Attack": "0 ms", "Env 1 Decay": "220 ms", "Env 1 Sustain": 0,
    "Env 1 Release": "180 ms"})
```
A real pluck closes the filter with the note: the Env 2 -> Filter 1 Cutoff routing (as in the
acid recipe) with `Env 2 Decay` 150-300 ms. Delay (dotted 1/8) and reverb sends make it.

**Pad** (breakdowns, ambient, deep house):
```python
live_device_set_parameters(device=SERUM, values={
    "A Unison": "4", "A Uni Detune": 0.15, "B Enable": "On", "B Fine": "7 cents",
    "B Octave": "+1 oct", "B Level": 0.4, "Filter 1 On": "On",
    "Filter 1 Type": "MG Low 12", "Filter 1 Freq": "1.8 kHz", "Filter 1 Res": "10 %",
    "Env 1 Attack": "600 ms", "Env 1 Decay": "1.5 s", "Env 1 Sustain": "-3 dB",
    "Env 1 Release": "2.5 s"})
```
Slow movement without a routing: a rack macro on `Filter 1 Freq` with
`live_automation_shape(shape="sine", period="4 bars", low=0.35, high=0.55)`; or on `A WT Pos`
for timbre drift. Chorus-Ensemble and a long reverb after the rack, low cut 150-200 Hz.

**Noise riser / sweep** (transitions): `A Enable` "Off", `Noise Enable` "On", `Noise Level`
0.7, Filter 1 on with a band-pass or low-pass type, then in the arrangement
`live_automation_record(parameter="Filter 1 Freq", shape="ramp_up", ...)` over the build
(or on a rack macro), plus a rising send to the reverb return. End exactly on the drop.

## 5. Making it fit the track

- **Low end**: one owner of the sub at a time. In a Serum bass with `Sub Enable`, keep the sub
  dry (`Sub Osc>Filter Balance` 0), mono, and side-chained; high-pass every other Serum patch
  (pads, leads, plucks) at 100-200 Hz with EQ Eight after the rack.
- **Level**: `Main Vol` around "-6 dB" leaves headroom; set balance on the track fader.
- **Check**: `live_mixer_meters(seconds=3)` while it plays (peak < ~0.9), and for a bass, bounce
  a few bars (`live_record_resample`) and run `live_audio_analyze`: `flags` show a weak or
  stereo low end, mud or harshness.
- **Variation over time**: never leave a Serum part static for more than 8 bars: automate a
  macro (filter, detune, WT position) slowly through each section, open it into drops, close it
  in breakdowns (`PRODUCTION.md` §3).

## 6. The whole synth: controls reference

Everything Serum 2 has, including what the 128 knobs cannot reach. Use it to **design a preset
file** (`SERUM_PRESETS.md`: find where a control is stored with `live_preset_search` /
`live_preset_compare`, then `live_preset_edit`) and to write exact manual steps. Adapted from
kasperikaivola's ableton-mcp-pro Serum 2 guide (MIT, `THIRD_PARTY_NOTICES.md`), which was
checked against Xfer's Serum 2 user guide (software 2.0.18, manual 1.0.3); later builds may
rename or add controls. A control listed here is not proof that it is exposed in Live or set in
the user's instance. Serum **2** has three oscillators and two filters: never assume Serum 1's
two oscillators and one filter.

**Where things are**: OSC A/B/C, SUB, NOISE and FILTER 1/2 on the oscillator page; FX and
MIXER for racks and routing; ENV/LFO/MATRIX for movement; ARP/CLIP for generated notes; GLOBAL
for voices, quality and tuning.

### Oscillators

OSC A, B and C each run in one of five modes — **Wavetable, Multisample, Sample, Granular,
Spectral** — and the mode decides which controls exist, so choose it first. Every oscillator
also has: enable; OCT / SEMI / FINE and pitch tracking; PHASE and RAND (with phase-memory
options); UNISON voices, DETUNE and BLEND (plus spread and stereo options); PAN and LEVEL; two
WARP slots (mode and amount, applied in order before the filter).

- **Wavetable**: the table is a named asset; WT POS scans its frames (a single-cycle sine or
  saw needs no scanning). Give the table's name and the position and its modulation range.
  Never step through table indices hoping to hit a name you haven't confirmed. The wavetable
  editor (drawing, harmonics, frame morphing, import from audio, image or formula) is manual.
- **Warp families**: sync adds harmonics; bend / asymmetry / PWM reshape the wave; mirror and
  remap change its geometry; FM / AM / RM need a modulator (FM from B needs B running, even
  when B's output is routed to None); phase distortion, folding, saturation and the oscillator
  filter change the harmonic balance. Check the exact mode label in the preset.
- **Multisample**: an instrument (SFZ) with its envelope, velocity tracking, randomization and
  timbre — acoustic or layered pitched foundations. Not interchangeable with one WAV.
- **Sample**: an audio file with root and tracking, start/end, loop start/end/mode and
  crossfade, scan and rate, slicing — one-shots, loops, tape-like gestures. Keep the start for
  transients; start after the transient for pads.
- **Granular**: a sample cut into overlapping grains.

  | Control | Decide |
  |---|---|
  | Start/end, loop, crossfade | Region and direction, smooth seam |
  | SCAN, X/Y | Automatic movement or a fixed position; path and rate |
  | WINDOW | Grain edge shape (not the amp envelope) |
  | DENSITY | Hz, tempo-synced or a grain count; Jump Start, Max Grains (CPU) |
  | LENGTH | ms, synced, or a percentage of the density period (not in grain-count mode) |
  | OFFSET / DIR / PITCH random, RAND LENGTH/PAN/LEVEL | Scatter in position, direction, pitch, length, stereo, level |

  Long overlapping grains make smooth textures; short sparse or synced grains add detail. Keep
  pitch randomization small on harmonic pads.
- **Spectral**: the source is resynthesized from its frequency content: start/end, lower and
  upper analysed frequency, loop, X/Y, SCAN, CUT, FILTER, MIX (CUT and FILTER depend on the
  spectral mode — not an ordinary low-pass). Frozen, evolving timbres and odd transition FX.

**SUB**: waveform, pitch, phase, pan, level, route. A centred sine routed Direct gives a clean
fundamental; not every patch needs one. **NOISE**: a sample (not necessarily white noise) with
one-shot/loop, start and random start, pitch and tracking, pan, level, route.

### Filters and routing

FILTER 1/2: power, type, cutoff (Hz), resonance, drive, VAR, mix, key tracking, output. VAR
means something different per type (a second frequency, a character control) — read it from
the type, never guess. Families: low/high/band/notch and combinations, comb/flanger, formant,
special models; tune comb and formant filters to the harmony when they must follow it.

Each oscillator, SUB and NOISE routes to **Filter** (FILTER 1, 2 or both), **Main** (into the
FX), **Direct** (past filters and FX) or **None** (silent, still usable as a modulator), plus
BUS 1/2 sends. Two enabled filters are not automatically in series: write down the whole path.
In the MIXER, don't count a dry signal twice (direct plus a dry bus).

### Modulation

- **ENV 1-4**: attack, hold, decay, sustain, release and curves. ENV 1 is the amp envelope;
  ENV 2-4 do nothing without a route. Legato and retrigger change them.
- **LFOs**: Normal, Path, Chaos (Lorenz, Rossler) or S&H; drawn shape; Free / Retrig /
  Envelope (one-shot) mode; mono or poly; synced or Hz rate; phase, delay, rise, smoothing.
- **MATRIX** route: source, optional aux source, destination, depth, polarity, curve, enabled.
  An envelope, LFO or macro without a route does nothing. Give the base value and both end
  points in real units. Sources: velocity, note, mod wheel, aftertouch, macros, random, MPE.
- **Macros**: name the gesture, route every destination with end points, give its start value.

### Effects

Three racks — **MAIN, BUS 1, BUS 2** — processed top to bottom; a module can appear twice, so
name the rack *and* the slot. For every module: enabled, its controls, MIX and output level.

| Module | Main controls |
|---|---|
| Bode | frequency shift, range, direction, delay (synced), feedback, balance, blur |
| Chorus | rate (sync), delay 1/2 ms, depth, feedback, wet low/high-pass |
| Compressor | single or multiband, threshold, ratio, attack, release, gain |
| Convolve | impulse file, size, tone, pre-delay, attack/decay/damp |
| Delay | normal / ping-pong / tap, synced or ms times L/R, link, feedback, wet filter frequency and Q |
| Distortion | algorithm, drive, filter type/cutoff/resonance pre or post |
| Equalizer | two bands: type, frequency, Q, gain |
| Filter | model, cutoff, resonance, drive, VAR (on the summed voices, not per voice) |
| Flanger / Phaser | rate (sync), depth, feedback, stereo phase; phaser poles and frequency |
| Hyper / Dimension | voices, rate, detune, retrigger; dimension size |
| Reverb | Plate, Hall, Vintage, Nitrous or Basin — each with its own controls (size is not decay time) |
| Splitter | low/high, low/mid/high or mid/side branches, each with its own modules and gain |
| Utility | polarity L/R, low/high-pass, mono bass and its frequency, width, pan |

Order matters: distortion before a filter tames its harmonics, after it restores edge; chorus
before reverb widens what the reverb hears; delay before reverb blurs the echoes, after it
echoes the whole tail.

### Performance and global

Polyphony, mono/legato, portamento time/curve/always, pitch-bend range, master level (long
releases with unison or grains multiply the voice count — set a budget). **ARP** and **CLIP**
generate notes inside Serum: keep both off when the Live clip should decide what plays.
**GLOBAL**: voice control (per-voice pan/detune/cutoff/envelope randomization), quality,
Serum 1 compatibility, tuning (A=440, tuning file, MTS-ESP) — keep the tuning unless asked.

### Recipe: Mystic Halo (new pitched FX texture)

An original proposal, not a factory patch: a slow upper-mid shimmer around held chord tones.
Start from Init (on a new track only); ARP/CLIP off, BUS sends 0, SUB/NOISE/C off, warps off.

| Area | Target |
|---|---|
| OSC A | Single-cycle triangle (confirm the table), tracking on, unison 3, detune ~0.06, blend 70 %, RAND 100 %, level 55 % |
| OSC B | Single-cycle sine, +1 oct +7 semitones, unison 1, RAND 100 %, level 14 % |
| Filter | A+B -> FILTER 1 -> Main; MG Low 12, 1800 Hz, resonance 12 %, drive 8 %; FILTER 2 off |
| ENV 1 | A 1200 ms, H 0, D 2400 ms, S 75 %, R 3800 ms |
| LFO 1 | sine 0.07 Hz free, mono -> Filter 1 cutoff 1400-2300 Hz |
| LFO 2 | sine 0.11 Hz free, mono -> OSC B level 10-18 % |
| MAIN FX | 1 Chorus 0.18 Hz, 8/13 ms, depth 20 %, mix 18 %; 2 Delay ping-pong dotted 1/8, feedback 24 %, wet 2.5 kHz, mix 14 %; 3 Reverb Hall size 70 %, pre-delay 30 ms, decay 4.8 s, mix 28 % |
| Voices | poly 12, no legato, bend ±2, master -12 dB |
| Gesture | mod wheel -> Filter 1 cutoff 1800-3500 Hz and Hall mix 28-38 % |

Notes: the root or fifth held for 2-4 bars, high register, a little overlap. Audition at
matched level; shorten the tail or the mix if it covers the lead.

### Improving an existing patch

Keep its preset, sources, FX and automation; pick the one change that answers the complaint:
- **Static**: a 0.09 Hz sine LFO on WT POS, ±5 % around its position (multi-frame tables), or
  cutoff ±2 semitones for a single-cycle source.
- **Flat attack**: ENV 2 (A 10 ms, D 350 ms, S 0, R 200 ms) -> cutoff, up one octave from its
  base — unless a route already does that.
- **Thin body**: a quiet second source, same octave, sine, single voice, ~18 dB under the main
  one, through the same filter (an octave down fights the bass).
- **No expression**: mod wheel -> cutoff up one octave, optionally one FX mix +8 points.

### Saving

A new preset under a new name, never over the user's. `live_preset_load` publishes the file as
a `.vstpreset` and loads it; saving inside Serum's own browser stays manual.
