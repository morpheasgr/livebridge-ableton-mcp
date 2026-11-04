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

**The exposed controls are one float32 table, in the map's order:** offset = 208 + 4 x the
parameter's index in `plugin_maps/massive_vst3.json` (OSC2-AMP index 16, OSC3-AMP 21,
ENVELOPE3-DEC.TME 104 all fit), values 0..1 as Live's normalized value. Still unknown: the
routings (source, target, amount), LFO curves, stepper steps, performer curves, the other
switches. The method that found the above: save two presets that differ in one known thing,
unpack both, compare.
