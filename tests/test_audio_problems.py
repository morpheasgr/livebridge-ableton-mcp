"""Technical problem detectors and layer alignment (audio_analysis.problems / align) on
synthetic audio, and the live_audio_problems / live_audio_align tools."""

import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
sf = pytest.importorskip("soundfile")
pytest.importorskip("scipy")
pytest.importorskip("pyloudnorm")

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "mcp_server"))

from livebridge_mcp import audio_analysis  # noqa: E402
from livebridge_mcp.client import BridgeClient  # noqa: E402
from livebridge_mcp.server import create_app  # noqa: E402

from test_consistency import call_tool  # noqa: E402

FS = 48000


def _wav(tmp_path, name, left, right=None):
    data = np.column_stack([left, left if right is None else right]).astype(np.float32)
    path = tmp_path / f"{name}.wav"
    sf.write(str(path), data, FS, subtype="FLOAT")
    return str(path)


def _music(seconds=8.0, seed=1):
    """A chord that changes every half second (no note holds) plus very quiet noise."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(FS * seconds)) / FS
    out = np.zeros_like(t)
    roots = [220.0, 246.9, 261.6, 293.7, 329.6, 349.2, 392.0, 440.0]
    for i in range(int(seconds * 2)):
        a, b = int(i * FS / 2), int((i + 1) * FS / 2)
        f = roots[int(rng.integers(len(roots)))]
        for ratio in (1.0, 1.26, 1.5):
            out[a:b] += 0.08 * np.sin(2 * np.pi * f * ratio * t[a:b])
    return out + 0.0005 * rng.standard_normal(len(t))


def _kinds(result):
    return {p["problem"]: p for p in result["problems"]}


def test_clean_material_has_no_problems(tmp_path):
    result = audio_analysis.problems(_wav(tmp_path, "clean", _music()))
    assert result["problems"] == [], result["problems"]
    assert result["notes"][0] == "No technical problems found."


def test_each_detector(tmp_path):
    music = _music()
    t = np.arange(len(music)) / FS
    clipped = _kinds(audio_analysis.problems(_wav(tmp_path, "clip", np.clip(music * 12, -1, 1))))
    assert clipped["clipping"]["severity"] == "significant"

    dc = _kinds(audio_analysis.problems(_wav(tmp_path, "dc", music + 0.02)))
    assert dc["dc_offset"]["offset_db"] == pytest.approx(-34.0, abs=0.5)

    flipped = _kinds(audio_analysis.problems(_wav(tmp_path, "pol", music, -music)))
    assert flipped["polarity"]["correlation"] < -0.9

    gate = np.repeat(np.random.default_rng(3).random(16) > 0.5, FS // 2)[:len(t)]
    hum_signal = music * gate + 0.003 * (np.sin(2 * np.pi * 60 * t) +
                                        0.5 * np.sin(2 * np.pi * 120 * t) +
                                        0.3 * np.sin(2 * np.pi * 180 * t))
    hum = _kinds(audio_analysis.problems(_wav(tmp_path, "hum", hum_signal)))
    assert hum["hum"]["frequency_hz"] == 60.0 and 120.0 in hum["hum"]["harmonics_hz"]

    noisy = music * gate + 0.01 * np.random.default_rng(4).standard_normal(len(t))
    assert _kinds(audio_analysis.problems(_wav(tmp_path, "noise", noisy)))["noise"][
        "floor_db"] > -60

    ringing = music + 0.04 * np.sin(2 * np.pi * 3150 * t)
    resonance = _kinds(audio_analysis.problems(_wav(tmp_path, "ring", ringing)))["resonance"]
    (peak,) = [p for p in resonance["peaks"] if abs(p["frequency_hz"] - 3150) < 10]
    assert peak["eq"]["gain_db"] < 0 and peak["q"] > 2 and peak["persistence"] > 0.6
    assert peak["note"].startswith("G6")                 # Live names middle C "C3"


def _kick(seconds=4.0):
    x = np.zeros(int(FS * seconds))
    n = np.arange(int(0.15 * FS))
    hit = np.sin(2 * np.pi * (50 + 150 * np.exp(-n / 800)) * n / FS) * np.exp(-n / 3000)
    for k in range(int(seconds * 2)):
        x[k * FS // 2:k * FS // 2 + len(n)] += hit
    return x


@pytest.mark.parametrize("delay_ms, sign", [(3.0, -1.0), (-1.5, 1.0), (0.0, -1.0)])
def test_align_finds_offset_and_polarity(tmp_path, delay_ms, sign):
    x = _kick()
    d = int(round(abs(delay_ms) / 1000 * FS))
    if delay_ms >= 0:
        y = np.concatenate([np.zeros(d), x[:len(x) - d]])
    else:
        y = np.concatenate([x[d:], np.zeros(d)])
    result = audio_analysis.align(_wav(tmp_path, "a", x), _wav(tmp_path, "b", sign * y))
    assert result["offset_ms"] == pytest.approx(delay_ms, abs=0.05)
    assert result["polarity"] == ("inverted" if sign < 0 else "same")
    assert result["correlation"]["aligned"] > 0.95
    if sign < 0 or d:
        assert result["sum_gain_db"] > 0 and result["advice"]


def test_align_aligned_parts_need_nothing(tmp_path):
    x = _kick()
    result = audio_analysis.align(_wav(tmp_path, "a", x), _wav(tmp_path, "b", 0.5 * x))
    assert result["offset_ms"] == pytest.approx(0.0, abs=0.02)
    assert result["advice"] == ["Already aligned: no useful offset or polarity flip in this "
                                "band."]


def test_tools(tmp_path, tcp_bridge, song):
    app = create_app(BridgeClient(host="127.0.0.1", port=tcp_bridge.port, timeout=10.0))
    music = _music()
    found = call_tool(app, "live_audio_problems", {"source": _wav(tmp_path, "m", music + 0.02)})
    assert [p["problem"] for p in found["problems"]] == ["dc_offset"]
    x = _kick()
    a, b = _wav(tmp_path, "a", x), _wav(tmp_path, "b", -x)
    aligned = call_tool(app, "live_audio_align", {"a": a, "b": b})
    assert aligned["polarity"] == "inverted" and aligned["offset_beats"] == 0.0
    # apply: b must be a track's clip; the fake Utility has no named phase switches
    assert call_tool(app, "live_audio_align", {"a": a, "b": b, "apply": True})["type"] \
        == "bad_args"
    for args in ({"a": a, "b": b, "band": "air"}, {"a": a, "b": b, "max_ms": 500},
                 {"a": a, "b": b, "duration": 0}):
        assert call_tool(app, "live_audio_align", args)["type"] == "bad_args", args
    assert call_tool(app, "live_audio_problems", {"source": str(tmp_path / "none.wav")})[
        "type"] == "not_found"
