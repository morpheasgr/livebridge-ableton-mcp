# Omnisphere control reference for agents

Companion to `SKILL.md` and `SERUM.md` for Spectrasonics Omnisphere 2/3, adapted from
ableton-mcp-pro (MIT, Copyright (c) 2026 nicholasbien; added by kasperikaivola, commit 6ac32c8 —
`THIRD_PARTY_NOTICES.md`).

**Control in LiveBridge**: there is no parameter map for Omnisphere, so Live reaches only the
parameters the user adds with Live's **Configure** (the plug-in device's title bar) or maps with
Omnisphere's MIDI Learn. Read them with `live_device_parameters(track=..., device="Omnisphere")`
and set them with `live_device_set_parameters`; everything else is a manual instruction (the
`Omnisphere — manual settings` list below). Load it with `live_browser_load(query="Omnisphere",
category="plugin")`.

Original version-aware design reference.
This document describes controls to consider; it does not observe an instance,
confirm factory assets, or establish writable Live mappings.

## Identity

Baseline: Omnisphere 2, with version-gated notes for 2.5+ and 3. Establish the
actual installed version from the user or UI before relying on version-specific
controls. Reading this guide is not detecting an installation. Record track/device, role, Part, Layer(s), MIDI
channel, output and starting patch. Do not infer Part/Layer from a generic host
parameter label or the Ableton track number.

Research checked October 3, 2026. Spectrasonics' modern Omnisphere 2/3 Reference
Guide redirects to a documentation host that blocked access during this research.
The accessible official legacy guide documents the underlying controls, but its
two-layer counts and FX/modulation restrictions are version-specific. Current
product specifications confirm Omnisphere 3's four-layer architecture and new
feature families. Exact modern labels/ranges not established here are
**unknown/unverified**; use the plug-in's Help/Reference Guide or a user screenshot
to resolve them. This is a design reference, not a claim of exhaustive verified
documentation of every current build or effect algorithm.

Hierarchy: a Multi contains up to eight Parts; each Part holds a Patch. Each
Patch has layers with oscillator/source, filters, amplitude and modulation
controls. Omnisphere 2.5+ offers A/B/C/D; older builds can have only A/B. Use only
layers visible in the user's version. A Layer is not a Part or a Live track.

Always read unfiltered `live_device_parameters` before edits and include
`Omnisphere — manual settings` in final chat. Group every unapplied setting by
Part/Layer/rack with exact desired value and `not applied — set manually` status.
Observed host names, MIDI Learn assignments and Live Configure are distinct.

## Browsing

| Browser/control | Agent use |
|---|---|
| Patch Browser | Complete sounds, including sources/modulation/FX; record exact name, library and version |
| Soundsource Browser | Source for one Sample-mode layer; does not reproduce a complete patch |
| Multi Browser | Part combinations and routing; don't load over an existing multitimbral setup |
| Category/type/keyword filters | Find a role/timbre; use actual returned or user-confirmed names |
| Audition/rating/project features | Help the user select a source; not evidence the agent heard it |
| Sound Match / Sound Lock where available | Explore related patches or preserve chosen aspects; verify which sections are locked |
| User Audio import | Confirm WAV/AIFF path, root and pitch behavior; import is not a full multisample mapper |

Omnisphere's internal browsers are not the same as Live's browser. A loadable
Live rack/plug-in URI may load the instrument without selecting its internal
patch or Soundsource. Propose a search such as “bowed glass / breathy choir /
metallic texture” only as a search direction. Never invent a factory patch title
or treat a category as an exact source selection. If exact content is essential,
request the selected name/screenshot; give a synth-waveform fallback while waiting.

Use initialization only on a requested new patch and specify whether Patch or
Multi is initialized. Layer links/copy/paste can affect several sections, while
level/pan/tuning may have separate behavior; check the exact changed scope.

## Oscillators

Choose **Sample** or **Synth** per layer before prescribing modifiers.

| UI area / control family | Design decision |
|---|---|
| Layer enable, level, pan | Active layers, balance, stereo position; inactive layers off on a new recipe |
| Transpose/coarse/fine and tracking | Register/interval/cents; source root and whether pitch follows notes |
| Sample Soundsource | Exact source/library, start offset, sample/timbre tracking and source-dependent articulations/mix |
| Synth waveform/wavetable | Exact waveform/table, Shape and Symmetry; do not equate Shape to named-source selection |
| Hard Sync | Enable/depth and pitch behavior of sync; describe harmonic movement separately from transpose |
| Analog / phase | Drift/randomness, start consistency; don't prescribe unavailable modern Drift as a legacy knob |

Sample mode can use acoustic, vocal, environmental or synthesized Soundsources.
Its Start/Timbre and source zoom controls are source-dependent. Specify sample
start in the actual UI unit; “20%” needs a defined duration/slider interpretation.
Waveform Shape/Symmetry in Synth mode change its timbre; scan range and effect
depend on the selected waveform. Do not borrow Serum's table/frame indices.
Source modifiers affect synthesis; a post-layer FX rack affects the combined
layer audio. These scopes are different.

### FM and Ring

Each layer has its own internal modulator, so another audible layer is not
required for FM. Account for enable, modulator waveform, frequency/ratio,
keyboard tracking and depth. Low depth on a simple source is a useful proposed
glassy accent; non-tracking frequency can produce inharmonic clang. Legacy
frequency values are UI scalars, not universally physical ratios or Hz.
[Official FM reference](https://support.spectrasonics.net/manual/Omnisphere/edit_page/oscillator/page15.html).

Ring modulation likewise needs its waveform/frequency/tracking/depth and enable
state. Describe sum/difference-tone intent; do not confuse it with FM or amplitude
LFO tremolo. If modern labels cannot be observed, specify conceptual endpoints
and mark their UI mapping unverified rather than assigning guessed indices.

### Wave Shaper

WS/zoom includes crusher, shaper and reducer behavior. Specify enable, algorithm,
depth, bias/symmetry where available, bit/sample-rate reduction and mix/gain.
Routing modes place shaping at different points around filtering; include the
exact selected placement. Digital grit before a low-pass can soften its edge;
after filtering can restore brightness. Reduced bit depth and reduced sample
rate are separate controls. Legacy control facts must be checked against the
visible WS page before a modern-build manual recipe uses their numeric ranges.

## Multipliers

The voice-multiplier area provides Unison, Harmonia and Granular. Select the
available mode deliberately; do not assume all modes can be combined in a
particular build. Voice multiplication increases CPU and can blur attacks.

| Mode | Account for | Useful proposed role |
|---|---|---|
| Unison | Enable/mode, voice count, detune, spread, depth/mix and phase behavior | Width/beating without changing notes |
| Harmonia | Each of up to four added voices: enable, pitch interval, fine tune, level, pan; waveform if available | Octave/fifth shimmer or subtle thickening |
| Granular | Sample-mode source, Speed/Position, grain controls, pitch controls, enable/depth | Evolving organic atmosphere or fragmented FX |

Harmonia is additional oscillator voices inside a layer, not additional MIDI
notes or Parts. Confirm Synth-mode waveform controls versus Sample-mode behavior.
Use consonant intervals only when harmony supports them; an added third fixes
chord quality and may clash with the song. [Official Harmonia zoom](https://support.spectrasonics.net/manual/Omnisphere/edit_page/oscillator/page25a.html).

### Granular details

Granular operates on Sample-mode content. The Speed/Position choice determines
whether playback moves through the source or addresses a location. Legacy Speed
has a centered freeze point and reverse behavior beyond it; don't import a
conventional 0–1 playback-speed assumption. Verify the current UI definition.

- Grain controls: depth, intensity/grain character, smoothing and spread. Give
  their actual UI units, not an invented grain length in ms if no such display
  exists. Smoothing and amplitude release are separate decisions.
- Pitch controls: detuning, pitch grains, interval and gliding; define pitch
  variation and any scale-related intent. Broad scatter can destroy tonality.
- Position/speed: define region, motion direction and rate, freeze point and
  retrigger behavior. Keep sample source and pitch tracking explicit.
- Level/pan/filter/amp: retain a complete per-layer signal path. Short/gritty
  grains do not automatically become quiet enough for an underlayer.

[Grain controls](https://support.spectrasonics.net/manual/Omnisphere/edit_page/oscillator/page29b.html)
and [pitch controls](https://support.spectrasonics.net/manual/Omnisphere/edit_page/oscillator/page29c.html)
document the legacy names. Modern ranges remain subject to confirmation.

## Filters

Layer filter controls and Filter Zoom cover two filters with series/parallel
structure. Specify each filter's power, exact type/slope, cutoff, resonance,
gain/drive where present, key tracking, envelope depth, stereo spread/pan and
mix/routing. A front-page cutoff slider does not establish both zoom filters'
settings. Main-page/master tone filtering is a different scope.

Low-pass, high-pass, band-pass/notch, vowel/formant, comb/resonator and specialty
models suit different tasks. Select the displayed name, not an enum guess. The
same cutoff has different results on a 2-pole and a 4-pole model. Envelope depth
without its envelope timing is incomplete. Track resonance and gain carefully
when changing models. [Official filter reference](https://support.spectrasonics.net/manual/Omnisphere/edit_page/filters/index.html).

For a quiet atmospheric addition, propose a low-pass around 1800–3500 Hz plus
gentle tracking and slow motion, and state the chosen exact cutoff after context
inspection. Use post-FX low cuts only if the design needs them; do not invent
missing measurements to justify “fixing mud”.

## Modulation

| Area | Include in a complete instruction |
|---|---|
| Layer AMP envelope | Attack/decay/sustain/release, segment curves, velocity response and retrigger |
| Layer FILTER envelope | Same timing plus depth/polarity, velocity contribution and cutoff base |
| MOD envelopes | Selected source/slot, contour, loop/sustain/release and destination; scope mono/poly matters |
| LFOs | Slot, waveform, rate Hz or sync division, polarity, phase/retrigger, delay/fade, smoothing if available |
| Modulation Matrix / Flex-Mod | Source, target with Part/Layer/FX identity, amount/polarity, curve/scaling/secondary source and enabled state |
| Wheel/velocity/key/pressure/random/expression | Exact range and input prerequisite; never assume existing routes |

Envelopes offer simple ADSR and complex multi-point editing. For a complex curve,
give every time/value point, segment curve and loop/sustain/release behavior.
Do not estimate unavailable envelope durations from their host normalized values.
Legacy documents describe six LFOs and eight envelopes in two-layer Parts;
modern version counts differ. Use the observed slot inventory; do not silently
carry these legacy counts into Omnisphere 2.5+/3.
[Envelope reference](https://support.spectrasonics.net/manual/Omnisphere/edit_page/envelopes/index.html),
[LFO reference](https://support.spectrasonics.net/manual/Omnisphere/edit_page/lfos/index.html).

Direct right-click assignment and matrix editing can require GUI work. An exposed
destination value does not expose its modulation routes. Give base and endpoints
where the matrix depth-to-physical-unit conversion is unknown. Retain existing
routes for improvement requests and describe how the new one interacts with them.

### Orb

Orb radius/angle, depth, inertia, dice-generated scenes, recording and trigger
mode can create broad patch-dependent motion. It is not a fixed “brightness”
control. Enabling it can instantiate processing/FX; preserve a baseline before
experimentation. For a reproducible design prefer explicit matrix routes; if using
Orb, give the required scene/state and path/recording, not just cursor position.
A manual recorded path is not implemented until the user records it.
[Official Orb reference](https://support.spectrasonics.net/manual/Omnisphere/main/orb/index.html).

## Effects

Identify the actual Part and selected Layer rack, Common rack, Aux rack or Multi
Master rack. Trace insert order top to bottom and send/return routing. Legacy
Layer FX are pre-fader, Common FX post-fader, and Multi Master belongs to OUT A;
verify modern routing rather than assuming all outputs pass through Master.
Specify slot/type/enable, every deliberately set control, output gain and mix.
An Aux effect generally needs wet-only processing and a separate send amount;
Common insert FX usually need a wet/dry blend. [Official FX architecture](https://support.spectrasonics.net/manual/Omnisphere/fx_page/page00.html).

The legacy guide allows modulation of Layer FX but excludes Common FX targets.
Omnisphere 3's specification describes expanded FX modulation. Determine the
installed version/target list before promising a Common/Aux/Master modulation
route. Never treat a configured opaque FX parameter as a known effect.

| Family / examples (confirm availability) | Controls the design must identify |
|---|---|
| Compressor/limiter/gate; Precision, Vintage, Modern, Stomp-Comp, Tube Limiter, Tape Slammer | Threshold/ratio or model-specific amount, attack/release, knee/detector if available, gain, mix; matched output |
| EQ; Parametric, Graphic, Vintage/Studio bands | Each active band type/frequency/Q/gain; output gain and pre/post placement |
| Filter/color; Formant Filter, Power Filter, Valve Radio, Wah | Type/mode, cutoff/formants/resonance/drive, envelope/LFO settings and wet mix |
| Amp/distortion; Flame, Smoke, amp models, waveshapers | Model, drive/bias/tone/EQ, cabinet/placement if offered, gain and wet mix |
| Chorus/flanger/phaser; Ultra Chorus, Retro/Pro/EZ-Phaser | Rate/sync, depth, feedback, delay/stages, stereo phase/spread, filtering, gain/mix |
| Delay; BPM Delay/X2/X3, Chorus Echo, Radio Delay, Retroplex | Per-tap time/division, levels/pan, feedback, filtering, drive/placement, gain/mix |
| Reverb; PRO-Verb, EZ-Verb, Spring | Time versus size, pre-delay, diffusion/damping/width, tone, modulation, freeze, gain/mix |
| Texture; Innerspace, Quad Resonators (version-dependent) | Actual source/algorithm, resonator tuning/filtering, motion, mix/level; no inferred hidden IR names |
| Stereo; Imager and model-dependent utility | Width/balance/phase, crossover if present, gain; check mono compatibility by audition |

These are parameter checklists, not identical knob layouts for every algorithm.
Use the exact effect's current UI before prescribing labels/units. If not known,
choose a confirmed simpler effect or request a screenshot, rather than filling
in imaginary controls. The official Reference Guide is the exhaustive catalogue.

### Two practical legacy effects

PRO-Verb has wet mix; Size, Time (ms), Predelay (ms), CPU load; page-two controls
include tone/spread/diffusion/freeze. Choose its actual initialized preset and
then list deliberate changes. Freeze captures a temporary buffer, not a permanent
source saved by ordinary patch storage; preserving the sound may require audio.
BPM Delay is tempo-locked; specify note division, feedback, tone filtering and
overdrive placement/feedback-loop state as well as wet mix. X2 and X3 introduce
multiple taps with separate levels/times: do not prescribe a nonexistent global
“ping-pong” selector by borrowing Serum labels.
[Official legacy FX control catalogue](https://support.spectrasonics.net/manual/Omnisphere/fx_page/all.htm).

## Performance

MAIN/controls: voices/polyphony, solo/legato/glide and bend ranges, layer blend,
velocity curve, tuning and output level. Check the actual solo/glide options;
polyphonic chord recipes should not silently enable mono glide. Keep concert
pitch and temperament unless a design specifically needs another tuning.

ARP: power, preset/pattern, mode/order, trigger/clock/division, octaves/range,
reset, length, swing, velocity and per-step note/gate/other modifiers. Groove Lock
needs its actual MIDI groove source. Later versions add expressive modifiers;
only use observed controls. Disable ARP for an externally sequenced pad recipe.
Latch can continue notes after release; record its intended state.

MULTI/Mixer: Part MIDI channels, mute/solo, level/pan, output assignment and Aux
sends. STACK concerns Part splits/crossfades and performance control; LIVE concerns
Part selection/layering. Neither is automatically a per-patch Layer crossfade.
A new simple track normally needs one Part on its input channel and audible output;
preserve other Parts of an existing instance. Hardware Integration/MIDI Learn
provides manual controller mappings, not automatic Configure access through MCP.

## Version-gated

Omnisphere 3's official overview confirms new adaptive Global Controls (Tone,
Ambience, Filter, Envelope, Vibrato, Unison), Patch Mutations, Quadzone, oscillator
Drift, dual frequency shifting, new filters/FX and expanded modulation. These are
**optional version-3 ideas**, not required settings for a version-2 patch.
[Official current specifications](https://www.spectrasonics.net/products/omnisphere/overview.php).

For v3, identify the actual UI control and mapping first. Adaptive controls depend
on the patch, so a given percentage is not a fixed cutoff/decay. Quadzone designs
need layer assignment, boundaries/crossfades and source/range. Frequency-shifter
designs need both shifts, tracking, series/parallel routing and modulation.
Mutation requires a saved baseline and the selected result; random generation is
not a deterministic recipe. Exact ranges/labels not observed stay unverified.

## Recipes

Original proposals, not factory patches or current settings. Use the installed
version and available controls. Every target stays manual until actually applied
and read back. All unmentioned controls retain the stated initialized baseline.

### Glass Sanctuary — new mystical texture, no sample-library dependency

Use a new single-Part initialized Patch, Part 1 / input channel 1 / OUT A,
Part level -12 dB, center; other Parts inactive on this **new** instance. Stack,
Live layering, Orb, ARP/latch off. Layers A/B on, C/D off where available.
Clear unused modulation and FX on this new Init-based patch before the following
steps. Use current UI equivalents only after labels/units are confirmed.

| Area | Target |
|---|---|
| Layer A source | SYNTH sine waveform (proposed selection — verify exact displayed name), transpose 0 semitones, fine 0 cents, tracking on, phase 0°, Analog 0%; level -6 dB, pan center; Shape/Symmetry retain initialized sine state |
| Layer A modifiers | FM on, modulator sine, keyboard tracking on; Frequency UI scalar 0.500 and Depth 0.120 only if legacy-equivalent labels/scales are confirmed; otherwise mapping unknown/unverified — set manually after confirmation. Ring, WS, Sync, multiplier off |
| Layer A filter | On, proposed `LPF Warm 12db` (confirm actual label), cutoff 2600 Hz, resonance 10% of UI span, gain 0 dB, spread 0%, key tracking 50%; second filter off; envelope depth 0 |
| Layer A AMP | Attack 900 ms, decay 2200 ms, sustain 70% amplitude, release 4200 ms, simple ADSR/Init curves, velocity response 20% UI span; polyphonic retrigger |
| Layer B source | SYNTH triangle (confirm exact waveform name), transpose +12 semitones, fine +3 cents, tracking on, phase 0°, Analog 0%; level -18 dB, pan center; other source shape controls retain initialized triangle state |
| Layer B modifiers/filter | FM/Ring/WS/Sync/multiplier off; same confirmed LPF, cutoff 1800 Hz, resonance 5% UI span, gain 0 dB, spread 0%, tracking 50%, envelope depth 0; second filter off |
| Layer B AMP | Attack 1600 ms, decay 2400 ms, sustain 65% amplitude, release 5000 ms; Init curves, velocity response 20% UI span; polyphonic retrigger |
| LFO 1 / matrix | Sine, BPM off, 0.08 Hz, bipolar, free-running, phase 0°, delay 0 ms, fade-in 0 ms; A filter cutoff base 2600 Hz -> endpoints 2200/3100 Hz; B cutoff base 1800 Hz -> endpoints 1500/2100 Hz; matrix depth calibrated manually to endpoints |
| Common rack slot 1 | Initialized PRO-Verb on; wet mix 32%, Size 75 UI value, Time 5500 ms, Predelay 35 ms, Freeze off; all remaining PRO-Verb controls retain its confirmed initialized baseline |
| Other FX/routing | A/B layer racks empty; other Common slots empty; Aux sends zero, Multi Master FX empty; ensure no second wet/dry path |
| Performance | Polyphony 12, Solo/Legato off, glide 0 ms, bend up/down 2 semitones, tune A=440 Hz; velocity as above, wheel initially 0 |
| Wheel / matrix | Unipolar wheel -> A FM depth 0.120 to 0.220 (only on confirmed scale); wheel -> B level -18 to -12 dB; no other new routes |

Do not call the FM scalar a 2:1 ratio or convert it directly to a Live normalized
value. If a required label/range cannot be confirmed, that row is blocked by
mapping uncertainty, with the exact desired design retained in the handoff.
Save as `Glass Sanctuary — <song role>`. Audition sparse upper chord tones for
2–4 bars; avoid harmonic guesses if the key is unknown. Match level before judging
fullness. This recipe remains partial while source/mapping/manual steps remain.

### Organic variation with a confirmed Soundsource

On a separate new variation, replace Layer B with a **user-confirmed** bowed-glass,
choir or breathy environmental Soundsource; record its exact name/library.
Keep its octave, level, AMP envelope and filter as above. Enable Granular in
Position mode at 40% of sample duration, smoothing 70% UI span, spread 35%,
grain depth 50%, pitch detuning 0; intensity and other controls retain the chosen
confirmed initialized Granular preset (record its name). If that preset/range
cannot be confirmed, give this as a conditional idea pending confirmation, not a
complete patch. No random factory source names or invented ms grain lengths.

### Existing bland patch — choose one change, keep the identity

- **No evolving texture:** if a Synth waveform's Shape is available, sine LFO at
  0.06 Hz, BPM off, free-running, bipolar -> Shape ±8% of UI span about observed
  base; clamp to legal endpoints. If no morphing waveform is confirmed, target
  a verified filter cutoff instead, base frequency ±2 semitones.
- **Too uniform:** propose wheel -> an observed layer level from its current
  value to +3 dB, and cutoff to +1 octave; leave sources and existing FX intact.
  Give Part/Layer and routes; do not assume the wheel is unused.
- **Weak attack:** retain AMP; use that Layer's FILTER envelope with attack 5 ms,
  decay 300 ms, sustain 0%, release 180 ms, unipolar cutoff movement from current
  base to +1 octave and back. Preserve other envelope/matrix routes; proposed
  change depends on the current filter/envelope being confirmed.
- **Bland sustain:** if another Layer is off/available, propose a quiet contrasting
  source at -18 dB relative to the main layer, same register, attack 1500 ms,
  release 3000 ms; name the exact chosen source and filter route after confirmation.
- **Space without identity loss:** on a confirmed PRO-Verb insert, propose wet mix
  +5 percentage points and Predelay 25 ms; retain other settings. If space already
  comes from Aux/external Live effects, inspect those first to avoid duplication.

These are hypotheses, not claims about unheard audio. A suggestion-only request
does not authorize any writes. Provide before/target values where observed and
an ordered manual table for the chosen proposal.

## Saving

Save a new Patch for one Part's sound, a Multi for Part combinations and routing,
and a Live rack/default configuration if configured host parameters must persist.
Name and retain every imported user-audio dependency. Patch saving is not proof
an Orb motion, frozen reverb buffer or external automation was saved; verify the
relevant persistence behavior. Do not overwrite existing presets or a Multi for
a single-Layer suggestion. Save/import/MIDI Learn/Configure operations remain
manual unless the available tools actually verify them.

## Sources

- [Official Omnisphere 2 Reference Guide](https://support.spectrasonics.net/manual/Omnisphere2/25/en/)
  and [Omnisphere 3 guide](https://support.spectrasonics.net/manual/Omnisphere3/):
  documentation-host access blocked during this research; use in-plug-in Help for
  current-build exact names/ranges. These links are verification routes, not read evidence.
- [Official current product specifications](https://www.spectrasonics.net/products/omnisphere/overview.php):
  establishes version-3 feature families; recipes do not assume their availability.
- [Legacy oscillator guide](https://support.spectrasonics.net/manual/Omnisphere/edit_page/oscillator/index.html),
  [FM](https://support.spectrasonics.net/manual/Omnisphere/edit_page/oscillator/page15.html),
  [Harmonia](https://support.spectrasonics.net/manual/Omnisphere/edit_page/oscillator/page25a.html),
  [granular grain](https://support.spectrasonics.net/manual/Omnisphere/edit_page/oscillator/page29b.html)
  and [pitch](https://support.spectrasonics.net/manual/Omnisphere/edit_page/oscillator/page29c.html).
- [Legacy filters](https://support.spectrasonics.net/manual/Omnisphere/edit_page/filters/index.html),
  [envelopes](https://support.spectrasonics.net/manual/Omnisphere/edit_page/envelopes/index.html),
  [LFOs](https://support.spectrasonics.net/manual/Omnisphere/edit_page/lfos/index.html),
  [FX routing](https://support.spectrasonics.net/manual/Omnisphere/fx_page/page00.html),
  [FX controls](https://support.spectrasonics.net/manual/Omnisphere/fx_page/all.htm),
  [Orb](https://support.spectrasonics.net/manual/Omnisphere/main/orb/index.html).

Vendor references establish the cited control families in their documented
versions. Workflow, recipes and improvement hypotheses are original proposals;
no vendor manual text, presets or samples are bundled.
