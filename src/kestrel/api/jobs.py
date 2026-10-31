"""In-process job runner for long-running operations.

Every operation the dashboard triggers (data ingestion now; backtests and
validation runs later) is a ``Job``: it gets an id, reports progress and log
lines, can be cancelled cooperatively, and ends with a result or an error.
The UI polls job state, so no operation ever blocks an HTTP request.

This deliberately stays in-process (a thread pool) rather than adding Redis or
Celery: one user, one machine. The ``JobManager`` interface is the seam where a
distributed queue could slot in later without touching routes or the UI.
"""

from __future__ import annotations

import threading
import traceback
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def finished(self) -> bool:
        return self in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}


class JobCancelledError(Exception):
    """Raised inside a job when cancellation has been requested."""


class JobError(Exception):
    """An expected failure with a user-facing message (bad input, nothing to do).

    Logged as a single line. Any other exception is treated as a bug and its
    traceback is kept in the job log.
    """


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class Job:
    id: str
    kind: str
    params: dict[str, Any]
    status: JobStatus = JobStatus.QUEUED
    progress: float = 0.0
    message: str = "Queued"
    logs: list[str] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    cancel_requested: bool = False


class JobContext:
    """Handle passed to a running job for reporting progress and checking cancellation."""

    def __init__(self, job: Job, lock: threading.Lock) -> None:
        self._job = job
        self._lock = lock

    def progress(self, fraction: float, message: str | None = None) -> None:
        with self._lock:
            self._job.progress = min(max(fraction, 0.0), 1.0)
            if message is not None:
                self._job.message = message

    def log(self, line: str) -> None:
        with self._lock:
            self._job.logs.append(f"{_now():%H:%M:%S} {line}")

    def check_cancelled(self) -> None:
        if self._job.cancel_requested:
            raise JobCancelledError


JobFn = Callable[[JobContext], dict[str, Any]]


class JobManager:
    def __init__(self, max_workers: int = 2, history: int = 200) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="kestrel-job")
        self._history = history

    def submit(self, kind: str, params: dict[str, Any], fn: JobFn) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, params=params)
        with self._lock:
            self._jobs[job.id] = job
            self._trim()
        self._pool.submit(self._run, job, fn)
        return job

    def _run(self, job: Job, fn: JobFn) -> None:
        ctx = JobContext(job, self._lock)
        with self._lock:
            if job.cancel_requested:
                job.status, job.message, job.finished_at = JobStatus.CANCELLED, "Cancelled", _now()
                return
            job.status, job.message, job.started_at = JobStatus.RUNNING, "Running", _now()
        try:
            result = fn(ctx)
        except JobCancelledError:
            with self._lock:
                job.status, job.message = JobStatus.CANCELLED, "Cancelled"
        except JobError as exc:
            with self._lock:
                job.status, job.message, job.error = JobStatus.FAILED, "Failed", str(exc)
                job.logs.append(f"{_now():%H:%M:%S} Failed: {exc}")
        except Exception as exc:
            with self._lock:
                job.status, job.message, job.error = JobStatus.FAILED, "Failed", str(exc)
                job.logs.append(traceback.format_exc(limit=5))
        else:
            with self._lock:
                job.status, job.progress, job.result = JobStatus.SUCCEEDED, 1.0, result
                job.message = "Done"
        finally:
            with self._lock:
                job.finished_at = _now()

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def cancel(self, job_id: str) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None and not job.status.finished:
                job.cancel_requested = True
                job.message = "Cancelling"
            return job

    def _trim(self) -> None:
        finished = [j for j in self._jobs.values() if j.status.finished]
        for job in sorted(finished, key=lambda j: j.created_at)[
            : max(0, len(self._jobs) - self._history)
        ]:
            del self._jobs[job.id]

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
