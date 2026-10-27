"""Arrangement automation passes, run in the background of the MCP server.

Live's API cannot draw arrangement automation lanes and drops clip envelopes from arrangement
copies (LIVE_API_VERIFIED §19.5), so the arrangement tools (`live_arrangement_from_scenes`,
`live_arrangement_duplicate_clip`, `live_arrangement_copy_range`) hand their automation to this
module, which records it the way a person would — one pass of Arrangement Record for many
parameters at once (``automation.record lanes=...``), at a raised tempo when it can, written by
the LiveBridge Engine 50 times a second when one is in the set:

* ``record`` jobs: lanes (curves in song beats) to record, e.g. the clip envelopes the copies
  lost;
* ``copy`` jobs: parameters to *sample* while a source range plays (their automation playing
  back, read without recording), then recorded at the destination.

Jobs queue up and run one after another on a worker thread; :func:`status` reports them
(``live_automation_record(action="status")``) and :func:`cancel` stops them.
"""

from __future__ import annotations

import itertools
import threading
import time
from typing import Any, Callable

from . import engine

#: beats between two writes "fast" aims for, per writer (see tools/automation.py)
_RESOLUTION = {"external": 0.2, "live": 0.5}
_TICK_S = {"external": 0.02, "live": 0.1}
_MAX_LANES = 64
_POLL_S = 0.25
_KEEP = 8

Call = Callable[[str, dict[str, Any]], Any]


def _is_error(value: Any) -> bool:
    return isinstance(value, dict) and "error" in value and "type" in value


class Job:
    """One arrangement's automation: a list of passes run in order."""

    _ids = itertools.count(1)

    def __init__(self, kind: str, lanes: list[dict[str, Any]], start: float, end: float,
                 destination: float | None = None, origin: str = "") -> None:
        self.id = "auto%d" % next(self._ids)
        self.kind = kind                      # "record" | "copy"
        self.lanes = lanes                    # record: {parameter, points, track?, name?}
        self.start = float(start)             # copy: {parameter, track?}
        self.end = float(end)
        self.destination = destination
        self.origin = origin
        self.phase = "queued"
        self.passes: list[dict[str, Any]] = []
        self.notes: list[str] = []
        self.error: str | None = None
        self.cancelled = False
        self.created = time.time()
        self.finished_at: float | None = None

    @property
    def finished(self) -> bool:
        return self.phase in ("done", "failed", "cancelled")

    def summary(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "job": self.id, "kind": self.kind, "for": self.origin, "phase": self.phase,
            "finished": self.finished, "range": [round(self.start, 4), round(self.end, 4)],
            "parameters": [{"track": lane.get("track"),
                            "parameter": lane.get("name") or lane.get("parameter")}
                           for lane in self.lanes],
            "passes": self.passes}
        if self.destination is not None:
            data["destination"] = round(self.destination, 4)
        if self.notes:
            data["notes"] = self.notes
        if self.error:
            data["error"] = self.error
        return data


class Runner:
    """The queue of jobs and the worker that runs them against one bridge."""

    def __init__(self, call: Call, sleep: Callable[[float], None] = time.sleep) -> None:
        self.call = call
        self.sleep = sleep
        self.jobs: list[Job] = []
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.worker: threading.Thread | None = None

    # -- queue ---------------------------------------------------------------
    def submit(self, job: Job) -> Job:
        with self.lock:
            self.jobs.append(job)
            done = [j for j in self.jobs if j.finished]
            for old in done[:-_KEEP]:
                self.jobs.remove(old)
            if self.worker is None or not self.worker.is_alive():
                self.worker = threading.Thread(target=self._work, name="lb-automation",
                                               daemon=True)
                self.worker.start()
        self.wake.set()
        return job

    def status(self) -> list[dict[str, Any]]:
        with self.lock:
            return [job.summary() for job in self.jobs]

    def active(self) -> bool:
        with self.lock:
            return any(not job.finished for job in self.jobs)

    def cancel(self) -> list[str]:
        with self.lock:
            pending = [job for job in self.jobs if not job.finished]
        for job in pending:
            job.cancelled = True
            if job.phase == "queued":
                job.phase = "cancelled"
        return [job.id for job in pending]

    def wait(self, timeout: float) -> bool:
        """Wait until every job finished (tests)."""
        end = time.time() + timeout
        while time.time() < end:
            if not self.active():
                return True
            time.sleep(0.05)
        return not self.active()

    def _next(self) -> Job | None:
        with self.lock:
            return next((job for job in self.jobs if job.phase == "queued"), None)

    def _work(self) -> None:
        while True:
            job = self._next()
            if job is None:
                return
            try:
                job.phase = "running"
                self._run(job)
                if job.phase == "running":
                    job.phase = "cancelled" if job.cancelled else "done"
            except Exception as exc:  # never kill the worker
                job.phase = "failed"
                job.error = f"{type(exc).__name__}: {exc}"
            job.finished_at = time.time()

    # -- one job -------------------------------------------------------------
    def _run(self, job: Job) -> None:
        state = self.call("transport.get", {})
        if _is_error(state):
            raise RuntimeError(state.get("error"))
        if state.get("is_playing"):
            self.call("transport.stop", {})
            job.notes.append("stopped the transport to record the automation")
            self.sleep(0.3)
        tempo = float(state.get("tempo") or 120.0)
        if job.kind == "copy":
            sampled = self._sample(job, tempo)
            if sampled is None:
                return
            lanes = sampled
        else:
            lanes = job.lanes
        lanes = [lane for lane in lanes if lane.get("points")]
        for first in range(0, len(lanes), _MAX_LANES):
            if job.cancelled:
                return
            chunk = lanes[first:first + _MAX_LANES]
            start = min(float(lane["points"][0][0]) for lane in chunk)
            end = max(float(lane["points"][-1][0]) for lane in chunk)
            self._pass(job, "record", chunk, start, end, tempo)
            if job.phase == "failed":
                return

    def _writer(self) -> dict[str, Any] | None:
        try:
            found = engine.devices()
        except engine.EngineError:
            return None
        return engine.choose(found) if found else None

    def _pass(self, job: Job, mode: str, lanes: list[dict[str, Any]], start: float,
              end: float, tempo: float) -> dict[str, Any] | None:
        """One recording (mode "record") or sampling ("sample") pass; returns the finished
        Remote Script status (sampling: with samples) or None."""
        device = self._writer()
        writer = "external" if device is not None else "live"
        fast = round(min(999.0, max(tempo, _RESOLUTION[writer] * 60.0 / _TICK_S[writer])), 2)
        specs = [{"parameter": lane["parameter"]} if mode == "sample" else
                 {"parameter": lane["parameter"], "points": lane["points"],
                  "mode": lane.get("mode", "linear")} for lane in lanes]
        args: dict[str, Any] = {"lanes": specs, "start": start, "end": end, "preroll": 0}
        if mode == "sample":
            args["action"] = "sample"
        elif writer == "external":
            args["writer"] = "external"
        if fast > tempo * 1.01:
            args["tempo"] = fast
        info: dict[str, Any] = {"mode": mode, "lanes": len(lanes),
                                "range": [round(start, 4), round(end, 4)],
                                "tempo_during_pass": args.get("tempo", tempo),
                                "writer": "LiveBridge Engine" if device else "Remote Script"}
        job.passes.append(info)
        answer = self.call("automation.record", args)
        if _is_error(answer) and "tempo automation" in str(answer.get("error")) and \
                "tempo" in args:
            args.pop("tempo")
            info["tempo_during_pass"] = tempo
            job.notes.append("the song has tempo automation: recorded at the song tempo")
            answer = self.call("automation.record", args)
        if _is_error(answer):
            info["error"] = answer.get("error")
            job.phase, job.error = "failed", f"{mode} pass: {answer.get('error')}"
            return None
        if device is not None:
            try:
                if mode == "sample":
                    engine.request(device, "sample", {
                        "paths": [engine.lom_to_max(lane["parameter"]) for lane in lanes],
                        "start": start, "end": end})
                else:
                    curves = answer.get("curves") or []
                    engine.request(device, "automate", {
                        "lanes": [{"path": engine.lom_to_max(c["path"]), "points": c["points"],
                                   "mode": c.get("mode", "linear")} for c in curves],
                        "start": start, "end": end})
            except (engine.EngineError, ValueError, KeyError) as exc:
                self.call("automation.record", {"action": "stop"})
                info["error"] = str(exc)
                job.phase, job.error = "failed", f"the LiveBridge Engine refused: {exc}"
                return None
        expected = float(answer.get("expected_seconds") or 0.0)
        deadline = time.time() + expected * 2.0 + 30.0
        status = answer
        while time.time() < deadline:
            if job.cancelled:
                self.call("automation.record", {"action": "stop"})
                return None
            self.sleep(_POLL_S)
            status = self.call("automation.record_status", {})
            if isinstance(status, dict) and status.get("finished"):
                break
        else:
            self.call("automation.record", {"action": "stop"})
            job.phase, job.error = "failed", f"{mode} pass did not finish in time"
            return None
        info["phase"] = status.get("phase")
        if status.get("phase") != "done":
            job.phase = "failed"
            job.error = f"{mode} pass {status.get('phase')}: {status.get('error')}"
            return None
        if mode == "sample" and device is not None:
            try:
                result = engine.request(device, "sample", {"result": True})
            except engine.EngineError:
                result = None
            if isinstance(result, dict) and result.get("reads"):
                status["engine_samples"] = result.get("samples")
        return status

    def _sample(self, job: Job, tempo: float) -> list[dict[str, Any]] | None:
        """Sample the job's parameters over its range; their lanes at the destination."""
        lanes: list[dict[str, Any]] = []
        shift = float(job.destination) - job.start
        for first in range(0, len(job.lanes), _MAX_LANES):
            chunk = job.lanes[first:first + _MAX_LANES]
            status = self._pass(job, "sample", chunk, job.start, job.end, tempo)
            if status is None:
                return None
            rows = status.get("lanes") or []
            engine_samples = status.get("engine_samples")
            for index, lane in enumerate(chunk):
                samples = None
                if engine_samples and index < len(engine_samples) and engine_samples[index]:
                    samples = engine_samples[index]
                elif index < len(rows):
                    samples = rows[index].get("samples")
                if not samples:
                    continue
                points = [[round(float(t) + shift, 6), v] for t, v in samples
                          if job.start - 1e-6 <= float(t) <= job.end + 1e-6]
                if not points:
                    continue
                if points[0][0] > job.start + shift + 1e-6:
                    points.insert(0, [round(job.start + shift, 6), points[0][1]])
                if points[-1][0] < job.end + shift - 1e-6:
                    points.append([round(job.end + shift, 6), points[-1][1]])
                lanes.append({**lane, "points": points})
        return lanes


_RUNNERS: dict[int, Runner] = {}


def runner_for(call: Call, key: int) -> Runner:
    """The runner of one bridge (``key``: id of the BridgeClient)."""
    if key not in _RUNNERS:
        _RUNNERS[key] = Runner(call)
    return _RUNNERS[key]
