# Making a track that works as a whole

Companion to `SKILL.md` (tool mechanics). This file is the *musical* knowledge: how a track is
built, how tension and release work, which effects go where, and how to check your work with
`live_theory_analyze` and `live_audio_analyze`. Read it before arranging, building transitions or
mixing. Rules here are defaults that fit most electronic/pop music — the user's taste and the
reference track win.

## 1. Think in energy, not in parts

A track is an **energy curve** over time. Every section either raises energy, holds it, or
releases it; a listener gets bored when the curve is flat and tired when it never drops.

- Energy comes from five dials: **density** (how many parts), **low end** (kick + bass present
  or not), **brightness** (filter cutoff, hats, air), **rhythmic activity** (16ths vs quarters,
  fills), **loudness/width**. A drop = all five up. A breakdown = low end and rhythm out,
  harmony and space in.
- **Contrast makes impact.** A drop only hits as hard as the bar before it is empty. Removing
  things (kick out, low cut, a bar of silence) is as important as adding them.
- Work in **powers of two**: 4/8/16/32-bar blocks. Change *something* every 8 bars (add a hat,
  open a filter, a fill, a new layer), something bigger every 16.
- One **idea** per track (a hook: riff, vocal chop, bass motif, chord stab). Everything else
  supports it. Introduce it partially early (filtered, only rhythm, only 2 notes), reveal it in
  full at the first drop, vary it in the second.
- Plan first: `live_cue_layout` with the sections below, *then* fill them. After arranging,
  bounce and look at `energy.rms_db` from `live_audio_analyze` — it must show the shape you
  intended (quiet intro, dip before the drop, highest plateau at the drops).

## 2. Section blueprints

Bars at 4/4. Pick one, adapt lengths to the genre and the reference.

**Club (house, techno, trance, DnB) — DJ-friendly, ~5-7 min**

| Section | Bars | What happens |
|---|---|---|
| Intro | 16-32 | Drums first (kick + hats), no bass or filtered; elements enter every 8 bars. Mixable. |
| Build A | 8-16 | Hook teased (filtered), bass enters or rises, riser in the last 4-8 |
| Drop 1 | 16-32 | Full groove: kick, bass, hook. Variation at the halfway point |
| Breakdown | 16-32 | Kick + bass out. Chords/pad/vocal, wide and wet. Lowest energy of the track |
| Build B | 8-16 | Longer/bigger than Build A: snare roll, riser, filter opening, last bar emptied |
| Drop 2 | 32 | Drop 1 + one new element (extra hat layer, counter-melody, octave-up bass) |
| Outro | 16-32 | Mirror of the intro: remove elements every 8 bars, end on drums |

**Song form (pop, hip-hop, lo-fi, indie electronic) — ~2.5-3.5 min**

| Section | Bars | What happens |
|---|---|---|
| Intro | 4-8 | Signature sound or filtered chorus chords; short |
| Verse 1 | 8-16 | Sparse: drums + bass + one harmonic part; leaves room for the vocal/lead |
| Pre-chorus | 4-8 | Lift: rising line, drums thin out or roll, filter opens |
| Chorus | 8-16 | Everything: wider, brighter, the hook, fuller drums |
| Verse 2 | 8-16 | Verse 1 plus one new layer (perc, counter-line); never an exact copy |
| Chorus 2 | 8-16 | As chorus 1, optionally double length |
| Bridge | 8 | Contrast: new chords (start on IV or vi), half-time or drums out |
| Final chorus | 16 | Biggest: extra harmony, ad-libs, maybe key up +1/+2 |
| Outro | 4-8 | Hook fragment, filter down or hard stop |

Trap / drill: intro 4-8, hook 8-16, verse 16, hook, verse, hook, outro; the 808 and hat
rolls carry the energy changes, drops are made by muting drums for 1-2 beats before the hook.

In Live: build each section as a **scene** (copy the fullest scene, then *remove* parts for the
quieter sections — subtractive arranging keeps everything coherent), name them, then
`live_arrangement_from_scenes`.

## 3. Build-ups — the toolbox

A build raises tension over 4-16 bars and **releases on bar 1 of the next section**. Stack 3-5
of these; more for the main build, fewer for small transitions.

| Technique | How (Live device -> LiveBridge) | Notes |
|---|---|---|
| **High-pass sweep** on the drum/music bus | `Auto Filter` (highpass), automate Frequency 30 Hz -> 400-800 Hz with `ramp_up`; snap back to minimum on the drop | Removes low end gradually so the drop's bass feels huge. The single most effective trick |
| **Low-pass opening** on the hook/pad | `Auto Filter` lowpass, Frequency ~300 Hz -> open, curve > 1 so most of the opening happens late | Classic filter build; add Resonance 20-40 % for bite |
| **Snare/clap roll** | `live_clip_write_pattern`: 1/4 notes -> 1/8 -> 1/16 -> 1/32 over 8 bars, velocity ramp 60 -> 127; optional pitch rise via Simpler Transpose automation | Halve the note length every 2 bars; last half bar can drop out entirely |
| **Riser** (noise or tonal) | White-noise sample or synth noise osc + rising filter + rising volume; or a Splice "riser"/"uplifter" (match length to the build, end exactly on the drop) | Tonal risers must be in key (root or fifth) |
| **Pitch rise** | Automate synth pitch/Transpose +12 (or +24) over the build | On the lead or a dedicated riser, not on the bass |
| **Reverb/delay swell** | Automate the send to the reverb return `ramp_up` (e.g. -inf -> -3 dB) on snare roll/vocal; cut the send on the drop | Wash grows, then dry drop = contrast |
| **Kick removal** | Mute kick for the last 1-2 bars (or last 2 beats) | The "air" before impact |
| **Stereo narrowing** | `Utility` Width 100 % -> 0-30 % over the build, 100 %+ on the drop | The drop opens up physically |
| **Volume dip** | Music bus -2 to -3 dB across the build, back to 0 on the drop | The drop is perceived as louder without clipping |
| **Rhythmic gating / stutter** | `Beat Repeat` on the last bar (Grid 1/8 -> 1/16 -> 1/32) or gate via `Auto Pan` (Phase 0, square) | For the final bar only |
| **Tension harmony** | Hold the V (or bVII) chord, or a pedal note on the fifth, through the build; resolve to i/I on the drop | Theory-level tension; check with `live_theory_analyze` |
| **Drum fill** | Last bar or last 2 beats: tom/snare fill, leave the final 8th or quarter **silent** | Silence before the 1 is the strongest accent there is |

Automation mechanics: session clips -> `live_automation_shape(parameter=..., shape="ramp_up",
start=..., end=..., low=..., high=..., curve=2.0)`; arrangement -> `live_automation_record` with
`shape=` or `points=`. Always write the **reset** on the first beat of the next section (filter
open, width 100 %, send back down) or the drop stays thin.

**The last bar before a drop** (do at least two): kick out · everything high-passed · a
reverse crash/reverb tail swelling into the 1 · vocal or FX one-shot ("pre-drop") · a full
quarter note of silence on beat 4.

## 4. Drops, breakdowns and transitions

**Drop impact checklist**: crash + sub impact ("boom") on beat 1 · full low end returns exactly
on the 1 (kick + bass) · filters fully open · width back · reverb sends reset so it is *drier*
than the build · side-chain audible · the hook in its full form. If the drop feels weak, the
build is too full — remove more before it rather than adding to the drop.

**Breakdown**: drop the kick and bass, keep or introduce chords/pad/vocal, open up reverb and
delay (longer decay, higher sends), automate a slow low-pass down then up. Give it a new
detail the drop did not have (piano doubling the chords, an arp, a vocal phrase). It is the
emotional centre — harmony matters most here.

**Small transitions (every 8/16 bars)** — pick one: crash or reversed crash on the 1 · short
fill in the last 2 beats · down-lifter (falling noise) after a drop to settle · hat opening
(closed -> open hat last 8th) · one-beat drum mute · delay throw on the last word/note (send up
for one beat) · tape-stop/pitch-down on the last beat. Put transition FX on their **own track**
("FX") so they are easy to move and level.

**Element in/out rules**: bring parts in on a phrase start (bar 1, 9, 17 ...), take them out
at a phrase end. Fade long pads and FX in (volume or filter ramp over 4-8 bars) instead of
hard-starting them. Never start two big new elements in the same bar unless it is a drop.

## 5. Effects — what, where and why

**Signal-chain order (default)**: EQ (corrective: low cut, remove mud) -> compressor ->
saturation/colour -> EQ (tone) -> modulation (chorus/phaser) -> *sends* to delay and reverb ->
Utility (gain, width). Time-based effects go on **return tracks**, not inserts, so several parts
share one space (that is what glues a mix).

| Effect (Live) | Use it for | Typical settings |
|---|---|---|
| `EQ Eight` | Low cut everything but kick/bass (80-150 Hz; 200-300 Hz for hats/FX), cut mud 250-500 Hz on pads/chords, tame 2-5 kHz harshness, gentle air shelf 10 kHz+ on hats/vocals | Cut narrow, boost wide; cuts before boosts |
| `Compressor` | Level control (ratio 2-4:1, 3-6 dB GR), punch (attack 10-30 ms lets the transient through), **side-chain** from the kick on bass/pads (ratio 4:1+, attack ~1 ms, release 80-200 ms tuned to the tempo) | `live_routing_route(method="sidechain")` |
| `Glue Compressor` | Drum bus / master glue: ratio 2:1, attack 10-30 ms, release Auto, 1-3 dB GR | More than 3 dB GR on a bus flattens the groove |
| `Saturator` / `Drum Buss` / `Roar` | Warmth and harmonics: bass audible on small speakers (Saturator, soft curve, Drive 3-8 dB), drum weight (Drum Buss: Drive, Boom tuned to the key's root) | Level-match after: louder always sounds "better" |
| `Reverb` / `Hybrid Reverb` | Return A: short room/plate 0.6-1.2 s (drums, keeps things close); Return B: long hall 2.5-5 s (pads, vocals, breakdowns). Low cut the return at 200-300 Hz, high cut 6-10 kHz | Pre-delay 10-30 ms keeps the source in front |
| `Delay` / `Echo` | Tempo-synced 1/8 dotted or 1/4 on leads/vocals; ping-pong for width; filter the repeats (300 Hz - 4 kHz); feedback 25-45 % | Delay throws: automate the send for single words/notes |
| `Auto Filter` | Sweeps (section 3), LFO movement on pads (rate 1/2-2 bars, small amount), band-pass "telephone" intro | Drive/Resonance add character |
| `Chorus-Ensemble` / `Phaser-Flanger` | Width and movement on pads, keys, hats; slow rate | Never on sub bass |
| `Utility` | Gain staging, **Bass Mono** below 120 Hz, width automation, mono check | Last in the chain |
| `Limiter` | Master only, last device: ceiling -1 dB, 2-4 dB reduction max while producing | Not a volume knob |
| `Redux`, `Erosion`, `Vinyl Distortion` | Lo-fi texture, transition degrade (automate Redux down-sampling into a drop) | Small amounts |
| `Beat Repeat`, `Grain Delay`, `Spectral Time/Resonator` | Stutters, glitches, freezes, tonal FX from drums | On an FX return or resampled to audio |

Depth rule: dry + loud + bright = **front**; wet + quieter + darker = **back**. Give each part a
place: kick, bass, lead/vocal front and centre; pads, FX, backing back and wide.

## 6. Harmony and melody that fit together

- **Settle the key first** and set it in Live (`live_transport_set(root_note="F",
  scale_name="Minor")`) — then
  `fit_scale`, Roman-numeral chords and `key="song"` all agree. Existing material?
  `live_theory_analyze(clips=[...])` tells you the key, the progression and what clashes.
  Live 12.4.15+ also gives each clip its own scale: a part in another mode (a borrowed-chord
  section, a modulation) gets `live_clip_set(scale_name=..., root_note=...)` and is checked
  with `key="clip"`; `clip_scale` in the analysis flags a clip whose scale disagrees with its
  notes.
- Progressions that work (minor keys dominate electronic music): `i VI III VII` (epic/trance/
  pop), `i VII VI VII`, `i iv VI V`, `i VI iv v`, `VI VII i` (uplifting 3-chord), one-chord
  vamps with a moving bass (techno, house). Major: `I V vi IV`, `vi IV I V`, `IV I V vi`,
  `ii V I` (jazz/lo-fi with 7ths and 9ths). Modal colour: dorian (minor with a bright 6th —
  deep house, funk), phrygian (b2 — dark techno, trap), mixolydian (b7 — disco, funk).
- **Bass**: root on the chord change, then fifth/octave/passing notes on weak beats; mono;
  below C3; one note at a time; leave gaps where the kick hits (or side-chain). A bass note that
  is not in the chord must be a deliberate passing tone on a weak beat.
- **Chords**: voice-lead (keep common tones, move other voices by step:
  `voice_leading=true`); keep voicings between C3 and C5; no thirds below ~E2 (mud); spread wide
  voicings for pads, tight for stabs. 7ths/9ths for warmth (house, lo-fi, R&B), power/sus chords
  for ambiguity (techno, trance builds).
- **Melody/lead**: mostly chord tones on strong beats, steps between them; a range of about an
  octave; **repeat with variation** (A A' A B: say it, repeat it, repeat it, answer it); leave
  rests — a hook needs space to be remembered; land on the tonic or third at phrase ends, on
  the fifth or second to keep it open. Highest note once per phrase, near the end.
- **Call and response** between parts (lead <-> bass fill, vocal <-> synth stab) instead of
  everything playing at once: parts share the bar like a conversation.
- **Rhythm relationships**: the bass locks to the kick pattern, chords/stabs play *around* the
  snare/clap (off-beats, syncopation), hats carry the subdivision. Groove = velocity accents +
  small timing offsets (`swing`, `humanize_timing` 0.01-0.02) — quantised-flat parts sound dead.
- After writing parts, run `live_theory_analyze(clips=[bass, chords, lead])`: fix every
  `semitone` clash that is not intended, every `low_mud` entry, and check `out_of_key` notes.

## 7. Arrangement density — who plays when

Frequency slots, max one dominant part each at any moment: **sub** (kick *or* bass at a time ->
side-chain), **bass body** 80-250 Hz, **low-mids** 250-800 Hz (chords, snare body — the
crowded zone: be strict), **mids** 800 Hz-3 kHz (lead/vocal — the listener's focus),
**highs** 5 kHz+ (hats, air, noise). If two parts fight for one slot: alternate them in time,
move one an octave, EQ a pocket, or delete one. Three to five simultaneous musical elements is
plenty; "bigger" comes from contrast and layering the *same* idea (octave doubles, a second
timbre on the same notes), not from more ideas.

Layering: layer sounds that differ in role (sub + mid bass + top fizz; kick click + body +
sub), high-pass the upper layers, keep the sub layer mono, check phase on the low layers.

Variation budget for a repeated 8-bar loop: bar 4 small fill, bar 8 bigger fill or drop-out;
second 8: add/remove one percussion part, change the last chord or the bass ending; every 16:
a transition from section 4. Automate *something* slowly through each section (filter, send,
decay, width) so nothing is static.

## 8. Mixing as one picture

1. **Gain-stage first**: kick peaking around -10 to -8 dB, build the mix around it, master
   peaking near -6 dB before the limiter. Balance with faders before touching plug-ins.
2. **Low end**: kick and bass decided first — who owns the sub? Short punchy kick + long sub
   bass, or boomy 808 kick + mid bass. Side-chain the loser. Everything else low-cut. Mono
   below 120 Hz.
3. **Glue**: shared reverbs on returns, one bus compressor on drums, a little saturation on
   buses, parts from the same "world" (don't mix ten unrelated preset aesthetics).
4. **Width**: centre = kick, bass, snare, lead vocal; sides = pads, doubles, hats, FX. Check
   mono (Utility Width 0 %) — what disappears has phase problems.
5. **Measure, because you cannot hear**: `live_record_resample` the loudest section (or the
   whole arrangement), then `live_audio_analyze(file_path=<clips[0].file_path>, kind="mix",
   reference=<the user's reference track>)`. Read: `lufs` and `true_peak_db` (headroom),
   `crest_db` (< 8 = over-compressed), `bands_db` vs the reference (mud 250-500, harsh 2-5 k,
   missing sub), `stereo.low_end_width` (must be ~0), `correlation` (> 0.3), `energy` (does the
   arrangement breathe?). Fix the biggest deviation first, re-bounce, re-measure. Tell the user
   what you measured and that their ears have the final word.
6. Loudness targets for the final bounce: streaming -14 to -9 LUFS, club -8 to -6 LUFS, true
   peak <= -1 dBTP. While producing: stay quieter, keep headroom.

## 9. Genre cheat sheet (arrangement + signature moves)

| Genre | Structure | Builds & signature FX |
|---|---|---|
| House / tech house | 16-32 bar intro/outro, 2 drops, breakdown 16 | Filter sweeps on the music bus, clap/snare rolls, off-beat open hat drops out before the drop, vocal chops with delay throws, strong side-chain pump |
| Techno | Long evolving sections (32+), no real "chorus"; tension by modulation | Slow filter/decay/reverb automation over 32-64 bars, rumble bass (reverbed kick, low-passed, side-chained), hat decay opening, one-bar kick drops |
| Trance / melodic techno | Long breakdown (32-64) with the full melody, big build, euphoric drop | Supersaw stacks, 16-bar snare roll + riser + pitch rise, long hall reverbs, arp over pads, white-noise sweeps |
| Drum & bass | Intro 16-32, drop 32-64, short breakdown, second drop with switched bass | Bass modulation/resampling, fast risers, half-time switch-ups, amen fills at phrase ends |
| Dubstep / bass music | Intro 16, build 8-16, drop 16-32 (half-time, 140), repeat | Extreme pre-drop silence + vocal one-shot, call-and-response bass sounds per bar, LFO filter wobbles |
| Hip-hop / trap | Hook-verse form, 8-16 bar loops with mutes | Drum drop-outs (1-2 beats) before sections, 808 glides, hat rolls (1/32, triplets) as fills, filtered-sample intro, tape-stop |
| Pop / dance-pop | Verse / pre / chorus / bridge, 2.5-3.5 min, hook within 30 s | Pre-chorus lift with drums thinning, riser + reverse reverb into the chorus vocal, final chorus with added harmonies |
| Lo-fi / chillhop | Loop based, 2-3 min, A/B sections with small changes | Vinyl noise, side-chained pads, low-passed everything (top ~8-10 kHz), tape wobble (slow pitch LFO), drum mutes as transitions |
| Ambient / downtempo | Slow evolution, no drops | Long fades, reverb/delay as instruments, spectral freezes, density changes instead of drum fills |

## 10. Finishing checklist

1. Sections named with cues; the energy curve has a clear peak and a clear low point.
2. Something changes at least every 8 bars; no section is an exact copy of an earlier one.
3. Every transition has a lead-in (fill/riser/reverse) **and** a landing (crash/impact/new
   element), and every automated build parameter is reset on the 1.
4. `live_theory_analyze` over bass + chords + lead: no unintended clashes, one key.
5. Low end: one owner of the sub at a time, mono, side-chained.
6. Bounce -> `live_audio_analyze` (with the reference if there is one): headroom, balance,
   width, energy shape.
7. Tell the user what to listen for ("the build into bar 33: filter + snare roll, the kick
   drops out for the last bar") and ask for their ears — then iterate.

## 11. Melody craft (lead, hook, topline, melodic bass)

Adapted from hellyee's melody-craft skill (MIT, `THIRD_PARTY_NOTICES.md`). §6 says which notes
fit; this is how a phrase becomes a melody instead of a scale exercise.

**The seven traps** — a generated melody sounds simple when it has these. Never hand over one
that fails more than one, unless it is a stated choice:
1. **Grid marching**: every note on a beat or an 8th. Start off the beat, anticipate the bar,
   arrive late.
2. **Uniform lengths**: all 0.5 or 1.0 beats. Use 3+ lengths: ornaments 0.25, motion 0.5-0.75,
   anchors 1.5-2, an arrival 2.5-4.
3. **Scale-walking**: up the scale, down the scale. Steps are filler; a leap of a 6th says more
   than six steps.
4. **No rests**: wall-to-wall notes. At least one beat of silence every 4 bars; end phrases a
   beat early.
5. **Flat velocity**: everything 90-100. Span 25+, shaped toward the climax (62 -> 74 -> 88 ->
   106), ornaments -20, syncopated accents +10.
6. **Only chord tones**: every note safe. The emotion is in notes that don't belong and then
   resolve by step.
7. **Pasted repeats**: bar 1 four times. Repeat the shape, change the detail.

**Build it**
- **Motif first**: 3-6 notes with one distinctive thing (a leap, a rhythm, a tension). Develop
  it: sequence (a step higher), inversion (rise becomes fall), augmentation/diminution
  (durations doubled/halved), fragmentation (repeat the tail, tightening), octave
  displacement. A strong 8 bars: motif (1-2), its sequence (3-4), fragment and build (5-6),
  climax and resolution (7-8).
- **Question and answer**: the first half ends away from the tonic (2nd, 5th, 7th), the second
  lands on the tonic or third. For longing, deny the answer once — repeat the question a third
  higher, then resolve.
- **Tension notes**, each resolved by step: appoggiatura (leap to a non-chord note on a strong
  beat, fall a step: grief, longing), suspension (hold over the chord change, resolve down:
  ache), passing and neighbour tones (flow, hesitation), anticipation (the next chord's note
  early: urgency), b2 oscillation (phrygian rocking: menace), raised 7th (harmonic minor:
  drama). About one per bar in calm material, 2-3 at the peak.
- **Leaps**: after a 5th or more, move stepwise the other way. Put the biggest leap right before
  the climax.
- **One climax**: the highest note once, 60-75% through the phrase, with the highest velocity
  and often the longest note; two climaxes are no climax.

**Check it**: `live_theory_melody(track="Lead", slot=0, harmony=[{"track": "Pad", "slot": 0}])`
measures the checklist (`checks` with `ok` and the numbers, `suggestions` per failed check).

**Feedback means a minimal edit, not a new melody** — keep the motif and change only what the
words name (read with `live_clip_get_notes`, write with `live_clip_add_notes(replace=true)`,
then say in one sentence what changed):

| User says | Do |
|---|---|
| "too simple" | tension notes, off-grid starts, a real leap (traps 1, 3, 6) |
| "not emotional" | appoggiaturas on strong beats, a wider velocity arc, slower harmony under the climax |
| "complex but soulless" | *remove* notes: keep the contour, add rests |
| "monotonous" | develop the motif (sequence, inversion); change bars 5-6 |
| "too busy" | halve the notes; keep the climax and the anchors |
| "not catchy" | a motif of 5 notes or fewer, repeated more; simplify the first bar's rhythm |
| "should rise" | each repeat of the sequence higher; climax later and higher |
| "needs air" | rests; end phrases a beat early |
| "darker" | an octave lower, b2/tritone touches, lower velocity ceiling |
| "robotic" | off-grid starts, varied lengths, ±8 velocity, detached repeated notes (0.4 in a 0.5 slot) |

## 12. From a mood to notes

Adapted from hellyee's emotion-to-notes skill (MIT). When the user names a feeling instead of
(or next to) musical terms, place it on two axes — pleasant/unpleasant and calm/energetic —
and pick the nearest row:

| Mood | Scale | Tempo | Contour | Velocity | Density |
|---|---|---|---|---|---|
| Melancholy | minor, dorian | 60-85 | falling, wide leaps down | soft, 40-75 | sparse, long notes |
| Anger | phrygian, harmonic minor | 130-155 | jagged, short repeated motifs | hard, 95-127 | dense, off-grid accents |
| Calm | major, lydian | 65-90 | stepwise, gentle arcs | 50-85, even | sparse, legato |
| Euphoria | mixolydian, major (pentatonic) | 122-145 | rising leaps, wide range | punchy, 90-122 | dense, syncopated |
| Tension | phrygian, tritones, whole-tone touches | any | hovering, then a sudden leap | swelling | irregular |
| Nostalgia | dorian, major with a borrowed b7 | 70-95 | falling, then resolving up | 55-80, breathy | moderate, loose swing |
| Hope | lydian, major | 90-115 | rising, open intervals | building | building |
| Loneliness | minor, sparse harmony | 55-75 | isolated single lines | very soft, wide gaps | very sparse |
| Triumph | major, mixolydian | 100-130 | strong upward leaps, arrival on the tonic | 95-127 | strong downbeats |

- **The user's explicit key and tempo win**: the mood then shapes contour, velocity and
  density inside them. Never swap the key or tempo for an "emotionally better" one — say so.
- Genre bends it: "dark" in techno is minor/phrygian, sparse and hard-hitting, not slow.
- **Arcs** ("from nostalgic to euphoric"): give each section a row and change one or two
  dimensions between neighbours (keep the scale, raise density and velocity), filters opening
  as energy rises. Grief to hope: rotate the same chords so the progression starts on the major
  chord (Am-F-C-G -> C-G-Am-F) rather than changing key.
- **Verify, don't assert**: `live_theory_melody(..., mood="melancholy")` compares contour (ups
  vs downs), notes per bar, velocity and scale with the row; report it plainly ("dorian,
  falling contour, mean velocity 58 — matches melancholy").
