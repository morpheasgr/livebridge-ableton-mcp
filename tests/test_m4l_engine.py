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
const recorder = { file: null, started: 0 };

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

    def start(freq=None):
        outlets = tmp_path / f"outlets{len(started)}.log"
        env = dict(os.environ, NODE_PATH=str(tmp_path / "node_modules"),
                   LIVEBRIDGE_ENGINE_DIR=str(registry), LIVEBRIDGE_CAPTURE_DIR=str(captures),
                   OUTLET_LOG=str(outlets), STUB_FREQ=str(freq or "none"))
        before = set(registry.glob("*.json"))
        process = subprocess.Popen([NODE, str(harness), str(ENGINE_JS)], stdin=subprocess.PIPE,
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
