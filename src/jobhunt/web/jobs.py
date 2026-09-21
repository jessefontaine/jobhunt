"""Run one long pipeline action at a time in a background thread, keeping its progress log."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

Progress = Callable[[str], None]
Status = Literal["running", "done", "failed"]

KEEP = 20  # finished jobs remembered for /jobs/{id}


class JobBusy(Exception):
    """Raised by JobRunner.start while another job is still running."""


@dataclass
class Job:
    id: int
    name: str
    status: Status = "running"
    lines: list[str] = field(default_factory=list)
    error: str | None = None
    started_at: datetime = field(default_factory=datetime.now)
    finished_at: datetime | None = None

    @property
    def running(self) -> bool:
        return self.status == "running"

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "lines": list(self.lines),
            "error": self.error,
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "finished_at": (
                self.finished_at.isoformat(timespec="seconds") if self.finished_at else None
            ),
        }


class JobRunner:
    """One job at a time. With `background=False` the job runs inline (for tests)."""

    def __init__(self, background: bool = True):
        self.background = background
        self._jobs: list[Job] = []
        self._next_id = 1
        self._lock = threading.Lock()

    @property
    def current(self) -> Job | None:
        """The running job, else the most recently started one."""
        return self._jobs[-1] if self._jobs else None

    def get(self, job_id: int) -> Job | None:
        return next((job for job in self._jobs if job.id == job_id), None)

    def start(self, name: str, fn: Callable[[Progress], object]) -> Job:
        with self._lock:
            if self._jobs and self._jobs[-1].running:
                raise JobBusy(f"{self._jobs[-1].name} is still running")
            job = Job(id=self._next_id, name=name)
            self._next_id += 1
            self._jobs.append(job)
            del self._jobs[:-KEEP]
        if self.background:
            threading.Thread(target=self._run, args=(job, fn), daemon=True).start()
        else:
            self._run(job, fn)
        return job

    @staticmethod
    def _run(job: Job, fn: Callable[[Progress], object]) -> None:
        try:
            fn(job.lines.append)
        except Exception as exc:  # the job must always reach a final state
            job.error = f"{type(exc).__name__}: {exc}"
            job.lines.append(job.error)
            final: Status = "failed"
        else:
            final = "done"
        job.finished_at = datetime.now()
        job.status = final  # last, so a poller that sees a final status also sees finished_at
