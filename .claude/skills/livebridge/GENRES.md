# Genre recipes: drums, bass, chords, sounds

Companion to `PRODUCTION.md` (song structure, build-ups, effects, mixing) and `SERUM.md` (Serum 2
patches). This file is the concrete material per genre: drum patterns ready for
`live_clip_write_pattern`, bass and chord patterns, sound-design starting points with Live's own
instruments, and the processing that defines each style. Read the section for the genre the
user asked for before writing parts. Adapted from the ableton-mcp-pro production skills
(MIT, see `THIRD_PARTY_NOTICES.md`).

## 0. How to use the recipes

- **Patterns** are 16 steps per bar (`step="1/16"`, the default). In the strings `X` = accent
  (127), `x` = hit (`velocity`, default 100), `o` = ghost (`ghost_velocity`, default 50),
  `1`-`9` = a velocity level, `.` = rest; `|` separates beats. Write them with
  `live_clip_write_pattern(track="Drums", slot=0, pattern={...}, repeat=4)`; drum names map to
  the loaded kit's pads (check `result.kit` and `warnings`). Rows that are not drum names
  ("perc", a second tom) take a note name: look the pad up with `live_drumrack_overview`.
- **Swing** in these recipes is given as MPC percent and as LiveBridge's `swing` (fraction of a
  step): 54 % = 0.08, 56 % = 0.12, 58 % = 0.16, 62 % = 0.24, 66 % (triplet shuffle) = 0.33.
  Swing hats and percussion, not kick and clap: write them in separate calls, or swing the
  whole clip and re-quantize the kick (`live_clip_transform_notes(pitch="C1", quantize="1/16")`).
- **Bass and melodic lines** are note lists for `live_clip_add_notes(notes=[[pitch, start,
  length, velocity], ...])`, start and length in beats. A slide (glide) = the note overlaps the
  next one; a staccato jump = a gap.
- **Device parameter names** differ between Live versions: read them first
  (`live_device_parameters(track=..., device="Operator", filter="Osc")`) and set by display text.
  Built-in instruments from presets usually arrive as racks with macros: use `live_rack_macros`
  first. For Serum 2 versions of the bass sounds, use `SERUM.md`.
- **What the API cannot do** (from `SKILL.md` §7): map a knob to a macro, delete rack chains,
  group tracks, save presets. Where a recipe needs one of these, tell the user the one manual
  step, or use the alternative given.

## 1. Techno (minimal, dark, warehouse) — 128-138 BPM, minor, F/A/C minor

**Drums.** The kick is a metronome: every beat, velocity 110-127, never varied. Clap dry on 2
and 4 (95-110). Closed hats on every 16th, shaped strong-weak-medium-weak (about 100-45-75-35).
The ride on the 8th off-beats is the signature: slightly overdriven (Saturator), almost as loud
as the hats. Percussion off-beat and syncopated, never on the kick.
```python
# minimal (intros, breakdowns)
live_clip_write_pattern(track="Drums", slot=0, repeat=4, pattern={
    "kick": "x...|x...|x...|x...", "clap": "....|x...|....|x...",
    "hat":  "x...|x...|x...|x...", "rim":  "....|....|..x.|...."})
# driving (peak time): velocity-shaped hats, ride on off-beats
live_clip_write_pattern(track="Drums", slot=1, repeat=4, pattern={
    "kick": "x...|x...|x...|x...", "clap": "....|x...|....|x...",
    "hat":  "8363|8363|8363|8363", "ride": ".x.x|.x.x|.x.x|.x.x",
    "rim":  "...x|..x.|...x|.x.."})
```
Hypnotic variant: odd-length percussion phasing against the bar. In one clip:
`live_clip_write_euclid(track="Drums", slot=2, bars=5, rhythms={"kick": "4/16", "rim":
"2/5", "tom_high": {"hits": 3, "steps": 12, "rotate": 1, "velocity": 80}})` (rows with their
own cycle lengths drift and come round after 5 bars); or a 5-sixteenth clip on its own track
with `live_clip_set(loop_end=1.25)`. Then `live_clip_note_chance(probability=[0.6, 0.95],
pitch="F#1")` on the hats for a loop that never repeats exactly.

**Bass.** 1-3 notes, locked to the kick; timbre moves, pitch doesn't. Range F0-C2.
- *Sub* (deep/minimal): sine, mono, attack 5 ms, release 50 ms, long notes under the kick.
- *Distorted* (peak time): square carrier with a saw modulator an octave up (Operator FM), LP
  24 dB at ~45 %, filter envelope decay 200 ms, then Saturator (drive ~8 dB, Analog Clip).
- *Acid-influenced*: saw, LP 24 dB, resonance ~60 %, filter envelope decay 150 ms, velocity to
  filter, glide 80 ms, Overdrive after (see §8 Acid).
```python
# off-beat F minor bass (between kicks), 1 bar
live_clip_add_notes(track="Bass", slot=0, notes=[
    ["F1", 0.5, 0.25, 90], ["F1", 1.5, 0.25, 90], ["F1", 2.5, 0.25, 90], ["G#1", 3.5, 0.25, 110]])
```
Chain: EQ Eight (high-pass 30 Hz, -3 dB wide at 250 Hz) -> Compressor side-chained to the kick
(4:1, attack 0.1 ms, release ~120 ms at 135 BPM, 4-6 dB reduction) -> Utility (Width 0 %).

**Sends and bus.** Return A: short dark reverb, 0.4-0.8 s, high cut 3-5 kHz, low cut 200 Hz,
clap and percussion at 25-40 %. Return B: ping-pong delay 1/8 or 3/16, feedback 25-40 %,
high-pass 400-600 Hz, selected percussion only. Drum bus: parallel compression (return with a
compressor at 10:1, fast attack and release, blended 25-40 %) and Drum Buss (Boom 10-20 %,
Crunch 15-30 %, damping 7-9 kHz for the dark warehouse top).

**Movement.** Automate over 8-32 bars: bass filter 30 % -> 55 %, FM amount up in builds,
resonance up in breakdowns, Saturator drive +2-4 dB in the last 4 bars before a drop, note
length 1/8 -> 1/16 for urgency (`live_clip_transform_notes(gap=...)`).

## 2. Hardgroove — 140-145 BPM (142), F/G/A

The groove comes from **pitched toms** interlocking with the kick and from layering programmed
parts with loops. Kick four-on-the-floor. Toms pitched down 3-7 semitones, tuned a fourth or
fifth apart, high-passed, mono, with a conga/bongo layer at 20-30 %. Hats: programmed 16ths
**plus** a hat loop on top; on-beat hats quieter than off-beat hats (drive). Claps on 2 and 4
plus a short clap loop. Kick duplicated on a muted "SC" track with 0.1-beat notes as a clean
side-chain trigger (`live_clip_duplicate` + `live_clip_transform_notes(...)`; route
side-chains from it).
```python
# swung toms (2 bars), off-beat-heavy hats, open hat breathing point
live_clip_write_pattern(track="Drums", slot=0, repeat=2, pattern={
    "kick":    "x...|x...|x...|x...|x...|x...|x...|x...",
    "tom_low": ".x..|...x|..x.|.x..|..x.|...x|.x..|..x.",
    "hat":     "5757|5757|5757|5757|5757|5757|5757|5757",
    "ohh":     "....|....|..x.|....|....|....|..x.|....",
    "clap":    "....|x...|....|x...|....|x...|....|x..."})
```
Straight variant: toms on every "and" (`"..x.|..x.|..x.|..x."`), relentless. Minimal variant:
8th hats and one unexpected tom per bar. Nudge toms and percussion slightly ahead of the grid
for urgency (`live_clip_transform_notes(pitch=..., shift=-0.01)`).

**Bass.** Minimal (2-3 notes), percussive: square (or Operator: square carrier, saw modulator +12
for grit), LP 24 dB ~40 %, envelope decay 150 ms, key tracking off, amp A 0 / D 200 ms / S 70 %
/ R 50 ms, glide 30 ms. Notes shorter than their step (leave gaps). EQ: cut below 30 Hz and the
mids/highs; Saturator after the EQ (Analog Clip, 6-12 dB drive, output pulled down); side-chain
release 100-150 ms at 142 BPM; mono; no reverb or delay. Second loudest after the kick.
```python
# F, Ab, Eb hardgroove line, 2 bars
live_clip_add_notes(track="Bass", slot=0, notes=[
    ["F1", 0, 0.4, 110], ["F1", 1.5, 0.4, 100], ["G#1", 2.5, 0.4, 105],
    ["F1", 4, 0.4, 110], ["F1", 5.5, 0.4, 100], ["D#1", 6.5, 0.4, 105]])
```
Tom bass alternative: tuned 707/909 toms in a Drum Rack as the bass line. Acid crossover:
resonance 50-70 %, glide 40-60 ms with overlapping notes.

## 3. House (classic, deep, peak time) — 120-128 BPM (124)

**Drums.** Four-on-the-floor kick (loudest element). Clap on 2 and 4 with a snare 6-8 dB below
it for body. Open hat on every off-beat: *the* house sound; closed hat chokes it
(`live_drumrack_set_pad(note="F#1", choke_group=1)` and the same group for "A#1"). Closed hats on
16ths, loud on the beat (100-110), soft between (60-75), quietest on the "e" (40-55).
```python
# classic
live_clip_write_pattern(track="Drums", slot=0, repeat=4, pattern={
    "kick": "x...|x...|x...|x...", "clap": "....|x...|....|x...",
    "ohh":  "..x.|..x.|..x.|..x.", "hat":  "x.x.|x.x.|x.x.|x.x."})
# deep (spacious, laid-back clap, sparse percussion)
live_clip_write_pattern(track="Drums", slot=1, repeat=4, pattern={
    "kick": "x...|x...|x...|x...", "clap": "....|x...|....|..x.",
    "ohh":  "..x.|....|..x.|....", "hat":  "x.x.|x.x.|x.x.|x.x.",
    "rim":  "....|...x|....|.x.."})
# peak time: 16th hats, 8th ride, congas
live_clip_write_pattern(track="Drums", slot=2, repeat=4, pattern={
    "kick": "x...|x...|x...|x...", "clap": "....|x...|....|x...",
    "ohh":  "..x.|..x.|..x.|..x.", "hat":  "9565|9565|9565|9565",
    "ride": "x.x.|x.x.|x.x.|x.x.", "tom_high": "...x|....|.x..|...x"})
```
Swing hats and percussion 54-58 % (`swing=0.08`-`0.16`) or a Groove Pool MPC/909 groove at
30-50 %; kick and clap stay on the grid. Two-bar variation: duplicate to 2 bars and move one or
two percussion hits in bar 2. Bass slots between the kicks: at most one bass note on a
downbeat per bar.

**Chords.** Extensions are the sound: m7, maj7, m9 (`live_clip_write_chords("Am7 Dm7 G7
Cmaj7")`, `"Fm9 Bbm7"` two-chord vamp, disco `"Dm7 G7 Cmaj7 Fmaj7"`). Open voicings, voice
leading on. Rhythms:
- *Off-beat stabs* (the signature): a chord on every "and", beat 1 left empty. Write the chord
  then cut it into stabs: `live_clip_write_chords(..., duration=0.25)` per stab via
  `live_clip_add_notes`, or write a pattern row per chord tone. Stab patch: saw + square
  (60/40), amp A 0 / D 150-300 ms / S 0 / R 80 ms, band-pass 0.8-2 kHz, resonance 20-30 %.
- *Deep pads*: whole notes, two detuned saws (Wavetable, 4-6 unison, detune 15-25 %), LP 50-65 %,
  slow filter LFO 0.2-0.4 Hz, amp A 80 ms / R 1.5-2 s.
- *Filtered disco chords*: any chords through Auto Filter (LP 24 dB); automate the cutoff from
  200-400 Hz to 4-8 kHz over 8-16 bars, reset at each section. Minimal house alternative:
  band-pass with resonance 60-80 % and its synced LFO.

FX: Chorus-Ensemble on pads, ping-pong delay 1/8 or 3/16 (feedback 20-35 %, wet 15-25 %) on
stabs, plate 1-2 s with 15-25 ms pre-delay; side-chain chords to the kick (4:1, release
100-150 ms; 8:1+ for the French-house pump). Drum bus: parallel compression 25-40 %, Drum Buss
transients +2-3 dB, high shelf +air at 8-10 kHz, -2 dB at 300-400 Hz; hats high-passed at
400-500 Hz; clap +1-2 dB at 1-2 kHz, low cut below 200 Hz.

## 4. UK garage and speed garage — 130-140 BPM

**Principle.** The groove is pattern placement and sound choice, not processing. Everything is
**short**: short kick (pitched up 2-3 semitones), rimshots tightened with the amp envelope,
shakers/cabasa instead of open hats (soften the attack; high-pass hats at 750 Hz). Three gain
stages: loudest = kick + rim/snare + main hat; middle = background percussion; quietest = fills
and texture. Swing 54-60 % (`swing=0.08`-`0.2`) or a Groove Pool MPC 16th swing at 60-70 %:
without swing it is just house.
```python
# classic 2-step (2 bars)
live_clip_write_pattern(track="Drums", slot=0, swing=0.16, pattern={
    "kick":   "x...|....|..x.|....|....|x...|....|..x.",
    "rim":    "....|x...|....|x...|....|x...|....|x...",
    "shaker": "x.x.|x.x.|x.x.|x.x.|x.x.|x.x.|x.x.|x.x.",
    "C#2":    "...o|..o.|.o..|.o..|.o..|..o.|.o..|...o"})
# tresillo / "fake triplet" 3-3-2 (1 bar)
live_clip_write_pattern(track="Drums", slot=1, swing=0.16, repeat=2, pattern={
    "kick": "x...|....|....|....", "snare": "....|..x.|....|x...",
    "shaker": "x.x.|x.x.|x.x.|x.x.", "C#2": "...o|.o..|.o.o|...o"})
```
(`C#2` stands for a background percussion pad; check the kit.) Skippy variant: extra displaced
kicks and more ghosts. Displaced snare: the rim one 16th *before* beats 2 and 4 for urgency.
Four-on-the-floor UKG: house kick, clap on 2 and 4, 16th ghost hats and rim ghosts, swung.

Processing by **sends**, not inserts: Send 1 = drum-bus compression for kick + snare (low cut
first); Send 2 = Redux (subtle bit-crush) for all percussion and hats: the shared crunch glues it.

**Speed garage (135-145).** Kick, bass, main open hat and snare are the loudest, at the same
level. The **bass is the melody**: a 2-bar walking line through scale degrees (root -> 5 -> 6 ->
6 -> 3 -> root) with octave jumps, notes with small gaps (not legato). FM sound (Operator: sine
carrier two octaves down, modulator at -12 semitones slightly detuned, ~60 % with a fast-decay
envelope; mono, glide 10-15 %, slight amp attack for the "reverse bass" feel; LP with no
resonance ~25 % opened by a fast envelope); light tube saturation; side-chained (4:1, 80-120 ms).
Layers: a quieter Drum Rack of different snares/claps, a rave loop well below the mains,
pitched-up vocal chops (`live_clip_convert(to="drums")` or Simpler slicing) with echo and
high-pass, a crash every 4 bars with reverb, small transition FX before each crash.
```python
# D minor walking bass, 2 bars (D, A, Bb, Bb, F, D with octave jumps)
live_clip_add_notes(track="Bass", slot=0, notes=[
    ["D1", 0, 0.45, 110], ["D2", 0.5, 0.45, 95], ["A1", 1, 0.45, 105], ["A#1", 1.5, 0.45, 100],
    ["A#2", 2, 0.45, 95], ["F1", 3, 0.45, 105], ["D1", 4, 0.45, 110], ["D2", 4.75, 0.45, 95],
    ["A1", 5.5, 0.45, 105], ["F1", 6.5, 0.45, 100], ["D1", 7.5, 0.45, 105]])
```

## 5. Trance — 132-140 BPM

**Bass, three types.** Dial the sound in against the kick, not solo. Saw/square with a short
filter envelope (attack 0-5 ms, decay 80-200 ms, sustain 10-25 %, resonance 15-30 %). Optional
split: a sine sub (LP 120-150 Hz, steady) + a mid layer (HP 100-120 Hz, all the movement,
side-chained harder).
```python
# 1. off-beat (human, danceable; breakdowns)
live_clip_write_pattern(track="Bass", slot=0, repeat=4, pattern={"F1": "..x.|..x.|..x.|..x."})
# 2. filled 16ths (driving; side-chain essential)
live_clip_write_pattern(track="Bass", slot=1, repeat=4, gate=0.8,
                        pattern={"F1": ".xxx|.xxx|.xxx|.xxx"})
# 3. octave-jump 16ths (maximum energy)
live_clip_write_pattern(track="Bass", slot=2, repeat=4,
                        pattern={"F1": "x.x.|x.x.|x.x.|x.x.", "F2": ".x.x|.x.x|.x.x|.x.x"})
```
Note length *is* the groove (shorter = punchier, longer = more sub). Side-chain: attack 0-1 ms,
ratio 4:1-10:1, 6-12 dB reduction, release timed so the bass is back before the next 16th
(~109 ms at 138 BPM). Move between types per section: off-beat in the breakdown, filled or
octave in the drop. Follow the chords: `live_clip_transform_notes(transpose=...)` per bar or
write each bar's root.

**Melody.** Chords first, then a lead using only 3-4 scale notes, rhythm first (one note, find
the rhythm, then change pitches), octave jumps for energy, call and response (bright call,
softer answer). Tension: sus4 -> major, 7ths, ii-V-I or a deceptive vi; in builds avoid the
tonic in the melody (use 3rd or 5th). Build density towards the drop (half notes -> 8ths ->
16ths -> 32nd runs) and climb the scale. A "chord melody" pad that holds chords against the
main progression adds depth and gives the arrangement something to swap in.
`live_clip_write_arp(chords=..., style="up", rate="1/16")` sketches arp ideas; ping-pong delay
1/8 or dotted 1/8.

**Supersaw chords.** Wavetable: saw, 8 unison, detune ~30 % (20 % tight / 40 % wide), second
oscillator +12 semitones quieter, sub on; LP 70-80 %, resonance 0-15 %; stabs A 0-10 ms / D
200-500 ms / S 60-80 % / R 200-500 ms, pads A 50-200 ms / R 0.5-2 s. Layers in an Instrument
Rack (`live_device_insert("Instrument Rack")`, `live_rack_insert_chain`, `live_device_move`):
a tight near-mono layer (20 % detune) + a wide layer (40 %). A Chord MIDI effect with +12 doubles
the octave. Then Multiband Dynamics (OTT-style, 50-70 %), reverb 2-4 s with 20-40 ms pre-delay
and a high cut at 8-10 kHz, EQ low cut at 100-150 Hz. Mono below 200 Hz (EQ Eight M/S, side
high-passed). Spread voicings; trance `"Am F C G"`, emotional `"i VI III VII"`. Serum version:
`SERUM.md` §4.

## 6. Bass music: Reese, growl, dubstep — 140-150 BPM (DnB 170-176)

**Reese** (DnB, neuro, techno), range C0-C1: two saws, B detuned +15-35 cents (+25 is the
classic), **parallel** (no FM), a clean sine sub layer, mono with glide 100-300 ms. Chain: Glue
Compressor (soft clip, ~5 dB) -> Auto Filter notch sweeping 200 Hz-2 kHz with a slow LFO (or LP
12/24 dB from ~250 Hz automated up in builds) -> Chorus-Ensemble after a low-pass -> Erosion
(Wide Noise, subtle) -> short dark reverb -> Glue Compressor (~5 dB) -> EQ Eight in M/S with the
side high-passed at 200 Hz -> side-chain from the kick. Phaser for fills, automated back down.
Serum version: `SERUM.md` §4.

**Growl** (dubstep, bass music): FM in Operator: carrier A (sine plus added harmonics), B, C, D
modulating A, all fine-tuned +33 cents, levels about -9 / -12 / -15 dB; 1 voice, glide ~100 ms.
The growl is the **modulator level** moving: automate Oscillator D's level (or a rack macro on
it, which the user maps once) with different shapes per bar. Dry/wet rack: a Dry chain (clean
sub) and a Wet chain (EQ Eight high-pass 100 Hz and a mid boost 500 Hz-3 kHz -> Cabinet ->
Saturator with Color -> Phaser for vowels); blend with `live_rack_set_chain(volume=...)`. Then
EQ cleanup, Compressor 4:1 fast, side-chain. Use the formant filter (vowels) and phaser dry/wet
as extra automation lanes; faster movement in drops, slower in builds.

**Patterns.** Short staccato notes for rhythmic growls, overlaps for slides, call and response
between two bass sounds bar by bar; half-time drums at 140 (snare on 3).

## 7. Synthwave — 100-120 BPM (108-115), A/E/F#/C minor

**Drums.** Gated-reverb snare is everything: a short snare -> Reverb (plate/hall, decay 3-5 s,
size 80-100 %, 100 % wet, pre-delay 0) -> Gate (threshold about -25 dB, return -30 dB, release
50-100 ms, lookahead 10 ms), in an Audio Effect Rack chain next to a dry chain so the two blend.
Punchy 808-ish kick (+ at 60 Hz, click at 3-5 kHz), toms through the same gated reverb, hats dry.
```python
live_clip_write_pattern(track="Drums", slot=0, repeat=4, pattern={
    "kick": "x...|x...|x...|x...", "snare": "....|x...|....|x...",
    "hat":  "9.6.|9.6.|9.6.|9.6.", "ohh":   "....|...x|....|...x"})
```
**Bass**: saw (-6 dB) + sine sub (-8 dB) in parallel, mono, glide 30-80 ms, LP 600-900 Hz,
resonance 15-25 %, filter envelope D 200 ms S 60 %; driving 8ths or 16ths on the root;
side-chain 4:1, attack 0.5 ms, release 80-120 ms, 3-5 dB. **Arp** (the signature): saw or pulse,
2-4 unison voices, detune 10-15 %, LP 60-75 %, resonance 20-30 %, A 0 / D 300-500 ms / S 40-60 %;
`live_clip_write_arp(style="up", rate="1/16", octaves=2, gate=0.8)`; Chorus then dotted-1/8
delay (feedback 30-45 %, wet 25-35 %, high cut 6-8 kHz). **Lead**: two saws, B +7 cents, LP
80-90 %, A 5-15 ms / D 800 ms / S 75 % / R 400 ms; Chorus -> dotted-1/8 delay -> hall 3-4 s.
**Pad**: PWM pulse (LFO 0.2-0.5 Hz on pulse width) or two saws an octave apart, LP (MS2 type)
50-65 %, A 200-500 ms / R 2-3 s, high-pass 150-200 Hz, hall 4-6 s, low in the mix. Chorus on
everything; tape-style Saturator (2-4 dB, 60-80 % wet) on the master.

## 8. Acid (house, techno, trance) — 125-145 BPM, C1-C2

The TB-303 is one saw (or square for dub acid), a resonant 24 dB low-pass and an accent/slide
sequencer: **the filter is the instrument**. Operator: Saw D on A only, filter LP 24 dB (Ladder)
cutoff 30-40 % (400-800 Hz), resonance 60-80 % (below 50 % is generic, above 85 % whistles),
filter envelope A 0 / D 100-200 ms / S 0 / R 0, amount 50-70 %, filter velocity 60-80 %; amp A
0 / D 200-400 ms / S 60-80 % / R 50-100 ms (staccato variant: D 80-150 ms, S 0-20 %, R 20-40 ms);
1 voice, glide 60-120 ms (glide only on overlapping notes). Serum version: `SERUM.md` §4.
- **Accent** = velocity 110-127 (others 60-80) -> more filter envelope. Accent off the beat,
  every 3rd or 5th note.
- **Slide** = the note overlaps the next one. **Mix lengths**: short plucks and held notes.
- Mostly root and octave, minor intervals (b3, b7) for colour; write in the key of the kick's
  fundamental; try a 13- or 15-step loop for drift (`live_clip_set(loop_end=3.25)`).
```python
# classic acid, 1 bar in C (accent = high velocity, slide = overlap into the next note)
live_clip_add_notes(track="Acid", slot=0, notes=[
    ["C1", 0, 0.2, 120], ["C1", 0.5, 0.2, 70], ["C1", 0.75, 0.55, 120], ["C2", 1.25, 0.3, 120],
    ["C1", 1.5, 0.2, 70], ["C1", 2, 0.2, 70], ["C1", 2.5, 0.2, 120], ["C1", 2.75, 0.55, 70],
    ["D#1", 3.25, 0.3, 120], ["C1", 3.5, 0.2, 70]])
```
Chain: Saturator (drive 10-15 dB, Soft Sine or Analog Clip, soft clip on, output -6 to -10 dB)
or Overdrive (drive 40-60 %) -> EQ Eight (high-pass 40 Hz) -> Compressor 4:1 fast -> dotted-1/8
delay (feedback 20-30 %, return high-passed at 300 Hz) -> optional short spring reverb.
The trick: automate cutoff and resonance **independently** over 4-8 bars (slow rises, sudden
drops; resonance up in breakdowns). High resonance + high cutoff = screaming, + low cutoff =
bubbling.

## 9. Ambient and ethereal pads — any tempo (tempo sets chord length)

**Music first.** Simple triads with spread voicings (middle note up an octave, the 3rd up two
octaves on some chords); major 7ths are the most dreamy. Split chord tones across 2+ layers with
different timbres (layer 1 root + 5th low, layer 2 3rd + 7th high); a second layer of single
scale notes (3rd, 9th, 4th) that combine with layer 1 into one rich chord. A **drone** on one
note (major 7th, 5th or root) held while chords move. A **hidden lead**: deep, filtered chord
tones that melt into the wash.
```python
live_clip_write_chords(track="Pad A", slot=0, chords="F#m D A", duration=16,
                       voicing="spread", voice_leading=true, velocity=70)
live_clip_add_notes(track="Drone", slot=0, notes=[["C#4", 0, 48, 60]])
```
**Sound.** Keep patches simple: two detuned saws + low-pass + slow movement; complexity comes
from reverb. Wavetable starting point: saw with 2-6 unison voices at 1-2 % (ethereal) or 15 %
detune, a sine a fifth up quieter, LP 12 dB ~55-70 %, resonance 0, amp A 800 ms / D 2 s / S 80 %
/ R 3 s; slow LFO (0.06-0.5 Hz) on cutoff, a few cents of pitch, or wavetable position (keep the
position static on chord layers, move it on drones and leads). Envelope sweep: Env 2 with a 6-11 s
decay to both wavetable position and cutoff for a pad that evolves on one held note. A quiet
filtered-noise layer (band-pass, key-tracked) for air. **Low-pass every layer, then again after
the reverb** on the group: that is what makes layers melt.

**Effects as the instrument.** Reverb 80 %+ wet, 5-10 s, pre-delay 30-80 ms, diffusion high,
damping darkens the tail; ping-pong delay 3/16 or dotted 1/8, feedback 30-40 %; Chorus before the
reverb (0.5-1 Hz, feedback 20-30 %). The limiter trick (sound design, not mixing): a Limiter after
the reverb with the input pushed squashes the tails into a dense "distressed" texture. Textures:
Granulator II on a held note or field recording, an octave-down copy underneath, field
recordings high-passed at 200 Hz through the same reverb.

## 10. Hip-hop, lo-fi, trap, drum & bass — grooves

| Genre | BPM | Pattern (16 steps) | Feel |
|---|---|---|---|
| Lo-fi | 70-90 | kick `x.....x.........`, snare `....x.......x...`, hat `x.x.x.x.x.x.x.x.` | heavy swing (`swing=0.24`-`0.33` on hats), humanize velocity ±15-20, timing ±0.02 beats, low velocities |
| Boom-bap | 85-95 | kick `x..x..x...x.....`, snare `....x.......x...`, hat `xxxxxxxxxxxxxxxx` (50-80) | swing 58-62 % on hats, snare slightly late, kick on grid |
| Trap | 130-150 (half time) | kick `x......x........`, snare `........x.......` (on 3), hat 16ths with 1/32 rolls (`step="1/32"` bursts) | 808 slides (overlapping notes, `Porta Always` in Serum), tight kick/snare, loose hats |
| DnB | 170-176 | kick `x.........x.....`, snare `....x.......x...`, hat 16ths | ghost snares at 30-50, tight kick and snare, loose hats |
| Reggaeton | 95-100 | kick `x...x...x...x...`, snare (dembow) `...x..x....x..x.` | straight, punchy |

**The golden rule of swing:** strong beats stay on time, weak beats arrive late. Swing hats
58-62 %, snare 52-54 %, kick on the grid; percussion or rim slightly *early* for push-pull.
Behind the beat (5-20 ms late) = laid back (boom bap, lo-fi, neo-soul); ahead (5-10 ms early) =
urgent (some techno, uptempo funk). Velocity matters more than timing: hats wander between 40 and
100 rather than strictly alternating; ghost snares 20-50; kick 100-120; snare 100-127. Try making
the late (swung) note louder than the on-beat one for a rolling groove.

**Energy in three moves.** From one section to the next make exactly three obvious changes:
intro -> A: steady hats, clap on 2 and 4, a percussion hit before the claps; A -> B (peak): busier
kick (late, quiet extra kicks), ride instead of simple percussion, tom fills; B -> outro: strip
back to downbeat kicks and some hats. In a loop: duplicate to 2 bars and change one small thing.

## 11. Arranging a loop into a track (club music)

`PRODUCTION.md` §2 has the section blueprints; this is the working method.
1. Treat the full 8-bar loop as **the drop**, placed around bar 57, and work backwards.
2. **Intro (1-32)**: kick (plus clap) only, hats in at bar 9, percussion layered in gradually,
   bass at bar 17 playing **one note** (the root). Clean, mixable, few FX.
3. **Build (33-56)**: percussion, a filtered hook or vocal opening, a 16th snare build with a
   rising volume curve, a band-pass (not low-pass) white-noise sweep.
4. **Drop 1 (57-72)**: everything; the bass switches from one note to the full line.
5. **Breakdown (73-88)**: drums out, bass out or high-passed away, percussion "falling apart",
   elements returning gradually.
6. **Drop 2 (89-104)**: drop 1 with variations; cut the kick halfway for tension.
7. **Outro (105+)**: the intro in reverse, elements leaving one by one.

Extras: a kick impact (kick with heavy reverb and a limiter, one long hit at transitions, never
over the bass); crashes generously, two or more different ones (one with delay, one with reverb),
a short reversed crash before the main one; bass high-pass automation through breakdowns that
snaps back on the drop. Lengths: techno/house 6-8 min with 16-32-bar DJ intros and 8-16-bar
breakdowns; trance/EDM 5-7 min with 16-32-bar breakdowns; DnB 4-6 min with double drops;
hip-hop/lo-fi 3-4 min, verse/hook form. LiveBridge steps for this are in `WORKFLOWS.md` §1.
