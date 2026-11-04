# Massive (classic) preset format — research notes

Findings from the user's own files (AdditionMOD.nmsv, its .vstpreset, and two saves of the routed
template), for a future Massive patch writer. Format analysis of our own presets for
interoperability; no Massive program code was examined. Nothing here is implemented yet.

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

Method: save two presets that differ in one known thing, unpack both, compare.
