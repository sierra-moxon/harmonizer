"""Tests for the /new job page and the create/submit path (Phase 8).

Two layers:

* **Helper-level** (always run): ``JobManager.create_job`` end-to-end against a
  real temp SQLite DB + a fixture TSV, plus ``create_and_submit`` with the loop
  stubbed, asserting a Job row, job_dir, and sidecars are created.
* **Simulation** (skips cleanly if the harness can't run): a NiceGUI ``User``
  renders ``/new`` and ``/`` and asserts the expected controls/labels appear.
"""

from __future__ import annotations

import json
from pathlib import Path

from harmonizer.database.models import Job, JobStatus
from harmonizer.job.setup import (
    CURATION_INPUTS_FILENAME,
    CURATION_REPORT_FILENAME,
    DRAFT_MAPPING_FILENAME,
)

from .conftest import run_sim


# -- JobManager.create_job end-to-end -----------------------------------------


def test_create_job_creates_row_dir_and_sidecars(manager, sample_tsv, jobs_root):
    job_id = manager.create_job(
        spreadsheet=sample_tsv,
        study_context="soil samples from a grassland",
        max_iterations=2,
    )

    # DB row exists with the guessed interface and PENDING status.
    job = manager.get_job(job_id)
    assert isinstance(job, Job)
    assert job.id == job_id
    assert job.status == JobStatus.PENDING
    assert job.source_filename == sample_tsv.name
    assert job.interface_guess  # a soil-ish interface was guessed

    # Job dir + sidecars exist on disk.
    job_dir = jobs_root / job_id
    assert (job_dir / "data" / sample_tsv.name).is_file()
    for sidecar in (
        DRAFT_MAPPING_FILENAME,
        CURATION_INPUTS_FILENAME,
        CURATION_REPORT_FILENAME,
    ):
        assert (job_dir / sidecar).is_file()

    draft = json.loads((job_dir / DRAFT_MAPPING_FILENAME).read_text())
    assert draft["job_id"] == job_id
    assert "columns" in draft


def test_create_job_defaults_max_iterations_from_settings(manager, sample_tsv):
    # make_settings sets max_iterations=3; not passing it should use that.
    job_id = manager.create_job(spreadsheet=sample_tsv)
    job = manager.get_job(job_id)
    assert job is not None
    assert job.max_iterations == 3


# -- create_and_submit (page helper) with a stubbed loop ----------------------


def test_create_and_submit_runs_and_completes(manager, sample_tsv, jobs_root):
    from harmonizer.web.app import create_and_submit

    job_id = create_and_submit(
        manager,
        spreadsheet=sample_tsv,
        study_context="",
        max_iterations=2,
        original_name=sample_tsv.name,
    )
    # Block on the worker so we can assert the terminal state deterministically.
    manager._futures[job_id].result(timeout=30)

    job = manager.get_job(job_id)
    assert job is not None
    assert job.status == JobStatus.COMPLETED

    # The stubbed loop runs the real report phase → both artifacts exist.
    job_dir = jobs_root / job_id
    assert (job_dir / "mapped_output.json").is_file()
    assert (job_dir / CURATION_REPORT_FILENAME).is_file()


def test_create_and_submit_preserves_uploaded_filename(
    manager, sample_tsv, tmp_path, jobs_root
):
    from harmonizer.web.app import create_and_submit

    # Simulate a streamed temp upload with a mangled name.
    tmp_upload = tmp_path / "harmonizer-upload-abc123.tsv"
    tmp_upload.write_text(sample_tsv.read_text())

    job_id = create_and_submit(
        manager,
        spreadsheet=tmp_upload,
        study_context="",
        max_iterations=2,
        original_name="my_soil.tsv",
    )
    manager._futures[job_id].result(timeout=30)

    job = manager.get_job(job_id)
    assert job is not None
    assert job.source_filename == "my_soil.tsv"
    assert (jobs_root / job_id / "data" / "my_soil.tsv").is_file()


# -- failed run marks the job FAILED (loop error captured) --------------------


def test_submit_job_marks_failed_on_loop_error(
    session_factory, make_settings, sample_tsv, jobs_root
):
    from harmonizer.job.manager import JobManager

    async def boom(job_dir, session_factory=None, settings=None, **_):
        # Mimic run_mapping_async recording FAILED then re-raising.
        from harmonizer.state.mapping_state import MappingState

        state = MappingState.load_from_database_sync(
            Path(job_dir).name, session_factory
        )
        state.status = JobStatus.FAILED
        state.error = "RuntimeError: kaboom"
        state.save_to_database_sync(session_factory)
        raise RuntimeError("kaboom")

    mgr = JobManager(
        session_factory=session_factory,
        settings=make_settings(),
        max_workers=1,
        run_loop=boom,
    )
    try:
        job_id = mgr.create_job(spreadsheet=sample_tsv, max_iterations=2)
        mgr.submit_job(job_id).result(timeout=30)
        job = mgr.get_job(job_id)
        assert job is not None
        assert job.status == JobStatus.FAILED
        assert job.error and "kaboom" in job.error
    finally:
        mgr.shutdown(wait=True)


def test_manager_defensively_marks_failed_before_loop_handles_it(
    session_factory, make_settings, sample_tsv
):
    """If the run raises before recording FAILED itself, the manager still
    records FAILED (keeps the pool alive, no stuck RUNNING)."""
    from harmonizer.job.manager import JobManager

    async def raise_early(job_dir, session_factory=None, settings=None, **_):
        raise ValueError("never touched the DB")

    mgr = JobManager(
        session_factory=session_factory,
        settings=make_settings(),
        max_workers=1,
        run_loop=raise_early,
    )
    try:
        job_id = mgr.create_job(spreadsheet=sample_tsv, max_iterations=2)
        mgr.submit_job(job_id).result(timeout=30)
        job = mgr.get_job(job_id)
        assert job is not None
        assert job.status == JobStatus.FAILED
        assert job.error and "never touched the DB" in job.error
    finally:
        mgr.shutdown(wait=True)


# -- NiceGUI User simulation (skips cleanly if unavailable) -------------------


def test_new_page_renders_controls():
    async def scenario(user):
        await user.open("/new")
        await user.should_see("New job")
        await user.should_see("Create job")
        await user.should_see("Max iterations")

    run_sim(scenario)


def test_index_page_renders_empty_state():
    async def scenario(user):
        await user.open("/")
        await user.should_see("Jobs")

    run_sim(scenario)
