"""Long mix/master runs as background jobs: the report survives the MCP client's timeout."""

import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "mcp_server"))

from livebridge_mcp import engine, jobs  # noqa: E402
from livebridge_mcp.client import BridgeClient  # noqa: E402
from livebridge_mcp.server import create_app  # noqa: E402

from test_consistency import call_tool  # noqa: E402


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVEBRIDGE_LIBRARY", str(tmp_path / "library"))
    jobs.reset()
    yield
    end = time.time() + 5
    while jobs.running() and time.time() < end:
        time.sleep(0.05)
    jobs.reset()


def test_quick_work_answers_inline_and_slow_work_becomes_a_job(monkeypatch):
    assert jobs.start("t", lambda: {"ok": 1}) == {"ok": 1}
    monkeypatch.setattr(jobs, "SYNC_WAIT_S", 0.05)

    def slow():
        jobs.note("halfway")
        time.sleep(0.4)
        return {"report": "full"}
    first = jobs.start("live_mix_auto", slow)
    assert first["status"] == "running" and "live_mix_job" in first["next"]
    with pytest.raises(jobs.Busy):                    # one run at a time
        jobs.start("live_master_auto", lambda: None)
    assert jobs.get(first["job"])["phase"] == "halfway"
    done = jobs.get(first["job"], wait=3)
    assert done["status"] == "done" and done["result"] == {"report": "full"}
    # kept on disk for a restarted server
    jobs.reset()
    assert jobs.get(first["job"])["result"] == {"report": "full"}


def test_a_crash_ends_the_job_with_its_message(monkeypatch):
    monkeypatch.setattr(jobs, "SYNC_WAIT_S", 2.0)

    def boom():
        raise RuntimeError("fell over")
    answer = jobs.start("x", boom)
    assert answer["type"] == "internal" and "fell over" in answer["error"]
    assert jobs.running() is None


def test_tools_run_as_jobs_and_live_mix_job_reports(monkeypatch):
    app = create_app(BridgeClient(host="127.0.0.1", port=9, timeout=0.5))
    monkeypatch.setattr(jobs, "SYNC_WAIT_S", 0.05)

    def slow_devices(*a, **k):
        time.sleep(0.3)
        return []
    monkeypatch.setattr(engine, "devices", slow_devices)
    started = call_tool(app, "live_mix_balance", {"seconds": 3})
    assert started["status"] == "running" and started["tool"] == "live_mix_balance"
    busy = call_tool(app, "live_master_auto", {"seconds": 3})
    assert busy["type"] == "busy" and "live_mix_balance" in busy["error"]
    report = call_tool(app, "live_mix_job", {"job": started["job"], "wait_seconds": 5})
    assert report["status"] == "failed" and report["result"]["type"] == "not_found"
    assert call_tool(app, "live_mix_job", {"wait_seconds": 99})["type"] == "bad_args"
    assert call_tool(app, "live_mix_job", {"job": 999})["type"] == "not_found"
