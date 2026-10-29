# Changelog

LiveBridge's version is the Remote Script's (`VERSION` in `remote_script/LiveBridge/LiveBridge.py`);
the MCP server package and the installer have their own version numbers. `live_status` shows both.

## 0.2.0 (MCP server) / 1.1.0 (Remote Script)

- LiveBridge Phase: polarity, delay and all-pass phase rotation in gen~ (any Max). New tool `live_mix_phase` (220 tools): tries polarity and all-pass frequencies on a part while the song loops and keeps the setting that leaves the most low end on the master — Fuser-style phase analysis by measurement.
- LiveBridge Channel (Max 9): the Engine plus a channel strip from Ableton's DSP (high-pass, Channel EQ, Stereo Compressor, Saturator, Utility), every control a Live parameter, neutral at rest. New tool `live_mix_auto` (219 tools): rests the strips, listens to the raw parts, sets each strip for its role with a reason per move (`mix.channel_plan`), listens again, then balances and masters if asked.
- Engine 1.7.0: `live_engine_modulate(mode="offset")` modulates through `live.modulate~` — around the parameter's own value like Live's LFO, the knob stays the user's, nothing to restore. The default stays `"replace"` (`live.remote~`).
- LiveBridge Master (Max 9): Ableton's Channel EQ, Stereo Compressor, Limiter and Utility DSP (`abl.device.*`) in one Max audio effect, every control a Live parameter; `live_master_auto(chain="livebridge")` uses it instead of EQ Eight -> Glue -> Limiter. The installer puts it in the User Library.
- Engine 1.6.2: the fast automation pass no longer calls `begin_gesture`/`end_gesture`, which Max's LiveAPI does not have (each call was an error in the Max window). `docs/LIVE_M4L_LOM_12.4.15.md` and `LIVE_API_VERIFIED.md` §21: what Max for Live reaches compared with the Remote Script.
- Max for Live API dump: Engine 1.6.1 answers `lom_dump` (members merged across every object of a type: LiveAPI lists only what each object has) (its `context.js` walks Live's object model through LiveAPI `info`); `tests/dump_m4l_lom.py` writes `docs/LIVE_M4L_LOM_<version>.md` with, per type, what the Python API has that Max cannot reach and the reverse.
- Per-clip scale (Live 12.4.15): `live_clip_set(scale_name=, root_note=, scale_mode=)`, `scale` in `live_clip_get` and the clip summaries, `key="clip"` in `live_theory_analyze` / `live_theory_melody`, and a `clip_scale` hint when a clip's own scale disagrees with its notes. Older Live answers `unsupported`.
- The open set's name: `live_set_snapshot` reports `set_name`/`set_path` (`Song.name`/`Song.file_path`), or `unsaved: true` for a set never saved.
- Session memory files notes under the open set's name and finds the project by the set's file on recall (`matched_by: "set"`); notes written before the set was saved say `unsaved_set` and move over with `live_memory_update(project=..., whole_project=true)`.
- Version bump so `live_status` shows whether the new code runs; `live_status` also reports the MCP server's `pid` and `started` time.

## Unreleased

- **Fix: the touch log no longer counts restores** (Host 0.3.2): Live's undo or a reopened set
  makes a plug-in re-announce all its values at once; such bursts (8+ parameters in 50 ms)
  are listed as `restores`, not touches. The host reads a baseline after every load and
  restore so touches have a `before`; `touched="reset"` starts a clean round.
- **What you touch in a hosted plug-in's window** (`live_plugin_host_parameters(touched=...)`,
  Host 0.3.1): the host logs the changes `vst~` reports from the plug-in's own window, in
  order with before/after values, leaving out LiveBridge's own writes and preset loads — no
  snapshot needed. Bank dials now follow window edits.
- **Fix: `live_plugin_host_expose(remove_host=false)` keeps the host** — an instrument's
  native rack goes on a new "<plug-in> (native)" track (one instrument per MIDI track), an
  effect's next to the host; `host_removed` is checked afterwards, and parameters a plug-in
  adds to the rack on its own are listed as `extra`.
- **Scout in the host, play natively** (`live_plugin_host_save`, `live_plugin_host_expose`,
  `live_plugin_host_parameters(snapshot=..., changed_since=...)`; Host 0.3.0): see what the user
  moves in a plug-in's window across all its parameters, save the hosted patch as a
  `.vstpreset` (`vst~ write`), and hand over to a native rack exposing the chosen ≤128
  parameters with that preset. Host replies now resolve as soon as `vst~` answers (reading
  ~500 parameters takes seconds).
- **Presets know their plug-in** (`live_plugin_host_preset`, Host 0.2.5): a `.vstpreset`'s VST3
  class ID is looked up in the installed plug-ins' `moduleinfo.json` and in a table learned
  from loads that worked. A preset for a different plug-in than the host holds is refused
  before anything changes, and an unnamed host takes the preset's plug-in name.
- **LiveBridge Host names itself after its plug-in** (Host 0.2.3): the device's title becomes
  "Serum 2", and a reopened set shows that name instead of "plug-in".
- **LiveBridge Host: an "Open plug-in" button** on the device (and on bank devices) opens the
  hosted plug-in's own window.
- **Fix: a reopened set's LiveBridge Host knows its plug-in again** (Host 0.2.2): `vst~`
  restored the plug-in and its sound, but the host script started blank ("ready — no
  plug-in", tools refused). It now adopts the restored plug-in at startup and before any
  request, without reloading it.
- **Older Serum presets load** (`live_preset_load`, `live_plugin_host_preset`): a preset saved by
  an older Serum 2 (factory and commercial packs) is packed with a newer template instead of
  being refused; settings it lacks keep the template's defaults and the result says
  `upgraded_from`. Presets newer than every template are still refused.
- **Fix: no more "Node script not ready" lines in the Max window** (Engine 1.5.2, MIDI 1.0.1,
  Host 0.2.1): each device holds its messages to Node for Max behind a closed gate until the
  script says it is ready.
- **Fix: `live_engine_unmodulate` restores the parameter's value** (Engine 1.5.1). It let go of
  the parameter in the same instant it sent the saved value, so the knob kept the LFO's last
  position; it now lets go a few audio vectors later and writes the saved value once more.
- **LiveBridge Engine, a Max for Live device for real-time listening** (`live_engine_status`,
  `live_engine_listen`; needs Max for Live): drop it on the master and Claude reads what is
  playing right now — momentary/short-term LUFS (BS.1770 K-weighting in gen~), peaks, crest
  factor, the 8 spectral bands of `live_audio_analyze`, stereo correlation, width and the width
  below 120 Hz, with the same mix flags — without bouncing; the last 60 s stay available
  (`history=true`). The device talks to the MCP server over a local, token-protected TCP port
  it registers in `~/.livebridge/engine/`; the Remote Script is unchanged. Built from source by
  `max_for_live/build.py`; the installer copies it into the User Library
  (`--no-engine` skips it). See `docs/ENGINE.md`.
- **Engine on tracks: A/B, captures and live masking** (`live_engine_compare`,
  `live_engine_capture`, `live_engine_masking`; device 1.1.0): put the Engine at the end of
  tracks too. `live_engine_listen(save_as=...)` + `live_engine_compare` is the A/B check of a
  change while the song loops; `live_engine_capture` records several devices to WAV at the same
  moment (`~/.livebridge/captures`, kept a week); `live_engine_masking` captures every track
  device and runs the masking analysis at fader level, skipping muted tracks;
  `live_engine_listen(track="all")` reads every device over one window. The device now also
  reports which track it is on when Node for Max starts after the device.
- **Live parameter modulation** (`live_engine_modulate`, `live_engine_unmodulate`; device
  1.2.0): tempo-synced LFOs (sine, triangle, saw, ramp down, square, random, smooth random) and
  step sequences on any device or mixer parameter, locked to Live's song position ("1 bar",
  "1/8d", "1/16t") or free ("0.5 Hz"), through the device's 8 `live.remote~` slots: no
  automation, no undo steps, nothing saved with the set; stopping restores the value.
- **Follow actions** (`live_engine_follow`, `live_engine_unfollow`; device 1.3.0): Live's
  Python API has no follow actions, so the Engine plays them — every N bars of Live's song
  position a rule picks the next clip of a track or the next scene (next, previous, first,
  last, any, other, again, stop, jump:N with weights, a chance and a seed, optionally among
  chosen slots) and fires it just before the boundary so launch quantization lands it on the
  grid.
- **Faster arrangement automation recording** (`live_automation_record(speed="fast" | 2..20)`):
  automation is stored in beats, so the pass plays at a raised tempo and the tempo is restored
  after. With a LiveBridge Engine in the set (device 1.4.0) the Engine writes the curve 50
  times a second with begin/end gesture while the Remote Script runs the pass (~600 BPM: about
  5x faster at 120 BPM at today's ~0.2 beat resolution); without one the Remote Script writes
  at ~300 BPM (0.5 beat, still exact for ramps). Refused for the tempo itself and when the song
  has tempo automation. `automation.record` gains `tempo` and `writer="external"`.
- **Serum 2 preset files** (`live_preset_find`, `live_preset_inspect`, `live_preset_read`,
  `live_preset_search`, `live_preset_edit`, `live_preset_convert`, `live_preset_compare`,
  `live_preset_load`): the whole Serum 2 patch beyond the 128 knobs Live can reach — read and
  search complete `.SerumPreset` state, edit it with guarded JSON Patch operations (copy whole
  wavetable oscillators or FX racks from another preset), unpack/repack it as JSON, compare two
  presets, and load one into Live as Serum 2 on a new track or replacing a track's instrument
  (packed into a `.vstpreset` with a template the user saved from their own Serum 2, found in
  the User Library; written to `User Library/LiveBridge Presets/Serum 2`). Companion guides
  `SERUM_PRESETS.md` (`livebridge://serum-presets`) and `OMNISPHERE.md`
  (`livebridge://omnisphere`). The codec, preset tree, VST3 container, packing and both guides
  are ported from kasperikaivola's fork of ableton-mcp-pro (MIT, commit 6ac32c8); new `serum`
  extra (cbor2, zstandard), installed by default.
- **Plug-in host, every VST3 parameter** (`live_plugin_host_load`, `live_plugin_host_parameters`,
  `live_plugin_host_set`; experimental, needs Max for Live): two new Max for Live devices
  (LiveBridge Host Effect / Instrument, built by `max_for_live/build.py`, installed with the
  others) load a VST3 inside Max's `vst~`, past Live's 128-parameter Configure limit; values by
  number or display text. No automation lanes for hosted parameters. Idea from Producer Pal's
  plug-in-host spike (GPL-3, no code taken). `live_plugin_host_preset` loads a `.vstpreset` or a Serum 2
  design (packed first) into the hosted plug-in, so a file-designed patch is fine-tuned on the
  same instance. `live_plugin_host_bank` makes chosen hosted
  parameters automatable: a per-plug-in host with Live dials named after them (written to the
  User Library, swapped in with every value copied) — the approach of xxnoodl's
  ableton_device_vst_wrapper (MIT).
- **Mixing and mastering by measurement** (`live_sound_check`, `live_mix_balance`,
  `live_master_auto`; Engines on the tracks and the master): a sound check for silent parts,
  filter-strangled patches and cutoffs below a role's working floor; faders set to role levels
  (or a genre's) in real dB over measured passes, trimmed for master headroom, with each
  spectral problem placed on the master (shared by 3+ tracks) or on the tracks that own it; an
  EQ Eight -> Glue -> Limiter chain driven to a destination loudness (streaming -14, Apple -16,
  club -8 ...) with crest-factor and drop-vs-breakdown guardrails. Roles come from track names
  or the sound. `live_audio_analyze(genre=...)` compares a bounce with one of 18 genre
  balances. Ideas and data from HighVoltSound's ableton-auto-mix-mcp and hellyee (both MIT).
- **Melody check** (`live_theory_melody`): the craft checklist for a lead or hook — off-grid
  starts, note lengths, rests, velocity arc, one climax, recovered leaps, resolved tension
  notes, a developed motif, question/answer — and, with `mood=`, contour, velocity and density
  against the mood. New guides: melody craft and mood-to-notes (`PRODUCTION.md` §11-12),
  transitions per genre, the sound check and balancing/mastering (`WORKFLOWS.md` §18-20), the
  silent-failure list in `SKILL.md`; adapted from hellyee (MIT).
- **More guidance from the same fork** (MIT): `SERUM.md` §6 lists every Serum 2 control
  (oscillator modes incl. granular and spectral, routing, matrix, FX racks, performance) with a
  new-patch recipe and fixes for a bland patch; `WORKFLOWS.md` §16 adds one section to an
  arrangement (drop, breakdown, interlude, buildup, intro, outro — finding "the last drop",
  overlap rules, sweeps recorded as automation) and §17 designs a synth sound (Serum 2 or
  Omnisphere, what each request allows, the "manual settings" hand-off).
- **Technical problems and layer alignment** (`live_audio_problems`, `live_audio_align`):
  clipping, DC offset, left/right polarity, mains hum (50/60 Hz with harmonics, measured in the
  quiet passages), noise floor and persistent resonances (each with an EQ Eight bell: frequency,
  gain, Q, and the nearest note so a held note is not mistaken for ringing); the offset in
  ms/beats and polarity of one layer against another (kick vs sub), with the level the aligned
  sum gains, and `apply=true` to flip the polarity with a Utility. New skill workflows: technical
  check and alignment, parallel compression, a mastering chain, creative send effects. Ideas from
  the feature list of fadelabs/phantom (AGPL-3.0); no code was used.
- **Arrangement automation by default** (`automation=true` on `live_arrangement_from_scenes`,
  `live_arrangement_duplicate_clip`, `live_arrangement_copy_range`): Live's API drops clip
  envelopes from arrangement copies (device parameters always, mixer ones on tracks with an
  instrument or audio effect — probe in `integration_check.py --scenario envelopes`, results in
  LIVE_API_VERIFIED §19.5) and cannot read lanes. The lost envelopes now become the tracks'
  automation lanes (one curve per parameter over the arrangement, the parameter's value between
  sections), and a copied range takes its lanes along (played and sampled, then recorded at
  the destination) — fast background passes in the MCP server, reported by
  `live_automation_record(action="status")`, cancelled with `action="stop"`.
  `automation.record` records several parameters in one pass (`lanes=`) and samples
  (`action="sample"`); the Engine (1.5.0) writes and reads lanes 50 times a second. Fixed:
  recording passes now also set Live's start marker, where playback actually starts (they
  played from the old start marker before reaching the range).
- **LiveBridge MIDI, a Max for Live MIDI effect** (`live_midi_played`, `live_midi_play`,
  `live_midi_stop`): passes MIDI through and remembers the last 10 minutes of notes and
  controllers with Live's song position — `live_midi_played` returns what you just played as
  clip-ready notes (optionally quantized) — and plays phrases into the instrument after it in
  time with the song, without writing a clip. The installer copies it to User Library >
  Presets > MIDI Effects > Max MIDI Effect; the Engine and MIDI tools tell Claude to load the
  devices themselves through the browser when they are missing.
- **Production knowledge for Claude**, next to the skill and served as MCP resources:
  `GENRES.md` (`livebridge://genres`: drum patterns, bass lines, chords and sounds for techno,
  hardgroove, house, UK and speed garage, trance, bass music, synthwave, acid, ambient and
  hip-hop/trap/DnB grooves), `SERUM.md` (`livebridge://serum`: Serum 2 parameter cheat sheet,
  recipes, API limits) and `WORKFLOWS.md` (`livebridge://workflows`: extending an arrangement,
  co-writing, MIDI cleanup, chords, mix doctor, side-chain, reference match, pre-master audit,
  vocal chain). Adapted in part from the MIT-licensed ableton-mcp-pro and glincker/ableton-skills
  (`THIRD_PARTY_NOTICES.md`).
- **Sample search with Live's browser tags** (`live_sample_search`, `live_sample_tags`,
  `live_sample_duplicates`, `live_sample_index`, `live_plugin_search`): everything Live's browser lists under Library > Samples — Core
  Library, packs, User Library and every folder added to Places, not only what Live's API
  returns — searchable by words and tags ("Drums|Kick", "Character|Punchy"), with absolute
  paths ready for `live_drumrack_set_pad` / `live_sample_import`; also most-used first
  (Live's use counter), "sounds like this one" (`similar_to`, Live's own audio features),
  identical-audio duplicates and a `source` filter (Core Library, packs, User Library, your
  folders), Live's browser categories (`category=`), several searches per call
  (`searches=`), and every installed plug-in Live knows (`live_plugin_search`). Paths come
  from the folders Live's database keeps up to the drive. Built in the MCP server from
  a temporary copy of Live's browser database (database + -wal; the original is never opened),
  refreshed when it changes. See `docs/SAMPLE_SEARCH.md`.
- `tests/test_skill_docs.py` checks that every tool and argument the skill files name exists.
- **12 new tools (180 in total)**, written for LiveBridge from a list of features other Ableton
  MCP servers offer (no code taken from them):
  - *Sound library*: `live_sound_save` / `_load` / `_list` / `_delete` keep a device's sound
    (any device, Serum 2 racks included) in `~/.livebridge/sounds` and put it back later, on
    another instance, or after `live_plugin_expose` re-exposed a plug-in.
  - *Generative*: `live_clip_write_euclid` (Euclidean rhythms and polymeters) and
    `live_clip_note_chance` (per-note play probability and velocity variation).
  - *Mix analysis*: `live_audio_masking` (which parts fight over which frequency bands, with
    advice) and `live_audio_compare` (before/after deltas with better/worse verdicts).
  - *Session memory*: `live_memory_note` / `_recall` / `_update` / `_forget` keep decisions, open
    ideas and the user's preferences across sessions in `~/.livebridge/memory.json`, matched to
    the open set by its track names and tempo.
  `LIVEBRIDGE_LIBRARY` moves the library folder. The new modules are in the `production`
  toolset.

## 1.0.0 — 2026-09-23

The first public release: Claude controls Ableton Live 12 through MCP.

- **Remote Script** for Live 12 (standard-library Python 3.11): a TCP JSON-lines server that
  runs 200 bridge commands on Live's main thread, each change one undo step.
- **MCP server** `livebridge-mcp` with 168 tools in 22 areas: transport, tracks, mixer, routing,
  recording and resampling, clips and notes (patterns, chords, arps, transforms), arrangement,
  locators, scenes, devices, racks, Drum Racks, Simpler, the browser, plug-ins, samples, Splice
  import, automation (clip envelopes and recorded arrangement automation), view, and the Live
  Object Model (describe/get/set/call, batches, Python inside Live).
- **Serum 2 and other VST3 plug-ins** fully controllable without *Configure*: generated rack
  presets expose up to 128 parameters (`live_plugin_expose`, `live_plugin_param_map`).
- **Analysis:** `live_theory_analyze` (key, chords, Roman numerals, clashes between parts) and
  `live_audio_analyze` (LUFS, true peak, spectrum, stereo, tempo, key, energy; comparison with a
  reference track), plus a production guide for Claude (`livebridge://production`).
- **Two computers:** LAN mode with a token, UDP discovery (`live_discover`), `live_connect`, and
  automatic file upload to the Live machine.
- **Splice:** the installer registers Splice's official MCP server; `live_splice_*` imports the
  downloads into Live.
- **Installer** for macOS and Windows: Remote Script, config with a random token, MCP server,
  Claude Code and Claude Desktop registration, and the LiveBridge skill.
- **Documentation and examples:** eight example walkthroughs run against Live 12.4.5, a library
  of 100+ prompts, scripting examples, and a website.
- **Tests:** 1,500+ unit tests against a simulated Live on macOS, Windows and Linux, plus an
  end-to-end check for a real Live.
