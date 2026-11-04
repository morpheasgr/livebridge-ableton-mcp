# Massive sound design for agents

Companion to `SKILL.md` for Native Instruments **Massive** (classic, 1.x; not Massive X). Written
for LiveBridge from Massive's documented architecture and common sound-design practice.

## What LiveBridge can reach

- **The map ships with LiveBridge** (`massive_vst3.json`, 202 parameters measured on Live
  12.4.7, Windows), so `live_plugin_expose` works at once. At most 128 controls per exposure;
  the `sound_design` group (126) is the default working set. Other groups: `osc`, `osc 1|2|3`,
  `noise`, `feedback`, `mod osc`, `filter`, `filter 1|2`, `env`, `env 1..4`, `lfo`,
  `mod 5..8`, `fx`, `insert`, `master fx`, `voice`, `unison`, `output`, `routing`, `macros`.
- **Exposed:** oscillators 1-3 (on/off, wavetable, mode, position, the mode's second control,
  pitch, level, filter 1/2 balance), noise, feedback, the modulation oscillator, both filters
  (type, cutoff, bandwidth, resonance, output gain, serial/parallel, crossfade), envelopes 1-4
  (delay, attack, decay, sustain and release times and levels, loop morph, velocity, key
  tracking), mod slots 5-8 (rate, amplitude, sync and its fraction, restart,
  crossfade/glide), inserts, master effects, EQ, unison, voice/trigger mode, glide, vibrato,
  pan, master volume, macros 1-8 (not in `sound_design`: Live shows them under their labels,
  "2".."8", so name them that way if needed).
- **Not exposed to the host:** modulation routings (Massive's drag-and-drop) and their
  amounts, LFO waveshapes, stepper steps, performer curves, macro assignments. They live in
  the patch: start from a preset that holds them (below) and drive them through the sources'
  own levels (`MODn-AMP`, the envelope levels).
- **Envelope 4 is the amp envelope**: it shapes every note without a routing. Envelopes 1-3
  and slots 5-8 only do something when the patch routes them somewhere.
- **Setting values** (measured on the user's Massive): the knobs display 0-100 without a unit.
  Pass them as text (`"45"`) or with `normalized=true` (`0.45`) in
  `live_device_set_parameters` — a bare number is Live's internal 0-1 value, so `45` means the
  maximum. Types, wavetables, modes and the voice mode are not stepped for Live (no choice
  list to read): set them by name (`"Lowpass 4"` worked) and read the current one with
  `live_device_parameters(track=..., device=..., filter="FILTER1")`. The filter and master
  effect names are listed below; wavetable names: read the current display, or step through
  `OSC1-WAVETABLE` with normalized values and read each display.

## Starting points (templates)

Two Live presets of Massive, made once by the user and kept in the User Library:

- **"Massive Init"**: the user's plain init. For static sounds — bass, lead, sub, keys with a
  fixed tone.
- **"Massive Init (LB routed)"**: the same init plus the routings below, every source at
  rest (level or amplitude 0) so it sounds exactly like the plain init until a source is
  turned up. For anything that moves.

| Source | Routed to | Turn it up with | For |
|---|---|---|---|
| Envelope 1 | Filter 1 and filter 2 cutoff | envelope 1 levels (`ATT.LEV`, `DEC.LEV`, `SUST.LEV`) | filter envelope: plucks, zaps, stabs |
| Envelope 2 | Oscillator 1-3 position | envelope 2 levels | wavetable sweeps per note |
| Envelope 3 | Oscillator 1-3 pitch | envelope 3 levels | pitch drops (kick-like bass, laser zap) |
| Slot 5 (LFO) | Filter 1 cutoff | `MOD5-AMP` | wobble, filter movement |
| Slot 6 (LFO) | Oscillator 1-3 position | `MOD6-AMP` | evolving pads and basses |
| Slot 7 (LFO) | Filter path pan | `MOD7-AMP` | auto-pan |

Amounts in the template (the most each routing can do, measured from the knob's own position):
envelope 1 and 2 about half the knob's travel, envelope 3 about +12 semitones (pitch spans
±64), slot 5 about a quarter, slot 6 about 30 %, slot 7 full. Envelope levels and LFO depth
only scale them down. The LFOs: curve A sine, curve B saw down, `MODn-CROSSF./GLIDE` blends
between them (`0` = sine, `100` = saw: measured); their internal envelope (attack 0, decay inf)
is fixed by the template. The template also has the oscillators' "restart via gate" on (every
note starts in phase: without it three identical oscillators summed differently per note,
±6 dB) and oscillators 2 and 3 at amp 0 — a recipe that uses them turns up `OSC2-AMP` /
`OSC3-AMP`. Verified on the user's Massive: envelope 1 and slot 5 move the cutoff, slot 6 the
position, slot 7 the pan, envelope 3 the pitch, and all rest at 0. Each
Massive knob has two modulation slots plus an SC slot, so a knob takes at most two routings:
the template uses both on the cutoffs and positions, and no macros (cutoff and position are
exposed and can be automated directly).

**Massive's own presets** (`.nmsv`, in `%CommonProgramFiles(x86)%\Native Instruments\Massive\Sounds`
and `Documents\Native Instruments\Massive\Sounds`; on macOS under Application Support and
Documents) are listed by `live_plugin_preset_files(plugin="Massive")` as not embeddable: the user
loads them in Massive's browser. A `.vstpreset` (what Live saves when the Massive device is
dragged into the User Library) loads directly.

Load one with `live_plugin_expose(plugin="Massive", preset_file=<the preset's path>,
parameters=["sound_design"], new_track=true, track_name="Bass")`; find the path with
`live_plugin_preset_files(plugin="Massive")`.

**Saving a sound.** `live_sound_save` keeps the exposed controls' values only, not the routings,
so a saved Massive sound means "this template plus these values": say which template in the
description (`description="from Massive Init (LB routed); Reese, macro 1 = brightness"`), and
load it onto an exposure of that same template with `live_sound_load`. For a complete Massive
preset, the user saves the device from Live (drag it into the User Library).

## How Massive is laid out

- **Oscillators 1-3**: wavetable oscillators. `POSITION` scans the wavetable; `MODE` picks how
  it is read (Spectrum, Bend, Formant and others) and `PARAM2` is that mode's intensity. `PITCH`
  is in semitones, -64 to +64 (a detune is a fraction of one). `FLT.ROUTING` sends each to filter 1, filter
  2 or a blend. `POSITION` changes some tables a lot and others barely (moving it on Squ-Sw I
  changed the bands by under 1 dB): for movement, pick a table whose position you can hear.
  The 87 wavetables, in order: VA-PWM, VA-Sync, Squ-Sw I, Squ-Sw II, SmoSqu, Sin-Squ, Sin-Tri, Plysaw I, Plysaw II, Math I, Math II, Math III, Esca I, Esca II, M-plex, SinForm, AddOct, Additiv I, Additiv II, Additiv III, Additiv IV, Additiv V, Additiv VI, Inharm, Sinarm I, Sinarm II, Woody, Sync, Acid, Grn I, Grn II, Grn III, Grn IV, Crude, Drive I, Drive II, Drive III, Electr, Scrim, Needl, Throat, PWM, Stront, Crush, Reduc, Kanga, Frozen, Vulgar, Classic, Disto, Carbon, Speech, M-Talk, D-Throat, Melom, Melof, Grain I, Grain II, F-Saw, F-Squ, Sonic, Skelchy, D-org, Flend I, Flend II, Flend III, Digi I, Digi II, Digi III, Chrom, Magyr, A.I., Silver, S-Yard, Wicked, Bronz, Arctic, Herby, Bender, G-Pulse, B-Pulse, Chord, Color, Iron, Cica, Melan, Luna.
- **Noise** (type, colour, level) and **Feedback** (the output fed back into the filters, for
  grit) have the same routing control.
- **Modulation oscillator**: a fourth oscillator that only modulates — ring modulation, phase,
  position or filter FM, each aimed at an oscillator (`MOD.OSC-RM-OSC` and the like).
- **Filters 1 and 2**: `SERIELL-PARALLEL` sets serial or parallel; `FILTER-1/2-CROSSFADE` mixes
  their outputs. What `CUT/PRM.1`, `BW/PRM.2` and `RES/PRM.3` do depends on the filter type —
  read the display after choosing the type. Types (Massive 1.7): None, Lowpass 4, Lowpass 2,
  Highpass 4, Highpass 2, Allpass, Double Notch, Bandpass, Bandreject, Scream, Daft, Comb,
  Acid.
- **Inserts 1 and 2** sit in the voice path (`INSERTn-POSITION`); **master effects 1 and 2**
  after it. `DW/PRM.1` is the dry/wet or the first control, depending on the type. Master
  effect types (Massive 1.7): None, Reverb, Small Reverb, Flanger Positive, Flanger Pos Mono,
  Flanger Negative, Flanger Negative Mono, Chorus, Chorus Mono, Chorus Ensemble, Phaser, Phaser
  Mono, Delay, Delay Synced, Dimension Expander, Classic Tube, Tele Tube, Brauner Tube. The
  inserts have their own list — read it with `live_device_get_parameter`.
- **Envelopes 1-4**: Delay (`DELAY`); Attack — time (`ATT.TME`) and the **peak level it rises
  to** (`ATT.LEV`); Decay — time (`DEC.TME`) and the level it falls to (`DEC.LEV`); a sustain
  stage — its time ("S Loop", `SUST.TME`), Morph (`LOOP.MORPH`) and level (`SUST.LEV`); Release
  (`REL. TME`). The Vel and KTR sliders are `VELOCITY` and `KEYTRACKING`. Not exposed: the two
  Morph menus, the Loop switch, and the Linear / Gate / One Shot / Hold / Trg Zero Reset
  switches. **All three levels at 0 = the envelope does nothing** (verified on the user's
  Massive): that is how a routed envelope rests, and its levels are its depth.
- **Slots 5-8**: each is an LFO, performer or stepper, depending on the patch. `RATE`, or with
  `SYNC` on, the fraction `NUM`/`DEN`; `AMP` is its depth; `RESTART` retriggers per note;
  `CROSSF./GLIDE` blends LFO shapes or glides stepper steps. For musical rates use `SYNC` on
  with `NUM`/`DEN` (`"2"`/`"1"` = one cycle per 2 bars; slots 5 and 6 have them in
  `sound_design`); the free `RATE` is steep — `"50"` cycles faster than once a second, `"15"`
  is very slow. With `RESTART` on, a slow LFO restarts each note and never completes a cycle
  on short notes.
- **Voicing**: `VOICEMODE` (poly/mono), `TRIGGERMODE`, `GLIDE-TIME`, `UNISONO` (voices) with
  spread switches and amounts for pitch, position and pan, `VIBRATO-RATE` / `-DEPTH`.

## Recipes

Starting values, then listen (`live_engine_listen`) and adjust. Read the displays first: the
numbers below are what to aim for, not raw values.

- **Sub**: plain init. Oscillator 1 on a sine-like wavetable, 2 and 3 off, no noise, filter 1
  low-pass well open or off, mono, no unison, envelope 4 attack 0 / release short. Play it an
  octave below the bass line.
- **Reese**: plain init. Oscillators 1 and 2 on the same saw-like wavetable, oscillator 2
  `PITCH` +0.1 to +0.2 semitones, mono, unison 2-4 with a small pitch spread, filter 1 low-pass
  (Lowpass 4) around 40-50 %, Classic Tube or Tele Tube as a master effect for grit,
  envelope 4 sustain full, `OSC2-AMP` up to match oscillator 1. Movement:
  the routed template with slot 5 slow (1-2 bars) and `MOD5-AMP` low.
- **Wobble bass**: routed template. Filter 1 Lowpass 4 (or Daft, Scream, Acid) fairly closed,
  slot 5 synced (1/4, 1/8 or 1/8 triplet), `MOD5-AMP` high, `MOD5-RESTART` on so every note
  starts in phase. Automate `MOD5-NUM`/`DEN` for rate changes in the drop. The template's
  routing is a quarter of the knob: at `MOD5-AMP` "50" the wobble moved the top bands only
  ~2 dB — use "80"-"100" and a cutoff low enough that the sweep is heard.
- **Pluck**: routed template. Filter 1 low-pass closed, envelope 1 quick attack, decay 150-300
  ms to level 0, no sustain level; envelope 4 decay 200-400 ms, sustain 0. Delay Synced or
  Small Reverb as a master effect.
- **Pad**: routed template. Oscillators 1-2 detuned slightly (`OSC2-AMP` up), unison 4+ with pitch and pan
  spread, envelope 4 attack 300 ms+ and release 1-2 s, slot 6 slow on position with a low
  `MOD6-AMP`, filter 1 half open, Reverb and Dimension Expander (or Chorus Ensemble) as master
  effects.
- **Lead**: mono with glide (`GLIDE-TIME` short), vibrato depth small, filter 1 more open,
  unison 2 for width, Delay Synced as a master effect.
- **Pitch drop (laser zap, kick-like bass)**: routed template. Envelope 3: attack time 0,
  attack level full, decay short (`"10"`-`"30"`; the template's 81 lasts most of a 2-s note),
  decay level 0, sustain level 0 — the note starts up to
  12 semitones high and falls to its pitch. A sustain level above 0 holds the pitch up for the
  whole note (it is an offset, not a drop: measured on the user's Massive).

## When something sounds wrong

- **Silent:** an oscillator off or at level 0, all oscillators routed to a filter that is off,
  `MASTER-VOLUME` down, or envelope 4 at zero sustain on a long note.
- **Checking movement with the Engine:** its timeline has one reading per second, so a
  faster LFO averages out (a cutoff wobble then shows as a level swing, not in the bands). To
  confirm a routing, slow the LFO to one cycle every few seconds, then set the real rate.
- **Nothing moves although a source is up:** that routing is not in the patch — use the routed
  template, or ask the user to drag the routing in Massive.
- **A preset loads the wrong sound, or Massive's Save / Save As are greyed out:** Massive's
  sound database is broken — the user rebuilds it in Massive's Browser (fixed it on the
  user's machine: `.vstpreset` files then loaded the designed sound again). LiveBridge's
  templates load the same way, so fix this before anything else.
- **Harsh or clipping:** resonance plus feedback plus a clipper insert stack up — lower one,
  then check the level with `live_engine_listen`.
