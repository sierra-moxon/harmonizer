"""Thread-pool-backed job manager for the web UI.

:class:`JobManager` wraps the two halves of a mapping run so the NiceGUI pages
never block the event loop:

* :meth:`create_job` runs the *deterministic* pre-pass/setup
  (:func:`harmonizer.job.setup.create_job`) synchronously and returns the job id.
  Setup is fast (a pandas read + fuzzy match + a few JSON writes), so it runs
  inline on the caller's thread.
* :meth:`submit_job` runs the *iterative* orchestrator loop
  (:func:`harmonizer.orchestrator.loop.run_mapping_async`) on a worker thread
  from a :class:`~concurrent.futures.ThreadPoolExecutor`, wrapping the coroutine
  in ``asyncio.run`` (each worker thread gets its own event loop).

Mirrors OpenScientist's ``job_manager.py`` (thread pool + create/submit split;
pattern only, authored here), minus OAuth/multi-tenancy.

Session lifecycle (SQLite, threads)
-----------------------------------
SQLite connections must not cross threads. Every layer here already opens a
short-lived session *per operation* through the shared session factory rather
than holding one open, so the manager only needs to hand the *factory* around;
:func:`harmonizer.state.mapping_state.MappingState` and the loop each open and
close their own sessions inside the worker thread. The manager builds **one**
factory (bound to the configured ``HARMONIZER_DATABASE_URL``) and threads it
through both ``create_job`` and ``run_mapping_async`` for consistency and
testability.

Container-isolation seam (Phase 9)
----------------------------------
:meth:`_run_job_blocking` is the single place where a mapping run is executed.
By default it runs the loop *in process* via ``asyncio.run(run_mapping_async(...))``.
When ``settings.use_container_isolation`` is set, it instead launches a per-job
container via :class:`~harmonizer.job_container.runner.JobContainerRunner`
(mounting ``job_dir`` and passing ``HARMONIZER_JOB_*`` / provider env); the rest
of the manager (status transitions, error capture, listing) is unchanged. See
the marked seam below.
"""

from __future__ import annotations

import asyncio
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from sqlalchemy.orm import Session, sessionmaker

from harmonizer.database.models import Job, JobStatus
from harmonizer.database.session import get_session_factory, init_db
from harmonizer.job.setup import create_job as _setup_create_job
from harmonizer.job_container.runner import JobContainerRunner
from harmonizer.orchestrator.loop import run_mapping_async
from harmonizer.settings import Settings, get_settings

#: Signature of the loop entry point, so tests can inject a stub that avoids the
#: live provider/CLI while still exercising the manager's threading and status.
RunLoop = Callable[..., object]

#: Signature of the container runner, so tests can inject a stub that avoids a
#: running Docker daemon while still asserting the manager routes to it.
ContainerRunner = Callable[[str, Path], object]


class JobManager:
    """Create jobs (pre-pass) and run them (loop) on a thread pool."""

    def __init__(
        self,
        session_factory: sessionmaker[Session] | None = None,
        settings: Settings | None = None,
        max_workers: int = 2,
        run_loop: RunLoop | None = None,
        container_launch: ContainerRunner | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._session_factory = session_factory or get_session_factory()
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="harmonizer-job"
        )
        # The loop coroutine factory. Injectable so JobManager can be tested
        # without spawning the real agent/CLI or making network calls. When not
        # injected, resolve it lazily at call time via the module attribute
        # ``run_mapping_async`` so monkeypatching that attribute (e.g. in a
        # NiceGUI simulation using the default manager) still takes effect.
        self._run_loop = run_loop
        # The per-job container launcher (Phase 9). Injectable so the seam can be
        # tested without a Docker daemon; when not injected a real
        # ``JobContainerRunner`` is used (only reached when the flag is on).
        self._container_launch = container_launch
        # Track in-flight futures so callers can await/join if they want to.
        self._futures: dict[str, Future] = {}

    # -- properties ------------------------------------------------------------

    @property
    def jobs_root(self) -> Path:
        """The root directory under which per-job directories are created."""
        return Path(self._settings.jobs_root)

    def job_dir(self, job_id: str) -> Path:
        """Return the job directory for ``job_id`` (``<jobs_root>/<job_id>``)."""
        return self.jobs_root / job_id

    # -- create (deterministic pre-pass) --------------------------------------

    def create_job(
        self,
        spreadsheet: str | Path,
        study_context: str = "",
        max_iterations: int | None = None,
        job_id: str | None = None,
    ) -> str:
        """Run the pre-pass for ``spreadsheet`` and return the new job id.

        Synchronous: the deterministic setup is fast. Delegates to
        :func:`harmonizer.job.setup.create_job`, which creates the job dir,
        copies the upload, writes the three sidecars, and seeds the ledger.
        """
        job_id = job_id or uuid.uuid4().hex[:12]
        _setup_create_job(
            job_id=job_id,
            spreadsheet=spreadsheet,
            study_context=study_context,
            max_iterations=(
                max_iterations
                if max_iterations is not None
                else self._settings.max_iterations
            ),
            jobs_root=self.jobs_root,
            session_factory=self._session_factory,
        )
        return job_id

    # -- submit (iterative loop on a worker thread) ---------------------------

    def submit_job(self, job_id: str) -> Future:
        """Schedule the mapping loop for ``job_id`` on a worker thread.

        Returns the :class:`~concurrent.futures.Future` for the run. Failures are
        recorded on the ``Job`` row (``FAILED`` + ``error``) by the loop *and*
        defensively here, so a crash never leaves a job stuck ``RUNNING`` and
        never kills the worker.
        """
        future = self._executor.submit(self._run_job_blocking, job_id)
        self._futures[job_id] = future
        return future

    def _run_job_blocking(self, job_id: str) -> None:
        """Execute one mapping run to completion on the worker thread.

        This is the container-isolation seam for Phase 9. By default it runs the
        loop in process (``asyncio.run`` gives this thread its own event loop).
        When ``settings.use_container_isolation`` is enabled it instead launches
        a per-job container (mounting ``job_dir`` and passing the
        ``HARMONIZER_JOB_*`` / provider env). The surrounding status- and
        error-handling is identical for both paths: a run that dies without
        recording ``FAILED`` still gets marked defensively below.
        """
        job_dir = self.job_dir(job_id)
        try:
            # --- Phase 9 seam -------------------------------------------------
            if self._settings.use_container_isolation:
                self._launch_container(job_id, job_dir)
            else:
                self._run_in_process(job_dir)
        except Exception as exc:  # noqa: BLE001 — keep the pool alive.
            # The loop already persists FAILED + error before re-raising, but a
            # failure *before* the loop reaches its own handler (e.g. bad job
            # dir, or a container that died) would otherwise leave the job
            # untouched; mark it defensively.
            self._mark_failed(job_id, exc)

    def _run_in_process(self, job_dir: Path) -> None:
        """Run the orchestrator loop in this worker thread's own event loop."""
        run_loop = self._run_loop or run_mapping_async
        asyncio.run(
            run_loop(  # type: ignore[misc]
                job_dir,
                session_factory=self._session_factory,
                settings=self._settings,
            )
        )

    def _launch_container(self, job_id: str, job_dir: Path) -> None:
        """Run the job in an isolated per-job Docker container (Phase 9)."""
        launch = self._container_launch or JobContainerRunner(self._settings).launch
        launch(job_id, job_dir)

    def _mark_failed(self, job_id: str, exc: Exception) -> None:
        """Best-effort FAILED marker used when the loop could not record it."""
        with self._session_factory() as session:
            job = session.get(Job, job_id)
            if job is None:
                return
            if job.status not in (JobStatus.COMPLETED, JobStatus.FAILED):
                job.status = JobStatus.FAILED
                job.error = f"{type(exc).__name__}: {exc}"
                session.commit()

    # -- read side (pages list jobs / poll a single job) ----------------------

    def list_jobs(self) -> list[Job]:
        """Return all jobs, newest first (detached, safe to read on any thread).

        Each call opens and closes its own session so nothing is shared across
        threads. Objects are expunged so the page can read their attributes
        after the session is closed (``expire_on_commit=False`` is set on the
        factory, and we detach explicitly).
        """
        with self._session_factory() as session:
            jobs = list(
                session.query(Job).order_by(Job.created_at.desc()).all()
            )
            for job in jobs:
                session.expunge(job)
            return jobs

    def get_job(self, job_id: str) -> Job | None:
        """Return a single detached :class:`Job`, or ``None`` if unknown."""
        with self._session_factory() as session:
            job = session.get(Job, job_id)
            if job is not None:
                session.expunge(job)
            return job

    # -- lifecycle -------------------------------------------------------------

    def ensure_schema(self) -> None:
        """Create tables for the manager's engine if they do not yet exist."""
        init_db()

    def shutdown(self, wait: bool = True) -> None:
        """Shut the worker pool down (call on app shutdown)."""
        self._executor.shutdown(wait=wait)
