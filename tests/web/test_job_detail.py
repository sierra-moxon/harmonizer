"""Tests for the /job/<id> detail page helpers and rendering (Phase 8).

Covers the pure helpers (status badge colours, progress computation, download
path resolution) directly, then a NiceGUI ``User`` simulation of the detail /
schema pages that skips cleanly if the harness can't run headless.
"""

from __future__ import annotations

from pathlib import Path

from harmonizer.database.models import JobStatus, PlaceholderOutcome
from harmonizer.job.setup import (
    CURATION_REPORT_FILENAME,
    MAPPED_OUTPUT_FILENAME,
)
from harmonizer.state.mapping_state import MappingState, PlaceholderEntry
from harmonizer.web.helpers import (
    compute_progress,
    is_terminal,
    resolve_downloads,
    status_color,
)

from .conftest import run_sim


# -- status badge mapping -----------------------------------------------------


def test_status_color_covers_every_status():
    seen = {status: status_color(status) for status in JobStatus}
    # Distinct, non-empty colours for the meaningful states.
    assert seen[JobStatus.RUNNING] == "blue"
    assert seen[JobStatus.COMPLETED] == "green"
    assert seen[JobStatus.FAILED] == "red"
    assert all(isinstance(c, str) and c for c in seen.values())


def test_is_terminal():
    assert is_terminal(JobStatus.COMPLETED)
    assert is_terminal(JobStatus.FAILED)
    assert is_terminal(JobStatus.CANCELLED)
    assert not is_terminal(JobStatus.PENDING)
    assert not is_terminal(JobStatus.RUNNING)


# -- progress computation -----------------------------------------------------


def _state_with(outcomes: list[PlaceholderOutcome]) -> MappingState:
    return MappingState(
        job_id="j",
        placeholders=[
            PlaceholderEntry(row_id="*", column=f"c{i}", outcome=o)
            for i, o in enumerate(outcomes)
        ],
    )


def test_compute_progress_counts_and_fraction():
    state = _state_with(
        [
            PlaceholderOutcome.PENDING,
            PlaceholderOutcome.RESOLVED,
            PlaceholderOutcome.RESOLVED,
            PlaceholderOutcome.LEFT_PLACEHOLDER,
            PlaceholderOutcome.VALIDATOR_REJECTED,
        ]
    )
    p = compute_progress(state)
    assert p.total == 5
    assert p.remaining == 1
    assert p.resolved == 2
    assert p.left_placeholder == 1
    assert p.validator_rejected == 1
    assert p.done == 4
    assert p.fraction == 4 / 5


def test_compute_progress_empty_is_complete():
    p = compute_progress(MappingState(job_id="j"))
    assert p.total == 0
    assert p.remaining == 0
    assert p.fraction == 1.0


# -- download resolution ------------------------------------------------------


def test_resolve_downloads_only_offers_existing_files(tmp_path):
    # Nothing on disk yet → no downloads.
    assert resolve_downloads(tmp_path) == []

    (tmp_path / MAPPED_OUTPUT_FILENAME).write_text("{}")
    downloads = resolve_downloads(tmp_path)
    assert [d.filename for d in downloads] == [MAPPED_OUTPUT_FILENAME]
    assert downloads[0].path == tmp_path / MAPPED_OUTPUT_FILENAME
    assert downloads[0].exists

    (tmp_path / CURATION_REPORT_FILENAME).write_text("{}")
    downloads = resolve_downloads(tmp_path)
    assert {d.filename for d in downloads} == {
        MAPPED_OUTPUT_FILENAME,
        CURATION_REPORT_FILENAME,
    }


# -- render_job_detail against a real manager + DB ----------------------------


def test_get_job_returns_none_for_unknown_id(manager):
    # The read-side branch render_job_detail guards on: an unknown id yields
    # None (rendered as an "Unknown job" message rather than raising).
    assert manager.get_job("does-not-exist") is None


def test_job_detail_progress_source_is_live_state(manager, sample_tsv):
    """After create_job, the live MappingState drives the detail progress: all
    placeholder columns start PENDING (remaining == total)."""
    job_id = manager.create_job(spreadsheet=sample_tsv, max_iterations=2)
    state = MappingState.load_from_database_sync(
        job_id, manager._session_factory
    )
    p = compute_progress(state)
    assert p.total >= 1
    assert p.remaining == p.total  # nothing resolved yet
    assert p.resolved == 0


# -- NiceGUI User simulation (skips cleanly if unavailable) -------------------


def test_schema_page_lists_interfaces():
    async def scenario(user):
        await user.open("/schema")
        await user.should_see("Schema")

    run_sim(scenario)


def test_job_detail_unknown_job_renders_message():
    async def scenario(user):
        await user.open("/job/nonexistent-id")
        await user.should_see("Unknown job")

    run_sim(scenario)
