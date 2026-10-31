# LiveBridge roadmap

LiveBridge is free and open source (MIT), built on what open projects learned about Ableton
Live's Remote Script API. It is not a product for sale; this document says where it stands, where
it is going and how to help. The original Dutch plan from the first build (September 2026) is kept
as [PLAN.md](PLAN.md); [CHANGELOG.md](CHANGELOG.md) has the detail of every change.

## What LiveBridge is for

Let Claude do in Ableton Live what a producer does — build tracks, edit clips and notes, load and
set devices and plug-ins, arrange, automate, record — and, since late 2026, **mix and master by
measurement**: listen to every part and the master, set channel strips and faders by role, and
master to a loudness target, explaining every move.

The goal for mixing and mastering is practical: multitrack mix-mastering by an engineer is
expensive. LiveBridge should get an independent producer a long way towards a release-ready mix
and master, and show honestly where a human ear (or an engineer) still makes the difference.

## Principles

These held through the whole build and stay:

1. **Measure, don't guess.** Every mix or master decision is measured before and after, and
   reported with its reason. Rules of thumb are labelled as such; measured targets (from released
   masters) replace them where we have data.
2. **The user can override everything.** Every automatic step can be skipped, planned without
   applying (`apply=false`), limited (`protect`, `roles`, `max_move_db`, ...) or replaced by the
   user's own processing (`strips="measure"`). Manual changes are respected (`keep_manual`).
3. **A rerun on a settled mix changes nothing.** Hysteresis on every decision; per-set memory of
   what LiveBridge did; run-to-run noise (a few tenths of a dB) is not a reason to move anything.
4. **Never touch Live's own files.** No writing to Live's database or preferences; reads go
   through temporary copies, done by the MCP host, never inside Live.
5. **Windows and macOS, always.** Every feature, installer and test runs on both (CI covers
   macOS, Windows and Linux).
6. **Clean-room towards non-permissive projects.** Ideas from GPL / AGPL / BSL tools are fine,
   their code is not. MIT-licensed code may be ported with its notice.
7. **Tested without Live.** A fake `Live` package runs the whole suite (about 1,770 tests);
   real-Live checks are scripted and repeatable.

## Where it stands (October 2026)

| Layer | What | Needs |
|---|---|---|
| Remote Script + MCP server | 222 tools: transport, tracks, clips, notes, devices, browser, plug-ins (VST3 maps, Serum 2), racks, automation, arrangement, routing, recording, Splice, theory and audio analysis, session memory | Live 12, **any edition** |
| LiveBridge Engine | Claude's ears: loudness, peaks, crest, 8 bands, stereo, per track and on the master; capture, modulation, follow actions | Max for Live |
| LiveBridge Channel, Master, Phase, Unmask | Channel strip, mastering chain, phase alignment, side-chained dynamic EQ — Ableton's own DSP in Max devices | Max for Live, **Max 9** (Live 12.2+) for Channel and Master |
| LiveBridge Host | Any VST3 plug-in with all its parameters (past Live's 128) | Max for Live (experimental) |
| Installer / updater | One double-click on Windows and macOS; updates in place | Python 3.10+ |

**Editions, said plainly:** the core works in Intro, Standard and Suite. Everything that listens
— the mix and master tools — needs Max for Live (Suite, or Standard with the Max for Live
add-on). Without it the tools say so and nothing breaks.

**Mix and master, verified on real projects:** two finished melodic house / techno tracks were
mixed and mastered by LiveBridge and compared with their professionally mix-mastered releases.
After the fixes that comparison led to (an 80 Hz low cut and 30 Hz high-passes that took the sub
off; a genre profile that called a released master's highs "too low"), reruns are stable and the
masters land within 0.3 LU of their target. Against the releases LiveBridge is still about 1 LU
quieter, about 2 dB less limited, and 5-6 dB lighter in the sub — the next work is about that.

## Roadmap

### Next: master against real references

The releases taught more than any rule of thumb. Turn that into the method:

1. **Profiles from released masters.** Point a tool at a folder of finished masters (one style,
   one artist, a label's sound). It finds each track's drop and breakdown from its energy curve,
   measures loudness, crest, the 8 bands, width and drop/breakdown contrast, and stores the
   averages — with their spread — as a named profile usable wherever `genre` is
   (`genre="my_masters"`). The built-in genre profiles get recalibrated the same way as people
   contribute measurements.
2. **Reference mode for mastering.** `live_master_auto(reference=<file or profile>)` aims at the
   reference's loudness and crest instead of a fixed destination, moves the master EQ toward its
   bands (within limits, never a cure for a mix problem) and its width, and reports what is still
   off.
3. **A/B built in.** After mastering, bounce the section and compare it with the reference
   automatically — the check that is done by hand today.
4. **Mix targets from the same data.** Where the releases consistently differ from LiveBridge's
   mixes (the sub weight, for one), turn that into role levels and band targets for the balance —
   as advice first, action only when the user switches it on.

### Then: the user's own plug-ins in LiveBridge's roles

Today a user can keep their own processing (`strips="measure"`) or drive any plug-in by hand. Next,
the automatic steps drive the user's plug-ins too:

- **Roles with small contracts:** master limiter (input gain, ceiling), master EQ (bell gain,
  frequency, Q), strip EQ (high-pass, bell), strip compressor (threshold, ratio, attack, release).
- **Maps built on the user's machine:** LiveBridge reads the plug-in's own parameter list,
  matches the controls, the user confirms once; the map is a small JSON file that can be shared
  and contributed. Nothing hardcoded per vendor; unmapped roles fall back to LiveBridge's devices.
- **What stays impossible:** buttons and meters inside a plug-in window (an assistant's
  "analyze", gain-reduction meters) are not in Live's API. Those stay the user's; LiveBridge
  measures before and after.

### Later

- **Rendering audio** without real-time recording (the Extensions SDK, Suite) — only if it becomes
  stable and available outside beta.
- **More measured genre profiles**, contributed by users with releases in those styles.
- **Section-aware mixing:** balance and check each section (intro, breakdown, drop), not only the
  fullest one; parts that only play elsewhere (a vocal in the breakdown) get their own pass.
- **Clearer edition handling in the docs and the site:** what works without Max for Live, what
  needs Max 9.

### Not planned

- A paid or closed version.
- Replacing an engineer's judgement: LiveBridge reports numbers and reasons; taste and the final
  call stay with the user.
- Anything that writes Live's database, preferences or set files behind Live's back.

## How to help

- **Test on your own projects** and report what bothered you while working: wrong roles, moves
  that sound wrong, confusing reports. The most useful reports compare LiveBridge's result with a
  reference (`live_audio_analyze(file_path=..., reference=..., reference_start=...)`).
- **Contribute measurements** of released masters in a style (numbers only, no audio), and
  plug-in maps once the role mechanism exists.
- **A rule for test reports:** report what changes the sound, moves something it shouldn't, or
  makes a report wrong. Run-to-run noise of a few tenths of a dB is expected.
- Code: see [CONTRIBUTING.md](CONTRIBUTING.md); every change comes with tests that run without
  Live.
