"""The LiveBridge Host Max for Live devices, as data: the generic hosts that
``max_for_live/build.py`` writes, and per-plug-in *bank* devices written at runtime by
``live_plugin_host_bank``.

A host loads a VST3 inside Max's ``vst~`` (see ``max_for_live/LiveBridge Host/lbhost.js``). Its
parameters are not Live parameters, so they cannot be automated. A bank device adds up to 128
``live.dial`` objects named after chosen plug-in parameters: Live reads a device's parameter
list once, when it loads, so the names are baked into the file (the approach of xxnoodl's
ableton_device_vst_wrapper, MIT). Each dial drives its parameter (``<vst index> <value>`` into
``vst~``); values LiveBridge sets are sent back to the dial with ``set`` (no echo). The
plug-in, the bank and the host kind reach ``lbhost.js`` as one hex-encoded JSON argument.
"""

from __future__ import annotations

import json
import re
import struct
from typing import Any

AUDIO_EFFECT = 0x61616161          # 'aaaa'
INSTRUMENT = 0x69696969            # 'iiii'
APP_VERSION = {"major": 9, "minor": 0, "revision": 7, "architecture": "x64", "modernui": 1}
#: vst~'s message outlets (after its 2 signal outlets) all go to lbhost.js, tagged
VST_OUTLETS = 5
MAX_BANK = 128
_DIAL_W, _DIAL_H, _ROWS = 46.0, 48.0, 3


def _box(box_id: str, text: str, rect: list[float], inlets: int, outlets: int,
         outlettype: list[str] | None = None, **extra) -> dict:
    box = {"id": box_id, "maxclass": "newobj", "text": text, "numinlets": inlets,
           "numoutlets": outlets, "outlettype": outlettype or [""] * outlets,
           "patching_rect": rect}
    box.update(extra)
    return {"box": box}


def _line(source: str, outlet: int, destination: str, inlet: int) -> dict:
    return {"patchline": {"source": [source, outlet], "destination": [destination, inlet]}}


def encode_config(config: dict[str, Any]) -> str:
    """The JSON config as one Max-safe symbol (hex), read back by lbhost.js."""
    return "lbhost:" + json.dumps(config, separators=(",", ":")).encode("utf-8").hex()


def _unique_names(bank: list[dict[str, Any]]) -> list[str]:
    seen: dict[str, int] = {}
    names = []
    for entry in bank:
        base = re.sub(r"\s+", " ", str(entry["name"])).strip()[:60] or f"Param {entry['index']}"
        name = base
        while name.lower() in seen:
            seen[base.lower()] = seen.get(base.lower(), 1) + 1
            name = f"{base} {seen[base.lower()]}"
        seen[name.lower()] = 1
        names.append(name)
    return names


def host_patcher(instrument: bool, config: dict[str, Any] | None = None,
                 bank: list[dict[str, Any]] | None = None, title: str = "LiveBridge Host",
                 ) -> dict:
    """A host patch: effect (plugin~ -> vst~ -> plugout~) or instrument (midiin -> midiparse
    -> vst~ -> plugout~), lbhost.js, and optionally a bank of dials ``[{name, index}]``
    (``index``: vst~'s 1-based parameter number)."""
    bank = list(bank or [])[:MAX_BANK]
    config = dict(config or {})
    config["kind"] = "instrument" if instrument else "effect"
    if bank:
        config["bank"] = [{"name": n, "index": int(e["index"])}
                          for n, e in zip(_unique_names(bank), bank)]
    vst_out = 2 + VST_OUTLETS
    boxes = [
        _box("obj-vst", "vst~ 2 2 @autosave 1 @valuemode 2", [20.0, 140.0, 200.0, 22.0], 2,
             vst_out, ["signal", "signal"] + [""] * VST_OUTLETS, varname="plug",
             parameter_enable=1, saved_attribute_attributes={"valueof": {
                 "parameter_longname": "LiveBridge Host state", "parameter_shortname": "state",
                 "parameter_type": 3, "parameter_invisible": 1}}),
        _box("obj-plugout", "plugout~", [20.0, 200.0, 60.0, 22.0], 2, 0),
        _box("obj-node", "node.script lbhost.js @autostart 1 @watch 0 @args "
             + encode_config(config), [240.0, 300.0, 300.0, 22.0], 1, 2),
        _box("obj-route", "route status log context vst bank", [240.0, 340.0, 230.0, 22.0], 1,
             6),
        _box("obj-thisdevice", "live.thisdevice", [520.0, 200.0, 90.0, 22.0], 1, 3,
             ["bang", "int", "int"]),
        _box("obj-context", "js lbhost_context.js", [520.0, 240.0, 130.0, 22.0], 1, 1),
        _box("obj-set", "prepend set", [240.0, 380.0, 80.0, 22.0], 1, 1),
        _box("obj-print", "print LiveBridgeHost", [340.0, 420.0, 130.0, 22.0], 1, 0),
        {"box": {"id": "obj-status", "maxclass": "message", "text": "starting...",
                 "numinlets": 2, "numoutlets": 1, "outlettype": [""],
                 "patching_rect": [240.0, 420.0, 200.0, 22.0], "presentation": 1,
                 "presentation_rect": [8.0, 34.0, 184.0, 22.0]}},
        # "Open plug-in": the plug-in's own window (vst~ "open"), by hand
        {"box": {"id": "obj-open", "maxclass": "textbutton", "text": "Open plug-in",
                 "numinlets": 1, "numoutlets": 3, "outlettype": ["", "", "int"],
                 "patching_rect": [20.0, 260.0, 100.0, 20.0], "presentation": 1,
                 "presentation_rect": [8.0, 64.0, 110.0, 22.0]}},
        _box("obj-openbang", "t b", [20.0, 290.0, 30.0, 22.0], 1, 1, ["bang"]),
        {"box": {"id": "obj-openmsg", "maxclass": "message", "text": "open",
                 "numinlets": 2, "numoutlets": 1, "outlettype": [""],
                 "patching_rect": [20.0, 320.0, 40.0, 22.0]}},
        {"box": {"id": "obj-title", "maxclass": "comment", "text": title,
                 "numinlets": 1, "numoutlets": 0, "fontsize": 13.0, "fontface": 1,
                 "patching_rect": [520.0, 420.0, 150.0, 22.0], "presentation": 1,
                 "presentation_rect": [8.0, 8.0, 184.0, 22.0]}},
    ]
    lines = [
        _line("obj-vst", 0, "obj-plugout", 0), _line("obj-vst", 1, "obj-plugout", 1),
        _line("obj-node", 0, "obj-route", 0), _line("obj-route", 0, "obj-set", 0),
        _line("obj-set", 0, "obj-status", 0), _line("obj-route", 1, "obj-print", 0),
        _line("obj-route", 2, "obj-context", 0), _line("obj-route", 3, "obj-vst", 0),
        _line("obj-route", 5, "obj-print", 0),
        _line("obj-thisdevice", 0, "obj-context", 0), _line("obj-context", 0, "obj-node", 0),
        _line("obj-open", 0, "obj-openbang", 0), _line("obj-openbang", 0, "obj-openmsg", 0),
        _line("obj-openmsg", 0, "obj-vst", 0),
    ]
    for outlet in range(VST_OUTLETS):
        tag = f"obj-vstout{outlet}"
        boxes.append(_box(tag, f"prepend vstout {outlet + 2}",
                          [200.0 + 110.0 * outlet, 220.0, 105.0, 22.0], 1, 1))
        lines += [_line("obj-vst", 2 + outlet, tag, 0), _line(tag, 0, "obj-node", 0)]
    if instrument:
        boxes += [
            _box("obj-midiin", "midiin", [20.0, 20.0, 50.0, 22.0], 1, 1, ["int"]),
            _box("obj-midiparse", "midiparse", [20.0, 60.0, 120.0, 22.0], 1, 8,
                 ["", "", "", "int", "int", "int", "int", ""]),
        ]
        lines += [_line("obj-midiin", 0, "obj-midiparse", 0),
                  _line("obj-midiparse", 7, "obj-vst", 0)]
    else:
        boxes.append(_box("obj-plugin", "plugin~", [20.0, 20.0, 60.0, 22.0], 2, 2,
                          ["signal", "signal"]))
        lines += [_line("obj-plugin", 0, "obj-vst", 0), _line("obj-plugin", 1, "obj-vst", 1)]
    width = 200.0
    if config.get("bank"):
        entries = config["bank"]
        # feedback: "bank <index> <value>" -> route by index -> set the dial (no output)
        boxes.append(_box("obj-bankroute", "route " + " ".join(str(e["index"]) for e in entries),
                          [700.0, 340.0, 300.0, 22.0], 1, len(entries) + 1))
        lines.append(_line("obj-route", 4, "obj-bankroute", 0))
        for i, entry in enumerate(entries):
            col, row = divmod(i, _ROWS)
            dial, send, back = f"obj-dial{i}", f"obj-dialout{i}", f"obj-dialset{i}"
            boxes.append({"box": {
                "id": dial, "maxclass": "live.dial", "varname": f"bank{i}",
                "numinlets": 2, "numoutlets": 2, "outlettype": ["", "float"],
                "parameter_enable": 1,
                "patching_rect": [700.0 + 60.0 * (i % 16), 400.0 + 80.0 * (i // 16), 44.0, 48.0],
                "presentation": 1,
                "presentation_rect": [200.0 + _DIAL_W * col, 4.0 + _DIAL_H * row, 44.0, 48.0],
                "saved_attribute_attributes": {"valueof": {
                    "parameter_longname": entry["name"],
                    "parameter_shortname": entry["name"][:12],
                    "parameter_type": 0, "parameter_mmin": 0.0, "parameter_mmax": 1.0,
                    "parameter_unitstyle": 1, "parameter_initial_enable": 0}}}})
            boxes.append(_box(send, f"prepend {entry['index']}",
                              [700.0 + 60.0 * (i % 16), 460.0 + 80.0 * (i // 16), 55.0, 22.0],
                              1, 1))
            boxes.append(_box(back, "prepend set",
                              [700.0 + 60.0 * (i % 16), 370.0 + 80.0 * (i // 16), 55.0, 22.0],
                              1, 1))
            lines += [_line(dial, 0, send, 0), _line(send, 0, "obj-vst", 0),
                      _line("obj-bankroute", i, back, 0), _line(back, 0, dial, 0)]
        width = 200.0 + _DIAL_W * ((len(entries) + _ROWS - 1) // _ROWS) + 8.0
    kind = INSTRUMENT if instrument else AUDIO_EFFECT
    return gate_until_ready({"patcher": {
        "fileversion": 1, "appversion": APP_VERSION, "classnamespace": "box",
        "rect": [80.0, 80.0, 1100.0, 600.0], "openinpresentation": 1,
        "default_fontsize": 12.0, "default_fontface": 0, "default_fontname": "Arial",
        "gridsize": [15.0, 15.0], "devicewidth": width, "description":
        "LiveBridge Host: any VST3 plug-in with all of its parameters reachable by the "
        "LiveBridge MCP server (experimental)",
        "boxes": boxes, "lines": lines,
        "dependency_cache": [
            {"name": "lbhost.js", "bootpath": ".", "type": "TEXT", "implicit": 1},
            {"name": "lbhost_context.js", "bootpath": ".", "type": "TEXT", "implicit": 1},
        ],
        "project": {"version": 1, "amxdtype": kind, "readonly": 0, "devpathtype": 0,
                    "devpath": ".", "sortmode": 0, "viewmode": 0,
                    "contents": {"patchers": {}, "code": {}}, "layout": {},
                    "searchpath": {}, "detailsvisible": 0},
    }})


def gate_until_ready(patcher: dict, node: str = "obj-node", route: str = "obj-route") -> dict:
    """Hold back every message to the Node script until it says "ready".

    A device's helpers (context.js, meters, MIDI) start talking at once, while Node for Max
    needs a second or two to start; each message in that gap printed "Node script not ready
    can't handle message ..." in the Max window. Everything bound for ``node`` now passes a
    closed ``gate``; the script's "ready" (a new last selector of ``route``) opens it.
    """
    boxes = patcher["patcher"]["boxes"]
    lines = patcher["patcher"]["lines"]
    route_box = next(b["box"] for b in boxes if b["box"]["id"] == route)
    selectors = route_box["text"].split()[1:]
    if "ready" in selectors:
        return patcher
    reject = len(selectors)
    route_box["text"] += " ready"
    route_box["numoutlets"] = reject + 2
    route_box["outlettype"] = [""] * (reject + 2)
    for line in lines:
        source = line["patchline"]["source"]
        if source == [route, reject]:
            line["patchline"]["source"] = [route, reject + 1]
        if line["patchline"]["destination"] == [node, 0]:
            line["patchline"]["destination"] = ["obj-gate", 1]
    x, y = route_box["patching_rect"][0], route_box["patching_rect"][1]
    boxes.append(_box("obj-gate", "gate 1 0", [x - 120.0, y - 80.0, 55.0, 22.0], 2, 1))
    boxes.append(_box("obj-ready", "t 1", [x - 120.0, y - 40.0, 30.0, 22.0], 1, 1, ["int"]))
    lines += [_line("obj-gate", 0, node, 0), _line(route, reject, "obj-ready", 0),
              _line("obj-ready", 0, "obj-gate", 0)]
    return patcher


def amxd(patcher: dict, device_type: int) -> bytes:
    """The bytes of an unfrozen .amxd: 'ampf' header, meta, then the patch JSON + NUL."""
    payload = json.dumps(patcher, indent="\t", ensure_ascii=False).encode("utf-8") + b"\x00"
    header = b"ampf" + struct.pack("<II", 4, device_type) + b"meta" + struct.pack("<II", 4, 0)
    return header + b"ptch" + struct.pack("<I", len(payload)) + payload


def bank_device(plugin: dict[str, Any], instrument: bool, bank: list[dict[str, Any]]) -> bytes:
    """A per-plug-in host with ``bank`` dials that loads ``plugin`` ({file, sub, name}) itself
    the first time it runs."""
    title = f"{plugin.get('name') or plugin['file']} (LiveBridge)"
    patcher = host_patcher(instrument, {"autoload": plugin}, bank, title=title[:40])
    return amxd(patcher, INSTRUMENT if instrument else AUDIO_EFFECT)
