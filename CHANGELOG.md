# Changelog

LiveBridge's version is the Remote Script's (`VERSION` in `remote_script/LiveBridge/LiveBridge.py`);
the MCP server package and the installer have their own version numbers. `live_status` shows both.

## 0.3.0 (MCP server) / 1.1.2 (Remote Script)

- The website, README and project links point to this fork (morpheasgr/livebridge-ableton-mcp, branch `fork`) — downloads, clone commands and docs links led to the original repository, which does not have the mixing and mastering work; credit to Valentijn as the original author stays (license, footer, README). The site gains the double-click installer and updater, the Max 9 requirement of the channel strip and mastering devices, `strips="measure"`, the tested Live versions (up to 12.4.16) and links to the changelog and roadmap.
- From the stability check: the master's gain search counts a reading exactly 0.3 LU off as on target (floating point made it 0.30000000000000027 and took one more step); the compressor threshold's jitter is 3 dB (a bass's peak swung 1.3 dB between identical runs and moved its threshold 2.2).
- The compressor threshold's hysteresis also holds when the last threshold sat at the dial's 0 dB end (0 equals rest in number, so it read as "no compressor": the Arp's 0 -> -0.8); the master EQ deepens a cut only by 0.5 dB or more (-2.05 -> -2.2 was written).
- From a normal-mode rerun of Helios: the balance gives a part still where the last run left it 1 dB more tolerance (the level-neutral target moves a little whenever another part's level changes: an unchanged piano bass crossed the line and moved 1.6 dB); compressor makeup has hysteresis like the other strip controls (0.5 dB); a protected part's Trim no longer gets a "back to rest" reason; the master EQ keeps a value within the dial's own steps (-1.95 was written as -2.0 on every run).
- From re-mixing both test tracks after the fixes (sub gap to the releases shrank 6.8 dB on Lunae, 4.0 dB on Helios): a strip control going back to rest gets a reason (the kick's compressor went off with none — its crest sat just under the bar a new compressor needs); a compressor threshold clamped at the dial's 0 dB on a part peaking far above 0 dBFS says it takes more off than planned, and to lower the instrument's output; LiveBridge Master's `settings` include EQ Low Cut and round to 0.1 dB; the measured melodic_house profile says so in its caveat.
- `live_mix_auto(strips="measure")`: your own processing stays — no LiveBridge Channel is placed or changed, an Engine (meters only, no sound change) goes on each track without a LiveBridge meter, and the run only measures, balances the faders and masters (for mixes built on your own EQ/compressor plug-ins; `low_end` does not run, it would add devices). The first step towards letting the user override LiveBridge's processing.
- Channel high-pass by role: kick and sub keep everything down to 20 Hz (no high-pass), bass and drums from 25 Hz (all were 30 Hz, about 1.7 dB of the sub gap against the released masters). A rerun puts LiveBridge's earlier 30 Hz back to rest. The band verdicts stay advice.
- Found by comparing with the released masters of both test tracks (LiveBridge's drops had 9-13 dB less sub): LiveBridge Master's EQ Low Cut is the Channel EQ's fixed 80 Hz high-pass, and the device shipped with it on and `live_master_auto` switched it on — it took the sub and the kick's body off the master. It is off by default now, and `live_master_auto` switches it off on a device an earlier version set up (a note and `steps` say so).
- From measuring two released masters in the style against LiveBridge: the melodic_house band profile is measured now — both masters sat 6-10 dB under its 2-5 kHz target and 4-5 dB under 5-20 kHz, with ~2 dB more mids, so the profile flagged a "lack" of highs that finished records in the style do not have (high_mid -5.7, high -4.6 were profile artefacts; the mid excess and the sub deficit were real). `live_audio_analyze` takes `reference_start` / `reference_duration` (the reference was cut with the analysed file's window: a 32 s drop bounce was compared with a whole track); with `genre`, a flag says the band flags use general targets and `genre_balance` the genre's; the genre list names house and melodic_house; `start` / `reference_start` count from each file's first sound (`from_sound`, default on): a master's silent gap before the music (milliseconds: at most 50 ms counts as a gap, reported as `file.lead_silence_s`) no longer shifts the bars; a longer silence is the music itself (a first bar opening with an off-beat hi-hat) and shifts nothing.
- A master rerun starts its gain search at the last run's drive (reset to 0 for 'before') when that fits what is missing: one step instead of climbing 6 dB at a time (4-5 steps to the same 15.7 dB).
- From the band-stability retest (all passed): the balance reads the master with the chain in front of its Engine switched off for that one listen (the mix itself; the limiter had reshaped the bands — sub -10.0 mastered vs -7.4 raw), then on again; the protected Trim keeps its last value only within 1 dB again (1.5 let the protected lead sit 1.2-1.7 dB above its raw level); the master's gain search stops within 0.3 LU of the target (it ended 0.5 short).
- Installer: without Node.js the Splice line says it is optional and fine when Splice is a Claude Desktop connector ("npx (Node.js) not found" read like an error on every run).
- From the master and contrast run on the second project (rerun idempotent, master on target): `live_master_auto(loud_bar=, quiet_bar=)` on a stopped song puts the start marker back (it stayed at the loud bar); a band's master-or-tracks verdict keeps the last run's when the shares sit near the line (top part 33-47 %; remembered per set with the balance) — the lead's share moving 34 -> 41 % flipped high_mid and the master EQ with it; wider hysteresis where the readings swing most (compressor threshold 2 dB, the protected Trim 1.5 dB, EQ cuts 1 dB); LiveBridge Master's `settings` show the exact value (Limit Gain 14.2 displayed "14 dB"); new `crest_drop_db` next to `limiting_db` and `peak_reduction_db`.
- From the strip retest (all else passed): a protected part's Trim keeps the last run's value when the new one is within 1 dB (it drifted 2.2 -> 1.4 on an unchanged mix); its change reads from the value before the run ("2.2 -> 1.4", not "0 -> 1.4"); its `after` reading is marked `before_trim` with `lufs_with_trim` next to it.
- From the first full mix test on a second project (balance passed: moves exactly as planned, idempotent faders): `protect` keeps a part's level, not only its fader — the strip's Trim gives back what the high-pass/EQ/compression took (the protected lead lost 4.7 dB, then gained 2.7); the strip plan has hysteresis (a move LiveBridge made stays unless the reading clearly leaves its range, a new one needs a clear case, a threshold within 1 dB of the last one is kept — the lead's compressor and saturation came and went between two identical runs); strip controls are compared as Live's raw value (the display rounds, so -15.5 read "-16 dB" and three untouched thresholds were taken for the user's); the compressor note says a slow attack lets the transient through (crest can rise) instead of blaming the threshold; `apply=false` also plans the balance and the bands (with a note that the strips will change the levels); the balance's `role_from` is the real source (it said "you" for every part).
- From the first run on a second project: `live_mix_auto(apply=false)` places the devices too (it placed them only when applying, so a plan on a fresh set stopped with "no LiveBridge Channel"); the master's Engine is placed when the run balances (the band check needs it); a run that started the song waits for Live to have stopped before putting the start marker back (a marker written in the same tick as the stop was lost: 1.1.1 became 113.1.1); the "no Channel" error no longer talks about removing Engines.
- `live_mix_auto` remembers its Channel settings per set (`channel_strips.json`, keyed like the balance's memory: set path, else name): it was keyed by track name only, so a "Kick" in another set whose strip happened to match was taken for LiveBridge's own setting and overwritten. The old memory, which has no set, is dropped: on the first rerun in an old set LiveBridge's earlier settings count as the user's and are kept (`kept`); `keep_manual=false` sets them all again.
- Remote Script 1.1.2: `transport.stop(to_start=true)` on a stopped transport answers the playhead's target (1.1.1), not where it was before Live's next tick.
- Remote Script 1.1.1: `transport.stop` / `live_transport_stop` on a stopped transport changes nothing (`already_stopped: true`). Live's second Stop sends the playhead and the start marker back to 1.1.1, so a "just in case" stop moved the user's start marker (found while reproducing a start marker that ended up elsewhere); `to_start=true` asks for that on purpose.
- From the third master retest (all passed: before -36.2 LUFS consistent with every step, Kick -15.7 in both readings, EQ and start marker kept): `live_mix_phase` and `live_mix_unmask` take `section_bars` (play, loop, stop and give the loop back, like `live_mix_auto`); the master step says why a master EQ cut stays when the band still reads high (and, at the -6 dB limit, that the rest belongs in the parts) — the balance's band advice does not know about it; the phase note names its real bar (1 dB, or twice the noise); `max_gain_db` is documented as the limiter's own gain (LiveBridge Master's makeup adds up to 12 dB on top); `crest_db` is peak minus the unweighted RMS, not minus the LUFS.
- From the second master retest (reset, readback and the one-shot verdict passed): every listen of a run restarts the held loop from its first bar — a 6 s window in a 16-bar (31 s) section heard different bars each time, so the master's "before" read -30 LUFS where its own steps implied about -37 (and `limiting_db`, the gain-staging note, a kick's -16.1 vs -18.0 between the per-track and balance readings followed); the master EQ no longer adds up over runs (the bands are measured after it: a cut already as deep as the reading asks for stays, a bigger excess deepens it, a lack backs it off — an unchanged mix's mid went -3.5 -> -6); a run that started the song (`section_bars`, or the arrangement loop) puts Live's start marker back (97.1.1 became 113.1.1). `eq_bands` was also documented on `live_mix_phase` by mistake.
- `live_mix_phase` puts LiveBridge Phase in front of the track's Channel (or Engine), like `live_mix_unmask` does with Unmask, and moves one an earlier run left after it: the Channel's compressor, saturation and meter work on the phase-corrected part (found on a real set's bass track).
- From the master retest (passed: -34.5 -> -8.2 LUFS, EQ Mid -3.5 dB at 700 Hz, no fader moves; the "dead" Limit Gain was the stale "before"): LiveBridge Master's dB controls are set as numbers (their dials are linear) — as text Live matched them against a display that rounds to whole dB above 10, so Comp Makeup 11.1 landed as 11.5; `limiter_gain_db` / `makeup_db` are the values read back from Live; new `peak_reduction_db` (what the limiter shaves off the loudest peaks — 8.3 dB where `limiting_db`, the average loudness cost, read 2.8), named in the squash note, which also says that where the gain comes from (faders or drive) does not change the sound; a note when the mix reaches the master more than 20 dB under the target. `live_mix_balance(one_shots=[...])`: parts left alone as one-shots; `live_mix_auto` passes the parts its own listen found silent, so DR Impact 2 (silent in the per-track window, -23.9 LUFS in the balance's) gets one verdict.
- From the first master test on a real set: `live_master_auto` measures "before" with its own gain stages at 0 dB (a rerun read the last run's +18 dB Limit Gain as the mix: before and after were identical) and lists what it reset (`reset`); when the first real gain step is not heard (master not louder, peaks under the ceiling) it stops, sets the gain back and checks the device — on LiveBridge Master it turns Output down 6 dB for one listen to tell "the device's DSP does not run" from "only the limiter ignores its gain" (`device_check`) — instead of reporting `missing_gain_db` for gain that never reached the sound; a master device switched off is refused. It never moves faders on `chain="livebridge"` (a mix louder than the target lowers Limit Gain, down to -24 dB); on the native chain the fader trim is listed in `steps` and happens only when the mix is louder than the target (not for a peak above -3 dB). Master band fixes from the balance (`fix: "master"`) are written to LiveBridge Master's EQ (new `eq_bands`, which `live_mix_auto` passes: half the excess, 4 dB a run, +-6 dB in all; Low/Mid at the band's frequency/High), read back and listed in `steps` and `eq`. Past `max_gain_db`, the device's Comp Makeup drives the limiter up to 12 dB further (`makeup_db`); above 12 dB of drive a note warns that the transients are being flattened. The report adds `settings` (read back from Live), `limiting_db` (drive minus loudness gained) and keeps `before`, `steps`, `eq` in the summary. `live_mix_auto`: every part carries the balance's verdict (`balance`: the move, unchanged, protected, or why it was left alone — the per-track section said `lufs null` for DR Impact 2 while the balance had measured it); the balance answer lists `left_out` with the reason.
- LiveBridge Master / Channel / Phase: the switches (EQ Low Cut, Limit Auto Release, Bass Mono, Invert) are Off/On menus — as `live.text` toggles Live showed them as "val1"/"val2". Frequency dials (EQ Mid Freq, Bass Mono Freq, ...) follow a curve: Live's raw value is the dial position on a linear 120-7500 / 50-500 scale (3752 for 1 kHz, 292 for 120 Hz), the DSP gets the displayed Hz — LiveBridge sets them as "<n> Hz" text. Replace the devices in a set to get the new menus (Live keeps the old device until it is reloaded).
- The installer's checklist says whether the Claude Desktop skill needs uploading again: first install, UPDATED (remove the old one, upload the new one), or unchanged.
- The installer (and uninstaller) also finds Claude Desktop's config in recent versions' per-account folders, `%LOCALAPPDATA%\Claude-Data\claude_<id>\claude_desktop_config.json`, and no longer creates a config in a folder an older version left behind when a real config exists elsewhere (it wrote one holding only LiveBridge into the unused Microsoft Store folder).
- The installer's next-steps list is shorter and wraps at 78 columns: the Max for Live devices are one step, grouped by browser folder with one line each.
- Updater and double-click launchers: `Install LiveBridge.bat` / `.command` and `Update LiveBridge.bat` / `.command` in the folder; `installers/update.py` (also `install.ps1/install.sh --update`) pulls a git checkout or downloads the branch's newest commit over a downloaded folder — files you added stay, unchanged files are not rewritten, a failed download changes nothing, locked files stop it before the installer — then runs the installer.
- From the third retest (passed): a part a limit keeps off its role's level is listed under `held` (`off_db`, which limit) with a note on what to do (`live_mix_balance` with a higher `max_move_db` / `max_raise_db` — `live_mix_auto` does not take them), instead of only under `unchanged` (DR Hihat 1 still 5.6 dB hot, DR Tom 4.6 dB short).
- From the second retest: the balance remembers per set what it did to each fader (`balance_state.json` in the library): while a fader stays where it left it, `max_raise_db` / `max_move_db` count over runs, so a capped part (DR Tom +3, +3, ...) is final until the user moves that fader; the one-shot check keeps its answer between runs unless the reading is clearly different (±10 %; DR Impact 2 flipped at 30-35 %); `protect` freezes the faders but no longer anchors the centring on them (everything else came up ~2.5 dB around the lead); the summary lists `unchanged` and `protected` parts (VOX Rhythmic Loop was on target but not mentioned).
- From the first idempotency test on a real set: the balance report lists every fader move of the run with from/to (it showed only the last pass, `moves=[]`, while 6 faders had moved); a part left alone in the first pass (silent, a one-shot) stays alone in the later ones (DR Impact 2 was "left alone" and moved 6 dB); parts more than `tolerance_db` (now 3) off their role move only to just inside it, so a second run changes nothing; no headroom trim from a master Engine that sits behind a limiter (its peak is the limiter's output); melodic_house leads at -2 LU. `live_mix_auto`: a muted track with no mute automation keeps its strip; per strip `changes` shows only what the run changed, `before -> after`; "BS Break Subbass" is a sub, not drums. `section_bars=[first, last]` on `live_mix_auto` and `live_mix_balance`: the tool loops and plays the section itself and stops again afterwards.
- `live_status` shows `mcp_host.build`, a fingerprint of the server's code as loaded at start; `python installers/build_id.py` prints the checkout's, so a restart that did not pick up a pull shows (the version alone stayed 0.2.0 over several changes). The level-neutral balance below shipped as 0.2.0 without a bump; 0.3.0 is the first version that is sure to have it.

## 0.2.0 (MCP server) / 1.1.0 (Remote Script)

- The balance is level-neutral: moves are centred so the mix keeps its level and only parts more than `tolerance_db` (2) off their role move — anchoring on the kick and lowering everything when one quiet part could not reach its target took a 23-track mix down 10-14 dB; a fader that would need more than +6 dB stops there; `max_move_db` is a budget per call; `protect` keeps parts in place; the headroom trim moves every track fader; unmeasured parts are named. Runs listen over exactly one loop so before/after hear the same bars. `live_mix_auto`: kick/bass fixes opt-in (`low_end`), a Phase device that changed nothing is removed, the measured compressor effect per part (`measured`), a summary report (`detail="full"` for all), the genre list, unknown genres refused. `live_master_auto`: the contrast check reports both sections' LUFS and warns when the quiet bar is louder; at the gain cap it says how much level is missing (`missing_gain_db`).
- From the first long run on a real set: runs loop the bars that were playing while they listen and give the loop back (a 130 s run had balanced against the next section); strip controls set by hand are kept on a rerun (`keep_manual`, LiveBridge remembers its own settings); the balance raises a fader at most 3 dB per run (`max_raise_db`) and counts a part as playing only within 10 dB of its peak (a tom's tail made it look busy).
- Long mix/master runs no longer lose their report to the MCP client's call timeout: `live_mix_auto`, `live_mix_balance`, `live_master_auto`, `live_mix_phase` and `live_mix_unmask` run as background jobs (past ~40 s the call answers `{job, status: "running", phase}`), new tool `live_mix_job` (222 tools) returns the report; one run at a time (`busy`). Balance: parts that play under 35 % of the window (one-shots, fills) are left alone (`min_active`), a fader moves at most 6 dB per pass (`max_move_db`), readings carry `active_share`. Genres: `house` has its own profile (it was silently techno) and `melodic_house` keeps leads, pads and keys close to the kick; the answer names the profile.
- Mix and master without placing anything: `live_mix_auto` puts a LiveBridge Channel on every sounding track (replacing an Engine there) and an Engine on the master itself, aligns and unmasks kick vs bass (`low_end`), balances, and masters through LiveBridge Master by default (`master_chain="livebridge"`); `live_master_auto`, `live_mix_phase` and `live_mix_unmask` place the meters they need (`place_devices=true`). A device not found right after installing is retried with a fresh browser cache.
- Fixes from the first Live run of the LiveBridge devices: parameter names are no longer cut at 12 characters (Live's API uses the short name: "Comp Thresho" broke `live_mix_auto`; the plug-in host bank dials too); frequency controls are set as "<n> Hz" text (their curved dials do not take raw Hz); the compressors' second inlet (their detector) is fed — they ignored threshold and ratio and only added makeup; makeup is only added with a fast attack (a slow one lets the transient through, so makeup raised the kick's peak); controls in 3 rows that fit Live's device height.
- LiveBridge Unmask (Live 12): a side-chained dynamic EQ in gen~ (`plugin~ 1 2 3 4`: the device's side-chain input). New tool `live_mix_unmask` (221 tools): measures two clashing parts, picks the shared band, loads the device on the part that gives way, routes the other one into its side-chain and cuts that band only while the dominant part plays — Fuser's idea. `live_routing_route(method="sidechain")` now also routes a Max device's side-chain input.
- Fix: LiveBridge Master and Channel were silent in Max 9.1.5 — `abl.device.stereocompressor~` (documented) is not there; both use `abl.device.compressor~` once per side now (no knee/mix/auto-release controls).
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
