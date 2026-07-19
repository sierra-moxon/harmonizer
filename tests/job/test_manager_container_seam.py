"""JobManager container-isolation seam (Phase 9).

Asserts that ``_run_job_blocking`` routes to the per-job container launcher when
``settings.use_container_isolation`` is True, and to the in-process loop when it
is False. Both the loop and the container launcher are stubbed so no real
container/agent/CLI/network is touched.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from harmonizer.database.models import Job, JobStatus
from harmonizer.job.manager import JobManager
from harmonizer.settings import Settings


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(jobs_root=str(tmp_path / "jobs"), max_iterations=3)


def _seed_job(session_factory: sessionmaker[Session], job_id: str) -> None:
    with session_factory() as session:
        session.add(
            Job(
                id=job_id,
                source_filename="x.tsv",
                interface_guess="SoilInterface",
                status=JobStatus.PENDING,
                max_iterations=3,
            )
        )
        session.commit()


def test_container_isolation_on_calls_launch_not_loop(session_factory, settings):
    """With the flag on, the manager launches a container, not the loop."""
    launched: list[tuple[str, Path]] = []
    loop_calls: list = []

    async def fake_loop(job_dir, **_):
        loop_calls.append(job_dir)

    def fake_launch(job_id, job_dir):
        launched.append((job_id, Path(job_dir)))

    settings = Settings(jobs_root=settings.jobs_root, use_container_isolation=True)
    mgr = JobManager(
        session_factory=session_factory,
        settings=settings,
        max_workers=1,
        run_loop=fake_loop,
        container_launch=fake_launch,
    )
    try:
        _seed_job(session_factory, "jobA")
        mgr.submit_job("jobA").result(timeout=30)
    finally:
        mgr.shutdown(wait=True)

    assert launched == [("jobA", mgr.job_dir("jobA"))]
    assert loop_calls == []


def test_container_isolation_off_calls_loop_not_launch(session_factory, settings):
    """With the flag off (default), the manager runs the in-process loop."""
    launched: list = []
    loop_calls: list = []

    async def fake_loop(job_dir, **_):
        loop_calls.append(Path(job_dir))

    def fake_launch(job_id, job_dir):
        launched.append((job_id, job_dir))

    # use_container_isolation defaults to False.
    mgr = JobManager(
        session_factory=session_factory,
        settings=settings,
        max_workers=1,
        run_loop=fake_loop,
        container_launch=fake_launch,
    )
    try:
        _seed_job(session_factory, "jobB")
        mgr.submit_job("jobB").result(timeout=30)
    finally:
        mgr.shutdown(wait=True)

    assert loop_calls == [mgr.job_dir("jobB")]
    assert launched == []


def test_container_launch_failure_marks_job_failed(session_factory, settings):
    """A container that dies is caught and the job is marked FAILED."""

    def boom(job_id, job_dir):
        raise RuntimeError("container exited 1")

    settings = Settings(jobs_root=settings.jobs_root, use_container_isolation=True)
    mgr = JobManager(
        session_factory=session_factory,
        settings=settings,
        max_workers=1,
        container_launch=boom,
    )
    try:
        _seed_job(session_factory, "jobC")
        mgr.submit_job("jobC").result(timeout=30)
    finally:
        mgr.shutdown(wait=True)

    job = mgr.get_job("jobC")
    assert job is not None
    assert job.status == JobStatus.FAILED
    assert "container exited 1" in (job.error or "")
