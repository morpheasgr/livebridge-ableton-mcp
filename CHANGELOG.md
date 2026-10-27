# Changelog

LiveBridge's version is the Remote Script's (`VERSION` in `remote_script/LiveBridge/LiveBridge.py`);
the MCP server package and the installer have their own version numbers. `live_status` shows both.

## Unreleased

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
