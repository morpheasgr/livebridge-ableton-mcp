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

## Folders

User patches: `Documents\Surge Synth Team\Surge XT\Patches\<category>` (Windows).
