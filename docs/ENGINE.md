# LiveBridge Engine (Max for Live)

LiveBridge talks to Live through a Python Remote Script, and Live's Python API cannot hear audio.
**LiveBridge Engine** is an optional Max for Live audio effect that can: put it on the master
track (and at the end of any track you want Claude to hear on its own) and Claude reads
loudness, peaks, spectral balance and stereo image *while the song plays*, compares before and
after a change, records what it hears and finds masking between tracks — without bouncing —
moves any parameter with tempo-synced LFOs and step sequences without writing
automation, and plays follow actions on tracks and scenes.

| Tool | What it does |
|---|---|
| `live_engine_status` | Which Engine devices run, where (master or a track) and whether audio frames arrive |
| `live_engine_listen` | Listen for `seconds` (or read the last `seconds` with `history=true`): momentary / short-term / window LUFS, peak, RMS, crest factor, 8-band balance, stereo correlation, width, width below 120 Hz, mix flags, optional per-second `timeline`; `track="all"` reads every device over one window; `save_as` keeps the reading for an A/B |
| `live_engine_compare` | A/B: a saved reading against a second one or what plays now on the same device — deltas, better/worse, flags resolved or new |
| `live_engine_capture` | Record several devices to WAV at the same moment (`~/.livebridge/captures`, kept a week) for `live_audio_analyze`, `live_audio_compare`, `live_sample_import` |
| `live_engine_masking` | Capture every track device at once and run the masking analysis of `live_audio_masking` at fader level (muted / -inf tracks left out) |
| `live_engine_modulate` | LFO (sine, triangle, saw, ramp down, square, random, smooth random) or step sequence on any device or mixer parameter, synced to the song ("1 bar", "1/8d", "1/16t") or free ("0.5 Hz"), with depth/center or low/high and phase |
| `live_engine_unmodulate` | Stop one modulation (or `all=true`); the parameter goes back to its value |
| `live_engine_follow` | Follow actions: every N bars pick the next clip of a track or the next scene — next, previous, first, last, any, other, again, stop, jump:N with weights, a chance, a seed, optionally only some slots |
| `live_engine_unfollow` | Stop a track's or the scenes' rule (or `all=true`); what plays keeps playing |
| `live_sound_check` | Every track: silent / quiet / ok, a patch strangled by its filter, cutoffs below the role's floor, the chain's usual silent causes (works without the device too, with less) |
| `live_mix_balance` | Faders to role (or genre) levels by measurement, settled over passes, trimmed for master headroom; each band problem placed on the master or on the 1-2 tracks that own it |
| `live_master_auto` | EQ Eight -> Glue -> Limiter before the master's Engine, limiter gain raised step by step to a loudness target; stops when the crest factor collapses or the drop/breakdown contrast shrinks |

Everything else in LiveBridge works without the device; without it these tools say how to
add it.

**On tracks**, put the Engine **last** in the chain: it hears the track after its effects but
before the fader, pan and mute (`live_engine_masking` applies the fader level itself).

## Install

Needs Max for Live (Live Suite, or Standard with the add-on). The installer copies the device to
`<User Library>/Presets/Audio Effects/Max Audio Effect/LiveBridge Engine/` (`--no-engine` skips
it; the uninstaller removes it, `--keep-engine` keeps it). In Live's browser: **User Library >
Presets > Audio Effects > Max Audio Effect > LiveBridge Engine** — drag it onto the master
track. It passes audio through untouched; its status line reads "ready", then "master" (or
the track name) once it knows where it sits.

Claude can add it too: `live_browser_load(query="LiveBridge Engine", category="audio_effect",
track="master")` (or any track name) loads it at the end of the chain, like any Max for Live
device in the browser; the tools' "not found" answers say so.

Manual install: copy the folder `max_for_live/LiveBridge Engine/` (all four files: the `.amxd`,
`engine.js`, `context.js`, `lb_engine.gendsp`) anywhere Live's browser can see, keeping them
together.

## How it works

```
 audio ─► gen~ lb_engine ─► 18 meter values ─► every 100 ms ─► node.script engine.js
          (K-weighting,       (mean squares,     (snapshot~)     keeps the last 60 s,
           8 band-passes,      peaks, mid/side)                  TCP 127.0.0.1:<random>
           mid/side, 120 Hz)                                            ▲
                                                                        │ JSON lines + token
 live.thisdevice ─► js context.js (LiveAPI: master or which track) ─►  │
                                                                        │
                         MCP server: engine.py  ◄───────────────────────┘
                         reads ~/.livebridge/engine/<instance>.json (port + token)
```

- **DSP** (`lb_engine.gendsp`, GenExpr generated by `max_for_live/build.py`): BS.1770
  K-weighting (coefficients computed for the running sample rate), the 8 bands of
  `live_audio_analyze` (sub 20-60, bass 60-120, low-mid 120-250, mud 250-500, mid 500-2k,
  presence 2-5k, high 5-10k, air 10-20k Hz) as band-passes, mid/side, L·R and a 120 Hz low-pass
  for low-end width. Mean squares use a 0.1 s one-pole (0.4 s for the K-weighted momentary
  loudness), peaks decay over 0.3 s.
- **Node for Max** (`engine.js`): buffers frames (timestamp + 18 values), listens on
  `127.0.0.1` on a random port and writes a registry file `~/.livebridge/engine/<instance>.json`
  (`LIVEBRIDGE_ENGINE_DIR` moves it; mode 0600) with the port and a random 48-hex token. The
  file is removed when the device is deleted or Live quits; stale files are removed by the MCP
  server when nothing answers.
- **Protocol**: one JSON object per line, `{"id", "token", "cmd", "args"}` ->
  `{"id", "ok", "result" | "error": {"type", "message"}}`. Commands: `hello` (role, track,
  version, frames buffered), `frames` (`seconds` ≤ 60, `wait`: true = collect from now on,
  false = the last `seconds`), `modulate` (`path`: Max LOM path, `shape`, `beats` or `hz`,
  `low`/`high` shares of the range, `phase`, `steps`, `seed`), `unmodulate` (`path` or `all`),
  `follow` (`target`: `live_set tracks N` or `live_set` for scenes, `every_beats`,
  `lead_beats`, `actions`, `chance`, `slots`, `seed`), `unfollow` (`target` or `all`)
  and `capture` (`seconds` ≤ 60, `name`: a plain `.wav` file name
  in the capture folder, `at`: start time in epoch ms, at most 5 s ahead — the MCP server
  gives every device the same `at`). Captures are written by the patch's `sfrecord~`
  (`open <path>`, `1`, `0`); `LIVEBRIDGE_CAPTURE_DIR` moves the folder. `lom_dump` (1.6.0+, a
  dev command): `context.js` walks Live's object model as Max sees it (LiveAPI `info` of every
  object type, devices once per class) and the answer is that walk plus the Max, Node and
  Engine versions; `tests/dump_m4l_lom.py` writes it to `docs/LIVE_M4L_LOM_<version>.md`.
- **Host side** (`mcp_server/livebridge_mcp/engine.py`): turns frames into readings. LUFS is
  K-weighted and averaged over the window, excluding frames below -70 LUFS (no relative gate);
  the peak is a sample peak, so values can differ from a mastering meter by a fraction of a dB.

- **Modulation**: 8 slots per device, each a `line~` into a `live.remote~`. `engine.js`
  computes the shape 50 times a second (`line~` glides 20 ms between values) from Live's song
  position, which `context.js` observes (`current_song_time`, `tempo`, `is_playing`; at most
  every 50 ms) and `engine.js` extrapolates in between; when the transport is stopped the
  shapes run free at the song tempo. `context.js` also turns the parameter's path into the id
  `live.remote~` needs (`resolve` / `resolved`), and reads min, max and the value to restore.
  `live.remote~` changes the parameter without automation or undo steps; while it holds a
  parameter, Live greys it out and ignores its automation. Unmodulating sends the saved value,
  lets go 80 ms later (`live.remote~` reads its signal once per audio vector: letting go at
  once kept the LFO's last value) and then writes the saved value through the LiveAPI as well
  — that last write is one undo step. Modulations live in the device's
  Node process: they end with the device and are not saved with the set.

- **Follow actions**: Live 12.4.5's API has no follow-action properties, so the device plays
  them. On the same 20 ms tick, each rule watches the song position; a quarter beat (at most
  half the launch quantization) before each boundary of its grid it rolls the chance, picks a
  weighted action, asks `context.js` which slots hold clips and which plays (`query` ->
  `info`) and fires the clip slot or scene (`call fire`, `call stop_all_clips`), so Live's
  launch quantization starts it exactly on the boundary. Rules start with playback, keep
  their last 16 decisions (`live_engine_status`) and are not saved with the set.

The Remote Script is not involved, so a busy Live main thread never delays the device and the
device never touches the set.

## Building

The `.amxd` and `.gendsp` are generated, not hand-edited:

```
python max_for_live/build.py          # rewrite the device files
python max_for_live/build.py --check  # exit 1 when they are out of date (CI runs this via tests)
```

`tests/test_m4l_engine.py` runs the GenExpr line by line in Python on test signals (coefficients
against the Python formulas, K-weighted loudness against pyloudnorm, band placement, stereo
readings), checks the patch wiring, and runs `engine.js` under Node with a stand-in for Max's
`max-api` end to end through the MCP tools.

## Roadmap

Arrangement automation lanes stay out of reach for drawing: Max for Live's LiveAPI is the same
Live Object Model as the Remote Script's, without track automation envelopes, so
`live_automation_record` records them by playing. The Engine makes that faster: with
`speed="fast"` the Remote Script plays the pass at a raised tempo (automation is stored in
beats; the tempo is restored after) and the Engine writes the curve 50 times a second
(`automate`: `path`, `points` [[beat, value]], `mode`, `start`, `end`; `begin_gesture` at
`start`, `setvalue` through `context.js`, `end_gesture` at `end`) — about 5x faster than real
time at the Remote Script's resolution. Not for the tempo itself or a song with tempo
automation, which would override the raised tempo.

The arranging tools build on this: `live_arrangement_from_scenes`, `live_arrangement_duplicate_clip`
and `live_arrangement_copy_range` (`automation=true` by default) record the clip envelopes Live
does not copy, and a copied range's lanes, in background passes of several parameters each
(`automation.record lanes=...`; a copy first plays the source range with `action="sample"` and
reads the values — the Engine's `sample` command, `getvalues` through `context.js`). See
`mcp_server/livebridge_mcp/arrange_automation.py`.

# LiveBridge MIDI (Max for Live)

A MIDI effect for MIDI tracks (before the instrument). It passes MIDI through unchanged, keeps
the last 10 minutes of notes and controllers that reach it — your keyboard while the track is
armed or monitored, and the track's own clips — with Live's song position, and plays notes into
the instrument after it.

| Tool | What it does |
|---|---|
| `live_midi_played` | What was played in the last `seconds` (up to 600) as `live_clip_add_notes`-ready notes, starts in beats from the bar of the first note, optional `quantize`; notes still held (`held`) and controller summaries too |
| `live_midi_play` | Play a phrase (`[{pitch, start, duration, velocity}]` in beats) into the instrument at the next bar / beat or now, `repeat` times every `loop_beats` — nothing written |
| `live_midi_stop` | Stop it; sounding notes are released |

Installed to `<User Library>/Presets/MIDI Effects/Max MIDI Effect/LiveBridge MIDI/`
(`LiveBridge MIDI.amxd`, `lbmidi.js`, `lbmidi_context.js`); Claude loads it with
`live_browser_load(query="LiveBridge MIDI", category="midi_effect", track=...)`.

- **Patch**: `midiin` -> `midiout` (pass-through); `midiin` -> `midiparse` -> `note` / `cc`
  messages to `node.script lbmidi.js`; its `play <pitch> <velocity>` -> `midiformat` ->
  `midiout`. `lbmidi_context.js` is the Engine's `context.js` under its own name (Max finds js
  files by name along its search path) and reports the track and the transport.
- **Protocol**: like the Engine's, registry entry with `"kind": "midi"`; commands `hello`,
  `played` (`seconds`), `play` (`notes`, `start`, `repeat`, `loop_beats`, `beats_per_bar`),
  `stop`. The Engine tools ignore MIDI devices and the other way round.
- **Timing**: played notes carry the song position (extrapolated from `context.js` reports);
  notes played by the device are scheduled by Node's clock at the current tempo — good for
  auditioning, a clip is tighter.

# LiveBridge Host (Max for Live, experimental)

Live's API reaches only the plug-in parameters added with **Configure** (at most 128). LiveBridge
Host loads a VST3 plug-in inside Max's own `vst~` object instead, which can list and set every
parameter (Serum 2 about 540, Diva about 280). Two devices, installed next to the others:
`LiveBridge Host Effect` (audio effect: `plugin~` -> `vst~ 2 2` -> `plugout~`) and `LiveBridge
Host Instrument` (instrument: `midiin` -> `midiparse` -> `vst~` -> `plugout~`).

**The workflow — scout in a host, play natively** (tried in Live with Massive and Serum 2):
load the plug-in in a host (or a saved `.vstpreset` into one), let the user tweak in its window
and read what they moved (`touched`), pick the ≤128 parameters that matter (moved first, then
what the patch uses, then the role's defaults), and `live_plugin_host_expose` them: the patch is
saved, the plug-in loads natively in a rack with those parameters exposed, the host goes. For
Serum 2 the preset-file tools (`SERUM_PRESETS.md`) also reach everything that is not a
parameter.

| Tool | What it does |
|---|---|
| `live_plugin_host_load` | Find the plug-in's `.vst3` (system VST3 folders, `LIVEBRIDGE_VST3_PATH`), load the host on a new MIDI track or at the end of a track, load the plug-in into it |
| `live_plugin_host_parameters` | Every parameter (the per-MIDI-CC stand-ins VST3 adds are hidden), searchable and paged, with value and display text |
| `live_plugin_host_set` | Set many parameters by name or id (`h12`), to 0..1 or display text ("800 Hz"); open/close the plug-in window |

By hand: the device's **Open plug-in** button opens the hosted plug-in's own window.
| `live_plugin_host_preset` | Load a `.vstpreset` (or a Serum 2 design, packed first with your template) into the hosted plug-in (`vst~` `read`); a before/after sample of values says whether it took; bank dials follow. The preset's VST3 class ID says which plug-in it is for (`plugin_ids.py`: the plug-ins' `moduleinfo.json`, plus a table learned from loads that worked, `plugin_ids.json`): a preset for another plug-in is refused, an unnamed host takes its name |
| `live_plugin_host_save` | The hosted plug-in's complete state (`vst~` `write`) as a `.vstpreset` in User Library > LiveBridge Presets > Plug-in Presets > <plug-in>; class ID checked and learned |
| `live_plugin_host_expose` | Hand over to a native instance: save, build the ParameterId map once (`live_plugin_param_map`), `live_plugin_expose(preset_file=…)` with the chosen ≤128 parameters, remove the host |
| `live_plugin_host_bank` | Make chosen parameters automatable: a per-plug-in host whose Live dials carry their names, swapped in on the same track with every parameter value copied |

- **Patch**: `lbhost.js` (Node for Max) sends `vst~` messages (`plug_vst3`, `subname`,
  `params`, `get`, `<index> <value>`, `open`/`wclose`) and hears every message outlet as
  `vstout <outlet> ...`; replies are matched by timing, one request at a time. With
  `valuemode 2`, `params` streams one name per message out of outlet 2, `get <n>` answers
  `<n> <value> <text>` out of outlet 3, `get -4` is the parameter count and `get -7` 1 for an
  instrument; indices are 1-based (ids `h<n>` use them). These facts come from two other
  vst~ wrappers that were tried in Live (Producer Pal's spike and xxnoodl's
  ableton_device_vst_wrapper, both reading the same vst~ reference). Registry kind
  `"host"` (`host_kind`: effect / instrument), same protocol as the Engine.
- **Touch log**: `vst~` reports a change made in the plug-in's window, unasked, as
  `<n> <value> <text>`; `lbhost.js` logs those (first/last time, before/after, how often)
  and skips echoes of its own writes (400 ms) and everything during a plug-in or preset load.
  A burst — 8+ parameters within 50 ms — is a state restore (Live's undo, a reopened or
  duplicated set), listed under `restores`, not as touches. After each load or restore the
  host reads every value once, so a touch knows its `before`. Only plug-ins inside a host
  are watched; a native plug-in (after `live_plugin_host_expose`) is not.
  `live_plugin_host_parameters(touched=...)` reads it; bank dials follow window edits too.
- **By display text**: 33 values are tried and read back; an exact text wins, else the leading
  number is bisected between the two samples that bracket it. No match puts the old value back.
- **State**: `vst~` saves the plug-in's state with the Set (`parameter_enable`, `autosave`);
  one undo restores the whole state. When a Set reopens, `lbhost.js` finds the restored
  plug-in (`get -4` > 0) and adopts it — names, kind, status — without loading anything. Its
  name comes from the Live device: loading a plug-in renames the host device after it (Live
  saves device names in the Set; bank devices keep their file's name). The `.vst3` file name
  is not saved, so `live_plugin_host_bank` finds it by the plug-in's name (or `file=`). Opening the Set needs Max for Live.
- **Automation bank**: Live reads a device's parameters once, when it loads, so automatable
  names have to be in the file. `live_plugin_host_bank` writes `<plug-in> Bank.amxd` (User
  Library > LiveBridge Presets > Plug-in Hosts, next to copies of the scripts) with up to 128
  `live.dial`s named after the chosen parameters (`livebridge_mcp/host_device.py`): dial ->
  `prepend <vst index>` -> `vst~`; values LiveBridge sets go back to the dial with `set` (no
  echo). The device loads its plug-in itself the first time (config as one hex `@args`
  token) and leaves it alone when the Set restored it. The tool loads it on the same track,
  copies every parameter value (`values` / `restore`) and deletes the old host. Not carried
  over: plug-in state that is not a parameter (wavetables, samples); edits in the plug-in's
  window do not move the dials. Approach from xxnoodl's ableton_device_vst_wrapper (MIT).
- **Limits**: hosted parameters are not Live parameters unless banked; a user's existing
  plug-in can't be moved in with its settings (the API can't read plug-in state); VST3 only;
  latency changes of the plug-in are not reported to Live; not yet run in real Max by us — if
  a plug-in sends several names in one message, the count from `get -4` switches to one
  name per atom. Idea from Producer Pal's plug-in-host
  spike (GPL-3; re-implemented from its notes, no code taken).
