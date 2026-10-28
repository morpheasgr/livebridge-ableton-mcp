# Serum 2 preset files: the whole patch

Companion to `SKILL.md` and `SERUM.md`. `SERUM.md` covers the 128 knobs Live can reach
(`live_plugin_expose`); this file covers the **preset file**, which holds everything — wavetables,
oscillator modes, the modulation matrix, FX order, curves — through the `live_preset_*` tools,
and loading a finished file into Live as Serum 2 (`live_preset_load`).

Adapted from ableton-mcp-pro (MIT, Copyright (c) 2026 nicholasbien; Serum preset work by
kasperikaivola, commit 6ac32c8 — `THIRD_PARTY_NOTICES.md`), with LiveBridge's tool names.

## Workflow

**A request to create a new Serum 2 preset authorizes designing and writing a new
`.SerumPreset`. Deliver the file, not only suggestions, a renamed template, one
envelope tweak, or a dry-run.** Use the offline tools for controls absent from
Live's Configure inventory. Read `new-preset-design` and `module-editing` below
before implementation. Live availability is not a prerequisite for a file-only
request; inspect the instance only when working with a running instrument.
If loading is unavailable, finish the supported offline design and report the
load/audition step separately. Do not defer known file-editable settings to the
user merely because they are not host-exposed.

**“Make this preset faster/darker/bouncy”, “edit this preset”, and equivalent
imperative requests authorize actual preset-file edits.** Automatically follow
`existing-preset-edits`: unpack the identified existing `.SerumPreset` to complete
JSON on disk, edit a separate JSON, then repack a separate `.SerumPreset` and
verify decoded changes. Do not treat these as suggestion-only requests or stop
at Configure writes/a manual recipe. Preserve the original and the patch's
unrelated settings; do not initialize it or replace it with a new style template.

Find starting presets with `live_preset_find(query="reese")` before guessing file paths: it
searches Serum 2's preset folders (Serum 2: menu -> Show Presets Folder; set
`LIVEBRIDGE_SERUM_PRESETS` when it lives elsewhere) and the designs folder
(`~/.livebridge/serum`, where bare output names go). It also lists the Serum 2 templates in
Live's User Library that loading needs. Validate a candidate with `live_preset_inspect` before
editing. Legacy `.fxp` files cannot be edited by these tools. Paths are filesystem paths, not
Ableton browser URIs or Serum's relative asset paths.

These tools inspect and edit **offline** Serum 2 `.SerumPreset` files including
audio, wavetables, modulation, curves, FX and unknown fields. They preserve the
decoded state outside explicit edits. They cannot read a loaded instance's
unsaved state, automatically load an output into Serum or audition its sound.
Keep huge JSON on disk; never send the whole preset to chat.

The general sequence below supports new designs and direct guarded edits.
For requested existing-preset transformations, use the **Automatic JSON round
trip** in `existing-preset-edits` instead; do not apply the new-preset redesign
requirement to unrelated parts of an existing patch.

1. For a new preset, choose a validated Init or suitable template and relevant
   checked references; retain the source and record its version and dependencies.
   For current-instance edits, save/export its patch separately; a same-named
   disk preset is not current instance readback.
2. `live_preset_inspect(file_path)` gives metadata, module paths and source
   SHA256. Inspect actual modules, not an assumed schema.
3. `live_preset_search(file_path, query="Decay")` finds actual paths/values.
   Narrow with `path="/data/Env0"` or another discovered module.
4. `live_preset_read(file_path, path="/data/Env0/plainParams")` reads controls.
   Expand child pointers and paginate using `next_offset`. Read relevant FX,
   modulation routes and curves. Unknown IDs and defaults remain unknown.
5. Design and implement the complete sound using the coverage checklist below.
   Use observed keys/enums and verified ranges/units/mappings. Reference-native
   settings can be reused with their coherent module context; label their raw
   representation honestly. Never substitute normalized host values without a
   verified conversion. Resolve individual unknowns without abandoning supported edits.
6. `live_preset_edit` requires the inspected `expected_sha256` and operations.
   Default `dry_run=true` validates/encodes and previews without writing.
7. Repeat with `dry_run=false` and a separate output path. Default format is
   `preset`, extension `.SerumPreset`. Existing outputs need `overwrite=true`.
   Source overwrites, aliases and hard links are rejected.
8. `live_preset_compare(left_path=original, right_path=output)` confirms decoded
   changes. Inspect all pages; equality concerns state, not compression or sound.
9. `live_preset_load(source_path=output)` puts Serum 2 with that preset on a new MIDI track;
   `track=` replaces that track's instrument instead, keeping its clips and effects. It writes
   a new `.vstpreset` and never overwrites the source or the template. If Live is not
   available, report the `.SerumPreset` path and that the load did not happen. Listening
   remains the user's step.
10. To fine-tune by ear afterwards with every knob reachable, load it into a LiveBridge Host
   instead: `live_plugin_host_load(plugin="Serum 2")`, then
   `live_plugin_host_preset(source_path=output)` (same packing), then
   `live_plugin_host_parameters` / `live_plugin_host_set`; `live_plugin_host_bank` makes the
   knobs you will automate into Live dials. Experimental (Max for Live).

Suggestion-only requests permit reads, not file writes. `live_preset_load` is the
way a file gets into Live.

## Existing preset edits

Requests such as “make this synth preset faster”, “make it darker”, “make it
bouncy”, “give this preset a softer attack” or “edit this preset in this way”
are instructions to **apply** changes. Do not ask again whether to perform the
already requested edit. “Suggest how to make it darker” remains read-only.
Use the existing patch as the baseline, not Init or a replacement reference patch.
This is a targeted transformation, not the full redesign expected for a new preset.

### Identify the real source

Use the user's file path or the unambiguously identified preset from context.
Discover files with `live_preset_find` when needed, then validate the candidate.
If “this synth” refers to the loaded instrument, obtain an export of its **current
state** before the JSON workflow; an old same-named library preset may omit
unsaved changes. These tools cannot automatically export a running instance.
If a current export or source identity is missing, inspect available context and
ask only for that missing file/identity/export. Do not silently edit a different
preset or call a library copy the current instance. A supplied file-only request
does not require Live reads.

### Translate the adjective into patch edits

Inspect source/routing, envelopes, LFOs, matrix and FX before choosing controls.
State a brief interpretation and proceed with reasonable context-based choices.
Ask only when materially different interpretations cannot be resolved from the
patch/request; do not invent an audible observation.

| Request | Typical relevant changes, depending on the inspected patch |
|---|---|
| Faster / tighter | Shorter attack/decay/release or delay/reverb tails; faster LFO/sequence motion when rhythmic speed is intended; retain correct sync/retrigger and routes |
| Darker | Lower the relevant filter/EQ high-frequency range, reduce bright WT/warp/noise contributions or excessive bright FX; reconcile cutoff modulation so it does not undo the darker base setting |
| Bouncy | Shape amp and timbre envelopes for a defined transient/body/gap; align retriggered LFO/envelope movement and modulation depth to the rhythm, preserve intended sustain and glide |
| Softer / smoother | Reduce abrupt transients or harsh warp/distortion/resonance, smooth modulation, adjust envelope curves and tails where relevant |

These are design choices, not fixed parameter recipes. “Faster” does not authorize
changing the song's global tempo; “darker” does not automatically mean transposing
the notes down. Use native file values with verified semantics/conversions and
coherent routes. Keep unrelated sources, asset data, FX, macros, voicing and
automation intact unless the requested transformation needs a specific change.
Explain each intended change's purpose and record before/target values.

### Automatic JSON round trip

Use this default sequence for an identified existing `.SerumPreset`. All paths
are on the MCP server filesystem. Choose unused sibling/workspace output names
(for example `Name.source.json`, `Name.darker.json`, `Name.darker.SerumPreset`)
and avoid overwriting previous results. Keep large assets on disk.

1. `live_preset_inspect(file_path=source_preset)` records the original SHA256,
   version and dependencies.
2. `live_preset_convert(file_path=source_preset, output_path=source_json,
   output_format="json", expected_sha256=original_sha)` unpacks **complete** state.
3. Inspect `source_json` with info/read/search. Record its own SHA256, actual
   relevant paths, before values and complete coupled structures. Bounded previews
   are inspection aids, never a replacement for the full exported JSON.
4. Apply planned operations with `live_preset_edit(file_path=source_json,
   expected_sha256=source_json_sha, operations=..., output_format="json",
   output_path=edited_json)`: dry-run first, then repeat with `dry_run=false`.
   This edits complete on-disk JSON while preserving unrelated state and assets.
   The exported source JSON remains untouched. A local script can instead edit
   the complete document into a separate JSON when tool limits require it;
   preserve the `metadata`/`data` structure and source hash checks.
5. Inspect `edited_json` for **its own** SHA256, then
   `live_preset_convert(file_path=edited_json, output_path=edited_preset,
   output_format="preset", expected_sha256=edited_json_sha)` repacks it.
   Do not reuse the binary or source-JSON hash for the edited JSON.
6. `live_preset_compare(left_path=source_preset, right_path=edited_preset)`
   verifies all changes match the requested transformation and preserves unrelated
   state; inspect every page. Compare `edited_json` against `edited_preset` too:
   their decoded states must be equal. Recheck the original SHA256 to confirm
   it stayed unchanged. No-change repackaging or a dry-run alone is not an edit.
7. Deliver the edited preset path, concise change recipe, asset dependencies and
   file verification status. For live use, load the output through an independently
   verified mechanism or give the explicit loading step; verify/audition separately.
   Keep the `Serum 2 — manual settings` handoff with encoded-file versus unencoded
   settings distinguished as described in `preservation-and-handoff`.

If lossless JSON export is blocked by opaque CBOR types, report that specific
limitation and use guarded binary `live_preset_edit` for supported changes rather
than discard/approximate unknown data. Be explicit that this exceptional output
was edited directly, not through JSON. Missing control semantics/assets block
only their dependent edits; finish supported independent changes and identify
any resulting partial deliverable accurately.

## New preset design

Translate the requested style into audible design decisions: source spectrum,
register, attack/body/tail, movement, space and performance. An entirely new
preset has to be an entirely new design. An Init preset or another existing preset
(`live_preset_find`) may be the starting file, and the result is always saved as a new
file. Changing one or two parameters from that baseline is not a new preset; keep
editing before `live_preset_load`. Choose a baseline for
its useful architecture, not just its name. A styled new patch normally needs
substantial changes across sources, filtering, envelopes, modulation, FX and
voicing. Most of the defining settings must be deliberately designed and edited.
There is no numeric edit quota beyond that floor: a simple dry bass may
intentionally omit noise and reverb, while an evolving pad can need several
envelopes/LFOs and FX blocks. "Make this preset darker" is an existing-preset
edit, not a request to redesign every module.

Before writing, make a coverage table. For **every row**, record intended design,
actual discovered file pointers and one of: edit, retain from named baseline
(reason), off/unused, or blocked (specific missing mapping/asset). Then perform
the planned edits. Unchanged defaults are acceptable only when they serve the
design; unexplored template state is not a design decision.

| Category | Decisions and edits to account for |
|---|---|
| OSC A/B/C | Enable and oscillator mode; actual wavetable/sample selection; position, warp type/amount and dependencies; octave/semitone/fine; level/pan; unison/detune/blend/width; phase/randomness |
| SUB and NOISE | Enable/off; sub shape, octave, level and routing; noise asset, pitch/tracking, one-shot/loop, start/randomness, level and routing |
| FILTER 1/2 and mixer | Enable/type, cutoff/resonance/drive/VAR/mix/tracking; source destinations, series/parallel paths, direct bypass and bus sends/returns |
| ENV 1–4 | Amplitude shape plus independent timbre/pitch/FX envelopes as needed; attack/hold/decay/sustain/release, segment curves and retrigger; destination routes for every additional active envelope |
| LFOs and curves | Shape/points, rate, sync/division, phase, Free/Retrig/Envelope behavior, smoothing/delay/rise/loop; multiple independent motions where useful |
| Matrix and performance | Every source → destination, amount/polarity/curve; optional auxiliary source/scaling; envelope/LFO-to-parameter and source-rate modulation, macros/velocity/wheel/aftertouch with initial values and endpoints |
| FX racks | MAIN/BUS 1/BUS 2 routing; ordered slots, repeated effects, type/algorithm, enable/bypass and every intended setting; wet/dry vs send, tempo sync/times, feedback, gain/output |
| Voicing/global | Mono/poly/count, legato/retrigger, glide, bend ranges, tuning, master level; ARP/CLIP on/off; retain build/compatibility fields unless deliberately supported |
| Identity/assets | New name/description in intended metadata/data copies; dependencies embedded or resolved; separate output path |

Do not claim modulation is implemented merely because an envelope/LFO exists.
Connect it with a real matrix route; a macro knob without assignments does nothing
to its proposed destinations. Do not add every FX or modulation source to satisfy
the checklist. Design the signal path coherently and disable/remove unwanted
inherited routes/FX with the same care as adding new ones.

Execution order: sources and modes → routing/filters → envelopes/LFOs → ordered
FX → matrix/performance → voicing/identity. Resolve dependencies before routes.
Batch supported changes, dry-run, publish with `dry_run=false`, then compare all
relevant pages/modules against both the original and the design coverage table.
Verify that changes extend beyond metadata and actually implement the requested
style. If a feature is blocked, complete independent edits and state the precise
gap. A partial file must be described as partial, not as the finished requested sound.

## Module editing

The following is an **observed structural map**, not a universal schema or a
GUI-unit conversion table. It comes from Serum 2.0.16 fixture pairs of the
serum-preset-packager project (references the upstream author had locally, not files
LiveBridge ships). Discover the target's own structure first.
Read the matching JSON and `.SerumPreset`; folder name `checked_` alone does not
prove compatibility, asset availability, audible quality or enum meaning.

| Observed pointer/module | State to inspect and edit together |
|---|---|
| `/data/Oscillator0` … `Oscillator2` | Generator `plainParams` plus matching `WTOsc0` … `WTOsc2` or alternate mode subtree; observed WT fields include `relativePathToWT`, `numChannels`, `numFrames`, `sampleRate`, `flex`, and WT `plainParams` |
| `/data/Oscillator3/NoiseOsc3` | Noise source descriptor and controls; parent carries generator enable/level/pitch |
| `/data/Oscillator4/SubOsc4/plainParams` | Sub shape such as observed `kParamShape`; parent carries octave/volume/enable |
| `/data/VoiceFilter0/plainParams`, `VoiceFilter1` | Voice filter `kParamType`, `kParamFreq`, `kParamReso`, `kParamDrive`, `kParamVar`, `kParamEnable` where present; not the top-level `Filter` UI node |
| `/data/RoutingSlotN/plainParams` | Discovered routes, e.g. `kParamRoutingDest`; establish slot-to-source/destination mapping rather than assuming N is the oscillator index |
| `/data/Env0` … `Env3` | Envelope parameter objects with `kParamAttack`, `kParamHold`, `kParamDecay`, `kParamSustain`, `kParamRelease`, `kParamCurve1/2/3` where present |
| `/data/LFO0` … | `plainParams` plus `curveData`/`pathData`; observed `kParamMode` strings `Free` and `Envelope`, `kParamBeatSync`/`kParamRate`, and curve counts/coordinates/curvatures/loopback |
| `/data/ModSlotN` | `source`, destination type/module ID/parameter ID/name, and `plainParams` amount/bipolar/other controls; inspect full routes, not only the amount |
| `/data/FXRack0/FX`, `FXRack1/FX`, `FXRack2/FX` | Ordered arrays of complete slot objects: `type`, named `FX...` subtree and any extra curves/UI/routing/enable fields; verify rack-to-UI mapping for the build |
| `/data/Global0/plainParams` | Observed `kParamMonoToggle`, `kParamLegato`, `kParamPolyCount`, `kParamPortamentoTime`, `kParamMasterVolume`; inspect `VoicePanel0` and other global modules when relevant |

### Selecting sources and filters

Change wavetable **selection**, not just `kParamTablePos`. Prefer a compatible
reference's complete WT asset descriptor with its known asset, then deliberately
edit supported position/warp controls. Preserve/reconcile associated embedded
data and frame/channel/rate fields; never change only a filename while leaving
another asset's descriptor. Similarly, oscillator mode changes require their
selector, mode subtree, generator controls and routing, not just a new subtree.
Do not copy the entire generator accidentally when only its asset is needed.

`Bass - 777` contains `/Analog/SawRounded.wav`, WT warp label `kPWM`, voice
filter label `HEQ12`, and sub shape label `kRoundRect`. `Atmos__Temple` contains
`Analog/MATRIXY C64.wav`, `Vowel/E_Yeah.wav`, and voice filter label `MgL18`.
These are observed file labels, not confirmed installed menu names or asset
paths on another machine. Use them only with validated compatible descriptors
and dependencies. Treat an unavailable asset as a specific blocker.

### Multiple envelopes and modulation

Inspect all active `ModSlot` objects, macros and source modules. Reuse a compatible
complete known route for the intended source/destination pair, then change only
supported fields. Numeric `source` entries and `destModuleParamID` are not
self-documenting. A matching `destModuleParamName` alone does not validate the
other IDs. For a new pair, find a reference with that exact relationship or use
a controlled export; do not invent source/auxiliary IDs.

`Atmos__Temple` demonstrates distinct `Env0` and `Env1` shapes, 13 populated
matrix slots, WT-position and filter-frequency destinations, and two routes
targeting `LFO0` rate. For example, the observed filter route is:

```json
{
  "destModuleID": 0,
  "destModuleParamID": 3,
  "destModuleParamName": "kParamFreq",
  "destModuleTypeString": "VoiceFilter",
  "plainParams": {"kParamAmount": 14.03508186340332},
  "source": [3, 0]
}
```

This is a structural example, not a decoded claim about source `[3, 0]` or cutoff
endpoints. Preserve the complete relationship when reusing it; establish source
meaning and depth conversion before describing it as a particular UI route.
Envelopes, LFO rates, source scaling and route amounts must suit the new design.
For auxiliary/scaled sources or modulation of another modulator's parameter,
inspect a compatible example's entire relationship rather than reducing it to
a generic source/destination guess. Curves must retain coherent counts and arrays;
the fixtures' `numPoints` is not always the coordinate-array length. Learn that
convention before changing curve topology; never automatically "fix" the count.

### Building ordered and repeated FX

`Chord - Dream Saw` has six FXRack0 slots: FXHyperD → FXFlanger → FXReverb →
FXDelay → FXChorus → FXEQ. `Lead - Kenny` has FXDistortion → FXComp → FXReverb
→ FXDelay. `Bass - 777` has FXDistortion → FXEQ → FXFilter. These demonstrate
architecture choices; they are not chains to paste unchanged into every style.

To add an effect, read a compatible reference's **complete slot object** at
`/data/FXRackN/FX/i`. Copy it as an array `add`, then edit its named subtree,
algorithm and parameters intentionally. For distortion the inspected object
includes more than `FXDistortion/plainParams`: retain its `type`, `flex` and
other required slot fields. `type` numbers are not a universal effect catalogue.
Two instances of an effect need separate slot objects and separate settings.

Plan array operations in order: removals shift later indices; append via `/-`
or replace the complete array only when you have the full unabridged value.
Reinspect the final order. When inserting/removing/reordering or moving FX
between racks, reconcile every modulation destination targeting those effects;
do not assume `destModuleID` is a stable array index or globally unique ID.
Keep the topology unchanged if addressing cannot be established, and report
only that specific blocked topology change while applying independent edits.

### Concrete edit batch pattern

After inspection, a multi-module operation batch can test then replace actual
envelope, oscillator, filter, FX and global fields; add complete verified route
objects at unused slots and complete FX slots at known racks. For each operation
use the target's exact pointers and raw file values, not a generic hard-coded
schema. When `plainParams` is `"default"`, replace it with an object containing
only intentionally known overrides, leaving unknown omitted defaults alone.

For large complete asset/module copies exceeding tool limits, use
`live_preset_convert(..., output_format="json")`, a local script editing that
complete document into a **separate JSON**, then convert it to a separate preset.
Pass the inspected source `expected_sha256` to extraction. After the local edit,
inspect the separate edited JSON and pass **its** SHA256 to packaging; also
recheck the original if the design depends on it remaining unchanged. Avoid
output overwrites and compare the final binary's decoded state. Full JSON only works for states the
codec can represent losslessly; keep opaque CBOR state on the binary-edit path.

## Tools and paths

All but `live_preset_load` are offline; paths refer to the **MCP server's filesystem**. Inputs
may be binary presets or JSON exports with exactly `metadata` and `data` objects. Legacy `.fxp`
and other Xfer file types are rejected.

| Tool | Purpose |
|---|---|
| `live_preset_find` | Presets by name/folder words; the Serum 2 templates in the User Library |
| `live_preset_inspect` | Compact metadata, module paths and source SHA256 |
| `live_preset_read` | Paginated children/scalar at an exact JSON Pointer |
| `live_preset_search` | Literal paths/values; omit large arrays by default |
| `live_preset_edit` | Guarded add/replace/remove/test/copy batch; preview or output |
| `live_preset_convert` | Complete JSON extraction or binary packaging on disk |
| `live_preset_compare` | Paginated decoded changes, optionally scoped |
| `live_preset_load` | Pack into a `.vstpreset` and load it as Serum 2 on a track |

JSON Pointer root is `""`, with `/metadata` and `/data`. Escape literal slash in
a key as `~1`, tilde as `~0`; arrays use zero-based indices. Discover paths first.
Observed Serum 2.0.14 fixture paths include `/data/Env0/plainParams/kParamDecay`,
`/data/Oscillator0/SampleOsc0/embedded_audio/data/0`, `/data/LFO0/curveData` and
`/data/FXRack0/FX/0/FXDistortion/plainParams`.

Previews are **not replacement values or a complete export**; never write them
back as a module/asset. Expand their path; channel arrays permit sample slices
with offset/limit. Search skips scalar arrays >64 entries unless
`include_array_values=true`. Its skipped list only covers visited arrays, not an
exhaustive asset inventory. Paginate `/data` for the full module inventory.

Limits: input/decoded CBOR <=256 MiB, metadata <=4 MiB, page <=100, depth <=5,
nested preview budget 400 nodes, response <=32000 characters. Omitted oversized
details require smaller limit/depth or narrower scope. Edits <=100 operations
and <=1 MiB. Large asset edits can use complete disk JSON followed by conversion.

## Complete state recipes

**File-state editing is available for every addressable decoded field**, not
only Configure parameters. FX controls/order, matrix routes, wavetable selection
and embedded data, oscillator modes, filters, multiple envelopes/LFOs, curves,
sub/noise, voicing, routing, performance, ARP/CLIP and future/unknown modules use
the same inspection → JSON edit → repack workflow. Lack of live readback/audition
does not mean those file edits are missing. It limits the verification claim.
Unknown fields are preserved; interpreting undocumented semantics still requires
evidence. Do not invent a complete GUI mapping for every future Serum build.

### Copy complete state without putting it in chat

`live_preset_edit` supports `copy` in addition to add/replace/remove/test. It
copies a full decoded subtree directly from disk, even when the source contains
huge embedded arrays or binary CBOR values. It never copies a preview. Use
`from` and `path`; for a reference file also supply `source_file_path` and that
file's inspected `source_sha256`. Both source fields are required together.
Omit both to copy from the document as it stands at that point in the batch.

Example operations, with **actual discovered paths and hashes substituted**:

```json
[
  {
    "op": "copy",
    "source_file_path": "REFERENCE.SerumPreset",
    "source_sha256": "REFERENCE_SHA256_FROM_INFO",
    "from": "/data/Oscillator0/WTOsc0",
    "path": "/data/Oscillator0/WTOsc0"
  },
  {
    "op": "copy",
    "source_file_path": "REFERENCE.SerumPreset",
    "source_sha256": "REFERENCE_SHA256_FROM_INFO",
    "from": "/data/FXRack0/FX/0",
    "path": "/data/FXRack0/FX/-"
  },
  {
    "op": "copy",
    "source_file_path": "REFERENCE.SerumPreset",
    "source_sha256": "REFERENCE_SHA256_FROM_INFO",
    "from": "/data/ModSlot0",
    "path": "/data/ModSlot0"
  }
]
```

These are mechanical examples, not a coherent patch to apply blindly. Inspect
the donor's build, assets, module IDs, destinations and route meanings first.
Copies into object keys set/replace; copies into arrays **insert**, shifting
later indices; `/-` appends. To replace a whole FX array copy into its parent
object key `/data/FXRackN/FX`. Parents must exist. Donor hashes are checked on
read and again after encoding before output publication. A missing subtree,
bad hash or later failed operation prevents the entire batch from being written.

Use `output_format="json"` on the unpacked source JSON, make the copies and
targeted edits, then repack the separate edited JSON. A binary donor can supply
state to a JSON target when that state is JSON-representable. If the copied state
contains opaque CBOR values, JSON encoding rejects it rather than silently
discarding data; use `output_format="preset"` for the disclosed binary fallback.
Complete copies do not need huge `value` arrays and therefore avoid the 1 MiB
operation-payload limit; preset size/operation-count limits still apply.

### FX racks and controls

Inspect every active rack's full slot order, effect names and `plainParams`.
For existing effects replace/add exact controls at their actual pointers, e.g.
`/data/FXRack0/FX/0/FXDistortion/plainParams/kParamWet` if discovered. Algorithm
selectors, enables, sync modes, timing, feedback, wet/output levels and effect
curves are file-editable too. Use file-native values, never guessed host scaling.

For a new/repeated module, copy a complete compatible slot, then override its
intended settings with ordered operations. For rebuilding a chain, copy the
complete verified FX array and edit it; do not replace it with a truncated read
preview. Separate occurrences require their own controls. Reordering or moving
effects can change matrix addresses: inspect and reconcile dependent routes
before claiming the chain is complete. The copy tool does not remap IDs.
Rack destinations/sends and parallel wet-only versus insert wet/dry belong in
the same design. Preserve unknown effect fields and all unchanged rack state.

### Matrix and performance routes

Read complete active `ModSlotN` objects, source modules, macros and any auxiliary
controls. Editing amounts, bipolar/auxiliary curves/scaling, bypass and established
source/destination fields is supported in JSON. To create a new relationship,
copy a compatible full route into an inspected unused slot, then deliberately
change established destination/source fields and amounts. Source and auxiliary
numeric codes, module IDs and parameter IDs require a verified mapping or a
matching known relationship. Renaming `destModuleParamName` alone is insufficient.

Set the destination's base value and the source's envelope/LFO/macro settings
alongside the route. Modulation of LFO rate or another modulator is a real route,
not just a different source knob value. Multiple envelopes/LFOs and many routes
can be edited in one batch. Preserve intended velocity/wheel/MPE/aftertouch and
macro assignments; changing only a macro value does not author its assignments.

### Wavetables and oscillator modes

For selection changes copy a compatible **complete WT subtree/asset descriptor**
into the matching target mode subtree. This transfers the selected table plus
embedded samples, frame/channel/rate information, curves and unknown fields.
Then edit its position/warp controls and parent generator settings deliberately.
Changing only `relativePathToWT` or only `kParamTablePos` does not establish a
coherent source change. External dependencies still need to resolve on the user's
machine. Default waveform edits, sample/granular/spectral/multisample modes and
NOISE assets follow the same complete-subtree approach with their own mode
selectors and routing. A mode subtree existing in JSON does not prove it is active.

### Other preset features

ENV/LFO curves: edit established scalar controls directly. For new curve shapes,
copy compatible complete `curveData`/`pathData`, preserving count/point/curvature/
loopback conventions, then adjust supported coordinates/controls. Changing one
array without its coupled topology fields is not a valid shape edit.

SUB/NOISE, filters and voicing: inspect parent generator and mode controls,
`VoiceFilterN`, `RoutingSlotN`, `Global0` and related modules; edit all intended
enable/type/pitch/level/filter/mono/poly/glide/retrigger settings. Do not leave a
new source disabled or a designed envelope disconnected. Replace `plainParams`
sentinel `"default"` with an object only for known overrides; absent values are
not known numeric defaults.

ARP/CLIP, tuning, expression and additional features: inspect their actual
modules/subtrees, copy complete compatible structures when useful and edit the
selectors/controls/dependencies that activate them. Do not strip unrecognized
fields or assume these modules are outside the editor's scope. If semantics
remain unknown, retain that state and identify the specific unsupported design
choice, while finishing independent edits.

Completion: compare all touched modules and dependencies against the design,
verify edited JSON and repacked preset have equal decoded state and preserve
input hashes. Report **file-applied and decoded-verified** edits separately from
live-loaded/read-back/auditioned state. Pending live verification is not a reason
to call encoded FX/matrix/WT edits unimplemented or ask for manual recreation.

## Editing semantics

Illustrative single-control operation, not a complete new-preset design or a
universal patch recommendation. New designs need the multi-module workflow above:

```json
[
  {"op":"test","path":"/data/Env0/plainParams/kParamDecay","value":0.5039405578894977},
  {"op":"replace","path":"/data/Env0/plainParams/kParamDecay","value":0.6}
]
```

`copy` reads complete current/reference state as described in `complete-state-recipes`.
`replace`/`remove` require an existing target. Object `add` sets/replaces a key;
list `add` inserts, shifting indices; `/-` appends. Parents must exist. Operations
run in order. A failed operation/test prevents all output. SHA256 guards stale
reads and is checked again before publication; reinspect after external edits.

`plainParams` may be the string `"default"`, not known numeric defaults. To add
a verified parameter, explicitly replace that node with an object, then add the
key. Absent keys retain Serum's interpretation; their values are not inferred.
Coupled selectors, routes, sample metadata and curve counts must be updated
coherently. Valid CBOR alone does not establish a semantically valid patch.
Learn unfamiliar controls using controlled GUI before/after exports and compare
relevant modules; separate GUI noise, verify nonlinear conversions at multiple
values, and never guess enum/FX IDs.

Identity/version fields can appear in both metadata and data. Read both and
explicitly update intended copies; no automatic synchronization. Metadata `hash`
is preserved as opaque; no vendor checksum algorithm is inferred.

## Preservation and handoff

Binary edits preserve decoded fields including unknown CBOR types and assets.
Re-encoding may change bytes, ordering/float encoding and size; original byte
layout is not promised. JSON export rejects types it cannot retain (bytes, tags,
non-string keys, sets/tuples, nonfinite numbers). Use binary edits instead; never
replace an opaque subtree with a JSON approximation.

The user's fixture has stereo audio: 140544 samples/channel at 48000 Hz. Original,
JSON and repackaged binary have identical decoded state. This establishes local
fixture compatibility, not every version's schema or live load validity.

Final sound-design handoff: distinguish generated file/verified decoded edits
from loaded-instance writes. Include `Serum 2 — manual settings` with loading,
verification and every intended setting unapplied to the instance. File modulation
remains unapplied to the running instrument until loaded and checked.
For a file-only request use `Track/device: not applicable — offline preset`.
Report the output path, source/build, dependencies and full patch recipe. Group
settings already encoded in the verified file as **encoded in output; not loaded**
until `live_preset_load` reports `loaded: true`. Then group them as
**loaded with preset; not individually read back** and do not instruct the user to
recreate that state knob by knob. True missing/unencoded settings still need exact
UI locations/values and `not applied — set manually` status. If no live loading was
requested, state that clearly while retaining the manual-settings section; do not
call encoded modulation missing merely because no instance was requested.

## Loading

`live_preset_load(source_path=...)` packs the `.SerumPreset` into a new same-build
`.vstpreset` and loads it through Live's browser:

- **Template**: a `.vstpreset` saved from the user's own Serum 2 (in Live: the plug-in device's
  title bar -> Save Preset, into the User Library). It supplies the VST3 class id and the
  processor/controller schemas. LiveBridge finds the newest one of the preset's Serum build in
  the User Library (`live_preset_find` lists them); `template=` picks another. A different
  build is refused — no cross-version packing.
- **Output**: `User Library/LiveBridge Presets/Serum 2/<preset name>.vstpreset`. The same
  preset again reuses its file; a different one with the same name gets a short hash; nothing
  is overwritten. Source and template stay unchanged.
- **Load**: a new MIDI track named after the preset, or `track=` to replace that track's
  instrument (clips and effects stay; `replaced` names the old instrument). Live may need a
  moment to list the new file — the tool retries for a few seconds; otherwise the file is saved
  and the user can drag it from the browser.

`loaded: true` means a Serum 2 device appeared. Live reads none of the preset back: wavetables,
matrix routes and FX are **loaded with preset; not individually read back**, and Serum's knobs
stay hidden from Live until configured (`SERUM.md`: `live_plugin_expose` loads its own rack
patch, so expose first and design the file afterwards, or keep working on the file). No audio is
auditioned. Live must run on the machine with the MCP server (the file is written there).

Upstream verified this path once (Serum 2.0.16, Live 12.4, a new track). Use the browser path
the tool returns; a `.SerumPreset` path is not itself a browser item.

## Sources

Independent codec based on the container documented by
[serum-preset-packager](https://github.com/KennethWussmann/serum-preset-packager):
`XferJson\0`, uint64 little-endian metadata length, UTF-8 JSON metadata, uint32
decoded CBOR length, uint32 encoding `2`, Zstandard-compressed CBOR.
The upstream author notes that project had no license file and that no implementation was copied from it.
This is community research, not a vendor-supported preset API.
