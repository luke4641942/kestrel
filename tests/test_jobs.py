from __future__ import annotations

import threading
import time

from kestrel.api.jobs import Job, JobContext, JobError, JobManager, JobStatus


def _wait(manager: JobManager, job: Job, timeout: float = 5.0) -> Job:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = manager.get(job.id)
        assert current is not None
        if current.status.finished:
            return current
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def test_success_records_result_and_progress() -> None:
    manager = JobManager()

    def fn(ctx: JobContext) -> dict[str, int]:
        ctx.progress(0.5, "halfway")
        ctx.log("hello")
        return {"answer": 42}

    job = _wait(manager, manager.submit("t", {}, fn))
    assert job.status is JobStatus.SUCCEEDED
    assert job.result == {"answer": 42}
    assert job.progress == 1.0
    assert job.logs[0].endswith("hello")
    assert job.started_at is not None
    assert job.finished_at is not None


def test_failure_records_error() -> None:
    manager = JobManager()

    def fn(_: JobContext) -> dict[str, int]:
        raise RuntimeError("boom")

    job = _wait(manager, manager.submit("t", {}, fn))
    assert job.status is JobStatus.FAILED
    assert job.error == "boom"


def test_cooperative_cancellation() -> None:
    manager = JobManager()
    started = threading.Event()

    def fn(ctx: JobContext) -> dict[str, int]:
        started.set()
        while True:
            ctx.check_cancelled()
            time.sleep(0.01)

    job = manager.submit("t", {}, fn)
    assert started.wait(2)
    manager.cancel(job.id)
    assert _wait(manager, job).status is JobStatus.CANCELLED


def test_progress_is_clamped() -> None:
    job = Job(id="x", kind="t", params={})
    ctx = JobContext(job, threading.Lock())
    ctx.progress(7.0)
    assert job.progress == 1.0
    ctx.progress(-1.0)
    assert job.progress == 0.0


def test_list_is_newest_first() -> None:
    manager = JobManager()
    a = manager.submit("a", {}, lambda _: {})
    time.sleep(0.01)
    b = manager.submit("b", {}, lambda _: {})
    assert [j.id for j in manager.list()][:2] == [b.id, a.id]


def test_expected_failure_logs_message_without_traceback() -> None:
    manager = JobManager()

    def fn(_: JobContext) -> dict[str, int]:
        raise JobError("nothing to ingest")

    job = _wait(manager, manager.submit("t", {}, fn))
    assert job.status is JobStatus.FAILED
    assert job.error == "nothing to ingest"
    assert not any("Traceback" in line for line in job.logs)


def test_unexpected_failure_keeps_traceback() -> None:
    manager = JobManager()

    def fn(_: JobContext) -> dict[str, int]:
        raise RuntimeError("bug")

    job = _wait(manager, manager.submit("t", {}, fn))
    assert any("Traceback" in line for line in job.logs)


def test_job_cancelled_while_queued_never_runs() -> None:
    manager = JobManager(max_workers=1)
    release = threading.Event()
    ran: list[str] = []
    blocker = manager.submit("block", {}, lambda ctx: release.wait(5) and {})
    queued = manager.submit("queued", {}, lambda ctx: ran.append("ran") or {})
    manager.cancel(queued.id)
    release.set()
    _wait(manager, blocker)
    assert _wait(manager, queued).status is JobStatus.CANCELLED
    assert ran == []
    manager.shutdown()


def test_history_keeps_only_the_newest_finished_jobs() -> None:
    manager = JobManager(history=3)
    jobs = []
    for i in range(6):
        jobs.append(_wait(manager, manager.submit("quick", {"i": i}, lambda ctx: {})))
        time.sleep(0.002)  # distinct created_at for a stable order
    manager.submit("trigger-trim", {}, lambda ctx: {})
    kept = {j.params.get("i") for j in manager.list()}
    assert {0, 1, 2}.isdisjoint(kept)
    assert len(manager.list()) <= 4
    manager.shutdown()
