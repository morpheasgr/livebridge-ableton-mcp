# Changelog

LiveBridge's version is the Remote Script's (`VERSION` in `remote_script/LiveBridge/LiveBridge.py`);
the MCP server package and the installer have their own version numbers. `live_status` shows both.

## Unreleased

- **Production knowledge for Claude**, next to the skill and served as MCP resources:
  `GENRES.md` (`livebridge://genres`: drum patterns, bass lines, chords and sounds for techno,
  hardgroove, house, UK and speed garage, trance, bass music, synthwave, acid, ambient and
  hip-hop/trap/DnB grooves), `SERUM.md` (`livebridge://serum`: Serum 2 parameter cheat sheet,
  recipes, API limits) and `WORKFLOWS.md` (`livebridge://workflows`: extending an arrangement,
  co-writing, MIDI cleanup, chords, mix doctor, side-chain, reference match, pre-master audit,
  vocal chain). Adapted in part from the MIT-licensed ableton-mcp-pro and glincker/ableton-skills
  (`THIRD_PARTY_NOTICES.md`).
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
