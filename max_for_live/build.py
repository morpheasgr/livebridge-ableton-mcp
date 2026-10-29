#!/usr/bin/env python3
"""Build the LiveBridge Max for Live devices from source (standard library only).

    python max_for_live/build.py            # write the device files
    python max_for_live/build.py --check    # exit 1 when a committed file is out of date

``LiveBridge Engine`` (an audio effect) is written to ``max_for_live/LiveBridge Engine/``:

* ``LiveBridge Engine.amxd`` — the device: an ``ampf`` header (device type, meta, patch size)
  followed by the patcher JSON and a NUL byte, the layout Live writes for an unfrozen device.
* ``lb_engine.gendsp`` — the gen~ patcher with the analysis code (GenExpr, generated below).
* ``engine.js`` (Node for Max) and ``context.js`` (Max js) are hand-written sources next to it.

``LiveBridge MIDI`` (a MIDI effect) is written to ``max_for_live/LiveBridge MIDI/``: the
``.amxd`` and ``lbmidi_context.js``, a copy of the Engine's ``context.js`` under its own name
(Max finds js files by name along its search path, so two devices must not share one).
``lbmidi.js`` (Node for Max) is hand-written next to it.

The device passes audio through unchanged and measures it: per channel mean square and peak,
BS.1770 K-weighted power (loudness), 8 band powers matching ``audio_analysis.BANDS``, mid/side
and left*right (stereo width and correlation) and the low end's mid/side below 120 Hz. A metro
reads the 18 values every 100 ms and hands them to engine.js.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "mcp_server"))
from livebridge_mcp import host_device  # noqa: E402
ENGINE_DIR = HERE / "LiveBridge Engine"
ENGINE_AMXD = ENGINE_DIR / "LiveBridge Engine.amxd"
ENGINE_GENDSP = ENGINE_DIR / "lb_engine.gendsp"
ENGINE_CONTEXT = ENGINE_DIR / "context.js"
MIDI_DIR = HERE / "LiveBridge MIDI"
MIDI_AMXD = MIDI_DIR / "LiveBridge MIDI.amxd"
MIDI_CONTEXT = MIDI_DIR / "lbmidi_context.js"
HOST_DIR = HERE / "LiveBridge Host"
HOST_EFFECT_AMXD = HOST_DIR / "LiveBridge Host Effect.amxd"
HOST_INSTRUMENT_AMXD = HOST_DIR / "LiveBridge Host Instrument.amxd"
HOST_CONTEXT = HOST_DIR / "lbhost_context.js"
PHASE_DIR = HERE / "LiveBridge Phase"
PHASE_AMXD = PHASE_DIR / "LiveBridge Phase.amxd"
PHASE_GENDSP = PHASE_DIR / "lb_phase.gendsp"
CHANNEL_DIR = HERE / "LiveBridge Channel"
CHANNEL_AMXD = CHANNEL_DIR / "LiveBridge Channel.amxd"
CHANNEL_NODE = CHANNEL_DIR / "lbchannel.js"
CHANNEL_CONTEXT = CHANNEL_DIR / "lbchannel_context.js"
CHANNEL_GENDSP = CHANNEL_DIR / "lb_engine.gendsp"
MASTER_DIR = HERE / "LiveBridge Master"
MASTER_AMXD = MASTER_DIR / "LiveBridge Master.amxd"

AUDIO_EFFECT = 0x61616161          # 'aaaa'
MIDI_EFFECT = 0x6D6D6D6D           # 'mmmm'
INSTRUMENT = 0x69696969            # 'iiii'
FRAME_MS = 100
MOD_SLOTS = 8                       # live.remote~ objects (engine.js MOD_SLOTS)
APP_VERSION = {"major": 9, "minor": 0, "revision": 7, "architecture": "x64", "modernui": 1}

#: (name, low Hz, high Hz) — the same bands as livebridge_mcp.audio_analysis.BANDS.
BANDS = (("sub", 20, 60), ("bass", 60, 120), ("low_mid", 120, 250), ("mud", 250, 500),
         ("mid", 500, 2000), ("presence", 2000, 5000), ("high", 5000, 10000),
         ("air", 10000, 20000))
#: Outlet order of the gen~ object = the frame fields of engine.js.
OUTPUTS = ("msl", "msr", "pkl", "pkr", "msk") + tuple(f"msb{i}" for i in range(len(BANDS))) \
    + ("msm", "mss", "mlr", "msml", "mssl")

# BS.1770 K-weighting (high shelf + high pass), defined for any sample rate.
K_SHELF = {"f0": 1681.974450955533, "gain_db": 3.999843853973347, "q": 0.7071752369554196}
K_HIGHPASS = {"f0": 38.13547087602444, "q": 0.5003270373238773}
LOW_END_HZ = 120.0


# ----------------------------------------------------------------------------- coefficients

def k_shelf(fs: float) -> tuple[float, float, float, float, float]:
    """(b0, b1, b2, a1, a2) of the K-weighting shelf at sample rate fs."""
    k = math.tan(math.pi * K_SHELF["f0"] / fs)
    vh = 10 ** (K_SHELF["gain_db"] / 20)
    vb = vh ** 0.4996667741545416
    q = K_SHELF["q"]
    a0 = 1 + k / q + k * k
    return ((vh + vb * k / q + k * k) / a0, 2 * (k * k - vh) / a0,
            (vh - vb * k / q + k * k) / a0, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0)


def k_highpass(fs: float) -> tuple[float, float, float, float, float]:
    k = math.tan(math.pi * K_HIGHPASS["f0"] / fs)
    q = K_HIGHPASS["q"]
    a0 = 1 + k / q + k * k
    return 1.0, -2.0, 1.0, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0


def bandpass(fs: float, low: float, high: float) -> tuple[float, float, float, float, float]:
    """RBJ band-pass (0 dB peak) centred on the band's geometric middle."""
    f0 = min(math.sqrt(low * high), 0.45 * fs)
    q = f0 / (high - low)
    w0 = 2 * math.pi * f0 / fs
    alpha = math.sin(w0) / (2 * q)
    a0 = 1 + alpha
    return alpha / a0, 0.0, -alpha / a0, -2 * math.cos(w0) / a0, (1 - alpha) / a0


def lowpass(fs: float, f0: float) -> tuple[float, float, float, float, float]:
    w0 = 2 * math.pi * f0 / fs
    alpha = math.sin(w0) / (2 * 0.7071067811865476)
    a0 = 1 + alpha
    cos = math.cos(w0)
    return (1 - cos) / 2 / a0, (1 - cos) / a0, (1 - cos) / 2 / a0, -2 * cos / a0, \
        (1 - alpha) / a0


# ----------------------------------------------------------------------------- GenExpr

#: GenExpr for each coefficient set: the same formulas as above, in terms of samplerate.
_COEFF_CODE = {
    "kshelf": """\
k = tan(3.141592653589793 * {f0} / samplerate);
vh = pow(10, {gain} / 20);
vb = pow(vh, 0.4996667741545416);
a0 = 1 + k / {q} + k * k;
{p}b0 = (vh + vb * k / {q} + k * k) / a0;
{p}b1 = 2 * (k * k - vh) / a0;
{p}b2 = (vh - vb * k / {q} + k * k) / a0;
{p}a1 = 2 * (k * k - 1) / a0;
{p}a2 = (1 - k / {q} + k * k) / a0;""",
    "khigh": """\
k = tan(3.141592653589793 * {f0} / samplerate);
a0 = 1 + k / {q} + k * k;
{p}b0 = 1;
{p}b1 = -2;
{p}b2 = 1;
{p}a1 = 2 * (k * k - 1) / a0;
{p}a2 = (1 - k / {q} + k * k) / a0;""",
    "band": """\
f0 = min(sqrt({low} * {high}), 0.45 * samplerate);
w0 = 2 * 3.141592653589793 * f0 / samplerate;
q = f0 / ({high} - {low});
alpha = sin(w0) / (2 * q);
a0 = 1 + alpha;
{p}b0 = alpha / a0;
{p}b1 = 0;
{p}b2 = 0 - alpha / a0;
{p}a1 = -2 * cos(w0) / a0;
{p}a2 = (1 - alpha) / a0;""",
    "low": """\
w0 = 2 * 3.141592653589793 * {f0} / samplerate;
alpha = sin(w0) / (2 * 0.7071067811865476);
a0 = 1 + alpha;
{p}b0 = (1 - cos(w0)) / 2 / a0;
{p}b1 = (1 - cos(w0)) / a0;
{p}b2 = (1 - cos(w0)) / 2 / a0;
{p}a1 = -2 * cos(w0) / a0;
{p}a2 = (1 - alpha) / a0;""",
}


def _filter(state: str, coeffs: str, x: str, y: str) -> list[str]:
    """Direct form I biquad: y from x with its own state (History) and shared coefficients."""
    return [
        f"{y} = {coeffs}b0 * {x} + {coeffs}b1 * {state}x1 + {coeffs}b2 * {state}x2"
        f" - {coeffs}a1 * {state}y1 - {coeffs}a2 * {state}y2;",
        f"{state}x2 = {state}x1;", f"{state}x1 = {x};",
        f"{state}y2 = {state}y1;", f"{state}y1 = {y};",
    ]


def genexpr() -> str:
    """The analysis code of the gen~ object (codebox)."""
    coeff_sets = [("ks_", "kshelf", {"f0": K_SHELF["f0"], "gain": K_SHELF["gain_db"],
                                      "q": K_SHELF["q"]}),
                  ("kh_", "khigh", {"f0": K_HIGHPASS["f0"], "q": K_HIGHPASS["q"]}),
                  ("lp_", "low", {"f0": LOW_END_HZ})]
    coeff_sets += [(f"b{i}_", "band", {"low": low, "high": high})
                   for i, (_name, low, high) in enumerate(BANDS)]
    filters = ["ksl_", "khl_", "ksr_", "khr_", "lpm_", "lps_"] + \
        [f"bf{i}_" for i in range(len(BANDS))]
    meters = ["msl", "msr", "pkl", "pkr", "msk", "msm", "mss", "mlr", "msml", "mssl"] + \
        [f"msb{i}" for i in range(len(BANDS))]
    lines = ["// LiveBridge Engine analysis — generated by max_for_live/build.py, do not edit.",
             "// Inputs: left, right. Outputs (see OUTPUTS in build.py): mean squares, peaks,",
             "// K-weighted power, 8 band powers, mid/side/left*right, low-end mid/side.", ""]
    lines += [f"History {p}{c}(0);" for p, _kind, _v in coeff_sets
              for c in ("b0", "b1", "b2", "a1", "a2")]
    lines += [f"History {f}{s}(0);" for f in filters for s in ("x1", "x2", "y1", "y2")]
    lines += [f"History {m}(0);" for m in meters]
    lines += ["History cfast(0);", "History cmom(0);", "History pdecay(0);",
              "History ready(0);", ""]
    lines += ["if (ready == 0) {"]
    for prefix, kind, values in coeff_sets:
        code = _COEFF_CODE[kind].format(p=prefix, **{k: repr(float(v)) for k, v in values.items()})
        lines += ["    " + line for line in code.splitlines()]
    lines += ["    cfast = 1 - exp(-1 / (0.1 * samplerate));",
              "    cmom = 1 - exp(-1 / (0.4 * samplerate));",
              "    pdecay = exp(-1 / (0.3 * samplerate));",
              "    ready = 1;", "}", ""]
    lines += ["l = in1;", "r = in2;",
              "msl = msl + cfast * (l * l - msl);", "msr = msr + cfast * (r * r - msr);",
              "pkl = max(abs(l), pkl * pdecay);", "pkr = max(abs(r), pkr * pdecay);"]
    lines += _filter("ksl_", "ks_", "l", "kl1") + _filter("khl_", "kh_", "kl1", "kl")
    lines += _filter("ksr_", "ks_", "r", "kr1") + _filter("khr_", "kh_", "kr1", "kr")
    lines += ["msk = msk + cmom * (kl * kl + kr * kr - msk);",
              "m = (l + r) * 0.5;", "s = (l - r) * 0.5;",
              "msm = msm + cfast * (m * m - msm);", "mss = mss + cfast * (s * s - mss);",
              "mlr = mlr + cfast * (l * r - mlr);"]
    for i in range(len(BANDS)):
        lines += _filter(f"bf{i}_", f"b{i}_", "m", f"band{i}")
        lines.append(f"msb{i} = msb{i} + cfast * (band{i} * band{i} - msb{i});")
    lines += _filter("lpm_", "lp_", "m", "ml") + _filter("lps_", "lp_", "s", "sl")
    lines += ["msml = msml + cfast * (ml * ml - msml);",
              "mssl = mssl + cfast * (sl * sl - mssl);", ""]
    lines += [f"out{i + 1} = {name};" for i, name in enumerate(OUTPUTS)]
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------------- patchers

def _box(box_id: str, text: str, rect: list[float], inlets: int, outlets: int,
         outlettype: list[str] | None = None, **extra) -> dict:
    box = {"id": box_id, "maxclass": "newobj", "text": text, "numinlets": inlets,
           "numoutlets": outlets, "outlettype": outlettype or [""] * outlets,
           "patching_rect": rect}
    box.update(extra)
    return {"box": box}


def _line(source: str, outlet: int, destination: str, inlet: int) -> dict:
    return {"patchline": {"source": [source, outlet], "destination": [destination, inlet]}}


def gendsp(code: str | None = None, outputs: int | None = None) -> dict:
    """A gen~ patcher: two inputs into one codebox (default: the Engine's analysis)."""
    code = genexpr() if code is None else code
    outputs = len(OUTPUTS) if outputs is None else outputs
    boxes = [
        _box("obj-in1", "in 1", [20.0, 20.0, 30.0, 22.0], 0, 1),
        _box("obj-in2", "in 2", [120.0, 20.0, 30.0, 22.0], 0, 1),
        {"box": {"id": "obj-code", "maxclass": "codebox", "code": code,
                 "fontface": 0, "fontname": "<Monospaced>", "fontsize": 12.0,
                 "numinlets": 2, "numoutlets": outputs, "outlettype": [""] * outputs,
                 "patching_rect": [20.0, 60.0, 600.0, 500.0]}},
    ]
    lines = [_line("obj-in1", 0, "obj-code", 0), _line("obj-in2", 0, "obj-code", 1)]
    for i in range(outputs):
        boxes.append(_box(f"obj-out{i + 1}", f"out {i + 1}",
                          [20.0 + 34.0 * i, 580.0, 30.0, 22.0], 1, 0))
        lines.append(_line("obj-code", i, f"obj-out{i + 1}", 0))
    return {"patcher": {"fileversion": 1, "appversion": APP_VERSION,
                        "classnamespace": "dsp.gen", "rect": [50.0, 50.0, 700.0, 650.0],
                        "boxes": boxes, "lines": lines}}


def engine_patcher() -> dict:
    n = len(OUTPUTS)
    boxes = [
        _box("obj-plugin", "plugin~", [20.0, 20.0, 60.0, 22.0], 2, 2, ["signal", "signal"]),
        _box("obj-plugout", "plugout~", [20.0, 420.0, 60.0, 22.0], 2, 0),
        _box("obj-gen", "gen~ @gen lb_engine", [120.0, 60.0, 600.0, 22.0], 2, n,
             ["signal"] * n),
        _box("obj-metro", f"metro {FRAME_MS} @active 1", [760.0, 20.0, 110.0, 22.0], 2, 1,
             ["bang"]),
        _box("obj-trigger", "t " + " ".join(["b"] * n), [760.0, 60.0, 400.0, 22.0], 1, n,
             ["bang"] * n),
        _box("obj-pack", "pack " + " ".join(["0."] * n), [120.0, 180.0, 600.0, 22.0], n, 1),
        _box("obj-prepend", "prepend frame", [120.0, 220.0, 90.0, 22.0], 1, 1),
        _box("obj-node", "node.script engine.js @autostart 1 @watch 0",
             [120.0, 300.0, 280.0, 22.0], 1, 2),
        _box("obj-thisdevice", "live.thisdevice", [440.0, 220.0, 90.0, 22.0], 1, 3,
             ["bang", "int", "int"]),
        _box("obj-context", "js context.js", [440.0, 260.0, 90.0, 22.0], 1, 1),
        _box("obj-route",
             "route status log context capture resolve mod query call setvalue getvalues "
             "lomdump",
             [120.0, 340.0, 560.0, 22.0], 1, 12),
        _box("obj-setvalue", "prepend setvalue", [760.0, 180.0, 100.0, 22.0], 1, 1),
        _box("obj-getvalues", "prepend getvalues", [880.0, 180.0, 110.0, 22.0], 1, 1),
        # the API dump: "lomdump <file>" (context.js writes the file, answers "lomdumped")
        _box("obj-lomdump", "prepend lomdump", [1000.0, 180.0, 110.0, 22.0], 1, 1),
        # follow actions: "query <id> <kind> <path...>" and "call <method> <path...>"
        _box("obj-query", "prepend query", [560.0, 180.0, 90.0, 22.0], 1, 1),
        _box("obj-call", "prepend call", [660.0, 180.0, 80.0, 22.0], 1, 1),
        # parameter lookups for the modulation go to context.js as "resolve <slot> <path...>"
        _box("obj-resolve", "prepend resolve", [560.0, 220.0, 100.0, 22.0], 1, 1),
        # "mod <slot> value <v> [ms]" / "mod <slot> id <n>" -> one live.remote~ per slot
        _box("obj-modslot", "route " + " ".join(str(i) for i in range(MOD_SLOTS)),
             [700.0, 380.0, 200.0, 22.0], 1, MOD_SLOTS + 1),
        # recording for captures: "open <path>", 1 (start), 0 (stop and close) from engine.js
        _box("obj-sfrecord", "sfrecord~ 2", [20.0, 380.0, 80.0, 22.0], 2, 1, ["signal"]),
        _box("obj-set", "prepend set", [120.0, 380.0, 80.0, 22.0], 1, 1),
        _box("obj-print", "print LiveBridgeEngine", [240.0, 380.0, 140.0, 22.0], 1, 0),
        {"box": {"id": "obj-status", "maxclass": "message", "text": "starting...",
                 "numinlets": 2, "numoutlets": 1, "outlettype": [""],
                 "patching_rect": [120.0, 460.0, 200.0, 22.0], "presentation": 1,
                 "presentation_rect": [8.0, 34.0, 184.0, 22.0]}},
        {"box": {"id": "obj-title", "maxclass": "comment", "text": "LiveBridge Engine",
                 "numinlets": 1, "numoutlets": 0, "fontsize": 13.0, "fontface": 1,
                 "patching_rect": [440.0, 460.0, 150.0, 22.0], "presentation": 1,
                 "presentation_rect": [8.0, 8.0, 184.0, 22.0]}},
    ]
    lines = [
        _line("obj-plugin", 0, "obj-plugout", 0), _line("obj-plugin", 1, "obj-plugout", 1),
        _line("obj-plugin", 0, "obj-gen", 0), _line("obj-plugin", 1, "obj-gen", 1),
        _line("obj-metro", 0, "obj-trigger", 0),
        _line("obj-pack", 0, "obj-prepend", 0), _line("obj-prepend", 0, "obj-node", 0),
        _line("obj-thisdevice", 0, "obj-context", 0), _line("obj-context", 0, "obj-node", 0),
        _line("obj-node", 0, "obj-route", 0), _line("obj-route", 0, "obj-set", 0),
        _line("obj-set", 0, "obj-status", 0), _line("obj-route", 1, "obj-print", 0),
        _line("obj-route", 2, "obj-context", 0), _line("obj-route", 3, "obj-sfrecord", 0),
        _line("obj-route", 4, "obj-resolve", 0), _line("obj-resolve", 0, "obj-context", 0),
        _line("obj-route", 5, "obj-modslot", 0), _line("obj-route", 6, "obj-query", 0),
        _line("obj-route", 7, "obj-call", 0), _line("obj-route", 8, "obj-setvalue", 0),
        _line("obj-setvalue", 0, "obj-context", 0), _line("obj-route", 9, "obj-getvalues", 0),
        _line("obj-getvalues", 0, "obj-context", 0), _line("obj-route", 10, "obj-lomdump", 0),
        _line("obj-lomdump", 0, "obj-context", 0), _line("obj-route", 11, "obj-print", 0),
        _line("obj-query", 0, "obj-context", 0), _line("obj-call", 0, "obj-context", 0),
        _line("obj-plugin", 0, "obj-sfrecord", 0), _line("obj-plugin", 1, "obj-sfrecord", 1),
    ]
    for i in range(MOD_SLOTS):
        x = 700.0 + 110.0 * i
        # "value"/"id" drive live.remote~ (replace mode: takes the parameter over),
        # "offset"/"oid" drive live.modulate~ (offset mode: -1..1 around the user's value)
        boxes += [
            _box(f"obj-modroute{i}", "route value id offset oid", [x, 420.0, 100.0, 22.0], 1,
                 5),
            _box(f"obj-line{i}", "line~", [x, 460.0, 40.0, 22.0], 2, 2, ["signal", "bang"]),
            _box(f"obj-modid{i}", "prepend id", [x + 45.0, 460.0, 60.0, 22.0], 1, 1),
            _box(f"obj-remote{i}", "live.remote~", [x, 500.0, 80.0, 22.0], 2, 0),
            _box(f"obj-oline{i}", "line~", [x, 540.0, 40.0, 22.0], 2, 2, ["signal", "bang"]),
            _box(f"obj-oid{i}", "prepend id", [x + 45.0, 540.0, 60.0, 22.0], 1, 1),
            _box(f"obj-modulate{i}", "live.modulate~", [x, 580.0, 90.0, 22.0], 2, 1),
        ]
        lines += [_line("obj-modslot", i, f"obj-modroute{i}", 0),
                  _line(f"obj-modroute{i}", 0, f"obj-line{i}", 0),
                  _line(f"obj-line{i}", 0, f"obj-remote{i}", 0),
                  _line(f"obj-modroute{i}", 1, f"obj-modid{i}", 0),
                  _line(f"obj-modid{i}", 0, f"obj-remote{i}", 1),
                  _line(f"obj-modroute{i}", 2, f"obj-oline{i}", 0),
                  _line(f"obj-oline{i}", 0, f"obj-modulate{i}", 0),
                  _line(f"obj-modroute{i}", 3, f"obj-oid{i}", 0),
                  _line(f"obj-oid{i}", 0, f"obj-modulate{i}", 1)]
    # metro -> trigger fires right to left: every snapshot~ reports into the pack, the
    # leftmost (pack's hot inlet) last, so one complete frame leaves per tick
    for i in range(n):
        snap = f"obj-snap{i}"
        boxes.append(_box(snap, "snapshot~", [120.0 + 34.0 * i, 120.0, 30.0, 22.0], 2, 1,
                          ["float"]))
        lines += [_line("obj-gen", i, snap, 0), _line("obj-trigger", i, snap, 0),
                  _line(snap, 0, "obj-pack", i)]
    return {"patcher": {
        "fileversion": 1, "appversion": APP_VERSION, "classnamespace": "box",
        "rect": [80.0, 80.0, 1200.0, 560.0], "openinpresentation": 1,
        "default_fontsize": 12.0, "default_fontface": 0, "default_fontname": "Arial",
        "gridsize": [15.0, 15.0], "devicewidth": 200.0, "description":
        "LiveBridge Engine: real-time meters for the LiveBridge MCP server", "boxes": boxes,
        "lines": lines,
        "dependency_cache": [
            {"name": "engine.js", "bootpath": ".", "type": "TEXT", "implicit": 1},
            {"name": "context.js", "bootpath": ".", "type": "TEXT", "implicit": 1},
            {"name": "lb_engine.gendsp", "bootpath": ".", "type": "gDSP", "implicit": 1},
        ],
        "project": {"version": 1, "amxdtype": AUDIO_EFFECT, "readonly": 0, "devpathtype": 0,
                    "devpath": ".", "sortmode": 0, "viewmode": 0,
                    "contents": {"patchers": {}, "code": {}}, "layout": {},
                    "searchpath": {}, "detailsvisible": 0},
    }}


def midi_patcher() -> dict:
    boxes = [
        _box("obj-midiin", "midiin", [20.0, 20.0, 50.0, 22.0], 1, 1, ["int"]),
        _box("obj-midiout", "midiout", [20.0, 420.0, 55.0, 22.0], 1, 0),
        # a copy of what passes through: notes ("note <pitch> <velocity>") and controllers
        _box("obj-midiparse", "midiparse", [120.0, 60.0, 120.0, 22.0], 1, 8,
             ["", "", "", "int", "int", "int", "int", ""]),
        _box("obj-note", "prepend note", [120.0, 100.0, 85.0, 22.0], 1, 1),
        _box("obj-cc", "prepend cc", [220.0, 100.0, 75.0, 22.0], 1, 1),
        _box("obj-node", "node.script lbmidi.js @autostart 1 @watch 0",
             [120.0, 260.0, 260.0, 22.0], 1, 2),
        _box("obj-thisdevice", "live.thisdevice", [420.0, 140.0, 90.0, 22.0], 1, 3,
             ["bang", "int", "int"]),
        _box("obj-context", "js lbmidi_context.js", [420.0, 180.0, 130.0, 22.0], 1, 1),
        _box("obj-route", "route status log context play", [120.0, 300.0, 230.0, 22.0], 1, 5),
        # notes played by lbmidi.js ("play <pitch> <velocity>") go out with the input
        _box("obj-midiformat", "midiformat", [20.0, 380.0, 110.0, 22.0], 7, 1, ["int"]),
        _box("obj-set", "prepend set", [240.0, 340.0, 80.0, 22.0], 1, 1),
        _box("obj-print", "print LiveBridgeMIDI", [340.0, 380.0, 130.0, 22.0], 1, 0),
        {"box": {"id": "obj-status", "maxclass": "message", "text": "starting...",
                 "numinlets": 2, "numoutlets": 1, "outlettype": [""],
                 "patching_rect": [240.0, 420.0, 200.0, 22.0], "presentation": 1,
                 "presentation_rect": [8.0, 34.0, 184.0, 22.0]}},
        {"box": {"id": "obj-title", "maxclass": "comment", "text": "LiveBridge MIDI",
                 "numinlets": 1, "numoutlets": 0, "fontsize": 13.0, "fontface": 1,
                 "patching_rect": [420.0, 420.0, 150.0, 22.0], "presentation": 1,
                 "presentation_rect": [8.0, 8.0, 184.0, 22.0]}},
    ]
    lines = [
        _line("obj-midiin", 0, "obj-midiout", 0), _line("obj-midiin", 0, "obj-midiparse", 0),
        _line("obj-midiparse", 0, "obj-note", 0), _line("obj-midiparse", 2, "obj-cc", 0),
        _line("obj-note", 0, "obj-node", 0), _line("obj-cc", 0, "obj-node", 0),
        _line("obj-thisdevice", 0, "obj-context", 0), _line("obj-context", 0, "obj-node", 0),
        _line("obj-node", 0, "obj-route", 0), _line("obj-route", 0, "obj-set", 0),
        _line("obj-set", 0, "obj-status", 0), _line("obj-route", 1, "obj-print", 0),
        _line("obj-route", 2, "obj-context", 0), _line("obj-route", 3, "obj-midiformat", 0),
        _line("obj-midiformat", 0, "obj-midiout", 0), _line("obj-route", 4, "obj-print", 0),
    ]
    return {"patcher": {
        "fileversion": 1, "appversion": APP_VERSION, "classnamespace": "box",
        "rect": [80.0, 80.0, 700.0, 500.0], "openinpresentation": 1,
        "default_fontsize": 12.0, "default_fontface": 0, "default_fontname": "Arial",
        "gridsize": [15.0, 15.0], "devicewidth": 200.0, "description":
        "LiveBridge MIDI: what you play, and notes from Claude, for the LiveBridge MCP server",
        "boxes": boxes, "lines": lines,
        "dependency_cache": [
            {"name": "lbmidi.js", "bootpath": ".", "type": "TEXT", "implicit": 1},
            {"name": "lbmidi_context.js", "bootpath": ".", "type": "TEXT", "implicit": 1},
        ],
        "project": {"version": 1, "amxdtype": MIDI_EFFECT, "readonly": 0, "devpathtype": 0,
                    "devpath": ".", "sortmode": 0, "viewmode": 0,
                    "contents": {"patchers": {}, "code": {}}, "layout": {},
                    "searchpath": {}, "detailsvisible": 0},
    }}


# ----------------------------------------------------------------------------- LiveBridge Master

#: Live parameter styles (parameter_unitstyle).
_UNIT = {"float": 1, "ms": 2, "hz": 3, "db": 4, "pct": 5}
#: The mastering chain's parameters: (name, kind, min, max, initial, unit or menu items,
#: target objects, Max message, scale). Ableton's own DSP (abl.device.*) does the work; each
#: dial is a Live parameter, so LiveBridge (and automation) set it like any device's.
MASTER_PARAMS = (
    ("EQ Low Cut", "toggle", 0, 1, 1, None, ("eql", "eqr"), "highpass", 1.0),
    ("EQ Low", "dial", -15.0, 15.0, 0.0, "db", ("eql", "eqr"), "lowgain", 1.0),
    ("EQ Mid", "dial", -12.0, 12.0, 0.0, "db", ("eql", "eqr"), "midgain", 1.0),
    ("EQ Mid Freq", "dial", 120.0, 7500.0, 1000.0, "hz", ("eql", "eqr"), "midfreq", 1.0),
    ("EQ High", "dial", -15.0, 15.0, 0.0, "db", ("eql", "eqr"), "highgain", 1.0),
    # abl.device.compressor~ takes attack/release in seconds; one per side (the stereo
    # compressor of the reference pages is not in every Max 9)
    ("Comp Threshold", "dial", -40.0, 0.0, 0.0, "db", ("compl", "compr"), "threshold", 1.0),
    ("Comp Ratio", "dial", 1.0, 10.0, 2.0, "float", ("compl", "compr"), "ratio", 1.0),
    ("Comp Attack", "dial", 0.1, 100.0, 10.0, "ms", ("compl", "compr"), "attack", 0.001),
    ("Comp Release", "dial", 10.0, 1000.0, 200.0, "ms", ("compl", "compr"), "release", 0.001),
    ("Comp Makeup", "dial", -12.0, 24.0, 0.0, "db", ("compl", "compr"), "gain", 1.0),
    ("Limit Gain", "dial", -24.0, 24.0, 0.0, "db", ("lim",), "gain", 1.0),
    ("Limit Ceiling", "dial", -24.0, 0.0, -1.0, "db", ("lim",), "ceiling", 1.0),
    ("Limit Mode", "menu", 0, 2, 2, ("Standard", "Soft clip", "True peak"), ("lim",), "mode",
     1.0),
    ("Limit Lookahead", "menu", 0, 2, 1, ("1.5 ms", "3 ms", "6 ms"), ("lim",), "lookahead",
     1.0),
    ("Limit Auto Release", "toggle", 0, 1, 1, None, ("lim",), "autorelease", 1.0),
    ("Width", "dial", 0.0, 100.0, 100.0, "pct", ("util",), "width", 0.01),
    ("Bass Mono", "toggle", 0, 1, 0, None, ("util",), "bass_mono", 1.0),
    ("Bass Mono Freq", "dial", 50.0, 500.0, 120.0, "hz", ("util",), "bass_mono_frequency",
     1.0),
    ("Output", "dial", -24.0, 12.0, 0.0, "db", ("util",), "gain", 1.0),
)
_MASTER_DSP = (
    # id, object, inlets, outlets. Channel EQ runs once per side (mono or stereo, either way
    # one channel each); the others are stereo.
    ("eql", "abl.device.channeleq~", 1, 1),
    ("eqr", "abl.device.channeleq~", 1, 1),
    ("compl", "abl.device.compressor~", 1, 1),
    ("compr", "abl.device.compressor~", 1, 1),
    ("lim", "abl.device.limiter~", 2, 2),
    ("util", "abl.device.utility~", 2, 2),
)


def _master_param_box(i: int, spec: tuple, prefix: str = "p", left: float = 10.0) -> dict:
    name, kind, low, high, initial, unit = spec[:6]
    col, row = divmod(i, 4)
    rect = [left + 56.0 * col, 4.0 + 40.0 * row, 52.0, 36.0]
    valueof: dict = {"parameter_longname": name, "parameter_shortname": name[:12],
                     "parameter_initial_enable": 1, "parameter_initial": [initial]}
    if kind == "toggle":
        maxclass, outlets = "live.text", ["", ""]
        valueof.update(parameter_type=2, parameter_mmax=1, parameter_enum=["off", "on"])
        extra = {"mode": 1, "text": name, "texton": name}      # mode 1 = toggle
    elif kind == "menu":
        maxclass, outlets = "live.menu", ["", "", "float"]
        valueof.update(parameter_type=2, parameter_mmax=len(unit) - 1,
                       parameter_enum=list(unit))
        extra = {}
    else:
        maxclass, outlets = "live.dial", ["", "float"]
        valueof.update(parameter_type=0, parameter_mmin=low, parameter_mmax=high,
                       parameter_unitstyle=_UNIT[unit])
        if unit == "hz":
            valueof["parameter_exponent"] = 3.0
        extra = {}
    return {"box": {"id": f"obj-{prefix}{i}", "maxclass": maxclass, "varname": name,
                    "numinlets": 1, "numoutlets": len(outlets), "outlettype": outlets,
                    "parameter_enable": 1, "presentation": 1, "presentation_rect": rect,
                    "patching_rect": [20.0 + 120.0 * (i % 8), 40.0 + 120.0 * (i // 8),
                                      52.0, 36.0],
                    "saved_attribute_attributes": {"valueof": valueof}, **extra}}


def _wire_params(params: tuple, boxes: list, lines: list, prefix: str = "p",
                 left: float = 10.0, top: float = 40.0) -> None:
    """A Live parameter per entry of ``params``, each driving its DSP object(s) through
    ``prepend <message>`` (scaled first when the dial shows % but the DSP takes 0..1)."""
    for i, spec in enumerate(params):
        _name, _kind, _low, _high, _initial, _unit, targets, message, scale = spec
        boxes.append(_master_param_box(i, spec, prefix, left))
        source = f"obj-{prefix}{i}"
        x, y = 20.0 + 120.0 * (i % 8), top + 120.0 * (i // 8)
        if scale != 1.0:
            boxes.append(_box(f"obj-{prefix}s{i}", f"* {scale}", [x, y + 40.0, 50.0, 22.0], 2,
                              1))
            lines.append(_line(source, 0, f"obj-{prefix}s{i}", 0))
            source = f"obj-{prefix}s{i}"
        # the utility's width is its side level (mid stays at 1)
        text = "prepend midside 1." if message == "width" else f"prepend {message}"
        boxes.append(_box(f"obj-{prefix}m{i}", text, [x, y + 70.0, 110.0, 22.0], 1, 1))
        lines.append(_line(source, 0, f"obj-{prefix}m{i}", 0))
        for target in targets:
            lines.append(_line(f"obj-{prefix}m{i}", 0, f"obj-{target}", 0))


def master_patcher() -> dict:
    """LiveBridge Master: Channel EQ -> Compressor -> Limiter -> Utility, Ableton's own
    DSP as Max objects (abl.device.*, Max 9 / Live 12.2+), every control a Live parameter."""
    boxes = [
        _box("obj-plugin", "plugin~", [20.0, 420.0, 60.0, 22.0], 2, 2, ["signal", "signal"]),
        _box("obj-plugout", "plugout~", [20.0, 640.0, 60.0, 22.0], 2, 0),
        {"box": {"id": "obj-title", "maxclass": "comment", "text": "LiveBridge Master",
                 "numinlets": 1, "numoutlets": 0, "fontsize": 11.0, "fontface": 1,
                 "patching_rect": [20.0, 10.0, 150.0, 20.0]}},
    ]
    lines = []
    for n, (box_id, text, inlets, outlets) in enumerate(_MASTER_DSP):
        boxes.append(_box(f"obj-{box_id}", text, [20.0 + 190.0 * n, 520.0 + 30.0 * (n % 2),
                                                  180.0, 22.0],
                          inlets, outlets, ["signal"] * outlets))
    # audio: plugin~ -> EQ (L, R) -> compressor -> limiter -> utility -> plugout~
    lines += [_line("obj-plugin", 0, "obj-eql", 0), _line("obj-plugin", 1, "obj-eqr", 0),
              _line("obj-eql", 0, "obj-compl", 0), _line("obj-eqr", 0, "obj-compr", 0),
              _line("obj-compl", 0, "obj-lim", 0), _line("obj-compr", 0, "obj-lim", 1),
              _line("obj-lim", 0, "obj-util", 0), _line("obj-lim", 1, "obj-util", 1)]
    lines += [_line("obj-util", 0, "obj-plugout", 0), _line("obj-util", 1, "obj-plugout", 1)]
    _wire_params(MASTER_PARAMS, boxes, lines)
    return {"patcher": {
        "fileversion": 1, "appversion": APP_VERSION, "classnamespace": "box",
        "rect": [80.0, 80.0, 1100.0, 700.0], "openinpresentation": 1,
        "default_fontsize": 10.0, "default_fontface": 0, "default_fontname": "Arial",
        "gridsize": [15.0, 15.0], "devicewidth": 340.0,
        "description": "LiveBridge Master: EQ, glue compressor, limiter and width from "
                       "Ableton's own DSP, run by live_master_auto",
        "boxes": boxes, "lines": lines, "dependency_cache": [],
        "project": {"version": 1, "amxdtype": AUDIO_EFFECT, "readonly": 0, "devpathtype": 0,
                    "devpath": ".", "sortmode": 0, "viewmode": 0,
                    "contents": {"patchers": {}, "code": {}}, "layout": {},
                    "searchpath": {}, "detailsvisible": 0},
    }}


# ----------------------------------------------------------------------------- LiveBridge Channel

#: The channel strip in front of the Engine's meters (same columns as MASTER_PARAMS). Every
#: initial value is neutral: a fresh Channel does not change the sound (Comp Ratio 1 = no
#: compression, Saturation 0 % = dry, HP at 20 Hz). live_mix_auto sets them per role.
CHANNEL_PARAMS = (
    ("HP Freq", "dial", 20.0, 1000.0, 20.0, "hz", ("hp",), "frequency", 1.0),
    ("Low", "dial", -15.0, 15.0, 0.0, "db", ("eql", "eqr"), "lowgain", 1.0),
    ("Mid", "dial", -12.0, 12.0, 0.0, "db", ("eql", "eqr"), "midgain", 1.0),
    ("Mid Freq", "dial", 120.0, 7500.0, 1000.0, "hz", ("eql", "eqr"), "midfreq", 1.0),
    ("High", "dial", -15.0, 15.0, 0.0, "db", ("eql", "eqr"), "highgain", 1.0),
    ("Comp Threshold", "dial", -40.0, 0.0, 0.0, "db", ("compl", "compr"), "threshold", 1.0),
    ("Comp Ratio", "dial", 1.0, 10.0, 1.0, "float", ("compl", "compr"), "ratio", 1.0),
    ("Comp Attack", "dial", 0.1, 100.0, 10.0, "ms", ("compl", "compr"), "attack", 0.001),
    ("Comp Release", "dial", 10.0, 1000.0, 150.0, "ms", ("compl", "compr"), "release", 0.001),
    ("Comp Makeup", "dial", -12.0, 24.0, 0.0, "db", ("compl", "compr"), "gain", 1.0),
    ("Saturation", "dial", 0.0, 100.0, 0.0, "pct", ("satl", "satr"), "mix", 0.01),
    ("Drive", "dial", 0.0, 24.0, 6.0, "db", ("satl", "satr"), "drive", 1.0),
    ("Width", "dial", 0.0, 100.0, 100.0, "pct", ("util",), "width", 0.01),
    ("Bass Mono", "toggle", 0, 1, 0, None, ("util",), "bass_mono", 1.0),
    ("Bass Mono Freq", "dial", 50.0, 500.0, 120.0, "hz", ("util",), "bass_mono_frequency",
     1.0),
    ("Trim", "dial", -24.0, 12.0, 0.0, "db", ("util",), "gain", 1.0),
)
_CHANNEL_DSP = (
    # id, object, inlets, outlets: a clean 24 dB high-pass, Channel EQ and Saturator once per
    # side (mono or stereo, either way one channel each), the rest stereo
    ("hp", "abl.device.autofilter~ @filtertype 1 @filterslope 1 @circuit 0 @resonance 0. "
           "@drive 0. @lfoamount 0. @envamount 0. @mix 1.", 2, 2),
    ("eql", "abl.device.channeleq~", 1, 1),
    ("eqr", "abl.device.channeleq~", 1, 1),
    ("compl", "abl.device.compressor~", 1, 1),
    ("compr", "abl.device.compressor~", 1, 1),
    ("satl", "abl.dsp.saturator~ @curve 1 @mix 0.", 1, 1),
    ("satr", "abl.dsp.saturator~ @curve 1 @mix 0.", 1, 1),
    ("util", "abl.device.utility~", 2, 2),
)


def channel_patcher() -> dict:
    """LiveBridge Channel: the Engine (meters, listening, capture, modulation) with a channel
    strip in front of its meters — high-pass, EQ, compressor, saturation, width/bass mono,
    trim from Ableton's DSP (Max 9). Its Node script says ``strip: true`` in hello."""
    patcher = engine_patcher()
    body = patcher["patcher"]
    for item in body["boxes"]:
        box = item["box"]
        if box["id"] == "obj-node":
            box["text"] = "node.script lbchannel.js @autostart 1 @watch 0 @args channel"
        elif box["id"] == "obj-context":
            box["text"] = "js lbchannel_context.js"
        elif box["id"] == "obj-title":
            box["text"] = "LiveBridge Channel"
    # what plugin~ fed (meters, recorder, output) now hears the strip's output instead
    for line in body["lines"]:
        source = line["patchline"]["source"]
        if source[0] == "obj-plugin":
            source[0] = "obj-util"
    for n, (box_id, text, inlets, outlets) in enumerate(_CHANNEL_DSP):
        body["boxes"].append(_box(f"obj-{box_id}", text, [20.0 + 200.0 * (n % 4),
                                                          700.0 + 40.0 * (n // 4), 190.0,
                                                          22.0],
                                  inlets, outlets, ["signal"] * outlets))
    body["lines"] += [
        _line("obj-plugin", 0, "obj-hp", 0), _line("obj-plugin", 1, "obj-hp", 1),
        _line("obj-hp", 0, "obj-eql", 0), _line("obj-hp", 1, "obj-eqr", 0),
        _line("obj-eql", 0, "obj-compl", 0), _line("obj-eqr", 0, "obj-compr", 0),
        _line("obj-compl", 0, "obj-satl", 0), _line("obj-compr", 0, "obj-satr", 0),
        _line("obj-satl", 0, "obj-util", 0), _line("obj-satr", 0, "obj-util", 1),
    ]
    _wire_params(CHANNEL_PARAMS, body["boxes"], body["lines"], prefix="c", left=200.0,
                 top=800.0)
    body["devicewidth"] = 200.0 + 56.0 * math.ceil(len(CHANNEL_PARAMS) / 4) + 8.0
    body["description"] = ("LiveBridge Channel: a channel strip from Ableton's DSP plus the "
                           "Engine's meters, set by live_mix_auto")
    body["dependency_cache"] = [
        {"name": "lbchannel.js", "bootpath": ".", "type": "TEXT", "implicit": 1},
        {"name": "lbchannel_context.js", "bootpath": ".", "type": "TEXT", "implicit": 1},
        {"name": "lb_engine.gendsp", "bootpath": ".", "type": "gDSP", "implicit": 1},
    ]
    return patcher


# ----------------------------------------------------------------------------- LiveBridge Phase

PHASE_STAGES = 4
#: Polarity, a short delay and up to four all-pass stages at one frequency (the IBP idea):
#: the level stays, the phase around Phase Freq turns (a full stage: 0 -> -360 degrees across
#: it, -180 at the frequency). live_mix_phase tries settings and keeps the one with the least
#: low-end cancellation on the master.
PHASE_PARAMS = (
    ("Invert", "toggle", 0, 1, 0, None, ("pgen",), "invert", 1.0),
    ("Phase Freq", "dial", 20.0, 2000.0, 100.0, "hz", ("pgen",), "freq", 1.0),
    ("Phase Q", "dial", 0.1, 10.0, 0.7, "float", ("pgen",), "q", 1.0),
    ("Phase Stages", "menu", 0, PHASE_STAGES, 0,
     ("Off",) + tuple(str(i) for i in range(1, PHASE_STAGES + 1)), ("pgen",), "stages", 1.0),
    ("Delay", "dial", 0.0, 10.0, 0.0, "ms", ("pgen",), "delayms", 1.0),
)


def phase_genexpr() -> str:
    """The gen~ code of LiveBridge Phase (stereo in/out)."""
    lines = ["// LiveBridge Phase — generated by max_for_live/build.py, do not edit.",
             "// Polarity, delay (0-10 ms) and up to 4 all-pass stages at freq/q (RBJ).",
             "Param invert(0, min=0, max=1);", "Param freq(100, min=20, max=2000);",
             "Param q(0.7, min=0.1, max=10);",
             f"Param stages(0, min=0, max={PHASE_STAGES});",
             "Param delayms(0, min=0, max=10);",
             "History fs(100);", "History qs(0.7);", "History ds(0);",
             "Delay dl(4096);", "Delay dr(4096);"]
    for k in range(PHASE_STAGES):
        for c in "lr":
            lines += [f"History s{k}{c}x1(0);", f"History s{k}{c}x2(0);",
                      f"History s{k}{c}y1(0);", f"History s{k}{c}y2(0);"]
    lines += ["",
              "// glide the settings so a sweep does not click",
              "fs = fs + 0.0005 * (freq - fs);", "qs = qs + 0.0005 * (q - qs);",
              "ds = ds + 0.0005 * (delayms - ds);",
              "w0 = twopi * min(fs, samplerate * 0.45) / samplerate;",
              "alpha = sin(w0) / (2 * qs);", "a0 = 1 + alpha;",
              "c0 = (1 - alpha) / a0;", "c1 = -2 * cos(w0) / a0;",
              "pol = invert > 0.5 ? -1 : 1;",
              "dl.write(in1 * pol);", "dr.write(in2 * pol);",
              "dsamp = min(ds * samplerate / 1000, 4000);",
              "l = dl.read(dsamp, interp=\"linear\");",
              "r = dr.read(dsamp, interp=\"linear\");"]
    for k in range(PHASE_STAGES):
        lines.append(f"if (stages > {k + 0.5}) {{")
        for c, x in (("l", "l"), ("r", "r")):
            st = f"s{k}{c}"
            # all-pass: b0 = a2 = c0, b1 = a1 = c1, b2 = 1
            lines += [f"    y{c} = c0 * {x} + c1 * {st}x1 + {st}x2 - c1 * {st}y1 - c0 * {st}y2;",
                      f"    {st}x2 = {st}x1; {st}x1 = {x}; {st}y2 = {st}y1; {st}y1 = y{c};",
                      f"    {x} = y{c};"]
        lines.append("}")
    lines += ["out1 = l;", "out2 = r;"]
    return "\n".join(lines) + "\n"


def phase_patcher() -> dict:
    """LiveBridge Phase: plugin~ -> gen~ lb_phase -> plugout~, five Live parameters."""
    boxes = [
        _box("obj-plugin", "plugin~", [20.0, 300.0, 60.0, 22.0], 2, 2, ["signal", "signal"]),
        _box("obj-pgen", "gen~ @gen lb_phase", [20.0, 340.0, 200.0, 22.0], 2, 2,
             ["signal", "signal"]),
        _box("obj-plugout", "plugout~", [20.0, 380.0, 60.0, 22.0], 2, 0),
        {"box": {"id": "obj-title", "maxclass": "comment", "text": "LiveBridge Phase",
                 "numinlets": 1, "numoutlets": 0, "fontsize": 11.0, "fontface": 1,
                 "patching_rect": [20.0, 10.0, 150.0, 20.0]}},
    ]
    lines = [_line("obj-plugin", 0, "obj-pgen", 0), _line("obj-plugin", 1, "obj-pgen", 1),
             _line("obj-pgen", 0, "obj-plugout", 0), _line("obj-pgen", 1, "obj-plugout", 1)]
    _wire_params(PHASE_PARAMS, boxes, lines)
    return {"patcher": {
        "fileversion": 1, "appversion": APP_VERSION, "classnamespace": "box",
        "rect": [80.0, 80.0, 900.0, 500.0], "openinpresentation": 1,
        "default_fontsize": 10.0, "default_fontface": 0, "default_fontname": "Arial",
        "gridsize": [15.0, 15.0], "devicewidth": 130.0,
        "description": "LiveBridge Phase: polarity, delay and all-pass phase rotation for "
                       "kick/bass alignment, set by live_mix_phase",
        "boxes": boxes, "lines": lines,
        "dependency_cache": [{"name": "lb_phase.gendsp", "bootpath": ".", "type": "gDSP",
                              "implicit": 1}],
        "project": {"version": 1, "amxdtype": AUDIO_EFFECT, "readonly": 0, "devpathtype": 0,
                    "devpath": ".", "sortmode": 0, "viewmode": 0,
                    "contents": {"patchers": {}, "code": {}}, "layout": {},
                    "searchpath": {}, "detailsvisible": 0},
    }}


def host_patcher(instrument: bool) -> dict:
    """The generic plug-in hosts (patch built by livebridge_mcp.host_device, shared with the
    per-plug-in bank devices live_plugin_host_bank writes)."""
    return host_device.host_patcher(instrument)


def amxd(patcher: dict, device_type: int) -> bytes:
    """The bytes of an unfrozen .amxd: 'ampf' header, meta, then the patch JSON + NUL."""
    payload = json.dumps(patcher, indent="\t", ensure_ascii=False).encode("utf-8") + b"\x00"
    header = b"ampf" + struct.pack("<II", 4, device_type) + b"meta" + struct.pack("<II", 4, 0)
    return header + b"ptch" + struct.pack("<I", len(payload)) + payload


def read_amxd(data: bytes) -> tuple[int, dict]:
    """(device type, patcher) of .amxd bytes written by :func:`amxd` (for tests/tools)."""
    if data[:4] != b"ampf" or data[12:16] != b"meta" or data[24:28] != b"ptch":
        raise ValueError("not an unfrozen .amxd")
    device_type = struct.unpack_from("<I", data, 8)[0]
    size = struct.unpack_from("<I", data, 28)[0]
    payload = data[32:32 + size]
    return device_type, json.loads(payload.rstrip(b"\x00").decode("utf-8"))


def outputs() -> dict[Path, bytes]:
    return {
        ENGINE_GENDSP: (json.dumps(gendsp(), indent="\t", ensure_ascii=False) + "\n").encode(),
        ENGINE_AMXD: amxd(host_device.gate_until_ready(engine_patcher()), AUDIO_EFFECT),
        MIDI_AMXD: amxd(host_device.gate_until_ready(midi_patcher()), MIDI_EFFECT),
        MIDI_CONTEXT: ENGINE_CONTEXT.read_bytes(),
        HOST_EFFECT_AMXD: amxd(host_patcher(False), AUDIO_EFFECT),
        HOST_INSTRUMENT_AMXD: amxd(host_patcher(True), INSTRUMENT),
        HOST_CONTEXT: ENGINE_CONTEXT.read_bytes(),
        MASTER_AMXD: amxd(master_patcher(), AUDIO_EFFECT),
        PHASE_AMXD: amxd(phase_patcher(), AUDIO_EFFECT),
        PHASE_GENDSP: (json.dumps(gendsp(phase_genexpr(), 2), indent="\t", ensure_ascii=False)
                       + "\n").encode(),
        CHANNEL_AMXD: amxd(host_device.gate_until_ready(channel_patcher()), AUDIO_EFFECT),
        CHANNEL_NODE: (HERE / "LiveBridge Engine" / "engine.js").read_bytes(),
        CHANNEL_CONTEXT: ENGINE_CONTEXT.read_bytes(),
        CHANNEL_GENDSP: (json.dumps(gendsp(), indent="\t", ensure_ascii=False) + "\n").encode(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true",
                        help="only check that the committed files are up to date")
    args = parser.parse_args(argv)
    stale = []
    for path, data in outputs().items():
        if args.check:
            if not path.is_file() or path.read_bytes() != data:
                stale.append(path)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        print(f"wrote {path.relative_to(HERE.parent)} ({len(data)} bytes)")
    if stale:
        print("out of date (run python max_for_live/build.py): "
              + ", ".join(str(p.relative_to(HERE.parent)) for p in stale))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
