"""Automation that goes along with the arrangement tools (automation=true, the default): lost
clip envelopes recorded into lanes, and copied ranges sampled and recorded at the destination
— background passes (``arrange_automation``) run against the fake Live, whose playhead a clock
thread moves at the song tempo while it "plays"."""

import sys
import threading
import time
from pathlib import Path

import pytest

import Live
from live_stub_ext import arrangement_live

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "mcp_server"))

from livebridge_mcp import arrange_automation  # noqa: E402
from livebridge_mcp.client import BridgeClient  # noqa: E402
from livebridge_mcp.server import create_app  # noqa: E402

from test_consistency import call_tool  # noqa: E402


class SongClock(threading.Thread):
    """Moves the fake song's playhead at its tempo while it plays; logs (beat, values of the
    watched parameters) while Arrangement Record is on; ``playback(beat)`` may set values like
    automation playing back."""

    def __init__(self, song, watch, playback=None):
        super().__init__(daemon=True)
        self.song, self.watch, self.playback = song, watch, playback
        self.recorded = []
        self.running = True
        self.error = None

    def run(self):
        try:
            self._run()
        except Exception as error:  # surfaced by the tests
            self.error = error
            raise

    def _run(self):
        last = time.time()
        while self.running:
            time.sleep(0.005)
            now = time.time()
            if self.song.is_playing:
                self.song.current_song_time += self.song.tempo / 60.0 * (now - last)
                beat = self.song.current_song_time
                if self.song.record_mode:
                    self.recorded.append((beat, [p.value for p in self.watch]))
                elif self.playback is not None:
                    self.playback(beat)
            last = now

    def stop(self):
        self.running = False
        self.join(2)


@pytest.fixture
def envelopes_lost():
    uninstall = arrangement_live.install(Live, copy_envelopes="without_devices")
    yield
    uninstall()


@pytest.fixture
def app(tcp_bridge, tmp_path, monkeypatch):
    monkeypatch.setenv("LIVEBRIDGE_ENGINE_DIR", str(tmp_path / "no engine"))
    bridge = BridgeClient(host="127.0.0.1", port=tcp_bridge.port, timeout=10.0)
    application = create_app(bridge)
    yield application
    runner = arrange_automation.runner_for(None, id(bridge))
    runner.cancel()
    runner.wait(10)


def _runner(app):
    status = call_tool(app, "live_automation_record", {"action": "status"})
    return status


def _wait(app, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        status = call_tool(app, "live_automation_record", {"action": "status"})
        jobs = status.get("arrangement_jobs") or []
        if jobs and all(job["finished"] for job in jobs):
            return status
        time.sleep(0.1)
    raise AssertionError("the automation jobs did not finish: %r" % status)


def test_from_scenes_records_the_lost_envelopes(app, song, envelopes_lost):
    freq = song.tracks[0].devices[0].parameters[2]
    call_tool(app, "live_automation_write", {"track": "Bass", "slot": 0,
                                             "parameter": "Filter Freq",
                                             "points": [[0, 100], [2, 1000]], "mode": "linear"})
    base = freq.value
    clock = SongClock(song, [freq])
    clock.start()
    try:
        built = call_tool(app, "live_arrangement_from_scenes", {
            "sections": [{"scene": 1, "bars": 1}, {"scene": 0, "bars": 1}], "tracks": ["Bass"]})
        auto = built["automation"]
        assert auto["kind"] == "record" and auto["parameters"] == [
            {"track": "Bass", "parameter": "Filter Freq"}]
        assert "live_automation_record" in auto["status"]
        status = _wait(app)
    finally:
        clock.stop()
    (job,) = status["arrangement_jobs"]
    assert job["phase"] == "done", job
    (rec,) = job["passes"]
    assert rec["mode"] == "record" and rec["tempo_during_pass"] == 300.0
    assert rec["writer"] == "Remote Script"
    assert song.tempo == 124.0 and not song.record_mode and not song.is_playing
    # what Arrangement Record saw: the parameter's own value in section 1, the envelope in 2
    first = [v[0] for b, v in clock.recorded if 0.5 <= b <= 3.0]
    assert first and all(v == pytest.approx(base) for v in first)
    rising = [(b, v[0]) for b, v in clock.recorded if 4.6 <= b <= 5.4]
    assert rising and all(v == pytest.approx(100 + 450 * (b - 4.0), abs=120)
                          for b, v in rising)
    top = [v[0] for b, v in clock.recorded if 6.5 <= b <= 7.5]
    assert top and all(v == pytest.approx(1000.0, abs=1.0) for v in top)


def test_copy_range_samples_and_records_the_automation(app, song, envelopes_lost):
    resonance = song.tracks[0].devices[0].parameters[3]
    resonance._automation_state = 1                       # a lane plays it

    def lane(beat):                                       # the lane playing back: 0.1 -> 0.9
        if 8.0 <= beat <= 12.0:
            resonance.value = 0.1 + 0.2 * (beat - 8.0)

    call_tool(app, "live_arrangement_duplicate_clip", {"track": "Bass", "slot": 0, "time": 8,
                                                       "automation": False})
    clock = SongClock(song, [resonance], playback=lane)
    clock.start()
    try:
        copied = call_tool(app, "live_arrangement_copy_range", {
            "start": 8, "end": 12, "destination": 32, "tracks": ["Bass"]})
        assert copied["automation"]["kind"] == "copy"
        names = [p["parameter"] for p in copied["automation"]["parameters"]]
        assert any(n.endswith("parameters[3]") for n in names)
        status = _wait(app, 30)
    finally:
        clock.stop()
    (job,) = status["arrangement_jobs"]
    assert job["phase"] == "done", (job.get("error"), clock.error)
    assert [p["mode"] for p in job["passes"]] == ["sample", "record"]
    assert job["destination"] == 32.0
    written = [(b, v[0]) for b, v in clock.recorded if 32.5 <= b <= 35.5]
    assert len(written) > 5
    assert all(v == pytest.approx(0.1 + 0.2 * (b - 32.0), abs=0.15) for b, v in written)


def test_automation_false_and_nothing_to_record(app, song, envelopes_lost):
    plain = call_tool(app, "live_arrangement_duplicate_clip", {"track": "Bass", "slot": 1,
                                                               "time": 64})
    assert plain["automation"] == {"note": "nothing to record: the copies kept their clip "
                                           "envelopes, or the clips had none"}
    off = call_tool(app, "live_arrangement_duplicate_clip", {"track": "Bass", "slot": 1,
                                                             "time": 72, "automation": False})
    assert "automation" not in off
    quiet = call_tool(app, "live_arrangement_copy_range", {"start": 64, "end": 68,
                                                           "destination": 96,
                                                           "tracks": ["Vocals"]})
    assert quiet["automation"] == {"note": "no automation plays in the copied range"}


def test_stop_cancels_queued_jobs(app, song, envelopes_lost):
    call_tool(app, "live_automation_write", {"track": "Bass", "slot": 0,
                                             "parameter": "Filter Freq",
                                             "points": [[0, 100], [2, 1000]], "mode": "linear"})
    # no clock: the pass would wait for the playhead; stop cancels it
    built = call_tool(app, "live_arrangement_from_scenes", {"sections": [{"scene": 0,
                                                                          "bars": 8}],
                                                            "tracks": ["Bass"]})
    assert built["automation"]["job"]
    time.sleep(0.5)
    stopped = call_tool(app, "live_automation_record", {"action": "stop"})
    assert stopped["cancelled_jobs"] == [built["automation"]["job"]]
    status = _wait(app, 10)
    assert status["arrangement_jobs"][0]["phase"] in ("cancelled", "failed")
    for _ in range(40):
        if not song.is_playing and song.tempo == 124.0:
            break
        time.sleep(0.05)
    assert not song.record_mode and song.tempo == 124.0
