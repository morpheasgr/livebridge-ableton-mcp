"""The LiveBridge Engine Max for Live device: its analysis code (executed here line by line on
test signals), the built .amxd, the Node for Max script (run under node with a stand-in for
Max's API) and the MCP tools that talk to it."""

import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "mcp_server"))
sys.path.insert(0, str(REPO / "max_for_live"))

import build as m4l_build  # noqa: E402
from livebridge_mcp import engine  # noqa: E402
from livebridge_mcp.client import BridgeClient  # noqa: E402
from livebridge_mcp.server import create_app  # noqa: E402

from test_consistency import call_tool  # noqa: E402

ENGINE_JS = REPO / "max_for_live" / "LiveBridge Engine" / "engine.js"
NODE = shutil.which("node")


# ----------------------------------------------------------------------------- the DSP

class GenSimulator:
    """Runs the generated GenExpr (History declarations, a one-time init block and plain
    assignments) in Python, one sample at a time."""

    FUNCTIONS = {"tan": math.tan, "pow": math.pow, "sin": math.sin, "cos": math.cos,
                 "sqrt": math.sqrt, "exp": math.exp, "min": min, "max": max, "abs": abs}

    def __init__(self, code: str, samplerate: float):
        self.env = dict(self.FUNCTIONS, samplerate=float(samplerate))
        init, main, block = [], [], None
        for raw in code.splitlines():
            line = raw.split("//")[0].strip()
            if not line:
                continue
            history = re.match(r"^History (\w+)\((.*)\);$", line)
            if history:
                self.env[history.group(1)] = float(history.group(2))
            elif line == "if (ready == 0) {":
                block = init
            elif line == "}":
                block = None
            else:
                assert line.endswith(";"), line
                (block if block is not None else main).append(line[:-1])
        self.init = compile("\n".join(init), "genexpr-init", "exec")
        self.main = compile("\n".join(main), "genexpr-main", "exec")

    def run(self, left, right):
        env = self.env
        if env["ready"] == 0:
            exec(self.init, env)
        for l, r in zip(left, right):
            env["in1"], env["in2"] = l, r
            exec(self.main, env)
        return [env[f"out{i + 1}"] for i in range(len(m4l_build.OUTPUTS))]


def sine(freq, seconds, fs=48000, amp=0.5, phase=0.0):
    return [amp * math.sin(2 * math.pi * freq * n / fs + phase) for n in range(int(seconds * fs))]


@pytest.fixture(scope="module")
def code():
    return m4l_build.genexpr()


def outputs(code, left, right, fs=48000):
    values = GenSimulator(code, fs).run(left, right)
    return dict(zip(m4l_build.OUTPUTS, values))


def test_coefficient_formulas_in_genexpr_match_python(code):
    for fs in (44100, 48000, 96000):
        sim = GenSimulator(code, fs)
        exec(sim.init, sim.env)
        for prefix, coeffs in (("ks_", m4l_build.k_shelf(fs)), ("kh_", m4l_build.k_highpass(fs)),
                               ("lp_", m4l_build.lowpass(fs, m4l_build.LOW_END_HZ))):
            got = [sim.env[prefix + c] for c in ("b0", "b1", "b2", "a1", "a2")]
            assert got == pytest.approx(coeffs, rel=1e-12), (fs, prefix)
        for i, (_name, low, high) in enumerate(m4l_build.BANDS):
            got = [sim.env[f"b{i}_{c}"] for c in ("b0", "b1", "b2", "a1", "a2")]
            assert got == pytest.approx(m4l_build.bandpass(fs, low, high), rel=1e-12), i


def test_k_weighting_matches_pyloudnorm(code):
    pyln = pytest.importorskip("pyloudnorm")
    np = pytest.importorskip("numpy")
    for freq in (100.0, 1000.0, 5000.0):
        tone = sine(freq, 1.6, amp=0.5)
        out = outputs(code, tone, tone)
        live = -0.691 + 10 * math.log10(out["msk"])
        reference = pyln.Meter(48000).integrated_loudness(
            np.column_stack([sine(freq, 3.0), sine(freq, 3.0)]))
        assert live == pytest.approx(reference, abs=0.35), freq


def test_levels_bands_and_stereo(code):
    tone = sine(1000.0, 0.8)
    same = outputs(code, tone, tone)
    assert same["msl"] == pytest.approx(0.125, rel=0.05) and same["pkl"] == pytest.approx(
        0.5, rel=0.02)
    bands = [same[f"msb{i}"] for i in range(len(m4l_build.BANDS))]
    assert m4l_build.BANDS[bands.index(max(bands))][0] == "mid"
    assert same["mss"] < 1e-9 and same["mlr"] == pytest.approx(0.125, rel=0.05)

    opposite = outputs(code, tone, [-x for x in tone])
    assert opposite["msm"] < 1e-9 and opposite["mlr"] == pytest.approx(-0.125, rel=0.05)

    presence = outputs(code, sine(3000.0, 0.5), sine(3000.0, 0.5))
    bands = [presence[f"msb{i}"] for i in range(len(m4l_build.BANDS))]
    assert m4l_build.BANDS[bands.index(max(bands))][0] == "presence"

    wide_bass = outputs(code, sine(50.0, 0.8), sine(50.0, 0.8, phase=math.pi))
    assert wide_bass["mssl"] > 100 * max(wide_bass["msml"], 1e-12)


def test_summary_of_simulated_frames(code):
    tone = sine(1000.0, 0.8)
    out = outputs(code, tone, tone)
    frame = [out[name] for name in m4l_build.OUTPUTS]
    frames = [[1000 + 100 * i] + frame for i in range(20)]
    summary = engine.summarize(frames, kind="stem")
    assert summary["frames"] == 20 and summary["seconds"] == 1.9 and not summary["silent"]
    assert summary["loudness"]["peak_db"] == pytest.approx(-6.0, abs=0.2)
    assert summary["loudness"]["rms_db"] == pytest.approx(-9.0, abs=0.3)
    assert summary["loudness"]["crest_db"] == pytest.approx(3.0, abs=0.3)
    assert summary["stereo"] == {"correlation": 1.0, "width": 0.0,
                                 "low_end_width": summary["stereo"]["low_end_width"]}
    bands = summary["spectrum"]["bands_db"]
    assert max(bands, key=bands.get) == "mid"
    assert summary["loudness"]["lufs"] == summary["loudness"]["momentary_lufs"]


def test_summary_edge_cases():
    assert engine.summarize([])["silent"] is True
    quiet = [[i * 100] + [0.0] * len(engine.FIELDS) for i in range(5)]
    summary = engine.summarize(quiet, timeline=True)
    assert summary["silent"] and summary["loudness"]["lufs"] is None
    assert summary["flags"] == ["Silence: nothing played through the device in this window."]
    assert len(summary["timeline"]) == 1


# ----------------------------------------------------------------------------- the device file

def test_amxd_is_built_and_wired():
    assert m4l_build.main(["--check"]) == 0, "run python max_for_live/build.py"
    device_type, patcher = m4l_build.read_amxd(m4l_build.ENGINE_AMXD.read_bytes())
    assert device_type == m4l_build.AUDIO_EFFECT
    boxes = {b["box"]["id"]: b["box"] for b in patcher["patcher"]["boxes"]}
    for line in patcher["patcher"]["lines"]:
        (source, outlet), (destination, inlet) = (line["patchline"]["source"],
                                                  line["patchline"]["destination"])
        assert source in boxes and destination in boxes, line
        assert outlet < boxes[source]["numoutlets"] and inlet < boxes[destination]["numinlets"]
    texts = [b["text"] for b in boxes.values() if "text" in b]
    assert "gen~ @gen lb_engine" in texts and any(t.startswith("node.script engine.js")
                                                  for t in texts)
    folder = m4l_build.ENGINE_DIR
    for dependency in patcher["patcher"]["dependency_cache"]:
        assert (folder / dependency["name"]).is_file(), dependency["name"]
    gen = json.loads(m4l_build.ENGINE_GENDSP.read_text(encoding="utf-8"))
    codebox = [b["box"] for b in gen["patcher"]["boxes"] if b["box"]["maxclass"] == "codebox"]
    assert codebox[0]["numoutlets"] == len(m4l_build.OUTPUTS) == len(engine.FIELDS)
    assert codebox[0]["code"] == m4l_build.genexpr()


def test_master_device_is_built_and_wired():
    device_type, patcher = m4l_build.read_amxd(m4l_build.MASTER_AMXD.read_bytes())
    assert device_type == m4l_build.AUDIO_EFFECT
    boxes = {b["box"]["id"]: b["box"] for b in patcher["patcher"]["boxes"]}
    for line in patcher["patcher"]["lines"]:
        (source, outlet), (destination, inlet) = (line["patchline"]["source"],
                                                  line["patchline"]["destination"])
        assert source in boxes and destination in boxes, line
        assert outlet < boxes[source]["numoutlets"] and inlet < boxes[destination]["numinlets"]
    texts = [b.get("text", "") for b in boxes.values()]
    # abl.device.stereocompressor~ (documented) is missing in Max 9.1.5: never use it
    assert not any("stereocompressor" in t for t in texts)
    for dsp in ("abl.device.channeleq~", "abl.device.compressor~",
                "abl.device.limiter~", "abl.device.utility~"):
        assert any(t.startswith(dsp) for t in texts), dsp
    names = [b["saved_attribute_attributes"]["valueof"]["parameter_longname"]
             for b in boxes.values() if b.get("parameter_enable")]
    assert names == [p[0] for p in m4l_build.MASTER_PARAMS] and len(set(names)) == len(names)
    assert {"Limit Gain", "Limit Ceiling", "Comp Ratio", "EQ Low Cut"} <= set(names)
    # every control reaches its DSP object(s)
    feeds = {}
    for line in patcher["patcher"]["lines"]:
        feeds.setdefault(line["patchline"]["source"][0], []).append(
            line["patchline"]["destination"][0])

    def reaches(start):
        seen, todo = set(), [start]
        while todo:
            for nxt in feeds.get(todo.pop(), []):
                if nxt not in seen:
                    seen.add(nxt)
                    todo.append(nxt)
        return seen
    for i, spec in enumerate(m4l_build.MASTER_PARAMS):
        assert {f"obj-{t}" for t in spec[6]} <= reaches(f"obj-p{i}"), spec[0]
    assert "obj-plugout" in reaches("obj-plugin")


def test_channel_device_is_the_engine_plus_a_strip():
    device_type, patcher = m4l_build.read_amxd(m4l_build.CHANNEL_AMXD.read_bytes())
    assert device_type == m4l_build.AUDIO_EFFECT
    body = patcher["patcher"]
    boxes = {b["box"]["id"]: b["box"] for b in body["boxes"]}
    for line in body["lines"]:
        (source, outlet), (destination, inlet) = (line["patchline"]["source"],
                                                  line["patchline"]["destination"])
        assert source in boxes and destination in boxes, line
        assert outlet < boxes[source]["numoutlets"] and inlet < boxes[destination]["numinlets"]
    assert boxes["obj-node"]["text"].startswith("node.script lbchannel.js")
    assert "@args channel" in boxes["obj-node"]["text"]
    for dependency in body["dependency_cache"]:
        assert (m4l_build.CHANNEL_DIR / dependency["name"]).is_file(), dependency["name"]
    assert m4l_build.CHANNEL_NODE.read_bytes() == m4l_build.ENGINE_DIR.joinpath(
        "engine.js").read_bytes()
    feeds = {}
    for line in body["lines"]:
        feeds.setdefault(line["patchline"]["source"][0], set()).add(
            line["patchline"]["destination"][0])
    # plugin~ feeds only the strip; the meters, recorder and output hear the strip's end
    assert feeds["obj-plugin"] == {"obj-hp"}
    assert {"obj-gen", "obj-plugout", "obj-sfrecord"} <= feeds["obj-util"]
    names = [b["saved_attribute_attributes"]["valueof"]["parameter_longname"]
             for b in boxes.values() if b.get("parameter_enable")
             and b["id"].startswith("obj-c")]
    assert names == [p[0] for p in m4l_build.CHANNEL_PARAMS]
    from livebridge_mcp import mix
    assert set(mix.CHANNEL_NEUTRAL) == set(names)
    initial = {p[0]: p[4] for p in m4l_build.CHANNEL_PARAMS}
    assert initial == mix.CHANNEL_NEUTRAL                    # a fresh Channel is at rest


def test_phase_device_is_built_and_wired():
    _type, patcher = m4l_build.read_amxd(m4l_build.PHASE_AMXD.read_bytes())
    boxes = {b["box"]["id"]: b["box"] for b in patcher["patcher"]["boxes"]}
    for line in patcher["patcher"]["lines"]:
        (source, outlet), (destination, inlet) = (line["patchline"]["source"],
                                                  line["patchline"]["destination"])
        assert outlet < boxes[source]["numoutlets"] and inlet < boxes[destination]["numinlets"]
    names = [b["saved_attribute_attributes"]["valueof"]["parameter_longname"]
             for b in boxes.values() if b.get("parameter_enable")]
    assert names == ["Invert", "Phase Freq", "Phase Q", "Phase Stages", "Delay"]
    code = json.loads(m4l_build.PHASE_GENDSP.read_text(encoding="utf-8"))
    code = next(b["box"]["code"] for b in code["patcher"]["boxes"]
                if b["box"]["maxclass"] == "codebox")
    # every Live parameter has its gen~ Param, every stage its state
    for message in ("invert", "freq", "q", "stages", "delayms"):
        assert f"Param {message}(" in code
    assert code.count("if (stages >") == m4l_build.PHASE_STAGES


def test_unmask_device_has_a_side_chain_input():
    _type, patcher = m4l_build.read_amxd(m4l_build.UNMASK_AMXD.read_bytes())
    boxes = {b["box"]["id"]: b["box"] for b in patcher["patcher"]["boxes"]}
    assert boxes["obj-plugin"]["text"] == "plugin~ 1 2 3 4"      # channels 3-4: side-chain
    lines = [(l["patchline"]["source"], l["patchline"]["destination"])
             for l in patcher["patcher"]["lines"]]
    for (source, outlet), (destination, inlet) in lines:
        assert outlet < boxes[source]["numoutlets"] and inlet < boxes[destination]["numinlets"]
    assert {(o, i) for (s, o), (d, i) in lines if s == "obj-plugin" and d == "obj-ugen"} == {
        (n, n) for n in range(4)}
    names = [b["saved_attribute_attributes"]["valueof"]["parameter_longname"]
             for b in boxes.values() if b.get("parameter_enable")]
    assert names == ["Freq", "Q", "Depth", "Threshold", "Attack", "Release"]
    gen = json.loads(m4l_build.UNMASK_GENDSP.read_text(encoding="utf-8"))["patcher"]
    assert sum(1 for b in gen["boxes"] if b["box"].get("text", "").startswith("in ")) == 4


def test_generated_devices_keep_full_names_and_fit():
    """Live's API names a Max parameter by its short name: never cut it. Every control sits
    inside Live's device height (169 px) and inside the device width."""
    for path in (m4l_build.MASTER_AMXD, m4l_build.CHANNEL_AMXD, m4l_build.PHASE_AMXD,
                 m4l_build.UNMASK_AMXD):
        _type, patcher = m4l_build.read_amxd(path.read_bytes())
        body = patcher["patcher"]
        for item in body["boxes"]:
            box = item["box"]
            if box.get("parameter_enable") and "saved_attribute_attributes" in box:
                valueof = box["saved_attribute_attributes"]["valueof"]
                if "parameter_shortname" in valueof:
                    assert valueof["parameter_shortname"] == valueof["parameter_longname"], \
                        (path.name, valueof)
            if box.get("presentation") and "presentation_rect" in box:
                x, y, w, h = box["presentation_rect"]
                assert y + h <= 169 and x + w <= body["devicewidth"] + 1, (path.name, box["id"])


def test_compressors_hear_their_own_signal():
    """abl.device.compressor~'s second inlet is its detector (side-chain): unfed, it never
    compresses (Live report: threshold/ratio ignored, only the makeup gain worked)."""
    for path in (m4l_build.MASTER_AMXD, m4l_build.CHANNEL_AMXD):
        _type, patcher = m4l_build.read_amxd(path.read_bytes())
        feeds: dict = {}
        for line in patcher["patcher"]["lines"]:
            key = tuple(line["patchline"]["destination"])
            feeds.setdefault(key, set()).add(line["patchline"]["source"][0])
        for comp, eq in (("obj-compl", "obj-eql"), ("obj-compr", "obj-eqr")):
            assert eq in feeds[(comp, 0)] and feeds[(comp, 1)] == {eq}, (path.name, comp)


def test_frame_fields_agree_everywhere():
    js = ENGINE_JS.read_text(encoding="utf-8")
    fields = re.findall(r'"(\w+)"', js[js.index("const FRAME_FIELDS"):js.index("];",
                                                                       js.index("FRAME_FIELDS"))])
    assert tuple(fields) == engine.FIELDS
    assert [b[0] for b in m4l_build.BANDS] == [b[0] for b in __import__(
        "livebridge_mcp.audio_analysis", fromlist=["BANDS"]).BANDS]


# ----------------------------------------------------------------------------- the Node script

HARNESS = r"""
// Runs engine.js with a stand-in for Max's "max-api"; stdin lines are [handler, ...args].
const path = require("path");
global.__maxHandlers = {};
require(process.argv[2]);
require("readline").createInterface({ input: process.stdin }).on("line", (line) => {
  const [name, ...args] = JSON.parse(line);
  global.__maxHandlers[name](...args);
});
"""
MAX_API_STUB = r"""
// Stand-in for Max: outlets are logged to OUTLET_LOG; "capture" messages act like the patch's
// sfrecord~ ("open <path>", 1, 0) and write a stereo WAV holding a STUB_FREQ Hz sine.
const fs = require("fs");
const recorder = { file: null, started: 0, playing: 0 };

function writeWav(file, seconds) {
  const rate = 44100, frames = Math.round(seconds * rate), freq = Number(process.env.STUB_FREQ);
  const data = Buffer.alloc(frames * 4);
  for (let n = 0; n < frames; n++) {
    const v = Math.round(0.5 * 32767 * Math.sin(2 * Math.PI * freq * n / rate));
    data.writeInt16LE(v, n * 4);
    data.writeInt16LE(v, n * 4 + 2);
  }
  const head = Buffer.alloc(44);
  head.write("RIFF", 0); head.writeUInt32LE(36 + data.length, 4); head.write("WAVE", 8);
  head.write("fmt ", 12); head.writeUInt32LE(16, 16); head.writeUInt16LE(1, 20);
  head.writeUInt16LE(2, 22); head.writeUInt32LE(rate, 24); head.writeUInt32LE(rate * 4, 28);
  head.writeUInt16LE(4, 32); head.writeUInt16LE(16, 34); head.write("data", 36);
  head.writeUInt32LE(data.length, 40);
  fs.writeFileSync(file, Buffer.concat([head, data]));
}

module.exports = {
  addHandler: (name, fn) => { global.__maxHandlers[name] = fn; },
  outlet: (...args) => {
    fs.appendFileSync(process.env.OUTLET_LOG, JSON.stringify(args) + "\n");
    if (args[0] === "play") {
      fs.appendFileSync(process.env.OUTLET_LOG + ".play", JSON.stringify([Date.now(), ...args])
        + "\n");
    }
    if (args[0] === "resolve") {
      // like context.js: parameters 99 does not exist; everything else is a 20..20000 knob at
      // 1000 whose id is 100 + the parameter index
      const last = Number(args[args.length - 1]);
      const index = Number.isInteger(last) ? last : 50;
      setImmediate(() => global.__maxHandlers.resolved(
        args[1], index === 99 ? 0 : 100 + index, 20, 20000, 1000, "Filter", "Freq"));
      return;
    }
    if (args[0] === "query") {
      // like context.js: track 0 has clips in slots 0, 1 and 3 (playing: the last fired),
      // the set has 4 scenes
      const flags = args[2] === "scenes" ? [1, 1, 1, 1] : [1, 1, 0, 1, 0];
      const playing = args[2] === "scenes" ? -1 : recorder.playing;
      setImmediate(() => global.__maxHandlers.info(args[1], playing, -1, ...flags));
      return;
    }
    if (args[0] === "getvalues") {
      // like context.js reading the parameters: 0.5 each
      const count = String(args[2]).split("|").length;
      setImmediate(() => global.__maxHandlers.values(args[1], ...Array(count).fill(0.5)));
      return;
    }
    if (args[0] === "lomdump") {
      // like context.js: write the walk's JSON to the file, then answer with the type count
      const types = { Song: { type: "Song", path: "live_set", properties: [["tempo", "float"]],
                              functions: ["start_playing"], children: [["tracks", "Track", true]] } };
      fs.writeFileSync(args.slice(1).join(" "), JSON.stringify(
        { live_version: "12.4.15", max_version: "9.1.2", objects_looked_at: 3, types: types }));
      setImmediate(() => global.__maxHandlers.lomdumped(1));
      return;
    }
    if (args[0] === "call" && args[1] === "fire" && args.indexOf("clip_slots") > 0) {
      recorder.playing = args[args.length - 1];
      return;
    }
    if (args[0] !== "capture") {
      return;
    }
    if (args[1] === "open") {
      recorder.file = args[2];
    } else if (args[1] === 1) {
      recorder.started = Date.now();
    } else if (args[1] === 0 && recorder.file && process.env.STUB_FREQ !== "none") {
      writeWav(recorder.file, (Date.now() - recorder.started) / 1000);
    }
  },
  post: () => {},
};
"""


@pytest.fixture
def engines(tmp_path, monkeypatch):
    """Start engine.js processes (Node, stand-in max-api) sharing one registry and capture
    folder: ``start(freq=None)`` -> (process, send, outlets log)."""
    if NODE is None:
        pytest.skip("node is not installed")
    stub = tmp_path / "node_modules" / "max-api"
    stub.mkdir(parents=True)
    (stub / "index.js").write_text(MAX_API_STUB, encoding="utf-8")
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS, encoding="utf-8")
    registry, captures = tmp_path / "engine", tmp_path / "captures"
    monkeypatch.setenv("LIVEBRIDGE_ENGINE_DIR", str(registry))
    monkeypatch.setenv("LIVEBRIDGE_CAPTURE_DIR", str(captures))
    started = []

    def start(freq=None, script=ENGINE_JS):
        outlets = tmp_path / f"outlets{len(started)}.log"
        env = dict(os.environ, NODE_PATH=str(tmp_path / "node_modules"),
                   LIVEBRIDGE_ENGINE_DIR=str(registry), LIVEBRIDGE_CAPTURE_DIR=str(captures),
                   OUTLET_LOG=str(outlets), STUB_FREQ=str(freq or "none"))
        before = set(registry.glob("*.json"))
        process = subprocess.Popen([NODE, str(harness), str(script)], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                                   text=True)
        started.append(process)

        def send(*message):
            process.stdin.write(json.dumps(list(message)) + "\n")
            process.stdin.flush()

        assert engine.wait_for(lambda: set(registry.glob("*.json")) - before, 10), \
            process.stderr.read() if process.poll() is not None else "no registry file"
        return process, send, outlets

    start.registry, start.captures = registry, captures
    try:
        yield start
    finally:
        for process in started:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(5)
                except subprocess.TimeoutExpired:
                    process.kill()


@pytest.fixture
def running_engine(engines):
    process, send, outlets = engines()
    return process, send, engines.registry, outlets


TONE_FRAME = [0.125, 0.125, 0.5, 0.5, 0.25, 1e-6, 1e-6, 1e-5, 1e-4, 0.12, 1e-4, 1e-6, 1e-7,
              0.125, 0.0, 0.125, 1e-6, 0.0]


def test_engine_script_end_to_end(running_engine):
    process, send, registry, outlets = running_engine

    def sent():
        return [json.loads(line) for line in outlets.read_text(encoding="utf-8").splitlines()]

    # once listening, the script asks context.js where the device sits (the load-time report
    # from live.thisdevice arrives before Node runs and is lost)
    assert engine.wait_for(lambda: outlets.exists() and ["context"] in sent(), 5)
    entry = json.loads(next(registry.glob("*.json")).read_text(encoding="utf-8"))
    assert entry["role"] == "unknown"
    assert entry["host"] == "127.0.0.1" and len(entry["token"]) == 48
    if os.name == "posix":
        assert (next(registry.glob("*.json")).stat().st_mode & 0o777) == 0o600
    send("context", "master", "Main")
    assert engine.wait_for(lambda: engine.devices() and engine.devices()[0]["role"] ==
                           "master", 5)
    assert ["status", "master"] in sent()
    with pytest.raises(engine.EngineError, match="token") as refused:
        engine.request(dict(entry, token="wrong"), "hello")
    assert refused.value.type == "auth"
    for _ in range(12):
        send("frame", *TONE_FRAME)
        time.sleep(0.02)
    send("frame", 1, 2)                                     # wrong length: ignored
    app = create_app(BridgeClient(host="127.0.0.1", port=9, timeout=0.5))
    status = call_tool(app, "live_engine_status", {})
    assert status["devices"][0]["role"] == "master" and status["devices"][0][
        "frames_buffered"] == 12
    heard = call_tool(app, "live_engine_listen", {"seconds": 5, "history": True,
                                                  "timeline": True})
    assert heard["device"] == {"role": "master", "track": "Main"}
    assert heard["frames"] == 12 and heard["loudness"]["peak_db"] == pytest.approx(-6.0, abs=0.1)
    assert heard["loudness"]["momentary_lufs"] == pytest.approx(-0.691 + 10 * math.log10(0.25),
                                                               abs=0.1)
    assert heard["stereo"]["correlation"] == 1.0 and heard["timeline"]

    # listening from now on: only frames that arrive during the window count
    feeder = threading.Thread(target=lambda: [send("frame", *TONE_FRAME) or time.sleep(0.05)
                                              for _ in range(8)])
    feeder.start()
    live = call_tool(app, "live_engine_listen", {"seconds": 0.6})
    feeder.join()
    assert 4 <= live["frames"] <= 8

    assert call_tool(app, "live_engine_listen", {"track": "Bass"})["type"] == "not_found"
    if os.name == "posix":
        process.send_signal(signal.SIGTERM)
        process.wait(5)
        assert not list(registry.glob("*.json"))             # registry removed on exit


def test_engine_tools_without_a_device(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVEBRIDGE_ENGINE_DIR", str(tmp_path / "none"))
    app = create_app(BridgeClient(host="127.0.0.1", port=9, timeout=0.5))
    status = call_tool(app, "live_engine_status", {})
    assert status["devices"] == [] and "Max Audio Effect" in status["hint"]
    missing = call_tool(app, "live_engine_listen", {})
    assert missing["type"] == "not_found" and "LiveBridge Engine" in missing["error"]
    assert call_tool(app, "live_engine_listen", {"seconds": 0})["type"] == "bad_args"
    assert call_tool(app, "live_engine_listen", {"seconds": 45})["type"] == "bad_args"
    assert call_tool(app, "live_engine_listen", {"kind": "loud"})["type"] == "bad_args"


def test_a_busy_device_is_not_forgotten(tmp_path, monkeypatch):
    # it accepts the connection but answers too late: keep its registry file
    import socket as socketlib
    monkeypatch.setenv("LIVEBRIDGE_ENGINE_DIR", str(tmp_path))
    server = socketlib.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    try:
        entry = {"port": server.getsockname()[1], "token": "t", "instance": "busy"}
        (tmp_path / "busy.json").write_text(json.dumps(entry))
        assert engine.devices(timeout=0.3) == []
        assert (tmp_path / "busy.json").exists()
        with pytest.raises(engine.EngineError) as late:
            engine.request(entry, "hello", timeout=0.3)
        assert late.value.type == "timeout"
    finally:
        server.close()


def test_stale_registry_files_are_removed(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVEBRIDGE_ENGINE_DIR", str(tmp_path))
    import socket
    with socket.socket() as probe:                            # a port nobody listens on
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    stale = tmp_path / "dead.json"
    stale.write_text(json.dumps({"instance": "dead", "port": port, "token": "x"}),
                     encoding="utf-8")
    (tmp_path / "junk.json").write_text("{not json", encoding="utf-8")
    assert engine.devices() == [] and not stale.exists()


# ----------------------------------------------------------------------------- step 2: several
# devices, captures, live masking, A/B

def _placed(engines, role, track, freq=None):
    process, send, outlets = engines(freq)
    send("context", role, *track.split())
    return process, send


def _wait_roles(count):
    assert engine.wait_for(lambda: len([d for d in engine.devices()
                                        if d.get("role") != "unknown"]) == count, 5)


def _feed(send, frame, count=12, pause=0.02):
    for _ in range(count):
        send("frame", *frame)
        time.sleep(pause)


def test_listen_to_all_devices_and_compare(engines):
    _process, master = _placed(engines, "master", "Main")
    _process, bass = _placed(engines, "track", "Bass")
    _wait_roles(2)
    quiet = [v / 4 for v in TONE_FRAME]
    for _ in range(12):
        master.__call__("frame", *TONE_FRAME)
        bass.__call__("frame", *quiet)
        time.sleep(0.02)
    app = create_app(BridgeClient(host="127.0.0.1", port=9, timeout=0.5))
    both = call_tool(app, "live_engine_listen", {"track": "all", "history": True,
                                                 "seconds": 5})
    assert [d["device"] for d in both["devices"]] == [
        {"role": "master", "track": "Main"}, {"role": "track", "track": "Bass"}]
    louder, softer = (d["loudness"]["momentary_lufs"] for d in both["devices"])
    assert louder - softer == pytest.approx(6.0, abs=0.2)
    assert call_tool(app, "live_engine_listen", {"track": "all", "save_as": "x"})["type"] \
        == "bad_args"

    saved = call_tool(app, "live_engine_listen", {"track": "bass", "history": True,
                                                  "seconds": 5, "save_as": "before"})
    assert saved["saved_as"] == "before"
    feeder = threading.Thread(target=_feed, args=(bass, TONE_FRAME, 10, 0.05))
    feeder.start()
    compared = call_tool(app, "live_engine_compare", {"before": "before", "seconds": 0.45})
    feeder.join()
    assert compared["after"]["device"] == {"role": "track", "track": "Bass"}
    assert compared["comparison"]["delta"]["lufs"] == pytest.approx(6.0, abs=0.3)
    assert any(c.startswith("loudness (LUFS) +6") for c in compared["comparison"]["changes"])
    assert compared["saved"] == ["before"]
    again = call_tool(app, "live_engine_compare", {"before": "before", "after": "before"})
    assert again["comparison"]["changes"] == ["no measurable change"]
    missing = call_tool(app, "live_engine_compare", {"before": "nope"})
    assert missing["type"] == "not_found" and "before" in missing["error"]


def test_capture_records_every_device_at_the_same_moment(engines):
    _placed(engines, "master", "Main", freq=440)
    _placed(engines, "track", "Bass", freq=80)
    _wait_roles(2)
    found = engine.devices()
    answers = engine.capture(found, 0.5)
    assert all(isinstance(a, dict) for a in answers), answers
    assert abs(answers[0]["started"] - answers[1]["started"]) < 100
    names = [Path(a["file_path"]).name for a in answers]
    assert names[0].startswith("lb-") and names[0].endswith("-master.wav")
    assert names[1].endswith("-Bass.wav")
    assert all(Path(a["file_path"]).parent == engines.captures for a in answers)

    app = create_app(BridgeClient(host="127.0.0.1", port=9, timeout=0.5))
    result = call_tool(app, "live_engine_capture", {"seconds": 0.5, "tracks": ["Bass"]})
    (only,) = result["captures"]
    assert only["device"] == {"role": "track", "track": "Bass"}
    assert only["seconds"] == pytest.approx(0.5, abs=0.1)
    sf = pytest.importorskip("soundfile")
    data, rate = sf.read(only["file_path"])
    assert data.shape[1] == 2 and len(data) / rate == pytest.approx(0.5, abs=0.1)

    # the device refuses names with folders, overlong waits and a second capture at once
    device = engine.choose(engine.devices(), "Bass")
    for args in ({"seconds": 1, "name": "../x.wav"}, {"seconds": 1, "name": "a.txt"},
                 {"seconds": 0, "name": "a.wav"}, {"seconds": 1, "name": "a.wav",
                                                    "at": time.time() * 1000 + 60000}):
        with pytest.raises(engine.EngineError) as refused:
            engine.request(device, "capture", args)
        assert refused.value.type == "bad_args", args
    first = threading.Thread(target=engine.request, args=(device, "capture",
                                                           {"seconds": 0.6, "name": "a.wav"}))
    first.start()
    time.sleep(0.2)
    with pytest.raises(engine.EngineError) as busy:
        engine.request(device, "capture", {"seconds": 0.5, "name": "b.wav"})
    first.join()
    assert busy.value.type == "busy"
    assert call_tool(app, "live_engine_capture", {"seconds": 0.1})["type"] == "bad_args"
    assert call_tool(app, "live_engine_capture", {"tracks": ["Drums"]})["type"] == "not_found"


def test_lom_dump_and_its_markdown(engines, tmp_path, monkeypatch):
    _placed(engines, "track", "Bass")
    _wait_roles(1)
    (device,) = engine.devices()
    dump = engine.request(device, "lom_dump", timeout=10)
    assert dump["types"]["Song"]["functions"] == ["start_playing"]
    assert dump["node_version"].startswith("v") and dump["engine_version"] == device["version"]
    assert not list((tmp_path / "captures").glob("lom-dump-*"))      # the file is cleaned up
    import dump_m4l_lom
    monkeypatch.setattr(dump_m4l_lom, "DOCS", str(tmp_path))
    (tmp_path / "LIVE_API_DUMP_12.4.15.json").write_text(json.dumps({"classes": {
        "Live.Song.Song": {"members": {"tempo": {"kind": "property"},
                                       "name": {"kind": "property"},
                                       "add_tempo_listener": {"kind": "method"}}}}}))
    text = dump_m4l_lom.markdown(dump)
    assert "Python only: `name`" in text and "Max only: `start_playing`, `tracks`" in text
    assert dump_m4l_lom.main([]) == 0
    assert (tmp_path / "LIVE_M4L_LOM_12.4.15.md").is_file()


def test_capture_failure_is_reported(engines):
    _placed(engines, "track", "Bass", freq=None)              # writes no file
    _wait_roles(1)
    app = create_app(BridgeClient(host="127.0.0.1", port=9, timeout=0.5))
    failed = call_tool(app, "live_engine_capture", {"seconds": 0.5})
    assert failed["type"] == "invalid_state" and "no audio" in failed["error"]


def test_old_captures_are_pruned(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVEBRIDGE_CAPTURE_DIR", str(tmp_path))
    old, new, other = tmp_path / "lb-old.wav", tmp_path / "lb-new.wav", tmp_path / "mine.wav"
    for path in (old, new, other):
        path.write_bytes(b"x")
    os.utime(old, (time.time() - 8 * 86400,) * 2)
    os.utime(other, (time.time() - 8 * 86400,) * 2)
    engine.prune_captures()
    assert not old.exists() and new.exists() and other.exists()


def test_live_masking_uses_fader_levels_and_skips_muted_tracks(engines, tcp_bridge, song):
    pytest.importorskip("scipy")
    _placed(engines, "master", "Main", freq=1000)
    _placed(engines, "track", "Bass", freq=80)
    _placed(engines, "track", "Drums", freq=80)
    _placed(engines, "track", "Vocals", freq=3000)
    _wait_roles(4)
    app = create_app(BridgeClient(host="127.0.0.1", port=tcp_bridge.port, timeout=10.0))
    song.tracks[1].mute = True                                # Vocals
    result = call_tool(app, "live_engine_masking", {"seconds": 2})
    assert "Vocals is muted — left out." in result["notes"]
    (pair,) = result["pairs"]
    assert sorted(pair["parts"]) == ["Bass", "Drums"] and pair["score"] >= 50
    assert pair["bands"][0]["region"] in ("sub", "bass")
    assert len(result["captures"]) == 2

    # Drums 18 dB down at its fader (0.4): the 80 Hz overlap is no longer "close in level"
    song.tracks[1].mute = False
    song.tracks[2].mixer_device.volume.value = 0.4
    spread = call_tool(app, "live_engine_masking", {"seconds": 2, "tracks": ["Bass", "Drums",
                                                                             "Vocals"]})
    scores = {tuple(sorted(p["parts"])): p["score"] for p in spread["pairs"]}
    assert scores[("Bass", "Drums")] < pair["score"]
    assert scores[("Bass", "Vocals")] < 10

    assert call_tool(app, "live_engine_masking", {"tracks": ["Bass"]})["type"] == "bad_args"
    assert call_tool(app, "live_engine_masking", {"seconds": 1})["type"] == "bad_args"


# ----------------------------------------------------------------------------- step 3: live
# parameter modulation

def test_rates_and_paths():
    assert engine.parse_rate("1 bar") == {"beats": 4.0}
    assert engine.parse_rate("2 bars") == {"beats": 8.0}
    assert engine.parse_rate("3 beats") == {"beats": 3.0}
    assert engine.parse_rate("1/4") == {"beats": 1.0}
    assert engine.parse_rate("1/8d") == {"beats": 0.75}
    assert engine.parse_rate("1/16T") == {"beats": pytest.approx(1 / 6)}
    assert engine.parse_rate("0.5 Hz") == {"hz": 0.5}
    assert engine.parse_rate(2) == {"beats": 2.0}
    for bad in ("fast", "1/0", "", True):
        with pytest.raises(ValueError):
            engine.parse_rate(bad)
    assert engine.lom_to_max("song.tracks[0].devices[1].parameters[3]") == \
        "live_set tracks 0 devices 1 parameters 3"
    assert engine.lom_to_max("song.master_track.mixer_device.volume") == \
        "live_set master_track mixer_device volume"
    assert engine.lom_to_max("song.tracks[2].devices[0].drum_pads[36].chains[0]."
                             "devices[0].parameters[5]").startswith(
        "live_set tracks 2 devices 0 drum_pads 36 chains 0")
    for bad in ("tracks[0]", "song", "song.tracks[x]", "live_set tracks 0"):
        with pytest.raises(ValueError):
            engine.lom_to_max(bad)


SHAPE_SCRIPT = r"""
const engine = require(process.argv[1]);
const mod = { shape: "", beats: 4, hz: null, low: 0.25, high: 0.75, phase: 0, min: 0, max: 100,
              started: 0, seed: 7, steps: [0, 1, 0.5, 1] };
const out = {};
for (const shape of ["sine", "triangle", "saw", "ramp_down", "square", "random",
                     "smooth_random", "steps"]) {
  mod.shape = shape;
  out[shape] = [0, 0.125, 0.25, 0.5, 0.75, 0.999, 1.25].map((c) => engine.shapeAt(mod, c));
}
// song position: Live at beat 2 of a 1-bar saw, playing at 120 BPM, reported 250 ms ago
engine.state.transport = { beat: 2, tempo: 120, playing: true, at: Date.now() - 250 };
mod.shape = "saw";
out.synced = engine.modValue(mod, Date.now());
mod.phase = 0.5;
out.shifted = engine.modValue(mod, Date.now());
mod.phase = 0; mod.beats = null; mod.hz = 2; mod.started = Date.now() - 125;
out.free = engine.modValue(mod, Date.now());
process.stdout.write(JSON.stringify(out));
process.exit(0);
"""


def test_modulation_shapes_and_sync(tmp_path):
    if NODE is None:
        pytest.skip("node is not installed")
    env = dict(os.environ, LIVEBRIDGE_ENGINE_DIR=str(tmp_path))
    done = subprocess.run([NODE, "-e", SHAPE_SCRIPT, str(ENGINE_JS)], capture_output=True,
                          text=True, env=env, timeout=30)
    out = json.loads(done.stdout)
    assert out["sine"][:5] == pytest.approx([0.5, 0.5 + 0.5 * math.sin(math.pi / 4), 1, 0.5, 0])
    assert out["triangle"][:5] == pytest.approx([0, 0.25, 0.5, 1, 0.5])
    assert out["saw"][:5] == pytest.approx([0, 0.125, 0.25, 0.5, 0.75])
    assert out["ramp_down"][1] == pytest.approx(0.875)
    assert out["square"][:5] == [1, 1, 1, 0, 0]
    random = out["random"]
    assert len(set(random[:6])) == 1 and random[6] != random[0] and 0 <= random[0] < 1
    smooth = out["smooth_random"]
    assert smooth[0] == pytest.approx(random[0]) and smooth[5] == pytest.approx(random[6],
                                                                               abs=1e-3)
    assert out["steps"][:6] == [0, 0, 1, 0.5, 1, 1]
    # beat 2 + 0.5 beat since the report = 2.5 of 4 beats -> saw 0.625 -> 25 + 50 * 0.625
    assert out["synced"] == pytest.approx(56.25, abs=0.5)
    assert out["shifted"] == pytest.approx(25 + 50 * 0.125, abs=0.5)
    assert out["free"] == pytest.approx(25 + 50 * 0.25, abs=1.0)    # 2 Hz, 125 ms in
    assert not list(tmp_path.glob("*.json"))


def test_offset_modulation_uses_live_modulate(engines, tcp_bridge, song):
    """mode='offset': -1..1 around the user's value through live.modulate~, nothing restored."""
    _process, send, outlets = engines()
    send("context", "master", "Main")
    _wait_roles(1)

    def sent():
        return [json.loads(line) for line in outlets.read_text(encoding="utf-8").splitlines()]

    app = create_app(BridgeClient(host="127.0.0.1", port=tcp_bridge.port, timeout=10.0))
    started = call_tool(app, "live_engine_modulate", {
        "track": "Bass", "device": "Operator", "parameter": "Filter Freq", "shape": "sine",
        "rate": "1/4", "depth": 0.4, "mode": "offset"})
    mod = started["modulation"]
    assert mod["mode"] == "offset" and mod["depth"] == 0.4 and "low" not in mod
    log = sent()
    first = next(i for i, m in enumerate(log) if m[:3] == ["mod", 0, "offset"])
    assert first < log.index(["mod", 0, "oid", 102])
    assert not any(m[:3] in (["mod", 0, "value"], ["mod", 0, "id"]) for m in log)
    time.sleep(0.3)
    offsets = [m[3] for m in sent() if m[:3] == ["mod", 0, "offset"]]
    assert all(-0.2 - 1e-9 <= v <= 0.2 + 1e-9 for v in offsets) and \
        max(offsets) - min(offsets) > 0.1
    # the same parameter in the other mode: refused until this one stops
    other = call_tool(app, "live_engine_modulate", {
        "track": "Bass", "device": "Operator", "parameter": "Filter Freq", "mode": "replace"})
    assert other["type"] == "invalid_state" and "offset mode" in other["error"]
    for bad in ({"mode": "sideways"}, {"mode": "offset", "center": 0.5},
                {"mode": "offset", "depth": 3}):
        args = {"track": "Bass", "device": "Operator", "parameter": "Filter Freq", **bad}
        assert call_tool(app, "live_engine_modulate", args)["type"] == "bad_args"

    stopped = call_tool(app, "live_engine_unmodulate", {"all": True})
    assert stopped["stopped"]
    assert engine.wait_for(lambda: ["mod", 0, "oid", 0] in sent(), 3)
    log = sent()
    assert log.index(["mod", 0, "offset", 0, 20]) < log.index(["mod", 0, "oid", 0])
    assert not any(m[0] == "setvalue" for m in log)          # nothing to write back


def test_offset_modulation_needs_engine_1_7(tcp_bridge, song, monkeypatch):
    monkeypatch.setattr(engine, "devices", lambda *a, **k: [
        {"role": "master", "track": "Main", "version": "1.6.2", "modulations": [],
         "host": "127.0.0.1", "port": 9, "token": "x"}])
    app = create_app(BridgeClient(host="127.0.0.1", port=tcp_bridge.port, timeout=10.0))
    answer = call_tool(app, "live_engine_modulate", {
        "track": "Bass", "device": "Operator", "parameter": "Filter Freq", "mode": "offset"})
    assert answer["type"] == "unsupported" and "1.7" in answer["error"]


def test_live_modulation_end_to_end(engines, tcp_bridge, song):
    _process, send, outlets = engines()
    send("context", "master", "Main")
    _wait_roles(1)

    def sent():
        return [json.loads(line) for line in outlets.read_text(encoding="utf-8").splitlines()]

    app = create_app(BridgeClient(host="127.0.0.1", port=tcp_bridge.port, timeout=10.0))
    started = call_tool(app, "live_engine_modulate", {
        "track": "Bass", "device": "Operator", "parameter": "Filter Freq", "shape": "triangle",
        "rate": "1/4", "depth": 0.5})
    assert "modulation" in started, started
    mod = started["modulation"]
    assert started["engine"] == {"role": "master", "track": "Main"}
    assert mod["slot"] == 0 and mod["rate"] == {"beats": 1.0}
    assert mod["path"] == "song.tracks[0].devices[0].parameters[2]"
    assert (mod["low"], mod["high"]) in ((0.0, 0.5), (0.5, 1.0)) or \
        mod["high"] - mod["low"] == pytest.approx(0.5)
    # the first value goes to line~ before live.remote~ gets the parameter's id
    log = sent()
    first_value = next(i for i, m in enumerate(log) if m[:3] == ["mod", 0, "value"])
    take = log.index(["mod", 0, "id", 102])
    assert first_value < take and ["resolve", 0, "live_set", "tracks", 0, "devices", 0,
                                   "parameters", 2] in log
    time.sleep(0.3)
    values = [m[3] for m in sent() if m[:3] == ["mod", 0, "value"] and len(m) == 5]
    assert len(values) >= 5 and all(m == 20 for m in
                                    [x[4] for x in sent() if x[:3] == ["mod", 0, "value"]
                                     and len(x) == 5])
    low = 20 + (20000 - 20) * mod["low"]
    high = 20 + (20000 - 20) * mod["high"]
    assert all(low - 1 <= v <= high + 1 for v in values) and max(values) - min(values) > 100

    status = call_tool(app, "live_engine_status", {})
    (running,) = status["devices"][0]["modulations"]
    assert running["shape"] == "triangle" and running["original"] == 1000

    # again on the same parameter: replaced in its slot
    again = call_tool(app, "live_engine_modulate", {
        "track": "Bass", "device": "Operator", "parameter": "Filter Freq", "shape": "steps",
        "rate": "1 bar", "steps": [0, 1, 0, 1], "low": 0.1, "high": 0.9})
    assert "modulation" in again, again
    assert again["modulation"]["slot"] == 0 and again["modulation"]["rate"] == {"beats": 4.0}
    # a mixer parameter by path takes the next slot
    volume = call_tool(app, "live_engine_modulate", {
        "parameter": "song.tracks[1].mixer_device.volume", "shape": "sine", "rate": "2 Hz",
        "depth": 0.1})
    assert "modulation" in volume, volume
    assert volume["modulation"]["slot"] == 1 and volume["modulation"]["rate"] == {"hz": 2.0}

    stopped = call_tool(app, "live_engine_unmodulate", {"track": "Bass", "device": "Operator",
                                                        "parameter": "Filter Freq"})
    assert [m["slot"] for m in stopped["stopped"]] == [0]
    # the original goes in first, the parameter is let go a few audio vectors later (not in
    # the same instant, or live.remote~ keeps the LFO's last value), then written once more
    assert engine.wait_for(lambda: any(m[:2] == ["setvalue", 1000] for m in sent()), 3)
    log = sent()
    restore = next(i for i, m in enumerate(log) if m[:2] == ["setvalue", 1000])
    assert log.index(["mod", 0, "value", 1000]) < log.index(["mod", 0, "id", 0]) < restore
    path = " ".join(str(a) for a in log[restore][2:])
    assert path.startswith("live_set tracks") and path.endswith("parameters 2"), path
    assert len(call_tool(app, "live_engine_status", {})["devices"][0]["modulations"]) == 1
    everything = call_tool(app, "live_engine_unmodulate", {"all": True})
    assert [m["slot"] for m in everything["stopped"]] == [1]
    assert call_tool(app, "live_engine_unmodulate", {"all": True})["note"] == \
        "nothing was modulated"

    # 8 slots per device, then "busy"; a parameter Live does not have is refused
    device = engine.choose(engine.devices())
    for index in range(8):
        engine.request(device, "modulate", {"path": f"live_set tracks 0 devices 0 parameters "
                                                    f"{index}", "beats": 1})
    with pytest.raises(engine.EngineError) as busy:
        engine.request(device, "modulate", {"path": "live_set tracks 0 devices 0 parameters 8",
                                            "beats": 1})
    assert busy.value.type == "busy"
    engine.request(device, "unmodulate", {"all": True})
    with pytest.raises(engine.EngineError) as missing:
        engine.request(device, "modulate", {"path": "live_set tracks 0 devices 0 parameters "
                                                    "99", "beats": 1})
    assert missing.value.type == "not_found"
    for args in ({"path": "tracks 0", "beats": 1}, {"path": "live_set tracks 0", "beats": 1,
                                                    "hz": 1},
                 {"path": "live_set tracks 0", "beats": 1, "shape": "wobble"},
                 {"path": "live_set tracks 0", "beats": 1, "low": 2},
                 {"path": "live_set tracks 0", "beats": 1, "shape": "steps", "steps": []}):
        with pytest.raises(engine.EngineError) as refused:
            engine.request(device, "modulate", args)
        assert refused.value.type == "bad_args", args


def test_modulate_argument_checks(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVEBRIDGE_ENGINE_DIR", str(tmp_path / "none"))
    app = create_app(BridgeClient(host="127.0.0.1", port=9, timeout=0.5))
    for args in ({"parameter": "x", "shape": "wobble"}, {"parameter": "x", "rate": "fast"},
                 {"parameter": "x", "rate": "100 bars"}, {"parameter": "x", "rate": "50 Hz"},
                 {"parameter": "x", "shape": "steps"},
                 {"parameter": "x", "steps": [0.5]}, {"parameter": "x", "phase": 1},
                 {"parameter": "x", "depth": 2}, {"parameter": "x", "low": 0.2}):
        assert call_tool(app, "live_engine_modulate", args)["type"] == "bad_args", args
    assert call_tool(app, "live_engine_unmodulate", {})["type"] == "bad_args"
    assert call_tool(app, "live_engine_unmodulate", {"all": True, "parameter": 1})["type"] \
        == "bad_args"


# ----------------------------------------------------------------------------- step 4: follow
# actions

TARGET_SCRIPT = r"""
const engine = require(process.argv[1]);
let draws = 0;
const rule = { seed: 3, draws: 0 };
const clips = [0, 1, 3, 6];
const out = {};
for (const action of ["next", "previous", "first", "last", "again", "jump:3", "jump:2"]) {
  out[action] = [0, 3, 6, -1, 4].map((current) => engine.targetIndex(rule, action, current,
                                                                       clips));
}
out.other = Array.from({ length: 40 }, () => engine.targetIndex(rule, "other", 1, clips));
out.any = Array.from({ length: 40 }, () => engine.targetIndex(rule, "any", 1, clips));
out.empty = engine.targetIndex(rule, "next", 0, []);
process.stdout.write(JSON.stringify(out));
process.exit(0);
"""


def test_follow_action_targets(tmp_path):
    if NODE is None:
        pytest.skip("node is not installed")
    done = subprocess.run([NODE, "-e", TARGET_SCRIPT, str(ENGINE_JS)], capture_output=True,
                          text=True, timeout=30,
                          env=dict(os.environ, LIVEBRIDGE_ENGINE_DIR=str(tmp_path)))
    out = json.loads(done.stdout)
    # from slot 0, 3, 6 (the last, wraps), nothing playing (-1) and an empty slot 4
    assert out["next"] == [1, 6, 0, 0, 6]
    assert out["previous"] == [6, 1, 3, 6, 3]
    assert out["first"] == [0] * 5 and out["last"] == [6] * 5
    assert out["again"] == [0, 3, 6, 0, 0]
    assert out["jump:3"] == [3] * 5 and out["jump:2"] == [None] * 5
    assert 1 not in out["other"] and set(out["other"]) == {0, 3, 6}
    assert set(out["any"]) == {0, 1, 3, 6}
    assert out["empty"] is None


def _calls(outlets):
    return [json.loads(line) for line in outlets.read_text(encoding="utf-8").splitlines()
            if line.startswith('["call"')]


def test_follow_actions_end_to_end(engines, tcp_bridge, song):
    _process, send, outlets = engines()
    send("context", "master", "Main")
    _wait_roles(1)
    app = create_app(BridgeClient(host="127.0.0.1", port=tcp_bridge.port, timeout=10.0))
    started = call_tool(app, "live_engine_follow", {"target": "Bass", "every": "1 bar",
                                                    "actions": "next"})
    assert started["follow"]["kind"] == "track" and started["follow"]["every_beats"] == 4.0
    assert started["follow"]["target"] == "Bass" and "note" in started   # transport stopped
    lead = started["follow"]["lead_beats"]
    assert 0 <= lead <= 0.25
    time.sleep(0.3)
    assert _calls(outlets) == []                       # nothing while Live is stopped

    # 960 BPM: a bar every 0.25 s, from beat 0
    send("transport", 0, 960, 1)
    assert engine.wait_for(lambda: len(_calls(outlets)) >= 4, 5)
    fired = [c[-1] for c in _calls(outlets)][:4]
    assert fired == [1, 3, 0, 1]                       # slots with clips: 0, 1, 3 (wrapping)
    status = call_tool(app, "live_engine_status", {})
    (rule,) = status["devices"][0]["follow"]
    assert rule["target"] == "live_set tracks 0"
    beats = [h["beat"] for h in rule["history"]]
    assert beats[:3] == [4, 8, 12] and all(h["action"] == "next" for h in rule["history"])

    # scenes, weighted with a chance, reproducible with a seed
    call_tool(app, "live_engine_unfollow", {"target": "Bass"})
    count = len(_calls(outlets))
    scenes = call_tool(app, "live_engine_follow", {"target": "scenes", "every": "1 bar",
                                                   "actions": {"other": 1, "stop": 1},
                                                   "chance": 0.7, "seed": 5})
    assert scenes["follow"]["kind"] == "scenes"
    assert engine.wait_for(lambda: len(_calls(outlets)) >= count + 3, 6)
    later = _calls(outlets)[count:]
    assert all(c[1] == "stop_all_clips" and c[2:] == ["live_set"] or
               c[1:4] == ["fire", "live_set", "scenes"] for c in later)
    stopped = call_tool(app, "live_engine_unfollow", {"all": True})
    (gone,) = stopped["stopped"]
    assert gone["kind"] == "scenes" and gone["history"]
    settled = len(_calls(outlets))
    time.sleep(0.6)
    assert len(_calls(outlets)) == settled             # nothing after unfollow
    assert call_tool(app, "live_engine_unfollow", {"all": True})["note"] == \
        "no follow actions were running"

    for args in ({"target": "Bass", "every": "0.5 Hz"}, {"target": "Bass", "actions": "jump"},
                 {"target": "Bass", "actions": {"next": 0}}, {"target": "Bass", "chance": 0},
                 {"target": "Bass", "every": "1/64"}):
        assert call_tool(app, "live_engine_follow", args)["type"] == "bad_args", args
    assert call_tool(app, "live_engine_follow", {"target": "Nope"})["type"] == "not_found"
    assert call_tool(app, "live_engine_unfollow", {})["type"] == "bad_args"


# ----------------------------------------------------------------------------- fast arrangement
# automation recording (the Engine writes, the Remote Script runs the pass)

def test_curve_values(tmp_path):
    if NODE is None:
        pytest.skip("node is not installed")
    script = r"""
const e = require(process.argv[1]);
const pts = [[4, 100], [8, 1000], [12, 0]];
process.stdout.write(JSON.stringify([2, 4, 6, 8, 10, 13].map((b) => e.curveAt(pts, "linear", b))
  .concat([6, 9].map((b) => e.curveAt(pts, "step", b)))));
process.exit(0);
"""
    done = subprocess.run([NODE, "-e", script, str(ENGINE_JS)], capture_output=True, text=True,
                          timeout=30, env=dict(os.environ, LIVEBRIDGE_ENGINE_DIR=str(tmp_path)))
    assert json.loads(done.stdout) == [100, 100, 550, 1000, 500, 0, 100, 1000]


def test_fast_automation_recording_with_the_engine(engines, tcp_bridge, song):
    _process, send, outlets = engines()
    send("context", "master", "Main")
    _wait_roles(1)
    app = create_app(BridgeClient(host="127.0.0.1", port=tcp_bridge.port, timeout=10.0))
    param = song.tracks[0].devices[0].parameters[2]
    before = param.value
    started = call_tool(app, "live_automation_record", {
        "track": "Bass", "parameter": "Filter Freq", "points": [[4, 100], [8, 1000]],
        "preroll": 0, "speed": "fast"})
    assert started["speed"]["tempo_during_pass"] == 600.0, started
    assert started["speed"]["writer"].startswith("LiveBridge Engine")
    assert started["speed"]["beats_between_writes"] == pytest.approx(0.2)
    assert "curve" not in started and "path" not in started
    assert song.tempo == 600.0
    # Live plays from beat 4 at 600 BPM: 4 beats take 0.4 s
    send("transport", 4, 600, 1)
    sent = lambda: [json.loads(line) for line in  # noqa: E731
                    outlets.read_text(encoding="utf-8").splitlines()]
    assert engine.wait_for(lambda: any(m[0] == "setvalue" and m[1] == 1000 for m in sent()), 5)
    log = sent()
    values = [m[1] for m in log if m[0] == "setvalue"]
    # Max's DeviceParameter has no begin_gesture / end_gesture (LOM dump 12.4.15)
    assert not any(m[0] == "call" and "gesture" in str(m[1]) for m in log)
    assert len(values) >= 8 and values == sorted(values) and values[-1] == 1000
    assert values[0] == pytest.approx(100, abs=60)
    assert param.value == before                      # the Remote Script did not write
    # the Remote Script ends the pass a little after the end and restores the tempo
    song.current_song_time = 9.0
    assert engine.wait_for(lambda: call_tool(app, "live_automation_record",
                                             {"action": "status"}).get("finished"), 5)
    status = call_tool(app, "live_automation_record", {"action": "status"})
    assert status["phase"] == "done" and status["engine"]["phase"] == "done"
    assert status["engine"]["writes"] == len(values) and song.tempo == 124.0


def test_fast_automation_recording_without_the_engine(tcp_bridge, song, tmp_path,
                                                      monkeypatch):
    monkeypatch.setenv("LIVEBRIDGE_ENGINE_DIR", str(tmp_path))
    app = create_app(BridgeClient(host="127.0.0.1", port=tcp_bridge.port, timeout=10.0))
    started = call_tool(app, "live_automation_record", {
        "track": "Bass", "parameter": "Filter Freq", "points": [[4, 100], [8, 1000]],
        "preroll": 0, "speed": 2})
    assert started["speed"]["tempo_during_pass"] == 248.0
    assert started["speed"]["writer"].startswith("Remote Script") and song.tempo == 248.0
    stopped = call_tool(app, "live_automation_record", {"action": "stop"})
    assert stopped["id"] == started["id"]
    assert engine.wait_for(lambda: call_tool(app, "live_automation_record",
                                             {"action": "status"}).get("finished"), 5)
    assert song.tempo == 124.0
    fast = call_tool(app, "live_automation_record", {
        "track": "Bass", "parameter": "Filter Freq", "points": [[4, 100], [8, 1000]],
        "preroll": 0, "speed": "fast"})
    assert fast["speed"]["tempo_during_pass"] == 300.0
    call_tool(app, "live_automation_record", {"action": "stop"})
    assert engine.wait_for(lambda: call_tool(app, "live_automation_record",
                                             {"action": "status"}).get("finished"), 5)
    for speed in ("turbo", 0.5, 50):
        assert call_tool(app, "live_automation_record", {
            "track": "Bass", "parameter": "Filter Freq", "points": [[4, 100], [8, 1000]],
            "speed": speed})["type"] == "bad_args", speed
    refused = call_tool(app, "live_automation_record", {
        "track": "master", "parameter": "tempo", "points": [[0, 120], [4, 130]],
        "speed": "fast"})
    assert refused["type"] == "bad_args" and "tempo itself" in refused["error"]



def test_arrangement_automation_jobs_through_the_engine(engines, tcp_bridge, song):
    """copy_range with an Engine: it samples the lane (stand-in: 0.5) while the source range
    plays and writes it at the destination, at 600 BPM."""
    from live_stub_ext import arrangement_live
    import Live
    from test_arrangement_automation import SongClock

    _process, send, outlets = engines()
    send("context", "master", "Main")
    _wait_roles(1)

    class EngineClock(SongClock):
        def _run(self):
            last = time.time()
            told = 0.0
            while self.running:
                time.sleep(0.005)
                now = time.time()
                if self.song.is_playing:
                    self.song.current_song_time += self.song.tempo / 60.0 * (now - last)
                if now - told > 0.02:                   # what context.js reports
                    send("transport", self.song.current_song_time, self.song.tempo,
                         1 if self.song.is_playing else 0)
                    told = now
                last = now

    uninstall = arrangement_live.install(Live, copy_envelopes="without_devices")
    clock = EngineClock(song, [])
    clock.start()
    bridge = BridgeClient(host="127.0.0.1", port=tcp_bridge.port, timeout=10.0)
    app = create_app(bridge)
    try:
        resonance = song.tracks[0].devices[0].parameters[3]
        resonance._automation_state = 1
        call_tool(app, "live_arrangement_duplicate_clip", {"track": "Bass", "slot": 0,
                                                           "time": 8, "automation": False})
        copied = call_tool(app, "live_arrangement_copy_range", {
            "start": 8, "end": 12, "destination": 32, "tracks": ["Bass"]})
        assert copied["automation"]["kind"] == "copy"
        end = time.time() + 30
        while time.time() < end:
            status = call_tool(app, "live_automation_record", {"action": "status"})
            jobs = status.get("arrangement_jobs") or []
            if jobs and all(j["finished"] for j in jobs):
                break
            time.sleep(0.1)
    finally:
        clock.stop()
        uninstall()
        from livebridge_mcp import arrange_automation
        arrange_automation.runner_for(None, id(bridge)).cancel()
    (job,) = status["arrangement_jobs"]
    assert job["phase"] == "done", job
    assert [(p["mode"], p["writer"], p["tempo_during_pass"]) for p in job["passes"]] == [
        ("sample", "LiveBridge Engine", 600.0), ("record", "LiveBridge Engine", 600.0)]
    log = [json.loads(line) for line in outlets.read_text(encoding="utf-8").splitlines()]
    path = ["live_set", "tracks", 0, "devices", 0, "parameters", 3]
    assert any(m[0] == "getvalues" for m in log)
    written = [m[1] for m in log if m[0] == "setvalue" and m[2:] == path]
    assert written and all(v == 0.5 for v in written)
    assert not any(m[0] == "call" and "gesture" in str(m[1]) for m in log)
    assert song.tempo == 124.0
