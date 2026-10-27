#!/usr/bin/env python3
"""Build the LiveBridge Max for Live devices from source (standard library only).

    python max_for_live/build.py            # write the device files
    python max_for_live/build.py --check    # exit 1 when a committed file is out of date

``LiveBridge Engine`` (an audio effect) is written to ``max_for_live/LiveBridge Engine/``:

* ``LiveBridge Engine.amxd`` — the device: an ``ampf`` header (device type, meta, patch size)
  followed by the patcher JSON and a NUL byte, the layout Live writes for an unfrozen device.
* ``lb_engine.gendsp`` — the gen~ patcher with the analysis code (GenExpr, generated below).
* ``engine.js`` (Node for Max) and ``context.js`` (Max js) are hand-written sources next to it.

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
ENGINE_DIR = HERE / "LiveBridge Engine"
ENGINE_AMXD = ENGINE_DIR / "LiveBridge Engine.amxd"
ENGINE_GENDSP = ENGINE_DIR / "lb_engine.gendsp"

AUDIO_EFFECT = 0x61616161          # 'aaaa'
MIDI_EFFECT = 0x6D6D6D6D           # 'mmmm'
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


def gendsp() -> dict:
    outputs = len(OUTPUTS)
    boxes = [
        _box("obj-in1", "in 1", [20.0, 20.0, 30.0, 22.0], 0, 1),
        _box("obj-in2", "in 2", [120.0, 20.0, 30.0, 22.0], 0, 1),
        {"box": {"id": "obj-code", "maxclass": "codebox", "code": genexpr(),
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
        _box("obj-route", "route status log context capture resolve mod",
             [120.0, 340.0, 300.0, 22.0], 1, 7),
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
        _line("obj-route", 5, "obj-modslot", 0), _line("obj-route", 6, "obj-print", 0),
        _line("obj-plugin", 0, "obj-sfrecord", 0), _line("obj-plugin", 1, "obj-sfrecord", 1),
    ]
    for i in range(MOD_SLOTS):
        x = 700.0 + 110.0 * i
        boxes += [
            _box(f"obj-modroute{i}", "route value id", [x, 420.0, 90.0, 22.0], 1, 3),
            _box(f"obj-line{i}", "line~", [x, 460.0, 40.0, 22.0], 2, 2, ["signal", "bang"]),
            _box(f"obj-modid{i}", "prepend id", [x + 45.0, 460.0, 60.0, 22.0], 1, 1),
            _box(f"obj-remote{i}", "live.remote~", [x, 500.0, 80.0, 22.0], 2, 0),
        ]
        lines += [_line("obj-modslot", i, f"obj-modroute{i}", 0),
                  _line(f"obj-modroute{i}", 0, f"obj-line{i}", 0),
                  _line(f"obj-line{i}", 0, f"obj-remote{i}", 0),
                  _line(f"obj-modroute{i}", 1, f"obj-modid{i}", 0),
                  _line(f"obj-modid{i}", 0, f"obj-remote{i}", 1)]
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
        ENGINE_AMXD: amxd(engine_patcher(), AUDIO_EFFECT),
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
