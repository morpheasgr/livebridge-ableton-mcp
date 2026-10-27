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
module.exports = {
  addHandler: (name, fn) => { global.__maxHandlers[name] = fn; },
  outlet: (...args) => { process.stdout.write(JSON.stringify(args) + "\n"); },
  post: () => {},
};
"""


@pytest.fixture
def running_engine(tmp_path, monkeypatch):
    if NODE is None:
        pytest.skip("node is not installed")
    stub = tmp_path / "node_modules" / "max-api"
    stub.mkdir(parents=True)
    (stub / "index.js").write_text(MAX_API_STUB, encoding="utf-8")
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS, encoding="utf-8")
    registry = tmp_path / "engine"
    env = dict(os.environ, NODE_PATH=str(tmp_path / "node_modules"),
               LIVEBRIDGE_ENGINE_DIR=str(registry))
    process = subprocess.Popen([NODE, str(harness), str(ENGINE_JS)], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                               text=True)
    monkeypatch.setenv("LIVEBRIDGE_ENGINE_DIR", str(registry))

    def send(*message):
        process.stdin.write(json.dumps(list(message)) + "\n")
        process.stdin.flush()

    try:
        assert engine.wait_for(lambda: list(registry.glob("*.json")), 10), \
            process.stderr.read() if process.poll() is not None else "no registry file"
        yield process, send, registry
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(5)
            except subprocess.TimeoutExpired:
                process.kill()


TONE_FRAME = [0.125, 0.125, 0.5, 0.5, 0.25, 1e-6, 1e-6, 1e-5, 1e-4, 0.12, 1e-4, 1e-6, 1e-7,
              0.125, 0.0, 0.125, 1e-6, 0.0]


def test_engine_script_end_to_end(running_engine):
    process, send, registry = running_engine
    entry = json.loads(next(registry.glob("*.json")).read_text(encoding="utf-8"))
    assert entry["host"] == "127.0.0.1" and len(entry["token"]) == 48
    if os.name == "posix":
        assert (next(registry.glob("*.json")).stat().st_mode & 0o777) == 0o600
    send("context", "master", "Main")
    assert engine.wait_for(lambda: engine.devices() and engine.devices()[0]["role"] ==
                           "master", 5)
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
