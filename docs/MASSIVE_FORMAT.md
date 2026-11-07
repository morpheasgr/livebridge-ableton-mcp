# Massive (classic) preset format — research notes

Findings from the user's own files (AdditionMOD.nmsv, its .vstpreset, and two saves of the routed
template), implemented in `mcp_server/livebridge_mcp/massive.py` (`live_massive_write`). Format analysis of our own presets for
interoperability; no Massive program code was examined.

## Containers

- **`.nmsv`** (Massive's own preset) and the **`Comp` chunk of a `.vstpreset`** (what Live saves
  when the Massive device is dragged into the User Library) hold the same NI container: blocks
  `<u64 size><u32 1>hsin<u32 1><u32 0><16-byte id>` followed by `DSIN` chunks. The outer chunks hold
  the name, author, tags (UTF-16) and the controller pages; the `DSINt` chunk holds the patch.
- **The patch** is compressed: `<u8 1><u32 raw size><u32 packed size>` then a FastLZ level-1
  stream (the public LZ77 scheme). Unpacked it is another NI container. The `.vstpreset` copy adds
  a 16-byte `prst` block and a ~100-byte `bank` trailer; the patch data is otherwise identical.
- The `.vstpreset` wrapper itself is Steinberg's documented format (`parse_vstpreset` in
  `remote_script/LiveBridge/plugin_racks_lib.py` reads it); `live_plugin_expose(preset_file=...)`
  loads it.

FastLZ level 1, decoding (written for LiveBridge from the public description):

```python
def fastlz1(src, size):
    out, ip = bytearray(), 0
    ctrl = src[ip] & 31; ip += 1
    while True:
        if ctrl >= 32:                         # match: length, then distance back
            length = (ctrl >> 5) - 1
            if length == 6:
                length += src[ip]; ip += 1
            ref = len(out) - ((ctrl & 31) << 8) - 1 - src[ip]; ip += 1
            for _ in range(length + 3):
                out.append(out[ref]); ref += 1
        else:                                  # literal run of ctrl + 1 bytes
            out += src[ip:ip + ctrl + 1]; ip += ctrl + 1
        if ip >= len(src) or len(out) >= size:
            return bytes(out)
        ctrl = src[ip]; ip += 1
```

Writing can skip real compression: a stream of literal runs (at most 32 bytes each) is valid
FastLZ — to be confirmed by Massive loading one.

## Inside the unpacked patch (two template saves compared, 5,708 bytes each)

| Offset | Change between the saves | Meaning |
|---|---|---|
| 24-39, 108-123 | random | per-save ids (ignore) |
| 272 | 1.0 -> 0.0 | `OSC2-AMP` |
| 292 | 1.0 -> 0.0 | `OSC3-AMP` |
| 624 | 0.5 -> 0.8076 | `ENVELOPE3-DEC.TME` (Live showed "81") |
| 1124 | one byte 0 -> 1 | the oscillators' "restart via gate" switch |
| 5636 | 3 -> 2 | unknown (a counter?) |

**The continuous controls are one float32 table, in the map's order:** offset = 208 + 4 x the
parameter's index in `plugin_maps/massive_vst3.json` (OSC2-AMP index 16, OSC3-AMP 21,
ENVELOPE3-DEC.TME 104 all fit), values 0..1 as Live's normalized value. This holds for the
knobs (map indices 0-148); the switches and selectors from index 149 on (on/off, types,
wavetables, modes) are stored elsewhere as integers — OSC1-WAVETABLE (index 159) did not change
at 208 + 4 x 159 when the wavetable changed.

## Labelled pairs (2026-11-04)

Eight presets, each the routed template plus one change, compared with the template (offsets
in the unpacked patch; `f` = float32, `i` = int32, little-endian):

| Change | Offset | Before -> after | Reading |
|---|---|---|---|
| Env 1 -> filter 1 cutoff: amount to full | 1770 f | 0.6504 -> 1.0 | routing amount |
| (same slot) | 1774 i | 0 | its source: envelope 1 = 0 |
| Env 1 -> filter 2 cutoff removed | 1921 i | 0 -> -1 | empty slot = -1; the amount at 1917 (0.6738) stays |
| Env 2 -> filter 1 resonance added | 1872 f / 1876 i | 0 -> 0.6914 / -1 -> 1 | envelope 2 = 1 (a first save without the amount set showed only the source changing) |
| Macro 1 -> filter 1 resonance | 1872 f / 1876 i | 0 -> 0.7832 / -1 -> 13 | macro 1 = source 13 |
| Slot 5 curve A sine -> square | 3236 i | 0 -> 2 | curve index (sine 0, square 2; probably the menu's order: sine, saw, square, triangle) |
| Slot 8 LFO -> stepper | 3292 i | 0 -> 2 | slot type (LFO 0, stepper 2; performer probably 1); a 120-byte stepper block is inserted after it, and the container's size fields (offsets 0, 84, 180, 192) grow |
| Stepper step 1 to maximum | 3300 f (in the stepper file) | 0 -> 1.0 | step values are floats |
| Osc 1 wavetable Squ-Sw I -> VA-Sync | 1358 i | 0 -> -4 | wavetable id (not the list position: a table of ids is needed) |

So a **routing slot is a float32 amount followed by an int32 source** (-1 = empty; envelopes
1-4 probably 0-3, slots 5-8 probably 4-7, macro 1 = 13). Each knob's slots sit at fixed offsets
(filter 1 cutoff 1770, filter 1 resonance 1872, filter 2 cutoff 1917 ...): mapping the slot
offsets of every modulatable knob is the next step, again by labelled pairs. Still unknown:
the full source numbering, the wavetable ids, performer curves, the other switches.

**Knob records are keyed by the map index.** Each modulatable knob has a record that starts
with its index in `massive_vst3.json` as a byte (filter 1 cutoff 0x21 = 33, bandwidth 0x22,
resonance 0x23, filter 2 cutoff 0x24), followed by fields (e.g. -3.0 / 10.0, purpose unknown)
and its slots (amount f32 + source i32, -1 = empty, a couple of bytes between slots). Records
are variable-length, so a writer finds a knob by its index and walks its slots rather than
using fixed offsets.

The template's own routings, found by scanning for amount + source pairs: envelope 2 (1) and
slot 6 (5) on the oscillator positions (1419/1429, 1611/1621), envelope 3 (2) on pitch with
amount 0.094 = 12 of the ±64-semitone range (1378, 1570), envelope 1 (0) and slot 5 (4) on the
cutoffs (1770/1780, 1917/1927), slot 7 (6) on pan (2922). So **sources: envelopes 1-4 = 0-3,
slots 5-8 = 4-7, macros 1-8 = 13-20, KTr 21, Vel 22, TrR 23, AT 24** (all confirmed in round 2). Pitch amounts are a fraction of 128 semitones.

## Labelled pairs, round 2 (2026-11-04)

Ten more presets, each routing one source to filter 1 resonance (its first slot: amount at
1872, source at 1876):

| Source | Number |
|---|---|
| Envelope 1-4 | 0-3 (4 confirmed) |
| Slots 5-8 | 4-7 (8 confirmed) |
| Macros 1-8 | 13-20 (1 and 8 confirmed) |
| KTr | 21 |
| Vel | 22 |
| TrR | 23 |
| AT | 24 |

8-12 did not occur (unused, or sources not on these panels). **Insert 1** (16a -> 16b): its first
knob has a record like the others; slot 5 on it wrote amount 1.0 and source 4 into that
record's first slot (around offset 2072), so inserts are modulated the same way. Choosing the
insert type changed an integer at 2048 (0 -> 1) and reset a knob default at 2103 (1.0 -> 0.5).
(16a was saved with preset 15's TrR routing still on resonance; 16b vs 16a was clean.)
**Slot 8 as performer** (17): the slot type is 1 (LFO 0, performer 1, stepper 2), and a ~270-byte
performer block (repeating float/int curve records) is inserted, with the container's size
fields growing as for the stepper.

A writer does not need the wavetable ids: wavetables and every other exposed control are set
through Live by name after the written patch is loaded; the file carries only what the host
cannot reach (routings, LFO curves, stepper steps, slot types).

Method: save two presets that differ in one known thing, unpack both, compare.

## The knob record, exactly (2026-11-07)

`u32 4, f32 lo, f32 hi, u32 n`, then `n` slots of `f32 amount, i32 source, 2 flag bytes`, then
the knob's `u32` map index. `n` is fixed per knob (cutoff and oscillator position 3, pitch and
resonance 2, envelope and slot controls 1), so a write never changes the patch's size. 93
records in the template; two with index -2 (envelope 4 on the amp) are not map parameters. The
flag bytes are kept as found (`00 01` on some cutoff slots, meaning unknown). Sizes to fix when
the compressed stream changes length: every u64 block/chunk size whose extent covers it (five in
the template: the outer and inner `hsin` blocks and the `DSINt`/`DSINs` chunks).

## LFO curves (2026-11-07)

Slots 5-8 are 24-byte blocks `i32 type, i32 2, 0, 0, i32 curve A, i32 curve B` (template:
slot 5 at 3220, slot 6 at 3244 ...). Curves: sine 0, saw (down) 1, square 2, triangle 3 —
the buttons' order (18: slot 6 curve A triangle = 3 at 3260; 19: slot 7 curve B square = 2 at
3288). A stepper or performer block follows its slot's type, so later slots move.

## Steppers (2026-11-07)

Turning an LFO slot into a stepper replaces the 20 bytes after its type (`2, 0, 0, A, B`)
with 140: `i32 2, 16 x f32 steps (0..1, 0 = bottom), 16 zero bytes, i32 2, 16 x 3 per-step
bytes (00 01 00 by default: the two buttons under each step, top = glide), i32 0`, sets the
type to 2 and adds 120 to the five u64 sizes in the unpacked patch at 0, 84 (`hsin`), 124
(`DSIN`), 180 and 192 (`prst`). Verified for slots 5 (20) and 8 (05): the result equals
Massive's own save. Steps 1, 2 and 16 at +8, +12, +68 from the type (06, 22, 21).
