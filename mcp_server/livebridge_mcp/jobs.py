"""Long mix and master runs as background jobs, so an MCP client's request timeout never
loses their report.

An MCP client gives every tool call a fixed time (Claude Desktop: about a minute) that the
server cannot extend. ``live_mix_auto`` on 20 tracks, or ``live_master_auto`` stepping a limiter
over two sections, listens for longer than that: the work finished in Live but the report was
lost. :func:`start` runs such a tool in a thread and waits up to :data:`SYNC_WAIT_S`: done by
then, the report comes back as usual; otherwise the call answers ``{job, status: "running",
phase}`` at once and ``live_mix_job`` returns the report later. One job at a time (a second
run would fight the first over the same devices); a job's own nested tool calls run inline.
Finished reports are kept in memory and in ``mix_jobs.json`` in the library folder.
"""

from __future__ import annotations

import itertools
import json
import threading
import time
from typing import Any, Callable

from . import library

#: How long a call waits for its job before answering "running" (under the client's limit).
SYNC_WAIT_S = 40.0
_KEEP = 8
_FILE = "mix_jobs.json"

_lock = threading.Lock()
_jobs: dict[int, dict[str, Any]] = {}
_ids = itertools.count(1)
_local = threading.local()


class Busy(Exception):
    """Another mix/master job is still running."""

    def __init__(self, job: dict[str, Any]):
        super().__init__(f"{job['tool']} is still running (job {job['id']}, "
                         f"{_elapsed(job)} s, {job.get('phase') or 'starting'}) — wait for it "
                         f"with live_mix_job(job={job['id']}, wait_seconds=30) before starting another "
                         "mix or master run")
        self.job = job


def _elapsed(job: dict[str, Any]) -> float:
    return round((job.get("finished") or time.time()) - job["started"], 1)


def in_job() -> bool:
    """True inside a job's thread (nested tool calls then run inline)."""
    return getattr(_local, "job", None) is not None


def nested() -> bool:
    """True when a job's tool calls another one (mix_auto -> balance): the inner call keeps
    the outer call's window."""
    return getattr(_local, "depth", 0) > 1


def enter() -> None:
    _local.depth = getattr(_local, "depth", 0) + 1


def leave() -> None:
    _local.depth = max(0, getattr(_local, "depth", 0) - 1)


def note(phase: str) -> None:
    """Say what the current job is doing (shown by ``live_mix_job`` while it runs)."""
    job = getattr(_local, "job", None)
    if job is not None:
        job["phase"] = phase


def running() -> dict[str, Any] | None:
    with _lock:
        return next((j for j in _jobs.values() if j.get("finished") is None), None)


def view(job: dict[str, Any]) -> dict[str, Any]:
    """What ``live_mix_job`` shows of a job."""
    data = {"job": job["id"], "tool": job["tool"],
            "status": "running" if job.get("finished") is None else job.get("status", "done"),
            "elapsed_s": _elapsed(job)}
    if job.get("phase"):
        data["phase"] = job["phase"]
    if job.get("finished") is not None:
        data["result"] = job.get("result")
    return data


def _path():
    return library.library_dir() / _FILE


def _saved() -> list[dict[str, Any]]:
    data = library.read_json(_path(), [])
    return data if isinstance(data, list) else []


def _save(job: dict[str, Any]) -> None:
    try:
        kept = [j for j in _saved() if j.get("job") != job["id"]] + [view(job)]
        library.write_json(_path(), kept[-_KEEP:])
    except (OSError, TypeError, ValueError):
        pass


def start(tool: str, work: Callable[[], Any]) -> Any:
    """Run ``work`` (a tool's body) as a job; its result, or ``{job, status: "running"}``.

    Raises:
        Busy: another job is running.
    """
    if in_job():
        return work()
    with _lock:
        busy = next((j for j in _jobs.values() if j.get("finished") is None), None)
        if busy is not None:
            raise Busy(busy)
        job: dict[str, Any] = {"id": next(_ids), "tool": tool, "started": time.time(),
                               "finished": None, "phase": None}
        _jobs[job["id"]] = job
        for old in sorted(_jobs)[:-_KEEP]:
            if _jobs[old].get("finished") is not None:
                del _jobs[old]

    def run() -> None:
        _local.job = job
        try:
            job["result"] = work()
            job["status"] = "failed" if isinstance(job["result"], dict) and \
                "error" in job["result"] and "type" in job["result"] else "done"
        except Exception as exc:              # a crash still ends the job, with its message
            job["result"] = {"error": f"{type(exc).__name__}: {exc}", "type": "internal"}
            job["status"] = "failed"
        finally:
            job["finished"] = time.time()
            _local.job = None
            _save(job)

    thread = threading.Thread(target=run, name=f"livebridge-{tool}", daemon=True)
    thread.start()
    thread.join(SYNC_WAIT_S)
    if job.get("finished") is not None:
        return job["result"]
    return {"job": job["id"], "tool": tool, "status": "running", "phase": job.get("phase"),
            "elapsed_s": _elapsed(job),
            "next": f"live_mix_job(job={job['id']}, wait_seconds=30) — the run goes on in Live; "
                    "ask until status is done (the report comes then)"}


def get(job_id: int | None = None, wait: float = 0.0) -> dict[str, Any] | None:
    """A job by id (default: the newest), waiting up to ``wait`` s for it to finish; finished
    jobs of an earlier server process come from ``mix_jobs.json``."""
    with _lock:
        job = _jobs.get(job_id) if job_id is not None else (
            _jobs[max(_jobs)] if _jobs else None)
    if job is None:
        saved = _saved()
        found = [j for j in saved if job_id is None or j.get("job") == job_id]
        return found[-1] if found else None
    end = time.time() + max(0.0, wait)
    while job.get("finished") is None and time.time() < end:
        time.sleep(0.2)
    return view(job)


def reset() -> None:
    """Forget every job (tests)."""
    with _lock:
        _jobs.clear()
