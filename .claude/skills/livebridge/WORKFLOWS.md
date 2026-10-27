# Workflows: working *with* the producer

Companion to `SKILL.md` (tools), `PRODUCTION.md` (how a track works), `GENRES.md` (material per
genre) and `SERUM.md` (Serum 2). This file is step-by-step procedures for common requests:
extending an arrangement, co-writing, grooves, MIDI cleanup, chords, mix diagnosis, side-chain,
reference matching, pre-master checks, vocal chains, tempo and session snapshots. Several are
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
6. **Develop what they keep**: variations for later sections (`live_clip_duplicate_notes` with
   `transpose_steps=`, `live_clip_transform_notes(invert=..., retrograde=true)`, rhythmic
   displacement with `shift=`), call and response with another part, a reduced version for the
   intro. Ask before deleting the variants they did not pick.

Leave the **lead melody** to the user unless they ask for one; offer a motif to react to instead.

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
     harshness, missing sub and wide low end.
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
6. **Re-measure**: bounce again, `live_audio_analyze`, compare with the first measurement.

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
