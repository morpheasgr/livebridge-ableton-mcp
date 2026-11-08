# Surge XT patch format — research notes

Findings from Surge XT patches for a future LiveBridge patch writer: the user's own labelled
patches (LB StepSeq, LB Sources 1/2), Surge's 16 factory templates and five free patches
(CC0/free). Surge is GPL-3.0: format worked out from the files only, no Surge code read or
copied. Implemented in `mcp_server/livebridge_mcp/surge.py` (`live_surge_write`).

## File layout (`.fxp`)

- 60-byte FXP header, big-endian: `CcnK`, size (0), `FPCh`, version 1, plug-in id `cjs3`,
  version 1, 1 program, 28-byte name, u32 chunk size (= length of the rest).
- Chunk, little-endian: `sub3`, u32 XML size, 6 x u32 wavetable sizes (scene A osc 1-3, scene B
  osc 1-3; 0 = none), then the XML, then the wavetables in that order.
- Wavetable block: `u32 0, u32 samples per frame (512/2048), u16 frames, u16 flags (4)`, then
  int16 samples (12 + frames x samples x 2 bytes; checked on 16 patches).
- Revision 30+ (nightlies): a zstd frame after the wavetables, not counted in the header's
  sizes; revision 31 declares it in the XML (`<serialization absSize="77" />`). A writer replaces
  only the XML, copies everything after it unchanged and fixes the XML size and chunk size.
  Revisions seen: 21, 22, 24, 26, 30, 31.

## Wavetable flags and `.wt` files

Confirmed by reading Surge's `scripts/patch-tool` and `scripts/wt-tool` for facts (no code
used): the header word our patches show as 4 is a flags field — 0x01 the oscillator holds a
sample, 0x02 the sample loops, 0x04 samples are 16-bit integers (otherwise 32-bit floats), 0x08
full-range 16-bit (0x0C). A standalone `.wt` file has the same 12-byte header with the ident
`vawt` in place of the leading zero word, then the samples. The writer copies embedded tables
byte for byte, so every variant survives, and embeds a `.wt` file by writing its header with a
zero word in place of `vawt` (as Surge's own patches store it) into the oscillator's slot;
oscillator type 2 is Wavetable, 7 Window (both read the table; from the factory templates).

## As a `.vstpreset`

Surge XT's VST3 state (class id `ABCDEF019182FAEB566D624153675854`) is the `.fxp` body without
its 60-byte header: the `Comp` chunk is `sub3` + XML + the rest (checked on the user's "Init"
saved from Live, revision 31). So the writer emits both; `live_plugin_expose(preset_file=)`
loads the `.vstpreset`.

## The XML

- `<parameters>`: every parameter by name (`a_`/`b_` = scene A/B, `fx1_type`...), `value`,
  `type` (0 int, 2 float), flags (`extend_range`, `deactivated`, `temposync`, `deform_type`).
  Empty FX slots are absent (Init Saw has no `fx*` entries).
- Modulation: inside the target, `<modrouting source="N" depth="D" muted="0" source_index="0"
  [source_scene="0"] />`; several per target. A routing with depth exactly 0 is not saved.
- Depth units = the target's: semitones for pitch (+12 -> 12, max +/-14 on osc pitch) and for
  filter cutoff (5.79 st shown as 374-731 Hz -> 5.794); fractions for % targets (200 % -> 2).
  Cutoff `value` is semitones from A440 (3 -> 523.25 Hz).
- `<customcontroller>`: macro labels and values; `<lfobanklabels>`: per-output names;
  `<formulae>`: base64 Lua; `<msegs>`; `<stepsequences>`.

## Modulation sources (confirmed)

| N | Source | N | Source |
|---|---|---|---|
| 1 | Velocity | 15 | Amp EG |
| 2 | Keytrack | 16 | Filter EG |
| 3 | Poly AT | 17-22 | LFO 1-6 (voice) |
| 4 | MPE Pressure (channel AT) | 23-28 | S-LFO 1-6 (1, 2 confirmed) |
| 5 | Pitch bend | 30 | Release velocity |
| 6 | Mod wheel | 38 | Lowest key |
| 7-14 | Macros 1-8 | 39 | Highest key |

Open: Random (probably 31, used often on filters in free patches), Breath, Expression, Sustain,
MPE Timbre, Alternate, Latest Key. Multi-output LFOs (formula) pick the output with
`source_index`.

## LFOs and the step sequencer

- `a_lfoN_shape`: 0 sine (default) ... 6 envelope-like, 7 step sequencer, 9 formula.
- `<sequence scene="0" i="0" s0="1" s1="-1" s2="0.5" loop_start="0" loop_end="7" shuffle="0"
  trigmask="0" trigmask_0to15="0" trigmask_16to31="0" trigmask_32to47="0" />`: steps -1..1
  (linear; default 0, only non-zero steps written), loop counted from 0 (7 = step 8), swing,
  envelope-retrigger masks. The arpeggiator (revision 30+) is too new to write; its data is kept.

## Filter types

`a_filterN_type` 0-35 in Surge's own order (not the menu's), `a_filterN_subtype` 0-15; the list
with every subtype is `FILTER_TYPES` in `mcp_server/livebridge_mcp/surge.py` (read in Live by
local Claude, Surge XT 1.4.0): 0 Off, 1 LP 12 dB, 2 LP 24 dB, 3 LP Legacy Ladder, 4 HP 12 dB,
5 HP 24 dB, 6 BP 12 dB, 7 N 12 dB, 8 FX Comb +, 9 FX Sample & Hold, 10 LP Vintage Ladder, 11-12
LP OB-Xd 12/24 dB, 13-14 LP/HP K35, 15 LP Diode Ladder, 16-19 LP/HP/N/BP Cutoff Warp, 20-22
HP/N/BP OB-Xd 12 dB, 23 BP 24 dB, 24 N 24 dB, 25 FX Comb -, 26 FX Allpass, 27 FX Cutoff Warp AP,
28-31 LP/HP/N/BP Res Warp, 32 FX Res Warp AP, 33 MULTI Tri-pole, 34 MULTI Fast SVF, 35 MULTI
Xpander.

## Host values

In Live every control is 0..1, linear in the patch units: `(value - min) / (max - min)` for
floats, `0.005 + 0.99 * i / (count - 1)` for menus.

## LFO rate

`a_lfoN_rate` is log2 of the frequency: 0 = 1 Hz. With `temposync="1"` the same number is read at
120 BPM, so it names a note length: 1/8 = 2 (4 Hz at 120), 1/16 = 3, 1/4 = 1, 1 bar = -1
(user's patches: 1/8 -> 2, 1/16 -> 3). The step sequencer moves one step per LFO cycle.

## Folders

User patches: `Documents\Surge Synth Team\Surge XT\Patches\<category>` (Windows).
