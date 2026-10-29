# Workflows: working *with* the producer

Companion to `SKILL.md` (tools), `PRODUCTION.md` (how a track works), `GENRES.md` (material per
genre) and `SERUM.md` (Serum 2). This file is step-by-step procedures for common requests:
extending an arrangement, co-writing, grooves, MIDI cleanup, chords, mix diagnosis, side-chain,
reference matching, pre-master checks, vocal chains, tempo and session snapshots, technical
checks and layer alignment, parallel compression, a mastering chain, creative send effects, adding
one section, designing a synth sound, transitions, a sound check, and balancing and mastering by
measurement. Several are
adapted from glincker/ableton-skills (MIT, see `THIRD_PARTY_NOTICES.md`).

**Principles in every workflow**
- **Read before you write.** `live_set_snapshot`, `live_arrangement_overview`,
  `live_clip_get_notes`, `live_device_parameters`: base every suggestion on the set, not on
  assumptions.
- **The music is theirs.** Propose, then write; confirm before changing the user's own clips,
  the master, or more than a few things at once. Your own new tracks and clips may be changed
  freely.
- **One idea per listen.** Make a change, say what to listen for, ask. Everything is one undo
  step (`live_transport_undo`), so trying something costs nothing: offer it.
- **Say what is impossible** (saving, exporting, macro mapping, grouping tracks: `SKILL.md` §7)
  and give the one manual step instead.

## 1. Extend an arrangement

User: *"Turn my 32-bar loop into a full track"*, *"make the second drop longer"*, *"add an
outro"*.

1. **Read the material.**
   - `live_set_snapshot(detail="minimal")`: tempo, tracks, scenes.
   - `live_arrangement_overview(include_empty=false)`: what plays where, cues, length.
   - Session-only set? `live_scene_list(include_clips=true)`.
   - `live_cue_list()`: existing section names.
   - The musical content of the core parts: `live_theory_analyze(clips=[...])` for the key and
     progression, so new parts fit.
2. **Find the roles.** Name each track's job (kick, bass, hook, pad, perc, FX) and the loudest,
   fullest section: that is the drop or chorus to build around.
3. **Propose a structure** in one message: a section table with bars and what plays (from
   `PRODUCTION.md` §2 or `GENRES.md` §11), the transitions, and the total length in minutes.
   Ask *once* which structure and how long; then build. Example: "Intro 16 (kick + hats, bass
   at bar 9 on one note), Build 8 (snare roll, HP sweep), Drop 32 (everything, variation at bar
   17), Breakdown 16 (pad + vocal, no kick), Build 16, Drop 32 (+ octave lead), Outro 16 =
   136 bars, 4:15 at 128 BPM".
4. **Lay out the sections.**
   - `live_cue_layout(cues=[{"name": "Intro", "bar": 1}, ...])` first, so the plan is visible.
   - From scenes: build one scene per section **subtractively** (duplicate the fullest scene
     with `live_scene_duplicate`, then delete or mute the parts that should not play), then
     `live_arrangement_from_scenes(sections=[{"scene": "Intro", "bars": 16}, ...])`.
   - Extending what is already in the arrangement: `live_arrangement_copy_range(start=...,
     end=..., destination=..., unit="bars")` repeats a section (all tracks or some),
     `live_arrangement_duplicate_clip(length=...)` stretches one part,
     `live_arrangement_move_clip` / `live_arrangement_delete_clip` remove parts per section.
   - **Automation comes along by default** (`automation=true` on all three): the scenes' clip
     envelopes become the tracks' automation lanes, and a copied range takes its lanes along.
     It is recorded in the background (fast Arrangement Record passes, transport stopped
     first): say so in one line, poll `live_automation_record(action="status")` until
     `finished`, and do not start playback or another recording before. Pass
     `automation=false` only when the user asks for clips alone.
5. **Vary every repeat.** No section is an exact copy (`PRODUCTION.md` §7): in the copy, change
   the bass ending, add or drop a percussion part, open a filter, transpose the hook an octave
   (`live_clip_transform_notes(transpose=12)` on a duplicate clip). New material that must fit:
   write it in a session clip first, check it with `live_theory_analyze`, then place it.
6. **Transitions.** For every boundary a lead-in and a landing (`PRODUCTION.md` §3-4): snare
   roll (`live_clip_write_pattern`), filter sweeps with `live_automation_record` over the build
   range, a crash on the 1, the kick muted for the last bar. Put FX on their own "FX" track.
7. **Check.**
   - `live_arrangement_overview` again: the shape should be visible in the grid.
   - Bounce: `live_record_resample(start=..., bars=..., source="master")` (real time; tell the
     user).
   - `live_audio_analyze(file_path=..., kind="mix")`: the `energy` curve must show the planned
     peaks and dips.
   - Tell the user which bars to listen to.

## 2. Compose together (co-writing)

User: *"Write a bassline for my chords"*, *"give me a counter-melody"*, *"what comes next?"*.

1. **Get the context**: key and chords from `live_theory_analyze(clips=[<chords>, <bass>])`
   (set the song key with `live_transport_set(root_note=..., scale_name=...)` if it differs),
   tempo and genre from the set, the user's words for mood.
2. **Offer 2-3 directions in words first**, short and musical: "A) a rolling 16th bass on the
   roots with octave jumps, B) a sparse syncopated line leaving the kick alone, C) a walking line
   through the chord tones". Let the user pick (or pick the one that fits the genre if they say
   "surprise me").
3. **Write variants into separate clips**, never over the user's clip: one per scene slot
   (`slot=` empty slots) or a new track named for the idea ("Bass B"). Use the composite tools:
   `live_clip_write_chords`, `live_clip_write_arp`, `live_clip_write_pattern`, or
   `live_clip_add_notes` for free lines (`GENRES.md` has idiomatic material).
4. **Check the fit**: `live_theory_analyze(clips=[existing..., new])`. Fix unintended
   `semitone` clashes and `low_mud` before the user hears it
   (`live_clip_transform_notes(fit_scale=true)` or `live_clip_modify_notes`).
5. **Let them hear it**: fire the scene (`live_scene_fire`) only if they asked you to play; say
   which slot holds which variant.
6. **Remember the choice**: `live_memory_note(text="kept bass B: sparse syncopated line",
   kind="decision")`, and leave rejected directions out of later suggestions.
7. **Develop what they keep**: variations for later sections (`live_clip_duplicate_notes` with
   `transpose_steps=`, `live_clip_transform_notes(invert=..., retrograde=true)`, rhythmic
   displacement with `shift=`), call and response with another part, a reduced version for the
   intro. Ask before deleting the variants they did not pick.

Leave the **lead melody** to the user unless they ask for one; offer a motif to react to instead.
For movement without writing variations, give hats, ghosts and percussion a play probability:
`live_clip_note_chance(probability=[0.5, 0.9], pitch=...)`.

## 3. Start a project (producer mode)

User: *"Set up a lo-fi house loop in C minor"*, *"start a techno track"*.

1. Parse the brief: genre, key and mode, tempo (genre default), length, instruments. If two or
   more are missing, ask **one** question; otherwise use the defaults (`GENRES.md`, `SKILL.md`
   §4 table).
2. `live_set_snapshot`: don't duplicate what exists. A non-empty set: ask whether to add
   to it.
3. `live_transport_set(tempo=..., signature="4/4", root_note=..., scale_name=...)`.
4. Returns: `live_device_insert("Reverb", track="A")` (hall/plate) and
   `live_device_insert("Delay", track="B")` (ping-pong, dotted 1/8) if the set has returns; no
   automatic sends (some genres want it dry).
5. Tracks with instruments: drums, bass, chords/pad, and an empty lead track for the user, with
   names and colours (drums red/orange, bass yellow, chords green, pads blue, lead purple, vocals
   pink, FX grey). Use the browser for presets (`live_browser_load(..., new_track=true,
   track_name=...)`), `live_device_insert` for plain built-ins, `live_plugin_expose` for Serum 2.
   At most ~8 tracks at once; more in stages.
6. Starter patterns from `GENRES.md`, 4-8 bars. Never arm tracks.
7. Report what was created and what the user could do next (play a melody, pick a variant).

## 4. Grooves and MIDI cleanup

**Write a groove**: genre and tempo -> `live_drumrack_overview` to see the kit (load one if
missing) -> the pattern from `GENRES.md` (§10 for hip-hop/trap/DnB) -> humanize per genre ->
optional fill in the last 1-2 beats of bar 2 (snare roll, tom run, hat stutter). Write at most 8
bars, then let the user judge. Nothing 100 % on the grid except techno and hard EDM.

**Clean up a clip** (`live_clip_get_notes` first; ask which clip if it is unclear). Preview the
plan in one sentence ("±12 velocity on the 32 hats, keeping the 4 accents, ±0.015 beat timing")
and apply it in one call:

| Request | Call | Defaults |
|---|---|---|
| Humanize drums/hats | `live_clip_transform_notes(humanize_velocity=..., humanize_timing=...)` | hats/perc ±10-15 velocity, drums ±8-12; timing 0.01-0.03 beats. Keep accents (>100) loud and ghosts (<40) quiet: filter with `pitch=` per row or adjust after |
| Humanize keys/strings | `humanize_timing` 0.02-0.04 | never move bar 1 beat 1 |
| Swing | `swing=` 0.08-0.33 with `quantize="1/16"` | hip-hop 0.16-0.24, house 0.08-0.16, techno 0 |
| Quantize (keep feel) | `live_clip_transform_notes(quantize="1/16", strength=0.75)` or `live_clip_edit(action="quantize", amount=0.75)` | 60-70 % for swung genres, 90-100 % for tight percussion only |
| Overlaps / stuck notes | `live_clip_transform_notes(fix_overlaps=true)`; delete notes shorter than ~0.02 beats with `live_clip_remove_notes(note_ids=...)` | ask about notes longer than 2 bars in fast parts |
| Tighter / looser lengths | `gap=` (staccato), `legato=true` | |
| Voice leading of chords | read the chords, re-voice with `live_clip_write_chords(..., voice_leading=true)` into a new clip | never on the melody |
| Generative variation | `live_clip_modify_notes(changes=[{"note_id": ..., "probability": 0.6}])` | hats and percussion; Live plays them sometimes |

Never change pitches in a cleanup (except voice leading), never touch velocity-127 accents,
never exceed ±25 velocity or ~±0.05 beats of humanize. After applying: "listen; more or less?"

**Feel and tempo coaching.** Stiff beat -> humanize or swing; "too slow but I don't want to
speed up" -> double-time hats or half-time drums at the same tempo; never change the tempo
without approval (audio clips and the user's timing shift); time signature changes at section
boundaries only (`live_scene_set(time_signature=...)` per scene). Defaults: ambient 50-70,
lo-fi 70-90, boom-bap 85-95, house 120-128 (124), techno 125-135, trance 132-140, hardstyle
150-160, DnB 170-176.

## 5. Chords

1. Need: key and mode, mood/genre, target sound. Missing two? Ask one sentence.
2. Pick a progression (in C / C minor; transpose to the key, or use Roman numerals with the song
   key set):

| Mood / genre | Progressions |
|---|---|
| Sad, cinematic | `i VI III VII` (Cm Ab Eb Bb); `Cm Ab Fm G` (Aeolian + V) |
| Hopeful, pop | `I V vi IV`; `I vi IV V` |
| EDM uplifting | `vi IV I V` (Am F C G) |
| Tension | `i iv i V7`; `i VII VI V` |
| Neo-soul, R&B | `Imaj7 iii7 vi7 ii7` |
| Lo-fi, chill | `Cmaj7 Fmaj7 Bb7 Eb7`; ii-V-I with 9ths |
| Dorian / deep house | `i IV v i` in dorian; `Fm9 Bbm7` vamp |

3. Voice for the instrument: pads and synths spread over two octaves with no root in the bass's
   octave (`voicing="spread"` or `"open"`); stabs close; piano left hand root + fifth below C3,
   right hand 3rd + 7th (+9th) above; strings within range (Vln1 G3-G6, Vln2 G3-D6, Viola C3-A5,
   Cello C2-C5). `voice_leading=true` keeps common tones and moves voices by step.
4. Colour on request: 9ths on majors, 11ths on sus chords, b9/#11 on V chords.
5. `live_clip_write_chords(chords="...", duration=4, octave=3, voicing=..., voice_leading=true)`
   into a new clip, then `live_theory_analyze` with the bass to confirm.

## 6. Mix doctor

User: *"it sounds muddy"*, *"the kick and bass fight"*, *"no headroom"*.

1. **Symptom -> suspects:**

| Complaint | Look for |
|---|---|
| Muddy | 200-400 Hz build-up, no high-pass on non-bass parts |
| Boxy | 400-800 Hz |
| Harsh | 2-5 kHz, hats and synth tops, un-de-essed vocal |
| Thin | missing 100-250 Hz, over-eager high-pass on bass parts |
| Boomy | untamed 40-80 Hz, kick and bass resonances overlapping |
| Kick and bass fight | 60-120 Hz clash, no side-chain |
| No headroom | master peaking above -1 dB, tracks too hot |
| Lifeless, no glue | no bus compression, no shared reverb |
| Too wide / phasey | wide low end, stereo wideners, mono collapse |

2. **Inspect, don't guess:**
   - `live_mixer_get()` for every track's volume, pan and sends.
   - `live_device_list(tree=true)` for the chains; EQ and compressor settings via
     `live_device_parameters(filter=...)`.
   - While it plays: `live_mixer_meters(seconds=3)` for levels.
   - For frequencies: bounce the section and run `live_audio_analyze`; `flags` name mud,
     harshness, missing sub and wide low end. Ringing, hum, clipping, DC or a flipped side:
     `live_audio_problems` on the part (§12) — resonances come with an EQ Eight bell.
   - Kick and bass fighting can also be phase, not level: `live_audio_align` (§12).
   - Leave the user's solo and mute states as you found them.
3. **Diagnose in producer language**, with numbers: "the pads have no low cut and add to the
   250 Hz build-up the analysis flags; the bass has no side-chain from the kick".
4. **Propose concrete moves** (stock devices; values, not "add some EQ"): high-pass non-bass
   parts at 80-150 Hz; -2 to -4 dB shelf or bell at 200-300 Hz on muddy parts; -3 to -6 dB
   narrow cuts at resonances; kick->bass side-chain 4:1, attack 1-5 ms, release 50-120 ms, 4-6 dB;
   drum parallel compression (a return with 10:1 fast, blended 20-30 %); master bus glue 2:1, slow
   attack, 1-2 dB.
5. **Apply one fix at a time** after the user agrees, set by display text
   (`live_device_set_parameters(values={"1 Frequency A": "120 Hz"})` after reading the names),
   and ask them to listen between fixes. Don't touch the master without asking.
   **Masking, measured**: bounce the suspects over the same bars (`live_record_resample(
   source="Kick", start=..., bars=...)`, then `source="Bass"`, ...) and run
   `live_audio_masking(sources=[{"track": <bounce track>, "slot": 0, "name": "Kick"}, ...])`:
   per pair the bands where both are active and close in level, which one is louder there, and
   a move (side-chain for the low end, a cut on the less important part above it).
6. **Re-measure**: bounce again and run `live_audio_compare(before=<first bounce>, after=<new
   bounce>)`: it lists what changed with better/worse verdicts and which mix flags went away.

## 7. Side-chain

1. Confirm source and target ("duck the bass from the kick?").
2. `live_device_insert("Compressor", track=<target>)` then `live_routing_route(source=<kick>,
   destination=<target>, method="sidechain")` (Glue Compressor for a bus-style pump; pass
   `device=` when the target has several compressors).
3. Settings (read the names with `live_device_parameters(track=<target>, device="Compressor")`
   first):

| Use | Ratio | Attack | Release | Reduction |
|---|---|---|---|---|
| Kick -> bass (clean) | 4:1 | 1-5 ms | 80-120 ms | 4-6 dB |
| Kick -> bass (EDM pump) | 8:1 | 1 ms | 150-200 ms | 8-10 dB |
| Kick -> pads/chords | 3:1 | 10 ms | 150 ms | 3 dB |
| Vocal -> music bed | 2:1 | 15 ms | 300 ms | 2 dB |

   Release tuned to the tempo: the target should be back before the next kick (one 8th at 120
   BPM = 250 ms).
4. A clean trigger: a muted duplicate kick track with very short notes keeps the side-chain
   independent of the kick's sound (`GENRES.md` §2).
5. The user listens; adjust (too obvious: lower ratio or release; not enough: lower threshold).
   Not by default on everything: kick->bass is universal, pad pumping is genre-specific. Never
   side-chain the master.

## 8. Match a reference

1. Which reference, and which aspect: arrangement, mix, sound design, or vibe?
2. **If the user has the file**: `live_audio_analyze(file_path=<their mix bounce>,
   reference=<their reference file>)` compares loudness, band balance, width and the energy curve
   section by section. Their own reference file is fine to analyse; never download audio.
3. **From knowledge**: translate the artist or genre into techniques, not cloning. Examples:
   Burial (vinyl/cassette texture, rain, pitched vocal snippets, sub bass, 2-step garage
   rhythms, minor 7ths); Bonobo (live and electronic percussion, jazz harmony, vinyl textures,
   90-110 BPM, wide stereo, sample chops); Skrillex (vocal-formant bass modulation, stutter edits,
   bright leads, extreme width, side-chain pump); lo-fi hip-hop (Rhodes/Wurli, jazz chords,
   crackle, tape saturation, 70-90 BPM, swung 16ths); Kerri Chandler / deep house (9th chords,
   organ bass, swung hats, warm mix).
4. Propose the **three most distinctive** moves first, apply them, let the user listen, then
   refine. Unknown reference: ask what they like about it (drums? the vocal effect?).

## 9. Mix check and pre-master audit

**Bounce and measure** (`PRODUCTION.md` §8): record the arrangement section with
`live_record_resample(start=..., bars=..., source="master")` (real time, nothing may touch Live
meanwhile), then `live_audio_analyze(file_path=..., kind="mix", reference=...)`. One batch of
fixes per bounce: bounces cost real time.

| Reading | Club target | Usual fix |
|---|---|---|
| LUFS (drop) | -9 to -6, within ~1.5 LU of the reference | limiter input gain in 1 dB steps |
| True peak | <= -1 dBTP (streaming), -0.3 club | limiter ceiling, less master saturation |
| Crest | 8-11 dB; < 7 = over-limited | less limiting |
| Band balance | within 3 dB of the reference | change sources first; master EQ only the last 1-3 dB |
| Low-end width | ~0 (mono below 150 Hz) | Utility Bass Mono on bass parts |

Dark mix -> raise snare/hat/synth tops or open filters; buried mids -> raise chords/lead,
high-pass the bass layers; too much sub -> lower the sub, shorten the kick.

**Pre-master audit** (for sending to mastering, or before a final bounce). Report failures
first, then warnings, then passes; apply nothing without a yes, and one fix at a time:
- master peak -6 to -3 dB with no limiter and at most 2 dB of compression (if it goes to an
  engineer);
- pre-master loudness around -18 to -14 LUFS;
- mono compatibility: Utility Width 0 % on the master for a check; nothing disappears;
- low end below 30-40 Hz controlled, nothing useless above 18 kHz;
- no track clipping (`live_mixer_meters(seconds=5)`, `hot` flags);
- unused or disabled devices removed or bypassed.

Export itself is not in the API: give the settings (24-bit, project sample rate, normalize off,
dither only for 16-bit) and let the user export, or bounce with `live_record_resample`.

## 10. Vocal chain

Ask the style if not given; check peaks (`live_mixer_meters` while the vocal plays: very quiet or
clipping recordings need re-recording, processing won't fix them); keep existing devices.
Insert in order with `live_device_insert`, reverb and delay on **returns** via sends:

| Slot | Pop lead | Rap (modern) | Indie / lo-fi |
|---|---|---|---|
| EQ Eight | HPF 80 Hz, -3 dB 250 Hz | HPF 100 Hz, cut resonances | HPF 70 Hz |
| De-ess (Multiband Dynamics, 5-9 kHz) | 4:1 | 6:1 | (skip) |
| Compressor | 4:1, 5 ms, 60 ms, 4-6 dB | 6:1, 3 ms, 40 ms, 6-8 dB | 3:1, 30 ms, 80 ms, 3-4 dB |
| Glue / 2nd compressor | 2:1 slow, 1-2 dB | 3:1, 30 ms, 2-3 dB | (skip) |
| EQ Eight (tone) | +2 dB 100 Hz, 5 kHz, 12 kHz | +3 dB 5 kHz, +2 dB 200 Hz | -1 dB 8 kHz |
| Saturator | optional 2-3 dB | 4-6 dB soft sine | 3-5 dB, tape-like |
| Sends | hall 15-20 %, dotted-1/8 delay 15-20 % | plate 8-12 %, 1/16 slap 10 % | spring 25-30 % |

Never more than ~8 dB of compression in one stage; don't pitch-correct without asking. Tune by
feedback: dark -> +1-2 dB at 10-12 kHz; harsh -> more de-essing or a cut at 4-5 kHz.

## 11. Session snapshot

User: *"snapshot my session"*, *"what's in this set?"*, before a risky change.
`live_set_snapshot(detail="summary")`, `live_mixer_get()`, `live_device_list(tree=true)`,
`live_cue_list()`, and the arrangement overview -> a short markdown summary: tempo, key, length,
tracks (type, colour, volume, pan, devices with their key settings, clips with lengths and note
counts), returns and sends, master chain. Volume and the key device settings, not every knob; note
counts, not note lists. Write it to a file only if the user wants one (a timestamped name; never
overwrite).

## 12. Technical check and layer alignment

User: *"check these parts before I mix"*, *"something sounds off in the low end"*, *"the kick
loses punch when the bass comes in"*, a new sample or recording.

1. **Get audio of the part**: a LiveBridge Engine at the end of the track (Claude may add it,
   `SKILL.md` §4b) and `live_engine_capture(seconds=8, tracks=["Kick", "Sub"])` while it plays —
   both captured at the same moment — or `live_record_resample` per part, or the sample file.
2. **Technical problems**: `live_audio_problems(source="<file>")` on each part. Read it in order
   of severity:
   - *clipping*: lower the gain before the stage that clips; flattened peaks do not come back.
   - *dc_offset*: a low cut at 20-30 Hz (EQ Eight band 1) on that track.
   - *polarity*: the two sides cancel in mono: one side's phase switch on a Utility.
   - *hum*: narrow EQ Eight notches (Q 30+) at the listed lines — or a cleaner recording.
   - *noise*: a gate or expander; with samples, a cleaner sample.
   - *resonance*: each peak has `eq` {frequency_hz, gain_db, q}: one EQ Eight bell per peak
     (`live_device_set_parameters`), smallest cut that stops the ringing. A peak with a note
     name that never moves may be the part's own drone — ask before cutting it.
3. **Layers that should hit together** (kick + sub, two kick layers, bass DI + amp, snare top +
   bottom): `live_audio_align(a="<kick file>", b="<sub file>", band="low")`.
   - `polarity: inverted` -> `apply=true` (with b given as the track's clip) puts a Utility on
     B with its phase switches on; or switch them by hand.
   - `offset_ms` -> move B by that much: MIDI parts by shifting their notes
     (`live_clip_modify_notes`, `offset_beats` is given), audio clips by nudging, or Live's
     Track Delay in the track header (not in the API — tell the user the value).
   - `sum_gain_db` says what the fix brings: 1 dB and more is worth it; under 0.5 dB, leave it.
4. **Listen, then re-check**: capture again and run the same tool; `live_audio_compare` on the
   bus shows what the low end gained.

## 13. Parallel compression

User: *"make the drums hit harder"*, *"the bass needs more weight but not louder peaks"*. A
heavily compressed copy blended under the dry signal: the transients stay, the body comes up.

1. **Where**: drums (most common), bass, vocals. Not on parts with a lot of reverb.
2. **Two ways in Live**:
   - *Return track* (several parts can share it): `live_tracks_create(type="return",
     name="Crush")`, `live_device_insert(name="Compressor", track="Crush")` (or Glue
     Compressor / Drum Buss), then the send from each part with `live_mixer_set(track="Drums",
     sends={"A": "-12 dB"})`, raised until it just thickens.
   - *In one device*: Live's Compressor and Glue Compressor have a Dry/Wet knob — set it to
     20-40 % on the drum bus for the same effect in one place.
3. **Settings for the crushed copy**:

| Part | Ratio | Attack | Release | Reduction | Blend |
|---|---|---|---|---|---|
| Drums | 10:1+ (or Glue 10:1) | 0.1-1 ms | 50-100 ms | 10-15 dB | -15 to -10 dB under the dry |
| Bass | 6:1 | 5-10 ms | 100 ms | 6-10 dB | until the low mids fill in |
| Vocal | 8:1 | 1-3 ms | 60 ms | 10 dB | felt more than heard |

   Then EQ the crushed signal: cut its low end below 60-80 Hz (the dry kick carries that) and
   add a little 4-8 kHz for snap. A Saturator after the compressor adds grit.
4. **Check**: loudness rises — compare at matched level (`live_engine_compare` reports the
   LUFS change; judge tone at equal loudness), and keep the crest factor of the drum bus above
   ~8 dB (`live_engine_listen` while it plays).

## 14. Mastering chain (a demo or self-release)

User: *"master this"*, *"make it louder for the club / for streaming"*. Mastering in the set:
the master track's chain, last step before export. Mixing problems are fixed in the mix first
(§6, §9, §12) — the master chain only finishes.

1. **Before**: no clipping anywhere (`live_mixer_meters`), master peaking -6 to -3 dB with
   nothing on it, and a reference in mind (§8). Listen to the current state:
   `live_engine_listen(track="master", seconds=8)` while the drop plays.
2. **Chain on the master** (`live_device_insert(name=..., track="master")`, in this order,
   gentle at every step — each one 1-2 dB of work):

| Step | Device | Starting point |
|---|---|---|
| Corrective EQ | EQ Eight | low cut 25-30 Hz; cut only what `live_audio_problems` / `flags` name |
| Glue | Glue Compressor | 2:1, attack 10-30 ms, release Auto, 1-2 dB |
| Tone | EQ Eight | broad +/-1 dB moves only (low shelf 100 Hz, high shelf 10 kHz) |
| Width | Utility | Bass Mono on, 120 Hz; width unchanged unless the reference is wider |
| Colour (optional) | Saturator (soft) or Multiband Dynamics | 1-2 dB drive, level-matched |
| Loudness | Limiter | ceiling -1 dB (streaming) / -0.3 dB (club); raise its gain until the target |

3. **Targets** (measure with `live_engine_listen` live, then a bounce + `live_audio_analyze`):

| Destination | Integrated loudness | True peak | Note |
|---|---|---|---|
| Streaming | -14 to -9 LUFS | <= -1 dBTP | louder masters get turned down — judge the sound, not the number |
| Club / DJ | -9 to -6 LUFS | <= -0.3 dBTP | crest factor 7+ dB keeps the kick punching |
| Mastering engineer | none (pre-master, §9) | peaks -6 dB | no limiter at all |

4. **Check it**: `live_audio_compare(before=<pre-master bounce>, after=<mastered bounce>)` —
   punch (crest) must not collapse; low-end width ~0; mono check with Utility width 0 %.
   Bounce: `live_record_resample(source="master", ...)`; export itself is the user's step
   (24-bit WAV, dither only for 16-bit).

## 15. Creative send effects

User: *"make it bigger"*, *"add some space"*, *"a dub delay on the snare"*, transitions. Effects
on **returns**, fed by sends, so one space glues several parts (`PRODUCTION.md` §5).

| Effect | Return chain | Fed from | Tip |
|---|---|---|---|
| Delay throw | Echo or Delay 1/4 or dotted 1/8, feedback 30-40 %, filter 300 Hz-4 kHz | vocal / lead | automate the send up for single words or the last note of a phrase |
| Dub delay | Echo with Reverb on, feedback 50-70 %, Saturator after, high-pass 200 Hz | snare, stabs | ride the feedback for a run-away into a drop |
| Ducked reverb | Hybrid Reverb, 3-5 s, then a Compressor side-chained from the dry part | pads, vocal | tail blooms in the gaps, ducks while the part plays |
| Short room | Reverb 0.6-1 s, pre-delay 10 ms | drums | glues the kit; low cut the return at 300 Hz |
| Shimmer | Hybrid Reverb with its shimmer/pitch +12, long decay | pads in the breakdown | automate the send in, out before the drop |
| Distortion bus | Roar or Saturator hard, then EQ Eight band-pass 500 Hz-5 kHz | drums, bass | blended low under the dry for grit without losing low end |
| Freeze / riser | Grain Delay or Spectral Time, long feedback | any part, last bar | resample to audio for transitions (`live_record_resample`) |

1. Create the return (`live_tracks_create(type="return", name="Dub")`), insert the chain with
   `live_device_insert`, set the send with `live_mixer_set(track=..., sends={"B": "-15 dB"})`.
2. Movement: automate the send or the effect's feedback with `live_automation_record` over the
   bars it should happen, or audition with `live_engine_modulate` first and record when the
   user likes it.
3. Keep the low end out of every return (high-pass 150-300 Hz) and check mono: send effects
   are the usual source of a wide, phasey low end (`live_engine_listen` -> `low_end_width`).


## 16. Add one section

User: *"add a breakdown after the last drop"*, *"a 32-bar drop at bar 65"*, *"a buildup at bar
49"*, *"give it an intro"*. One section in an existing arrangement; a whole song from a loop is
§1. Adapted from kasperikaivola's ableton-mcp-pro `arrangement-sections` skill (MIT,
`THIRD_PARTY_NOTICES.md`). Do not change the tempo.

1. **Read**: `live_set_snapshot(detail="minimal")` (signature, tracks),
   `live_arrangement_overview(include_empty=false)` and `live_cue_list()`. Positions: give
   tools bars with `unit="bars"` (1-based) or beats; one bar is `numerator * 4 / denominator`
   beats (4/4 = 4, 6/8 = 3).
2. **The range.** The user's bar and length win. Default length: buildup 8 bars, everything
   else 16. *After the last drop* starts at the end of the last drop, rounded up to a bar. Find
   that drop in this order:
   1. the latest cue whose name contains "drop" (it runs to the next cue, or 16 bars);
   2. the latest arrangement clip named "drop";
   3. density: the last run of bars where a drum track and a bass track both have clips.
   Nothing found: say so and write nothing (an empty set still takes a section at a given bar).
3. **Roles** from the track name, otherwise the instrument: kick/snare/hat/perc/drum = drums;
   bass/sub = bass; pad/chord/stab/keys = harmonic; lead/pluck = lead; fx/riser/noise/atmos =
   fx. Skip group tracks.
4. **Overlap.** List the clips wholly inside the range and those that cross its edges
   (`live_arrangement_clips(start=..., end=..., unit="bars")`). Delete a clip
   (`live_arrangement_delete_clip`) only when it lies wholly inside and its track must change
   for this section; read its notes first if it is the source you copy from. A clip that
   crosses an edge: shorten it with `live_arrangement_resize_clip` only when the user agreed,
   otherwise leave it and report it. A buildup only adds.
5. **Write the section** (table below). Repeat material that exists rather than inventing:
   - a session loop: `live_arrangement_duplicate_clip(track=..., slot=..., time=...,
     length=..., unit="bars")` (repeats it to fill, clip envelopes recorded as lanes);
   - an arrangement passage: `live_arrangement_copy_range(start=..., end=..., destination=...,
     tracks=[...], unit="bars")` (lanes come along);
   - new notes: `live_arrangement_create_clip(track=..., start=..., length=..., unit="bars")`,
     then `live_clip_write_pattern` / `live_clip_add_notes` on the returned clip; a role
     without material gets notes from `GENRES.md`;
   - an audio hit: `live_sample_search(query="crash")` (or "impact"), then
     `live_arrangement_create_clip(track=<free audio track>, start=..., file_path=...)`;
     `live_tracks_create(type="audio", name="FX")` only when no audio track is free there.
6. **Automation is part of the section**, not a manual step: sweeps, send rises and fades with
   `live_automation_record(..., start=..., end=..., speed="fast")` (one recording at a time;
   wait for `finished`).
7. **Mark and check**: `live_cue_add(time=..., name="Breakdown")`, then
   `live_arrangement_overview` over the section. Report the start bar, the length, what was
   written, what was left in place and the automation recorded.

| Section | What it does |
|---|---|
| **Drop** | Full energy: repeat the fullest 4- or 8-bar loop on drums, bass and harmonic tracks, drums not thinned; a crash on beat 1; other roles left alone unless a clip inside the range blocks them |
| **Breakdown** | Pads, chords, atmospheres and fx stay; no kick, no main bass (delete only clips wholly inside). Last 4 bars: a snare or clap roll of 16ths, velocity rising 40 -> 110. Automate a low-pass opening or a reverb send rise over the last bars |
| **Interlude** | New sparse material, not a thinned drop: a 2-bar motif on a pad or lead tiled across, kick only on beat 1 of each bar (or none), the drop bass not copied unchanged; a new MIDI track for the motif when no pad or lead can take it |
| **Buildup** | Adds on top of the groove: a snare/clap 16th roll over the last 4 bars (last 8 when the build is 16+), velocity 40 -> 110; hat 16ths at 70-100 where the hat track is empty; a rising line of 8ths from the root to the octave on a lead or a new riser track (Auto Filter + Reverb after it). Record the high-pass or filter sweep, the riser send and a volume swell; crash on the next downbeat. Don't raise a send on a track that also plays outside the range — automate it |
| **Intro** | Drums throughout, bass and harmonic parts from the midpoint, crash on the last downbeat |
| **Outro** | Harmonic and fx throughout, drums and bass for the first half; a volume or filter fade on the master or the music group over the last bars |

## 17. Design a synth sound (Serum 2 or Omnisphere)

User: *"add a mystical FX synth to make this fuller"*, *"make a Serum preset for this style"*,
*"the lead sounds bland, ideas?"*, *"make this preset darker"*. Adapted from kasperikaivola's
ableton-mcp-pro synth sound-design skill and guide overview (MIT, `THIRD_PARTY_NOTICES.md`).

**What the request allows**

| Request | Do |
|---|---|
| "Add a … synth", "a new sound for …" | Read the song, design the sound, create the track and load it — no extra approval needed |
| "Create a Serum 2 preset in this style" | Design and write a new `.SerumPreset` (`SERUM_PRESETS.md`); a file-only request needs no Live connection |
| "Make this preset darker / faster / bouncier" | Edit the preset file into a new file (`live_preset_edit`), keep the original and everything unrelated; "faster" never means the song tempo |
| "Suggest", "ideas", "why is it bland" | Read-only: one recommended change plus up to two options, nothing written |
| "Improve / change this patch" | Small targeted writes with before values read back; never Init an existing patch |

**Read the context first**
1. `live_set_snapshot` (tempo, key, tracks), `live_tracks_get` on the target and its
   neighbours, the section's clips and notes (`live_clip_get_notes`, `live_theory_analyze`)
   for key, register and rhythm. Say what the evidence is ("from the MIDI and track names");
   don't claim to have heard anything without `live_engine_listen` or a bounce.
2. An existing synth: `live_device_parameters` (no filter) for the reachable controls; a
   Serum preset file holds the rest (`live_preset_inspect`).
3. Choose the smallest addition that fills a real gap: a quiet mid texture, transient detail or
   movement often helps more than another pad or bass. State role, register, density and
   width, and how it stays out of the existing parts' way.

**Choose the synth**

| Need | First choice | Why |
|---|---|---|
| Precise animated timbre, digital FX, clean modulation | Serum 2 | Wavetables, warp and matrix are all designable in the preset file |
| Organic, cinematic, acoustic or choir textures, hybrid atmospheres | Omnisphere | Soundsource layers and granular; Live reaches only Configured parameters (`OMNISPHERE.md`) |
| Improving an existing patch | Its own synth | Keep its identity; don't swap the instrument |
| The synth isn't installed (`live_plugin_list`) | Say so | Offer a design for one that is (Wavetable, Operator, Drift); never substitute silently |

**New Serum 2 sound**: design the whole patch as a file — sources, sub/noise, filters and
routing, envelopes, LFOs and matrix routes, FX order, voicing (`SERUM_PRESETS.md`; controls in
`SERUM.md` §6). One or two changes from a template is not a new preset; check with
`live_preset_compare` against the template. Then `live_preset_load(source_path=...,
track_name=...)` creates the track with Serum 2 and that patch (don't load a second Serum
from the browser). Add a clip only when asked or to audition it.

**New Omnisphere sound or another plug-in**: `live_browser_load(query="Omnisphere",
category="plugin", new_track=true, track_name=...)`, read the exposed parameters, set the
mapped ones, everything else goes in the hand-off list.

**Hand-off.** End with the design in chat: role and rationale, observed context and
assumptions, the patch (or the targeted change), what was verified, then one list per
instance with every setting Claude could not apply:

```text
Serum 2 — manual settings            (or: Omnisphere — manual settings, with version, Part, Layer)
Track: <name>, device <n>, role: <role>
Section / control | Desired value (units) | Status
```

Status is one of: *verified* (written and read back), *in the preset file, loaded* (set by
`live_preset_load`; not read back knob by knob), *in the preset file, not loaded* (give the
path), *not applied — set manually*. Values in real units (Hz, ms, dB, cents, %, note
divisions), never a guessed 0-1 number; modulation as source -> destination, base, end points,
rate, sync and retrigger. Automation is recorded with `live_automation_record`, not listed. If
nothing is left: "No manual settings required." Never call a sound finished while manual
steps remain.

## 18. Transitions that land

User: *"the drop doesn't hit"*, *"the sections feel disconnected"*, *"add a transition"*.
Adapted from hellyee's arrangement-transitions skill (MIT, `THIRD_PARTY_NOTICES.md`); the
build-up toolbox is `PRODUCTION.md` §3-4. A transition is energy management: what leaves, what
enters, what bridges the boundary.

| Device | Recipe in LiveBridge | Effect |
|---|---|---|
| Riser | a riser preset or noise sweep held 4-8 bars into the boundary (`SERUM.md` noise riser) | rising tension |
| Filter sweep | `live_automation_record(parameter="Frequency", device="Auto Filter", track=..., shape="ramp_up", start=..., end=...)` on the lead or pad itself | the part becomes the riser |
| Roll | hat/shaker 16ths -> 32nds over the last 2 bars, velocity 40 -> 127 (`live_clip_write_pattern`) | acceleration |
| Kick roll | last 2 bars: 8ths -> 16ths, velocity rising | heartbeat before the drop |
| **Gasp** | silence on **every** track for the last 1-2 beats — including arps and held pads (delete or shorten those clips) | the silence that makes the hit land |
| Impact | crash + kick (velocity 115+) exactly on the downbeat | the exclamation mark |
| Sub drop | a 1-bar low note with pitch automation 0 -> -24 semitones over ~3.5 beats | the floor falls away |
| Melodic pre-cut | pads/leads stop 1-2 bars before the drums do | a staged exit reads as intent |
| Downlifter | a reversed riser or falling sweep after the boundary | settling into the new section |

**The rule you can measure: a drop only hits if the moment before it is smaller.** Loop the
last bars of the build, `live_engine_listen(seconds=4, save_as="build")`, then the first bars
of the drop and `live_engine_compare(before="build")`: the drop must be louder (LUFS and
peak), and the gasp should dip clearly. If the build is as loud as the drop, take energy out
of the build (fewer layers, a filter, no kick in its last bar) instead of piling FX onto the
drop. A gasp that barely dips: some layer kept playing — find it (`live_engine_listen(
track="all")`) and cut it there too.

| Genre | Transitions |
|---|---|
| Techno | no drama: layers enter and leave one at a time over 8-16 bars, filters open slowly, the kick almost never stops (drop it for exactly one bar instead of a gasp). Keep the full riser kit for one moment in the track, if any |
| House | a filter sweep on the chord loop, an open-hat lift in the last bar, a 1-beat drum cut before the new 8-bar phrase; a short snare fill (4 hits, beats 3-4). Every 8 or 16 bars |
| Trance / uplifting | the full kit, staged: melodic pre-cut (2 bars) -> 8-bar riser + kick roll + shaker crescendo -> gasp (1-2 beats) -> impact + sub drop -> everything at once. Into the breakdown: all drums out on a downbeat, pad and lead ring on. Drop 2 adds a layer drop 1 lacked |
| Melodic techno | trance staging, stretched and subtler: 16-bar filter climbs, no impacts, the arp never stops (gap it only at *the* drop) |
| Drum & bass | switch-ups: a new drum pattern every 16 bars, the last 2 bars a fill (displaced snares, doubled kicks); bass swells into boundaries; drops announced by a bar of drums only or a stab |
| Trap | hat rolls are the transition (16ths into 32nds/triplets in the last bar), a pitch-rising 808 or riser, a full silent bar before the drop, the 808 gliding into its first note |
| Ambient | no fills or impacts: crossfades — one pad's volume/filter falls over 8-16 bars while the next rises; boundaries should be inaudible as events |

Section lengths: techno phrases of 16/32 bars (intro 16-32 drums only, DJ-friendly), house
8/16, trance 8/16/32 (breakdown 16-32 is the emotional core), DnB 16, trap 4/8.

## 19. Sound check before you build

Adapted from hellyee's sound-check skill (MIT). Everything in Live fails loudly except silence:
a track that makes no sound — an empty drum pad, a filter shut, a fired session clip that
overrides the arrangement — writes, places and renders without an error. Check before an
arrangement is built on the parts, after loading any drum rack, and whenever the user says
something is missing, thin or inaudible.

1. Loop a section where every part plays, `live_transport_play`, then `live_sound_check()`.
   Per track: `signal` (ok / quiet / silent), the guessed `role`, `health` (a patch strangled by
   its filter: one band holding 85%+ of the energy; nothing above 2 kHz; no transients on a
   pluck or drum) and every filter cutoff against the role's working floor (bass and pads
   ~1.5 kHz, keys, leads and vocals ~3 kHz).
2. **A silent track: never raise the fader first.** Work down `problems`: no instrument? a
   drum note on an empty pad (`live_drumrack_overview` — factory kits often fill only 36-51)?
   muted, or another track soloed? does the section contain its clip? 'Back to Arrangement'
   (`live_transport_set(back_to_arranger=false)`)? a closed filter? Only a part that is
   genuinely quiet is a level problem: raise velocities or the device output, then the fader.
3. Read `order` as a mix: for four-on-the-floor, kick on top, bass under it, then lead,
   percussion, pads. Upside down = a balance problem before 176 bars are written over it.
4. A strangled preset: open the **lowest** low-pass in the chain (it wins; opening another
   filter does nothing), or pick another preset. These checks catch *broken*, not *boring* —
   say the numbers, let the user judge the tone.
5. After editing a session clip, the arrangement copies are not updated: re-place them
   (`live_arrangement_duplicate_clip`) and check again. Report what you measured ("hats now
   -24 LUFS, high band 4%"), not the intention.

## 20. Balance and master by measurement

User: *"balance the mix"*, *"kick should sit on top"*, *"master it for Spotify"*, *"make a club
version"*, *"mix and master this"*. The tools place their own devices (a LiveBridge Channel
at the end of each sounding track, an Engine on the master, LiveBridge Master before it) —
don't ask the user to drag anything in; Max for Live is needed (Max 9 for Channel/Master).
Mixing first, mastering second; most "weak" masters are mix problems.

**All in one**: loop the fullest section, start playback, `live_mix_auto(genre="techno",
master="streaming")` — strips, kick/bass phase and unmasking, balance and master in one call.
The steps below are the same work one tool at a time (to stop between steps, or to redo one).
Long runs answer `{job, status: "running"}` past ~40 s and go on in Live: poll
`live_mix_job(job=..., wait_seconds=30)` for the report; don't start a second run meanwhile.


1. **Sound check** (§19) — nothing silent, nothing strangled.
1b. **Channel strips** (`live_mix_auto(genre=..., balance=false, low_end=false)` for this step
   only): places the Channels, rests each strip, listens to the raw part, sets high-pass, mud/harshness cuts, air,
   compression to the role's crest factor and bass mono, listens again, then balances (step 2)
   and, with `master="streaming"`, masters (step 4) in one call. Report the `reasons`; on the
   user's own mix run `apply=false` first. A move they dislike: one
   `live_device_set_parameter(device="LiveBridge Channel", parameter=..., value=...)`.
1b2. **Clashes** (kick and bass, vocal and pad on the same band): `live_mix_unmask(track="Bass",
   against="Kick")` measures both, picks the band where both are strong, and puts a LiveBridge
   Unmask (side-chained dynamic EQ) on the bass: it cuts that band only while the kick plays
   in it (`depth_db` 3-6 is transparent). Dominant part first; one call per pair.
1c. **Phase** (kick and bass fighting, a thin low end): loop one bar where both play,
   `live_mix_phase(track="Bass")` puts LiveBridge Phase on the bass, tries polarity and an
   all-pass at 40-220 Hz, and keeps the setting that leaves the most low end on the master —
   only when it beats the untouched sound by more than the measuring noise. Rotate the
   submissive part (bass, a layer), never the kick.
2. **Balance**: loop the fullest section, `live_mix_balance(genre="techno")` (or without a
   genre: kick on top, sub -1, bass -3, snare -3, vocal -2, lead -6, keys -8, percussion -9,
   pads -10, hats -11, fx -13 LU). It measures every part pre-fader, sets the faders in dB (one
   undo step per pass), checks again, and trims everything together for 6 dB of master
   headroom. Unclear roles: pass `roles={"Audio 3": "hats"}`. Start with `apply=false` on the
   user's own mix and show the plan. Then listen together — targets are a starting point.
3. **Spectral fixes where they belong**: `bands` in the result says per band whether the
   excess is shared by 3+ tracks (one master/bus EQ move keeps phase coherent) or owned by 1-2
   tracks (cut it there, leave the master alone). For a genre's typical balance and loudness,
   `live_audio_analyze(file_path=<bounce>, genre="techno")` — rules of thumb; a reference
   track (§8) beats them.
4. **Master**: loop the loudest section, `live_master_auto(destination="streaming")` (or
   `"club"`, `"apple"`, `"soundcloud"`, `"demo"`, `lufs=-9`). It reuses the chain or builds EQ
   Eight -> Glue Compressor -> Limiter before the master's Engine (never a second limiter after
   a plug-in one), sets the ceiling, trims the faders if the mix is too hot, then raises the
   limiter gain step by step until the integrated loudness is on target. It stops early when the
   crest factor collapses (the punch goes) — then fix the mix instead (kick/bass balance, fewer
   layers in the drop). With `loud_bar=` and `quiet_bar=` it also checks that the drop and the
   breakdown stay apart and backs off if limiting squashed them together.
5. **Glue threshold** is the user's step (its gain-reduction meter is not readable): 1-3 dB on
   the loudest section. One master can't serve streaming *and* club: run it once per
   destination (a -8 LUFS club master is turned down on Spotify with its transients crushed).
   Export is the user's (24-bit WAV; dither only for 16-bit).
