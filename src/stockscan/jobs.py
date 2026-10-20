"""Single background worker running analyses one at a time."""

from __future__ import annotations

import datetime as _dt
import itertools
import queue
import threading
import traceback
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Job:
    id: int
    set_id: str
    analysis: str
    params: dict = field(default_factory=dict)
    status: str = "queued"  # queued, running, done, failed
    submitted: str = ""
    started: str = ""
    finished: str = ""
    report: str = ""  # run folder name
    summary: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


class JobQueue:
    """runner(job) -> (report run name, summary line); exceptions mark the job failed."""

    def __init__(self, runner: Callable[[Job], tuple[str, str]], keep: int = 200):
        self.runner = runner
        self.keep = keep
        self._q: queue.Queue[Job | None] = queue.Queue()
        self._jobs: dict[int, Job] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._work, name="stockscan-worker", daemon=True)
        self._thread.start()

    def submit(self, set_id: str, analysis: str, params: dict | None = None) -> Job:
        job = Job(next(self._ids), set_id, analysis, dict(params or {}), submitted=_now())
        with self._lock:
            self._jobs[job.id] = job
            for old in sorted(self._jobs)[:-self.keep]:
                if self._jobs[old].status in ("done", "failed"):
                    del self._jobs[old]
        self._q.put(job)
        return job

    def get(self, job_id: int) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self, set_id: str | None = None) -> list[Job]:
        with self._lock:
            jobs = list(self._jobs.values())
        if set_id is not None:
            jobs = [j for j in jobs if j.set_id == set_id]
        return sorted(jobs, key=lambda j: j.id, reverse=True)

    def busy(self) -> bool:
        return any(j.status in ("queued", "running") for j in self.list())

    def wait(self, job: Job, timeout: float | None = None) -> Job:
        """Block until a job finishes (for tests and the CLI)."""
        import time

        t0 = time.monotonic()
        while job.status in ("queued", "running"):
            if timeout is not None and time.monotonic() - t0 > timeout:
                raise TimeoutError(f"job {job.id} still {job.status}")
            time.sleep(0.05)
        return job

    def stop(self):
        self._q.put(None)

    def _work(self):
        while True:
            job = self._q.get()
            if job is None:
                return
            job.status, job.started = "running", _now()
            try:
                job.report, job.summary = self.runner(job)
                job.status = "done"
            except Exception:
                job.error = traceback.format_exc()
                job.status = "failed"
            job.finished = _now()
